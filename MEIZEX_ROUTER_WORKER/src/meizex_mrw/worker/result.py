"""Worker output contract.

WorkerResult is the single shape a turn produces — the same object that a
future CLI/`--json` mode and the event log both derive from. status is
``completed`` only when the synthesis verifier returned PASS; every other
outcome is ``failed`` with a human-readable ``error``. metrics only ever
holds real, measured values (duration, rounds, token counts the provider
actually reported) — never fabricated estimates presented as fact.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RouteInfo(BaseModel):
    provider: str
    model: str
    reason: str
    cloud_allowed: bool


class WorkerResult(BaseModel):
    status: Literal["completed", "failed"]
    task_type: str
    profile: str
    route: RouteInfo
    tools_used: list[str] = Field(default_factory=list)
    verification: Literal["PASS", "RETRY", "FAIL"] | None = None
    result: str = ""
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    """Serialized append-only audit trail for this turn (redacted before store)."""
