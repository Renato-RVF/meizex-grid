"""Real test: RemoteSSHExecutor.execute() itself now polls STATEWATCH's
residual() while it blocks, and emits a real StepDivergence event to a
real EventStore once a step crosses statewatch_warn_fraction of its own
timeout_s -- WHILE still running, well before the process boundary's
real kill (which fires at the full timeout_s -- see run_payload()).
That gap is the entire point: it is what gives a monitor actual lead
time instead of learning about trouble at the exact same instant the
step is forcibly killed anyway.

This does not open a real SSH connection: the SSH transport itself was
already proven for real against the Grid in NEXT-006..010 (see
MEIZEX_GRID/NEXT.md), and re-proving it here would need a live Grid node.
What's new and untested until now is the polling wiring INSIDE execute()
-- so this test swaps only the spawned command (a real local child
process, real stdin/stdout JSON protocol, real thread, real wall-clock
time) for one that simulates a slow remote job, and drives the executor
through its real, unmodified execute() end to end.

Timing is self-calibrated rather than hardcoded: spawning a real Python
child process on this machine has real, non-trivial overhead (observed
locally: >0.5s just to start the interpreter and hit the sleep line --
antivirus/Windows process-creation cost, not anything this module
controls). Hardcoding "sleep 0.85s, expect done under a 1.0s timeout"
was flaky for exactly that reason: the assertion was really about
wall-clock margins the test had no way to guarantee. Measuring the
actual round-trip once and deriving timeout_s/warn thresholds from it
keeps the test meaningful on a slow CI box or a fast one alike.
"""

from __future__ import annotations

import sys
import textwrap

from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.executors.remote_ssh import RemoteSSHExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest
from meizex_mrw.events.models import StepDivergence
from meizex_mrw.events.store import EventStore


def _slow_child(sleep_s: float) -> str:
    return textwrap.dedent(
        f"""
        import json, sys, time
        sys.stdin.readline()  # consume the JSON payload written to stdin, same as a real child would
        time.sleep({sleep_s})
        print(json.dumps({{"ok": True, "result": {{"slept": {sleep_s}}}}}))
        """
    )


def _make_executor(
    tmp_path, *, timeout_s: float, sleep_s: float, warn_fraction: float = 0.7
) -> tuple[RemoteSSHExecutor, EventStore]:
    executor = RemoteSSHExecutor(
        resource_id="grid.dell-b.remote-ssh",
        host="unused-in-this-test",
        ssh_user="unused",
        ssh_key_path="unused",
        remote_workdir="unused",
        remote_python="unused",
        timeout_s=timeout_s,
        statewatch_poll_s=0.05,
        statewatch_warn_fraction=warn_fraction,
    )
    # Swap the ssh-shaped command for a real local child that simulates a
    # slow remote job -- this is the one substitution; everything else
    # (execute_async, the concurrency gate, RemoteJobHandle, the polling
    # loop in execute() itself) runs completely unmodified.
    executor._command = [sys.executable, "-c", _slow_child(sleep_s)]

    event_store = EventStore(tmp_path / "events.jsonl")
    executor.attach_event_store(event_store)
    return executor, event_store


def _make_request(*, timeout_s: float, run_id: str, step_id: str) -> ResourceExecutionRequest:
    return ResourceExecutionRequest(
        mission="statewatch-wiring-test",
        step=ExecutionStep(
            capability="test.slow_job",
            resource="grid.dell-b.remote-ssh",
            kind="tool",
            execution_boundary="PROCESS",
        ),
        timeout_s=timeout_s,
        run_id=run_id,
        step_id=step_id,
    )


def _measure_spawn_overhead_s(tmp_path) -> float:
    """A trivial, near-instant real round-trip through the exact same
    executor/command machinery, with a generous timeout so it can never
    itself be killed. Its measured latency IS the real spawn overhead on
    this machine right now -- not a guess."""
    executor, _ = _make_executor(tmp_path, timeout_s=30.0, sleep_s=0.0)
    request = _make_request(timeout_s=30.0, run_id="calibration", step_id="calibration")
    result = executor.execute(request)
    assert result.status == "COMPLETED", f"calibration run itself failed: {result}"
    return result.latency_ms / 1000.0


def test_execute_emits_early_divergence_well_before_the_real_kill(tmp_path) -> None:
    overhead_s = _measure_spawn_overhead_s(tmp_path)
    # Target total wall-clock time: measured overhead plus a comfortable
    # 1s of real sleep. Placed at 85% of timeout_s -- inside the
    # (70%, 100%) warning window with real margin on both sides, so this
    # stays robust even if overhead varies a little between runs.
    target_total_s = overhead_s + 1.0
    timeout_s = target_total_s / 0.85
    sleep_s = round(max(target_total_s - overhead_s, 0.05), 3)
    warn_at_s = timeout_s * 0.7

    executor, event_store = _make_executor(tmp_path, timeout_s=timeout_s, sleep_s=sleep_s)
    request = _make_request(timeout_s=timeout_s, run_id="run-divergence-1", step_id="step-divergence-1")

    result = executor.execute(request)

    assert result.status == "COMPLETED", (
        f"job should have finished before the real kill at {timeout_s:.2f}s "
        f"(overhead={overhead_s:.2f}s, sleep={sleep_s:.2f}s): {result}"
    )

    events = event_store.read_all()
    divergences = [e for e in events if isinstance(e, StepDivergence)]
    assert len(divergences) == 1, f"expected exactly one StepDivergence, got {events}"
    d = divergences[0]
    assert d.run_id == "run-divergence-1"
    assert d.step_id == "step-divergence-1"
    assert d.expected_phase == "should_have_finished"
    assert d.observed_phase == "running"
    # Real lead time: the warning landed strictly before the kill deadline,
    # not a same-instant relabeling of the eventual failure.
    assert warn_at_s <= d.elapsed_s < timeout_s, (
        f"divergence should land inside the warning window "
        f"[{warn_at_s:.2f}, {timeout_s:.2f}): got {d.elapsed_s:.2f}"
    )


def test_execute_emits_no_divergence_for_a_real_step_that_finishes_on_time(tmp_path) -> None:
    executor, event_store = _make_executor(tmp_path, timeout_s=10.0, sleep_s=0.1)
    request = _make_request(timeout_s=10.0, run_id="run-divergence-2", step_id="step-divergence-2")

    result = executor.execute(request)

    assert result.status == "COMPLETED"
    events = event_store.read_all()
    divergences = [e for e in events if isinstance(e, StepDivergence)]
    assert divergences == []
