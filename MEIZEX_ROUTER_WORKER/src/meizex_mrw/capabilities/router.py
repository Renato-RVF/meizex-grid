"""Capability Router MVP — the routing pipeline itself.

Transforms the MRW from ``TASK -> PROFILE -> MODEL`` into:

    TASK
    -> CAPABILITY REQUIREMENTS
    -> CAPABILITY REGISTRY
    -> CANDIDATE RESOURCES
    -> POLICY / HARDWARE / VALIDATION FILTER
    -> RANK
    -> EXECUTION PLAN

Pure selection: nothing executes, no provider is invoked, no LLM is called.
A resource can be excluded with an auditable reason:
* capability incompatible
* hardware incompatible (not modeled yet — reserved)
* provider unavailable
* policy
* cloud forbidden
* input modality incompatible
* validation requirement not met

Availability (M5.1): an unavailable resource is NOT dropped before ranking.
It stays in the candidate pool flagged ``available=False`` so the router can
distinguish:

* **best known** — the highest-ranked candidate by capability/policy/evidence
  (what we would prefer if everything were online);
* **best available** — the highest-ranked candidate whose runtime is actually
  present (what we can actually execute).

If best-known != best-available the route is explicitly ``DEGRADED`` with a
machine-readable ``degradation_reason``. A validation policy of
``VALIDATED_REQUIRED`` fails closed (``NO_ELIGIBLE_RESOURCE``) rather than
silently selecting an unvalidated fallback. Cloud is never selected as a
consequence of a validation failure.
"""

from __future__ import annotations

from meizex_mrw.capabilities import ranking
from meizex_mrw.capabilities.executor_compat import executor_compatible
from meizex_mrw.capabilities.models import (
    CandidateResource,
    CapabilityRequirement,
    CapabilityRouteResult,
    ExecutionStep,
    ResourceExclusion,
)
from meizex_mrw.capabilities.registry import (
    CapabilityRegistry,
    get_registry,
)
from meizex_mrw.capabilities.requirements import requirements as derive_requirements
from meizex_mrw.profiles.schema import ExecutionProfile

# Machine-readable degradation reasons.
DEGRADATION_VALIDATED_UNAVAILABLE = "validated_candidate_unavailable"
DEGRADATION_BEST_KNOWN_UNAVAILABLE = "best_known_candidate_unavailable"


