"""Deterministic execution-profile contract and eligibility primitives.

This module performs no routing, provider calls, quota lookup, or fallback.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExecutionMode(str, Enum):
    LOCAL = "LOCAL"
    HYBRID = "HYBRID"
    CLOUD = "CLOUD"


class DataClassification(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class CloudPolicy(str, Enum):
    YES = "YES"
    NO = "NO"
    CONDITIONAL = "CONDITIONAL"


class QuotaType(str, Enum):
    NONE = "NONE"
    FREE = "FREE"
    LIMITED = "LIMITED"
    PAID = "PAID"
    UNKNOWN = "UNKNOWN"


class Availability(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class ContextFit(str, Enum):
    FITS = "FITS"
    DOES_NOT_FIT = "DOES_NOT_FIT"
    UNKNOWN = "UNKNOWN"


class FitStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    CONDITIONAL = "CONDITIONAL"
    UNKNOWN = "UNKNOWN"


class HardwareFit(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    execution_mode: ExecutionMode
    provider: str
    model: Optional[str] = None
    runtime: Optional[str] = None
    context_capacity_tokens: Optional[int] = Field(default=None, ge=1)
    max_output_tokens: Optional[int] = Field(default=None, ge=1)
    network_required: bool = False
    cloud_required: bool = False
    data_classification_allowed: list[DataClassification] = Field(default_factory=list)
    estimated_input_cost_per_million: Optional[float] = Field(default=None, ge=0)
    estimated_output_cost_per_million: Optional[float] = Field(default=None, ge=0)
    quota_type: QuotaType = QuotaType.UNKNOWN
    quota_available: Availability = Availability.UNKNOWN
    latency_class: Optional[str] = None
    enabled: bool = True
    has_gpu: Optional[bool] = False
    gpu_offload_layers: int = Field(default=0, ge=0)

    @field_validator("profile_id", "provider")
    @classmethod
    def nonempty_identifier(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be empty")
        return clean


class ProfileEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    execution_mode: ExecutionMode
    eligible: bool
    context_fit: ContextFit
    policy_fit: FitStatus
    quota_fit: FitStatus
    hardware_fit: HardwareFit
    gpu_offload_layers_requested: int
    gpu_offload_layers_effective: int
    gpu_configuration_corrected: bool
    total_required_tokens: Optional[int]
    reasons: list[str] = Field(default_factory=list)


class HardwarePreflight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_gpu: Optional[bool]
    gpu_offload_layers_requested: int
    gpu_offload_layers_effective: int
    gpu_configuration_corrected: bool
    hardware_fit: HardwareFit
    reasons: list[str] = Field(default_factory=list)


def evaluate_hardware_preflight(profile: ExecutionProfile) -> HardwarePreflight:
    """Apply the CPU-only GPU=0 rule without probing or optimizing hardware."""
    requested = profile.gpu_offload_layers
    if profile.has_gpu is None:
        return HardwarePreflight(
            has_gpu=None,
            gpu_offload_layers_requested=requested,
            gpu_offload_layers_effective=requested,
            gpu_configuration_corrected=False,
            hardware_fit=HardwareFit.UNKNOWN,
            reasons=["HARDWARE_CAPABILITY_UNKNOWN"],
        )
    if profile.execution_mode == ExecutionMode.LOCAL and not profile.has_gpu and requested > 0:
        return HardwarePreflight(
            has_gpu=False,
            gpu_offload_layers_requested=requested,
            gpu_offload_layers_effective=0,
            gpu_configuration_corrected=True,
            hardware_fit=HardwareFit.PASS,
            reasons=[],
        )
    return HardwarePreflight(
        has_gpu=profile.has_gpu,
        gpu_offload_layers_requested=requested,
        gpu_offload_layers_effective=requested,
        gpu_configuration_corrected=False,
        hardware_fit=HardwareFit.PASS,
        reasons=[],
    )


def evaluate_context_fit(
    context_capacity_tokens: Optional[int],
    required_input_tokens: Optional[int],
    requested_output_tokens: int,
    safety_margin_tokens: int,
) -> tuple[ContextFit, Optional[int]]:
    if requested_output_tokens < 0 or safety_margin_tokens < 0:
        raise ValueError("requested_output_tokens and safety_margin_tokens must be non-negative")
    if required_input_tokens is not None and required_input_tokens < 0:
        raise ValueError("required_input_tokens must be non-negative")
    if context_capacity_tokens is None or required_input_tokens is None:
        return ContextFit.UNKNOWN, None
    total = required_input_tokens + requested_output_tokens + safety_margin_tokens
    fit = ContextFit.FITS if total <= context_capacity_tokens else ContextFit.DOES_NOT_FIT
    return fit, total


def evaluate_profile_eligibility(
    profile: ExecutionProfile,
    *,
    required_input_tokens: Optional[int],
    requested_output_tokens: int,
    safety_margin_tokens: int,
    data_classification: DataClassification,
    cloud_allowed: CloudPolicy,
    network_available: bool,
) -> ProfileEligibility:
    """Evaluate one profile without selecting or executing it."""
    reasons: list[str] = []
    hardware = evaluate_hardware_preflight(profile)
    context_fit, total = evaluate_context_fit(
        profile.context_capacity_tokens,
        required_input_tokens,
        requested_output_tokens,
        safety_margin_tokens,
    )

    if not profile.enabled:
        reasons.append("PROFILE_DISABLED")
    reasons.extend(hardware.reasons)
    if context_fit == ContextFit.DOES_NOT_FIT:
        reasons.append("CONTEXT_CAPACITY_EXCEEDED")
    elif context_fit == ContextFit.UNKNOWN:
        reasons.append("CONTEXT_CAPACITY_UNKNOWN")
    if profile.max_output_tokens is not None and requested_output_tokens > profile.max_output_tokens:
        reasons.append("OUTPUT_CAPACITY_EXCEEDED")
    if profile.network_required and not network_available:
        reasons.append("NETWORK_REQUIRED_BUT_UNAVAILABLE")

    cloud_execution = profile.cloud_required or profile.execution_mode == ExecutionMode.CLOUD
    policy_reasons: list[str] = []
    if cloud_execution and cloud_allowed == CloudPolicy.NO:
        policy_reasons.append("CLOUD_POLICY_DENIED")
    elif cloud_execution and cloud_allowed == CloudPolicy.CONDITIONAL:
        policy_reasons.append("CLOUD_POLICY_CONDITIONAL_UNRESOLVED")
    if data_classification not in profile.data_classification_allowed:
        policy_reasons.append("DATA_CLASSIFICATION_NOT_ALLOWED")
    reasons.extend(policy_reasons)

    if policy_reasons:
        policy_fit = (
            FitStatus.CONDITIONAL
            if policy_reasons == ["CLOUD_POLICY_CONDITIONAL_UNRESOLVED"]
            else FitStatus.FAIL
        )
    else:
        policy_fit = FitStatus.PASS

    if profile.quota_type == QuotaType.NONE:
        quota_fit = FitStatus.PASS
    elif profile.quota_available == Availability.YES:
        quota_fit = FitStatus.PASS
    elif profile.quota_available == Availability.NO:
        quota_fit = FitStatus.FAIL
        reasons.append("QUOTA_UNAVAILABLE")
    else:
        quota_fit = FitStatus.UNKNOWN
        reasons.append("QUOTA_AVAILABILITY_UNKNOWN")

    return ProfileEligibility(
        profile_id=profile.profile_id,
        execution_mode=profile.execution_mode,
        eligible=not reasons,
        context_fit=context_fit,
        policy_fit=policy_fit,
        quota_fit=quota_fit,
        hardware_fit=hardware.hardware_fit,
        gpu_offload_layers_requested=hardware.gpu_offload_layers_requested,
        gpu_offload_layers_effective=hardware.gpu_offload_layers_effective,
        gpu_configuration_corrected=hardware.gpu_configuration_corrected,
        total_required_tokens=total,
        reasons=reasons,
    )


def serialize_profile_evaluation(
    profile: ExecutionProfile, evaluation: ProfileEligibility
) -> str:
    payload = {
        "profile": profile.model_dump(mode="json"),
        "evaluation": evaluation.model_dump(mode="json"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
