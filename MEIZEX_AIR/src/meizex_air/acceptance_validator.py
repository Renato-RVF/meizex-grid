"""AIR-BEW-004 — Deterministic Completion / Acceptance Validator.

Answers exactly one question: DID THIS CANDIDATE CHECKPOINT SATISFY ITS
SPRINT'S ACCEPTANCE CONTRACT? The executor that produced a checkpoint is
never the authority over its own success -

    EXECUTION RESULT != ACCEPTANCE DECISION
    PERSISTED CHECKPOINT != TRUSTED CHECKPOINT
    CHECKPOINT EXISTS != CHECKPOINT ACCEPTED

This module does not execute a sprint, call MHL, call CHASSIS, call a model,
or perform semantic/LLM adjudication of any kind. It only evaluates
STRUCTURED, DETERMINISTIC checks against data the contract already carries.

Verification trust order (this module implements only tiers 1-2):
    1. DETERMINISTIC VERIFIER        <- this module
    2. STRUCTURED EVIDENCE           <- this module
    3. INDEPENDENT REVIEWER          <- future, not implemented
    4. EXECUTOR SELF-ASSESSMENT      <- LOWEST authority, never trusted here
Executor prose such as "task complete" / "all good" / "done" is never read
as acceptance evidence - SprintCheckpoint.findings/confirmed_paths are
opaque strings to this validator, inspected only for presence/count, never
for semantic content.

Discovery findings (full internal report in the AIR-BEW-004 delivery):
- No existing generic acceptance/validation contract exists in AIR.
  `validation_v2.py` (AIR's own "Experimental deterministic Validation
  Contract v2") is DWOsint-narrative-specific (citation coverage, chronology
  fidelity, semantic-support adjudication) - not reusable here, but its
  `ValidationStatus` enum (PASS/PARTIAL/FAIL/NOT_EVALUABLE) is useful design
  precedent for treating "cannot evaluate" as a first-class state distinct
  from PASS/FAIL, same spirit as this module's UNVERIFIABLE. Not imported;
  BES needs its own PASS/FAIL/UNVERIFIABLE vocabulary tied to REQUIRED vs
  optional, which validation_v2 doesn't have.
- MEIZEX_QUALITY_GATE was not imported or coupled to, per instructions - AIR
  has no formal dependency on it and this sprint does not create one.
- TaskLock (AIR-006) has no verifiable counterpart on SprintCheckpoint - no
  field on SprintCheckpoint proves a checkpoint was produced under a
  particular task_lock. TASK_LOCK_PRESERVED is therefore always
  UNVERIFIABLE when sprint.task_lock is set (never invented), and is marked
  OPTIONAL rather than required so that locked missions remain acceptable
  in principle - a future SprintCheckpoint field (task_lock_fingerprint or
  similar) would let this become a real REQUIRED check; recorded as a gap,
  not solved here.
- SprintCheckpoint.findings has no structural per-finding evidence
  association (it is `list[str]`, evidence_refs is a separate unrelated
  `list[str]`) - FINDING_EVIDENCE_VALIDATION is therefore always
  UNVERIFIABLE too, for the same "do not invent structure that doesn't
  exist" reason.
- SprintCheckpoint carries no measured token/tool usage field - BUDGET
  checks are therefore always UNVERIFIABLE; never fabricated.
- Canonical serialization/fingerprinting reuses identity.canonical_json /
  sha256_digest, same as every prior BEW module. SprintCheckpoint's own
  content_fingerprint() (AIR-BEW-001) is reused directly, not recomputed.

Acceptance criteria representation: BoundedExecutionSprint.acceptance_criteria
is free-form `list[str]` today (AIR-BEW-001), with no DSL. This module
recognizes a SMALL, fixed, documented vocabulary of near-exact phrasings
(see _CRITERION_PATTERNS below) and marks anything else UNVERIFIABLE rather
than guessing at meaning - UNKNOWN CRITERION != SATISFIED CRITERION. A
criterion string ending in the literal suffix "(optional)" (case-insensitive)
is treated as an optional check; everything else defaults to required. This
is a two-character convention, not a DSL.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .bounded_execution import BoundedExecutionSprint, SprintCheckpoint, SprintOutcome
from .bounded_execution_store import BoundedExecutionStore
from .bounded_execution_store import NotFoundError as StoreNotFoundError
from .identity import canonical_json, sha256_digest

VALIDATOR_SCHEMA_VERSION = "1.0"
VALIDATOR_VERSION = "1"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    #: The check has no deterministic way to be evaluated given current
    #: contract data - never coerced to PASS or FAIL. See module docstring's
    #: discovery findings for the specific structural gaps that force this.
    UNVERIFIABLE = "UNVERIFIABLE"


class ValidationCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    status: CheckStatus
    required: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AcceptanceValidationResult(BaseModel):
    """Immutable/serializable. Never mutates the candidate_checkpoint or the
    sprint - this is `candidate_checkpoint + validation_result`, never
    `candidate_checkpoint.status = PASS`. Persistence/promotion of this
    result is explicit future work (not implemented here)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = VALIDATOR_SCHEMA_VERSION
    validator_version: str = VALIDATOR_VERSION

    mission_id: str
    sprint_id: str
    checkpoint_id: str
    checkpoint_version: int
    #: Reused directly from SprintCheckpoint.content_fingerprint() (AIR-BEW-001)
    #: - not recomputed, not a new identity system.
    checkpoint_fingerprint: str

    accepted: bool
    decision: str  # "PASS" | "FAIL" - kept as str (not a 2-value enum) to
    # avoid a redundant parallel taxonomy to CheckStatus; validated below.

    checks: list[ValidationCheckResult] = Field(default_factory=list)
    #: check_id values for every REQUIRED check that was FAIL or UNVERIFIABLE
    #: - the actual reasons `accepted` is False.
    failed_checks: list[str] = Field(default_factory=list)
    #: check_id values for every OPTIONAL check that was FAIL or UNVERIFIABLE
    #: - observable, never silently discarded, never blocks acceptance.
    warnings: list[str] = Field(default_factory=list)

    def canonical(self) -> str:
        return canonical_json(self)

    def content_fingerprint(self) -> str:
        return sha256_digest(self)


