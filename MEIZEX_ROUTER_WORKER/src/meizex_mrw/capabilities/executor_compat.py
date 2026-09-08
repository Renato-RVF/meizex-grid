"""Shared executor-compatibility contract (single source of truth).

The router must never select a resource for which no registered executor
exists. Each executor in :mod:`meizex_mrw.dispatch.executors` answers
``can_handle`` with the SAME predicates defined here, so the router's
pre-selection filter and the dispatcher's executor selection can never
disagree about what is executable.

Predicates mirror, exactly, the contracts the executors implement:

* ``deterministic_compatible``  -> DeterministicExecutor
* ``mcp_compatible``            -> MCPExecutor
* ``llm_compatible``            -> LLMExecutor
* ``process_compatible``        -> ProcessExecutor
* ``executor_compatible``       -> OR of all four (routable to *some* executor)

The router uses :func:`executor_compatible` to drop candidates that have no
compatible executor BEFORE ranking and selection, so an unexecutable resource
can never become an operational ``ExecutionStep`` (dead step). A capability
whose only candidates are executor-incompatible fails closed with an explicit
``NO_EXECUTABLE_RESOURCE`` route status instead of producing a dead plan.
"""

from __future__ import annotations

from typing import Literal

DETERMINISTIC_RESOURCE = "mrw-deterministic-router"
LLM_KINDS = frozenset({"slm", "llm", "vlm"})
PROCESS_BOUNDARY = "PROCESS"
IN_PROCESS_BOUNDARY = "IN_PROCESS"

ExecutionBoundary = Literal["IN_PROCESS", "PROCESS"]


def deterministic_compatible(kind: str, resource_id: str) -> bool:
    """DeterministicExecutor contract: tool resource backed by the router."""
    return kind == "tool" and DETERMINISTIC_RESOURCE in resource_id


def mcp_compatible(kind: str, resource_id: str) -> bool:
    """MCPExecutor contract: mcp kind or an mcp-backed resource id."""
    return kind == "mcp" or "mcp" in resource_id.lower()


def llm_compatible(kind: str, resource_id: str = "") -> bool:
    """LLMExecutor contract: slm / llm / vlm kinds."""
    return kind in LLM_KINDS


def process_compatible(execution_boundary: str | None) -> bool:
    """ProcessExecutor contract: step classified with a process boundary."""
    return execution_boundary == PROCESS_BOUNDARY


def executor_compatible(
    kind: str,
    resource_id: str,
    execution_boundary: str | None = IN_PROCESS_BOUNDARY,
) -> bool:
    """Whether at least one registered executor can handle this step."""
    return any(
        (
            deterministic_compatible(kind, resource_id),
            mcp_compatible(kind, resource_id),
            llm_compatible(kind, resource_id),
            process_compatible(execution_boundary),
        )
    )


__all__ = [
    "DETERMINISTIC_RESOURCE",
    "LLM_KINDS",
    "PROCESS_BOUNDARY",
    "IN_PROCESS_BOUNDARY",
    "ExecutionBoundary",
    "deterministic_compatible",
    "mcp_compatible",
    "llm_compatible",
    "process_compatible",
    "executor_compatible",
]
