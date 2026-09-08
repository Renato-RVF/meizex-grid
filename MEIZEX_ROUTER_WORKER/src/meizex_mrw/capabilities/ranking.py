"""Deterministic, evidence-aware candidate ranking for the Capability Router MVP.

The cost/quality tier ranking idea is reused (refactored, not copied) from
the MEIZEX_CHASSIS_API audit's high-value finding — see
``C:\\PROJETOS\\MEIZEX_ECOSYSTEM\\MEIZEX_CHASSIS_API\\src\\meizex_chassis_api\\router.py``
``_rank_key``: economical = cost-first, maximum = quality-first, balance =
closest to quality tier 2. Ported as a small, self-contained pure function
with no Chassis package dependency; see docs/chassis_api_reuse_audit.md
(REUSE_AS_IS -> RANK_KEY).

Ranking is 100% deterministic and auditable. No ML scoring, no learned
weights. Every tie-break is an explicit, documented criterion:
1. capability match (satisfied by construction — a candidate already matches)
2. validation level (VALIDATED > OBSERVED > DECLARED > FOUND_NOT_TESTED)
3. deterministic / specialized preference (when the requirement wants it)
4. locality (local over remote)
5. availability (runtime present over "unbound/cache-only")
6. cost tier (lower first)
7. quality tier (higher first)
8. resource id (stable final tie-break)
"""

from __future__ import annotations

from meizex_mrw.capabilities.models import (
    CandidateResource,
    CapabilityRequirement,
)
from meizex_mrw.capabilities.registry import VALIDATION_PREFERENCE

# LLM-shaped resource kinds: a mission that lands only on these needs an LLM.
LLM_KINDS = frozenset({"llm", "slm", "vlm", "api"})

# Specialized/deterministic resource kinds — preferred when the requirement
# declares deterministic_preferred=True (never fall back to an LLM silently).
SPECIALIZED_KINDS = frozenset({"detector", "parser", "tool", "embedding", "library", "mcp"})

# Kind -> cost/quality tiers. Small static table derived from the inventory
# audit's kind taxonomy (not learned, not per-model). Slm is cheap; a full
# LLM/VLM costs more but is high quality. Kept out of the models so the
# registry remains a pure evidence document.
KIND_COST_TIER = {
    "slm": 1,
    "tool": 2,
    "parser": 2,
    "detector": 2,
    "embedding": 2,
    "library": 2,
    "mcp": 2,
    "llm": 3,
    "vlm": 3,
    "api": 3,
}
KIND_QUALITY_TIER = {
    "slm": 2,
    "tool": 2,
    "parser": 2,
    "detector": 2,
    "embedding": 2,
    "library": 2,
    "mcp": 2,
    "llm": 3,
    "vlm": 3,
    "api": 3,
}


def cost_tier(kind: str) -> int:
    return KIND_COST_TIER.get(kind, 2)


def quality_tier(kind: str) -> int:
    return KIND_QUALITY_TIER.get(kind, 2)


def runtime_available(runtime: str) -> bool:
    lowered = runtime.lower()
    return not ("unbound" in lowered or "not loaded" in lowered or "cache only" in lowered)


# Validation policy thresholds -> highest validation rank still eligible.
# FOUND_NOT_TESTED_ALLOWED (default) keeps legacy behavior: every evidence
# level is policy-eligible; ranking still prefers higher evidence.
VALIDATION_POLICY_THRESHOLD = {
    "FOUND_NOT_TESTED_ALLOWED": VALIDATION_PREFERENCE["FOUND_NOT_TESTED"],
    "OBSERVED_OR_BETTER": VALIDATION_PREFERENCE["OBSERVED"],
    "VALIDATED_REQUIRED": VALIDATION_PREFERENCE["VALIDATED"],
}


def meets_minimum_validation(
    validation_level: str, policy: str = "FOUND_NOT_TESTED_ALLOWED"
) -> bool:
    """True when an evidence level satisfies the validation policy.

    ``policy`` values: ``VALIDATED_REQUIRED`` (fail closed — only proven
    resources), ``OBSERVED_OR_BETTER``, or ``FOUND_NOT_TESTED_ALLOWED``
    (legacy default — every evidence level is eligible, ranking decides).
    """
    threshold = VALIDATION_POLICY_THRESHOLD.get(policy, VALIDATION_PREFERENCE["FOUND_NOT_TESTED"])
    return VALIDATION_PREFERENCE[validation_level] <= threshold


def _rank_key(candidate: CandidateResource, economy: str) -> tuple:
    """Reuse of Chassis API router._rank_key (cost/quality tiers), adapted
    from Chassis ModelInfo to the MRW CandidateResource. Same three orders."""
    cost = candidate.cost_tier
    quality = candidate.quality_tier
    resource_id = candidate.resource_id
    if economy == "economical":
        return (cost, -quality, resource_id)
    if economy == "maximum":
        return (-quality, cost, resource_id)
    # balance: closest to quality tier 2 first
    return (abs(quality - 2), cost, resource_id)


def rank(
    requirement: CapabilityRequirement,
    candidates: list[CandidateResource],
    economy: str = "balance",
) -> list[CandidateResource]:
    """Sort candidates deterministically for a single requirement.

    Stable sort: equal keys preserve the order candidates were discovered
    (which itself follows the registry file order — also deterministic).
    """

    def key(candidate: CandidateResource) -> tuple:
        validation = VALIDATION_PREFERENCE[candidate.validation_level]
        deterministic_pref = 0 if requirement.deterministic_preferred else 1
        specialized_pref = 0 if candidate.kind in SPECIALIZED_KINDS else 1
        # A requirement that names a preferred resource kind (e.g. a detector
        # for person detection) honors it over a generic multi-capability
        # resource with the same evidence level.
        preferred_kind = (
            0
            if (requirement.preferred_kind and candidate.kind == requirement.preferred_kind)
            else 1
        )
        locality = 0 if candidate.local and not candidate.cloud_required else 1
        availability = 0 if candidate.available else 1
        return (
            validation,
            deterministic_pref,
            preferred_kind,
            specialized_pref,
            locality,
            availability,
            *_rank_key(candidate, economy),
        )

    ranked = sorted(candidates, key=key)
    for index, candidate in enumerate(ranked):
        candidate.rank = index
    return ranked


def llm_required_for(plan: list) -> bool:
    """True when the selected plan's resources are LLM-shaped (kind)."""
    return any(step.kind in LLM_KINDS for step in plan)