# ---------------------------------------------------------------------------
# Acceptance-criterion recognition - small, fixed, documented vocabulary
# ---------------------------------------------------------------------------

_OPTIONAL_SUFFIX_RE = re.compile(r"\(\s*optional\s*\)\s*$", re.IGNORECASE)
_MIN_FINDINGS_RE = re.compile(r"^(?:at least|minimum|min)\s+(\d+)\s+findings?$", re.IGNORECASE)
_NO_OPEN_QUESTIONS_RE = re.compile(r"^no\s+(?:unresolved\s+)?open\s+questions?$", re.IGNORECASE)
_REQUIRES_EVIDENCE_RE = re.compile(r"^requires?\s+evidence$", re.IGNORECASE)
_REQUIRES_CONFIRMED_PATHS_RE = re.compile(r"^requires?\s+confirmed\s+paths?$", re.IGNORECASE)


def _split_optional_suffix(criterion: str) -> tuple[str, bool]:
    """Returns (core_text, is_optional)."""
    match = _OPTIONAL_SUFFIX_RE.search(criterion)
    if not match:
        return criterion.strip(), False
    return criterion[: match.start()].strip(), True


def _evaluate_acceptance_criterion(criterion: str, checkpoint: SprintCheckpoint) -> ValidationCheckResult:
    core, is_optional = _split_optional_suffix(criterion)
    required = not is_optional
    check_id = f"ACCEPTANCE::{criterion}"

    match = _MIN_FINDINGS_RE.match(core)
    if match:
        minimum = int(match.group(1))
        actual = len(checkpoint.findings)
        status = CheckStatus.PASS if actual >= minimum else CheckStatus.FAIL
        return ValidationCheckResult(
            check_id=check_id, status=status, required=required,
            message=f"requires >= {minimum} findings, checkpoint has {actual}",
            details={"minimum": minimum, "actual": actual},
        )

    if _NO_OPEN_QUESTIONS_RE.match(core):
        unresolved = list(checkpoint.open_questions) + list(checkpoint.unresolved_items)
        status = CheckStatus.PASS if not unresolved else CheckStatus.FAIL
        return ValidationCheckResult(
            check_id=check_id, status=status, required=required,
            message=(
                "no open_questions/unresolved_items allowed"
                if status == CheckStatus.PASS
                else f"{len(unresolved)} unresolved item(s) present"
            ),
            details={"unresolved_count": len(unresolved)},
        )

    if _REQUIRES_EVIDENCE_RE.match(core):
        status = CheckStatus.PASS if checkpoint.evidence_refs else CheckStatus.FAIL
        return ValidationCheckResult(
            check_id=check_id, status=status, required=required,
            message=(
                f"{len(checkpoint.evidence_refs)} evidence_refs present"
                if status == CheckStatus.PASS
                else "no evidence_refs present"
            ),
            details={"evidence_ref_count": len(checkpoint.evidence_refs)},
        )

    if _REQUIRES_CONFIRMED_PATHS_RE.match(core):
        status = CheckStatus.PASS if checkpoint.confirmed_paths else CheckStatus.FAIL
        return ValidationCheckResult(
            check_id=check_id, status=status, required=required,
            message=(
                f"{len(checkpoint.confirmed_paths)} confirmed_paths present"
                if status == CheckStatus.PASS
                else "no confirmed_paths present"
            ),
            details={"confirmed_path_count": len(checkpoint.confirmed_paths)},
        )

    # UNKNOWN CRITERION != SATISFIED CRITERION - never silently PASS.
    return ValidationCheckResult(
        check_id=check_id, status=CheckStatus.UNVERIFIABLE, required=required,
        message=(
            f"criterion text {core!r} matches no recognized deterministic pattern "
            "- this validator does not guess free-text meaning"
        ),
    )


