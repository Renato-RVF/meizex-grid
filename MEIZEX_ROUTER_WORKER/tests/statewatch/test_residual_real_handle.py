"""Real test: a real RemoteJobHandle, a real background thread, real
wall-clock time -- proving residual_for_handle() catches a job that has
gone past its own declared timeout_s from OUTSIDE, without anyone ever
calling handle.wait() (which is the only mechanism that exists today).

No SSH, no network: the SSH transport itself was already proven for real
against the Grid in NEXT-006..010 (see MEIZEX_GRID/NEXT.md). This test's
job is the monitor, not the transport -- so it drives RemoteJobHandle's
real state-transition methods directly from a real thread, same as
RemoteSSHExecutor.execute_async's own `_run()` would, minus the actual
ssh subprocess.
"""

from __future__ import annotations

import threading
import time

from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.executors.remote_ssh import RemoteJobHandle
from meizex_mrw.dispatch.models import ResourceExecutionRequest, ResourceExecutionResult
from meizex_mrw.statewatch.residual import residual_for_handle


def _make_request(*, timeout_s: float) -> ResourceExecutionRequest:
    return ResourceExecutionRequest(
        mission="statewatch-real-test",
        step=ExecutionStep(
            capability="test.slow_job",
            resource="grid.dell-b.remote-ssh",
            kind="tool",
            execution_boundary="PROCESS",
        ),
        timeout_s=timeout_s,
        step_id="statewatch-real-test-step",
    )


def test_residual_detects_a_real_job_running_past_its_declared_timeout() -> None:
    """A job that takes 0.6s wall-clock but declared a 0.2s timeout_s:
    the monitor should see a divergence once real elapsed time crosses
    that boundary, well before the job actually finishes."""
    request = _make_request(timeout_s=0.2)
    handle = RemoteJobHandle(request)
    start = time.monotonic()

    def _slow_job() -> None:
        handle._mark_started()
        time.sleep(0.6)  # real wall-clock delay, standing in for a slow SSH round-trip
        handle._finish(
            ResourceExecutionResult(
                step_id="statewatch-real-test-step",
                resource_id="grid.dell-b.remote-ssh",
                capability="test.slow_job",
                executor_kind="remote_ssh",
                status="COMPLETED",
                output={"ok": True},
            )
        )

    thread = threading.Thread(target=_slow_job, daemon=True)
    thread.start()

    # Poll like an external monitor would -- no wait(), no knowledge of
    # when the job will actually finish, just real elapsed time.
    saw_running_ok = False
    saw_divergence = False
    deadline = start + 2.0
    while time.monotonic() < deadline and not handle.done():
        elapsed = time.monotonic() - start
        d = residual_for_handle(handle, elapsed_s=elapsed, timeout_s=request.timeout_s)
        if elapsed < 0.2:
            assert d is None, f"unexpected divergence before timeout_s: {d}"
            saw_running_ok = True
        elif d is not None:
            assert d.expected_phase == "should_have_finished"
            assert d.observed_phase == "running"
            saw_divergence = True
        time.sleep(0.05)

    thread.join(timeout=2.0)
    assert handle.done(), "background job never finished -- test setup is broken"
    assert saw_running_ok, "never observed the normal, pre-timeout running phase"
    assert saw_divergence, "monitor never caught the real timeout_s overrun"

    # Once finished, the residual must clear even though the job overran --
    # "finished late" is still a consistent (expected, observed) pair.
    final_elapsed = time.monotonic() - start
    assert residual_for_handle(handle, elapsed_s=final_elapsed, timeout_s=request.timeout_s) is None


def test_residual_stays_quiet_for_a_real_job_that_finishes_on_time() -> None:
    request = _make_request(timeout_s=5.0)
    handle = RemoteJobHandle(request)
    start = time.monotonic()

    def _fast_job() -> None:
        handle._mark_started()
        time.sleep(0.1)
        handle._finish(
            ResourceExecutionResult(
                step_id="statewatch-real-test-step",
                resource_id="grid.dell-b.remote-ssh",
                capability="test.fast_job",
                executor_kind="remote_ssh",
                status="COMPLETED",
                output={"ok": True},
            )
        )

    thread = threading.Thread(target=_fast_job, daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    assert handle.done()

    elapsed = time.monotonic() - start
    assert residual_for_handle(handle, elapsed_s=elapsed, timeout_s=request.timeout_s) is None
