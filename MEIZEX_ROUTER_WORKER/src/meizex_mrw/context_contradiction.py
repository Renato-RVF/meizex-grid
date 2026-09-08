"""Deterministic context-contradiction checker.

Category L of the 2026-09-05 expanded questionnaire ("Contradição no
contexto"): when the request's own context contains two conflicting
pieces of information about the same thing (two different temperature
readings, two people making opposite claims about the same machine), a
small model tends to arbitrarily pick one and answer as if there were no
conflict. The correct behavior is to surface the contradiction (state
both values, or say the question cannot be resolved from the given
information) -- not silently resolve it.

This is checkable WITHOUT semantic judgment for the two narrow shapes
the questionnaire actually exercises:

  1. Two numeric "measurement registers N degrees" clauses with
     different values -- a correct answer mentions BOTH values; an
     answer restating only one is a silent, arbitrary resolution.
  2. Two "<person> claims that <subject> is <state>" clauses with
     different states for the same subject -- a correct answer to a
     yes/no question about that subject hedges (acknowledges the
     conflict); a bare "sim"/"não" is a silent, arbitrary resolution.

Like format_constraint.py, this needs the ORIGINAL REQUEST text (to see
the context the answer must be consistent with), so it is called
directly from worker/engine.py rather than through verify_synthesis()
(same reasoning as Achados 67/68: widen the engine's checks, not the
Verifier's contract). Returns None (never raises) when the request does
not contain one of these two narrow contradiction shapes -- this is not
a general contradiction detector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MEASUREMENT_RE = re.compile(
    r"registra\s+(-?\d+(?:[.,]\d+)?)\s*°\s*c", re.IGNORECASE
)

_CLAIM_RE = re.compile(
    r"(\w+)\s+afirma\s+que\s+(?:a|o)\s+(\w+)\s+est[áa]\s+(\w+)", re.IGNORECASE
)

_HEDGE_MARKERS = (
    "não é possível determinar",
    "nao e possivel determinar",
    "não é possível confirmar",
    "nao e possivel confirmar",
    "não há como saber",
    "nao ha como saber",
    "impossível determinar",
    "impossivel determinar",
    "impossível confirmar",
    "impossivel confirmar",
    "divergência",
    "divergencia",
    "diverge",
    "contradiz",
    "contradição",
    "contradicao",
    "conflito",
    "depende de qual",
    "duas versões",
    "duas versoes",
    "informações conflitantes",
    "informacoes conflitantes",
)

_BARE_YES_NO_RE = re.compile(r"^(sim|n[ãa]o)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ContradictionCheckResult:
    satisfied: bool
    detail: str
    contradiction_kind: str


def _strip(text: str) -> str:
    return text.strip()


def _check_numeric_contradiction(request: str, answer: str) -> ContradictionCheckResult | None:
    values = _MEASUREMENT_RE.findall(request)
    distinct = list(dict.fromkeys(values))  # de-duplicated, order preserved
    if len(distinct) < 2:
        return None

    answer_numbers = set(re.findall(r"-?\d+(?:[.,]\d+)?", answer))
    missing = [v for v in distinct if v not in answer_numbers]
    satisfied = not missing
    return ContradictionCheckResult(
        satisfied=satisfied,
        detail=(
            f"context has {len(distinct)} conflicting measurements {distinct}, but the "
            f"answer omits {missing} -- looks like one reading was picked arbitrarily"
            if not satisfied
            else "all conflicting measurements acknowledged"
        ),
        contradiction_kind="numeric_measurement",
    )


def _check_boolean_contradiction(request: str, answer: str) -> ContradictionCheckResult | None:
    claims = _CLAIM_RE.findall(request)
    if len(claims) < 2:
        return None

    # Group claimed states by subject (e.g. "máquina") -- a contradiction
    # requires at least two different claimed states for the SAME subject.
    subjects: dict[str, set[str]] = {}
    for _speaker, subject, state in claims:
        subjects.setdefault(subject.lower(), set()).add(state.lower())
    if not any(len(states) >= 2 for states in subjects.values()):
        return None

    stripped_answer = _strip(answer)
    answer_lower = stripped_answer.lower()
    is_bare_yes_no = bool(_BARE_YES_NO_RE.match(stripped_answer))
    has_hedge = any(marker in answer_lower for marker in _HEDGE_MARKERS)

    satisfied = has_hedge or not is_bare_yes_no
    return ContradictionCheckResult(
        satisfied=satisfied,
        detail=(
            f"context has conflicting claims about the same subject, but the answer "
            f"{stripped_answer!r} states a categorical yes/no with no hedge acknowledging "
            "the conflict"
            if not satisfied
            else "conflicting claims acknowledged (hedged or non-categorical answer)"
        ),
        contradiction_kind="boolean_claim",
    )


def check_context_contradiction(request: str, answer: str) -> ContradictionCheckResult | None:
    """Check whether ``answer`` acknowledges a contradiction present in
    ``request``'s own context, for the two narrow shapes this module
    recognizes. Returns None (never raises) when neither shape is
    detected -- callers should treat that exactly like "nothing to
    check", not like a pass."""
    result = _check_numeric_contradiction(request, answer)
    if result is not None:
        return result

    return _check_boolean_contradiction(request, answer)
