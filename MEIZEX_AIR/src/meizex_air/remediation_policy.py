"""AIR-BEW-006 — Deterministic Remediation Policy Engine.

Answers exactly one question: GIVEN A STRUCTURED FAILURE CLASSIFICATION, WHAT
IS THE SAFEST BOUNDED NEXT ACTION?

    VALIDATE (AIR-BEW-004) != CLASSIFY (AIR-BEW-005) != REMEDIATE (this module)
    AIR-BEW-004 answered "did this checkpoint satisfy its acceptance
    contract?"; AIR-BEW-005 answered "why, from supported evidence?"; this
    module answers "what is the safest bounded next action, given that
    classification?" It never re-derives either upstream answer.

    REMEDIATION DECISION != REMEDIATION EXECUTION
    This module DECIDES. It never retries anything, never mutates a sprint,
    never creates a child sprint, never calls a router, model, runtime, or
    tool, and never implements a HITL UI. `decide_remediation` returns a
    `RemediationDecision` - a plan for a decision, not the decision acted
    upon.

    ATTRIBUTION != AUTHORITY
    FailureClassification.component_attribution (AIR-BEW-005) naming a
    component (e.g. TOOL) never by itself authorizes an action against that
    component (e.g. "replace the tool") - every action still comes from the
    documented policy table below, never from attribution alone.

This module does not execute a sprint, retry, repartition, escalate, call a
model, call MHL, call CHASSIS, call a router, or implement a HITL UI.

Discovery findings (full internal report in the AIR-BEW-006 delivery):
- EXISTING_REMEDIATION_TYPES = none. No retry/escalation/policy taxonomy
  exists anywhere in AIR before this module.
- EXISTING_POLICY_TYPES = none directly reusable. CHASSIS_API has its own
  `ExecutionPolicyManifest`/budget-decision concepts (see
  MEIZEX_CHASSIS_API/policy.py, referenced only as READ-ONLY prior art, not
  imported - CHASSIS's policy governs PROVIDER/NETWORK eligibility, a
  different question from "what should THIS sprint's remediation be").
- EXISTING_RETRY_FIELDS = none on any AIR contract. `BoundedExecutionSprint`
  (AIR-BEW-001) has no retry-count field, `SprintCheckpoint` has no attempt
  index - this module's optional `AttemptState` is therefore a NEW, minimal,
  explicitly-caller-supplied input (never persisted, never invented from
  thin air), matching the task prompt's "do not invent a session engine."
- EXISTING_ESCALATION_FIELDS = `BoundedExecutionSprint.execution_profile_ref`
  (AIR-BEW-001) and `ExecutionProfile.profile_id` (execution_profiles.py)
  are both plain `str` references - the SAME convention this module's
  `PolicyContext.target_execution_profile_ref` reuses. No escalation-target
  resolution logic exists anywhere in AIR; this module never invents one -
  ESCALATE_PROFILE is only ever populated from an explicitly-supplied,
  already-authorized reference, never resolved/guessed here.
- EXISTING_EXECUTION_PROFILE_REFS = `execution_profiles.py`'s
  `evaluate_profile_eligibility` decides whether ONE named profile is
  eligible for given resource/policy constraints - it does not choose a
  replacement profile, and this module does not call it or duplicate its
  logic; `target_execution_profile_ref` is trusted as pre-authorized by
  whatever caller supplies `PolicyContext`.
- REUSABLE_RESULT_PATTERN = `FailureClassification`'s
  `canonical()`/`content_fingerprint()` pair (AIR-BEW-005) is reused
  directly for `RemediationDecision`, same shape every prior BEW module
  uses. `FAILURE_CLASS_PRECEDENCE` (AIR-BEW-005's own primary-selection
  tie-break tuple) is imported and reused verbatim as THIS module's
  secondary-failure tie-break, rather than defining a second, possibly
  divergent ranking of the same 17 classes.
- REUSABLE_SERIALIZATION = identity.canonical_json / identity.sha256_digest,
  the same convention every prior BEW module uses.
- NEXT.md already records `PARTITION BEFORE ESCALATION` and `FAIL SMALL` as
  CORE BES PRINCIPLES (see the BES ROADMAP section) - both are encoded here
  as real policy rules (see ACTION PRECEDENCE and the per-class ladder
  below), not just restated.

DECISION_STRENGTH is a bounded qualitative scale, never a pseudo-probability,
and is deliberately DERIVED from FailureClassification.classification_strength
(AIR-BEW-005) rather than invented fresh - a remediation decision can only be
as confident as the classification it is based on:
    classification_strength -> decision_strength
    DETERMINISTIC  -> DETERMINISTIC
    STRONG_SIGNAL  -> STRONG_POLICY_MATCH
    WEAK_SIGNAL    -> WEAK_POLICY_MATCH
    UNRESOLVED     -> ABSTAIN
Since AIR-BEW-005 guarantees UNKNOWN_FAILURE always carries
classification_strength=UNRESOLVED, this mapping alone already gives
UNKNOWN_FAILURE -> ABSTAIN with no special-casing needed - NO EVIDENCE ->
NO SPECULATIVE REMEDIATION falls out of the same mapping that produced
UNKNOWN_FAILURE in the first place.

Known, documented limitation: this mapping always reads
`classification_strength`, which AIR-BEW-005 computes for `primary_failure`
specifically. When a SECONDARY failure's policy constraint wins the final
action (see ACTION PRECEDENCE), `decision_strength` still reflects the
overall classification event's strength, not a per-secondary-class strength
AIR-BEW-005 does not expose. Re-deriving one here would mean re-classifying
inside the remediation layer (VALIDATE != CLASSIFY != REMEDIATE) - recorded
as a gap for a future AIR-BEW-005 enhancement, not solved by guessing here.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .bounded_execution import BoundedExecutionSprint
from .failure_classifier import (
    FAILURE_CLASS_PRECEDENCE,
    ClassificationStrength,
    FailureClass,
    FailureClassification,
)
from .identity import canonical_json, sha256_digest

REMEDIATION_POLICY_SCHEMA_VERSION = "1.0"
POLICY_VERSION = "1"


class RemediationAction(str, Enum):
    NO_ACTION = "NO_ACTION"
    RETRY_SAME = "RETRY_SAME"
    RETRY_REDUCED_SCOPE = "RETRY_REDUCED_SCOPE"
    REPARTITION = "REPARTITION"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    ESCALATE_PROFILE = "ESCALATE_PROFILE"
    HALT = "HALT"
    HITL = "HITL"


#: Explicit, documented action-safety precedence (most restrictive first).
#: Deliberately NOT enum declaration order above. Used to resolve conflicts
#: when primary_failure and secondary_failures suggest different baseline
#: actions - the MOST restrictive wins (see decide_remediation).
#: Rationale, derived from the task prompt's category-level precedence
#: (HALT/HITL > capability/configuration impossibility > dependency
#: blockers > budget/context reduction > evidence acquisition > bounded
#: retry > NO_ACTION), realized here as a total order over ACTIONS since
#: each category resolves to one or more of these 8 concrete actions:
#:   1. HITL              - most conservative: stop AND require a human.
#:   2. HALT               - stop; may resume without a human once whatever
#:                            blocked it is externally resolved.
#:   3. ESCALATE_PROFILE   - capability/configuration impossibility with an
#:                           explicitly authorized replacement.
#:   4. REPARTITION        - structural change; PARTITION BEFORE ESCALATION
#:                           puts it ahead of any escalation action but
#:                           behind stop/human-authorization actions.
#:   5. RETRY_REDUCED_SCOPE - budget/context reduction, a lighter structural
#:                            change than a full repartition.
#:   6. REQUEST_EVIDENCE   - evidence acquisition.
#:   7. RETRY_SAME         - bounded retry, the least restrictive real
#:                           "try again."
#:   8. NO_ACTION          - nothing needed.
_ACTION_PRECEDENCE: tuple[RemediationAction, ...] = (
    RemediationAction.HITL,
    RemediationAction.HALT,
    RemediationAction.ESCALATE_PROFILE,
    RemediationAction.REPARTITION,
    RemediationAction.RETRY_REDUCED_SCOPE,
    RemediationAction.REQUEST_EVIDENCE,
    RemediationAction.RETRY_SAME,
    RemediationAction.NO_ACTION,
)


class DecisionStrength(str, Enum):
    """Bounded qualitative scale. Never a pseudo-probability - see module
    docstring for the classification_strength -> decision_strength mapping."""

    DETERMINISTIC = "DETERMINISTIC"
    STRONG_POLICY_MATCH = "STRONG_POLICY_MATCH"
    WEAK_POLICY_MATCH = "WEAK_POLICY_MATCH"
    ABSTAIN = "ABSTAIN"


_STRENGTH_MAP: dict[ClassificationStrength, DecisionStrength] = {
    ClassificationStrength.DETERMINISTIC: DecisionStrength.DETERMINISTIC,
    ClassificationStrength.STRONG_SIGNAL: DecisionStrength.STRONG_POLICY_MATCH,
    ClassificationStrength.WEAK_SIGNAL: DecisionStrength.WEAK_POLICY_MATCH,
    ClassificationStrength.UNRESOLVED: DecisionStrength.ABSTAIN,
}


class AttemptState(BaseModel):
    """Minimal, caller-supplied, NOT persisted here (AIR has no attempt-
    history store - see module docstring). Absent -> conservative
    first-attempt policy (every count treated as 0)."""

    model_config = ConfigDict(extra="forbid")

    attempt_count: int = Field(default=0, ge=0)
    #: Consecutive prior attempts that produced the SAME primary_failure
    #: this decision is being made for. Drives every bounded-retry ladder
    #: below (CIRCUIT BREAKER PRINCIPLE: never authorize unbounded retry).
    same_failure_count: int = Field(default=0, ge=0)
    same_action_count: int = Field(default=0, ge=0)


class PolicyContext(BaseModel):
    """Minimal, caller-supplied policy inputs. Never resolved/guessed by
    this module - see EXISTING_ESCALATION_FIELDS in the module docstring."""

    model_config = ConfigDict(extra="forbid")

    #: An explicitly pre-authorized replacement profile reference. Only
    #: ever copied through to RemediationDecision.target_execution_profile_ref
    #: - never invented, never resolved from a candidate list here.
    target_execution_profile_ref: Optional[str] = None
    #: Explicit signal that RETRY_REDUCED_SCOPE/REPARTITION is not a viable
    #: option for this sprint (e.g. it is already maximally partitioned) -
    #: forces BUDGET_EXCEEDED/CONTEXT_SATURATION toward HITL instead of a
    #: silently-repeating reduction ladder. Never inferred from prose.
    partitioning_not_possible: bool = False
    #: Bounded-retry ceiling. Default matches the task prompt's suggested
    #: `max_same_retry = 1` - not hardcoded elsewhere in AIR, so this is the
    #: single place that number lives; callers with a different policy
    #: override it here rather than this module guessing per-mission values.
    max_same_retry: int = Field(default=1, ge=0)


class RemediationDecision(BaseModel):
    """Immutable/serializable. A DECISION, never an EXECUTION - see module
    docstring. Never mutates the sprint or failure_classification it was
    derived from."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = REMEDIATION_POLICY_SCHEMA_VERSION
    mission_id: str
    sprint_id: str
    checkpoint_id: str
    #: Reused directly from FailureClassification.content_fingerprint()
    #: (AIR-BEW-005) - not recomputed, not a new identity system.
    failure_classification_fingerprint: str

    action: RemediationAction
    reason_code: str
    reason: str
    decision_strength: DecisionStrength

    #: -1 whenever `action` consumes one bounded retry attempt
    #: (RETRY_SAME/RETRY_REDUCED_SCOPE), else None (no retry is being spent).
    retry_budget_delta: Optional[int] = None
    #: Fixed three-key hint set (see task prompt's RETRY REDUCED SCOPE
    #: section) - only populated when action == RETRY_REDUCED_SCOPE.
    scope_reduction_hint: Optional[dict[str, bool]] = None
    #: True only when action == REPARTITION. This module DECIDES repartition
    #: is required; it never generates the partition (see module docstring).
    repartition_required: bool = False
    #: Minimal structured hint, only populated when action == REQUEST_EVIDENCE
    #: - deliberately NOT a full evidence planner (see task prompt).
    evidence_request: Optional[dict[str, str]] = None
    #: Only populated when action == ESCALATE_PROFILE, and only ever a
    #: direct copy of PolicyContext.target_execution_profile_ref - never
    #: invented (see TARGET_PROFILE_INVENTED in the delivery report).
    target_execution_profile_ref: Optional[str] = None
    #: True only for TASK_DRIFT-driven RETRY_SAME with a task_lock present
    #: on the sprint - a hint the future execution layer may use to
    #: re-inject the task_lock, never enforced here.
    task_lock_reinforcement_requested: bool = False

    #: True only for action == HITL - HITL is a decision state, not a UI
    #: implementation (see module docstring); this module never prompts
    #: anyone.
    requires_human_approval: bool = False

    policy_version: str = POLICY_VERSION

    warnings: list[str] = Field(default_factory=list)

    def canonical(self) -> str:
        return canonical_json(self)

    def content_fingerprint(self) -> str:
        return sha256_digest(self)