def _resolve_candidates(
    registry: CapabilityRegistry,
    requirement: CapabilityRequirement,
    profile: ExecutionProfile | None = None,
) -> tuple[list[CandidateResource], list[ResourceExclusion]]:
    candidates: list[CandidateResource] = []
    exclusions: list[ResourceExclusion] = []

    # Combine requirement minimum validation level with profile's
    policy = requirement.minimum_validation_level
    if profile and profile.minimum_validation_level:
        # Strict validation always wins. (e.g. VALIDATED_REQUIRED over OBSERVED_OR_BETTER)
        if (
            profile.minimum_validation_level == "VALIDATED_REQUIRED"
            or policy == "FOUND_NOT_TESTED_ALLOWED"
        ):
            # For simplicity in this logic MVP, profile overrides if requirement is weaker.
            if policy != "VALIDATED_REQUIRED":
                policy = profile.minimum_validation_level  # type: ignore

    for resource in registry.find(requirement.capability):
        exclusion: str | None = None
        validation = registry.validation_level(resource, requirement.capability)

        # Cloud is forbidden by default in this milestone (MRW stays
        # local-first). Only a requirement that explicitly allows cloud may
        # see a cloud-required resource as a candidate.
        cloud_allowed = requirement.cloud_allowed
        if profile:
            cloud_allowed = cloud_allowed and not profile.local_only and profile.cloud_allowed

        # Grid (a MEIZEX Grid node — trusted LAN machine, neither this
        # process nor a cloud provider) is forbidden by default too, same
        # spirit as cloud: opt-in per profile, not a silent default. See
        # MEIZEX_GRID/NEXT-011 for why this needed its own category instead
        # of overloading `local`/`cloud_required`.
        grid_allowed = bool(profile and profile.grid_allowed)

        if resource.location == "grid":
            if not grid_allowed:
                exclusion = "grid_forbidden"
        elif resource.location == "cloud":
            if not cloud_allowed:
                exclusion = "cloud_forbidden"
        elif resource.location == "local":
            pass  # explicitly local: always policy-eligible on this axis
        # location is None: legacy resource, preserve exact prior behavior.
        elif not cloud_allowed and (resource.cloud_required or not resource.local):
            exclusion = "cloud_forbidden"

        # Profile resource kind constraint
        elif (
            profile
            and profile.allowed_resource_kinds is not None
            and resource.kind not in profile.allowed_resource_kinds
        ):
            exclusion = "profile_resource_kind_forbidden"

        # Profile hardware constraints
        elif profile and profile.hardware_constraints:
            for k, v in profile.hardware_constraints.items():
                if (
                    k not in resource.hardware_requirements
                    or resource.hardware_requirements[k] != v
                ):
                    exclusion = "profile_hardware_constraint_not_met"
                    break

        # Input modality must be compatible (e.g. audio -> speech resource).
        elif (
            requirement.input_modality
            and requirement.input_modality not in resource.input_modalities
        ):
            exclusion = (
                f"input_modality_incompatible: need {requirement.input_modality!r}, "
                f"resource supports {resource.input_modalities or 'none'!r}"
            )

        # Validation policy gate: a policy that demands a minimum evidence
        # level removes anything below it. This is what makes
        # VALIDATED_REQUIRED fail closed — no unvalidated fallback survives.
        elif not ranking.meets_minimum_validation(validation, policy):
            exclusion = "validation_required_not_met"

        # Executor gate: a resource with no registered executor can never
        # become an operational step (dead step). Filtered BEFORE ranking so
        # the router never relies on a late dispatcher failure.
        elif not executor_compatible(
            resource.kind,
            resource.id,
            resource.execution_boundary,
        ):
            exclusion = "no_compatible_executor"

        if exclusion is not None:
            exclusions.append(
                ResourceExclusion(
                    resource_id=resource.id,
                    capability=requirement.capability,
                    reason=exclusion,
                )
            )
            continue

        matched = registry.matched_capability(resource, requirement.capability) or ""
        candidates.append(
            CandidateResource(
                resource_id=resource.id,
                capability=requirement.capability,
                matched_capability=matched,
                kind=resource.kind,
                validation_level=validation,
                local=resource.local,
                cloud_required=resource.cloud_required,
                runtime=resource.runtime,
                available=ranking.runtime_available(resource.runtime),
                cost_tier=ranking.cost_tier(resource.kind),
                quality_tier=ranking.quality_tier(resource.kind),
                reason=f"matched registry capability {matched!r} ({validation})",
                execution_boundary=resource.execution_boundary,
            )
        )

    return candidates, exclusions


def _degradation_reason(preferred: CandidateResource) -> str:
    if preferred.validation_level == "VALIDATED":
        return DEGRADATION_VALIDATED_UNAVAILABLE
    return DEGRADATION_BEST_KNOWN_UNAVAILABLE


