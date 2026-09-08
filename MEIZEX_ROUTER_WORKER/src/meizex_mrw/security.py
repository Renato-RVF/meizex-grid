"""Security boundaries shared by logs, subprocess streams, and event storage.

Ported near-verbatim from MEIZEX_HARNESS_V2/src/meizex_harness_v2/security.py —
this module had zero dependencies on the old Chassis/GUI and its redaction
patterns are runtime-agnostic, so there was nothing to strip.
"""

from __future__ import annotations

import logging
import re
from typing import Any

_REDACTED = "***"
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.I | re.S,
        ),
        _REDACTED,
    ),
    (re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}\b"), _REDACTED),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"), _REDACTED),
    (
        re.compile(r"\bAuthorization\s*[:=]\s*(?:Bearer|Basic)?\s*[A-Za-z0-9._~+/=-]{8,}", re.I),
        "Authorization: " + _REDACTED,
    ),
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I),
        "Bearer " + _REDACTED,
    ),
    (
        re.compile(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        _REDACTED,
    ),
    (
        re.compile(
            r"(?P<prefix>[\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token"
            r"|token|secret|password)[\"']?\s*[:=]\s*[\"']?)"
            r"(?P<value>[^\"'\s,;}]{4,})",
            re.I,
        ),
        r"\g<prefix>" + _REDACTED,
    ),
)


def redact_sensitive_text(value: str) -> str:
    """Remove recognizable credentials while preserving diagnostic structure."""
    if not isinstance(value, str):
        return value
    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_sensitive_value(value: Any) -> Any:
    """Recursively sanitize strings before crossing a persistence boundary."""
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {key: redact_sensitive_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)
    return value


class RedactingFormatter(logging.Formatter):
    """Redact the fully rendered record, including formatted tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))
