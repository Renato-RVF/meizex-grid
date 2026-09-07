"""Experimental deterministic Validation Contract v2.

This module is intentionally not integrated with the production BRIDGE validator.
Semantic decisions are explicit adjudication inputs, never inferred by an LLM.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SemanticSupport(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARAPHRASE_SUPPORTED = "PARAPHRASE_SUPPORTED"
    OVER_INFERENCE = "OVER_INFERENCE"
    CONTRADICTED_BY_EVIDENCE = "CONTRADICTED_BY_EVIDENCE"
    GENUINELY_UNSUPPORTED = "GENUINELY_UNSUPPORTED"
    UNRESOLVED = "UNRESOLVED"


class FidelityStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class ContradictionFidelityStatus(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class CitationValidityStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE_NO_CITATIONS = "NOT_EVALUABLE_NO_CITATIONS"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class ClaimAdjudicationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    claim_text: str
    semantic_support: SemanticSupport
    semantic_key: str
    candidate_evidence_ids: list[int] = Field(default_factory=list)
    candidate_source_ids: list[int] = Field(default_factory=list)
    rationale: str = ""


class GeneratedChronologyAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: FidelityStatus
    swapped_dates: int = 0
    wrong_entity_date_attributions: int = 0
    wrong_source_date_attributions: int = 0
    reversed_orderings: int = 0
    invented_dates: int = 0
    reasons: list[str] = Field(default_factory=list)


class ContradictionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ContradictionFidelityStatus
    preserves_both_sides: bool | None = None
    false_resolution: bool | None = None
    invented_causality: bool | None = None
    required_uncertainty_preserved: bool | None = None
    reasons: list[str] = Field(default_factory=list)


class CitationCoverageV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_claim_count: int
    cited_material_claim_count: int
    citation_coverage_rate: float | None


class CitationValidityV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CitationValidityStatus
    citation_count: int
    cited_evidence_ids_exist: str
    cited_source_ids_exist: str
    citation_links_resolve: str
    invalid_citations: list[tuple[int, int]] = Field(default_factory=list)


class SemanticSupportV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    counts: dict[str, int]
    semantically_supported_raw_instances: int
    real_raw_failure_instances: int
    unresolved_instances: int


class DuplicateAccountingV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_failure_instance_count: int
    unique_semantic_failure_count: int
    duplicate_failure_instance_count: int
    duplicate_semantic_keys: list[str]


class ValidationResultV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = "AIR_VALIDATION_V2_EXPERIMENTAL"
    citation_coverage: CitationCoverageV2
    citation_validity: CitationValidityV2
    semantic_support: SemanticSupportV2
    generated_chronology_fidelity: GeneratedChronologyAssessment
    contradiction_fidelity: ContradictionAssessment
    duplicate_accounting: DuplicateAccountingV2
    overall_validation_status: ValidationStatus


def material_claims(text: str) -> list[tuple[str, str]]:
    claims = [
        item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", text)
        if len(re.findall(r"\w+", item)) >= 6
    ]
    return [(f"U{index:02d}", claim) for index, claim in enumerate(claims, start=1)]


def citation_pairs(text: str) -> list[tuple[int, int]]:
    return [(int(evidence), int(source)) for evidence, source in re.findall(
        r"\[Evidence\s+(\d+)\s*;\s*Source\s+(\d+)\]", text, re.IGNORECASE
    )]


def evaluate_validation_v2(
    *,
    generated_text: str,
    bundle: dict[str, Any],
    adjudications: list[ClaimAdjudicationV2],
    chronology_assessment: GeneratedChronologyAssessment,
    contradiction_assessment: ContradictionAssessment,
) -> ValidationResultV2:
    parsed = material_claims(generated_text)
    expected = [(item.claim_id, item.claim_text) for item in adjudications]
    if parsed != expected:
        raise ValueError("adjudications must exactly cover material claims in stable order")

    claim_citations = [citation_pairs(text) for _, text in parsed]
    all_citations = [pair for pairs in claim_citations for pair in pairs]
    cited_claims = sum(bool(pairs) for pairs in claim_citations)
    coverage_rate = cited_claims / len(parsed) if parsed else None
    coverage = CitationCoverageV2(
        material_claim_count=len(parsed), cited_material_claim_count=cited_claims,
        citation_coverage_rate=coverage_rate,
    )

    evidence = {row["evidence_id"]: row for row in bundle.get("evidence", [])}
    sources = {row["source_id"]: row for row in bundle.get("sources", [])}
    invalid = [
        pair for pair in all_citations
        if pair[0] not in evidence or pair[1] not in sources
        or evidence[pair[0]].get("source_id") != pair[1]
    ]
    if not all_citations:
        validity = CitationValidityV2(
            status=CitationValidityStatus.NOT_EVALUABLE_NO_CITATIONS,
            citation_count=0,
            cited_evidence_ids_exist="NOT_EVALUABLE_NO_CITATIONS",
            cited_source_ids_exist="NOT_EVALUABLE_NO_CITATIONS",
            citation_links_resolve="NOT_EVALUABLE_NO_CITATIONS",
        )
    else:
        status = CitationValidityStatus.FAIL if invalid else CitationValidityStatus.PASS
        validity = CitationValidityV2(
            status=status, citation_count=len(all_citations),
            cited_evidence_ids_exist="NO" if any(e not in evidence for e, _ in all_citations) else "YES",
            cited_source_ids_exist="NO" if any(s not in sources for _, s in all_citations) else "YES",
            citation_links_resolve="NO" if invalid else "YES", invalid_citations=invalid,
        )

    counts = {status.value: 0 for status in SemanticSupport}
    for item in adjudications:
        counts[item.semantic_support.value] += 1
    supported_states = {SemanticSupport.SUPPORTED, SemanticSupport.PARAPHRASE_SUPPORTED}
    failure_states = {
        SemanticSupport.OVER_INFERENCE, SemanticSupport.CONTRADICTED_BY_EVIDENCE,
        SemanticSupport.GENUINELY_UNSUPPORTED,
    }
    failures = [item for item in adjudications if item.semantic_support in failure_states]
    unique_keys = sorted({item.semantic_key for item in failures})
    key_counts = {key: sum(item.semantic_key == key for item in failures) for key in unique_keys}
    duplicates = sorted(key for key, count in key_counts.items() if count > 1)
    semantic = SemanticSupportV2(
        counts=counts,
        semantically_supported_raw_instances=sum(
            item.semantic_support in supported_states for item in adjudications
        ),
        real_raw_failure_instances=len(failures),
        unresolved_instances=counts[SemanticSupport.UNRESOLVED.value],
    )
    duplicate = DuplicateAccountingV2(
        raw_failure_instance_count=len(failures),
        unique_semantic_failure_count=len(unique_keys),
        duplicate_failure_instance_count=len(failures) - len(unique_keys),
        duplicate_semantic_keys=duplicates,
    )

    if not parsed:
        overall = ValidationStatus.NOT_EVALUABLE
    elif (failures or chronology_assessment.status == FidelityStatus.FAIL
          or contradiction_assessment.status == ContradictionFidelityStatus.FAIL
          or validity.status == CitationValidityStatus.FAIL):
        overall = ValidationStatus.FAIL
    elif (semantic.unresolved_instances or coverage_rate != 1.0
          or chronology_assessment.status != FidelityStatus.PASS
          or contradiction_assessment.status != ContradictionFidelityStatus.PASS):
        overall = ValidationStatus.PARTIAL
    else:
        overall = ValidationStatus.PASS

    return ValidationResultV2(
        citation_coverage=coverage, citation_validity=validity,
        semantic_support=semantic,
        generated_chronology_fidelity=chronology_assessment,
        contradiction_fidelity=contradiction_assessment,
        duplicate_accounting=duplicate,
        overall_validation_status=overall,
    )