def _find_signal_value(failure_classification: FailureClassification, signal_type: str) -> Optional[str]:
    for signal in failure_classification.signals:
        if signal.signal_type == signal_type:
            return signal.value
    return None


def _baseline_action_for_class(
    cls: FailureClass,
    sprint: BoundedExecutionSprint,
    failure_classification: FailureClassification,
    attempt_state: AttemptState,
    policy_context: PolicyContext,
) -> tuple[RemediationAction, str]:
    """One deterministic (action, reason_code) per failure class. Several
    branches below are documented UNREACHABLE under the current AIR-BEW-005
    classifier (it never emits CONFIGURATION_MISMATCH/TOOL_CALL_LOOP/
    TASK_RETENTION_FAILURE/PATH_GUESSING/SILENT_STALL - see
    failure_classifier.UNSUPPORTED_FAILURE_CLASSES) - they are defined here
    only for ONTOLOGY COMPLETENESS (every FailureClass has a policy, so this
    function never raises on a class it doesn't recognize), never fabricated
    as reachable behavior today."""
    same = attempt_state.same_failure_count
    max_retry = policy_context.max_same_retry

    if cls == FailureClass.CAPABILITY_FAILURE:
        if policy_context.target_execution_profile_ref:
            return RemediationAction.ESCALATE_PROFILE, "CAPABILITY_FAILURE_AUTHORIZED_TARGET"
        return RemediationAction.HITL, "NO_AUTHORIZED_ESCALATION_TARGET"

    if cls == FailureClass.CONFIGURATION_MISMATCH:  # UNREACHABLE today
        if policy_context.target_execution_profile_ref:
            return RemediationAction.ESCALATE_PROFILE, "CONFIGURATION_MISMATCH_AUTHORIZED_TARGET"
        return RemediationAction.HALT, "CONFIGURATION_MISMATCH_NO_AUTHORIZED_TARGET"

    if cls == FailureClass.DEPENDENCY_FAILURE:
        dependency_signal_value = _find_signal_value(failure_classification, "DEPENDENCY_STATE_BLOCKED")
        if dependency_signal_value == "UNVERIFIABLE":
            # No dependency state was supplied at all - go get it, don't
            # blindly retry a sprint whose prerequisite is simply unknown.
            return RemediationAction.REQUEST_EVIDENCE, "DEPENDENCY_STATE_UNVERIFIABLE_REQUEST_EVIDENCE"
        # FAIL (dependency ran and did not PASS) - retrying THIS sprint
        # cannot fix an already-failed prerequisite.
        return RemediationAction.HALT, "DEPENDENCY_NOT_PASSED_HALT"

    if cls == FailureClass.TOOL_FAILURE:
        if same >= max_retry:
            return RemediationAction.HITL, "REPEATED_TOOL_FAILURE"
        return RemediationAction.RETRY_SAME, "FIRST_TOOL_FAILURE_BOUNDED_RETRY"

    if cls == FailureClass.TOOL_CALL_LOOP:  # UNREACHABLE today
        if same >= max_retry:
            return RemediationAction.REPARTITION, "REPEATED_TOOL_CALL_LOOP_REPARTITION"
        return RemediationAction.HALT, "TOOL_CALL_LOOP_HALT_PENDING_TELEMETRY"

    if cls in (FailureClass.BUDGET_EXCEEDED, FailureClass.CONTEXT_SATURATION):
        if policy_context.partitioning_not_possible:
            return RemediationAction.HITL, f"{cls.value}_PARTITIONING_NOT_POSSIBLE"
        if same >= max_retry:
            return RemediationAction.REPARTITION, f"REPEATED_{cls.value}_REPARTITION"
        return RemediationAction.RETRY_REDUCED_SCOPE, f"FIRST_{cls.value}_REDUCED_SCOPE"

    if cls == FailureClass.CONTRADICTION:
        if same >= max_retry:
            return RemediationAction.REPARTITION, "REPEATED_CONTRADICTION_REPARTITION_ADJUDICATION"
        return RemediationAction.REQUEST_EVIDENCE, "FIRST_CONTRADICTION_REQUEST_EVIDENCE"

    if cls == FailureClass.FALSE_COMPLETION:
        if same >= max_retry:
            return RemediationAction.HITL, "REPEATED_FALSE_COMPLETION"
        return RemediationAction.RETRY_SAME, "FIRST_FALSE_COMPLETION_BOUNDED_RETRY"

    if cls == FailureClass.INVALID_OUTPUT:
        if same == 0:
            return RemediationAction.RETRY_SAME, "FIRST_INVALID_OUTPUT_BOUNDED_RETRY"
        if same == max_retry:
            return RemediationAction.RETRY_REDUCED_SCOPE, "REPEATED_INVALID_OUTPUT_REDUCED_SCOPE"
        return RemediationAction.HALT, "REPEATED_INVALID_OUTPUT_HALT"

    if cls == FailureClass.EVIDENCE_STARVATION:
        return RemediationAction.REQUEST_EVIDENCE, "EVIDENCE_STARVATION_REQUEST_EVIDENCE"

    if cls == FailureClass.UNVERIFIABLE_COMPLETION:
        if same >= max_retry:
            return RemediationAction.HITL, "REPEATED_UNVERIFIABLE_COMPLETION"
        return RemediationAction.REQUEST_EVIDENCE, "FIRST_UNVERIFIABLE_COMPLETION_REQUEST_EVIDENCE"

    if cls == FailureClass.TASK_DRIFT:
        if same >= max_retry:
            return RemediationAction.RETRY_REDUCED_SCOPE, "REPEATED_TASK_DRIFT_REDUCED_SCOPE"
        return RemediationAction.RETRY_SAME, "FIRST_TASK_DRIFT_REINFORCE_TASK_LOCK"

    if cls == FailureClass.TASK_RETENTION_FAILURE:  # UNREACHABLE today
        if same >= max_retry:
            return RemediationAction.REPARTITION, "REPEATED_TASK_RETENTION_FAILURE_REPARTITION"
        return RemediationAction.RETRY_REDUCED_SCOPE, "TASK_RETENTION_FAILURE_REDUCED_SCOPE_PENDING_TELEMETRY"

    if cls == FailureClass.PATH_GUESSING:  # UNREACHABLE today
        return RemediationAction.RETRY_REDUCED_SCOPE, "PATH_GUESSING_REDUCED_SCOPE_PENDING_TELEMETRY"

    if cls == FailureClass.SILENT_STALL:  # UNREACHABLE today
        if same >= max_retry:
            return RemediationAction.HITL, "REPEATED_SILENT_STALL"
        return RemediationAction.RETRY_SAME, "FIRST_SILENT_STALL_RETRY_PENDING_TELEMETRY"

    # FailureClass.UNKNOWN_FAILURE and any future addition this table has
    # not been updated for: fail closed to the most conservative action
    # rather than guessing. UNKNOWN_FAILURE's own decision_strength is
    # separately guaranteed ABSTAIN via classification_strength=UNRESOLVED
    # (see module docstring) - HITL here is the ACTION, ABSTAIN is the
    # STRENGTH, two different fields answering two different questions.
    return RemediationAction.HITL, "UNKNOWN_OR_UNMAPPED_FAILURE_ABSTAIN"


