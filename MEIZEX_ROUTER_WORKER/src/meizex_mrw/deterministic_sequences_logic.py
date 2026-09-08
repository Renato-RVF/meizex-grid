"""Deterministic sequence-analysis and boolean-logic executor.

Same rationale as the other deterministic executors (Achados 53/56/57):
"ordene estes números", "qual o próximo número na sequência", and
"verdadeiro e falso?" each have exactly one mechanically correct
answer -- sorting/min/max/arithmetic-or-geometric-progression, and
boolean AND/OR/NOT/XOR evaluation, none of which require model
reasoning.

Covers the patterns from the user's questionnaire categories E
(sequências) and F (lógica booleana): sort a list of numbers
(ascending/descending), find the max/min of a list, find the next term
of an arithmetic or geometric progression, and evaluate a boolean
expression built from "verdadeiro"/"falso" combined with
e/ou/não/xor/ou exclusivo. Each extractor is a narrow regex or a
restricted-grammar check tied to the questionnaire's actual phrasing --
this is not a general NLU parser or a full logic solver, and returns
None (falls through to the LLM path unchanged) for anything it does not
confidently recognize (e.g. a sequence with no constant difference or
ratio, or a boolean expression mixing XOR with AND/OR chains).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


class SequenceLogicError(ValueError):
    """Raised internally when a matched pattern's payload cannot be parsed."""


@dataclass(frozen=True)
class SequenceLogicResult:
    operation: str
    formatted: str
    """Human-readable answer text, e.g. 'O próximo número é 10.'"""


def _parse_numbers(text: str) -> list[float]:
    values = []
    for raw in _NUMBER_RE.findall(text):
        values.append(float(raw.replace(",", ".")))
    return values


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    rounded = round(value, 4)
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def _format_list(values: list[float]) -> str:
    return ", ".join(_format_number(v) for v in values)


# --- Sequence analysis -------------------------------------------------

