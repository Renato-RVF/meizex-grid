"""Deterministic workload-partitioning contracts and evaluation.

This module represents plans only. It performs no decomposition, execution,
scheduling, routing, provider call, or merge operation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .execution_profiles import (
    CloudPolicy,
    DataClassification,
    ExecutionProfile,
    ProfileEligibility,
    evaluate_profile_eligibility,
)


class Partitionability(str, Enum):
    SAFE = "SAFE"
    SAFE_WITH_DEPENDENCIES = "SAFE_WITH_DEPENDENCIES"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"


class MergeStrategy(str, Enum):
    DETERMINISTIC_CONCAT = "DETERMINISTIC_CONCAT"
    STRUCTURED_MERGE = "STRUCTURED_MERGE"
    REQUIRES_SYNTHESIS = "REQUIRES_SYNTHESIS"


class PartitionPlanStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"


class WorkloadShard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str
    objective: str
    required_inputs: list[str] = Field(default_factory=list)
    required_evidence_ids: list[int] = Field(default_factory=list)
    required_source_ids: list[int] = Field(default_factory=list)
    estimated_input_tokens: int = Field(ge=0)
    estimated_output_tokens: int = Field(ge=0)
    dependencies: list[str] = Field(default_factory=list)
    can_execute_independently: bool = True
    candidate_profile_ids: list[str] = Field(default_factory=list)
    data_classification: DataClassification = DataClassification.PUBLIC
    cloud_allowed: CloudPolicy = CloudPolicy.NO

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "WorkloadShard":
        self.shard_id = self.shard_id.strip()
        self.objective = self.objective.strip()
        if not self.shard_id or not self.objective:
            raise ValueError("shard_id and objective must not be empty")
        for name in (
            "required_inputs", "required_evidence_ids", "required_source_ids",
            "dependencies", "candidate_profile_ids",
        ):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        self.dependencies = sorted(self.dependencies)
        self.required_evidence_ids = sorted(self.required_evidence_ids)
        self.required_source_ids = sorted(self.required_source_ids)
        self.candidate_profile_ids = sorted(self.candidate_profile_ids)
        return self


class CrossShardContradiction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contradiction_id: str
    side_a_evidence_id: int
    side_b_evidence_id: int
    reconciliation_shard_id: str | None = None


class WorkloadPartitionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    task_label: str
    partition_strategy: str
    shards: list[WorkloadShard] = Field(min_length=1)
    merge_strategy: MergeStrategy
    global_context_requirements: list[str] = Field(default_factory=list)
    cross_shard_dependencies: list[CrossShardContradiction] = Field(default_factory=list)
    evidence_source_map: dict[int, int] = Field(default_factory=dict)
    provenance_required: bool = False
    metadata_complete: bool = True
    status: PartitionPlanStatus = PartitionPlanStatus.DRAFT

    @model_validator(mode="after")
    def validate_dependency_graph(self) -> "WorkloadPartitionPlan":
        shard_ids = [shard.shard_id for shard in self.shards]
        if len(shard_ids) != len(set(shard_ids)):
            raise ValueError("duplicate shard ID")
        known = set(shard_ids)
        graph: dict[str, list[str]] = {}
        for shard in self.shards:
            if shard.shard_id in shard.dependencies:
                raise ValueError(f"self dependency: {shard.shard_id}")
            missing = sorted(set(shard.dependencies) - known)
            if missing:
                raise ValueError(f"missing dependency: {missing[0]}")
            graph[shard.shard_id] = shard.dependencies

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(shard_id: str) -> None:
            if shard_id in visiting:
                raise ValueError("dependency cycle")
            if shard_id in visited:
                return
            visiting.add(shard_id)
            for dependency in graph[shard_id]:
                visit(dependency)
            visiting.remove(shard_id)
            visited.add(shard_id)

        for shard_id in sorted(known):
            visit(shard_id)
        self.shards = sorted(self.shards, key=lambda shard: shard.shard_id)
        self.global_context_requirements = sorted(set(self.global_context_requirements))
        self.cross_shard_dependencies = sorted(
            self.cross_shard_dependencies, key=lambda item: item.contradiction_id
        )
        return self


class ShardContextEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str
    requirement_tokens: int
    profile_evaluations: list[ProfileEligibility]


class PartitionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partitionability: Partitionability
    shard_count: int
    all_dependencies_resolve: bool
    total_task_input_tokens: int
    total_distributed_input_tokens: int
    unsplit_requirement_tokens: int
    aggregate_requirement_tokens: int
    max_active_requirement_tokens: int
    active_context_reduction: bool
    aggregate_token_increase: bool
    final_synthesis_required: bool
    all_candidate_shards_fit: bool | None
    shard_evaluations: list[ShardContextEvaluation]
    reasons: list[str]


def evaluate_partition_plan(
    plan: WorkloadPartitionPlan,
    *,
    profiles: list[ExecutionProfile],
    total_task_input_tokens: int,
    fixed_prompt_overhead_tokens: int,
    safety_margin_tokens: int,
    unsplit_requested_output_tokens: int,
    network_available: bool = False,
) -> PartitionEvaluation:
    """Evaluate a caller-supplied plan without creating or executing shards."""
    numeric = (
        total_task_input_tokens, fixed_prompt_overhead_tokens,
        safety_margin_tokens, unsplit_requested_output_tokens,
    )
    if any(value < 0 for value in numeric):
        raise ValueError("token values must be non-negative")
    profiles_by_id = {profile.profile_id: profile for profile in profiles}
    if len(profiles_by_id) != len(profiles):
        raise ValueError("duplicate profile ID")

    reasons: set[str] = set()
    evidence_to_shard: dict[int, str] = {}
    shards_by_id = {shard.shard_id: shard for shard in plan.shards}
    for shard in plan.shards:
        for evidence_id in shard.required_evidence_ids:
            if evidence_id in evidence_to_shard:
                reasons.add(f"EVIDENCE_ASSIGNED_TO_MULTIPLE_SHARDS:{evidence_id}")
            evidence_to_shard[evidence_id] = shard.shard_id
            if plan.provenance_required:
                source_id = plan.evidence_source_map.get(evidence_id)
                if source_id is None:
                    reasons.add(f"EVIDENCE_SOURCE_MAPPING_MISSING:{evidence_id}")
                elif source_id not in shard.required_source_ids:
                    reasons.add(f"PROVENANCE_ATOMICITY_VIOLATION:{evidence_id}:{source_id}")

    dependency_shape = any(shard.dependencies for shard in plan.shards)
    for contradiction in plan.cross_shard_dependencies:
        side_a = evidence_to_shard.get(contradiction.side_a_evidence_id)
        side_b = evidence_to_shard.get(contradiction.side_b_evidence_id)
        if side_a is None or side_b is None:
            reasons.add(f"CONTRADICTION_EVIDENCE_MISSING:{contradiction.contradiction_id}")
            continue
        if side_a == side_b:
            continue
        reconciliation_id = contradiction.reconciliation_shard_id
        reconciliation = shards_by_id.get(reconciliation_id or "")
        if reconciliation is None:
            reasons.add(f"CROSS_SHARD_CONTRADICTION_UNRECONCILED:{contradiction.contradiction_id}")
            continue
        if not {side_a, side_b}.issubset(set(reconciliation.dependencies)):
            reasons.add(f"CONTRADICTION_RECONCILIATION_DEPENDENCIES_MISSING:{contradiction.contradiction_id}")
        else:
            dependency_shape = True

    shard_evaluations: list[ShardContextEvaluation] = []
    all_fit_values: list[bool] = []
    aggregate_requirement = 0
    max_requirement = 0
    for shard in plan.shards:
        requirement = (
            shard.estimated_input_tokens + fixed_prompt_overhead_tokens
            + shard.estimated_output_tokens + safety_margin_tokens
        )
        aggregate_requirement += requirement
        max_requirement = max(max_requirement, requirement)
        evaluations: list[ProfileEligibility] = []
        for profile_id in shard.candidate_profile_ids:
            profile = profiles_by_id.get(profile_id)
            if profile is None:
                reasons.add(f"CANDIDATE_PROFILE_MISSING:{shard.shard_id}:{profile_id}")
                continue
            evaluation = evaluate_profile_eligibility(
                profile,
                required_input_tokens=shard.estimated_input_tokens + fixed_prompt_overhead_tokens,
                requested_output_tokens=shard.estimated_output_tokens,
                safety_margin_tokens=safety_margin_tokens,
                data_classification=shard.data_classification,
                cloud_allowed=shard.cloud_allowed,
                network_available=network_available,
            )
            evaluations.append(evaluation)
        if shard.candidate_profile_ids:
            all_fit_values.append(any(item.eligible for item in evaluations))
        shard_evaluations.append(ShardContextEvaluation(
            shard_id=shard.shard_id,
            requirement_tokens=requirement,
            profile_evaluations=evaluations,
        ))

    unsafe_prefixes = (
        "EVIDENCE_ASSIGNED_TO_MULTIPLE_SHARDS:",
        "EVIDENCE_SOURCE_MAPPING_MISSING:",
        "PROVENANCE_ATOMICITY_VIOLATION:",
        "CONTRADICTION_EVIDENCE_MISSING:",
        "CROSS_SHARD_CONTRADICTION_UNRECONCILED:",
        "CONTRADICTION_RECONCILIATION_DEPENDENCIES_MISSING:",
    )
    if not plan.metadata_complete:
        partitionability = Partitionability.UNKNOWN
        reasons.add("PARTITION_METADATA_INSUFFICIENT")
    elif any(reason.startswith(unsafe_prefixes) for reason in reasons):
        partitionability = Partitionability.UNSAFE
    elif dependency_shape or plan.global_context_requirements:
        partitionability = Partitionability.SAFE_WITH_DEPENDENCIES
    else:
        partitionability = Partitionability.SAFE

    final_synthesis_required = plan.merge_strategy == MergeStrategy.REQUIRES_SYNTHESIS
    unsplit_requirement = (
        total_task_input_tokens + fixed_prompt_overhead_tokens
        + unsplit_requested_output_tokens + safety_margin_tokens
    )
    return PartitionEvaluation(
        partitionability=partitionability,
        shard_count=len(plan.shards),
        all_dependencies_resolve=True,
        total_task_input_tokens=total_task_input_tokens,
        total_distributed_input_tokens=sum(s.estimated_input_tokens for s in plan.shards),
        unsplit_requirement_tokens=unsplit_requirement,
        aggregate_requirement_tokens=aggregate_requirement,
        max_active_requirement_tokens=max_requirement,
        active_context_reduction=max_requirement < unsplit_requirement,
        aggregate_token_increase=aggregate_requirement > unsplit_requirement,
        final_synthesis_required=final_synthesis_required,
        all_candidate_shards_fit=all(all_fit_values) if all_fit_values else None,
        shard_evaluations=shard_evaluations,
        reasons=sorted(reasons),
    )
