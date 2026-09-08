"""Out-of-process executable capability entry point (M9).

Run as a real child process via ``python -m meizex_mrw.dispatch.process_runner``.
The parent (:func:`meizex_mrw.dispatch.process_boundary.run_payload`) writes one
JSON line on stdin:

    {"capability": "...", "mission": "...", "previous_output": ..., "args": {...}}

The runner executes the named executable capability handler and writes one JSON
object on stdout:

    {"ok": true, "result": {...}}
    {"ok": false, "error": "..."}

Handler exceptions are captured as ``ok: false`` (transport still delivers a
structured result). ``sys.exit``/``os._exit`` are deliberately NOT caught here:
a deliberate exit is the child terminating itself, which the parent must be
able to survive — that is precisely the crash-resilience guarantee being
exercised across the real process boundary.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any


def _env_lookup(name: str) -> str | None:
    upper = name.upper()
    for key, value in os.environ.items():
        if key.upper() == upper:
            return value
    return None


def _handler_deterministic_processing(
    payload: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any]:
    """The M9 executable capability: deterministic JSON transform, no LLM.

    Runs inside the real child process; the pid/parent_pid pair proves the
    capability crossed the process boundary.
    """
    return {
        "ok": True,
        "result": {
            "processed": True,
            "mission": payload.get("mission"),
            "received_input": payload.get("previous_output"),
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "note": "Executed out-of-process via explicit process boundary (no LLM).",
        },
    }


def _handler_environment_probe(payload: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Report whether specific environment variables crossed the boundary.

    Used to prove (from the REAL child response) that ambient secrets are not
    implicitly inherited and that explicitly allowed variables do cross.
    """
    names = args.get("vars", [])
    return {
        "ok": True,
        "result": {
            "pid": os.getpid(),
            "env": {name: _env_lookup(name) for name in names},
        },
    }


HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "deterministic_processing": _handler_deterministic_processing,
    "environment_probe": _handler_environment_probe,
}


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    capability = payload.get("capability") or "deterministic_processing"
    args = payload.get("args") or {}
    handler = HANDLERS.get(capability)
    if handler is None:
        return {"ok": False, "error": f"unknown executable capability {capability!r}"}
    return handler(payload, args)


def main() -> int:
    try:
        payload = _read_payload()
        response = _dispatch(payload)
        json.dump(response, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.stdout.flush()
    except Exception as exc:  # noqa: BLE001 - transport-level failure, stay structured
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
