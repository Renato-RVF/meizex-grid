"""RemoteSSHExecutor — runs an executable capability on a remote Grid node (M-Grid).

This is a thin specialization of :class:`ProcessExecutor`: the process
boundary contract (JSON on stdin, JSON on stdout, timeout, structured
failure mapping) is transport-agnostic — ``run_payload`` only cares that
``command`` is something ``subprocess.Popen`` can spawn and pipe to. SSH
with a remote command attaches local stdin/stdout to the remote process's
stdin/stdout transparently, so no new transport logic is needed: only the
``command`` list changes.

Prerequisite (out of band, not managed by this module): the target host
must have the ``meizex_mrw`` package importable (its dependencies —
httpx, pydantic, PyYAML — installed) and a Grid SSH pilot already
configured per MEIZEX_GRID/NEXT-004 / NEXT-005 (dedicated non-admin
account, public-key auth, firewall restricted to the caller's IP). This
class does not configure any of that — it only shapes the command that
SSH runs.

THIS MILESTONE DOES NOT CLAIM: network resilience, retry, connection
pooling, or credential management. A single SSH round-trip per execute()
call, same as the local ProcessExecutor's single subprocess round-trip.
"""

from __future__ import annotations

from meizex_mrw.dispatch.executors.process import ProcessExecutor
from meizex_mrw.events.store import EventStore


class RemoteSSHExecutor(ProcessExecutor):
    """Executes a capability on a remote Grid node via SSH, in-process-equivalent.

    Reuses ProcessExecutor's execute()/run_payload() unchanged — only the
    spawned command differs (ssh instead of a local python invocation).
    """

    def __init__(
        self,
        *,
        host: str,
        ssh_user: str,
        ssh_key_path: str,
        remote_workdir: str,
        remote_python: str,
        ssh_path: str = "ssh",
        timeout_s: float | None = None,
        extra_env: dict[str, str] | None = None,
        event_store: EventStore | None = None,
    ) -> None:
        remote_cmd = (
            f'cd /d "{remote_workdir}" && set PYTHONPATH=src && '
            f'"{remote_python}" -m meizex_mrw.dispatch.process_runner'
        )
        command = [
            ssh_path,
            "-i",
            ssh_key_path,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            f"{ssh_user}@{host}",
            remote_cmd,
        ]
        super().__init__(
            command=command,
            timeout_s=timeout_s,
            extra_env=extra_env,
            event_store=event_store,
        )
        self._host = host

    @property
    def kind(self) -> str:
        return "remote_ssh"


__all__ = ["RemoteSSHExecutor"]
