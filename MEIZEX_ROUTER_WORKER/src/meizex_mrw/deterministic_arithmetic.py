"""Deterministic arithmetic executor.

Closes a real gap found in a live stress test (2026-09-05): request_policy
already classifies "quanto é 47 * 89?" as RequestClass.ARITHMETIC with
ModelDirectAnswer.FORBIDDEN — the model is not supposed to answer this —
but nothing ever implemented that promise. The classification was only
consulted for the MUTATING write-gate check, so arithmetic requests fell
through to task_classifier's unrelated "general_chat" path and were
answered by the LLM anyway. Confirmed wrong twice in the same stress
test: 47*89 -> 4193 (should be 4183), 123*456 -> 46,778 (should be
56,088) — a wrong-but-well-formed answer that the structural Verifier
has no way to catch, since it never checks numeric correctness.

This module is the deterministic side of that promise: extract a real
embedded expression from the request text and evaluate it with Python's
own ast module (never `eval`), so the same input always yields the same
output and no LLM is ever in the loop for a computable expression.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass

# A contiguous run of digits/operators/parens/dots/spaces, must start and
# end on a digit or closing paren so "2+2.txt" style filenames (a bare
# substring, not a real expression) never qualify — mirrors the
# discipline already documented in request_policy/classifier.py for the
# same false-positive risk.
_EXPRESSION_PATTERN = re.compile(r"[0-9(][0-9\s.+\-*/×÷^()]*[0-9)]")
_HAS_OPERATOR = re.compile(r"[+\-*/×÷^]")

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class ExpressionError(ValueError):
    """Raised when text that looks arithmetic cannot be safely evaluated."""


@dataclass(frozen=True)
class ArithmeticResult:
    expression: str
    value: float
    formatted: str
    """Human-readable answer text, e.g. '47 * 89 = 4183'."""


def extract_expression(text: str) -> str | None:
    """Find the longest embedded arithmetic expression in free text, or
    None if there isn't one. Requires at least one operator — a bare
    number ("42") is not an expression to compute, it's just a number."""
    best: str | None = None
    for match in _EXPRESSION_PATTERN.finditer(text):
        candidate = match.group(0).strip()
        if not _HAS_OPERATOR.search(candidate):
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    return best


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        try:
            return _ALLOWED_BINOPS[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise ExpressionError("division by zero") from exc
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_safe_eval(node.operand))
    raise ExpressionError(f"unsupported expression element: {ast.dump(node)}")


def evaluate_expression(expr: str) -> float:
    """Safely evaluate a plain arithmetic expression (+-*/, parens, unary
    +/-). Never uses eval()/exec() — walks a restricted AST so only
    numeric literals and the four basic operators are reachable, no
    attribute access, no calls, no names."""
    try:
        # '^' means exponentiation in this module's input phrasing ("2^10"),
        # never Python's bitwise XOR -- translated before parsing so the
        # restricted AST walk below only ever sees real Python syntax
        # (ast.Pow), not a XOR node that would need its own allow-listing.
        # '×'/'÷' (Portuguese multiplication/division signs) are likewise
        # translated to Python's own '*'/'/' before parsing.
        normalized = expr.replace("^", "**").replace("×", "*").replace("÷", "/")
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"not a valid expression: {expr!r}") from exc
    return _safe_eval(tree.body)


