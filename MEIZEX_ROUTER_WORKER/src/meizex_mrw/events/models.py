"""Typed events for the MRW append-only log.

Curated from MEIZEX_HARNESS_V2's event_core.py — kept: the append-only
JSONL pattern, typed Pydantic events, and discriminated replay. Dropped:
events tied to the old Chassis/GUI (NexusScoreComputed was defined there
and never emitted by anything — dead schema, not carried forward) and
the old resource_type enum hardcoding two specific cloud providers
(api_gemini/api_openai) that don't exist in this core yet.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    event_type: str
    session_id: str
    # M10 durable observation correlation. Optional so every pre-existing event
    # type keeps validating and being emitted unchanged; the dispatcher/worker
    # stamp them when a run is in progress.
    run_id: str | None = None
    step_id: str | None = None


class TurnStarted(BaseEvent):
    """A turn's own `id` is the turn_id every other event in the turn refers to."""

    event_type: Literal["TurnStarted"] = "TurnStarted"
    user_message: str


class TaskClassified(BaseEvent):
    event_type: Literal["TaskClassified"] = "TaskClassified"
    turn_id: str
    task_type: str
    confidence: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)


class ExecutionProfileSelected(BaseEvent):
    event_type: Literal["ExecutionProfileSelected"] = "ExecutionProfileSelected"
    turn_id: str
    profile_name: str
    provider: str
    model: str
    cloud_allowed: bool


class ModelRouteSelected(BaseEvent):
    event_type: Literal["ModelRouteSelected"] = "ModelRouteSelected"
    turn_id: str
    provider: str
    model: str
    reason: str
    cloud_allowed: bool


class ToolsSelected(BaseEvent):
    event_type: Literal["ToolsSelected"] = "ToolsSelected"
    turn_id: str
    selected: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    reason: str = ""


class InferenceRequested(BaseEvent):
    event_type: Literal["InferenceRequested"] = "InferenceRequested"
    turn_id: str
    provider: str
    model: str
    message_count: int = Field(ge=0)
    tool_count: int = Field(ge=0)


