"""Tool metadata schema — what tool_router needs to rank a tool, nothing more.

Deliberately small: name/description/capabilities/categories/risk/read_only
are the fields the deterministic router in tool_router.py actually
consumes (see MRW_TARGET_ARCHITECTURE.md §6). server/namespace/schema_size
are optional bookkeeping for a future multi-MCP-server catalog and are not
required to construct one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Risk = Literal["low", "medium", "high"]


class ToolMetadata(BaseModel):
    name: str
    description: str = ""
    capabilities: set[str] = Field(default_factory=set)
    categories: set[str] = Field(default_factory=set)
    risk: Risk = "medium"
    read_only: bool = False

    # Optional — filled in once a real multi-server MCP catalog exists.
    server: str | None = None
    namespace: str | None = None
    schema_size: int | None = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
