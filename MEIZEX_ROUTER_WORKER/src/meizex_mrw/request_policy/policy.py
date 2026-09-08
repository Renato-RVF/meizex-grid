"""Pure policy table: RequestClass -> (CapabilityUsePolicy, ModelDirectAnswer, reasons, group hint).

Ported from MOL (meizex_orchestrator_lite/request_policy/policy.py),
unchanged except the import path.

CAPABILITY PREEMPTION: a deterministic capability class (ARITHMETIC,
FILESYSTEM, CURRENT_STATE) forbids a direct model answer outright - model
confidence cannot override this rule. No I/O, no LLM, no MCP connection,
no GUI, no global mutable state.
"""

from __future__ import annotations

from meizex_mrw.request_policy.classifier import (
    classify_filesystem_operation,
    classify_request_class,
)
from meizex_mrw.request_policy.models import (
    CapabilityUsePolicy,
    ClassificationSource,
    ModelDirectAnswer,
    PolicyReasonCode,
    RequestClass,
    RequestEffectIntent,
    RequestPolicyDecision,
    ToolSemanticGroup,
)

# request_class -> default REQUEST EFFECT INTENT for classes where it is
# fixed regardless of text (FILESYSTEM is computed per-request instead -
# see _filesystem_effect_intent).
_DEFAULT_EFFECT_INTENT: dict[RequestClass, RequestEffectIntent] = {
    RequestClass.ARITHMETIC: RequestEffectIntent.NON_TOOL,
    RequestClass.TEXT_COUNTING: RequestEffectIntent.NON_TOOL,
    RequestClass.DATE_COMPUTATION: RequestEffectIntent.NON_TOOL,
    RequestClass.UNIT_CONVERSION: RequestEffectIntent.NON_TOOL,
    RequestClass.SEQUENCE_ANALYSIS: RequestEffectIntent.NON_TOOL,
    RequestClass.BOOLEAN_LOGIC: RequestEffectIntent.NON_TOOL,
    RequestClass.CODE_TRACE: RequestEffectIntent.NON_TOOL,
    RequestClass.CURRENT_STATE: RequestEffectIntent.READ_ONLY,
    RequestClass.CREATIVE_GENERATION: RequestEffectIntent.NON_TOOL,
    RequestClass.TEXT_TRANSFORMATION: RequestEffectIntent.NON_TOOL,
    RequestClass.UNKNOWN: RequestEffectIntent.UNKNOWN,
}

_FILESYSTEM_MUTATING_REASONS = frozenset({
    PolicyReasonCode.FILESYSTEM_MUTATION_DETECTED,
    PolicyReasonCode.FILESYSTEM_MULTIPLE_MUTATION_KINDS_DETECTED,
    PolicyReasonCode.FILESYSTEM_DIRECTORY_CREATE_DETECTED,
})
_FILESYSTEM_READ_ONLY_REASONS = frozenset({
    PolicyReasonCode.FILESYSTEM_COMPOUND_LATEST_CONTENT_DETECTED,
    PolicyReasonCode.FILESYSTEM_DIRECTORY_OBSERVATION_DETECTED,
    PolicyReasonCode.FILESYSTEM_DOCUMENT_READ_DETECTED,
})


def _filesystem_effect_intent(
    operation_reasons: tuple[PolicyReasonCode, ...],
) -> RequestEffectIntent:
    reasons = set(operation_reasons)
    if reasons & _FILESYSTEM_MUTATING_REASONS:
        return RequestEffectIntent.MUTATING
    if reasons & _FILESYSTEM_READ_ONLY_REASONS:
        return RequestEffectIntent.READ_ONLY
    return RequestEffectIntent.UNKNOWN


# request_class -> (capability_use_policy, model_direct_answer, extra_reason_codes,
# required_group_hint)
_PolicyTableEntry = tuple[
    CapabilityUsePolicy, ModelDirectAnswer, tuple[PolicyReasonCode, ...], ToolSemanticGroup | None
]
_POLICY_TABLE: dict[RequestClass, _PolicyTableEntry] = {
    RequestClass.ARITHMETIC: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        None,
    ),
    RequestClass.TEXT_COUNTING: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        None,
    ),
    RequestClass.DATE_COMPUTATION: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        None,
    ),
    RequestClass.UNIT_CONVERSION: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        None,
    ),
    RequestClass.SEQUENCE_ANALYSIS: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        None,
    ),
    RequestClass.BOOLEAN_LOGIC: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        None,
    ),
    RequestClass.CODE_TRACE: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        None,
    ),
    RequestClass.FILESYSTEM: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        ToolSemanticGroup.FILESYSTEM,
    ),
    RequestClass.CURRENT_STATE: (
        CapabilityUsePolicy.DETERMINISTIC_REQUIRED,
        ModelDirectAnswer.FORBIDDEN,
        (PolicyReasonCode.MODEL_USE_NOT_REQUIRED,),
        None,
    ),
    RequestClass.CREATIVE_GENERATION: (
        CapabilityUsePolicy.MODEL_ALLOWED,
        ModelDirectAnswer.ALLOWED,
        (PolicyReasonCode.MODEL_USE_PERMITTED,),
        None,
    ),
    RequestClass.TEXT_TRANSFORMATION: (
        CapabilityUsePolicy.MODEL_ALLOWED,
        ModelDirectAnswer.ALLOWED,
        (PolicyReasonCode.MODEL_USE_PERMITTED,),
        None,
    ),
    RequestClass.UNKNOWN: (
        CapabilityUsePolicy.CLARIFICATION_REQUIRED,
        ModelDirectAnswer.UNRESOLVED,
        (PolicyReasonCode.CLARIFICATION_NEEDED,),
        None,
    ),
}


def decide_policy(text: str) -> RequestPolicyDecision:
    """Pure function: same text always yields the same decision. This is a
    policy route, not a task result - see module docstrings in models.py."""
    request_class, classifier_reasons = classify_request_class(text)
    capability_use_policy, model_direct_answer, policy_reasons, required_group = _POLICY_TABLE[
        request_class
    ]

    if request_class == RequestClass.FILESYSTEM:
        operation_reasons = classify_filesystem_operation(text or "")
        effect_intent = _filesystem_effect_intent(operation_reasons)
        reason_codes = classifier_reasons + policy_reasons + operation_reasons
    else:
        effect_intent = _DEFAULT_EFFECT_INTENT[request_class]
        reason_codes = classifier_reasons + policy_reasons

    return RequestPolicyDecision(
        request_class=request_class,
        capability_use_policy=capability_use_policy,
        model_direct_answer=model_direct_answer,
        required_semantic_group=required_group,
        reason_codes=reason_codes,
        classification_source=ClassificationSource.DETERMINISTIC_RULES,
        effect_intent=effect_intent,
    )
