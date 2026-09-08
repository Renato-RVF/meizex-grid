"""Deterministic-first request policy domain.

Ported from MOL (meizex_orchestrator_lite/request_policy/models.py) --
MRW's own task_classifier.py only produces a task_type + confidence
score and has no notion of *effect intent* (read-only vs. mutating),
which is exactly the safety-relevant distinction MOL's classifier
already had. Ported as-is rather than re-derived, same reasoning kept
in each docstring.

DETERMINISTIC FIRST. LLM ONLY WHEN JUSTIFIED. This module decides, from a
raw request string alone (no LLM, no network), whether a deterministic
capability is required before any model reasoning may be considered.

MODEL_CAN_ANSWER != MODEL_SHOULD_ANSWER. CAPABILITY_EXISTS != CAPABILITY_
MUST_BE_USED. A RequestPolicyDecision is a policy route, never a task
result: DETERMINISTIC_PATH_REQUIRED != DETERMINISTICALLY_RESOLVED (that
distinction is enforced by keeping this module free of any "resolved"
field - resolution is tracked separately, only when something actually
performs it).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ToolSemanticGroup(StrEnum):
    """Canonical vocabulary. Ported from MOL's capabilities/models.py --
    inlined here (rather than porting the whole capabilities package)
    because request_policy only ever needs the enum values, not MOL's
    broader tool-inspection machinery around it."""

    FILESYSTEM = "FILESYSTEM"
    ETL = "ETL"
    ANALYSIS = "ANALYSIS"
    WORKSPACE = "WORKSPACE"
    RENDERING = "RENDERING"
    MEDIA = "MEDIA"
    RUNS = "RUNS"
    CONTEXT = "CONTEXT"
    LEGAL = "LEGAL"
    TEXT = "TEXT"
    TABULAR = "TABULAR"
    UNKNOWN = "UNKNOWN"


class RequestClass(StrEnum):
    """Small, evidence-bounded vocabulary - not a general intent taxonomy."""

    ARITHMETIC = "ARITHMETIC"
    TEXT_COUNTING = "TEXT_COUNTING"
    DATE_COMPUTATION = "DATE_COMPUTATION"
    UNIT_CONVERSION = "UNIT_CONVERSION"
    SEQUENCE_ANALYSIS = "SEQUENCE_ANALYSIS"
    BOOLEAN_LOGIC = "BOOLEAN_LOGIC"
    CODE_TRACE = "CODE_TRACE"
    FILESYSTEM = "FILESYSTEM"
    CURRENT_STATE = "CURRENT_STATE"
    TEXT_TRANSFORMATION = "TEXT_TRANSFORMATION"
    CREATIVE_GENERATION = "CREATIVE_GENERATION"
    UNKNOWN = "UNKNOWN"


class CapabilityUsePolicy(StrEnum):
    DETERMINISTIC_REQUIRED = "DETERMINISTIC_REQUIRED"
    DETERMINISTIC_PREFERRED = "DETERMINISTIC_PREFERRED"
    MODEL_ALLOWED = "MODEL_ALLOWED"
    MODEL_REQUIRED = "MODEL_REQUIRED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


class ModelDirectAnswer(StrEnum):
    """UNKNOWN != MODEL CALL REQUIRED: an unresolved classification maps to
    UNRESOLVED, never to ALLOWED - clarification is asked for, not paid for
    with an LLM call."""

    ALLOWED = "ALLOWED"
    FORBIDDEN = "FORBIDDEN"
    UNRESOLVED = "UNRESOLVED"


class PolicyReasonCode(StrEnum):
    ARITHMETIC_DETECTED = "ARITHMETIC_DETECTED"
    FILESYSTEM_REFERENCE_DETECTED = "FILESYSTEM_REFERENCE_DETECTED"
    CURRENT_STATE_REFERENCE_DETECTED = "CURRENT_STATE_REFERENCE_DETECTED"
    DETERMINISTIC_RULE_MATCH = "DETERMINISTIC_RULE_MATCH"
    NO_DETERMINISTIC_RULE = "NO_DETERMINISTIC_RULE"
    MODEL_USE_NOT_REQUIRED = "MODEL_USE_NOT_REQUIRED"
    MODEL_USE_PERMITTED = "MODEL_USE_PERMITTED"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    # FILESYSTEM effect/operation sub-signals. REQUEST CLASS != REQUEST
    # EFFECT INTENT - these describe *what kind* of filesystem action was
    # detected, independent from the FILESYSTEM class match.
    FILESYSTEM_DIRECTORY_OBSERVATION_DETECTED = "FILESYSTEM_DIRECTORY_OBSERVATION_DETECTED"
    FILESYSTEM_DOCUMENT_READ_DETECTED = "FILESYSTEM_DOCUMENT_READ_DETECTED"
    FILESYSTEM_COMPOUND_LATEST_CONTENT_DETECTED = "FILESYSTEM_COMPOUND_LATEST_CONTENT_DETECTED"
    FILESYSTEM_MUTATION_DETECTED = "FILESYSTEM_MUTATION_DETECTED"
    FILESYSTEM_MIXED_SIGNAL_DETECTED = "FILESYSTEM_MIXED_SIGNAL_DETECTED"
    FILESYSTEM_NO_OPERATION_SIGNAL_DETECTED = "FILESYSTEM_NO_OPERATION_SIGNAL_DETECTED"
    FILESYSTEM_CREATE_DETECTED = "FILESYSTEM_CREATE_DETECTED"
    FILESYSTEM_DIRECTORY_CREATE_DETECTED = "FILESYSTEM_DIRECTORY_CREATE_DETECTED"
    FILESYSTEM_WRITE_DETECTED = "FILESYSTEM_WRITE_DETECTED"
    FILESYSTEM_DELETE_DETECTED = "FILESYSTEM_DELETE_DETECTED"
    FILESYSTEM_RENAME_DETECTED = "FILESYSTEM_RENAME_DETECTED"
    FILESYSTEM_MOVE_DETECTED = "FILESYSTEM_MOVE_DETECTED"
    FILESYSTEM_COPY_DETECTED = "FILESYSTEM_COPY_DETECTED"
    FILESYSTEM_MULTIPLE_MUTATION_KINDS_DETECTED = "FILESYSTEM_MULTIPLE_MUTATION_KINDS_DETECTED"
    ARITHMETIC_NATURAL_LANGUAGE_DETECTED = "ARITHMETIC_NATURAL_LANGUAGE_DETECTED"
    TEXT_COUNTING_DETECTED = "TEXT_COUNTING_DETECTED"
    DATE_COMPUTATION_DETECTED = "DATE_COMPUTATION_DETECTED"
    UNIT_CONVERSION_DETECTED = "UNIT_CONVERSION_DETECTED"
    SEQUENCE_ANALYSIS_DETECTED = "SEQUENCE_ANALYSIS_DETECTED"
    BOOLEAN_LOGIC_DETECTED = "BOOLEAN_LOGIC_DETECTED"
    CODE_TRACE_DETECTED = "CODE_TRACE_DETECTED"


class RequestEffectIntent(StrEnum):
    """REQUEST EFFECT INTENT != TOOL EFFECT CLASSIFICATION. This says what
    the *user* is asking for (observe vs change the world); a tool's own
    effect classification says what a *tool* may do. They are meant to be
    compared by the caller, not duplicated here.

    UNKNOWN is used both for "no signal at all" and "conflicting signals"
    (e.g. "Leia e depois apague arquivo.txt.") - both cases mean the policy
    must not guess, so both are represented the same way rather than
    inventing a fourth vocabulary member (the reason_codes on the
    RequestPolicyDecision carry that distinction: FILESYSTEM_MIXED_SIGNAL_DETECTED
    vs FILESYSTEM_NO_OPERATION_SIGNAL_DETECTED)."""

    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"
    NON_TOOL = "NON_TOOL"
    UNKNOWN = "UNKNOWN"


class EscalationReason(StrEnum):
    """Why a turn would be handed to a stronger/external layer -- three
    causes the 2026-09-05 ecosystem sweep found conflated in most
    comparable routers (hybrid, RouteLabs, OrchestratorLLM), each
    requiring a different response:

    ESCALATE_CAPABILITY: the lower layer cannot execute the task at all
    (e.g. no deterministic rule and the local model lacks a needed
    tool/skill). Escalation here is about *ability*.

    ESCALATE_UNCERTAINTY: the lower layer produced an answer, but a
    Verifier-style check (structural, or now cross-provider
    disagreement via provider_disagreement.py) found reason to doubt
    it. Escalation here is about *correctness*.

    AUDIT_RISK: the answer looks fine, but the consequence of being
    wrong is high enough that a single decision chain should not be
    trusted alone (e.g. a hospital-equipment alarm, a financial
    transfer). Escalation here is about *stakes*, independent of how
    confident any single layer was.

    Declared here as the vocabulary this project intends to use once an
    actual escalation ladder (deterministic -> local -> frontier-as-
    auditor) is built; only the disagreement SIGNAL (provider_
    disagreement.py) exists today. No engine wiring yet -- this is a
    named target, not a claim that escalation is implemented."""

    ESCALATE_CAPABILITY = "ESCALATE_CAPABILITY"
    ESCALATE_UNCERTAINTY = "ESCALATE_UNCERTAINTY"
    AUDIT_RISK = "AUDIT_RISK"


class ClassificationSource(StrEnum):
    """Only one member exists on purpose - it makes the NO LLM
    CLASSIFICATION invariant a type-level fact, not just a comment."""

    DETERMINISTIC_RULES = "DETERMINISTIC_RULES"


@dataclass(frozen=True)
class RequestPolicyDecision:
    request_class: RequestClass
    capability_use_policy: CapabilityUsePolicy
    model_direct_answer: ModelDirectAnswer
    required_semantic_group: ToolSemanticGroup | None
    reason_codes: tuple[PolicyReasonCode, ...]
    classification_source: ClassificationSource
    effect_intent: RequestEffectIntent
