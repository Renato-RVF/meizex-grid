"""Deterministic task classifier — v0/v1.

No LLM call. Signals: presence of a filesystem path, filesystem-oriented
verbs/nouns (PT-BR and EN-US, as auxiliary signals only — not the
architecture, per the audit's finding that the legacy tool-selection
logic depended on PT-BR keywords as its *only* mechanism).

v1 adds ``required_capabilities``: the canonical capability vocabulary the
Capability Router (milestone 5) reasons about, derived by the deterministic
capabilities.requirements module. The task_type stays v0-compatible so the
worker/CLI contract is unchanged.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from meizex_mrw.capabilities.requirements import required_capabilities

TaskType = str  # open string; today only "filesystem_read" | "general_chat" | "unknown"


class ClassifiedTask(BaseModel):
    task_type: TaskType
    confidence: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)


_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/][^\s\"']+|(?:\.{1,2})?/[^\s\"']{2,}")

_FILESYSTEM_VERBS = (
    "analise",
    "analisar",
    "analyze",
    "leia",
    "ler",
    "read",
    "liste",
    "listar",
    "list",
    "explore",
    "explorar",
    "inspecione",
    "inspecionar",
    "inspect",
    "busque",
    "buscar",
    "search",
    "verifique",
    "verificar",
    "check",
    "escaneie",
    "scan",
)

_FILESYSTEM_NOUNS = (
    "diretório",
    "diretorio",
    "directory",
    "folder",
    "pasta",
    "arquivo",
    "arquivos",
    "file",
    "files",
    "repositório",
    "repositorio",
    "repository",
    "repo",
    "codebase",
)


def classify(message: str) -> ClassifiedTask:
    """Same input, same output — no randomness, no network call."""
    caps = required_capabilities(message)
    if len(message.strip()) < 3:
        return ClassifiedTask(
            task_type="unknown", confidence=0.0, signals=[], required_capabilities=caps
        )

    signals: list[str] = []
    has_path = bool(_PATH_PATTERN.search(message))
    if has_path:
        signals.append("path_present")

    lowered = message.lower()
    has_fs_verb = any(verb in lowered for verb in _FILESYSTEM_VERBS)
    if has_fs_verb:
        signals.append("filesystem_verb")
    has_fs_noun = any(noun in lowered for noun in _FILESYSTEM_NOUNS)
    if has_fs_noun:
        signals.append("filesystem_noun")

    if has_path and has_fs_verb:
        return ClassifiedTask(
            task_type="filesystem_read",
            confidence=0.95,
            signals=signals,
            required_capabilities=caps,
        )
    if has_path or (has_fs_verb and has_fs_noun):
        return ClassifiedTask(
            task_type="filesystem_read",
            confidence=0.7,
            signals=signals,
            required_capabilities=caps,
        )

    return ClassifiedTask(
        task_type="general_chat",
        confidence=0.5,
        signals=signals,
        required_capabilities=caps,
    )
