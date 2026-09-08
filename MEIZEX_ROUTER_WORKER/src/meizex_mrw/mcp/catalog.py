"""Normalizes raw MCP `tools/list` entries into ToolMetadata tool_router can rank.

Separated from MCPClient (MRW_TARGET_ARCHITECTURE.md §14: "idealmente
separar MCPClient / MCPServerConfig / ToolCatalog") so a future
multi-server registry can merge several servers' catalogs without the
client needing to know about ranking metadata at all.

The MCP protocol only gives name/description/inputSchema — it has no
concept of our capabilities/categories/risk/read_only taxonomy, so those
must be supplied explicitly per tool name via `overrides`. A tool with no
override defaults to the safest assumption for an unknown tool: not
read-only, medium risk — the deterministic tool_router policy check then
naturally excludes it from any profile that forbids writes until someone
tags it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from meizex_mrw.tools.metadata import Risk, ToolMetadata


@dataclass
class ToolOverride:
    capabilities: set[str] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)
    risk: Risk = "medium"
    read_only: bool = False


class ToolCatalog:
    def __init__(self, tools: list[ToolMetadata] | None = None) -> None:
        self._by_name: dict[str, ToolMetadata] = {tool.name: tool for tool in (tools or [])}

    @classmethod
    def from_mcp_tools(
        cls,
        raw_tools: list[dict[str, Any]],
        *,
        server: str | None = None,
        overrides: dict[str, ToolOverride] | None = None,
    ) -> ToolCatalog:
        overrides = overrides or {}
        tools: list[ToolMetadata] = []
        for raw in raw_tools:
            name = raw["name"]
            override = overrides.get(name, ToolOverride())
            schema = raw.get("inputSchema") or {"type": "object", "properties": {}}
            tools.append(
                ToolMetadata(
                    name=name,
                    description=raw.get("description", ""),
                    capabilities=override.capabilities,
                    categories=override.categories,
                    risk=override.risk,
                    read_only=override.read_only,
                    server=server,
                    parameters=schema,
                    schema_size=len(str(schema)),
                )
            )
        return cls(tools)

    def all(self) -> list[ToolMetadata]:
        return list(self._by_name.values())

    def get(self, name: str) -> ToolMetadata | None:
        return self._by_name.get(name)

    def __len__(self) -> int:
        return len(self._by_name)