# Recognized output_contract keys - the only ones this validator can check
# deterministically against SprintCheckpoint's fixed field set. Any other
# key is UNVERIFIABLE for the same "do not guess" reason as acceptance
# criteria.
_OUTPUT_CONTRACT_FIELD_MAP = {
    "findings": "findings",
    "confirmed_paths": "confirmed_paths",
    "evidence_refs": "evidence_refs",
}


def _evaluate_output_contract_entry(
    key: str, description: str, checkpoint: SprintCheckpoint
) -> ValidationCheckResult:
    check_id = f"OUTPUT_CONTRACT::{key}"
    field_name = _OUTPUT_CONTRACT_FIELD_MAP.get(key.strip().lower())
    if field_name is None:
        return ValidationCheckResult(
            check_id=check_id, status=CheckStatus.UNVERIFIABLE, required=True,
            message=(
                f"output_contract key {key!r} is not one of "
                f"{sorted(_OUTPUT_CONTRACT_FIELD_MAP)} - no deterministic check exists for it"
            ),
        )
    value = getattr(checkpoint, field_name)
    status = CheckStatus.PASS if value else CheckStatus.FAIL
    return ValidationCheckResult(
        check_id=check_id, status=status, required=True,
        message=f"output_contract requires non-empty {field_name}, checkpoint has {len(value)}",
        details={"count": len(value), "description": description},
    )


# ---------------------------------------------------------------------------
# Built-in structural checks (not derived from acceptance_criteria text)
# ---------------------------------------------------------------------------


def _check_identity_mission(sprint: BoundedExecutionSprint, checkpoint: SprintCheckpoint) -> ValidationCheckResult:
    ok = checkpoint.mission_id == sprint.mission_id
    return ValidationCheckResult(
        check_id="IDENTITY_MISSION_MATCH", status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        required=True,
        message="mission_id matches" if ok else (
            f"checkpoint.mission_id={checkpoint.mission_id!r} != sprint.mission_id={sprint.mission_id!r}"
        ),
    )


def _check_identity_sprint(sprint: BoundedExecutionSprint, checkpoint: SprintCheckpoint) -> ValidationCheckResult:
    ok = checkpoint.sprint_id == sprint.sprint_id
    return ValidationCheckResult(
        check_id="IDENTITY_SPRINT_MATCH", status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        required=True,
        message="sprint_id matches" if ok else (
            f"checkpoint.sprint_id={checkpoint.sprint_id!r} != sprint.sprint_id={sprint.sprint_id!r}"
        ),
    )


def _check_checkpoint_status_valid(checkpoint: SprintCheckpoint) -> ValidationCheckResult:
    """CheckpointStatus (record lifecycle) - deliberately NOT the same
    question as EXECUTION_OUTCOME_PASS (execution outcome, failure_class)."""
    from .bounded_execution import CheckpointStatus

    ok = checkpoint.status != CheckpointStatus.REJECTED
    return ValidationCheckResult(
        check_id="CHECKPOINT_STATUS_VALID", status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        required=True,
        message=f"checkpoint.status={checkpoint.status.value}" + ("" if ok else " (REJECTED candidates are never accepted)"),
        details={"status": checkpoint.status.value},
    )


