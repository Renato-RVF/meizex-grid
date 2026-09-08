"""Deterministic date-arithmetic and unit-conversion executor.

Same rationale as deterministic_arithmetic.py (Achado 53) and
deterministic_counting.py (Achado 56): "quantos dias há entre X e Y",
"que dia da semana é Z", and "quantos km são N milhas" each have exactly
one mechanically correct answer -- computable with datetime arithmetic
or a fixed conversion factor, no model reasoning required. Left to an
LLM, date math and unit conversion are exactly the kind of "sounds
plausible, is quietly wrong" answers this project exists to catch.

Covers the patterns from the user's questionnaire categories C (datas)
and D (unidades): difference in days between two dates, date plus/minus
N days, day of the week for a given date, and conversions between the
metric/imperial pairs km<->milhas, kg<->libras, m<->pés, L<->galões, and
Celsius<->Fahrenheit. Each extractor is a narrow regex tied to the
Portuguese phrasing actually used in the questionnaire plus a couple of
natural variants -- this is not a general NLU parser or a full unit
library, and returns None (falls through to the LLM path unchanged) for
anything it does not confidently recognize.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_WEEKDAYS_PT = (
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
)


class DateUnitError(ValueError):
    """Raised internally when a matched date string is not a valid calendar date."""


@dataclass(frozen=True)
class DateUnitResult:
    operation: str
    formatted: str
    """Human-readable answer text, e.g. '10/01/2026 é uma sábado.'"""


_DATE_TOKEN = r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})"

_DAYS_BETWEEN_RE = re.compile(
    rf"quantos\s+dias\s+(?:h[áa]|existem)\s+entre\s+{_DATE_TOKEN}\s+e\s+{_DATE_TOKEN}",
    re.IGNORECASE,
)
_WEEKDAY_RE = re.compile(
    rf"que\s+dia\s+da\s+semana\s+(?:[ée]|cai)\s+(?:o\s+dia\s+)?{_DATE_TOKEN}",
    re.IGNORECASE,
)
_DATE_PLUS_DAYS_RE = re.compile(
    rf"que\s+data\s+(?:[ée]|fica)\s+(\d+)\s+dias?\s+depois\s+de\s+{_DATE_TOKEN}",
    re.IGNORECASE,
)
_DATE_MINUS_DAYS_RE = re.compile(
    rf"que\s+data\s+(?:[ée]|fica)\s+(\d+)\s+dias?\s+antes\s+de\s+{_DATE_TOKEN}",
    re.IGNORECASE,
)

_NUMBER = r"(\d+(?:[.,]\d+)?)"


def _num(text: str) -> float:
    return float(text.replace(",", "."))


# (regex, factor-or-callable, target-unit-label)
_UNIT_CONVERSIONS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(rf"quant[oa]s?\s+km\s+s[ãa]o\s+{_NUMBER}\s+milhas?", re.IGNORECASE),
        "mi_to_km", "km",
    ),
    (
        re.compile(rf"quant[oa]s?\s+milhas?\s+s[ãa]o\s+{_NUMBER}\s+km", re.IGNORECASE),
        "km_to_mi", "milhas",
    ),
    (
        re.compile(rf"quant[oa]s?\s+kg\s+s[ãa]o\s+{_NUMBER}\s+libras?", re.IGNORECASE),
        "lb_to_kg", "kg",
    ),
    (
        re.compile(rf"quant[oa]s?\s+libras?\s+s[ãa]o\s+{_NUMBER}\s+kg", re.IGNORECASE),
        "kg_to_lb", "libras",
    ),
    (
        re.compile(rf"quant[oa]s?\s+metros?\s+s[ãa]o\s+{_NUMBER}\s+p[ée]s", re.IGNORECASE),
        "ft_to_m", "metros",
    ),
    (
        re.compile(rf"quant[oa]s?\s+p[ée]s\s+s[ãa]o\s+{_NUMBER}\s+metros?", re.IGNORECASE),
        "m_to_ft", "pés",
    ),
    (
        re.compile(rf"quant[oa]s?\s+litros?\s+s[ãa]o\s+{_NUMBER}\s+gal[õo]es", re.IGNORECASE),
        "gal_to_l", "litros",
    ),
    (
        re.compile(rf"quant[oa]s?\s+gal[õo]es\s+s[ãa]o\s+{_NUMBER}\s+litros?", re.IGNORECASE),
        "l_to_gal", "galões",
    ),
    (
        re.compile(
            rf"quant[oa]s?\s+graus\s+fahrenheit\s+s[ãa]o\s+{_NUMBER}\s+graus\s+celsius",
            re.IGNORECASE,
        ),
        "c_to_f", "°F",
    ),
    (
        re.compile(
            rf"quant[oa]s?\s+graus\s+celsius\s+s[ãa]o\s+{_NUMBER}\s+graus\s+fahrenheit",
            re.IGNORECASE,
        ),
        "f_to_c", "°C",
    ),
)

_CONVERSION_FUNCS = {
    "mi_to_km": lambda v: v * 1.60934,
    "km_to_mi": lambda v: v / 1.60934,
    "lb_to_kg": lambda v: v * 0.453592,
    "kg_to_lb": lambda v: v / 0.453592,
    "ft_to_m": lambda v: v * 0.3048,
    "m_to_ft": lambda v: v / 0.3048,
    "gal_to_l": lambda v: v * 3.78541,
    "l_to_gal": lambda v: v / 3.78541,
    "c_to_f": lambda v: v * 9 / 5 + 32,
    "f_to_c": lambda v: (v - 32) * 5 / 9,
}


def _to_date(day: str, month: str, year: str) -> date:
    try:
        return date(int(year), int(month), int(day))
    except ValueError as exc:
        raise DateUnitError(str(exc)) from exc


def _format_number(value: float) -> str:
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def compute(text: str) -> DateUnitResult | None:
    """Recognize and answer one of the date/unit patterns above. Returns
    None (never raises) for anything not confidently recognized --
    callers fall through to the normal LLM path in that case."""
    match = _DAYS_BETWEEN_RE.search(text)
    if match:
        d1, m1, y1, d2, m2, y2 = match.groups()
        try:
            date_a = _to_date(d1, m1, y1)
            date_b = _to_date(d2, m2, y2)
        except DateUnitError:
            return None
        days = abs((date_b - date_a).days)
        return DateUnitResult(
            operation="days_between",
            formatted=f"Há {days} dias entre {d1}/{m1}/{y1} e {d2}/{m2}/{y2}.",
        )

    match = _WEEKDAY_RE.search(text)
    if match:
        d, m, y = match.groups()
        try:
            target = _to_date(d, m, y)
        except DateUnitError:
            return None
        weekday_name = _WEEKDAYS_PT[target.weekday()]
        return DateUnitResult(
            operation="weekday",
            formatted=f"{d}/{m}/{y} cai em uma {weekday_name}.",
        )

    match = _DATE_PLUS_DAYS_RE.search(text)
    if match:
        n, d, m, y = match.groups()
        try:
            base = _to_date(d, m, y)
        except DateUnitError:
            return None
        result_date = base + timedelta(days=int(n))
        return DateUnitResult(
            operation="date_plus_days",
            formatted=f"{result_date.day:02d}/{result_date.month:02d}/{result_date.year}",
        )

    match = _DATE_MINUS_DAYS_RE.search(text)
    if match:
        n, d, m, y = match.groups()
        try:
            base = _to_date(d, m, y)
        except DateUnitError:
            return None
        result_date = base - timedelta(days=int(n))
        return DateUnitResult(
            operation="date_minus_days",
            formatted=f"{result_date.day:02d}/{result_date.month:02d}/{result_date.year}",
        )

    for pattern, conversion_key, target_label in _UNIT_CONVERSIONS:
        match = pattern.search(text)
        if match:
            value = _num(match.group(1))
            converted = _CONVERSION_FUNCS[conversion_key](value)
            return DateUnitResult(
                operation=conversion_key,
                formatted=f"{_format_number(value)} equivale(m) a "
                f"{_format_number(converted)} {target_label}.",
            )

    return None
