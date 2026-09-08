#!/usr/bin/env python
"""Real, strictly read-only filesystem MCP server (stdio/JSON-RPC).

Used by the Milestone 3 real-filesystem integration test and by the MRW CLI
(`mrw run/route/tools --cwd <dir>`) to run a genuine read-only mission
against a real directory through the worker's real MCPClient subprocess. It
is a real MCP server, not a fixture stub: stat/list/search/read hit the
actual filesystem.

Guarantees (enforced here, never delegated to the caller):
  * every tool is read-only — no write/delete/rename/move/shell capability;
  * all reads are confined to a single root directory (chosen at startup);
  * reads are capped (read_document_bounded truncates; search/list are
    bounded) so a single response can never blow the worker context budget;
  * no symlink following and no escaping the root.

Usage: python -m meizex_mrw.mcp_filesystem_readonly --root <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from meizex_mrw.mcp.catalog import ToolOverride

MAX_READ_CHARS = 4000
MAX_ENTRIES = 100
MAX_SEARCH_RESULTS = 50

FS_TOOL_NAMES = (
    "stat_path",
    "list_directory",
    "search_files",
    "read_document_bounded",
    "read_document_range",
)


def filesystem_tool_overrides() -> dict[str, ToolOverride]:
    """The read-only tool metadata MRW uses to route/tag the FS toolset."""
    return {
        name: ToolOverride(categories={"filesystem_read"}, risk="low", read_only=True)
        for name in FS_TOOL_NAMES
    }


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _text_result(req_id: int, text: str) -> None:
    _write(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    )


def _error(req_id: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": -2, "message": message}})


class ReadOnlyFilesystem:
    """Real, strictly read-only filesystem access confined to one root.

    The pure ``*_data`` methods perform the actual filesystem operation and
    return a plain value (dict for stat/list/search, str for the bounded
    read), raising ``ValueError``/``OSError`` on failure. The MCP server
    handlers and the in-process DeterministicExecutor both reuse these
    methods, so the read-only guarantees live in one place.

    Path grounding (``require_grounding=True``, the default): a path may
    only be read (``read_document_bounded_data``/``read_document_range_data``)
    if this same instance already discovered it via ``stat_path_data``,
    ``list_directory_data`` (which grounds the directory's children too),
    or ``search_files_data`` -- otherwise the read is refused with
    ``ValueError("Policy blocked: Path not grounded: ...")``, even when the
    path exists on disk. This defends against a model hallucinating a
    plausible-looking path and getting real file content back just
    because it happens to be correct, instead of because it actually
    looked. Real incident this reproduces/guards against: see
    ``tests/test_incident_regression_fixture.py``.

    Grounding is per-instance, in-memory state — exactly matched to how
    the MCP server in :func:`main` uses one long-lived instance across
    every ``tools/call`` a model makes in one conversation, so discovery
    in an earlier round grounds a read in a later one. The in-process
    :class:`~meizex_mrw.dispatch.executors.deterministic.DeterministicExecutor`
    is the deliberate exception: it builds a fresh, single-use instance
    per literal, already-resolved read (the path came from parsing the
    user's own mission text, not a model improvising across tool-call
    rounds), so it constructs this class with ``require_grounding=False``.
    """

    def __init__(self, root: Path, *, require_grounding: bool = True) -> None:
        self.root = root.resolve()
        self._require_grounding = require_grounding
        self._grounded: set[Path] = set()

    def _contained(self, path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path escapes the read-only root: {path}")
        if resolved.is_symlink():
            raise ValueError(f"symlinks are not followed: {path}")
        return resolved

    def _ground(self, path: Path) -> None:
        self._grounded.add(path)

    def _require_grounded(self, target: Path, original: str) -> None:
        if self._require_grounding and target not in self._grounded:
            raise ValueError(f"Policy blocked: Path not grounded: {original}")

    def stat_path_data(self, path: str) -> dict:
        target = self._contained(path)
        self._ground(target)
        if target.is_dir():
            return {
                "path": str(target),
                "kind": "dir",
                "entries": len(list(target.iterdir())) if target.exists() else 0,
            }
        if target.is_file():
            return {
                "path": str(target),
                "kind": "file",
                "size": target.stat().st_size,
                "extension": target.suffix or "",
            }
        raise FileNotFoundError(f"no such path: {path}")

    def list_directory_data(self, path: str) -> dict:
        target = self._contained(path)
        if not target.is_dir():
            raise NotADirectoryError(f"not a directory: {path}")
        self._ground(target)
        entries = []
        for item in sorted(target.iterdir()):
            self._ground(item.resolve())
            entries.append({"name": item.name, "kind": "dir" if item.is_dir() else "file"})
        return {"path": str(target), "entries": entries[:MAX_ENTRIES]}

    def search_files_data(self, pattern: str, root: str = "") -> dict:
        base = self._contained(root) if root else self.root
        needle = pattern.lower()
        matches: list[dict] = []
        for current, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(current, d))]
            for name in files + dirs:
                if needle in name.lower():
                    full = os.path.join(current, name)
                    self._ground(Path(full).resolve())
                    matches.append(
                        {
                            "name": full,
                            "kind": "dir" if name in dirs else "file",
                        }
                    )
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        break
            if len(matches) >= MAX_SEARCH_RESULTS:
                break
        return {"matches": matches}

    def read_document_bounded_data(self, path: str, max_chars: int = MAX_READ_CHARS) -> str:
        target = self._contained(path)
        self._require_grounded(target, path)
        if not target.is_file():
            raise FileNotFoundError(f"not a readable file: {path}")
        limit = min(int(max_chars or MAX_READ_CHARS), MAX_READ_CHARS)
        return target.read_text(encoding="utf-8", errors="replace")[:limit]

    def read_document_range_data(
        self, path: str, offset: int = 0, max_chars: int = MAX_READ_CHARS
    ) -> dict:
        """Paginated read for files too large for a single
        read_document_bounded call. offset/max_chars are character (not
        byte) positions into the decoded text, so pagination is stable
        regardless of encoding. next_offset always equals offset +
        chars_returned, valid to pass back in as the next call's offset
        whether or not has_more is True (an EOF offset just returns
        chars_returned=0, has_more=False, deterministically)."""
        target = self._contained(path)
        self._require_grounded(target, path)
        if not target.is_file():
            raise FileNotFoundError(f"not a readable file: {path}")
        offset = max(int(offset or 0), 0)
        limit = min(int(max_chars or MAX_READ_CHARS), MAX_READ_CHARS)
        full_text = target.read_text(encoding="utf-8", errors="replace")
        total_chars = len(full_text)
        chunk = full_text[offset : offset + limit]
        chars_returned = len(chunk)
        next_offset = offset + chars_returned
        return {
            "path": str(target),
            "offset": offset,
            "max_chars": limit,
            "content": chunk,
            "chars_returned": chars_returned,
            "total_chars": total_chars,
            "next_offset": next_offset,
            "has_more": next_offset < total_chars,
        }

    def stat_path(self, req_id: int, path: str) -> None:
        try:
            _text_result(req_id, json.dumps(self.stat_path_data(path)))
        except (ValueError, OSError) as exc:
            _error(req_id, str(exc))

    def list_directory(self, req_id: int, path: str) -> None:
        try:
            _text_result(req_id, json.dumps(self.list_directory_data(path)))
        except (ValueError, OSError) as exc:
            _error(req_id, str(exc))

    def search_files(self, req_id: int, pattern: str, root: str = "") -> None:
        try:
            _text_result(req_id, json.dumps(self.search_files_data(pattern, root)))
        except (ValueError, OSError) as exc:
            _error(req_id, str(exc))

    def read_document_bounded(
        self, req_id: int, path: str, max_chars: int = MAX_READ_CHARS
    ) -> None:
        try:
            _text_result(req_id, self.read_document_bounded_data(path, max_chars))
        except (ValueError, OSError) as exc:
            _error(req_id, str(exc))

    def read_document_range(
        self, req_id: int, path: str, offset: int = 0, max_chars: int = MAX_READ_CHARS
    ) -> None:
        try:
            _text_result(req_id, json.dumps(self.read_document_range_data(path, offset, max_chars)))
        except (ValueError, OSError) as exc:
            _error(req_id, str(exc))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only filesystem MCP server")
    parser.add_argument("--root", required=True, help="Root directory that confines all reads")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        sys.stderr.write(f"root is not a directory: {root}\n")
        sys.exit(2)

    server = ReadOnlyFilesystem(root)
    tools = [
        {
            "name": "stat_path",
            "description": f"Read-only metadata about a file or directory inside {root}.",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "list_directory",
            "description": f"Read-only listing of a directory inside {root}.",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "search_files",
            "description": f"Read-only name search under {root}.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "root": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
        {
            "name": "read_document_bounded",
            "description": f"Read up to max_chars characters of a text file inside {root}.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "read_document_range",
            "description": (
                f"Paginated read of a text file inside {root} — pass the previous "
                "call's next_offset to continue past a file too large for "
                "read_document_bounded's single-shot cap."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    ]

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        req_id = request.get("id")
        if method == "initialize":
            _write({"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05"}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _write({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})
        elif method == "tools/call":
            params = request.get("params", {})
            name = params.get("name")
            arguments = params.get("arguments", {})
            if name == "stat_path":
                server.stat_path(req_id, arguments.get("path", ""))
            elif name == "list_directory":
                server.list_directory(req_id, arguments.get("path", ""))
            elif name == "search_files":
                server.search_files(req_id, arguments.get("pattern", ""), arguments.get("root", ""))
            elif name == "read_document_bounded":
                server.read_document_bounded(
                    req_id, arguments.get("path", ""), arguments.get("max_chars")
                )
            elif name == "read_document_range":
                server.read_document_range(
                    req_id,
                    arguments.get("path", ""),
                    arguments.get("offset", 0),
                    arguments.get("max_chars"),
                )
            else:
                _error(req_id, f"unknown tool {name}")
        elif req_id is not None:
            _error(req_id, f"unknown method {method}")


if __name__ == "__main__":
    main()
