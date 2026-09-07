"""AIR-BEW-005 — Deterministic Execution Failure Ontology / Failure Classifier.

Answers exactly one question: IF A BOUNDED SPRINT WAS NOT ACCEPTED, WHAT
FAILURE SIGNALS ARE ACTUALLY SUPPORTED BY THE AVAILABLE EVIDENCE?

    VALIDATE   != CLASSIFY != REMEDIATE
    AIR-BEW-004's validate_checkpoint() already answered "did this candidate
    checkpoint satisfy its acceptance contract?" This module never re-derives
    that answer - it only classifies WHY an already-rejected
    AcceptanceValidationResult was rejected, from signals that already exist
    on sprint / checkpoint / validation_result / (optional) context_metadata.
    AIR-BEW-006 owns what AIR should do about it (retry / repartition /
    escalation) - this module returns no remediation of any kind.

    FAILURE_CLASS != RESPONSIBLE_COMPONENT
    A failure mode may be observable without proving root cause -
    `component_attribution` defaults to UNKNOWN and is only set to something
    else when a signal directly and unambiguously names the responsible
    component (see COMPONENT ATTRIBUTION below).

This module does not execute a sprint, retry, repartition, escalate, call a
model, call MHL, call CHASSIS, or parse raw backend logs. It performs no
remediation and suggests none.

Discovery findings (full internal report in the AIR-BEW-005 delivery):
- EXISTING_FAILURE_TYPES = SprintOutcome (bounded_execution.py): PENDING /
  PASS / INCOMPLETE / UNSUPPORTED / TASK_DRIFT / BUDGET_EXCEEDED /
  TOOL_FAILURE / CONTRADICTION / CAPABILITY_FAILURE / INVALID_OUTPUT - the
  only existing executor-facing failure taxonomy in AIR. Six of its members
  (TASK_DRIFT/BUDGET_EXCEEDED/TOOL_FAILURE/CONTRADICTION/CAPABILITY_FAILURE/
  INVALID_OUTPUT) map 1:1 onto FailureClass members of the same name and are
  read here as ONE structured signal each (see EXPLICIT_OUTCOME_MAP below) -
  never as unquestioned classifier authority. PENDING/PASS never appear as
  signals (a checkpoint is only classified once its validation was rejected).
  INCOMPLETE/UNSUPPORTED are deliberately left UNMAPPED: no member of this
  module's ontology means exactly "generic incomplete work", and inventing
  one to fit the label would be exactly the "plausible-sounding label"
  fabrication this sprint must avoid - an INCOMPLETE/UNSUPPORTED checkpoint
  is classified from its OTHER signals (validator checks, context metadata)
  or, absent those, from UNKNOWN_FAILURE.
- EXISTING_STATUS_TYPES = CheckpointStatus (record lifecycle - AIR-BEW-004
  already separated it from execution outcome; not reused here since a
  REJECTED-status checkpoint is a contract-usage error, not a sprint failure
  class); acceptance_validator.CheckStatus (PASS/FAIL/UNVERIFIABLE) - reused
  structurally: a required FAIL/UNVERIFIABLE check is exactly the material
  AcceptanceValidationResult.failed_checks already aggregates, re-read here
  check-by-check (not by re-parsing the string list) so signal `details` can
  be attached.
- EXISTING_SIGNAL_FIELDS = ValidationCheckResult.check_id/status/required/
  details (acceptance_validator.py, AIR-BEW-004); ContextMetadata.
  truncation_applied (context_compiler.py, AIR-BEW-003/003A, optional input
  here) - checkpoint.open_questions/unresolved_items/findings/evidence_refs/
  confirmed_paths are NOT re-interpreted directly here; AIR-BEW-004 already
  interprets them into ValidationCheckResult, and re-deriving a second,
  possibly-divergent interpretation from the same raw fields would violate
  VALIDATE != CLASSIFY (this module classifies the validator's OUTPUT, not
  the checkpoint's raw fields a second time) - the one narrow exception is
  checkpoint.failure_class itself, which the validator does not repackage
  into a check and which the ontology needs as a first-class signal.
- EXISTING_ATTRIBUTION_FIELDS = none. No component-attribution concept
  exists anywhere in AIR before this module - new territory, kept
  deliberately minimal.
- REUSABLE_RESULT_PATTERN = AcceptanceValidationResult's
  `checks: list[ValidationCheckResult]` + `canonical()`/`content_fingerprint()`
  shape (AIR-BEW-004) is reused directly: FailureClassification carries
  `signals: list[FailureSignal]` and the identical canonical()/
  content_fingerprint() method pair.
- REUSABLE_SERIALIZATION = identity.canonical_json / identity.sha256_digest,
  the same convention every prior BEW module uses.

Verification trust order (unchanged from AIR-BEW-004 - not re-implemented,
only consumed):
    1. DETERMINISTIC VERIFIER   <- AIR-BEW-004's validate_checkpoint
    2. STRUCTURED EVIDENCE      <- AIR-BEW-004's ValidationCheckResult.details
    3. INDEPENDENT REVIEWER     <- future, not implemented
    4. EXECUTOR SELF-ASSESSMENT <- lowest authority; checkpoint.failure_class
       is read here as ONE structured signal, not as ground truth - see
       FALSE_COMPLETION, where a self-reported PASS is explicitly checked
       AGAINST the validator's independent `accepted=False`.

CLASSIFICATION_STRENGTH is a bounded qualitative scale, never a
pseudo-probability:
    DETERMINISTIC  - directly proven by structured state / validator result
                      / explicit status
    STRONG_SIGNAL  - multiple compatible structured indicators
    WEAK_SIGNAL    - one indirect but meaningful structured indicator
    UNRESOLVED     - evidence insufficient for a confident class

NO EVIDENCE -> NO FAILURE CLAIM. UNKNOWN_FAILURE is a first-class, VALID
result - never a temporary placeholder pending a "better" guess.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from .acceptance_validator import AcceptanceValidationResult, CheckStatus
from .bounded_execution import BoundedExecutionSprint, SprintCheckpoint, SprintOutcome
from .context_compiler import ContextMetadata
from .identity import canonical_json, sha256_digest

FAILURE_ONTOLOGY_VERSION = "1.0"
CLASSIFIER_VERSION = "1"


# ---------------------------------------------------------------------------
# Failure ontology - minimum initial set
# ---------------------------------------------------------------------------


class FailureClass(str, Enum):
    """The bounded initial Execution Failure Ontology. Presence in this enum
    means the class is DEFINED and DOCUMENTED - it does NOT mean the
    classifier below can currently emit it (see SUPPORTED_FAILURE_CLASSES /
    DETECTABILITY_NOTES)."""

    CAPABILITY_FAILURE = "CAPABILITY_FAILURE"
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    TOOL_FAILURE = "TOOL_FAILURE"
    TOOL_CALL_LOOP = "TOOL_CALL_LOOP"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CONTEXT_SATURATION = "CONTEXT_SATURATION"
    CONTRADICTION = "CONTRADICTION"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    EVIDENCE_STARVATION = "EVIDENCE_STARVATION"
    FALSE_COMPLETION = "FALSE_COMPLETION"
    UNVERIFIABLE_COMPLETION = "UNVERIFIABLE_COMPLETION"
    TASK_DRIFT = "TASK_DRIFT"
    TASK_RETENTION_FAILURE = "TASK_RETENTION_FAILURE"
    PATH_GUESSING = "PATH_GUESSING"
    SILENT_STALL = "SILENT_STALL"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


#: Explicit, documented primary-selection precedence (most authoritative /
#: most execution-blocking first). Deliberately NOT enum declaration order -
#: declaration order above is grouped for readability, this tuple is the
#: actual policy and is exhaustively checked against the enum in tests.
#: Rationale (see AIR-BEW-005 delivery for the full write-up):
#:   1. CAPABILITY_FAILURE / CONFIGURATION_MISMATCH - execution was
#:      impossible or misconfigured from the start; nothing downstream is
#:      trustworthy once this is true.
#:   2. DEPENDENCY_FAILURE / TOOL_FAILURE / TOOL_CALL_LOOP - infrastructure-
#:      level failures the sprint's own output cannot compensate for.
#:   3. BUDGET_EXCEEDED / CONTEXT_SATURATION - hard resource ceilings.
#:   4. CONTRADICTION / FALSE_COMPLETION - the checkpoint's own claims
#:      conflict with reality (an explicit contradiction status, or a
#:      self-reported PASS the independent validator rejected). Ranked
#:      ABOVE plain output/evidence defects: knowing the executor's
#:      self-assessment cannot be trusted is a more important thing to
#:      surface than the specific defect that tripped the validator - that
#:      defect still appears as a secondary_failure, never discarded.
#:   5. INVALID_OUTPUT / EVIDENCE_STARVATION - proven, specific output-level
#:      defects in an otherwise-executed, honestly-self-assessed sprint.
#:   6. UNVERIFIABLE_COMPLETION - AIR could not PROVE completion; weaker
#:      than a proven defect (see module docstring), so ranked below the
#:      proven-defect classes above it.
#:   7. TASK_DRIFT / TASK_RETENTION_FAILURE / PATH_GUESSING / SILENT_STALL -
#:      task/search behavior failures.
#:   8. UNKNOWN_FAILURE - absolute fallback, never pre-empts a real class.
_FAILURE_CLASS_PRECEDENCE: tuple[FailureClass, ...] = (
    FailureClass.CAPABILITY_FAILURE,
    FailureClass.CONFIGURATION_MISMATCH,
    FailureClass.DEPENDENCY_FAILURE,
    FailureClass.TOOL_FAILURE,
    FailureClass.TOOL_CALL_LOOP,
    FailureClass.BUDGET_EXCEEDED,
    FailureClass.CONTEXT_SATURATION,
    FailureClass.CONTRADICTION,
    FailureClass.FALSE_COMPLETION,
    FailureClass.INVALID_OUTPUT,
    FailureClass.EVIDENCE_STARVATION,
    FailureClass.UNVERIFIABLE_COMPLETION,
    FailureClass.TASK_DRIFT,
    FailureClass.TASK_RETENTION_FAILURE,
    FailureClass.PATH_GUESSING,
    FailureClass.SILENT_STALL,
    FailureClass.UNKNOWN_FAILURE,
)

#: Public alias - AIR-BEW-006 (remediation_policy.py) reuses this exact
#: precedence order as its secondary-failure tie-break, rather than
#: inventing a second ranking of the same 17 classes.
FAILURE_CLASS_PRECEDENCE = _FAILURE_CLASS_PRECEDENCE

#: Classes this classifier can actually EMIT today, given real structured
#: AIR signals. Emission code below never produces a class outside this set.
SUPPORTED_FAILURE_CLASSES = frozenset(
    {
        FailureClass.CAPABILITY_FAILURE,
        FailureClass.DEPENDENCY_FAILURE,
        FailureClass.TOOL_FAILURE,
        FailureClass.BUDGET_EXCEEDED,
        FailureClass.CONTEXT_SATURATION,
        FailureClass.CONTRADICTION,
        FailureClass.INVALID_OUTPUT,
        FailureClass.EVIDENCE_STARVATION,
        FailureClass.FALSE_COMPLETION,
        FailureClass.UNVERIFIABLE_COMPLETION,
        FailureClass.TASK_DRIFT,
        FailureClass.UNKNOWN_FAILURE,
    }
)

#: Present in the ontology, never emitted today - no structured AIR signal
#: distinguishes them yet. DETECTABILITY = FUTURE / PARTIAL (see individual
#: notes) until a structured execution-telemetry contract exists.
UNSUPPORTED_FAILURE_CLASSES = frozenset(FailureClass) - SUPPORTED_FAILURE_CLASSES

DETECTABILITY_NOTES: dict[FailureClass, str] = {
    FailureClass.CONFIGURATION_MISMATCH: (
        "FUTURE - no configuration/profile validation signal is exposed to "
        "this classifier yet"
    ),
    FailureClass.TOOL_CALL_LOOP: (
        "FUTURE - requires structured execution telemetry (same tool/args "
        "repeated N times, no new evidence); no such contract exists yet"
    ),
    FailureClass.TASK_RETENTION_FAILURE: (
        "PARTIAL - requires a later-turn task-lock contradiction signal; no "
        "such contract exists on SprintCheckpoint yet"
    ),
    FailureClass.PATH_GUESSING: (
        "FUTURE - requires structured execution telemetry (requested path, "
        "confirmed-before-call flag, tool outcome); no such contract exists yet"
    ),
    FailureClass.SILENT_STALL: (
        "FUTURE - requires a structured observation of no-content/no-tool-"
        "call/no-error termination; no such contract exists yet"
    ),
}


class ClassificationStrength(str, Enum):
    """Bounded qualitative scale. Never a pseudo-probability - see module
    docstring for the meaning of each value."""

    DETERMINISTIC = "DETERMINISTIC"
    STRONG_SIGNAL = "STRONG_SIGNAL"
    WEAK_SIGNAL = "WEAK_SIGNAL"
    UNRESOLVED = "UNRESOLVED"


_STRENGTH_RANK: dict[ClassificationStrength, int] = {
    ClassificationStrength.UNRESOLVED: 0,
    ClassificationStrength.WEAK_SIGNAL: 1,
    ClassificationStrength.STRONG_SIGNAL: 2,
    ClassificationStrength.DETERMINISTIC: 3,
}


class ComponentAttribution(str, Enum):
    """FAILURE_CLASS != RESPONSIBLE_COMPONENT. Defaults to UNKNOWN; only set
    to something else when a signal directly and unambiguously names the
    responsible component (see classify_failure)."""

    AIR = "AIR"
    MHL = "MHL"
    CHASSIS = "CHASSIS"
    MODEL = "MODEL"
    RUNTIME = "RUNTIME"
    TOOL = "TOOL"
    POLICY = "POLICY"
    CONTEXT_COMPILER = "CONTEXT_COMPILER"
    STORE = "STORE"
    EXTERNAL_PROVIDER = "EXTERNAL_PROVIDER"
    UNKNOWN = "UNKNOWN"


class SignalSource(str, Enum):
    ACCEPTANCE_VALIDATOR = "ACCEPTANCE_VALIDATOR"
    CHECKPOINT = "CHECKPOINT"
    CONTEXT_COMPILER = "CONTEXT_COMPILER"
    SPRINT_CONTRACT = "SPRINT_CONTRACT"
    STORE = "STORE"
    EXPLICIT_EXECUTION_OBSERVATION = "EXPLICIT_EXECUTION_OBSERVATION"


class FailureSignal(BaseModel):
    """One structured, sourced observation that supports (part of) a failure
    classification. Never free prose - `value`/`details` are structured data
    already present on sprint/checkpoint/validation_result/context_metadata."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    signal_type: str
    source: SignalSource
    value: Any
    details: dict[str, Any] = Field(default_factory=dict)
    strength: ClassificationStrength


