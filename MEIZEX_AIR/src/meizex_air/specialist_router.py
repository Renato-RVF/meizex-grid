"""AIR-BEW-007 — Deterministic Heterogeneous Specialist Router (selection only).

Answers exactly one question: GIVEN A BOUNDED SPRINT'S EXECUTION REQUIREMENTS,
AVAILABLE SPECIALISTS, AND POLICY, WHICH SPECIALIST IS ELIGIBLE TO EXECUTE IT?

    This sprint SELECTS. It does NOT execute.
    SPECIALIST != MODEL != RUNTIME != EXECUTION_PROFILE
    A specialist may REFERENCE an ExecutionProfile (execution_profile_ref, a
    plain string, same convention as BoundedExecutionSprint.execution_profile_ref
    and ExecutionProfile.profile_id) - it never embeds one.

Core principle, in order: before "which model?", AIR must ask "does this
sprint require probabilistic reasoning at all?" (DETERMINISTIC FIRST).
Only when the answer is yes does model/harness/tool selection begin, and only
among specialists that are already eligible - never as a first cut.

Motivated directly by AIR-REAL-TRAVEL-001 (the real-world travel mission):
that mission's orchestrator manually decided, for every one of 10 sprints,
"does this need live web research (a tool-calling LLM), or is it pure
arithmetic (deterministic Python), or is it qualitative synthesis
(probabilistic reasoning)?" This module makes that decision layer a real,
deterministic, testable AIR contract instead of an implicit human judgment
call repeated by hand every time.

Discovery findings (full internal report in the AIR-BEW-007 delivery):
- EXISTING_CAPABILITY_TYPES = `ResourceCapability` (models.py, AIR-006 Task
  Definition: RUN_CODE/RUN_SHELL/SERVE_MODEL/INFERENCE/TOOL_ORCHESTRATION/
  FILE_MUTATION/NETWORK_ACCESS/STREAM_OUTPUT/SESSION_STATE) is reused
  DIRECTLY here as the capability vocabulary for both
  SprintExecutionRequirements.required_capabilities and
  ExecutionSpecialist.capabilities - a clean match, not reinvented.
  FILE_MUTATION is reused as the sole filesystem-capability signal for BOTH
  requires_filesystem_read and requires_filesystem_write (ResourceCapability
  has no dedicated read-only token; documented as a coarse existing mapping,
  not a new invented capability).
- EXISTING_RESOURCE_TYPES = `ResourceKind` (models.py: DETERMINISTIC_EXECUTOR/
  MODEL_RUNTIME/RUNTIME_ENVIRONMENT/AGENT_HARNESS/HARNESS_GATEWAY/HUMAN/
  UNKNOWN) and `ExecutorType` (PYTHON/POWERSHELL/LLAMA_CPP/LM_STUDIO/OLLAMA/
  ONNX_RUNTIME) were both considered and NOT reused for `SpecialistKind`:
  ResourceKind has no LOCAL_LLM/CLOUD_LLM split, which is exactly what this
  router's network/data-policy eligibility depends on, and ExecutorType is
  runtime-level (would violate SPECIALIST != RUNTIME). `EvidenceConfidence`
  (models.py: CONFIRMED/PARTIAL/UNKNOWN/NOT_FOUND) IS reused directly for
  `ExecutionSpecialist.availability_confidence` - the minimal, already-
  existing "how well is this declaration backed" vocabulary the task's
  "evidence_authority_state if already available" asked for, without
  building a new Evidence Ladder.
- EXISTING_ROUTING_TYPES = none. `ExecutionContract` (models.py) is the
  closest prior art - it records a resource selection for a TaskSpecification
  (task_id-keyed) - but it has no eligibility-set/rejection-reasons/ranking
  apparatus and operates at a different granularity (AIR-006 tasks, not
  AIR-BEW-001 BoundedExecutionSprints). Not reused directly; its
  selected_resource_id/selected_resource_kind/requested_capabilities naming
  spirit is carried forward here instead.
- EXISTING_EXECUTION_PROFILE_TYPES = `ExecutionMode` (LOCAL/HYBRID/CLOUD) is
  reused directly for `ExecutionSpecialist.network_scope` and
  `SprintExecutionRequirements.execution_mode_constraints`. `DataClassification`
  (PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED) is reused directly for both
  `required` and `_allowed` data-classification fields - the exact field-
  naming convention `ExecutionProfile.data_classification_allowed` already
  established. `CloudPolicy` (YES/NO/CONDITIONAL) is reused directly for
  `SprintExecutionRequirements.cloud_policy`. `evaluate_hardware_preflight`
  and `evaluate_context_fit` (execution_profiles.py) are called directly,
  not reimplemented, when a caller supplies resolved ExecutionProfile
  objects for hardware/context fit checks - this module implements no new
  hardware-probing or context-budget logic of its own.
- EXISTING_PROVIDER_TYPES = none - `ExecutionProfile.provider` is a plain
  str, no enum. Confirms SpecialistKind must stay provider-agnostic
  (LOCAL_LLM/CLOUD_LLM, never a named vendor).
- EXISTING_POLICY_TYPES = `CloudPolicy`, `DataClassification` (both reused,
  see above); `ExecutionConstraints` (models.py: memory/latency/risk/
  user_disruption, loose optional strings) was considered for
  `resource_requirements` but NOT reused - it is looser than what this
  router can actually enforce today. `minimum_context`/`maximum_context_if_known`
  (plain Optional[int]) are used instead, since token-capacity is the one
  resource dimension AIR already checks deterministically
  (evaluate_context_fit); anything richer is deferred to the future
  AIR-CONFIG-001 ExecutionConfiguration contract, not fabricated here.
- REUSABLE_SERIALIZATION / REUSABLE_IDENTITY_PATTERN = identity.canonical_json
  / identity.sha256_digest, the same convention every prior BEW module uses;
  `content_fingerprint()` / `canonical()` method pair reused verbatim.

Routing outcome trust: NO ELIGIBLE SPECIALIST -> NO ROUTE, never a forced
best-effort pick. UNKNOWN REQUIREMENT != PERMISSION - an unset/underspecified
requirement never silently widens the eligible set.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .execution_profiles import (
    CloudPolicy,
    ContextFit,
    DataClassification,
    ExecutionMode,
    ExecutionProfile,
    HardwareFit,
    evaluate_context_fit,
    evaluate_hardware_preflight,
)
from .identity import canonical_json, sha256_digest
from .models import EvidenceConfidence, ResourceCapability

ROUTER_SCHEMA_VERSION = "1.0"
ROUTER_POLICY_VERSION = "1"


# ---------------------------------------------------------------------------
# Specialist kind - heterogeneous, provider-agnostic
# ---------------------------------------------------------------------------


class SpecialistKind(str, Enum):
    DETERMINISTIC_PYTHON = "DETERMINISTIC_PYTHON"
    LOCAL_LLM = "LOCAL_LLM"
    CLOUD_LLM = "CLOUD_LLM"
    HARNESS = "HARNESS"
    MCP = "MCP"
    API = "API"
    CLI = "CLI"
    FILESYSTEM = "FILESYSTEM"
    STRUCTURED_AUTOMATION = "STRUCTURED_AUTOMATION"
    HUMAN = "HUMAN"


class CostClass(str, Enum):
    FREE = "FREE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class LatencyClass(str, Enum):
    INSTANT = "INSTANT"
    FAST = "FAST"
    MODERATE = "MODERATE"
    SLOW = "SLOW"


#: Explicit rank order (lower = cheaper/faster = preferred, all else equal).
#: Never accidental enum declaration order - both enums above happen to
#: already be declared cheapest/fastest-first, but ranking reads this dict,
#: not the enum, so a future reordering of the enum can never silently
#: change ranking behavior.
_COST_RANK: dict[CostClass, int] = {
    CostClass.FREE: 0, CostClass.LOW: 1, CostClass.MEDIUM: 2, CostClass.HIGH: 3,
}
_LATENCY_RANK: dict[LatencyClass, int] = {
    LatencyClass.INSTANT: 0, LatencyClass.FAST: 1, LatencyClass.MODERATE: 2, LatencyClass.SLOW: 3,
}


class RejectionReason(str, Enum):
    MISSING_CAPABILITY = "MISSING_CAPABILITY"
    TOOLING_UNSUPPORTED = "TOOLING_UNSUPPORTED"
    NETWORK_FORBIDDEN = "NETWORK_FORBIDDEN"
    DATA_POLICY_FORBIDDEN = "DATA_POLICY_FORBIDDEN"
    RESOURCE_MISMATCH = "RESOURCE_MISMATCH"
    PROFILE_MISMATCH = "PROFILE_MISMATCH"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    PROBABILISTIC_REASONING_REQUIRED = "PROBABILISTIC_REASONING_REQUIRED"
    SPECIALIST_KIND_FORBIDDEN = "SPECIALIST_KIND_FORBIDDEN"


class RoutingOutcome(str, Enum):
    SELECTED = "SELECTED"
    NO_ELIGIBLE_SPECIALIST = "NO_ELIGIBLE_SPECIALIST"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class RoutingConfidence(str, Enum):
    """Bounded qualitative scale - never a pseudo-probability. Deliberately a
    NEW, routing-specific enum (not AIR-BEW-005/006's ClassificationStrength/
    DecisionStrength reused verbatim) since routing confidence answers a
    different question (how much did ranking actually differentiate the
    eligible set) than classification/remediation confidence do - the
    qualitative-scale PATTERN is reused, not the values."""

    DETERMINISTIC = "DETERMINISTIC"  # exactly one eligible specialist
    RANKED = "RANKED"  # multiple eligible, a real signal (deterministic-first
    # tier, preferred-kind, priority, or cost/latency) separated the winner
    AMBIGUOUS = "AMBIGUOUS"  # multiple eligible, only the specialist_id
    # tiebreak separated them - still deterministic, but the policy had
    # nothing substantive to go on
    ABSTAINED = "ABSTAINED"  # NO_ELIGIBLE_SPECIALIST or HUMAN_REQUIRED


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


class SprintExecutionRequirements(BaseModel):
    """What a bounded sprint needs, in order to be routed. Never embeds a
    model/runtime/profile - only requirement flags and reused AIR vocabulary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = ROUTER_SCHEMA_VERSION
    mission_id: str
    sprint_id: str

    requires_probabilistic_reasoning: bool
    required_capabilities: list[ResourceCapability] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)

    requires_network: bool = False
    cloud_policy: CloudPolicy = CloudPolicy.NO
    data_classification: DataClassification
    execution_mode_constraints: Optional[list[ExecutionMode]] = None

    minimum_context: Optional[int] = Field(default=None, ge=0)
    maximum_context_if_known: Optional[int] = Field(default=None, ge=0)

    requires_external_freshness: bool = False
    requires_structured_output: bool = False
    requires_tool_calling: bool = False
    #: FILE_MUTATION (ResourceCapability) is the sole reused signal for both
    #: read and write filesystem access - see module docstring.
    requires_filesystem_read: bool = False
    requires_filesystem_write: bool = False
    requires_human_approval: bool = False

    preferred_specialist_kinds: list[SpecialistKind] = Field(default_factory=list)
    forbidden_specialist_kinds: list[SpecialistKind] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "SprintExecutionRequirements":
        self.mission_id = self.mission_id.strip()
        self.sprint_id = self.sprint_id.strip()
        if not self.mission_id:
            raise ValueError("mission_id must not be empty")
        if not self.sprint_id:
            raise ValueError("sprint_id must not be empty")
        self.required_capabilities = sorted(set(self.required_capabilities), key=lambda c: c.value)
        self.required_tools = sorted(set(self.required_tools))
        self.preferred_specialist_kinds = sorted(set(self.preferred_specialist_kinds), key=lambda k: k.value)
        self.forbidden_specialist_kinds = sorted(set(self.forbidden_specialist_kinds), key=lambda k: k.value)
        if self.execution_mode_constraints is not None:
            self.execution_mode_constraints = sorted(set(self.execution_mode_constraints), key=lambda m: m.value)
        if (
            self.minimum_context is not None
            and self.maximum_context_if_known is not None
            and self.minimum_context > self.maximum_context_if_known
        ):
            raise ValueError("minimum_context must not exceed maximum_context_if_known")
        return self

    def canonical(self) -> str:
        return canonical_json(self)

    def content_fingerprint(self) -> str:
        return sha256_digest(self)