def decide_remediation(
    sprint: BoundedExecutionSprint,
    failure_classification: FailureClassification,
    *,
    attempt_state: Optional[AttemptState] = None,
    policy_context: Optional[PolicyContext] = None,
) -> RemediationDecision:
    """Pure, deterministic. No I/O, no store, no model call, no execution of
    any kind. Never mutates `sprint` or `failure_classification`. Same
    inputs always produce the same RemediationDecision (content_fingerprint()
    is stable)."""
    attempt_state = attempt_state if attempt_state is not None else AttemptState()
    policy_context = policy_context if policy_context is not None else PolicyContext()

    if failure_classification.primary_failure is None:
        # Accepted checkpoint - nothing to remediate. NO_ACTION for
        # successful work, never a retry.
        return RemediationDecision(
            mission_id=failure_classification.mission_id,
            sprint_id=failure_classification.sprint_id,
            checkpoint_id=failure_classification.checkpoint_id,
            failure_classification_fingerprint=failure_classification.content_fingerprint(),
            action=RemediationAction.NO_ACTION,
            reason_code="ACCEPTED_NO_FAILURE",
            reason="validation_result.accepted was True - nothing to remediate",
            decision_strength=DecisionStrength.DETERMINISTIC,
        )

    present_classes = [failure_classification.primary_failure, *failure_classification.secondary_failures]
    candidates = [
        (cls, *_baseline_action_for_class(cls, sprint, failure_classification, attempt_state, policy_context))
        for cls in present_classes
    ]

    def _sort_key(candidate: tuple[FailureClass, RemediationAction, str]) -> tuple[int, int]:
        cls, action, _reason_code = candidate
        return (_ACTION_PRECEDENCE.index(action), FAILURE_CLASS_PRECEDENCE.index(cls))

    winning_cls, winning_action, winning_reason_code = min(candidates, key=_sort_key)

    decision_strength = _STRENGTH_MAP[failure_classification.classification_strength]

    retry_budget_delta: Optional[int] = None
    scope_reduction_hint: Optional[dict[str, bool]] = None
    repartition_required = False
    evidence_request: Optional[dict[str, str]] = None
    target_execution_profile_ref: Optional[str] = None
    task_lock_reinforcement_requested = False

    if winning_action in (RemediationAction.RETRY_SAME, RemediationAction.RETRY_REDUCED_SCOPE):
        retry_budget_delta = -1
    if winning_action == RemediationAction.RETRY_REDUCED_SCOPE:
        scope_reduction_hint = {
            "reduce_context": True,
            "reduce_scope": True,
            "preserve_verified_state": True,
        }
    if winning_action == RemediationAction.REPARTITION:
        repartition_required = True
    if winning_action == RemediationAction.REQUEST_EVIDENCE:
        evidence_request = {
            "target_question": f"resolve blocking signal(s) for {winning_cls.value} on sprint {sprint.sprint_id}",
        }
    if winning_action == RemediationAction.ESCALATE_PROFILE:
        target_execution_profile_ref = policy_context.target_execution_profile_ref
    if (
        winning_action == RemediationAction.RETRY_SAME
        and winning_cls == FailureClass.TASK_DRIFT
        and sprint.task_lock is not None
    ):
        task_lock_reinforcement_requested = True

    return RemediationDecision(
        mission_id=failure_classification.mission_id,
        sprint_id=failure_classification.sprint_id,
        checkpoint_id=failure_classification.checkpoint_id,
        failure_classification_fingerprint=failure_classification.content_fingerprint(),
        action=winning_action,
        reason_code=winning_reason_code,
        reason=f"{winning_cls.value} -> {winning_action.value} ({winning_reason_code})",
        decision_strength=decision_strength,
        retry_budget_delta=retry_budget_delta,
        scope_reduction_hint=scope_reduction_hint,
        repartition_required=repartition_required,
        evidence_request=evidence_request,
        target_execution_profile_ref=target_execution_profile_ref,
        task_lock_reinforcement_requested=task_lock_reinforcement_requested,
        requires_human_approval=(winning_action == RemediationAction.HITL),
    )
