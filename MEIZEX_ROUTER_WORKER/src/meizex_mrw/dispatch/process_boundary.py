"""Explicit process boundary execution for MRW (M9).

An executable capability classified ``PROCESS`` runs OUTSIDE the MRW
orchestrator process. The parent drives a real child process, feeds it a
serializable JSON payload, reads a structured JSON result, captures the exit
code and a bounded/stderr sample, and maps every failure mode onto a
structured outcome. The child may crash, ``sys.exit``, be terminated, or
segfault without ever terminating the MRW main process.

Secondary guarantee: the child environment is built from an explicit minimal
allowlist — it never inherits the host environment implicitly, so ambient
secrets (provider API keys, tokens, arbitrary credentials) do not cross the
boundary unless explicitly allowed.

THIS MILESTONE DOES NOT CLAIM: filesystem isolation, network isolation, or
protection against malicious code operating with the user's OS privileges.
This is a process boundary, NOT a sandbox. Do not name or describe this layer
as SANDBOXED, FULLY_ISOLATED, or SECURE_SANDBOX.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Literal

ProcessBoundaryStatus = Literal[
    "SUCCESS",
    "NONZERO_EXIT",
    "TIMEOUT",
    "TRANSPORT_FAILURE",
    "SPAWN_FAILURE",
]

# Minimal environment required for a well-behaved child on Windows-first
# platforms. Everything else in the host environment is deliberately dropped.
# Windows environment lookup is case-insensitive, so matching is
# case-insensitive on every platform.
DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "OS",
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "USERNAME",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "APPDATA",
    "LOCALAPPDATA",
)

# Always force a UTF-8 child stdio so the JSON transport is independent of the
# host console codepage (Windows-primary concern).
_ALWAYS_UTF8 = {"PYTHONIOENCODING": "utf-8"}

_STDERR_MAX_CHARS = 4096


@dataclass
class ProcessBoundaryOutcome:
    """Structured result of an out-of-process execution."""

    status: ProcessBoundaryStatus
    exit_code: int | None = None
    ok: bool = False
    result: Any = None
    error: str | None = None
    stderr: str = ""
    pid: int | None = None
    timed_out: bool = False


def _env_lookup(name: str) -> str | None:
    upper = name.upper()
    for key, value in os.environ.items():
        if key.upper() == upper:
            return value
    return None


def build_child_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the child environment from an explicit allowlist.

    Only names in :data:`DEFAULT_ENV_ALLOWLIST` (plus ``PYTHONIOENCODING``)
    survive from the host; callers may extend with ``extra`` for variables
    genuinely required by a specific executable capability.
    """
    env = {
        name: value for name in DEFAULT_ENV_ALLOWLIST if (value := _env_lookup(name)) is not None
    }
    env.update(_ALWAYS_UTF8)
    if extra:
        env.update(extra)
    return env


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_payload(
    command: list[str],
    payload: dict[str, Any],
    *,
    timeout_s: float,
    cwd: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> ProcessBoundaryOutcome:
    """Run ``command`` as a child process with an explicit environment.

    The payload is serialized as one JSON line on the child's stdin; the child
    is expected to write one JSON object on stdout of the form
    ``{"ok": bool, "result": ..., "error": ...}``. Every failure mode
    (spawn failure, timeout, non-zero exit, malformed transport) is returned
    as a structured :class:`ProcessBoundaryOutcome` — it never raises, so a
    failing child can never take down the parent.
    """
    env = build_child_environment(extra_env)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=_creationflags(),
        )
    except (OSError, ValueError) as exc:
        return ProcessBoundaryOutcome(
            status="SPAWN_FAILURE",
            error=f"failed to spawn child process: {exc}",
        )

    pid = proc.pid
    input_text = json.dumps(payload, ensure_ascii=False) + "\n"
    timed_out = False
    stdout_text = ""
    stderr_text = ""
    try:
        try:
            stdout_text, stderr_text = proc.communicate(input=input_text, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            try:
                stdout_text, stderr_text = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                # The process refused to die; wait() with a hard cap is the
                # last resort before giving up on reaping it.
                proc.wait(timeout=5)
                stdout_text, stderr_text = proc.communicate(timeout=5)
    except Exception as exc:  # noqa: BLE001 - never let the child take the parent down
        return ProcessBoundaryOutcome(
            status="TRANSPORT_FAILURE",
            pid=pid,
            timed_out=timed_out,
            error=f"process boundary transport failed: {exc}",
            stderr=stderr_text[-_STDERR_MAX_CHARS:],
        )

    exit_code = proc.returncode
    if timed_out:
        return ProcessBoundaryOutcome(
            status="TIMEOUT",
            exit_code=exit_code,
            pid=pid,
            timed_out=True,
            error=f"child process exceeded the {timeout_s}s timeout",
            stderr=stderr_text[-_STDERR_MAX_CHARS:],
        )

    if exit_code != 0:
        return ProcessBoundaryOutcome(
            status="NONZERO_EXIT",
            exit_code=exit_code,
            pid=pid,
            error=f"child process exited with code {exit_code}",
            stderr=stderr_text[-_STDERR_MAX_CHARS:],
        )

    parsed: Any = None
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(parsed, dict):
        return ProcessBoundaryOutcome(
            status="TRANSPORT_FAILURE",
            exit_code=exit_code,
            pid=pid,
            error="child produced no structured result on stdout",
            stderr=stderr_text[-_STDERR_MAX_CHARS:],
        )

    return ProcessBoundaryOutcome(
        status="SUCCESS",
        exit_code=exit_code,
        ok=bool(parsed.get("ok")),
        result=parsed.get("result"),
        error=parsed.get("error"),
        pid=pid,
        stderr=stderr_text[-_STDERR_MAX_CHARS:],
    )


__all__ = [
    "DEFAULT_ENV_ALLOWLIST",
    "ProcessBoundaryOutcome",
    "ProcessBoundaryStatus",
    "build_child_environment",
    "run_payload",
]
