"""Loads ExecutionProfile definitions from the top-level `profiles/` directory
(YAML or JSON) instead of hardcoding profiles in Python."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from meizex_mrw.profiles.schema import ExecutionProfile

# src/meizex_mrw/profiles/loader.py -> parents[3] is the repo root, whose
# sibling top-level `profiles/` directory holds the actual profile files
# (kept separate from this package, which holds the schema/loader code).
DEFAULT_PROFILES_DIR = Path(__file__).resolve().parents[3] / "profiles"


class ProfileNotFoundError(FileNotFoundError):
    pass


class ProfileRegistry:
    def __init__(self, directory: str | Path = DEFAULT_PROFILES_DIR) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, ExecutionProfile] = {}

    def get(self, name: str) -> ExecutionProfile:
        if name not in self._cache:
            self._cache[name] = self._load(name)
        return self._cache[name]

    def names(self) -> list[str]:
        """All profile names in the directory, sorted (no caching, reflects disk)."""
        found: list[str] = []
        if not self.directory.is_dir():
            return found
        for path in sorted(self.directory.iterdir()):
            if path.suffix.lower() in (".yaml", ".yml", ".json") and path.is_file():
                found.append(path.stem)
        return found

    def _load(self, name: str) -> ExecutionProfile:
        for suffix in (".yaml", ".yml", ".json"):
            path = self.directory / f"{name}{suffix}"
            if path.exists():
                return self._load_path(path, name)
        raise ProfileNotFoundError(f"no profile named {name!r} in {self.directory}")

    @staticmethod
    def _load_path(path: Path, name: str) -> ExecutionProfile:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"profile file {path} did not contain a mapping")
        data.setdefault("name", name)
        return ExecutionProfile.model_validate(data)


_default_registry = ProfileRegistry()


def get(name: str) -> ExecutionProfile:
    """Module-level convenience: `from meizex_mrw import profiles; profiles.get(name)`."""
    return _default_registry.get(name)


def names() -> list[str]:
    """Module-level convenience: list every available profile name."""
    return _default_registry.names()
