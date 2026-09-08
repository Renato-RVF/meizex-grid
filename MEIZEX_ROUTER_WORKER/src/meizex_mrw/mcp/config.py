"""MCP server connection config — separated from MCPClient so a future
multi-server registry can hold a list of these without redesigning the
client itself (MRW_AUDIT_REPORT.md flagged single-server-only as a gap
in the legacy mcp_client.py)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    """Merged over the inherited environment, not a replacement — callers
    only need to pass variables they actually care about."""
