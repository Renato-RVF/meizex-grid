"""AIR-BEW-003 — Deterministic Context Compiler (compilation only, no execution).

Compiles persisted TaskPlan / BoundedExecutionSprint / SprintCheckpoint state
into a bounded, deterministic CompiledSprintContext:

    MISSION STATE (TaskPlan)
        v
    CURRENT SPRINT (BoundedExecutionSprint)
        v
    DEPENDENCY CHECKPOINTS (SprintCheckpoint[], only sprint.dependencies)
        v
    CONTEXT COMPILER  (this module)
        v
    BOUNDED ACTIVE CONTEXT (CompiledSprintContext)

Never TOTAL MISSION HISTORY -> MODEL. This module does not call an LLM,
execute a sprint, invoke a tool, route a provider, or render a Formula
prompt. CONTEXT SELECTION != PROMPT FORMATTING - a future Formula-rendering
step is downstream of this, not implemented here.

Discovery findings (full internal report in the AIR-BEW-003 delivery):
- No existing context/prompt contract or token estimator exists anywhere in
  AIR - `estimate_tokens()` below is a new, deliberately simple, deliberately
  labeled ESTIMATE (chars/4, the same widely-used order-of-magnitude
  heuristic used elsewhere in this ecosystem, reimplemented fresh here - not
  imported from CHASSIS, which has its own private copy for its own reasons).
- TaskLock (AIR-006) is carried through unchanged, never regenerated -
  reused exactly as AIR-BEW-001/002 already established.
- `sprint_belongs_to_plan()` (AIR-BEW-001) is reused to validate plan/sprint
  consistency before compiling anything.
- Canonical serialization/fingerprinting reuses identity.canonical_json /
  sha256_digest, same as every prior BEW module.
- BoundedExecutionStore.load_latest_checkpoint() (AIR-BEW-002, itself
  explicit-version-only, never mtime-based) is the sole resolution path for
  "which checkpoint version represents a dependency's current state."

Checkpoint admissibility (the core trust boundary of this module):
    checkpoint.failure_class == SprintOutcome.PASS
        -> confirmed_paths + findings become trusted known_facts,
           evidence_refs become relevant_evidence_refs
    any other failure_class (INCOMPLETE/UNSUPPORTED/TASK_DRIFT/
    BUDGET_EXCEEDED/TOOL_FAILURE/CONTRADICTION/CAPABILITY_FAILURE/
    INVALID_OUTPUT)
        -> findings/confirmed_paths are NEVER promoted to known_facts;
           only a synthesized, explicit open_question records that this
           dependency did not PASS - failed work never becomes a trusted
           fact by omission.
CheckpointStatus (CANDIDATE/ACCEPTED/SUPERSEDED/REJECTED) acceptance
gating is explicitly NOT implemented here - that is AIR-BEW-004's job
(Checkpoint / Acceptance Validator), not this one.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bounded_execution import (
    BoundedExecutionSprint,
    SprintCheckpoint,
    SprintOutcome,
    TaskPlan,
    sprint_belongs_to_plan,
)
from .bounded_execution_store import BoundedExecutionStore
from .bounded_execution_store import NotFoundError as StoreNotFoundError
from .identity import canonical_json, sha256_digest
from .models import TaskLock

CONTEXT_COMPILER_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Token estimation - deliberately simple, deliberately not exact
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Conservative, deterministic, model-agnostic ESTIMATE - never an exact
    tokenizer count for any specific model. ~4 characters/token is a
    widely-used rough order-of-magnitude heuristic for English-ish text;
    using it (rather than a real tokenizer library) keeps the Context
    Compiler free of any model/runtime dependency, matching RUNTIME
    INDEPENDENCE for this sprint. Always label output as ESTIMATED_CONTEXT_
    TOKENS, never as an exact model token count."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _estimate_block_tokens(value) -> int:
    """Estimate tokens for one canonical-JSON-serializable content block."""
    return estimate_tokens(canonical_json(value))


# ---------------------------------------------------------------------------
# Typed failure taxonomy
# ---------------------------------------------------------------------------


class ContextCompilerError(Exception):
    """Base for all typed AIR-BEW-003 errors."""


class PlanNotFoundError(ContextCompilerError):
    pass


class SprintNotFoundError(ContextCompilerError):
    pass


class DependencyNotFoundError(ContextCompilerError):
    """A sprint_id in BoundedExecutionSprint.dependencies is not a member of
    the owning TaskPlan.sprint_ids - it is not a recognized sprint in this
    mission at all, distinct from being a recognized sprint with no
    checkpoint yet (see DependencyCheckpointMissingError)."""


class DependencyCheckpointMissingError(ContextCompilerError):
    """A dependency sprint is a recognized part of the mission but has no
    persisted checkpoint to resolve. Fail closed - dependency state is never
    pretended to be available."""


class InvalidDependencyStateError(ContextCompilerError):
    """A supplied dependency checkpoint is structurally inconsistent with
    the sprint being compiled (e.g. mission_id mismatch)."""


class ContextBudgetUnsatisfiableError(ContextCompilerError):
    """The MANDATORY content (P0: objective/task_lock/constraints/
    acceptance_criteria/output_contract, plus P1: the current sprint's own
    known_facts/open_questions/evidence_requirements) alone exceeds
    token_budget. Dependency-inherited material (P2/P3) is never the cause
    of this error - only the current sprint's own irreducible content is."""