_SORT_RE = re.compile(
    r"ordene\s+(?:os\s+)?n[uú]meros?[:\s]+(.+)",
    re.IGNORECASE | re.DOTALL,
)
_MAX_RE = re.compile(
    r"qual\s+(?:[ée]\s+)?o\s+maior\s+n[uú]mero\s+entre\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)
_MIN_RE = re.compile(
    r"qual\s+(?:[ée]\s+)?o\s+menor\s+n[uú]mero\s+entre\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)
_NEXT_SEQUENCE_RE = re.compile(
    r"qual\s+(?:[ée]\s+)?o\s+pr[oó]ximo\s+n[uú]mero\s+na\s+sequ[êe]ncia[:\s]+(.+)",
    re.IGNORECASE | re.DOTALL,
)
# Category T-2 (2026-09-05, "casos traiçoeiros"): "qual é maior: 9.11 ou
# 9.9?" is the classic trap where a model reads the digits as if they
# were software-version tokens (9.11 > 9.9) instead of comparing the
# actual numeric values (9.9 > 9.11). Compared as real floats, never
# lexicographically.
_COMPARE_TWO_RE = re.compile(
    r"qual\s+[ée]\s+maior[:\s,]+(-?\d+(?:[.,]\d+)?)\s+ou\s+(-?\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
# Category T-5: "quantos números há na lista 1, 01, 1.0, -1, +1?" -- the
# trap is treating this as "how many DISTINCT values" (1, 01, 1.0, and +1
# are all mathematically 1) instead of "how many ENTRIES does the list
# have" (5, the actual question asked).
_LIST_ENTRY_COUNT_RE = re.compile(
    r"quantos\s+n[uú]meros\s+h[áa]\s+na\s+lista[:\s]+(.+)", re.IGNORECASE
)


def _next_term(values: list[float]) -> tuple[str, float] | None:
    if len(values) < 2:
        return None

    # Intentional pairwise zip over values and values[1:] -- these two
    # slices always differ in length by exactly one (that is what makes
    # this pairwise), so strict=True (which requires equal lengths) does
    # not apply here.
    diffs = [round(b - a, 9) for a, b in zip(values, values[1:])]  # noqa: B905
    if all(d == diffs[0] for d in diffs):
        return "arithmetic", values[-1] + diffs[0]

    if all(a != 0 for a in values[:-1]):
        ratios = [round(b / a, 9) for a, b in zip(values, values[1:])]  # noqa: B905
        if all(r == ratios[0] for r in ratios):
            return "geometric", values[-1] * ratios[0]

    return None


def _compute_sequence(text: str) -> SequenceLogicResult | None:
    match = _SORT_RE.search(text)
    if match:
        values = _parse_numbers(match.group(1))
        if not values:
            return None
        descending = bool(re.search(r"decrescente", text, re.IGNORECASE))
        ordered = sorted(values, reverse=descending)
        return SequenceLogicResult(
            operation="sort_descending" if descending else "sort_ascending",
            formatted=f"{_format_list(ordered)}",
        )

    match = _MAX_RE.search(text)
    if match:
        values = _parse_numbers(match.group(1))
        if not values:
            return None
        return SequenceLogicResult(
            operation="max",
            formatted=f"O maior número é {_format_number(max(values))}.",
        )

    match = _MIN_RE.search(text)
    if match:
        values = _parse_numbers(match.group(1))
        if not values:
            return None
        return SequenceLogicResult(
            operation="min",
            formatted=f"O menor número é {_format_number(min(values))}.",
        )

    match = _NEXT_SEQUENCE_RE.search(text)
    if match:
        values = _parse_numbers(match.group(1))
        term = _next_term(values)
        if term is None:
            return None
        _kind, next_value = term
        return SequenceLogicResult(
            operation="next_term",
            formatted=f"O próximo número é {_format_number(next_value)}.",
        )

    match = _COMPARE_TWO_RE.search(text)
    if match:
        a = float(match.group(1).replace(",", "."))
        b = float(match.group(2).replace(",", "."))
        larger = max(a, b)
        return SequenceLogicResult(
            operation="compare_two",
            formatted=f"{_format_number(larger)} é maior.",
        )

    match = _LIST_ENTRY_COUNT_RE.search(text)
    if match:
        count = _count_list_entries(match.group(1))
        if count == 0:
            return None
        return SequenceLogicResult(
            operation="list_entry_count",
            formatted=f"A lista tem {count} entradas numéricas.",
        )

    return None


def _count_list_entries(text: str) -> int:
    stripped = text.strip().rstrip("?.")
    normalized = re.sub(r"\s+e\s+", ", ", stripped, flags=re.IGNORECASE)
    items = [item.strip() for item in normalized.split(",") if item.strip()]
    return len(items)


# --- Boolean logic -------------------------------------------------------

_XOR_RE = re.compile(
    r"^(verdadeiro|falso)\s+(?:xor|ou\s+exclusivo)\s+(verdadeiro|falso)$",
    re.IGNORECASE,
)

_BOOL_TOKEN_MAP = {
    "verdadeiro": "True",
    "verdade": "True",
    "falso": "False",
    "não": "not",
    "nao": "not",
    "e": "and",
    "ou": "or",
}
_BOOL_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)
_BOOL_ALLOWED_LEFTOVER_RE = re.compile(r"^[\s()]*$")


def _strip_trailing_punctuation(text: str) -> str:
    return text.strip().rstrip(".?!")


def _to_python_bool_expr(lowered: str) -> str | None:
    """Translate verdadeiro/falso/e/ou/não into a Python boolean expression,
    refusing (returns None) if any unrecognized word remains -- this is
    the same discipline as deterministic_arithmetic's restricted AST
    walk: never guess, never silently drop a token the module does not
    actually understand (e.g. 'xor', handled separately above)."""
    leftover = _BOOL_WORD_RE.sub(
        lambda m: "" if m.group(0) in _BOOL_TOKEN_MAP else m.group(0), lowered
    )
    if not _BOOL_ALLOWED_LEFTOVER_RE.match(leftover):
        return None
    return _BOOL_WORD_RE.sub(lambda m: _BOOL_TOKEN_MAP.get(m.group(0), m.group(0)), lowered)


def _safe_eval_bool(node: ast.AST) -> bool:
    if isinstance(node, ast.Expression):
        return _safe_eval_bool(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BoolOp):
        values = [_safe_eval_bool(v) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _safe_eval_bool(node.operand)
    raise SequenceLogicError(f"unsupported boolean expression node: {ast.dump(node)}")


def _compute_boolean(text: str) -> SequenceLogicResult | None:
    stripped = _strip_trailing_punctuation(text)
    lowered = stripped.lower()

    xor_match = _XOR_RE.match(lowered)
    if xor_match:
        left = xor_match.group(1) == "verdadeiro"
        right = xor_match.group(2) == "verdadeiro"
        value = left ^ right
        return SequenceLogicResult(
            operation="xor",
            formatted="Verdadeiro." if value else "Falso.",
        )

    if "xor" in lowered or "exclusivo" in lowered:
        return None

    py_expr = _to_python_bool_expr(lowered)
    if py_expr is None:
        return None

    try:
        tree = ast.parse(py_expr, mode="eval")
        value = _safe_eval_bool(tree)
    except (SyntaxError, SequenceLogicError):
        return None

    return SequenceLogicResult(
        operation="boolean_expression",
        formatted="Verdadeiro." if value else "Falso.",
    )


def compute(text: str) -> SequenceLogicResult | None:
    """Recognize and answer one of the sequence/boolean patterns above.
    Returns None (never raises) for anything not confidently recognized
    -- callers fall through to the normal LLM path in that case."""
    result = _compute_sequence(text)
    if result is not None:
        return result

    return _compute_boolean(text)