class InferenceCompleted(BaseEvent):
    event_type: Literal["InferenceCompleted"] = "InferenceCompleted"
    turn_id: str
    provider: str
    model: str
    finish_reason: str | None = None
    tool_call_count: int = Field(default=0, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ToolRequested(BaseEvent):
    event_type: Literal["ToolRequested"] = "ToolRequested"
    turn_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class HITLApproved(BaseEvent):
    event_type: Literal["HITLApproved"] = "HITLApproved"
    turn_id: str
    call_id: str
    approved_by: str = "policy"


class HITLRejected(BaseEvent):
    event_type: Literal["HITLRejected"] = "HITLRejected"
    turn_id: str
    call_id: str
    reason: str


class ToolExecuted(BaseEvent):
    event_type: Literal["ToolExecuted"] = "ToolExecuted"
    turn_id: str
    call_id: str
    tool_name: str
    result: str
    duration_ms: float | None = Field(default=None, ge=0)


class ToolFailed(BaseEvent):
    event_type: Literal["ToolFailed"] = "ToolFailed"
    turn_id: str
    call_id: str
    tool_name: str
    error: str


class VerificationCompleted(BaseEvent):
    event_type: Literal["VerificationCompleted"] = "VerificationCompleted"
    turn_id: str
    verdict: Literal["PASS", "RETRY", "FAIL"]
    detail: str | None = None


class AssistantMessage(BaseEvent):
    event_type: Literal["AssistantMessage"] = "AssistantMessage"
    turn_id: str
    content: str


class ResourceUsageMeasured(BaseEvent):
    """resource_type is a free string (e.g. "tool", "inference_local"),
    not a fixed enum of provider names — the core stays provider-agnostic,
    so it must not hardcode which runtimes exist."""

    event_type: Literal["ResourceUsageMeasured"] = "ResourceUsageMeasured"
    turn_id: str
    resource_type: str
    operation: str
    duration_ms: float = Field(ge=0)
    status: Literal["success", "timeout", "error", "cancelled"] = "success"


class TurnCompleted(BaseEvent):
    event_type: Literal["TurnCompleted"] = "TurnCompleted"
    turn_id: str
    detail: str | None = None


class TurnFailed(BaseEvent):
    event_type: Literal["TurnFailed"] = "TurnFailed"
    turn_id: str
    reason: str


class AuditRequested(BaseEvent):
    """The local Verifier exhausted its retries (or FAILed outright) and the
    worker is escalating to an external auditor profile -- ESCALATE_UNCERTAINTY
    per request_policy.models.EscalationReason. reason is the Verifier's own
    detail string (objective, mechanical), never a vague restatement."""

    event_type: Literal["AuditRequested"] = "AuditRequested"
    turn_id: str
    auditor_profile: str
    reason: str


class AuditCompleted(BaseEvent):
    """The audit call returned (or failed to reach the auditor at all).
    verdict is the auditor's own answer re-run through the Verifier -- an
    audit is not trusted just because it came from a stronger model."""

    event_type: Literal["AuditCompleted"] = "AuditCompleted"
    turn_id: str
    verdict: Literal["PASS", "RETRY", "FAIL", "UNREACHABLE"]
    provider: str
    model: str
    detail: str | None = None


class RunStarted(BaseEvent):
    """A logical MRW run begins. The run's `run_id` correlates every event in it."""

    event_type: Literal["RunStarted"] = "RunStarted"
    run_id: str
    mission: str


class StepStarted(BaseEvent):
    """A logical execution step begins (emitted by the dispatcher, not executors)."""

    event_type: Literal["StepStarted"] = "StepStarted"
    run_id: str
    step_id: str
    index: int = Field(ge=0)
    capability: str
    resource: str
    executor_kind: str


class StepCompleted(BaseEvent):
    event_type: Literal["StepCompleted"] = "StepCompleted"
    run_id: str
    step_id: str
    index: int = Field(ge=0)
    capability: str
    resource: str
    executor_kind: str
    status: str
    latency_ms: float = 0.0


class StepFailed(BaseEvent):
    event_type: Literal["StepFailed"] = "StepFailed"
    run_id: str
    step_id: str
    index: int = Field(ge=0)
    capability: str
    resource: str
    executor_kind: str
    error: str


class StepEscalated(BaseEvent):
    """A step pauses for parent-orchestrator assistance (not a failure)."""

    event_type: Literal["StepEscalated"] = "StepEscalated"
    run_id: str
    step_id: str
    index: int = Field(ge=0)
    capability: str
    resource: str
    executor_kind: str
    status: str
    escalation_reason: str | None = None


class RunEscalated(BaseEvent):
    """The run pauses; the pending state is saved under `resume_token`."""

    event_type: Literal["RunEscalated"] = "RunEscalated"
    run_id: str
    resume_token: str
    step_index: int = Field(ge=0)
    escalation_count: int = Field(ge=1)


class RunCompleted(BaseEvent):
    event_type: Literal["RunCompleted"] = "RunCompleted"
    run_id: str
    final_status: str
    step_count: int = Field(ge=0)


class StepDivergence(BaseEvent):
    """Emitted by an executor's own background monitor (not the dispatcher,
    which only observes start/end) the first time a step's real elapsed
    time stops matching what its own timeout_s predicted -- while the step
    is STILL RUNNING, before it completes or times out. See
    meizex_mrw.statewatch.residual; ``expected_phase``/``observed_phase``
    are its Divergence fields, carried through unchanged so this event is
    self-explanatory without cross-referencing the module that produced
    it."""

    event_type: Literal["StepDivergence"] = "StepDivergence"
    run_id: str
    step_id: str
    capability: str
    resource: str
    executor_kind: str
    expected_phase: str
    observed_phase: str
    elapsed_s: float = Field(ge=0)


EVENT_CLASSES: tuple[type[BaseEvent], ...] = (
    TurnStarted,
    TaskClassified,
    ExecutionProfileSelected,
    ModelRouteSelected,
    ToolsSelected,
    InferenceRequested,
    InferenceCompleted,
    ToolRequested,
    HITLApproved,
    HITLRejected,
    ToolExecuted,
    ToolFailed,
    VerificationCompleted,
    AssistantMessage,
    ResourceUsageMeasured,
    TurnCompleted,
    TurnFailed,
    AuditRequested,
    AuditCompleted,
    RunStarted,
    StepStarted,
    StepCompleted,
    StepFailed,
    StepEscalated,
    RunEscalated,
    RunCompleted,
    StepDivergence,
)

AnyEvent = Union[  # noqa: UP007 - Union kept (not X|Y) since it's used as a pydantic type
    TurnStarted,
    TaskClassified,
    ExecutionProfileSelected,
    ModelRouteSelected,
    ToolsSelected,
    InferenceRequested,
    InferenceCompleted,
    ToolRequested,
    HITLApproved,
    HITLRejected,
    ToolExecuted,
    ToolFailed,
    VerificationCompleted,
    AssistantMessage,
    ResourceUsageMeasured,
    TurnCompleted,
    TurnFailed,
    AuditRequested,
    AuditCompleted,
    RunStarted,
    StepStarted,
    StepCompleted,
    StepFailed,
    StepEscalated,
    RunEscalated,
    RunCompleted,
]

EVENT_CLASS_MAP: dict[str, type[BaseEvent]] = {
    cls.model_fields["event_type"].default: cls for cls in EVENT_CLASSES
}
