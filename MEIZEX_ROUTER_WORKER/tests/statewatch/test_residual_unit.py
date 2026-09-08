"""Pure-function tests for meizex_mrw.statewatch.residual.

No RemoteJobHandle, no threads, no SSH -- just the expected/observed phase
logic itself, deterministic on hand-picked (started, done, elapsed_s,
timeout_s) inputs.
"""

from __future__ import annotations

from meizex_mrw.statewatch.residual import Divergence, residual


def test_queued_and_still_queued_is_consistent() -> None:
    assert residual(started=False, done=False, elapsed_s=1.0, timeout_s=30.0) is None


def test_queued_past_grace_period_is_divergent() -> None:
    d = residual(
        started=False, done=False, elapsed_s=10.0, timeout_s=30.0, queue_grace_s=5.0
    )
    assert d == Divergence(
        expected_phase="queue_stuck", observed_phase="queued", elapsed_s=10.0
    )


def test_running_within_timeout_is_consistent() -> None:
    assert residual(started=True, done=False, elapsed_s=5.0, timeout_s=30.0) is None


def test_running_with_no_timeout_configured_is_always_consistent() -> None:
    assert residual(started=True, done=False, elapsed_s=99_999.0, timeout_s=None) is None


def test_finished_while_running_normally_is_consistent() -> None:
    assert residual(started=True, done=True, elapsed_s=5.0, timeout_s=30.0) is None


def test_finished_exactly_at_timeout_is_consistent() -> None:
    assert residual(started=True, done=True, elapsed_s=30.0, timeout_s=30.0) is None


def test_still_running_past_its_own_timeout_is_divergent() -> None:
    d = residual(started=True, done=False, elapsed_s=45.0, timeout_s=30.0)
    assert d == Divergence(
        expected_phase="should_have_finished", observed_phase="running", elapsed_s=45.0
    )


def test_divergence_str_is_informative() -> None:
    d = residual(started=True, done=False, elapsed_s=45.0, timeout_s=30.0)
    assert d is not None
    text = str(d)
    assert "should_have_finished" in text
    assert "running" in text
    assert "45.0" in text
