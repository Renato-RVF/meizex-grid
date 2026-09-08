"""SHA256 integrity verification for local model files.

Gap this closes: MEIZEX_IMAGES_CHECK_CLI ships a ``models.lock.json`` that
*records* a model+mmproj pair's SHA256, but nothing in that project ever
reads it back — it is a snapshot, not an enforced lock. MRW's providers
never touch local model files directly either (Chassis/LM Studio are
HTTP-only), so this module is new, standalone functionality: given a lock
file and a model directory, verify what is actually on disk still matches
what was recorded, and fail loudly (not silently) on drift — a model
swapped, truncated, or corrupted should never be used as if nothing
happened.

Deliberately has no dependency on any provider or profile — usable from
the CLI (``mrw model-lock verify``), a future direct-loading provider, or
a standalone script.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

_HASH_CHUNK_SIZE = 1024 * 1024


class ModelLockError(RuntimeError):
    """Raised for any lock-file problem (missing, malformed, unreadable)."""


class ModelIntegrityError(RuntimeError):
    """Raised when a file on disk does not match its recorded lock entry."""

    def __init__(self, filename: str, expected_sha256: str, actual_sha256: str) -> None:
        self.filename = filename
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256
        super().__init__(
            f"{filename}: SHA256 mismatch — expected {expected_sha256}, got {actual_sha256}"
        )


class ModelLockEntry(BaseModel):
    """One locked file: its expected name, size, and content hash."""

    filename: str
    size_bytes: int
    sha256: str


class ModelLock(BaseModel):
    """A lock file: a named pair (or single file) plus its locked entries.

    Shape intentionally mirrors images_check's models.lock.json
    (schema_version, pair_id, one entry per role) rather than inventing a
    new format, since that file already exists for the Gemma-4 pair.
    """

    schema_version: str = "1.0"
    pair_id: str
    entries: dict[str, ModelLockEntry] = Field(default_factory=dict)


def compute_sha256(path: Path) -> str:
    """Stream-hash a file; never loads it fully into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_lock(path: Path) -> ModelLock:
    """Load a lock file, translating images_check's flat shape
    (``{"model": {...}, "mmproj": {...}}``) into ``ModelLock.entries``
    transparently, so the same Gemma-4 lock file already on disk in
    MEIZEX_GGUF_FORMULA can be pointed at directly."""
    if not path.is_file():
        raise ModelLockError(f"lock file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelLockError(f"lock file is not valid JSON: {path}") from exc

    pair_id = raw.get("pair_id")
    if not pair_id:
        raise ModelLockError(f"lock file missing 'pair_id': {path}")

    entries: dict[str, ModelLockEntry] = {}
    for role, value in raw.items():
        if role in ("schema_version", "pair_id") or not isinstance(value, dict):
            continue
        try:
            entries[role] = ModelLockEntry.model_validate(value)
        except Exception as exc:  # pydantic.ValidationError, narrowed by role
            raise ModelLockError(f"lock file entry {role!r} is malformed: {path}") from exc

    if not entries:
        raise ModelLockError(f"lock file has no recognizable entries: {path}")

    return ModelLock(
        schema_version=raw.get("schema_version", "1.0"), pair_id=pair_id, entries=entries
    )


def verify_file(path: Path, entry: ModelLockEntry) -> None:
    """Raise ModelLockError if the file is missing, ModelIntegrityError if
    its size or hash drifted from the lock entry. Silent (returns None) on
    a clean match."""
    if not path.is_file():
        raise ModelLockError(f"locked file not found on disk: {path}")
    actual_size = path.stat().st_size
    if actual_size != entry.size_bytes:
        raise ModelIntegrityError(
            entry.filename, entry.sha256, f"<size mismatch: {actual_size} bytes>"
        )
    actual_sha256 = compute_sha256(path)
    if actual_sha256 != entry.sha256.upper():
        raise ModelIntegrityError(entry.filename, entry.sha256, actual_sha256)


def verify_directory(model_dir: Path, lock: ModelLock) -> dict[str, Path]:
    """Verify every entry in ``lock`` against files in ``model_dir`` (matched
    by ``entry.filename``). Returns the resolved path per role on success;
    raises on the first failure (ModelLockError or ModelIntegrityError)."""
    resolved: dict[str, Path] = {}
    for role, entry in lock.entries.items():
        path = model_dir / entry.filename
        verify_file(path, entry)
        resolved[role] = path
    return resolved
