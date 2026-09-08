"""Append-only JSONL event store.

Kept from the legacy design: append-only, one JSON object per line,
secrets redacted before they ever touch disk. Changed: reads are
cursor-based (:meth:`EventStore.read_from`) instead of always re-parsing
the whole file — the legacy `read_all()` re-read the entire JSONL on
every poll, which the audit flagged as a real scalability ceiling. A
byte offset from a previous read (or 0) is always safe to pass back in;
the store never truncates or rewrites lines in place, only appends.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from meizex_mrw.events.models import EVENT_CLASS_MAP, AnyEvent
from meizex_mrw.security import redact_sensitive_value

_OBSERVATIONS_ENV = "MRW_OBSERVATIONS_DIR"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def default_observations_dir() -> Path:
    """Deterministic local root for run-scoped observation streams.

    ``MRW_OBSERVATIONS_DIR`` overrides the default (used by tests and by any
    operator that wants observations kept outside the user home).
    """
    env = os.environ.get(_OBSERVATIONS_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".meizex" / "mrw" / "observations"


def validate_run_id(run_id: str) -> str:
    """Validate a run_id before it becomes part of a filesystem path.

    Rejects empty ids, path traversal and any character outside
    ``[A-Za-z0-9_-]`` so a run_id can never escape the observations root.
    """
    if not run_id or not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise ValueError(f"Invalid run_id format: {run_id!r}")
    return run_id


class EventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_run(cls, run_id: str, *, directory: str | Path | None = None) -> EventStore:
        """Build the single run-scoped stream: one run, one JSONL file."""
        run_id = validate_run_id(run_id)
        base = Path(directory).expanduser() if directory else default_observations_dir()
        return cls(base / f"{run_id}.jsonl")

    def append(self, event: AnyEvent) -> None:
        payload = redact_sensitive_value(event.model_dump(mode="json"))
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read_all(self, *, session_id: str | None = None) -> list[AnyEvent]:
        events, _ = self.read_from(0, session_id=session_id)
        return events

    def read_from(
        self, offset: int, *, session_id: str | None = None
    ) -> tuple[list[AnyEvent], int]:
        """Read events starting at a byte offset. Returns (events, new_offset).

        Only complete (newline-terminated) lines are consumed — a line still
        being written by a concurrent append is left for the next call, so
        the returned offset never points into the middle of a record.
        """
        if not self.path.exists():
            return [], offset

        with self.path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()

        if not chunk:
            return [], offset

        complete = chunk
        if not chunk.endswith(b"\n"):
            last_newline = chunk.rfind(b"\n")
            if last_newline == -1:
                return [], offset  # no complete line yet
            complete = chunk[: last_newline + 1]

        new_offset = offset + len(complete)
        events: list[AnyEvent] = []
        for raw_line in complete.split(b"\n"):
            line = raw_line.strip()
            if not line:
                continue
            data = json.loads(line.decode("utf-8"))
            if session_id is not None and data.get("session_id") != session_id:
                continue
            event_class = EVENT_CLASS_MAP.get(data.get("event_type"))
            if event_class is not None:
                events.append(event_class.model_validate(data))
        return events, new_offset