def route(
    mission: str,
    *,
    registry: CapabilityRegistry | None = None,
    economy: str = "balance",
    profile: ExecutionProfile | None = None,
) -> CapabilityRouteResult:
    """Route a mission to a deterministic execution plan. Never executes.

    For every requirement the router ranks ALL policy-eligible candidates
    (available or not), identifies the best-known resource, then picks the
    best available one. A difference between the two is an explicit DEGRADED
    route. An unavailable resource is never selected for execution.
    """
    active_registry = registry or get_registry()
    mission_requirements = derive_requirements(mission)

    all_candidates: list[CandidateResource] = []
    all_exclusions: list[ResourceExclusion] = []
    steps: list[ExecutionStep] = []
    selected_resource_ids: list[str] = []
    preferred_resource_ids: list[str] = []
    degradation_reasons: list[str] = []
    has_required_unroutable = False
    has_required_no_executor = False

    for requirement in mission_requirements:
        candidates, exclusions = _resolve_candidates(active_registry, requirement, profile)
        all_candidates.extend(candidates)
        all_exclusions.extend(exclusions)

        ranked = ranking.rank(requirement, candidates, economy=economy)
        if not ranked:
            if requirement.kind == "required":
                no_executor = any(
                    e.capability == requirement.capability and e.reason == "no_compatible_executor"
                    for e in exclusions
                )
                if no_executor:
                    has_required_no_executor = True
                else:
                    has_required_unroutable = True
            continue

        # Best known: what evidence/policy/ranking prefers (availability not
        # considered). Best available: the first one whose runtime is present.
        best_known = ranked[0]
        best_available = next((c for c in ranked if c.available), None)

        if best_available is None:
            # Nothing executable; if the policy demanded evidence, this is
            # fail-closed rather than a silent fallback.
            if requirement.kind == "required":
                has_required_unroutable = True
            continue

        chosen = best_available
        degradation: str | None = None
        if chosen.resource_id != best_known.resource_id:
            degradation = _degradation_reason(best_known)
            if degradation not in degradation_reasons:
                degradation_reasons.append(degradation)

        if chosen.resource_id not in selected_resource_ids:
            selected_resource_ids.append(chosen.resource_id)
            steps.append(
                ExecutionStep(
                    capability=requirement.capability,
                    resource=chosen.resource_id,
                    kind=chosen.kind,
                    best_known_resource=best_known.resource_id,
                    degradation=degradation,
                    reason=(
                        f"rank {chosen.rank}/{len(ranked)} via {economy} ranking "
                        f"({chosen.validation_level}, available)"
                    ),
                    execution_boundary=chosen.execution_boundary,
                )
            )
            preferred_resource_ids.append(best_known.resource_id)

    llm_required = ranking.llm_required_for(steps)

    if has_required_no_executor:
        # Fail closed: a required capability could not be satisfied because
        # every candidate has no compatible executor. No dead plan is emitted —
        # an explicit early failure that the dispatcher treats like
        # NO_ELIGIBLE_RESOURCE (never a silent LLM fallback).
        steps = []
        selected_resource_ids = []
        preferred_resource_ids = []
        degradation_reasons = []
        route_status = "NO_EXECUTABLE_RESOURCE"
        reason = "no_executable_resource_within_policy"
        llm_required = ranking.llm_required_for(steps)
    elif has_required_unroutable:
        # Fail closed: a required capability could not be satisfied within
        # policy, so no plan is emitted at all — never a partial plan that
        # looks executable while a required step is missing.
        steps = []
        selected_resource_ids = []
        preferred_resource_ids = []
        degradation_reasons = []
        route_status = "NO_ELIGIBLE_RESOURCE"
        reason = "no_eligible_resource_within_policy"
        llm_required = ranking.llm_required_for(steps)
    elif degradation_reasons:
        route_status = "DEGRADED"
        reason = "deterministic capability route (degraded)"
        if llm_required:
            reason += " (LLM required)"
    elif steps:
        route_status = "NORMAL"
        reason = "deterministic capability route"
        if llm_required:
            reason += " (LLM required)"
        else:
            reason += " (no LLM required)"
    else:
        route_status = "NO_ELIGIBLE_RESOURCE"
        reason = "no_candidate_resource_within_policy"

    return CapabilityRouteResult(
        mission=mission,
        required_capabilities=[r.capability for r in mission_requirements if r.kind == "required"],
        candidates=all_candidates,
        selected_resources=selected_resource_ids,
        execution_plan=steps,
        exclusions=all_exclusions,
        llm_required=llm_required,
        cloud_allowed=any(r.cloud_allowed for r in mission_requirements),
        reason=reason,
        execution_performed=False,
        route_status=route_status,
        preferred_resources=preferred_resource_ids,
        degradation_reasons=degradation_reasons,
    )
