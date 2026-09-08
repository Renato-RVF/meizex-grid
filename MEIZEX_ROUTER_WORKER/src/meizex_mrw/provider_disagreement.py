"""Cross-provider disagreement signal.

DIVERGENCE != TRUTH. Two providers disagreeing does not tell you which
one (if either) is right -- it tells you the process should not close
yet. This module is a pure comparator: given the same answer produced
by two providers (e.g. the local Chassis model and a cloud model asked
the same question), it returns AGREE/PARTIAL/DISAGREE plus a short
explanation, nothing more. It never decides what to do about a
disagreement (retry, escalate, ask a human) -- that policy belongs to
whatever calls this, not here (same separation of concerns as
request_policy: DECIDE != EXECUTE).

Rationale, from the 2026-09-05 ecosystem sweep: a Text-to-SQL study
found that verifiers from different providers make different mistakes,
and that combining two verifiers improved the ability to predict when
an answer is correct. Independently, hybrid/RouteLabs/OrchestratorLLM-
style routers already implement deterministic -> local -> frontier
escalation; the differentiator this project is aiming for is not that
ladder (it already exists elsewhere) but making the top rung a
*selective, evidence-driven auditor* rather than merely a stronger
executor -- and cross-provider disagreement is one concrete, checkable
signal for "this needs the auditor," alongside low local-model
confidence and high-consequence classification (ESCALATE_CAPABILITY /
ESCALATE_UNCERTAINTY / AUDIT_RISK -- see request_policy.models for
where that taxonomy is meant to live once the escalation ladder itself
is built; only the signal is implemented today).

NOT implemented here (explicitly out of scope for this module): calling
a second provider, constructing a "fresh escalation" prompt (see the
InflationAgent finding this project is adopting as a principle --
escalate evidence, not a failed reasoning chain), or any actual
escalation decision/execution. Those require live provider-call
infrastructure this repo does not have wired up yet.

extract_single_number()/numbers_agree() are also public, standalone
helpers (Achado 78, 2026-09-05): the numeric-normalization step
compare_answers() uses internally -- decimal-comma-vs-dot tolerant,
relative-tolerance based -- is generically useful to any caller
comparing two (or more, pairwise) numeric-ish strings that may just be
differently formatted, not actually different values. First external
consumer: PRONIL/PR_DESPESAS's Unicred cheque-extraction tool, whose own
divergence check previously compared raw strings and could flag
"150,00" vs "150.00" as a false DIVERGENCIA_ENTRE_LEITURAS.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_WORD_RE = re.compile(r"\w+", re.UNICODE)

# A numeric answer whose relative difference exceeds this fraction is
# always a DISAGREE, regardless of how similar the surrounding prose is
# -- "42" and "42.0001" are the same answer; "42" and "50" are not.
#
# Tightened from 0.01 (1%) to 0.001 (0.1%) after a real miss found while
# implementing category Q (2026-09-05 expanded questionnaire): 3847*229
# = 880963, but compare_answers("880963", "881963") -- a wrong digit in
# an exact arithmetic result -- agreed within the old 1% tolerance
# (relative diff ~0.113%). 1% was sized for rounding/formatting noise in
# unit conversions (e.g. "16.09" vs "16.0934", ~0.02%), not exact
# computation, where ANY digit-level difference should disagree. 0.1%
# still comfortably covers the unit-conversion rounding case (re-verified
# below) while catching the arithmetic-digit-error case.
_NUMERIC_RELATIVE_TOLERANCE = 0.001

# Token-overlap (Jaccard) thresholds for free-text comparison, used only
# when neither answer reduces to a single clear number. Deliberately
# coarse -- this is a triage signal, not a semantic-similarity model.
_AGREE_JACCARD_THRESHOLD = 0.6
_DISAGREE_JACCARD_THRESHOLD = 0.2


@dataclass(frozen=True)
class DisagreementResult:
    verdict: Literal["AGREE", "PARTIAL", "DISAGREE"]
    detail: str


def _canonicalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return text.casefold().strip()


def extract_single_number(text: str) -> float | None:
    """Returns the text's number only when there is exactly one -- text
    with zero or multiple numbers is not "a number" for the purposes of
    this narrow numeric-agreement check, and callers should fall through
    to a text-level comparison instead. Tolerant of Brazilian decimal-
    comma formatting ("150,00" -> 150.0)."""
    matches = _NUMBER_RE.findall(text)
    if len(matches) != 1:
        return None
    try:
        return float(matches[0].replace(",", "."))
    except ValueError:
        return None


def numbers_agree(a: float, b: float, *, tolerance: float = _NUMERIC_RELATIVE_TOLERANCE) -> bool:
    """True when two numbers are the same value up to relative
    ``tolerance`` (default 0.1%, sized for formatting/rounding noise --
    see the module-level tolerance constant's own history for why 1% was
    too loose). Never true for a genuine digit-level difference."""
    if a == b:
        return True
    denominator = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denominator <= tolerance


def _jaccard(a: str, b: str) -> float:
    words_a = set(_WORD_RE.findall(a))
    words_b = set(_WORD_RE.findall(b))
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def compare_answers(
    answer_a: str,
    answer_b: str,
    *,
    provider_a: str = "provider_a",
    provider_b: str = "provider_b",
) -> DisagreementResult:
    """Compare two providers' answers to the same question. Pure function:
    same two strings always yield the same verdict. Never raises."""
    canon_a, canon_b = _canonicalize(answer_a or ""), _canonicalize(answer_b or "")

    if canon_a == canon_b:
        return DisagreementResult(
            verdict="AGREE", detail=f"{provider_a} and {provider_b} produced identical answers"
        )

    num_a, num_b = extract_single_number(canon_a), extract_single_number(canon_b)
    if num_a is not None and num_b is not None:
        if numbers_agree(num_a, num_b):
            return DisagreementResult(
                verdict="AGREE",
                detail=f"{provider_a}={num_a!r} and {provider_b}={num_b!r} agree within tolerance",
            )
        return DisagreementResult(
            verdict="DISAGREE",
            detail=f"{provider_a}={num_a!r} and {provider_b}={num_b!r} disagree numerically",
        )

    overlap = _jaccard(canon_a, canon_b)
    if overlap >= _AGREE_JACCARD_THRESHOLD:
        return DisagreementResult(
            verdict="AGREE",
            detail=f"{provider_a} and {provider_b} answers overlap {overlap:.2f} (>= threshold)",
        )
    if overlap <= _DISAGREE_JACCARD_THRESHOLD:
        return DisagreementResult(
            verdict="DISAGREE",
            detail=f"{provider_a} and {provider_b} answers overlap only {overlap:.2f}",
        )
    return DisagreementResult(
        verdict="PARTIAL",
        detail=f"{provider_a} and {provider_b} answers overlap {overlap:.2f} (inconclusive)",
    )


def should_audit(result: DisagreementResult) -> bool:
    """Convenience predicate: DISAGREE and PARTIAL both warrant sending the
    case to an auditor (AUDIT_RISK / ESCALATE_UNCERTAINTY); only a clean
    AGREE does not. This is the only "decision" this module makes, and
    it is intentionally coarse -- callers with more context (consequence
    level, cost budget) may choose to ignore PARTIAL."""
    return result.verdict != "AGREE"
