"""AIR-BEW-001 — Bounded Execution Sprint contract foundation (contracts only).

This module defines the CONTRACT layer for Bounded Execution Sprints (BES),
the architectural response to MONOLITHIC_PROMPT_SERIES_001 (see NEXT.md):
RERUN-6/7/8/9 established that a single monolithic mission prompt handed to
one model turn does not reliably converge, and that the failure is sensitive
to instruction *content*, not prompt length. The response is to partition a
mission into small, bounded, independently-checkpointable units of work
BEFORE any prompt grows large enough to matter, rather than trying to tune a
single large prompt further.

It creates NO execution, NO scheduler, NO persistence backend, NO Context
Compiler, NO checkpoint validator. It only defines:

- TaskPlan       — mission-level structure: objective, constraints, the set
                    of sprint_ids that compose it, and the dependency shape
                    between them. Represents MISSION-LEVEL STRUCTURE, never
                    conversation history or model/run state.
- BoundedExecutionSprint — one small, bounded, validatable, persistable,
                    restartable, routable work unit. A sprint is a work
                    DEFINITION, not a Run (an execution attempt) and not
                    ACTIVE_MODEL_CONTEXT (what a model actually saw).
- SprintCheckpoint — a validated/persistable RESULT CANDIDATE for one sprint.
                    Never equivalent to memory/chat history; never embeds
                    full evidence content, only evidence_refs (see
                    TOTAL_EVIDENCE_STATE != ACTIVE_SPRINT_CONTEXT below).
- SprintOutcome / CheckpointStatus — the status/failure taxonomies.

Strict separations preserved throughout this module (do not collapse them):

    MISSION_STATE != SPRINT_WORKING_STATE != ACTIVE_MODEL_CONTEXT
    != CHECKPOINT != RUN != EXECUTION_PROFILE

    SPRINT WORK BUDGET != PROVIDER COST BUDGET != NETWORK POLICY BUDGET
    (token_budget/tool_budget/max_iterations here are work-unit limits only;
    CHASSIS's RunLimits/provider cost budgets are a separate, already-built
    concept in a different project - see MEIZEX_CHASSIS_API/schemas.py)

    TOTAL_EVIDENCE_STATE != ACTIVE_SPRINT_CONTEXT
    (Checkpoint.evidence_refs are references, never embedded copies)

Reuse over reinvention (see discovery notes in NEXT.md / delivery report):
- Canonical serialization reuses `identity.canonical_json` / `sha256_digest`
  (same JSON convention CHASSIS's ExecutionPolicyStore and AIR's own
  identity.py already use: sort_keys, compact separators, UTF-8).
- `TaskLock` (from .models, already built for AIR-006) is reused verbatim as
  the optional `task_lock` field on both TaskPlan and BoundedExecutionSprint,
  rather than inventing a second task-fingerprint mechanism - this is also
  what makes TASK_DRIFT a checkable condition later (compare a sprint's
  current objective against its carried task_lock.task_fingerprint).
- IDs (mission_id/sprint_id/checkpoint_id) are plain, externally-supplied
  strings, matching the existing convention for plan-level contracts
  (WorkloadPartitionPlan.plan_id, WorkloadShard.shard_id,
  TaskSpecification.task_id) - none of those use identity.py's
  content-derived IdentityRef machinery either. AIR-IDENTITY-003/004 remain
  unstarted; see IDENTITY_DEPENDENCY = PARTIAL in the delivery report. A
  checkpoint's *content* fingerprint (not its id) is still derived
  deterministically via `SprintCheckpoint.content_fingerprint()`, reusing
  identity.sha256_digest, so "no silent overwrite" is checkable now without
  waiting on canonical identity IDs.
- `execution_profile_ref` is a plain `Optional[str]`, matching
  `ExecutionProfile.profile_id` / `WorkloadShard.candidate_profile_ids` - not
  a new reference type. A sprint never embeds a model or runtime directly.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .identity import canonical_json, sha256_digest
from .models import TaskLock

BES_SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Status / failure taxonomy
# ---------------------------------------------------------------------------


class MissionStatus(str, Enum):
    """TaskPlan lifecycle. Deliberately small - mirrors the existing
    TaskStatus shape (models.py) rather than inventing a parallel one, but
    kept separate because TaskStatus belongs to a single ActiveTask, not a
    multi-sprint mission."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class SprintOutcome(str, Enum):
    """The requested SPRINT / FAILURE STATUS TAXONOMY, plus PENDING - the
    one pre-execution sentinel a sprint contract needs before it has ever
    run (this sprint is a contract-only artifact in AIR-BEW-001; nothing
    here executes a sprint, so every sprint constructed today legitimately
    starts at PENDING).

    Used as BoundedExecutionSprint.status (the sprint's own terminal
    outcome) AND as SprintCheckpoint.failure_class's value type (which
    checkpoint-outcome classification this checkpoint records) - one
    taxonomy, two attachment points, not two taxonomies.
    """

    PENDING = "PENDING"
    PASS = "PASS"
    INCOMPLETE = "INCOMPLETE"
    UNSUPPORTED = "UNSUPPORTED"
    TASK_DRIFT = "TASK_DRIFT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TOOL_FAILURE = "TOOL_FAILURE"
    CONTRADICTION = "CONTRADICTION"
    CAPABILITY_FAILURE = "CAPABILITY_FAILURE"
    INVALID_OUTPUT = "INVALID_OUTPUT"


