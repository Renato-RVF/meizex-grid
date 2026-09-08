"""Capability Routing MVP — data contracts.

The single vocabulary for the Capability Router:

* :class:`CapabilityResource` — one entry from the local capability registry
  (docs/local_capability_registry.json). Read-only view; the registry file is
  the source of truth.
* :class:`CapabilityRequirement` — what a mission needs, expressed in
  capability terms (not provider/brand terms).
* :class:`CandidateResource` — a registry resource matched to one requirement.
* :class:`ResourceExclusion` — a candidate removed and the auditable reason.
* :class:`ExecutionStep` — one planned step of a (possibly composed) plan.
* :class:`CapabilityRouteResult` — the full machine-readable routing output.

Nothing here executes anything: routing is pure selection.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ValidationLevel = Literal["VALIDATED", "OBSERVED", "DECLARED", "FOUND_NOT_TESTED"]

RequirementKind = Literal["required", "preferred"]

RouteStatus = Literal["NORMAL", "DEGRADED", "NO_ELIGIBLE_RESOURCE", "NO_EXECUTABLE_RESOURCE"]

# Execution boundary contract (M9): whether a capability resource must run
# out-of-process. IN_PROCESS = allowed to run inside the MRW orchestrator
# process (pure deterministic logic, parsing, routing, validation, policy).
# PROCESS = the executable capability's execution contract requires an
# explicit process boundary; it must NOT run inside the MRW process.
ExecutionBoundary = Literal["IN_PROCESS", "PROCESS"]

MinimumValidationLevel = Literal[
    "FOUND_NOT_TESTED_ALLOWED",
    "OBSERVED_OR_BETTER",
    "VALIDATED_REQUIRED",
]


class CapabilityResource(BaseModel):
    """One resource entry from the capability registry (typed read view)."""

    id: str
    kind: str
    runtime: str = ""
    artifact: str = ""
    declared_capabilities: list[str] = Field(default_factory=list)
    validated_capabilities: dict[str, bool] = Field(default_factory=dict)
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    local: bool = True
    cloud_required: bool = False
    tool_use: bool = False
    context_length: int | None = None
    quantization: str | None = None
    hardware_requirements: dict[str, Any] = Field(default_factory=dict)
    observed_performance: dict[str, Any] = Field(default_factory=dict)
    manuals: list[str] = Field(default_factory=list)
    source: list[str] = Field(default_factory=list)
    status: str = "AVAILABLE"
    execution_boundary: ExecutionBoundary = "IN_PROCESS"


class CapabilityRequirement(BaseModel):
    capability: str
    kind: RequirementKind = "required"
    input_modality: str | None = None
    output_modality: str | None = None
    local_required: bool = True
    cloud_allowed: bool = False
    deterministic_preferred: bool = False
    validation_required: bool = False
    preferred_kind: str | None = None
    minimum_validation_level: MinimumValidationLevel = "FOUND_NOT_TESTED_ALLOWED"

    @model_validator(mode="after")
    def _sync_validation_fields(self) -> CapabilityRequirement:
        # Backwards-compatible: the legacy boolean implies the strictest policy;
        # the policy name also reflects back onto the boolean.
        if self.validation_required:
            self.minimum_validation_level = "VALIDATED_REQUIRED"
        elif self.minimum_validation_level == "VALIDATED_REQUIRED":
            self.validation_required = True
        return self


class CandidateResource(BaseModel):
    resource_id: str
    capability: str
    matched_capability: str
    kind: str
    validation_level: ValidationLevel
    local: bool
    cloud_required: bool
    runtime: str
    cost_tier: int
    quality_tier: int
    rank: int = 0
    available: bool = True
    reason: str = ""
    execution_boundary: ExecutionBoundary = "IN_PROCESS"


class ResourceExclusion(BaseModel):
    resource_id: str
    capability: str
    reason: str


class ToolInvocation(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExecutionStep(BaseModel):
    capability: str
    resource: str
    kind: str
    invocation: ToolInvocation | None = None
    best_known_resource: str | None = None
    degradation: str | None = None
    reason: str = ""
    execution_boundary: ExecutionBoundary = "IN_PROCESS"


class CapabilityRouteResult(BaseModel):
    mission: str
    required_capabilities: list[str] = Field(default_factory=list)
    candidates: list[CandidateResource] = Field(default_factory=list)
    selected_resources: list[str] = Field(default_factory=list)
    execution_plan: list[ExecutionStep] = Field(default_factory=list)
    exclusions: list[ResourceExclusion] = Field(default_factory=list)
    llm_required: bool = False
    cloud_allowed: bool = False
    reason: str = ""
    execution_performed: bool = False
    route_status: RouteStatus = "NORMAL"
    preferred_resources: list[str] = Field(default_factory=list)
    degradation_reasons: list[str] = Field(default_factory=list)