def _format_value(value: float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        # Category T-1 (2026-09-05 expanded questionnaire, "casos
        # traiçoeiros"): 0.1 + 0.2 is mathematically 0.3, but Python's own
        # binary float arithmetic gives 0.30000000000000004 (IEEE-754
        # representation, not a computation error). str(value) leaked that
        # representation artifact as if it were the answer. Rounding to 10
        # decimal places reports the mathematically intended value without
        # losing precision that matters for any realistic word-problem
        # input, then trailing zeros are stripped.
        rounded = round(value, 10)
        if rounded == int(rounded):
            return str(int(rounded))
        return f"{rounded:.10f}".rstrip("0").rstrip(".")
    return str(value)


_THOUSANDS_GROUPED_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")


def _num(text: str) -> float:
    """Parse a number that may use Brazilian formatting: ',' as the decimal
    separator (1.275,50 -> 1275.50), or '.' as a thousands separator with
    no decimal part at all (1.275 -> 1275, not 1.275) -- ambiguous in
    general, but a dot followed by exactly one or more groups of 3 digits
    and nothing else is never a plausible decimal fraction in this
    module's inputs (a real decimal like "2.5" never has exactly 3
    digits after the dot in a currency/quantity context)."""
    if "," in text:
        return float(text.replace(".", "").replace(",", "."))
    if _THOUSANDS_GROUPED_RE.match(text):
        return float(text.replace(".", ""))
    return float(text)


# Word-problem patterns from the user's questionnaire (2026-09-05,
# category A) that a bare expression-extractor cannot reach: a percentage
# of a value, a price after a discount, splitting a sum of money, an
# average, and a remainder. Each is tried before the general expression
# path below -- narrow, phrasing-specific regexes, same discipline as
# deterministic_counting.py's cascade, not a general word-problem solver.
_PERCENT_OF_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*%\s*de\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE
)
_DISCOUNT_RE = re.compile(
    r"custa\s+r?\$?\s*(\d+(?:[.,]\d+)?)\b.*?desconto\s+de\s+(\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE | re.DOTALL,
)
_SPLIT_MONEY_RE = re.compile(
    r"divid[ae]\s+r?\$?\s*([\d.,]+)\s+igualmente\s+entre\s+(\d+)\s+pessoas?",
    re.IGNORECASE,
)
_AVERAGE_RE = re.compile(r"m[ée]dia\s+de\s+(.+)", re.IGNORECASE)
_REMAINDER_RE = re.compile(
    r"resto\s+da\s+divis[ãa]o\s+de\s+(\d+(?:[.,]\d+)?)\s+por\s+(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)


def _compute_word_problem(text: str) -> ArithmeticResult | None:
    match = _DISCOUNT_RE.search(text)
    if match:
        price, pct = _num(match.group(1)), _num(match.group(2))
        final = price * (1 - pct / 100)
        return ArithmeticResult(
            expression=match.group(0),
            value=final,
            formatted=f"Preço final: R$ {_format_value(final)}",
        )

    match = _SPLIT_MONEY_RE.search(text)
    if match:
        total, n = _num(match.group(1)), int(match.group(2))
        share = total / n
        return ArithmeticResult(
            expression=match.group(0),
            value=share,
            formatted=f"R$ {_format_value(share)} para cada pessoa",
        )

    match = _REMAINDER_RE.search(text)
    if match:
        a, b = _num(match.group(1)), _num(match.group(2))
        remainder = a % b
        return ArithmeticResult(
            expression=match.group(0),
            value=remainder,
            formatted=f"O resto é {_format_value(remainder)}",
        )

    match = _AVERAGE_RE.search(text)
    if match:
        numbers = [_num(n) for n in re.findall(r"\d+(?:[.,]\d+)?", match.group(1))]
        if numbers:
            average = sum(numbers) / len(numbers)
            return ArithmeticResult(
                expression=match.group(0),
                value=average,
                formatted=f"A média é {_format_value(average)}",
            )

    match = _PERCENT_OF_RE.search(text)
    if match:
        pct, base = _num(match.group(1)), _num(match.group(2))
        value = pct / 100 * base
        return ArithmeticResult(
            expression=match.group(0),
            value=value,
            formatted=f"{_format_value(pct)}% de {_format_value(base)} = {_format_value(value)}",
        )

    return None


def compute(text: str) -> ArithmeticResult | None:
    """Extract and evaluate an embedded arithmetic expression from
    ``text``. Returns None (never raises) when there is nothing safely
    computable — callers fall through to the normal LLM path in that
    case, same as before this module existed."""
    word_problem = _compute_word_problem(text)
    if word_problem is not None:
        return word_problem

    expr = extract_expression(text)
    if expr is None:
        return None
    try:
        value = evaluate_expression(expr)
    except ExpressionError:
        return None
    formatted_value = _format_value(value)
    return ArithmeticResult(
        expression=expr,
        value=value,
        formatted=f"{expr.strip()} = {formatted_value}",
    )
