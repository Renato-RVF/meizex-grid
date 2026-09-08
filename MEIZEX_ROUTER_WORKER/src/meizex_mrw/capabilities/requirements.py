"""Deterministic mission -> capability requirements classifier (v2).

The v1 classifier embedded its keyword vocabulary in code. Since M5.1 that
vocabulary lives in :mod:`meizex_mrw.capabilities.descriptors` (typed
:class:`CapabilityDescriptor` documents, data file
``docs/capability_descriptors.json``). This module is now a thin matcher:

1. lowercases the mission;
2. walks descriptors in file order (first match wins — same precedence as v1);
3. emits the descriptor's capability requirements.

Still fully deterministic: no LLM call is ever used to discover requirements
that simple rules resolve. Same input, same output.
"""

from __future__ import annotations

from meizex_mrw.capabilities import descriptors
from meizex_mrw.capabilities.models import CapabilityRequirement


def requirements(message: str) -> list[CapabilityRequirement]:
    """Deterministic mission -> capability requirements (v2).

    Same input, same output. Returns an explicit (possibly empty) list; an
    empty list means "no capability rule matched with confidence".
    """
    if len(message.strip()) < 3:
        return []
    lowered = message.lower()
    for descriptor in descriptors.load_descriptors():
        if descriptors.match_descriptor(descriptor, lowered):
            return descriptors.to_requirements(descriptor)
    return []


def required_capabilities(message: str) -> list[str]:
    """The capability names this mission requires (canonical vocabulary)."""
    return [r.capability for r in requirements(message) if r.kind == "required"]