def _check_execution_outcome(checkpoint: SprintCheckpoint) -> ValidationCheckResult:
    """SprintOutcome (execution outcome, failure_class) - deliberately NOT
    the same question as CHECKPOINT_STATUS_VALID (record lifecycle)."""
    ok = checkpoint.failure_class == SprintOutcome.PASS
    return ValidationCheckResult(
        check_id="EXECUTION_OUTCOME_PASS", status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        required=True,
        message=(
            "failure_class=PASS"
            if ok
            else f"failure_class={checkpoint.failure_class.value if checkpoint.failure_class else None} (not PASS)"
        ),
        details={"failure_class": checkpoint.failure_class.value if checkpoint.failure_class else None},
    )


def _check_task_lock(sprint: BoundedExecutionSprint) -> Optional[ValidationCheckResult]:
    if sprint.task_lock is None:
        return None  # nothing declared, nothing to check - not generated at all
    return ValidationCheckResult(
        check_id="TASK_LOCK_PRESERVED", status=CheckStatus.UNVERIFIABLE, required=False,
        message=(
            "sprint declares a task_lock, but SprintCheckpoint has no field that proves a "
            "checkpoint was produced under it - marked UNVERIFIABLE and OPTIONAL rather than "
            "invented or silently skipped (see module docstring: this is a known contract gap)"
        ),
        details={"task_fingerprint": sprint.task_lock.task_fingerprint},
    )


def _check_budget_contract(sprint: BoundedExecutionSprint) -> ValidationCheckResult:
    return ValidationCheckResult(
        check_id="BUDGET_CONTRACT", status=CheckStatus.UNVERIFIABLE, required=False,
        message=(
            "SprintCheckpoint carries no measured token/tool usage field - budget compliance "
            "cannot be verified without fabricating data, so this is always UNVERIFIABLE today"
        ),
        details={"token_budget": sprint.token_budget, "tool_budget": sprint.tool_budget},
    )


def _check_finding_evidence_association(checkpoint: SprintCheckpoint) -> ValidationCheckResult:
    return ValidationCheckResult(
        check_id="FINDING_EVIDENCE_ASSOCIATION", status=CheckStatus.UNVERIFIABLE, required=False,
        message=(
            "SprintCheckpoint.findings (list[str]) has no structural association to "
            "evidence_refs (list[str]) - per-finding evidence linkage is not representable in "
            "the current contract, so this is always UNVERIFIABLE, never guessed"
        ),
        details={"finding_count": len(checkpoint.findings), "evidence_ref_count": len(checkpoint.evidence_refs)},
    )


def _check_dependency_state(
    sprint: BoundedExecutionSprint,
    dependency_checkpoints: Optional[list[SprintCheckpoint]],
) -> Optional[ValidationCheckResult]:
    if not sprint.dependencies:
        return None  # nothing declared, nothing to check

    if dependency_checkpoints is None:
        return ValidationCheckResult(
            check_id="DEPENDENCY_STATE", status=CheckStatus.UNVERIFIABLE, required=True,
            message=(
                f"sprint declares dependencies {sorted(sprint.dependencies)} but no dependency "
                "checkpoint state was supplied to the validator"
            ),
            details={"dependencies": sorted(sprint.dependencies)},
        )

    by_sprint_id = {cp.sprint_id: cp for cp in dependency_checkpoints if cp.sprint_id in sprint.dependencies}
    missing = sorted(set(sprint.dependencies) - set(by_sprint_id))
    not_passed = sorted(
        sid for sid, cp in by_sprint_id.items() if cp.failure_class != SprintOutcome.PASS
    )
    if missing or not_passed:
        return ValidationCheckResult(
            check_id="DEPENDENCY_STATE", status=CheckStatus.FAIL, required=True,
            message=f"missing dependency checkpoints: {missing}; not-PASS dependency checkpoints: {not_passed}",
            details={"missing": missing, "not_passed": not_passed},
        )
    return ValidationCheckResult(
        check_id="DEPENDENCY_STATE", status=CheckStatus.PASS, required=True,
        message=f"all {len(sprint.dependencies)} declared dependencies resolved with failure_class=PASS",
        details={"dependencies": sorted(sprint.dependencies)},
    )


