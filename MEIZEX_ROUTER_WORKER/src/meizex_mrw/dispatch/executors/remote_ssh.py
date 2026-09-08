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

Async dispatch (:meth:`RemoteSSHExecutor.execute_async`): ``execute()``
itself stays synchronous/blocking on purpose — it is what
``ResourceDispatcher`` calls, and the ``ResourceExecutor`` protocol expects
a direct return, not a future. ``execute_async`` is an additive capability
for callers OUTSIDE the dispatcher's synchronous step loop (e.g. an
orchestrator that wants to hand a heavy job to the Grid and keep working):
it runs the exact same ``execute()`` on a background thread and returns a
:class:`RemoteJobHandle` immediately, with :meth:`RemoteJobHandle.cancel`
able to kill the in-flight local ssh process. A thread (not asyncio) is
the pragmatic choice here because the underlying transport is a blocking
``subprocess.Popen(...).communicate()`` call in
:mod:`meizex_mrw.dispatch.process_boundary` — moving to asyncio would mean
rewriting that transport, a bigger change than this milestone claims.
"""

from __future__ import annotations

import subprocess
import threading

from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.executors.process import ProcessExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest, ResourceExecutionResult
from meizex_mrw.events.store import EventStore


class RemoteJobHandle:
    """A RemoteSSHExecutor job running on a background thread.

    ``execute_async`` returns this immediately; the SSH round-trip keeps
    running in the background. The caller can do other work and later
    check :meth:`done`/:meth:`poll` (non-blocking), :meth:`wait` (blocking,
    with an optional timeout), or :meth:`cancel` an in-flight job.

    Cancellation kills the LOCAL ssh client process. That tears down the
    SSH connection, which normally makes the remote sshd terminate the
    remote python process too (its stdin/stdout pipe closes) — but this is
    an observed consequence of how OpenSSH behaves, not a guarantee this
    class enforces or verifies. A remote process that ignores a closed
    pipe would keep running on the Grid node with nothing here to detect
    or stop it; that residual risk is not solved by this milestone.
    """

    def __init__(self, request: ResourceExecutionRequest) -> None:
        self.request = request
        self._done = threading.Event()
        self._result: ResourceExecutionResult | None = None
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._cancelled = False

    def done(self) -> bool:
        """Non-blocking: True once the background job has a result."""
        return self._done.is_set()

    @property
    def cancelled(self) -> bool:
        """True if cancel() was called on this handle (whether or not the
        kill actually landed before the job finished on its own)."""
        return self._cancelled

    def poll(self) -> ResourceExecutionResult | None:
        """Non-blocking: the result if finished, else None."""
        return self._result if self._done.is_set() else None

    def wait(self, timeout: float | None = None) -> ResourceExecutionResult:
        """Block until the job finishes, or raise TimeoutError.

        A TimeoutError here is about THIS call, not the job itself — the
        background thread keeps running (it has its own timeout_s already
        enforced inside execute()/run_payload()); call wait() again to
        keep checking.
        """
        if not self._done.wait(timeout):
            raise TimeoutError(
                f"remote job {self.request.step_id!r} on resource "
                f"{self.request.step.resource!r} is still running after "
                f"waiting {timeout}s (it may still complete later)"
            )
        assert self._result is not None
        return self._result

    def cancel(self) -> bool:
        """Kill the local ssh process for this job, if it is still running.

        Returns True if a kill signal was actually sent (the job was still
        in flight and the ssh process had already been captured), False
        otherwise (already finished, or the process was not spawned yet —
        there is a narrow window right after execute_async() starts the
        thread where Popen has not run yet; call cancel() again if that
        matters, or accept the job runs in that case).
        """
        self._cancelled = True
        with self._proc_lock:
            proc = self._proc
        if proc is None or self._done.is_set():
            return False
        try:
            proc.kill()
        except OSError:
            return False
        return True

    def _capture_proc(self, proc: subprocess.Popen[str]) -> None:
        with self._proc_lock:
            self._proc = proc

    def _finish(self, result: ResourceExecutionResult) -> None:
        self._result = result
        self._done.set()


class RemoteSSHExecutor(ProcessExecutor):
    """Executes a capability on a remote Grid node via SSH, in-process-equivalent.

    Reuses ProcessExecutor's execute()/run_payload() unchanged — only the
    spawned command differs (ssh instead of a local python invocation).
    """

    def __init__(
        self,
        *,
        resource_id: str,
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
        self.resource_id = resource_id

    @property
    def kind(self) -> str:
        return "remote_ssh"

    def can_handle(self, step: ExecutionStep) -> bool:
        # Stricter than ProcessExecutor's plain boundary check: this executor
        # only claims steps explicitly addressed to ITS node. Without this,
        # any PROCESS-boundary step would be grabbed by whichever executor
        # happens to be first in the dispatcher's list, regardless of which
        # machine it actually targets -- silently wrong for a multi-node Grid.
        return step.execution_boundary == "PROCESS" and step.resource == self.resource_id

    def execute_async(self, request: ResourceExecutionRequest) -> RemoteJobHandle:
        """Submit ``request`` for background execution; returns immediately.

        Runs the unmodified ``execute()`` (same SSH round-trip, same
        timeout_s, same structured result) on a daemon thread. Use this
        when the CALLER (not the ResourceDispatcher's synchronous step
        loop) wants to hand off a job and keep doing other work — e.g. an
        orchestrator sending a heavy stress test to the Grid while it
        moves on to other sprints. The dispatcher itself keeps using the
        synchronous execute(); this method is not wired into can_handle()
        or the dispatcher's selection loop.
        """
        handle = RemoteJobHandle(request)

        def _run() -> None:
            handle._finish(self.execute(request, on_spawn=handle._capture_proc))

        thread = threading.Thread(
            target=_run,
            name=f"grid-ssh-{request.step_id or request.step.resource}",
            daemon=True,
        )
        thread.start()
        return handle


__all__ = ["RemoteSSHExecutor", "RemoteJobHandle"]