class InvalidContextInputError(ContextCompilerError):
    """The supplied plan/sprint pair is structurally inconsistent (e.g.
    sprint does not belong to plan)."""


# `STORE_ERROR` (per the AIR-BEW-003 delivery template) is not a new wrapper
# class here: any bounded_execution_store.BoundedExecutionStoreError not
# specifically translated above (StoreIOError, IntegrityError,
# InvalidRecordError from corrupted persisted data, etc.) propagates
# unchanged from compile_context_from_store - reusing BEW-002's own typed
# errors rather than duplicating them, per instructions.


# ---------------------------------------------------------------------------
# CompiledSprintContext contract
# ---------------------------------------------------------------------------


class CheckpointRef(BaseModel):
    """sprint_id + checkpoint_version - the minimal, unambiguous pointer to
    one persisted SprintCheckpoint (see BoundedExecutionStore.load_checkpoint)."""

    model_config = ConfigDict(extra="forbid")

    sprint_id: str
    checkpoint_version: int = Field(ge=1)


class ContextMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compiler_version: str = CONTEXT_COMPILER_VERSION
    #: Dependency checkpoints that were admissible inputs to compilation
    #: (i.e. len(sprint.dependencies), all successfully resolved - if any
    #: were unresolvable, compilation would already have failed closed).
    source_checkpoint_count: int = Field(ge=0)
    #: How many distinct dependency checkpoints had AT LEAST ONE of their
    #: items (a fact, question, or evidence ref) omitted for budget. Never
    #: counts checkpoints excluded for being unrelated to this sprint -
    #: those are never "sourced" in the first place. A checkpoint can be
    #: partially represented (some items in, some out) and still count once
    #: here.
    omitted_checkpoint_count: int = Field(ge=0)
    #: AIR-BEW-003A: P1/P2/P3 are reducible at individual-item granularity,
    #: not whole-block. These three counters are the per-category omission
    #: detail; omitted_checkpoint_count (above) stays as a coarser
    #: per-dependency summary.
    omitted_known_fact_count: int = Field(default=0, ge=0)
    omitted_open_question_count: int = Field(default=0, ge=0)
    omitted_evidence_ref_count: int = Field(default=0, ge=0)
    estimated_context_tokens: int = Field(ge=0)
    truncation_applied: bool = False


