"""AIR-IDENTITY-001 — Cross-project identity and versioning contract (contract only).

This module defines the MINIMAL executable identity contract that AIR, MHL,
CHASSIS and DWO can later adopt to reference the same models, artifacts,
profiles and runs without duplication or ambiguity.

It creates NO registry, NO database, NO persistence. It only defines:

- IdentityRef (kind + id + schema_version)
- deterministic helpers that derive IDs from canonical serialization
- the identity-kind separation rules (MODEL != ARTIFACT != RUNTIME !=
  EXECUTION_PROFILE != RUN, EXPERIMENT != RUN)

Conventions (see tests/identity/IDENTITY_CONTRACT_V1.md):

- IDENTITY_SCHEMA_VERSION = "1.0"
- RUN_RECORD_SCHEMA_VERSION = "1.0"
- canonical serialization = JSON, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False, UTF-8
- content-derived hex digests are lowercase; comparison is case-insensitive
- ID prefixes: model:, sha256:, runtime:, profile:, hardware:, formula:,
  bundle:sha256:, experiment:, run:
- content IDs NEVER include timestamps, memory addresses, or absolute local
  paths unless a path is explicitly part of the identity being derived.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDENTITY_SCHEMA_VERSION = "1.0"
RUN_RECORD_SCHEMA_VERSION = "1.0"

_HEX_DIGEST_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class IdentityKind(str, Enum):
    MODEL = "MODEL"
    ARTIFACT = "ARTIFACT"
    RUNTIME = "RUNTIME"
    EXECUTION_PROFILE = "EXECUTION_PROFILE"
    HARDWARE_SNAPSHOT = "HARDWARE_SNAPSHOT"
    FORMULA = "FORMULA"
    EVIDENCE_BUNDLE = "EVIDENCE_BUNDLE"
    EXPERIMENT = "EXPERIMENT"
    RUN = "RUN"


class IdentityRef(BaseModel):
    """A typed, versioned reference to one identity kind.

    `id` is the stable identity string. It is NOT a primary key and is NOT
    globally unique across kinds — the (kind, id) pair is what must be used
    wherever cross-project references are recorded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: IdentityKind
    id: str = Field(min_length=1, max_length=128)
    schema_version: str = IDENTITY_SCHEMA_VERSION

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("id must not be empty")
        return clean


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> Any:
    """Recursively normalize a value for deterministic hashing.

    - Enums (pydantic or stdlib) become their `.value`.
    - Mapping keys are kept; canonical JSON sorts them later.
    - bytes are kept verbatim (caller decides raw vs normalized content).
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="json"))
    return value


def canonical_json(value: Any) -> str:
    """Deterministic JSON serialization (stable keys, no whitespace, UTF-8)."""
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_digest(value: Any) -> str:
    """Lowercase hex SHA-256 of the canonical serialization of `value`.

    If `value` is bytes, hashes the raw bytes directly (raw-content mode);
    otherwise hashes canonical JSON (normalized-content mode).
    """
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_digest(digest: str) -> str:
    """Validate and lowercase a 64-char hex digest (sha256 form)."""
    clean = digest.strip().lower()
    if not _HEX_DIGEST_RE.match(clean):
        raise ValueError(f"invalid sha256 digest: {digest!r}")
    return clean


def make_ref(kind: IdentityKind | str, id_: str) -> IdentityRef:
    return IdentityRef(kind=IdentityKind(kind), id=id_)


# ---------------------------------------------------------------------------
# MODEL identity — logical/ontological identity of the model
# ---------------------------------------------------------------------------

def model_identity(
    *,
    provider: str,
    family: str,
    model: str,
    revision: Optional[str] = None,
) -> IdentityRef:
    """MODEL identity from provider/family/model/revision.

    Intentionally EXCLUDES quantization, local path, runtime, context and GPU
    settings — those belong to ARTIFACT / EXECUTION_PROFILE / RUNTIME
    identities.
    """
    definition = {
        "provider": provider,
        "family": family,
        "model": model,
        "revision": revision,
    }
    return make_ref(IdentityKind.MODEL, f"model:{sha256_digest(definition)}")


# ---------------------------------------------------------------------------
# ARTIFACT identity — content-addressable when a trusted digest exists
# ---------------------------------------------------------------------------

ARTIFACT_ID_PREFIX = "sha256:"


def is_canonical_artifact_id(id_: str) -> bool:
    """True only for a canonical content-addressed artifact id.

    Fails closed: legacy/provisional locator ids (e.g. `art_<...>`) and any
    string that only resembles a sha256 are NOT canonical.
    """
    if not id_.startswith(ARTIFACT_ID_PREFIX):
        return False
    digest = id_[len(ARTIFACT_ID_PREFIX):]
    return bool(_HEX_DIGEST_RE.match(digest))


def artifact_identity(digest: str) -> IdentityRef:
    """ARTIFACT identity from a content digest: `sha256:<hex>`.

    Path is locator metadata, never part of the identity.
    """
    return make_ref(IdentityKind.ARTIFACT, f"{ARTIFACT_ID_PREFIX}{normalize_digest(digest)}")


# ---------------------------------------------------------------------------
# RUNTIME identity
# ---------------------------------------------------------------------------

def runtime_identity(*, name: str, version: Optional[str] = None) -> IdentityRef:
    definition = {"name": name, "version": version}
    return make_ref(IdentityKind.RUNTIME, f"runtime:{sha256_digest(definition)}")


# ---------------------------------------------------------------------------
# EXECUTION_PROFILE identity — deterministic from operational configuration
# ---------------------------------------------------------------------------

# Documented operational fields that participate in profile identity.
_PROFILE_IDENTITY_FIELDS = (
    "execution_mode",
    "provider",
    "model",
    "artifact",
    "runtime",
    "context_capacity_tokens",
    "max_output_tokens",
    "network_required",
    "cloud_required",
    "data_classification_allowed",
    "quota_type",
    "has_gpu",
    "gpu_offload_layers",
    "temperature",
    "top_p",
    "seed",
)


def execution_profile_identity(config: Mapping[str, Any]) -> IdentityRef:
    """EXECUTION_PROFILE identity from its operational configuration.

    - Only the documented operational fields participate; execution RESULTS
      never participate (PROFILE != RUN).
    - CPU-only rule preserved: when has_gpu is False, gpu_offload_layers is
      normalized to 0 BEFORE hashing, so a CPU-only profile keeps a stable
      identity regardless of a stray non-zero requested offload.
    - Paths are excluded unless explicitly provided as an operational field.
    """
    picked: dict[str, Any] = {field: config.get(field) for field in _PROFILE_IDENTITY_FIELDS}
    has_gpu = picked.get("has_gpu")
    if has_gpu is False:
        picked["gpu_offload_layers"] = 0
    return make_ref(
        IdentityKind.EXECUTION_PROFILE,
        f"profile:{sha256_digest(picked)}",
    )


# ---------------------------------------------------------------------------
# HARDWARE_SNAPSHOT identity — observable host state semantics
# ---------------------------------------------------------------------------

def hardware_snapshot_identity(snapshot: Mapping[str, Any]) -> IdentityRef:
    """HARDWARE_SNAPSHOT identity from observable host state.

    Only structurally-relevant fields participate: CPU, RAM, GPU availability,
    VRAM and OS/runtime-relevant data. Volatile counters (usage %, process
    pressure) are omitted so the identity is stable across observations of the
    same machine configuration.
    """
    picked = {
        key: snapshot.get(key)
        for key in ("cpu", "ram_bytes", "gpu_available", "vram_bytes", "os", "os_version")
    }
    return make_ref(
        IdentityKind.HARDWARE_SNAPSHOT,
        f"hardware:{sha256_digest(picked)}",
    )


# ---------------------------------------------------------------------------
# FORMULA / prompt identity — content sensitive
# ---------------------------------------------------------------------------

def formula_identity(text: str) -> IdentityRef:
    """FORMULA identity from the (normalized) formula text content.

    Any change in formula text changes the identity. Raw bytes variant:
    formula_identity_from_bytes.
    """
    return make_ref(IdentityKind.FORMULA, f"formula:{sha256_digest(text)}")


def formula_identity_from_bytes(data: bytes) -> IdentityRef:
    return make_ref(IdentityKind.FORMULA, f"formula:{sha256_digest(data)}")


# ---------------------------------------------------------------------------
# EVIDENCE_BUNDLE identity — content-addressable for frozen bundles
# ---------------------------------------------------------------------------

def evidence_bundle_identity(digest: str) -> IdentityRef:
    """EVIDENCE_BUNDLE identity from the frozen bundle content digest."""
    return make_ref(
        IdentityKind.EVIDENCE_BUNDLE,
        f"bundle:sha256:{normalize_digest(digest)}",
    )


# ---------------------------------------------------------------------------
# EXPERIMENT vs RUN identity
# ---------------------------------------------------------------------------

def experiment_identity(definition: Mapping[str, Any]) -> IdentityRef:
    """EXPERIMENT identity from its definition/intention content.

    An experiment can have many runs; RUN identity is separate. A bare stable
    experiment name (historical IDs) can be supplied as
    {"name": "AIR-WORKFLOW-003-DWO-BRIDGE-002C"} — the derived identity is
    stable as long as the name is unchanged.
    """
    return make_ref(IdentityKind.EXPERIMENT, f"experiment:{sha256_digest(definition)}")


def make_run_id(
    *,
    experiment: Optional[str] = None,
    execution_profile: Optional[str] = None,
    hardware_snapshot: Optional[str] = None,
    formula: Optional[str] = None,
    evidence_bundle: Optional[str] = None,
    nonce: Optional[str] = None,
) -> IdentityRef:
    """RUN identity referencing the identities it executed under.

    Pass identity *ids* (or full IdentityRef ids) as strings. A `nonce`
    (timestamp, uuid, counter) makes the run unique across executions; the
    same input set without a nonce is deterministic.
    """
    record = {
        "experiment": experiment,
        "execution_profile": execution_profile,
        "hardware_snapshot": hardware_snapshot,
        "formula": formula,
        "evidence_bundle": evidence_bundle,
        "nonce": nonce,
    }
    return make_ref(IdentityKind.RUN, f"run:{sha256_digest(record)}")


def run_identity_from_record(record: Mapping[str, Any]) -> IdentityRef:
    """RUN identity from a full run record.

    Timestamps are allowed here ONLY as the `nonce` element — they never
    participate in content-derived identities of other kinds.
    """
    allowed = {
        key: record.get(key)
        for key in (
            "experiment",
            "execution_profile",
            "hardware_snapshot",
            "formula",
            "evidence_bundle",
            "nonce",
        )
    }
    return make_ref(IdentityKind.RUN, f"run:{sha256_digest(allowed)}")


def ref_id(ref: IdentityRef | str) -> str:
    """Extract the identity id from an IdentityRef or accept a bare id string."""
    return ref.id if isinstance(ref, IdentityRef) else ref