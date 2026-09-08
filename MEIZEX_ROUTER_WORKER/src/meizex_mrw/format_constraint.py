"""Deterministic output-format compliance checker.

Category H of the 2026-09-05 expanded questionnaire ("Instruções com
restrição de formato"): the Verifier should check the FORM of an answer,
not just whether tool evidence supports its content -- "Responda apenas
com SIM: '2 + 2 = 4?'" answered as "Sim, porque 2+2=4." is semantically
correct but a format violation (the request said "only SIM", nothing
else).

This module never judges whether an answer is factually correct -- same
discipline as verifier.py itself ("checks deterministic rules only,
never semantic/numeric correctness"). It only checks whether a request's
own explicit format instruction (a single word, a bare number, valid
JSON with named keys, an exact list length) was mechanically honored.

Unlike verifier.py, this needs the ORIGINAL REQUEST text (to know what
format was actually asked for) -- verify_synthesis()'s signature was
deliberately NOT changed to add that (Achado 66/67 chose a pre-flight
engine.py gate over widening the Verifier's contract for the same
reason). check_format_constraint() follows that same pattern: called
directly from worker/engine.py, which already has the request text,
rather than threading it through the Verifier.

extract_constraint()/check_format_constraint() both return None (never
raise) when the request's phrasing doesn't match one of the narrow
patterns below -- this is not a general instruction-following grader.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_EXACT_WORD_ANSWER_RE = re.compile(
    r"responda\s+apenas\s+com\s+['\"]?(\w+)['\"]?", re.IGNORECASE
)
_BARE_NUMBER_INTENT_RE = re.compile(
    r"retorne\s+apenas\s+o\s+n[uú]mero\s+resultante", re.IGNORECASE
)
_SINGLE_WORD_INTENT_RE = re.compile(
    r"responda\s+em\s+uma\s+[uú]nica\s+palavra", re.IGNORECASE
)
_JSON_KEYS_RE = re.compile(
    r"retorne\s+json\s+v[áa]lido\s+com\s+as\s+chaves\s+(\w+)\s+e\s+(\w+)", re.IGNORECASE
)
_LIST_COUNT_RE = re.compile(
    r"retorne\s+uma\s+lista\s+com\s+exatamente\s+(tr[êe]s|quatro|cinco|seis|\d+)\s+elementos",
    re.IGNORECASE,
)
_WORD_COUNT_WORDS = {"tres": 3, "três": 3, "quatro": 4, "cinco": 5, "seis": 6}

_BARE_NUMBER_ANSWER_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_SINGLE_WORD_ANSWER_RE = re.compile(r"^\S+$")


@dataclass(frozen=True)
class FormatConstraintResult:
    satisfied: bool
    detail: str
    constraint_kind: str


def _strip(text: str) -> str:
    return text.strip().strip(".").strip()


def _list_item_count(answer: str) -> int | None:
    stripped = answer.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return len(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    # Fall back to comma/semicolon/newline-separated items, e.g. "A, B e C"
    # or "A, B, C" -- strip a trailing " e X" conjunction into its own item.
    normalized = re.sub(r"\s+e\s+", ", ", stripped, flags=re.IGNORECASE)
    items = [item.strip() for item in re.split(r"[,;\n]", normalized) if item.strip()]
    return len(items) if items else None


def check_format_constraint(request: str, answer: str) -> FormatConstraintResult | None:
    """Check whether ``answer`` honors an explicit format instruction found
    in ``request``. Returns None (never raises) when the request contains
    none of the narrow, recognized format instructions -- callers should
    treat that exactly like "no constraint to check", not like a pass."""
    match = _EXACT_WORD_ANSWER_RE.search(request)
    if match:
        required = match.group(1).strip().lower()
        actual = _strip(answer).lower()
        satisfied = actual == required
        return FormatConstraintResult(
            satisfied=satisfied,
            detail=(
                f"request demands the answer be exactly {required!r} and nothing else, "
                f"but the answer was {answer.strip()!r}"
                if not satisfied
                else "exact-word answer format honored"
            ),
            constraint_kind="exact_word",
        )

    if _BARE_NUMBER_INTENT_RE.search(request):
        stripped = _strip(answer)
        satisfied = bool(_BARE_NUMBER_ANSWER_RE.match(stripped))
        return FormatConstraintResult(
            satisfied=satisfied,
            detail=(
                f"request demands a bare number and nothing else, but the answer was "
                f"{answer.strip()!r}"
                if not satisfied
                else "bare-number answer format honored"
            ),
            constraint_kind="bare_number",
        )

    if _SINGLE_WORD_INTENT_RE.search(request):
        stripped = _strip(answer)
        satisfied = bool(_SINGLE_WORD_ANSWER_RE.match(stripped))
        return FormatConstraintResult(
            satisfied=satisfied,
            detail=(
                f"request demands a single-word answer, but the answer was "
                f"{answer.strip()!r}"
                if not satisfied
                else "single-word answer format honored"
            ),
            constraint_kind="single_word",
        )

    match = _JSON_KEYS_RE.search(request)
    if match:
        required_keys = {match.group(1), match.group(2)}
        try:
            parsed = json.loads(answer.strip())
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if not isinstance(parsed, dict):
            return FormatConstraintResult(
                satisfied=False,
                detail=f"request demands valid JSON with keys {required_keys}, "
                f"but the answer is not a JSON object: {answer.strip()!r}",
                constraint_kind="json_keys",
            )
        satisfied = set(parsed.keys()) == required_keys
        return FormatConstraintResult(
            satisfied=satisfied,
            detail=(
                f"request demands JSON with exactly the keys {required_keys}, "
                f"but the answer has keys {set(parsed.keys())}"
                if not satisfied
                else "JSON key-set format honored"
            ),
            constraint_kind="json_keys",
        )

    match = _LIST_COUNT_RE.search(request)
    if match:
        count_token = match.group(1).lower()
        required_count = (
            int(count_token) if count_token.isdigit() else _WORD_COUNT_WORDS.get(count_token)
        )
        if required_count is None:
            return None
        actual_count = _list_item_count(answer)
        satisfied = actual_count == required_count
        actual_desc = actual_count if actual_count is not None else "an unparseable number of"
        return FormatConstraintResult(
            satisfied=satisfied,
            detail=(
                f"request demands a list with exactly {required_count} elements, "
                f"but the answer has {actual_desc}"
                if not satisfied
                else "list-length format honored"
            ),
            constraint_kind="list_count",
        )

    return None