class CompiledSprintContext(BaseModel):
    """A bounded, deterministic working set for exactly one sprint. Never a
    prose prompt - CONTEXT SELECTION != PROMPT FORMATTING, a future Formula
    renders THIS into a model-specific prompt, not implemented here."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = CONTEXT_COMPILER_VERSION
    mission_id: str
    plan_version: str
    sprint_id: str

    objective: str
    task_lock: Optional[TaskLock] = None
    constraints: list[str] = Field(default_factory=list)

    known_facts: list[str] = Field(default_factory=list)
    relevant_evidence_refs: list[str] = Field(default_factory=list)
    dependency_checkpoint_refs: list[CheckpointRef] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    acceptance_criteria: list[str] = Field(min_length=1)
    output_contract: dict[str, str] = Field(default_factory=dict)

    #: Reference only - never a hardcoded model or runtime, carried through
    #: unchanged from the sprint. Context Compiler is not Runtime Selector.
    execution_profile_ref: Optional[str] = None

    token_budget: int = Field(ge=1)
    tool_budget: int = Field(ge=0)
    max_iterations: int = Field(ge=1)

    context_metadata: ContextMetadata

    def canonical(self) -> str:
        return canonical_json(self)

    def content_fingerprint(self) -> str:
        """Reproducibility/integrity fingerprint - same persisted inputs +
        same compiler_version + same budget must produce the same
        fingerprint. NOT a new identity system (see identity.py); reuses
        sha256_digest directly over the whole canonical record."""
        return sha256_digest(self)


# ---------------------------------------------------------------------------
# Pure compilation
# ---------------------------------------------------------------------------


def compile_context(
    plan: TaskPlan,
    sprint: BoundedExecutionSprint,
    dependency_checkpoints: Optional[list[SprintCheckpoint]] = None,
) -> CompiledSprintContext:
    """Pure, deterministic. No I/O, no store, no filesystem access. Same
    inputs always produce a CompiledSprintContext with the same
    content_fingerprint()."""
    if not sprint_belongs_to_plan(plan, sprint):
        raise InvalidContextInputError(
            f"sprint {sprint.sprint_id!r} does not belong to plan "
            f"{plan.mission_id!r}@{plan.plan_version!r}"
        )

    dependency_checkpoints = dependency_checkpoints or []

    # Unrelated mission state is excluded here, unconditionally - this is
    # the property test_unrelated_checkpoint_excluded relies on. Never
    # trust the caller to have already filtered correctly.
    declared = set(sprint.dependencies)
    by_sprint_id: dict[str, SprintCheckpoint] = {}
    for checkpoint in dependency_checkpoints:
        if checkpoint.sprint_id not in declared:
            continue  # not a declared dependency of THIS sprint - excluded
        if checkpoint.mission_id != sprint.mission_id:
            raise InvalidDependencyStateError(
                f"dependency checkpoint for sprint {checkpoint.sprint_id!r} has "
                f"mission_id {checkpoint.mission_id!r}, expected {sprint.mission_id!r}"
            )
        # Deterministic "latest wins" if the caller passed more than one
        # checkpoint for the same dependency sprint_id - mirrors
        # BoundedExecutionStore.load_latest_checkpoint's own policy.
        existing = by_sprint_id.get(checkpoint.sprint_id)
        if existing is None or checkpoint.checkpoint_version > existing.checkpoint_version:
            by_sprint_id[checkpoint.sprint_id] = checkpoint

    missing = sorted(declared - set(by_sprint_id))
    if missing:
        raise DependencyCheckpointMissingError(
            f"sprint {sprint.sprint_id!r} declares dependencies with no resolvable "
            f"checkpoint: {missing}"
        )

    # ---- P0: IRREDUCIBLE CORE - the only mandatory content --------------
    # AIR-BEW-003A correction: P1/P2/P3 are PRIORITIZED REDUCIBLE CONTEXT,
    # not mandatory. CURRENT_SPRINT_STATE != MANDATORY_ACTIVE_CONTEXT - only
    # the execution core (objective/task_lock/constraints/acceptance_criteria/
    # output_contract) is ever guaranteed. Everything below this point is
    # included on a deterministic, best-effort, whole-item basis.
    p0 = {
        "objective": sprint.objective,
        "task_lock": sprint.task_lock.model_dump(mode="json") if sprint.task_lock else None,
        "constraints": sorted(sprint.constraints),
        "acceptance_criteria": sorted(sprint.acceptance_criteria),
        "output_contract": sprint.output_contract,
    }
    p0_tokens = _estimate_block_tokens(p0)
    if p0_tokens > sprint.token_budget:
        raise ContextBudgetUnsatisfiableError(
            f"sprint {sprint.sprint_id!r}: P0 (irreducible core) alone is ~{p0_tokens} "
            f"estimated tokens, exceeding token_budget={sprint.token_budget}"
        )

    running_total = p0_tokens

    # ---- Prioritized reducible items: P1 (current sprint) > P2 (direct
    # PASS dependencies) > P3 (non-PASS dependency failure metadata). Each
    # tier is fully processed (in deterministic sorted order, skip-and-
    # continue on individual items that don't fit) before the next tier is
    # even considered, so a P2/P3 item is never selected ahead of an
    # available P1 item, and a P3 item never ahead of an available P1/P2
    # item - "available" meaning it would have fit.
    seen_facts: set[str] = set()
    seen_questions: set[str] = set()
    seen_evidence: set[str] = set()
    included_facts: list[str] = []
    included_questions: list[str] = []
    included_evidence: list[str] = []
    omitted_fact_count = 0
    omitted_question_count = 0
    omitted_evidence_count = 0
    checkpoints_with_omission: set[str] = set()

    def _try_add_fact(value: str, dep_sprint_id: Optional[str] = None) -> None:
        nonlocal running_total, omitted_fact_count
        if not value or value in seen_facts:
            return
        tokens = estimate_tokens(value)
        if running_total + tokens <= sprint.token_budget:
            included_facts.append(value)
            seen_facts.add(value)
            running_total += tokens
        else:
            omitted_fact_count += 1
            if dep_sprint_id:
                checkpoints_with_omission.add(dep_sprint_id)

    def _try_add_question(value: str, dep_sprint_id: Optional[str] = None) -> None:
        nonlocal running_total, omitted_question_count
        if not value or value in seen_questions:
            return
        tokens = estimate_tokens(value)
        if running_total + tokens <= sprint.token_budget:
            included_questions.append(value)
            seen_questions.add(value)
            running_total += tokens
        else:
            omitted_question_count += 1
            if dep_sprint_id:
                checkpoints_with_omission.add(dep_sprint_id)

    def _try_add_evidence(value: str, dep_sprint_id: Optional[str] = None) -> None:
        nonlocal running_total, omitted_evidence_count
        if not value or value in seen_evidence:
            return
        tokens = estimate_tokens(value)
        if running_total + tokens <= sprint.token_budget:
            included_evidence.append(value)
            seen_evidence.add(value)
            running_total += tokens
        else:
            omitted_evidence_count += 1
            if dep_sprint_id:
                checkpoints_with_omission.add(dep_sprint_id)

    # ---- P1: current sprint's own state (reducible, no longer mandatory) --
    for fact in sorted(sprint.known_facts):
        _try_add_fact(fact)
    for question in sorted(sprint.open_questions):
        _try_add_question(question)
    for evidence in sorted(sprint.evidence_requirements):
        _try_add_evidence(evidence)

    # ---- P2: direct dependency checkpoints, PASS-admissible content only --
    for sprint_id in sorted(by_sprint_id):
        checkpoint = by_sprint_id[sprint_id]
        if checkpoint.failure_class != SprintOutcome.PASS:
            continue
        for fact in sorted(set(checkpoint.confirmed_paths) | set(checkpoint.findings)):
            _try_add_fact(fact, dep_sprint_id=sprint_id)
        for evidence in sorted(checkpoint.evidence_refs):
            _try_add_evidence(evidence, dep_sprint_id=sprint_id)

    # ---- P3: non-PASS dependency checkpoints, failure metadata only -------
    # Not redesigned this sprint (AIR-BEW-005 owns failure ontology) - same
    # synthesized "did not PASS" question plus unresolved_items/open_questions
    # passthrough as AIR-BEW-003, just now individually reducible instead of
    # unconditionally included.
    for sprint_id in sorted(by_sprint_id):
        checkpoint = by_sprint_id[sprint_id]
        if checkpoint.failure_class == SprintOutcome.PASS:
            continue
        question = (
            f"dependency {sprint_id} checkpoint v{checkpoint.checkpoint_version} did not "
            f"PASS (failure_class={checkpoint.failure_class.value if checkpoint.failure_class else None}) "
            "- its findings are not trusted"
        )
        _try_add_question(question, dep_sprint_id=sprint_id)
        for item in sorted(checkpoint.open_questions):
            _try_add_question(item, dep_sprint_id=sprint_id)
        for item in sorted(checkpoint.unresolved_items):
            _try_add_question(item, dep_sprint_id=sprint_id)

    # dependency_checkpoint_refs: EVERY resolved dependency checkpoint is
    # traceable here regardless of whether its content ultimately fit -
    # the ref itself is cheap; only its content is budget-reducible.
    included_refs = sorted(
        (
            CheckpointRef(sprint_id=sid, checkpoint_version=by_sprint_id[sid].checkpoint_version)
            for sid in by_sprint_id
        ),
        key=lambda ref: (ref.sprint_id, ref.checkpoint_version),
    )

    total_omitted = omitted_fact_count + omitted_question_count + omitted_evidence_count
    truncation_applied = total_omitted > 0

    context = CompiledSprintContext(
        mission_id=sprint.mission_id,
        plan_version=sprint.plan_version,
        sprint_id=sprint.sprint_id,
        objective=sprint.objective,
        task_lock=sprint.task_lock,
        constraints=sorted(sprint.constraints),
        known_facts=sorted(included_facts),
        relevant_evidence_refs=sorted(included_evidence),
        dependency_checkpoint_refs=included_refs,
        open_questions=sorted(included_questions),
        acceptance_criteria=sorted(sprint.acceptance_criteria),
        output_contract=sprint.output_contract,
        execution_profile_ref=sprint.execution_profile_ref,
        token_budget=sprint.token_budget,
        tool_budget=sprint.tool_budget,
        max_iterations=sprint.max_iterations,
        context_metadata=ContextMetadata(
            compiler_version=CONTEXT_COMPILER_VERSION,
            source_checkpoint_count=len(by_sprint_id),
            omitted_checkpoint_count=len(checkpoints_with_omission),
            omitted_known_fact_count=omitted_fact_count,
            omitted_open_question_count=omitted_question_count,
            omitted_evidence_ref_count=omitted_evidence_count,
            estimated_context_tokens=running_total,
            truncation_applied=truncation_applied,
        ),
    )
    return context


# ---------------------------------------------------------------------------
# Store-integrated compilation
# ---------------------------------------------------------------------------


def compile_context_from_store(
    store: BoundedExecutionStore,
    mission_id: str,
    sprint_id: str,
) -> CompiledSprintContext:
    """Convenience wrapper: resolves TaskPlan/Sprint/dependency Checkpoints
    from a BoundedExecutionStore, then delegates to the pure compile_context.
    Dependency checkpoints are resolved via load_latest_checkpoint - explicit
    version semantics only (BEW-002), never file modification time."""
    try:
        sprint = store.load_sprint(mission_id, sprint_id)
    except StoreNotFoundError as exc:
        raise SprintNotFoundError(f"no sprint {sprint_id!r} in mission {mission_id!r}") from exc

    try:
        plan = store.load_task_plan(mission_id, sprint.plan_version)
    except StoreNotFoundError as exc:
        raise PlanNotFoundError(
            f"no plan_version {sprint.plan_version!r} in mission {mission_id!r}"
        ) from exc

    dependency_checkpoints: list[SprintCheckpoint] = []
    for dependency_sprint_id in sprint.dependencies:
        if dependency_sprint_id not in plan.sprint_ids:
            raise DependencyNotFoundError(
                f"sprint {sprint_id!r} depends on {dependency_sprint_id!r}, which is not a "
                f"member of plan {plan.mission_id!r}@{plan.plan_version!r}.sprint_ids"
            )
        try:
            dependency_checkpoints.append(
                store.load_latest_checkpoint(mission_id, dependency_sprint_id)
            )
        except StoreNotFoundError as exc:
            raise DependencyCheckpointMissingError(
                f"sprint {sprint_id!r} depends on {dependency_sprint_id!r}, which has no "
                "persisted checkpoint yet"
            ) from exc

    return compile_context(plan, sprint, dependency_checkpoints)
