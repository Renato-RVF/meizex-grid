"""AIR-BEW-002 — Deterministic, versioned filesystem persistence for
TaskPlan / BoundedExecutionSprint / SprintCheckpoint (contracts defined in
.bounded_execution, AIR-BEW-001).

This module implements PERSISTENCE ONLY. It does not execute a sprint, call
MHL, call CHASSIS, call a model, route a provider, invoke a tool, retry,
repartition, or perform fan-in. Saving/loading a record never changes its
`status` or any other field - the store persists exactly what it is given
and returns exactly what was persisted.

Discovery note (see AIR-BEW-002 delivery report for the full internal
report): AIR has no existing generic storage/registry framework to reuse -
`cli.py`'s JSON writes are plain non-atomic `open(...).write(...)`, and
`config.py` has no state-root convention prior to this module (one was
added there: AIR_STATE_ROOT, following the exact AIR_MODEL_ROOTS /
AIR_FORMULA_ROOTS env-var-override pattern already established). What IS
reused: `identity.canonical_json` / `identity.sha256_digest` for
deterministic serialization and content fingerprinting (same convention
identity.py and CHASSIS_API's ExecutionPolicyStore already use), and the
Pydantic contract types themselves for "reject malformed data" /
"reject unknown fields" (all three carry `extra="forbid"`) essentially for
free via `model_validate`.

Core principles enforced here (see NEXT.md AIR-BEW-002 for the full list):

    PERSIST BEFORE CONTINUING
    NO SILENT OVERWRITE
    CHECKPOINT != CONVERSATIONAL MEMORY
    RUN != SPRINT
    TOTAL_STATE != ACTIVE_CONTEXT

Storage layout (deterministic, human-inspectable, no timestamps or
memory-address-derived names in any path):

    <state_root>/
      bounded_execution/
        missions/
          <mission_id>/
            plans/
              <plan_version>.json
            sprints/
              <sprint_id>.json
            checkpoints/
              <sprint_id>/
                <checkpoint_version>.json

Every persisted file is a small integrity envelope, not the bare record:

    {
      "envelope_version": "1.0",
      "record_type": "TaskPlan" | "BoundedExecutionSprint" | "SprintCheckpoint",
      "integrity_sha256": sha256(canonical_json(record)),
      "content_fingerprint": <only for SprintCheckpoint - its own
                              content_fingerprint(), stored for convenience;
                              never used by the store's own integrity check>,
      "record": {...the contract's own model_dump(mode="json")...}
    }

On every load, `integrity_sha256` is recomputed from the loaded `record` and
compared - a mismatch (hand-edited file, partial write that somehow survived
atomic replace, disk corruption) is IntegrityError, fail-closed. This is
NOT identity.py's IdentityRef content-addressing (that machinery is reserved
for MODEL/ARTIFACT/RUNTIME/etc. kinds under AIR-IDENTITY-003/004, still
unstarted) - it is a much narrower, already-available integrity check reusing
only `sha256_digest`, not a parallel identity system.

Immutability semantics (same underlying mechanism, two labels because the
task spec treats them as distinct outcomes for two different contract kinds):

    TaskPlan @ (mission_id, plan_version):
        same content again  -> idempotent no-op, returns the stored plan
        different content    -> VersionConflictError

    BoundedExecutionSprint @ (mission_id, sprint_id):
        same content again  -> idempotent no-op, returns the stored sprint
        different content    -> VersionConflictError
        (No independent sprint-versioning concept exists yet - a sprint
        contract's own `status` field IS part of "content" here, so a
        future execution engine that wants to legitimately advance a
        sprint's status will need its own explicit update path, not a
        raw re-save. That is out of scope - AIR-BEW-002 implements
        persistence for the contracts as AIR-BEW-001 defined them, not a
        new sprint-lifecycle mutation API.)

    SprintCheckpoint @ (mission_id, sprint_id, checkpoint_version):
        same content again  -> idempotent no-op, returns the stored checkpoint
        different content    -> ImmutabilityConflictError
        A different checkpoint_version is simply a new, independent file -
        checkpoint history is versioned state, not a single mutable record.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from .bounded_execution import BoundedExecutionSprint, SprintCheckpoint, TaskPlan
from .identity import canonical_json, sha256_digest

STORE_ENVELOPE_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Typed failure taxonomy (narrow, on purpose - see module docstring)
# ---------------------------------------------------------------------------


class BoundedExecutionStoreError(Exception):
    """Base class for every typed persistence error this module raises."""


class NotFoundError(BoundedExecutionStoreError):
    """The requested record does not exist."""


class InvalidRecordError(BoundedExecutionStoreError):
    """The record failed contract validation, is not valid JSON, or the path
    component supplied to address it is unsafe."""


class UnsupportedSchemaVersionError(InvalidRecordError):
    """The stored envelope's schema_version/envelope_version is not one this
    version of the store knows how to read. Fail-closed; no migrations."""


class VersionConflictError(BoundedExecutionStoreError):
    """A TaskPlan or BoundedExecutionSprint save target already holds
    DIFFERENT content at the same logical version/id. Never auto-resolved."""


class ImmutabilityConflictError(BoundedExecutionStoreError):
    """A SprintCheckpoint save target already holds DIFFERENT content at the
    same (mission_id, sprint_id, checkpoint_version). Never auto-resolved -
    checkpoint history is versioned state; write a new checkpoint_version
    instead."""


class IntegrityError(BoundedExecutionStoreError):
    """A loaded record's recomputed sha256 does not match its stored
    integrity_sha256 - the file was modified outside this store, or
    corrupted. Fail-closed; the record is never returned."""


class StoreIOError(BoundedExecutionStoreError):
    """An unexpected OS-level failure (permissions, disk full, etc.)
    occurred during a read or write that was not itself a validation or
    conflict failure."""


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _safe_path_component(value: str, *, field_name: str) -> str:
    """Validate that `value` is safe to use as exactly one path segment.

    Rejects empty strings, any value containing a path separator (so `../`,
    absolute paths, and drive-prefix escapes are all rejected as a single
    class of problem: os.path.basename(value) != value means value is not
    ONE clean segment), NUL bytes, and the special `.`/`..` segments.
    """
    if not isinstance(value, str):
        raise InvalidRecordError(f"{field_name} must be a string")
    candidate = value.strip()
    if not candidate:
        raise InvalidRecordError(f"{field_name} must not be empty")
    if "\x00" in candidate:
        raise InvalidRecordError(f"{field_name} contains a NUL byte")
    if candidate in (".", ".."):
        raise InvalidRecordError(f"{field_name} must not be '.' or '..'")
    basename_posix = candidate.replace("\\", "/").rsplit("/", 1)[-1]
    if basename_posix != candidate or "/" in candidate or "\\" in candidate:
        raise InvalidRecordError(f"{field_name} must be a single path segment: {value!r}")
    if ":" in candidate:
        raise InvalidRecordError(f"{field_name} must not contain ':' (drive-prefix escape): {value!r}")
    return candidate


def _safe_int_component(value: int, *, field_name: str) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidRecordError(f"{field_name} must be an int")
    if value < 1:
        raise InvalidRecordError(f"{field_name} must be >= 1")
    return str(value)


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, content: str) -> None:
    """serialize -> write temporary sibling -> flush + fsync -> atomic
    replace/rename. Never partially visible: readers either see the
    complete old file or the complete new one."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
            raise
    except (OSError, PermissionError) as exc:
        raise StoreIOError(f"failed to write {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Envelope helpers (shared by all three record kinds)
# ---------------------------------------------------------------------------


def _build_envelope(record_type: str, record: dict, *, extra: Optional[dict] = None) -> dict:
    envelope = {
        "envelope_version": STORE_ENVELOPE_VERSION,
        "record_type": record_type,
        "integrity_sha256": sha256_digest(record),
        "record": record,
    }
    if extra:
        envelope.update(extra)
    return envelope


def _read_envelope(path: Path, *, expected_record_type: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NotFoundError(f"no record at {path}") from None
    except (OSError, PermissionError) as exc:
        raise StoreIOError(f"failed to read {path}: {exc}") from exc

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidRecordError(f"malformed JSON at {path}: {exc}") from exc

    if not isinstance(envelope, dict):
        raise InvalidRecordError(f"envelope at {path} is not a JSON object")

    if envelope.get("envelope_version") != STORE_ENVELOPE_VERSION:
        raise UnsupportedSchemaVersionError(
            f"unsupported envelope_version at {path}: {envelope.get('envelope_version')!r}"
        )
    if envelope.get("record_type") != expected_record_type:
        raise InvalidRecordError(
            f"record_type mismatch at {path}: expected {expected_record_type!r}, "
            f"found {envelope.get('record_type')!r}"
        )
    record = envelope.get("record")
    if not isinstance(record, dict):
        raise InvalidRecordError(f"envelope at {path} has no valid 'record' object")

    stored_hash = envelope.get("integrity_sha256")
    recomputed_hash = sha256_digest(record)
    if stored_hash != recomputed_hash:
        raise IntegrityError(
            f"integrity check failed at {path}: stored={stored_hash!r} recomputed={recomputed_hash!r}"
        )
    return envelope


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class BoundedExecutionStore:
    """Deterministic, versioned filesystem persistence for TaskPlan,
    BoundedExecutionSprint and SprintCheckpoint. No in-memory registry - every
    operation reads/writes the filesystem directly, so a second `
    BoundedExecutionStore(root)` instance pointed at the same root
    reconstructs identical state (see test_process_restart_reconstruction)."""

    def __init__(self, root: Path | str):
        self.root = Path(root) / "bounded_execution"

    # -- path helpers --------------------------------------------------

    def _mission_dir(self, mission_id: str) -> Path:
        safe = _safe_path_component(mission_id, field_name="mission_id")
        return self.root / "missions" / safe

    def _plan_path(self, mission_id: str, plan_version: str) -> Path:
        safe_version = _safe_path_component(plan_version, field_name="plan_version")
        return self._mission_dir(mission_id) / "plans" / f"{safe_version}.json"

    def _sprint_path(self, mission_id: str, sprint_id: str) -> Path:
        safe_sprint = _safe_path_component(sprint_id, field_name="sprint_id")
        return self._mission_dir(mission_id) / "sprints" / f"{safe_sprint}.json"

    def _checkpoint_dir(self, mission_id: str, sprint_id: str) -> Path:
        safe_sprint = _safe_path_component(sprint_id, field_name="sprint_id")
        return self._mission_dir(mission_id) / "checkpoints" / safe_sprint

    def _checkpoint_path(self, mission_id: str, sprint_id: str, checkpoint_version: int) -> Path:
        safe_version = _safe_int_component(checkpoint_version, field_name="checkpoint_version")
        return self._checkpoint_dir(mission_id, sprint_id) / f"{safe_version}.json"

    # -- TaskPlan ---------------------------------------------------------

    def save_task_plan(self, plan: TaskPlan) -> TaskPlan:
        path = self._plan_path(plan.mission_id, plan.plan_version)
        record = plan.model_dump(mode="json")
        if path.exists():
            existing_envelope = _read_envelope(path, expected_record_type="TaskPlan")
            if canonical_json(existing_envelope["record"]) == canonical_json(record):
                return TaskPlan.model_validate(existing_envelope["record"])
            raise VersionConflictError(
                f"TaskPlan mission_id={plan.mission_id!r} plan_version={plan.plan_version!r} "
                "already exists with different content"
            )
        envelope = _build_envelope("TaskPlan", record)
        _atomic_write_text(path, canonical_json(envelope))
        return plan

    def load_task_plan(self, mission_id: str, plan_version: str) -> TaskPlan:
        path = self._plan_path(mission_id, plan_version)
        envelope = _read_envelope(path, expected_record_type="TaskPlan")
        try:
            return TaskPlan.model_validate(envelope["record"])
        except Exception as exc:  # pydantic ValidationError
            raise InvalidRecordError(f"stored TaskPlan at {path} fails contract validation: {exc}") from exc

    def list_plans(self, mission_id: str) -> list[str]:
        plans_dir = self._mission_dir(mission_id) / "plans"
        if not plans_dir.is_dir():
            return []
        return sorted(p.stem for p in plans_dir.glob("*.json"))

    # -- BoundedExecutionSprint --------------------------------------------

    def save_sprint(self, sprint: BoundedExecutionSprint) -> BoundedExecutionSprint:
        path = self._sprint_path(sprint.mission_id, sprint.sprint_id)
        record = sprint.model_dump(mode="json")
        if path.exists():
            existing_envelope = _read_envelope(path, expected_record_type="BoundedExecutionSprint")
            if canonical_json(existing_envelope["record"]) == canonical_json(record):
                return BoundedExecutionSprint.model_validate(existing_envelope["record"])
            raise VersionConflictError(
                f"BoundedExecutionSprint mission_id={sprint.mission_id!r} "
                f"sprint_id={sprint.sprint_id!r} already exists with different content"
            )
        envelope = _build_envelope("BoundedExecutionSprint", record)
        _atomic_write_text(path, canonical_json(envelope))
        return sprint

    def load_sprint(self, mission_id: str, sprint_id: str) -> BoundedExecutionSprint:
        path = self._sprint_path(mission_id, sprint_id)
        envelope = _read_envelope(path, expected_record_type="BoundedExecutionSprint")
        try:
            return BoundedExecutionSprint.model_validate(envelope["record"])
        except Exception as exc:
            raise InvalidRecordError(f"stored Sprint at {path} fails contract validation: {exc}") from exc

    def list_sprints(self, mission_id: str) -> list[str]:
        sprints_dir = self._mission_dir(mission_id) / "sprints"
        if not sprints_dir.is_dir():
            return []
        return sorted(p.stem for p in sprints_dir.glob("*.json"))

    # -- SprintCheckpoint ---------------------------------------------------

    def save_checkpoint(self, checkpoint: SprintCheckpoint) -> SprintCheckpoint:
        path = self._checkpoint_path(
            checkpoint.mission_id, checkpoint.sprint_id, checkpoint.checkpoint_version
        )
        record = checkpoint.model_dump(mode="json")
        if path.exists():
            existing_envelope = _read_envelope(path, expected_record_type="SprintCheckpoint")
            if canonical_json(existing_envelope["record"]) == canonical_json(record):
                return SprintCheckpoint.model_validate(existing_envelope["record"])
            raise ImmutabilityConflictError(
                f"SprintCheckpoint mission_id={checkpoint.mission_id!r} "
                f"sprint_id={checkpoint.sprint_id!r} "
                f"checkpoint_version={checkpoint.checkpoint_version!r} "
                "already exists with different content"
            )
        envelope = _build_envelope(
            "SprintCheckpoint", record, extra={"content_fingerprint": checkpoint.content_fingerprint()}
        )
        _atomic_write_text(path, canonical_json(envelope))
        return checkpoint

    def load_checkpoint(
        self, mission_id: str, sprint_id: str, checkpoint_version: int
    ) -> SprintCheckpoint:
        path = self._checkpoint_path(mission_id, sprint_id, checkpoint_version)
        envelope = _read_envelope(path, expected_record_type="SprintCheckpoint")
        try:
            checkpoint = SprintCheckpoint.model_validate(envelope["record"])
        except Exception as exc:
            raise InvalidRecordError(f"stored Checkpoint at {path} fails contract validation: {exc}") from exc
        stored_fingerprint = envelope.get("content_fingerprint")
        if stored_fingerprint is not None and stored_fingerprint != checkpoint.content_fingerprint():
            # The integrity_sha256 check in _read_envelope already caught any
            # tampering with `record` itself - this is a second, independent
            # check specifically against the convenience fingerprint field,
            # in case a future writer of this format populates it
            # inconsistently with the record it sits beside.
            raise IntegrityError(
                f"stored content_fingerprint at {path} does not match the loaded record's own "
                "content_fingerprint()"
            )
        return checkpoint

    def list_checkpoint_versions(self, mission_id: str, sprint_id: str) -> list[int]:
        """Deterministic ascending version list, derived only from filenames
        (never file modification time)."""
        checkpoint_dir = self._checkpoint_dir(mission_id, sprint_id)
        if not checkpoint_dir.is_dir():
            return []
        versions: list[int] = []
        for item in checkpoint_dir.glob("*.json"):
            try:
                versions.append(int(item.stem))
            except ValueError:
                continue
        return sorted(versions)

    def load_latest_checkpoint(self, mission_id: str, sprint_id: str) -> SprintCheckpoint:
        """'Latest' means the highest explicit checkpoint_version that
        exists - never inferred from file modification time."""
        versions = self.list_checkpoint_versions(mission_id, sprint_id)
        if not versions:
            raise NotFoundError(
                f"no checkpoints exist for mission_id={mission_id!r} sprint_id={sprint_id!r}"
            )
        return self.load_checkpoint(mission_id, sprint_id, max(versions))