class FailureClassification(BaseModel):
    """Immutable/serializable. Never mutates the sprint, checkpoint, or
    validation_result it was derived from. Persistence of this result is
    explicit future work (not implemented here, same posture AIR-BEW-004
    took for AcceptanceValidationResult)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = FAILURE_ONTOLOGY_VERSION
    mission_id: str
    sprint_id: str
    checkpoint_id: str
    checkpoint_version: int

    #: None only when the underlying validation_result was accepted - there
    #: is no failure to classify.
    primary_failure: Optional[FailureClass] = None
    secondary_failures: list[FailureClass] = Field(default_factory=list)

    signals: list[FailureSignal] = Field(default_factory=list)

    #: None only alongside primary_failure=None.
    classification_strength: Optional[ClassificationStrength] = None

    component_attribution: Optional[ComponentAttribution] = None
    component_attribution_strength: Optional[ClassificationStrength] = None

    classifier_version: str = CLASSIFIER_VERSION

    #: Reused directly from AcceptanceValidationResult.content_fingerprint()
    #: / .checkpoint_fingerprint (AIR-BEW-004) - not recomputed, not a new
    #: identity system.
    source_validation_fingerprint: str
    source_checkpoint_fingerprint: str

    warnings: list[str] = Field(default_factory=list)

    def canonical(self) -> str:
        return canonical_json(self)

    def content_fingerprint(self) -> str:
        return sha256_digest(self)


# ---------------------------------------------------------------------------
# Signal detection - one small, fixed, documented mapping per signal source
# ---------------------------------------------------------------------------

#: checkpoint.failure_class -> FailureClass, for the 6 SprintOutcome members
#: that already mean exactly one FailureClass. TASK_DRIFT is included here
#: (not left to future telemetry) because SprintOutcome.TASK_DRIFT is
#: already an explicit, deterministic status an executor/upstream caller can
#: set - the FUTURE-only TASK_DRIFT signal this ontology does NOT yet
#: support is a *validator-level* task-lock mismatch (see module docstring).
_EXPLICIT_OUTCOME_MAP: dict[SprintOutcome, FailureClass] = {
    SprintOutcome.TASK_DRIFT: FailureClass.TASK_DRIFT,
    SprintOutcome.BUDGET_EXCEEDED: FailureClass.BUDGET_EXCEEDED,
    SprintOutcome.TOOL_FAILURE: FailureClass.TOOL_FAILURE,
    SprintOutcome.CONTRADICTION: FailureClass.CONTRADICTION,
    SprintOutcome.CAPABILITY_FAILURE: FailureClass.CAPABILITY_FAILURE,
    SprintOutcome.INVALID_OUTPUT: FailureClass.INVALID_OUTPUT,
}


def _detect_signals(
    sprint: BoundedExecutionSprint,
    checkpoint: SprintCheckpoint,
    validation_result: AcceptanceValidationResult,
    context_metadata: Optional[ContextMetadata],
) -> dict[FailureClass, list[FailureSignal]]:
    by_class: dict[FailureClass, list[FailureSignal]] = {}

    def add(cls: FailureClass, signal: FailureSignal) -> None:
        by_class.setdefault(cls, []).append(signal)

    # --- Explicit checkpoint.failure_class signal ---------------------
    # ONE signal, never unquestioned authority (see FALSE_COMPLETION below,
    # where this exact field is checked AGAINST the validator's own verdict).
    mapped = _EXPLICIT_OUTCOME_MAP.get(checkpoint.failure_class) if checkpoint.failure_class else None
    if mapped is not None:
        add(
            mapped,
            FailureSignal(
                signal_id="CHECKPOINT_FAILURE_CLASS",
                signal_type=f"EXPLICIT_{checkpoint.failure_class.value}_STATUS",
                source=SignalSource.CHECKPOINT,
                value=checkpoint.failure_class.value,
                details={"checkpoint_id": checkpoint.checkpoint_id},
                strength=ClassificationStrength.DETERMINISTIC,
            ),
        )

    # --- Validator check-level signals ---------------------------------
    # Only REQUIRED, blocking (FAIL/UNVERIFIABLE) checks carry classifier
    # authority - optional checks are already fully surfaced as
    # AcceptanceValidationResult.warnings by AIR-BEW-004 and are not
    # re-classified here.
    for check in validation_result.checks:
        if not check.required or check.status not in (CheckStatus.FAIL, CheckStatus.UNVERIFIABLE):
            continue

        if check.check_id == "DEPENDENCY_STATE":
            # Both "explicitly missing/not-passed" (FAIL) and "no dependency
            # state supplied at all" (UNVERIFIABLE, fail-closed per
            # AIR-BEW-004) are DEPENDENCY_FAILURE - in both cases the
            # dependency relationship, not the sprint's own output, is what
            # blocked acceptance.
            add(
                FailureClass.DEPENDENCY_FAILURE,
                FailureSignal(
                    signal_id=f"VALIDATION::{check.check_id}",
                    signal_type="DEPENDENCY_STATE_BLOCKED",
                    source=SignalSource.ACCEPTANCE_VALIDATOR,
                    value=check.status.value,
                    details=dict(check.details),
                    strength=ClassificationStrength.DETERMINISTIC,
                ),
            )
            continue

        if check.status == CheckStatus.UNVERIFIABLE:
            # Required criterion/output-contract entry AIR cannot verify at
            # all - distinct from a proven structural defect (INVALID_OUTPUT)
            # or a proven absence of evidence (EVIDENCE_STARVATION): this
            # means AIR cannot PROVE completion, not that it disproved it.
            add(
                FailureClass.UNVERIFIABLE_COMPLETION,
                FailureSignal(
                    signal_id=f"VALIDATION::{check.check_id}",
                    signal_type="REQUIRED_CHECK_UNVERIFIABLE",
                    source=SignalSource.ACCEPTANCE_VALIDATOR,
                    value=check.status.value,
                    details={"check_id": check.check_id, **check.details},
                    strength=ClassificationStrength.DETERMINISTIC,
                ),
            )
            continue

        # From here: status == FAIL, required, not DEPENDENCY_STATE.
        if "evidence" in check.check_id.lower():
            add(
                FailureClass.EVIDENCE_STARVATION,
                FailureSignal(
                    signal_id=f"VALIDATION::{check.check_id}",
                    signal_type="REQUIRED_EVIDENCE_CHECK_FAILED",
                    source=SignalSource.ACCEPTANCE_VALIDATOR,
                    value=check.status.value,
                    details={"check_id": check.check_id, **check.details},
                    strength=ClassificationStrength.DETERMINISTIC,
                ),
            )
            continue

        if check.check_id.startswith("ACCEPTANCE::") or check.check_id.startswith("OUTPUT_CONTRACT::"):
            add(
                FailureClass.INVALID_OUTPUT,
                FailureSignal(
                    signal_id=f"VALIDATION::{check.check_id}",
                    signal_type="REQUIRED_OUTPUT_CHECK_FAILED",
                    source=SignalSource.ACCEPTANCE_VALIDATOR,
                    value=check.status.value,
                    details={"check_id": check.check_id, **check.details},
                    strength=ClassificationStrength.DETERMINISTIC,
                ),
            )
            continue

        # Remaining required-FAIL checks are the structural identity/status
        # checks (IDENTITY_MISSION_MATCH, IDENTITY_SPRINT_MATCH,
        # CHECKPOINT_STATUS_VALID, EXECUTION_OUTCOME_PASS). Deliberately NOT
        # mapped to a FailureClass: an identity mismatch is a contract-usage
        # error the ontology has no member for, and EXECUTION_OUTCOME_PASS
        # FAIL is already fully explained by the explicit-status signal
        # above (or, if failure_class was PENDING/INCOMPLETE/UNSUPPORTED,
        # by no signal at all - see module docstring on why those two are
        # left unmapped rather than forced into an ill-fitting class).

    # --- FALSE_COMPLETION: self-reported PASS contradicted by the ---------
    # independent validator. A compound signal (two independent structured
    # facts must both hold), never a bare trust of the self-report.
    if (
        checkpoint.failure_class == SprintOutcome.PASS
        and not validation_result.accepted
        and validation_result.failed_checks
    ):
        add(
            FailureClass.FALSE_COMPLETION,
            FailureSignal(
                signal_id="SELF_REPORTED_PASS_REJECTED",
                signal_type="SELF_REPORTED_PASS_CONTRADICTED_BY_VALIDATOR",
                source=SignalSource.CHECKPOINT,
                value=checkpoint.failure_class.value,
                details={"failed_checks": list(validation_result.failed_checks)},
                strength=ClassificationStrength.STRONG_SIGNAL,
            ),
        )

    # --- Context Compiler signal (optional input) --------------------
    if context_metadata is not None and context_metadata.truncation_applied:
        add(
            FailureClass.CONTEXT_SATURATION,
            FailureSignal(
                signal_id="CONTEXT_TRUNCATION_APPLIED",
                signal_type="CONTEXT_COMPILER_TRUNCATION",
                source=SignalSource.CONTEXT_COMPILER,
                value=True,
                details={
                    "omitted_checkpoint_count": context_metadata.omitted_checkpoint_count,
                    "omitted_known_fact_count": context_metadata.omitted_known_fact_count,
                    "omitted_open_question_count": context_metadata.omitted_open_question_count,
                    "omitted_evidence_ref_count": context_metadata.omitted_evidence_ref_count,
                },
                # WEAK_SIGNAL: truncation having occurred does not by itself
                # prove it CAUSED this checkpoint's rejection - one indirect
                # but meaningful indicator, not a proven cause.
                strength=ClassificationStrength.WEAK_SIGNAL,
            ),
        )

    return by_class


# ---------------------------------------------------------------------------
# Top-level pure classifier
# ---------------------------------------------------------------------------


def classify_failure(
    sprint: BoundedExecutionSprint,
    checkpoint: SprintCheckpoint,
    validation_result: AcceptanceValidationResult,
    *,
    context_metadata: Optional[ContextMetadata] = None,
    execution_observations: Optional[Mapping[str, Any]] = None,
) -> FailureClassification:
    """Pure, deterministic. No I/O, no store, no model call. Never mutates
    `sprint`, `checkpoint`, or `validation_result`. Same inputs always
    produce the same FailureClassification (content_fingerprint() is stable).

    `execution_observations` is accepted for forward compatibility with a
    future structured execution-telemetry contract (see DETECTABILITY_NOTES:
    TOOL_CALL_LOOP / PATH_GUESSING / SILENT_STALL / TASK_RETENTION_FAILURE)
    but is NOT read by this version - no such contract exists in AIR yet,
    and inventing one speculatively is explicitly out of scope for this
    sprint. Present in the signature only so callers do not need to change
    it when that contract lands.
    """
    del execution_observations  # accepted, intentionally unused - see docstring

    if validation_result.accepted:
        return FailureClassification(
            mission_id=sprint.mission_id,
            sprint_id=sprint.sprint_id,
            checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_version=checkpoint.checkpoint_version,
            source_validation_fingerprint=validation_result.content_fingerprint(),
            source_checkpoint_fingerprint=validation_result.checkpoint_fingerprint,
        )

    by_class = _detect_signals(sprint, checkpoint, validation_result, context_metadata)

    if not by_class:
        # Acceptance was rejected but no supported class can be proven from
        # available structured signals. NO EVIDENCE -> NO FAILURE CLAIM:
        # UNKNOWN_FAILURE is the correct, valid answer, not a placeholder.
        by_class = {FailureClass.UNKNOWN_FAILURE: []}

    present_classes = [cls for cls in _FAILURE_CLASS_PRECEDENCE if cls in by_class]
    primary = present_classes[0]
    secondary = present_classes[1:]

    def _strength_for(cls: FailureClass) -> ClassificationStrength:
        signals_for_class = by_class.get(cls) or []
        if not signals_for_class:
            return ClassificationStrength.UNRESOLVED
        return max((s.strength for s in signals_for_class), key=lambda s: _STRENGTH_RANK[s])

    all_signals: list[FailureSignal] = [s for signals_for_class in by_class.values() for s in signals_for_class]
    all_signals.sort(key=lambda s: s.signal_id)

    # Component attribution: deliberately minimal. Only TOOL_FAILURE has a
    # signal that directly and unambiguously names its responsible
    # component today (the tool itself) - every other primary failure keeps
    # component_attribution=UNKNOWN rather than guessing (e.g. CONTEXT_
    # SATURATION does NOT attribute to CONTEXT_COMPILER: truncating under
    # budget pressure is the compiler doing its documented job correctly,
    # not evidence the compiler is at fault).
    component_attribution = ComponentAttribution.UNKNOWN
    component_attribution_strength = ClassificationStrength.UNRESOLVED
    if primary == FailureClass.TOOL_FAILURE:
        component_attribution = ComponentAttribution.TOOL
        component_attribution_strength = ClassificationStrength.DETERMINISTIC

    return FailureClassification(
        mission_id=sprint.mission_id,
        sprint_id=sprint.sprint_id,
        checkpoint_id=checkpoint.checkpoint_id,
        checkpoint_version=checkpoint.checkpoint_version,
        primary_failure=primary,
        secondary_failures=secondary,
        signals=all_signals,
        classification_strength=_strength_for(primary),
        component_attribution=component_attribution,
        component_attribution_strength=component_attribution_strength,
        source_validation_fingerprint=validation_result.content_fingerprint(),
        source_checkpoint_fingerprint=validation_result.checkpoint_fingerprint,
    )
