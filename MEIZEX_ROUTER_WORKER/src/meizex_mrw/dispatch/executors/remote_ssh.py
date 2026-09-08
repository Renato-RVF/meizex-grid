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
import time

from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.executors.process import ProcessExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest, ResourceExecutionResult
from meizex_mrw.events.models import StepDivergence
from meizex_mrw.events.store import EventStore
from meizex_mrw.statewatch.residual import residual_for_handle


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
        self._started = threading.Event()
        self._result: ResourceExecutionResult | None = None
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._cancelled = False

    def done(self) -> bool:
        """Non-blocking: True once the background job has a result."""
        return self._done.is_set()

    def started(self) -> bool:
        """Non-blocking: True once the job actually began running (past any
        max_concurrent queueing) — False means it is still queued, waiting
        for a concurrency slot on its executor."""
        return self._started.is_set()

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
        """Cancel this job, running or still queued behind max_concurrent.

        Returns True if a kill signal was actually sent to an in-flight
        ssh process. Returns False in every other case (already finished;
        not started yet and still queued — but the queued case IS handled:
        marking ``cancelled`` here makes the executor skip it entirely
        once its turn comes, without ever spawning ssh, ver
        RemoteSSHExecutor.execute_async). There is one narrow window this
        does not cover: between the job actually starting and its ssh
        process being captured (on_spawn fires right after Popen — call
        cancel() again if that race matters to the caller).
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

    def _mark_started(self) -> None:
        self._started.set()

    def _finish(self, result: ResourceExecutionResult) -> None:
        self._result = result
        self._done.set()


class RemoteSSHExecutor(ProcessExecutor):
    """Executes a capability on a remote Grid node via SSH, in-process-equivalent.

    Reuses ProcessExecutor's execute()/run_payload() unchanged — only the
    spawned command differs (ssh instead of a local python invocation).
    """

    DEFAULT_MAX_CONCURRENT = 4
    DEFAULT_STATEWATCH_POLL_S = 1.0
    # 0.7, not 1.0: timeout_s is also the moment run_payload() kills the
    # process (see ProcessExecutor.execute), so warning AT timeout_s gives
    # a monitor no lead time at all -- the divergence and the kill would
    # land in the same instant. 0.7 gives real advance notice.
    DEFAULT_STATEWATCH_WARN_FRACTION = 0.7

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
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        statewatch_poll_s: float = DEFAULT_STATEWATCH_POLL_S,
        statewatch_warn_fraction: float = DEFAULT_STATEWATCH_WARN_FRACTION,
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
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        self.max_concurrent = max_concurrent
        # Bounds how many jobs actually run (ssh spawned) against THIS node
        # at once. A caller can still submit as many as it wants via
        # execute_async() without ever blocking -- extra jobs queue on this
        # semaphore inside their own background thread, not on the caller.
        self._concurrency_gate = threading.Semaphore(max_concurrent)
        self.statewatch_poll_s = statewatch_poll_s
        self.statewatch_warn_fraction = statewatch_warn_fraction

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

    def execute(self, request: ResourceExecutionRequest, **kwargs) -> ResourceExecutionResult:
        """Same synchronous contract the dispatcher relies on (it always
        blocks on this call and never sees a RemoteJobHandle) -- but
        internally this now runs through execute_async() and polls the
        handle with STATEWATCH's residual() while waiting, instead of just
        blocking on a single subprocess.communicate() the way the inherited
        ProcessExecutor.execute() does.

        The one behavioral addition: the first time real elapsed time
        stops matching what this step's own timeout_s predicted -- WHILE
        the step is still running, not after it finishes or times out -- a
        StepDivergence event is appended to the attached EventStore (if
        any). It fires at most once per step; a monitor watching the event
        stream sees it long before ``wait()``/the eventual TIMEOUT result
        would ever tell it anything is wrong.

        ``**kwargs`` (e.g. ``on_spawn``) is accepted and forwarded for
        interface compatibility with ProcessExecutor.execute(), but
        execute_async() already supplies its own on_spawn internally
        (RemoteJobHandle._capture_proc) to make cancel() work -- a caller
        passing its own on_spawn here would be silently ignored, so this
        is intentionally not exposed as a real parameter.
        """
        handle = self.execute_async(request)
        timeout_s = request.timeout_s or self._timeout_s
        start = time.monotonic()
        divergence_emitted = False
        # Poll fast enough to actually catch the warn_fraction crossing
        # while it happens, not just eventually -- capped by the
        # configured statewatch_poll_s as the ceiling for a long-running
        # step so this loop stays cheap.
        warn_at = timeout_s * self.statewatch_warn_fraction if timeout_s else None
        poll_interval = (
            min(self.statewatch_poll_s, max(warn_at / 4, 0.01)) if warn_at else self.statewatch_poll_s
        )

        while not handle.done():
            elapsed = time.monotonic() - start
            divergence = residual_for_handle(
                handle,
                elapsed_s=elapsed,
                timeout_s=timeout_s,
                warn_fraction=self.statewatch_warn_fraction,
            )
            if divergence is not None and not divergence_emitted and self._store is not None:
                self._store.append(
                    StepDivergence(
                        session_id=request.run_id or request.step_id or self.resource_id,
                        run_id=request.run_id or "",
                        step_id=request.step_id or f"{request.step.capability}-{self.kind}",
                        capability=request.step.capability,
                        resource=request.step.resource,
                        executor_kind=self.kind,
                        expected_phase=divergence.expected_phase,
                        observed_phase=divergence.observed_phase,
                        elapsed_s=divergence.elapsed_s,
                    )
                )
                divergence_emitted = True
            # done() may have flipped true while we were computing the
            # residual above; re-check before sleeping so a fast job never
            # waits out a full poll interval it didn't need to.
            if handle.done():
                break
            time.sleep(poll_interval)

        return handle.wait()

    def execute_async(self, request: ResourceExecutionRequest) -> RemoteJobHandle:
        """Submit ``request`` for background execution; returns immediately,
        regardless of how many jobs are already queued or running.

        At most ``self.max_concurrent`` jobs actually run (ssh spawned)
        against THIS node at once — extras wait on an internal semaphore,
        inside their own background thread, never blocking the caller.
        Cancelling a still-queued job (``handle.cancel()`` before its turn
        comes) makes it skip execution entirely once the slot frees up —
        it never spawns ssh.

        Runs the unmodified ``execute()`` (same SSH round-trip, same
        timeout_s, same structured result) once its concurrency slot is
        acquired. Use this when the CALLER (not the ResourceDispatcher's
        synchronous step loop) wants to hand off a job and keep doing other
        work — e.g. an orchestrator sending a heavy stress test to the Grid
        while it moves on to other sprints. The dispatcher itself keeps
        using the synchronous execute(); this method is not wired into
        can_handle() or the dispatcher's selection loop.
        """
        handle = RemoteJobHandle(request)

        def _run() -> None:
            with self._concurrency_gate:
                if handle.cancelled:
                    handle._finish(self._cancelled_before_start_result(request))
                    return
                handle._mark_started()
                # Deliberately ProcessExecutor.execute(), NOT self.execute():
                # self.execute() is now the STATEWATCH-polling wrapper that
                # itself calls execute_async() to get a handle to poll --
                # calling it from here would recurse (each job spawning
                # another execute_async() call) instead of ever doing the
                # actual subprocess round-trip.
                handle._finish(ProcessExecutor.execute(self, request, on_spawn=handle._capture_proc))

        thread = threading.Thread(
            target=_run,
            name=f"grid-ssh-{request.step_id or request.step.resource}",
            daemon=True,
        )
        thread.start()
        return handle

    def _cancelled_before_start_result(
        self, request: ResourceExecutionRequest
    ) -> ResourceExecutionResult:
        """A queued job cancelled before its concurrency slot ever opened:
        no ssh process ever spawned, so ProcessExecutor._map_outcome's
        machinery does not apply — build the structured result by hand."""
        return ResourceExecutionResult(
            step_id=request.step_id or f"{request.step.capability}-{self.kind}",
            resource_id=request.step.resource,
            capability=request.step.capability,
            executor_kind=self.kind,
            status="FAILED",
            output=None,
            evidence=[{"kind": "process_boundary", "status": "CANCELLED_BEFORE_START"}],
            error="cancelled while queued behind max_concurrent -- never spawned ssh",
        )


__all__ = ["RemoteSSHExecutor", "RemoteJobHandle"]
