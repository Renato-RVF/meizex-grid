"""MCP client — JSON-RPC 2.0 over stdio.

Refactored from MEIZEX_HARNESS_V2's mcp_client.py. Kept: stdio transport,
JSON-RPC framing, the initialize/notifications-initialized handshake,
tools/list, tools/call, correlation by request id, and the Windows
CREATE_NO_WINDOW flag (a .cmd/.bat MCP launcher can only run through
cmd.exe on Windows, and without this flag that pops a visible, empty
console window). Changed: takes an MCPServerConfig instead of three loose
constructor args; raises a dedicated MCPError instead of bare
RuntimeError/TimeoutError; uses a logger instead of a stray print();
detects a crashed subprocess while waiting for a response instead of
just timing out; stop() waits with a timeout before escalating to kill().
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from typing import Any

from meizex_mrw.mcp.config import MCPServerConfig
from meizex_mrw.security import redact_sensitive_text

logger = logging.getLogger("meizex_mrw.mcp")


class MCPError(RuntimeError):
    """Raised for MCP transport/protocol failures — not tool-level errors,
    which are returned as ordinary strings from call_tool()."""


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._env = {**os.environ, **config.env} if config.env else None
        self.process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._responses: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            self.config.command,
            cwd=self.config.cwd,
            env=self._env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            creationflags=creation_flags,
        )
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        self.send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "meizex-mrw", "version": "0.1.0"},
            },
        )
        self.send_notification("notifications/initialized", {})
        logger.info("MCP server %r initialized", self.config.name)

    def _read_stdout(self) -> None:
        while not self._stop_event.is_set() and self.process and self.process.poll() is None:
            line = self.process.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "MCP server %r sent invalid stdout: %s",
                    self.config.name,
                    redact_sensitive_text(line),
                )
                continue
            if "id" in data:
                with self._lock:
                    self._responses[data["id"]] = data

    def _read_stderr(self) -> None:
        while not self._stop_event.is_set() and self.process and self.process.poll() is None:
            line = self.process.stderr.readline()
            if not line:
                break
            logger.debug(
                "MCP server %r stderr: %s", self.config.name, redact_sensitive_text(line.strip())
            )

    def send_request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        if not self.process or not self.process.stdin:
            raise MCPError(f"MCP server {self.config.name!r} is not running")

        with self._lock:
            req_id = self._next_id
            self._next_id += 1

        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params

        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(
                f"MCP server {self.config.name!r} crashed while sending {method!r}"
            ) from exc

        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            with self._lock:
                if req_id in self._responses:
                    return self._responses.pop(req_id)
            if self.process.poll() is not None:
                raise MCPError(
                    f"MCP server {self.config.name!r} exited "
                    f"(code {self.process.returncode}) while waiting for {method!r}"
                )
            time.sleep(0.02)

        raise MCPError(f"timeout waiting for MCP response to {method!r}")

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self.process or not self.process.stdin:
            return
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def list_tools(self) -> list[dict[str, Any]]:
        res = self.send_request("tools/list")
        if "error" in res:
            raise MCPError(f"tools/list failed: {res['error']}")
        return res.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = 120.0) -> str:
        res = self.send_request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=timeout
        )
        if "error" in res:
            raise MCPError(f"tool {name!r} failed: {res['error']}")
        content = res.get("result", {}).get("content", [])
        text_outputs = [item.get("text", "") for item in content if item.get("type") == "text"]
        return "\n".join(text_outputs)

    def stop(self) -> None:
        self._stop_event.set()
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None