#: Every outcome except the pre-execution sentinel - a sprint/checkpoint in
#: one of these has actually concluded (successfully or not).
TERMINAL_SPRINT_OUTCOMES = frozenset(SprintOutcome) - {SprintOutcome.PENDING}


class CheckpointStatus(str, Enum):
    """A checkpoint RECORD's own acceptance lifecycle - distinct from
    `failure_class`, which classifies the underlying sprint's outcome. A
    checkpoint can be a CANDIDATE (freshly produced, not yet validated by
    the future Checkpoint Validator - see AIR-BEW-004) independently of
    whether the sprint it describes PASSed or hit CONTRADICTION/etc.

    Supports "no silent overwrite": a new checkpoint_version is a new
    record with CANDIDATE status: an older ACCEPTED version becomes
    SUPERSEDED, it is never mutated or deleted in place.
    """

    CANDIDATE = "CANDIDATE"
    ACCEPTED = "ACCEPTED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# TaskPlan — mission-level structure
# ---------------------------------------------------------------------------


class TaskPlan(BaseModel):
    """MISSION-LEVEL STRUCTURE: objective, constraints, and the set/shape of
    sprints that compose the mission. Never conversation history, never run
    state, never model state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = BES_SCHEMA_VERSION
    plan_version: str
    mission_id: str
    objective: str
    constraints: list[str] = Field(default_factory=list)
    sprint_ids: list[str] = Field(default_factory=list)
    #: sprint_id -> the sprint_ids it depends on. Every key and every
    #: referenced id must be a member of sprint_ids (validated below).
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    status: MissionStatus = MissionStatus.DRAFT
    #: Optional: reuses the existing AIR-006 TaskLock when this mission was
    #: derived from a locked TaskSpecification, so sprints can later detect
    #: drift from the original mission intent (see SprintOutcome.TASK_DRIFT).
    task_lock: Optional[TaskLock] = None
    created_from: Optional[str] = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "TaskPlan":
        self.mission_id = self.mission_id.strip()
        self.objective = self.objective.strip()
        self.plan_version = self.plan_version.strip()
        if not self.mission_id:
            raise ValueError("mission_id must not be empty")
        if not self.objective:
            raise ValueError("objective must not be empty")
        if not self.plan_version:
            raise ValueError("plan_version must not be empty")

        if len(self.sprint_ids) != len(set(self.sprint_ids)):
            raise ValueError("sprint_ids must not contain duplicates")
        known = set(self.sprint_ids)

        unknown_keys = sorted(set(self.dependencies) - known)
        if unknown_keys:
            raise ValueError(f"dependencies key references unknown sprint_id: {unknown_keys[0]}")

        graph: dict[str, list[str]] = {sprint_id: [] for sprint_id in self.sprint_ids}
        for sprint_id, deps in self.dependencies.items():
            if sprint_id in deps:
                raise ValueError(f"sprint {sprint_id} cannot depend on itself")
            missing = sorted(set(deps) - known)
            if missing:
                raise ValueError(f"sprint {sprint_id} depends on unknown sprint_id: {missing[0]}")
            graph[sprint_id] = sorted(set(deps))

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(sprint_id: str) -> None:
            if sprint_id in visiting:
                raise ValueError(f"dependency cycle involving sprint {sprint_id}")
            if sprint_id in visited:
                return
            visiting.add(sprint_id)
            for dependency in graph[sprint_id]:
                visit(dependency)
            visiting.remove(sprint_id)
            visited.add(sprint_id)

        for sprint_id in sorted(known):
            visit(sprint_id)

        self.dependencies = {key: graph[key] for key in sorted(graph) if graph[key]}
        self.constraints = list(self.constraints)
        return self

    def canonical(self) -> str:
        """Deterministic JSON serialization (AIR canonical convention)."""
        return canonical_json(self)


# ---------------------------------------------------------------------------
# BoundedExecutionSprint — one bounded work unit
# ---------------------------------------------------------------------------


class BoundedExecutionSprint(BaseModel):
    """A work unit that is SMALL, BOUNDED, VALIDATABLE, PERSISTABLE,
    RESTARTABLE and ROUTABLE. A sprint is a work DEFINITION - not a Run (an
    execution attempt) and not ACTIVE_MODEL_CONTEXT.

    Carries enough structure for a future Context Compiler (AIR-BEW-003, not
    implemented here) to build a compiled working set instead of replaying
    full conversation history - see the module docstring's field-to-purpose
    mapping.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = BES_SCHEMA_VERSION
    sprint_id: str
    mission_id: str
    plan_version: str

    objective: str

    inputs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    #: Generic evidence labels/refs this sprint needs - never full evidence
    #: content (TOTAL_EVIDENCE_STATE != ACTIVE_SPRINT_CONTEXT).
    evidence_requirements: list[str] = Field(default_factory=list)

    #: Work-unit budgets only - never a provider cost or network-policy
    #: budget (those are CHASSIS's RunLimits, a separate concept).
    token_budget: int = Field(ge=1)
    #: 0 is a legitimate budget: a pure-reasoning sprint that must not call
    #: any tool at all.
    tool_budget: int = Field(ge=0)
    max_iterations: int = Field(ge=1)

    acceptance_criteria: list[str] = Field(min_length=1)
    #: field_name -> description of the expected output shape. Deliberately
    #: loose (contract-readiness only - see AIR-BEW-004's future validator).
    output_contract: dict[str, str] = Field(default_factory=dict)

    #: Reference only - never a hardcoded model or runtime. May hold a plain
    #: ExecutionProfile.profile_id or a canonical `profile:<hash>` identity
    #: string; both are valid strings this contract does not need to
    #: distinguish between.
    execution_profile_ref: Optional[str] = None

    dependencies: list[str] = Field(default_factory=list)

    status: SprintOutcome = SprintOutcome.PENDING

    #: Optional: propagated from the owning TaskPlan so a sprint is
    #: independently drift-checkable without needing the whole plan loaded.
    task_lock: Optional[TaskLock] = None

    @model_validator(mode="after")
    def _validate(self) -> "BoundedExecutionSprint":
        self.sprint_id = self.sprint_id.strip()
        self.mission_id = self.mission_id.strip()
        self.plan_version = self.plan_version.strip()
        self.objective = self.objective.strip()
        if not self.sprint_id:
            raise ValueError("sprint_id must not be empty")
        if not self.mission_id:
            raise ValueError("mission_id must not be empty")
        if not self.plan_version:
            raise ValueError("plan_version must not be empty")
        if not self.objective:
            raise ValueError("objective must not be empty")
        if self.sprint_id in self.dependencies:
            raise ValueError(f"sprint {self.sprint_id} cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("dependencies must not contain duplicates")
        self.dependencies = sorted(set(self.dependencies))
        if len(self.acceptance_criteria) != len(set(self.acceptance_criteria)):
            raise ValueError("acceptance_criteria must not contain duplicates")
        return self

    def canonical(self) -> str:
        return canonical_json(self)


# ---------------------------------------------------------------------------
# SprintCheckpoint — a validated/persistable result candidate
# ---------------------------------------------------------------------------


class SprintCheckpoint(BaseModel):
    """A validated/persistable RESULT CANDIDATE for one sprint. Never
    equivalent to memory/chat history, never equivalent to a Run.

    Suitable for immutable/versioned persistence later (AIR-BEW-002, not
    implemented here): checkpoint_version increments, CheckpointStatus
    tracks CANDIDATE -> ACCEPTED/REJECTED -> SUPERSEDED, and
    `content_fingerprint()` gives a deterministic way to detect whether a
    new version actually changed anything, all without a persistence
    backend existing yet.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = BES_SCHEMA_VERSION
    checkpoint_id: str
    mission_id: str
    sprint_id: str
    checkpoint_version: int = Field(ge=1)

    status: CheckpointStatus = CheckpointStatus.CANDIDATE

    confirmed_paths: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    #: References only, never embedded evidence content.
    evidence_refs: list[str] = Field(default_factory=list)

    open_questions: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)

    #: None only while status == CANDIDATE and the sprint has not concluded;
    #: once a checkpoint records a concluded sprint, this must be a terminal
    #: SprintOutcome (never PENDING - a checkpoint only exists for a sprint
    #: that has actually been attempted).
    failure_class: Optional[SprintOutcome] = None

    recommended_next_sprints: list[str] = Field(default_factory=list)

    #: Opaque reference to the run that produced this checkpoint. A checkpoint
    #: is not a Run - this is a pointer to one, not an embedding of one.
    created_from_run_ref: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self) -> "SprintCheckpoint":
        self.checkpoint_id = self.checkpoint_id.strip()
        self.mission_id = self.mission_id.strip()
        self.sprint_id = self.sprint_id.strip()
        if not self.checkpoint_id:
            raise ValueError("checkpoint_id must not be empty")
        if not self.mission_id:
            raise ValueError("mission_id must not be empty")
        if not self.sprint_id:
            raise ValueError("sprint_id must not be empty")
        if self.failure_class == SprintOutcome.PENDING:
            raise ValueError(
                "failure_class must not be PENDING - a checkpoint only exists "
                "after a run attempt concluded"
            )
        return self

    def canonical(self) -> str:
        return canonical_json(self)

    def content_fingerprint(self) -> str:
        """Deterministic sha256 over this checkpoint's SUBSTANTIVE content -
        excludes checkpoint_id/checkpoint_version/status/created_from_run_ref
        so two checkpoint versions of the SAME underlying findings collapse
        to the same fingerprint, and a caller can detect a no-op version
        bump before persisting one. Reuses identity.sha256_digest rather
        than a bespoke hash routine."""
        payload = {
            "mission_id": self.mission_id,
            "sprint_id": self.sprint_id,
            "confirmed_paths": sorted(self.confirmed_paths),
            "findings": sorted(self.findings),
            "evidence_refs": sorted(self.evidence_refs),
            "open_questions": sorted(self.open_questions),
            "unresolved_items": sorted(self.unresolved_items),
            "failure_class": self.failure_class.value if self.failure_class else None,
            "recommended_next_sprints": sorted(self.recommended_next_sprints),
        }
        return sha256_digest(payload)


# ---------------------------------------------------------------------------
# Cross-object reference validation (pure, no registry/persistence)
# ---------------------------------------------------------------------------


def sprint_belongs_to_plan(plan: TaskPlan, sprint: BoundedExecutionSprint) -> bool:
    """True only if `sprint` is structurally consistent with `plan` -
    mission_id, plan_version, and sprint_id membership all agree. Pure
    check; does not mutate either object or consult any registry."""
    return (
        sprint.mission_id == plan.mission_id
        and sprint.plan_version == plan.plan_version
        and sprint.sprint_id in plan.sprint_ids
    )


def checkpoint_belongs_to_sprint(
    sprint: BoundedExecutionSprint, checkpoint: SprintCheckpoint
) -> bool:
    """True only if `checkpoint` is structurally consistent with `sprint` -
    mission_id and sprint_id both agree."""
    return (
        checkpoint.mission_id == sprint.mission_id
        and checkpoint.sprint_id == sprint.sprint_id
    )