# ---------------------------------------------------------------------------
# Top-level pure validator
# ---------------------------------------------------------------------------


def validate_checkpoint(
    sprint: BoundedExecutionSprint,
    checkpoint: SprintCheckpoint,
    *,
    dependency_checkpoints: Optional[list[SprintCheckpoint]] = None,
) -> AcceptanceValidationResult:
    """Pure, deterministic. No I/O, no store, no model call. Never mutates
    `sprint` or `checkpoint`. Same inputs always produce the same
    AcceptanceValidationResult (content_fingerprint() is stable)."""
    checks: list[ValidationCheckResult] = [
        _check_identity_mission(sprint, checkpoint),
        _check_identity_sprint(sprint, checkpoint),
        _check_checkpoint_status_valid(checkpoint),
        _check_execution_outcome(checkpoint),
    ]

    task_lock_check = _check_task_lock(sprint)
    if task_lock_check is not None:
        checks.append(task_lock_check)

    dependency_check = _check_dependency_state(sprint, dependency_checkpoints)
    if dependency_check is not None:
        checks.append(dependency_check)

    checks.append(_check_budget_contract(sprint))
    checks.append(_check_finding_evidence_association(checkpoint))

    # Acceptance criteria (sorted for determinism - two sprints with the
    # same criteria set in different list order must produce the same result).
    for criterion in sorted(sprint.acceptance_criteria):
        checks.append(_evaluate_acceptance_criterion(criterion, checkpoint))

    # Output contract (sorted by key for determinism).
    for key in sorted(sprint.output_contract):
        checks.append(_evaluate_output_contract_entry(key, sprint.output_contract[key], checkpoint))

    failed_checks: list[str] = []
    warnings: list[str] = []
    for check in checks:
        blocked = check.status in (CheckStatus.FAIL, CheckStatus.UNVERIFIABLE)
        if not blocked:
            continue
        if check.required:
            failed_checks.append(check.check_id)
        else:
            warnings.append(check.check_id)

    accepted = not failed_checks
    decision = "PASS" if accepted else "FAIL"

    return AcceptanceValidationResult(
        mission_id=sprint.mission_id,
        sprint_id=sprint.sprint_id,
        checkpoint_id=checkpoint.checkpoint_id,
        checkpoint_version=checkpoint.checkpoint_version,
        checkpoint_fingerprint=checkpoint.content_fingerprint(),
        accepted=accepted,
        decision=decision,
        checks=checks,
        failed_checks=failed_checks,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Store-integrated convenience wrapper
# ---------------------------------------------------------------------------


def validate_checkpoint_from_store(
    store: BoundedExecutionStore,
    mission_id: str,
    sprint_id: str,
    checkpoint_version: int,
) -> AcceptanceValidationResult:
    """Resolves sprint + candidate checkpoint + declared-dependency
    checkpoints (best-effort: a dependency with no persisted checkpoint is
    simply absent from what gets passed to validate_checkpoint, which then
    reports it via DEPENDENCY_STATE) from a BoundedExecutionStore. Store
    errors (NotFoundError, IntegrityError, InvalidRecordError, ...) are not
    re-wrapped - they propagate directly from BEW-002, same reuse-over-
    reinvention approach as compile_context_from_store (AIR-BEW-003)."""
    sprint = store.load_sprint(mission_id, sprint_id)
    checkpoint = store.load_checkpoint(mission_id, sprint_id, checkpoint_version)

    dependency_checkpoints: Optional[list[SprintCheckpoint]] = None
    if sprint.dependencies:
        dependency_checkpoints = []
        for dependency_sprint_id in sprint.dependencies:
            try:
                dependency_checkpoints.append(
                    store.load_latest_checkpoint(mission_id, dependency_sprint_id)
                )
            except StoreNotFoundError:
                # Absence is reported by _check_dependency_state via
                # "missing" - not an exception here, the same fail-closed
                # signal just travels through the acceptance result instead.
                # Any OTHER store error (IntegrityError, InvalidRecordError,
                # StoreIOError, ...) is a real failure and must propagate,
                # never be silently treated as "missing".
                continue

    return validate_checkpoint(sprint, checkpoint, dependency_checkpoints=dependency_checkpoints)
