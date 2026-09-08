"""Deterministic text-counting executor.

Same rationale as deterministic_arithmetic.py (Achado 53): "quantas
letras tem a palavra X" is a precise, mechanically checkable question --
there is exactly one correct answer, computable with len()/count(), no
model reasoning required. Left to an LLM, a small local model got this
wrong in a real stress test ("PROGRAMACAO" -> 20 letters instead of 11).

Covers the patterns from the user's Gate B "contagem determinística"
questionnaire (2026-09-05): letter count in a word, occurrences of a
specific letter, word count in a sentence, character count with/without
spaces, vowel count, consonant count, and exact string reversal. Each
extractor is a narrow regex tied to the Portuguese phrasing actually
used in the questionnaire plus a couple of natural variants -- this is
not a general NLU parser, and returns None (falls through to the LLM
path unchanged) for anything it does not confidently recognize.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_VOWELS = set("aeiouAEIOUáéíóúÁÉÍÓÚâêîôûÂÊÎÔÛãõÃÕàÀ")


class CountingError(ValueError):
    """Raised internally when a matched pattern's target text is empty."""


@dataclass(frozen=True)
class CountingResult:
    operation: str
    formatted: str
    """Human-readable answer text, e.g. 'banana tem 6 letras.'"""


def _strip_trailing_punctuation(text: str) -> str:
    return text.strip().rstrip(".?!")


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L")


# Order matters: more specific patterns (a named letter to count) must be
# tried before the generic "quantas letras" pattern, since both contain
# the substring "quantas letras".
_SPECIFIC_LETTER_COUNT_RE = re.compile(
    r"quantas\s+(?:vezes\s+(?:a\s+)?letra|letras)\s+['\"]?(\w)['\"]?\s+"
    r"(?:aparece(?:m)?\s+em|existe(?:m)?\s+em)\s+(.+)",
    re.IGNORECASE,
)
# The verb can also come first: "quantas vezes aparece a letra X em Y"
# (vs. "quantas vezes a letra X aparece em Y" above) -- both phrasings
# appear in real usage, so both are recognized rather than picking one.
_SPECIFIC_LETTER_COUNT_VERB_FIRST_RE = re.compile(
    r"quantas\s+vezes\s+aparece\s+a\s+letra\s+['\"]?(\w)['\"]?\s+em\s+(.+)",
    re.IGNORECASE,
)
_LETTER_COUNT_RE = re.compile(
    r"quantas\s+letras\s+(?:h[áa]\s+|existem\s+)?(?:na\s+palavra|em)\s+(.+)",
    re.IGNORECASE,
)
# Category T-3 (2026-09-05, "casos traiçoeiros"): "...ignorando espaço?"
# is a no-op qualifier (spaces are never counted as letters by
# _is_letter() anyway) but must still be stripped from the target text
# itself, or its own letters ("ignorando", "espaco") would be counted.
_IGNORE_SPACE_QUALIFIER_RE = re.compile(
    r",?\s*(?:ignorando|sem\s+contar|desconsiderando|excluindo)\s+(?:o\s+)?espa[çc]os?\s*$",
    re.IGNORECASE,
)
_WORD_COUNT_RE = re.compile(
    r"quantas\s+palavras\s+(?:h[áa]|existem)\s+em[:\s]+(.+)",
    re.IGNORECASE | re.DOTALL,
)
_CHAR_COUNT_WITH_SPACES_RE = re.compile(
    r"quantos\s+caracteres,?\s*incluindo\s+espa[çc]os,?\s*existem\s+em[:\s]+(.+)",
    re.IGNORECASE | re.DOTALL,
)
_CHAR_COUNT_WITHOUT_SPACES_RE = re.compile(
    r"quantos\s+caracteres,?\s*excluindo\s+espa[çc]os,?\s*existem\s+em[:\s]+(.+)",
    re.IGNORECASE | re.DOTALL,
)
_VOWEL_COUNT_RE = re.compile(
    r"quantas\s+vogais\s+existem\s+em\s+(.+)",
    re.IGNORECASE,
)
_CONSONANT_COUNT_RE = re.compile(
    r"quantas\s+consoantes\s+existem\s+em\s+(.+)",
    re.IGNORECASE,
)
_REVERSE_STRING_RE = re.compile(
    r"inverta\s+(?:exatamente\s+)?a\s+string\s+(\S+)",
    re.IGNORECASE,
)


def compute(text: str) -> CountingResult | None:
    """Recognize and answer one of the counting/measurement patterns above.
    Returns None (never raises) for anything not confidently recognized --
    callers fall through to the normal LLM path in that case."""
    match = _SPECIFIC_LETTER_COUNT_RE.search(text) or _SPECIFIC_LETTER_COUNT_VERB_FIRST_RE.search(
        text
    )
    if match:
        letter, target = match.group(1), _strip_trailing_punctuation(match.group(2))
        if target:
            count = target.lower().count(letter.lower())
            return CountingResult(
                operation="specific_letter_count",
                formatted=f"A letra '{letter}' aparece {count} vez(es) em '{target}'.",
            )

    match = _LETTER_COUNT_RE.search(text)
    if match:
        target = _strip_trailing_punctuation(match.group(1))
        target = _IGNORE_SPACE_QUALIFIER_RE.sub("", target).strip()
        if target:
            count = sum(1 for ch in target if _is_letter(ch))
            return CountingResult(
                operation="letter_count",
                formatted=f"'{target}' tem {count} letras.",
            )

    match = _WORD_COUNT_RE.search(text)
    if match:
        target = _strip_trailing_punctuation(match.group(1))
        if target:
            count = len(target.split())
            return CountingResult(
                operation="word_count",
                formatted=f"O texto tem {count} palavras.",
            )

    match = _CHAR_COUNT_WITH_SPACES_RE.search(text)
    if match:
        target = _strip_trailing_punctuation(match.group(1))
        if target:
            return CountingResult(
                operation="char_count_with_spaces",
                formatted=f"'{target}' tem {len(target)} caracteres (incluindo espaços).",
            )

    match = _CHAR_COUNT_WITHOUT_SPACES_RE.search(text)
    if match:
        target = _strip_trailing_punctuation(match.group(1))
        if target:
            stripped = target.replace(" ", "")
            return CountingResult(
                operation="char_count_without_spaces",
                formatted=f"'{target}' tem {len(stripped)} caracteres (excluindo espaços).",
            )

    match = _VOWEL_COUNT_RE.search(text)
    if match:
        target = _strip_trailing_punctuation(match.group(1))
        if target:
            count = sum(1 for ch in target if ch in _VOWELS)
            return CountingResult(
                operation="vowel_count",
                formatted=f"'{target}' tem {count} vogais.",
            )

    match = _CONSONANT_COUNT_RE.search(text)
    if match:
        target = _strip_trailing_punctuation(match.group(1))
        if target:
            count = sum(1 for ch in target if _is_letter(ch) and ch not in _VOWELS)
            return CountingResult(
                operation="consonant_count",
                formatted=f"'{target}' tem {count} consoantes.",
            )

    match = _REVERSE_STRING_RE.search(text)
    if match:
        target = _strip_trailing_punctuation(match.group(1))
        if target:
            return CountingResult(
                operation="string_reversal",
                formatted=target[::-1],
            )

    return None
