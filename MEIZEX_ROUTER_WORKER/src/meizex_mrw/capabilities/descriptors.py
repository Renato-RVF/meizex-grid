"""Deterministic capability descriptors — semantic vocabulary as data.

M5.1 Problem 2: the keyword rules that classify a mission into capability
requirements used to live hard-coded inside ``requirements.py``. This module
moves that vocabulary into a typed, data-driven structure:

* :class:`MatchSpec` — the deterministic matching spec (AND across groups,
  OR within a group; ``path_or_groups`` also matches when a filesystem path
  is present).
* :class:`RequirementOutput` — one capability requirement a matched
  descriptor emits.
* :class:`CapabilityDescriptor` — the full descriptor for one rule.
* :func:`load_descriptors` — loads the externalized data file
  (``docs/capability_descriptors.json``, schema ``meizex.capability-descriptor.v1``)
  and validates it against the models.

Design decisions:

* The descriptor file is the source of truth; :data:`EMBEDDED_DESCRIPTORS` is
  only a fallback when the file is missing, so behavior is preserved if the
  repo is not fully installed.
* A *missing* file falls back to the embedded copy. A *malformed* file raises
  :class:`DescriptorLoadError` — fail loud, never silently degrade matching.
* Matching stays fully deterministic (exact substring terms, same input ->
  same output). No embeddings, no LLM classifier here.
* The capability registry schema (``meizex.capability-registry.v1``) is NOT
  changed; descriptors live in their own document so the registry stays a pure
  resource inventory.

Future classifier evolution (registered in NEXT.md, not implemented here):
deterministic exact/rule matching -> registry-driven descriptors -> lexical
similarity -> embedding similarity -> a semantic classifier only when
ambiguity remains.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from meizex_mrw.capabilities.models import CapabilityRequirement

_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/][^\s\"']+|(?:\.{1,2})?/[^\s\"']{2,}")

# src/meizex_mrw/capabilities/descriptors.py -> parents[3] is the repo root,
# whose sibling docs/ directory holds the descriptor document.
DEFAULT_DESCRIPTORS_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "capability_descriptors.json"
)


class DescriptorError(RuntimeError):
    """Base error for descriptor load/validation failures."""


class DescriptorLoadError(DescriptorError):
    """The descriptor file is malformed or fails schema validation."""


class MatchSpec(BaseModel):
    """Deterministic match spec: AND across groups, OR within a group.

    ``path_or_groups`` widens the match to also fire when a filesystem path
    appears in the message (preserving the v1 filesystem rule).
    """

    groups: list[list[str]] = Field(default_factory=list)
    path_or_groups: bool = False


class RequirementOutput(BaseModel):
    """One requirement emitted by a matched descriptor."""

    capability: str
    kind: str = "required"
    input_modality: str | None = None
    output_modality: str | None = None
    local_required: bool = True
    cloud_allowed: bool = False
    deterministic_preferred: bool = False
    validation_required: bool = False
    preferred_kind: str | None = None
    minimum_validation_level: str = "FOUND_NOT_TESTED_ALLOWED"


class CapabilityDescriptor(BaseModel):
    """One data-driven classification rule."""

    capability: str
    match: MatchSpec
    outputs: list[RequirementOutput] = Field(default_factory=list)


def _matches_groups(message: str, groups: list[list[str]]) -> bool:
    return all(any(word in message for word in group) for group in groups)


def match_descriptor(descriptor: CapabilityDescriptor, message: str) -> bool:
    """Deterministic match of one descriptor against a lowered message."""
    if descriptor.match.path_or_groups:
        if _PATH_PATTERN.search(message):
            return True
        return _matches_groups(message, descriptor.match.groups)
    return _matches_groups(message, descriptor.match.groups)


def to_requirements(descriptor: CapabilityDescriptor) -> list[CapabilityRequirement]:
    """The capability requirements a matched descriptor emits."""
    reqs: list[CapabilityRequirement] = []
    for output in descriptor.outputs:
        reqs.append(
            CapabilityRequirement(
                capability=output.capability,
                kind=output.kind,  # type: ignore[arg-type]
                input_modality=output.input_modality,
                output_modality=output.output_modality,
                local_required=output.local_required,
                cloud_allowed=output.cloud_allowed,
                deterministic_preferred=output.deterministic_preferred,
                validation_required=output.validation_required,
                preferred_kind=output.preferred_kind,
                minimum_validation_level=output.minimum_validation_level,  # type: ignore[arg-type]
            )
        )
    return reqs


def load_descriptors(path: str | Path = DEFAULT_DESCRIPTORS_PATH) -> list[CapabilityDescriptor]:
    """Load and validate the descriptor document.

    A missing file falls back to the embedded descriptors; a malformed file
    raises :class:`DescriptorLoadError` (never silent degradation).
    """
    target = Path(path)
    if not target.exists():
        return EMBEDDED_DESCRIPTORS
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise DescriptorLoadError(f"could not read descriptor file: {target}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DescriptorLoadError(f"descriptor file is not valid JSON: {target}") from exc
    if not isinstance(data, dict) or "descriptors" not in data:
        raise DescriptorLoadError(f"descriptor file is missing the 'descriptors' key: {target}")
    try:
        return [CapabilityDescriptor.model_validate(item) for item in data["descriptors"]]
    except ValidationError as exc:
        raise DescriptorLoadError(f"descriptor file failed schema validation: {target}") from exc


# Embedded fallback mirroring docs/capability_descriptors.json (same rules,
# same order). Used only when the data file is absent.
EMBEDDED_DESCRIPTORS: list[CapabilityDescriptor] = [
    CapabilityDescriptor(
        capability="person_detection",
        match=MatchSpec(
            groups=[
                ["imagem", "imagens", "image", "images", "foto", "fotos", "photo", "photos"],
                ["pessoa", "pessoas", "people", "person", "humano", "humanos", "human"],
            ]
        ),
        outputs=[
            RequirementOutput(
                capability="person_detection",
                kind="required",
                input_modality="image",
                preferred_kind="detector",
                minimum_validation_level="VALIDATED_REQUIRED",
            ),
            RequirementOutput(
                capability="object_detection",
                kind="preferred",
                input_modality="image",
                preferred_kind="detector",
            ),
        ],
    ),
    CapabilityDescriptor(
        capability="document_parsing",
        match=MatchSpec(
            groups=[
                ["pdf", "documento", "document", "planilha"],
                ["extraia", "extrair", "extract", "parse", "processe", "processar", "process"],
            ]
        ),
        outputs=[
            RequirementOutput(
                capability="document_parsing",
                kind="required",
                input_modality="pdf",
            ),
            RequirementOutput(capability="ocr", kind="preferred"),
        ],
    ),
    CapabilityDescriptor(
        capability="speech_to_text",
        match=MatchSpec(
            groups=[
                ["áudio", "audio", "gravação", "gravacao", "recording", "podcast"],
                ["transcreva", "transcrever", "transcribe", "transcrição", "transcricao"],
            ]
        ),
        outputs=[
            RequirementOutput(
                capability="speech_to_text",
                kind="required",
                input_modality="audio",
            )
        ],
    ),
    CapabilityDescriptor(
        capability="deterministic_processing",
        match=MatchSpec(
            groups=[
                ["some", "somar", "sum", "agrupe", "agrupar", "group", "totalize", "totalizar"],
                ["json", "valores", "values", "categoria", "category", "registros", "records"],
            ]
        ),
        outputs=[
            RequirementOutput(
                capability="deterministic_processing",
                kind="required",
                deterministic_preferred=True,
            )
        ],
    ),
    CapabilityDescriptor(
        capability="repository_analysis",
        match=MatchSpec(
            groups=[
                ["repositório", "repositorio", "repository", "repo", "codebase"],
                [
                    "bug",
                    "erro",
                    "error",
                    "explique",
                    "expliquei",
                    "explain",
                    "possível",
                    "possible",
                ],
            ]
        ),
        outputs=[
            RequirementOutput(capability="coding", kind="required"),
            RequirementOutput(capability="general_reasoning", kind="required"),
            RequirementOutput(capability="filesystem_read", kind="preferred"),
        ],
    ),
    CapabilityDescriptor(
        capability="summarization",
        match=MatchSpec(
            groups=[["resuma", "resumo", "resumir", "summarize", "summarise", "summary"]]
        ),
        outputs=[
            RequirementOutput(capability="general_reasoning", kind="required"),
            RequirementOutput(capability="summarization", kind="preferred"),
        ],
    ),
    CapabilityDescriptor(
        capability="filesystem_read",
        match=MatchSpec(
            path_or_groups=True,
            groups=[
                [
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
                ],
                [
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
                ],
            ],
        ),
        outputs=[RequirementOutput(capability="filesystem_read", kind="required")],
    ),
]
