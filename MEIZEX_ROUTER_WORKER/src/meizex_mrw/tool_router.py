"""Deterministic pre-LLM tool selection.

Extracted (not copied) from MEIZEX_HARNESS_V2's daemon.py
(`_message_needs_tools` / `_select_tools_for_message`) — that logic was
embedded in a 1120-line daemon, PT-BR-keyword-only, and mixed with the
Chassis wire contract. This module keeps the good idea (filter a large
tool catalog down to a handful before the model ever sees a schema) and
drops the rest: no keyword list is the primary mechanism here — the
execution profile's allowlist is.

Pipeline: AVAILABLE TOOLS -> profile.allowed_tools (first filter) ->
task_type/capabilities match (second filter/ranking, ties broken by the
profile's own ordering) -> read_only/shell policy enforcement -> cap at
profile.max_selected_tools. 100% deterministic, no LLM call.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from meizex_mrw.profiles.schema import ExecutionProfile
from meizex_mrw.task_classifier import ClassifiedTask
from meizex_mrw.tools.metadata import ToolMetadata


class ToolSelection(BaseModel):
    selected: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)
    scores: dict[str, float] = Field(default_factory=dict)


def select(
    *,
    task: ClassifiedTask,
    profile: ExecutionProfile,
    available_tools: Sequence[ToolMetadata],
) -> ToolSelection:
    catalog = {tool.name: tool for tool in available_tools}
    reasons: dict[str, str] = {}
    scores: dict[str, float] = {}
    candidates: list[ToolMetadata] = []

    # First filter: only tools the profile explicitly allows are candidates
    # at all. Order within profile.allowed_tools is preserved as the
    # deterministic tie-break for everything that follows.
    for tool_name in profile.allowed_tools:
        tool = catalog.get(tool_name)
        if tool is None:
            reasons[tool_name] = "not_in_available_tools"
            scores[tool_name] = 0.0
            continue
        if not profile.policy.write_allowed and not tool.read_only:
            reasons[tool_name] = "blocked_by_write_policy"
            scores[tool_name] = 0.0
            continue
        if not profile.policy.shell_allowed and "shell" in tool.capabilities:
            reasons[tool_name] = "blocked_by_shell_policy"
            scores[tool_name] = 0.0
            continue
        candidates.append(tool)

    # Second filter/ranking: tools whose categories match the classified
    # task type are ranked first (stable sort preserves profile order
    # within each bucket), so "same input -> same output" always holds.
    def _rank_key(index_and_tool: tuple[int, ToolMetadata]) -> tuple[int, int]:
        index, tool = index_and_tool
        matches_task = task.task_type in tool.categories
        return (0 if matches_task else 1, index)

    ranked = [tool for _, tool in sorted(enumerate(candidates), key=_rank_key)]
    for rank, tool in enumerate(ranked):
        matches_task = task.task_type in tool.categories
        scores[tool.name] = 1.0 + (1.0 if matches_task else 0.0) - (rank * 1e-6)
        reasons[tool.name] = "matches_task_type" if matches_task else "in_profile_allowlist"

    selected_tools = ranked[: profile.max_selected_tools]
    for tool in ranked[profile.max_selected_tools :]:
        reasons[tool.name] = "exceeded_max_selected_tools"

    selected = [tool.name for tool in selected_tools]
    rejected = [name for name in profile.allowed_tools if name not in selected]

    return ToolSelection(selected=selected, rejected=rejected, reasons=reasons, scores=scores)
