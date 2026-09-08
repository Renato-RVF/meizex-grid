"""Execution profile — the first-class, declarative unit the router/worker
select before doing anything else. Provider, model, tool allowlist, and
policy live here so they never get hardcoded into routing/worker code.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ReasoningEffort = Literal["none", "low", "medium", "high"]


class InferenceSettings(BaseModel):
    reasoning_effort: ReasoningEffort | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    provider_timeout_s: float | None = Field(default=None, gt=0)
    """Optional httpx read/connect timeout (seconds) for this profile's
    provider client. Kept in the profile — not the provider config — because
    the right value depends on the mission (a large local-corpus prompt can
    legitimately take more than the provider default 60s to synthesize)."""


class ExecutionPolicy(BaseModel):
    write_allowed: bool = False
    shell_allowed: bool = False


class ExecutionProfile(BaseModel):
    name: str
    provider: str
    model: str
    context_budget: int = Field(gt=0)
    max_selected_tools: int = Field(default=5, gt=0)
    max_tool_rounds: int | None = Field(default=None, gt=0)
    """Tool-call budget for one mission turn. When set, the Worker uses this
    value (instead of its constructor default) unless the caller explicitly
    passed a constructor ``max_tool_rounds``. None keeps the Worker's
    backward-compatible default. Loop protection stays intact: the value is a
    hard cap that stops a runaway tool loop."""
    allowed_tools: list[str] = Field(default_factory=list)
    cloud_allowed: bool = False
    local_only: bool = True
    grid_allowed: bool = False
    """Whether the router may auto-select a resource declared
    `location: "grid"` (a MEIZEX Grid node, see MEIZEX_GRID/NEXT-011) for a
    mission, instead of it staying a manual/orchestrator decision. Defaults
    to False: routing to the Grid requires an explicit opt-in per profile,
    mirroring how cloud_allowed already works for cloud resources. This is
    the "configurável: automático ou à decisão do usuário" knob."""
    minimum_validation_level: str | None = None
    allowed_resource_kinds: list[str] | None = None
    hardware_constraints: dict[str, Any] = Field(default_factory=dict)
    inference: InferenceSettings = Field(default_factory=InferenceSettings)
    policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
