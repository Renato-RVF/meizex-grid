"""Pending route persistence for MRW."""

import json
from abc import ABC, abstractmethod
from pathlib import Path

from meizex_mrw.dispatch.models import PendingStateEnvelope


class PendingRouteStore(ABC):
    """Abstract store for paused missions (escalation to parent orchestrator)."""

    @abstractmethod
    def save(self, token: str, state: PendingStateEnvelope) -> None:
        pass

    @abstractmethod
    def load(self, token: str) -> PendingStateEnvelope:
        pass

    @abstractmethod
    def consume(self, token: str) -> PendingStateEnvelope:
        pass


class StoreError(Exception):
    pass


class TokenNotFoundError(StoreError):
    pass


class InMemoryRouteStore(PendingRouteStore):
    """Simple in-memory store for unit tests and local transient runs."""

    def __init__(self):
        self._data: dict[str, PendingStateEnvelope] = {}

    def save(self, token: str, state: PendingStateEnvelope) -> None:
        self._data[token] = state

    def load(self, token: str) -> PendingStateEnvelope:
        if token not in self._data:
            raise TokenNotFoundError(f"Token not found: {token}")
        return self._data[token]

    def consume(self, token: str) -> PendingStateEnvelope:
        if token not in self._data:
            raise TokenNotFoundError(f"Token not found: {token}")
        return self._data.pop(token)


class FilePendingRouteStore(PendingRouteStore):
    """File-backed persistence store for CLI usage across process boundaries.
    Defaults to ~/.meizex/mrw/pending_state.
    """

    def __init__(self, directory: Path | None = None):
        self._dir = directory or (Path.home() / ".meizex" / "mrw" / "pending_state")
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, token: str) -> Path:
        # Validate token to prevent path traversal
        if not token or "/" in token or "\\" in token or ".." in token:
            raise StoreError(f"Invalid token format: {token}")
        return self._dir / f"{token}.json"

    def save(self, token: str, state: PendingStateEnvelope) -> None:
        path = self._path(token)
        # Write atomically using a tmp file approach
        tmp_path = path.with_suffix(".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                f.write(state.model_dump_json(indent=2))
            tmp_path.replace(path)
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise StoreError(f"Failed to save state: {e}") from e

    def load(self, token: str) -> PendingStateEnvelope:
        path = self._path(token)
        if not path.exists():
            raise TokenNotFoundError(f"Token not found: {token}")
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return PendingStateEnvelope.model_validate(data)
        except Exception as e:
            raise StoreError(f"Failed to load or parse state: {e}") from e

    def consume(self, token: str) -> PendingStateEnvelope:
        state = self.load(token)
        path = self._path(token)
        try:
            path.unlink()
        except Exception as e:
            raise StoreError(f"Failed to consume token (delete file): {e}") from e
        return state