class ExecutionSpecialist(BaseModel):
    """One entry in the specialist registry. A capability/policy DESCRIPTION,
    never an executable handle - route_specialist never invokes one."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = ROUTER_SCHEMA_VERSION
    specialist_id: str
    specialist_version: str = "1"

    kind: SpecialistKind
    capabilities: list[ResourceCapability] = Field(default_factory=list)
    #: Plain string reference only - never an embedded ExecutionProfile.
    execution_profile_ref: Optional[str] = None

    availability: bool = True
    #: Reused directly from models.py - how well `availability` is backed,
    #: not a new Evidence Ladder.
    availability_confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN

    network_scope: ExecutionMode = ExecutionMode.LOCAL
    data_classifications_allowed: list[DataClassification] = Field(default_factory=list)

    supports_tools: bool = False
    supports_structured_output: bool = False
    supports_external_freshness: bool = False

    cost_class: CostClass = CostClass.LOW
    latency_class: LatencyClass = LatencyClass.MODERATE
    #: Caller-supplied deterministic ranking override. Neutral default (0);
    #: lower ranks first, same direction as cost/latency rank dicts.
    priority: int = 0

    @model_validator(mode="after")
    def _validate(self) -> "ExecutionSpecialist":
        self.specialist_id = self.specialist_id.strip()
        if not self.specialist_id:
            raise ValueError("specialist_id must not be empty")
        self.capabilities = sorted(set(self.capabilities), key=lambda c: c.value)
        self.data_classifications_allowed = sorted(set(self.data_classifications_allowed), key=lambda d: d.value)
        return self

    def canonical(self) -> str:
        return canonical_json(self)

    def content_fingerprint(self) -> str:
        return sha256_digest(self)


class SpecialistRoutingDecision(BaseModel):
    """Immutable/serializable. A SELECTION, never an EXECUTION - see module
    docstring. Never mutates the requirements or specialists it was derived
    from."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = ROUTER_SCHEMA_VERSION
    mission_id: str
    sprint_id: str

    outcome: RoutingOutcome
    selected_specialist_id: Optional[str] = None
    selected_specialist_kind: Optional[SpecialistKind] = None
    selected_execution_profile_ref: Optional[str] = None

    eligible_specialists: list[str] = Field(default_factory=list)
    ineligible_specialists: list[str] = Field(default_factory=list)
    #: specialist_id -> reasons it was rejected (empty for eligible ones).
    rejection_reasons: dict[str, list[RejectionReason]] = Field(default_factory=dict)

    routing_reason: str
    decision_strength: RoutingConfidence
    policy_version: str = ROUTER_POLICY_VERSION

    #: Reused directly from SprintExecutionRequirements.content_fingerprint()
    #: / a stable hash over the sorted specialist registry - not a new
    #: identity system.
    requirements_fingerprint: str
    specialist_set_fingerprint: str

    warnings: list[str] = Field(default_factory=list)

    def canonical(self) -> str:
        return canonical_json(self)

    def content_fingerprint(self) -> str:
        return sha256_digest(self)


