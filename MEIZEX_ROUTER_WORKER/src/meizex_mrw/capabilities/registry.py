"""Typed, read-only reader for the local capability registry.

The registry JSON (docs/local_capability_registry.json, schema
``meizex.capability-registry.v1``) is the operational input for the
Capability Router. This layer turns that file into typed pydantic models —
no database, no separate service, no writing.

The registry remains:
* readable and versionable in Git (it is a plain JSON document);
* evidence-based (DECLARED vs OBSERVED vs VALIDATED are kept separate);
* easily auditable (this module never fabricates validation levels).

This module never modifies the file. It only loads, validates, and queries.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from meizex_mrw.capabilities.models import (
    CapabilityResource,
    ValidationLevel,
)

# src/meizex_mrw/capabilities/registry.py -> parents[3] is the repo root,
# whose sibling docs/ directory holds the registry document.
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "local_capability_registry.json"
)

# Ordering preference for evidence levels: a higher-validation resource beats
# a lower one when both can satisfy a requirement. FOUND_NOT_TESTED is never
# preferred without explicit justification (a requirement asking for
# validation_required removes it entirely).
VALIDATION_ORDER: tuple[ValidationLevel, ...] = (
    "VALIDATED",
    "OBSERVED",
    "DECLARED",
    "FOUND_NOT_TESTED",
)
VALIDATION_PREFERENCE: dict[ValidationLevel, int] = {
    level: index for index, level in enumerate(VALIDATION_ORDER)
}

# Canonical requirement capabilities -> registry capability labels that satisfy
# them. This is the ONLY mapping between mission language and registry
# capabilities; keep it small, explicit, and derived from the inventory audit.
# Never invent a capability label that the registry does not actually contain.
CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "general_reasoning": ("text-generation", "reasoning", "summarization"),
    "summarization": ("text-generation", "summarization"),
    "structured_tool_calling": ("tool-calling", "tool-use"),
    "filesystem_read": ("filesystem-analysis", "routing"),
    "coding": ("coding",),
    "document_parsing": ("document-parsing", "form-field-extraction"),
    "ocr": ("ocr", "text-extraction"),
    "object_detection": ("object-detection",),
    "person_detection": ("person-detection", "object-detection"),
    "image_embedding": ("embedding", "image-retrieval"),
    "semantic_similarity": ("embedding",),
    "speech_to_text": ("speech-to-text", "transcription"),
    "lexical_search": ("bm25-search", "fts5"),
    "deterministic_processing": ("bm25-search", "fts5", "filesystem-analysis", "routing"),
}


class RegistryError(RuntimeError):
    """Base error for registry load/query failures."""


class RegistryLoadError(RegistryError):
    """The registry file is missing, malformed, or fails validation."""


class CapabilityRegistry:
    def __init__(self, path: str | Path = DEFAULT_REGISTRY_PATH) -> None:
        self.path = Path(path)
        self._resources: list[CapabilityResource] | None = None

    def load(self) -> list[CapabilityResource]:
        """Load and validate the registry file once; cache the typed view."""
        if self._resources is not None:
            return self._resources
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise RegistryLoadError(f"capability registry not found: {self.path}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RegistryLoadError(f"capability registry is not valid JSON: {self.path}") from exc
        if not isinstance(data, dict) or "resources" not in data:
            raise RegistryLoadError(
                f"capability registry is missing the 'resources' key: {self.path}"
            )
        try:
            self._resources = [
                CapabilityResource.model_validate(item) for item in data["resources"]
            ]
        except ValidationError as exc:
            raise RegistryLoadError(
                f"capability registry failed schema validation: {self.path}"
            ) from exc
        return self._resources

    def resources(self) -> list[CapabilityResource]:
        return self.load()

    def get(self, resource_id: str) -> CapabilityResource | None:
        return next((r for r in self.resources() if r.id == resource_id), None)

    @staticmethod
    def capability_aliases(capability: str) -> tuple[str, ...]:
        return CAPABILITY_ALIASES.get(capability, (capability,))

    def find(self, capability: str) -> list[CapabilityResource]:
        """Resources that satisfy a canonical requirement capability."""
        aliases = set(self.capability_aliases(capability))
        found: list[CapabilityResource] = []
        for resource in self.resources():
            labels = set(resource.declared_capabilities) | set(resource.validated_capabilities)
            if labels & aliases:
                found.append(resource)
        return found

    def matched_capability(self, resource: CapabilityResource, capability: str) -> str | None:
        aliases = set(self.capability_aliases(capability))
        labels = set(resource.declared_capabilities) | set(resource.validated_capabilities)
        for label in sorted(labels & aliases):
            return label
        return None

    def validation_level(self, resource: CapabilityResource, capability: str) -> ValidationLevel:
        """Evidence level of a specific capability on a specific resource.

        A capability is VALIDATED only when the specific registry label that
        actually matched the requirement (see :meth:`matched_capability`) is
        explicitly marked true in ``validated_capabilities``. The level is
        never inherited from a *different* validated alias: a resource whose
        overall ``status`` is VALIDATED but that only *declares* the matched
        capability (e.g. ``filesystem-analysis`` declared while only
        ``routing`` is validated) is reported DECLARED, keeping
        DECLARED != ROUTABLE != EXECUTABLE != VALIDATED honest.

        Otherwise the resource's overall ``status`` is used (AVAILABLE ->
        OBSERVED, VALIDATED but not this capability -> DECLARED,
        FOUND_NOT_TESTED stays FOUND_NOT_TESTED).
        """
        matched = self.matched_capability(resource, capability)
        if matched is not None and resource.validated_capabilities.get(matched):
            return "VALIDATED"
        status = resource.status.upper()
        if status == "AVAILABLE":
            return "OBSERVED"
        if status == "VALIDATED":
            return "DECLARED"
        if status == "FOUND_NOT_TESTED":
            return "FOUND_NOT_TESTED"
        return "DECLARED"


_default_registry = CapabilityRegistry()


def get_registry() -> CapabilityRegistry:
    """Module-level default registry (repo docs/local_capability_registry.json)."""
    return _default_registry
