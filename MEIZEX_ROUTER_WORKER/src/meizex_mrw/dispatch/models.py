"""Resource Dispatch MVP — data contracts.

Defines the execution boundary between the Router (planning) and Executors (doing),
and the parent orchestrator escalation contract.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from meizex_mrw.capabilities.models import CapabilityRouteResult, ExecutionStep

ExecutionStatus = Literal[
    "COMPLETED",
    "FAILED",
    "ASSISTANCE_REQUIRED",
    "APPROVAL_REQUIRED",
]


class ResourceExecutionRequest(BaseModel):
    """The input to any ResourceExecutor."""

    mission: str
    step: ExecutionStep
    previous_output: dict[str, Any] | str | None = None
    timeout_s: float | None = None
    run_id: str | None = None
    step_id: str | None = None


class ResourceExecutionResult(BaseModel):
    """The standard output of any ResourceExecutor."""

    step_id: str
    resource_id: str
    capability: str
    executor_kind: str
    status: ExecutionStatus
    output: dict[str, Any] | str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None
    escalation_reason: str | None = None


class AssistanceResponse(BaseModel):
    """The structured response from a parent orchestrator."""

    status: Literal["ASSISTANCE_COMPLETED", "ASSISTANCE_REJECTED"]
    discovered_resources: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    answer: str = ""
    human_approved: bool = False
    confidence: float = 0.0


class DispatchResult(BaseModel):
    """The full mission outcome from the Dispatcher."""

    mission: str
    status: Literal["COMPLETED", "FAILED", "DEGRADED", "ESCALATED"]
    step_results: list[ResourceExecutionResult] = Field(default_factory=list)
    final_output: dict[str, Any] | str | None = None
    resume_token: str | None = None
    run_id: str | None = None


class PendingStateEnvelope(BaseModel):
    """The serialized envelope for a paused mission state."""

    schema_version: str = "v1"
    resume_token: str
    route_result: CapabilityRouteResult
    step_index: int
    results: list[ResourceExecutionResult]
    current_output: dict[str, Any] | str | None = None
    escalation_count: int = 1
    # M10: additive, backward-compatible run correlation so a resume can
    # reopen the same observation stream that the original dispatch wrote to.
    run_id: str | None = None