def specialist_set_fingerprint(specialists: list[ExecutionSpecialist]) -> str:
    """Deterministic regardless of registry ordering - sorts each
    specialist's own content_fingerprint() before hashing the set."""
    return sha256_digest(sorted(s.content_fingerprint() for s in specialists))


# ---------------------------------------------------------------------------
# Eligibility filtering
# ---------------------------------------------------------------------------


def _eligibility_reasons(
    requirements: SprintExecutionRequirements,
    specialist: ExecutionSpecialist,
    *,
    remediation_target_profile_ref: Optional[str],
    resolved_execution_profiles: dict[str, ExecutionProfile],
) -> list[RejectionReason]:
    """All reasons `specialist` fails `requirements` - empty means eligible.
    HUMAN-kind specialists are exempt from capability/tool/freshness checks
    (a human is not bound by those technical constraints the same way an
    automated specialist is) but remain subject to kind/network/data-policy/
    availability/profile checks."""
    reasons: list[RejectionReason] = []
    is_human = specialist.kind == SpecialistKind.HUMAN

    if specialist.kind in requirements.forbidden_specialist_kinds:
        reasons.append(RejectionReason.SPECIALIST_KIND_FORBIDDEN)

    if requirements.requires_human_approval and not is_human:
        reasons.append(RejectionReason.HUMAN_APPROVAL_REQUIRED)

    if not requirements.requires_probabilistic_reasoning:
        pass  # deterministic specialists remain eligible - see ranking, not here
    elif specialist.kind == SpecialistKind.DETERMINISTIC_PYTHON:
        reasons.append(RejectionReason.PROBABILISTIC_REASONING_REQUIRED)

    if not is_human:
        if not set(requirements.required_capabilities).issubset(set(specialist.capabilities)):
            reasons.append(RejectionReason.MISSING_CAPABILITY)
        if requirements.requires_tool_calling and not specialist.supports_tools:
            reasons.append(RejectionReason.TOOLING_UNSUPPORTED)
        if requirements.requires_structured_output and not specialist.supports_structured_output:
            reasons.append(RejectionReason.MISSING_CAPABILITY)
        if requirements.requires_external_freshness and not specialist.supports_external_freshness:
            reasons.append(RejectionReason.MISSING_CAPABILITY)
        if (
            (requirements.requires_filesystem_read or requirements.requires_filesystem_write)
            and ResourceCapability.FILE_MUTATION not in specialist.capabilities
        ):
            reasons.append(RejectionReason.MISSING_CAPABILITY)

    if requirements.cloud_policy == CloudPolicy.NO and specialist.network_scope in (
        ExecutionMode.HYBRID, ExecutionMode.CLOUD,
    ):
        reasons.append(RejectionReason.NETWORK_FORBIDDEN)
    if (
        requirements.requires_network
        and specialist.network_scope == ExecutionMode.LOCAL
        and ResourceCapability.NETWORK_ACCESS not in specialist.capabilities
    ):
        reasons.append(RejectionReason.NETWORK_FORBIDDEN)

    if requirements.data_classification not in specialist.data_classifications_allowed:
        reasons.append(RejectionReason.DATA_POLICY_FORBIDDEN)

    if (
        requirements.execution_mode_constraints is not None
        and specialist.network_scope not in requirements.execution_mode_constraints
    ):
        reasons.append(RejectionReason.PROFILE_MISMATCH)
    if (
        remediation_target_profile_ref is not None
        and specialist.execution_profile_ref != remediation_target_profile_ref
    ):
        reasons.append(RejectionReason.PROFILE_MISMATCH)

    profile = (
        resolved_execution_profiles.get(specialist.execution_profile_ref)
        if specialist.execution_profile_ref else None
    )
    if profile is not None:
        hardware = evaluate_hardware_preflight(profile)
        if hardware.hardware_fit == HardwareFit.FAIL:
            reasons.append(RejectionReason.RESOURCE_MISMATCH)
        if requirements.minimum_context is not None:
            context_fit, _ = evaluate_context_fit(
                profile.context_capacity_tokens, requirements.minimum_context, 0, 0,
            )
            if context_fit != ContextFit.FITS:
                reasons.append(RejectionReason.RESOURCE_MISMATCH)

    if not specialist.availability:
        reasons.append(RejectionReason.UNAVAILABLE)

    # Dedup while preserving first-seen order (a reason may be appended by
    # more than one check above, e.g. MISSING_CAPABILITY).
    seen: set[RejectionReason] = set()
    ordered: list[RejectionReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            ordered.append(reason)
    return ordered


# ---------------------------------------------------------------------------
# Deterministic ranking
# ---------------------------------------------------------------------------


def _rank_key(requirements: SprintExecutionRequirements, specialist: ExecutionSpecialist) -> tuple:
    """Lower sorts first (wins). Explicit, documented precedence - never
    enum declaration order or registry order:
        1. DETERMINISTIC FIRST - if the sprint does not require probabilistic
           reasoning, a DETERMINISTIC_PYTHON specialist outranks every other
           kind (both are already eligible at this point).
        2. preferred_specialist_kinds - influences ranking only, never
           eligibility (see module docstring / eligibility filtering above).
        3. priority - caller-supplied override, neutral default 0.
        4. cost_class, then latency_class - resource-based ranking, applied
           only after every eligibility/preference signal above.
        5. specialist_id - final deterministic tiebreak, never random.
    """
    deterministic_first = 0
    if not requirements.requires_probabilistic_reasoning and specialist.kind != SpecialistKind.DETERMINISTIC_PYTHON:
        deterministic_first = 1
    preferred = 0 if specialist.kind in requirements.preferred_specialist_kinds else 1
    return (
        deterministic_first,
        preferred,
        specialist.priority,
        _COST_RANK[specialist.cost_class],
        _LATENCY_RANK[specialist.latency_class],
        specialist.specialist_id,
    )


# ---------------------------------------------------------------------------
# Top-level pure router
# ---------------------------------------------------------------------------


def route_specialist(
    requirements: SprintExecutionRequirements,
    specialists: list[ExecutionSpecialist],
    *,
    remediation_target_profile_ref: Optional[str] = None,
    resolved_execution_profiles: Optional[dict[str, ExecutionProfile]] = None,
    allow_hitl: bool = True,
) -> SpecialistRoutingDecision:
    """Pure, deterministic. No I/O, no store, no model call, no execution of
    any kind - selects only. Never mutates `requirements` or any element of
    `specialists`. Same inputs always produce the same SpecialistRoutingDecision
    (content_fingerprint() is stable).

    `remediation_target_profile_ref`: if AIR-BEW-006 already decided
    ESCALATE_PROFILE with a specific authorized target, pass it here - the
    eligible set is constrained to specialists whose execution_profile_ref
    matches exactly. Never invented if not supplied.

    `resolved_execution_profiles`: optional specialist_id-independent map of
    execution_profile_ref -> ExecutionProfile, for callers that want
    hardware/context fit checked via execution_profiles.py's own functions.
    Without it, hardware/context fit is simply not evaluated (NOT_SUPPORTED),
    never assumed to pass or fail.

    `allow_hitl`: whether HUMAN_REQUIRED is an authorized fallback outcome
    when no automated specialist is eligible. Defaults True, matching AIR's
    existing default posture (TaskSpecification/ExecutionContract both
    default authority_requirement="HUMAN_IN_THE_LOOP").
    """
    resolved_execution_profiles = resolved_execution_profiles or {}
    requirements_fp = requirements.content_fingerprint()
    specialist_set_fp = specialist_set_fingerprint(specialists)

    eligible: list[ExecutionSpecialist] = []
    ineligible: list[str] = []
    rejection_reasons: dict[str, list[RejectionReason]] = {}
    human_specialists: list[ExecutionSpecialist] = []

    for specialist in specialists:
        if specialist.kind == SpecialistKind.HUMAN:
            human_specialists.append(specialist)
        reasons = _eligibility_reasons(
            requirements, specialist,
            remediation_target_profile_ref=remediation_target_profile_ref,
            resolved_execution_profiles=resolved_execution_profiles,
        )
        if reasons:
            ineligible.append(specialist.specialist_id)
            rejection_reasons[specialist.specialist_id] = reasons
        else:
            eligible.append(specialist)

    if eligible:
        ranked = sorted(eligible, key=lambda s: _rank_key(requirements, s))
        winner = ranked[0]
        if len(eligible) == 1:
            strength = RoutingConfidence.DETERMINISTIC
        else:
            runner_up = ranked[1]
            strength = (
                RoutingConfidence.AMBIGUOUS
                if _rank_key(requirements, winner)[:-1] == _rank_key(requirements, runner_up)[:-1]
                else RoutingConfidence.RANKED
            )
        return SpecialistRoutingDecision(
            mission_id=requirements.mission_id,
            sprint_id=requirements.sprint_id,
            outcome=RoutingOutcome.SELECTED,
            selected_specialist_id=winner.specialist_id,
            selected_specialist_kind=winner.kind,
            selected_execution_profile_ref=winner.execution_profile_ref,
            eligible_specialists=sorted(s.specialist_id for s in eligible),
            ineligible_specialists=sorted(ineligible),
            rejection_reasons=rejection_reasons,
            routing_reason=f"{winner.specialist_id} ({winner.kind.value}) ranked first among {len(eligible)} eligible specialist(s)",
            decision_strength=strength,
            requirements_fingerprint=requirements_fp,
            specialist_set_fingerprint=specialist_set_fp,
        )

    # No automated specialist survived eligibility filtering.
    outcome = RoutingOutcome.HUMAN_REQUIRED if allow_hitl else RoutingOutcome.NO_ELIGIBLE_SPECIALIST
    selected_human = sorted(human_specialists, key=lambda s: s.specialist_id)[0] if (
        outcome == RoutingOutcome.HUMAN_REQUIRED and human_specialists
    ) else None
    reason = (
        "no eligible automated specialist; policy allows HITL"
        if outcome == RoutingOutcome.HUMAN_REQUIRED
        else "no eligible specialist and HITL not authorized for this routing call"
    )
    return SpecialistRoutingDecision(
        mission_id=requirements.mission_id,
        sprint_id=requirements.sprint_id,
        outcome=outcome,
        selected_specialist_id=selected_human.specialist_id if selected_human else None,
        selected_specialist_kind=SpecialistKind.HUMAN if selected_human else None,
        selected_execution_profile_ref=None,
        eligible_specialists=[],
        ineligible_specialists=sorted(ineligible),
        rejection_reasons=rejection_reasons,
        routing_reason=reason,
        decision_strength=RoutingConfidence.ABSTAINED,
        requirements_fingerprint=requirements_fp,
        specialist_set_fingerprint=specialist_set_fp,
    )
