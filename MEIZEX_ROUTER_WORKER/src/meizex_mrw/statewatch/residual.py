"""STATEWATCH residual — expected-vs-observed state divergence detection.

Prompted by VoLo (NVIDIA/Michigan, CoRL 2026, arXiv:2606.07723): a physical
orchestrator that separates execution from monitoring by continuously
comparing an EXPECTED state (predicted from what the orchestrator already
knows) against the OBSERVED state (what the world actually reports), and
treats the difference — the residual — as the trigger for
halt/recover/replan. VoLo applies this to a robot's pose and grasp state;
this module applies the same pattern to an in-flight job handle
(:class:`~meizex_mrw.dispatch.executors.remote_ssh.RemoteJobHandle` today,
anything with the same shape tomorrow).

The point is NOT "detect timeout" — a timeout is already handled by
``RemoteJobHandle.wait(timeout)`` raising, reactively, to whichever caller
happens to be blocked on it. The point is a residual a MONITOR can poll
from the outside, independent of any caller being blocked at all: "given
what I already know (elapsed time, whether it started, its own
timeout_s), is what I'm observing right now still consistent with that,
or has it diverged?" That question is answerable long before anything
times out, and by something that never called wait() in the first place.

Deliberately NOT here: the recovery/halt decision itself, retries,
replanning. This module only produces the residual signal — same
separation of concerns the source material argues for (the monitor is not
the recovery policy).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class JobHandleLike(Protocol):
    """Structural shape this module needs from a job handle — deliberately
    NOT importing RemoteJobHandle, so this stays usable against anything
    with the same three observable facts, not coupled to the Grid/SSH
    transport."""

    def started(self) -> bool: ...
    def done(self) -> bool: ...


@dataclass(frozen=True)
class Divergence:
    """A residual: what phase the job SHOULD be in, versus what it
    actually IS in, at the moment this was computed."""

    expected_phase: str
    observed_phase: str
    elapsed_s: float

    def __str__(self) -> str:  # pragma: no cover - trivial
        return (
            f"divergence at {self.elapsed_s:.1f}s: "
            f"expected={self.expected_phase!r} observed={self.observed_phase!r}"
        )


def expected_phase(
    *,
    started: bool,
    elapsed_s: float,
    timeout_s: float | None,
    queue_grace_s: float,
    warn_fraction: float = 1.0,
) -> str:
    """What phase a well-behaved job should be in by now, given only what
    was already known when it was submitted (its own timeout_s) — no
    observation of the job itself goes into this, on purpose: mixing
    observed data into the expectation would make divergence undetectable
    by construction.

    ``warn_fraction`` (0 < f <= 1) is the point along the way to
    ``timeout_s`` where "still running" stops being expected. The default,
    1.0, is the literal deadline — but a job's own timeout_s is usually
    also the moment the process boundary KILLS it (see
    ``ProcessExecutor.execute``/``run_payload``), so waiting for
    ``elapsed_s >= timeout_s`` gives a monitor no lead time at all: the
    divergence and the kill land at essentially the same instant. Passing
    e.g. ``warn_fraction=0.7`` flags "should have finished" at 70% of the
    way to the deadline — an actual EARLY warning, which is the entire
    point of watching a residual instead of just waiting for the eventual
    failure. This mirrors VoLo's monitor firing well before a rollout is
    forcibly halted, not at the same moment."""
    if not started:
        return "queued" if elapsed_s < queue_grace_s else "queue_stuck"
    if timeout_s is None or elapsed_s < timeout_s * warn_fraction:
        return "running"
    return "should_have_finished"


def observed_phase(*, started: bool, done: bool) -> str:
    """What the handle actually reports right now."""
    if done:
        return "finished"
    return "running" if started else "queued"

_CONSISTENT_PAIRS = {
    ("queued", "queued"),
    ("running", "running"),
    ("running", "finished"),
    ("should_have_finished", "finished"),
}


def residual(
    *,
    started: bool,
    done: bool,
    elapsed_s: float,
    timeout_s: float | None,
    queue_grace_s: float = 5.0,
    warn_fraction: float = 1.0,
) -> Divergence | None:
    """Pure function: compute the residual from primitive facts. No
    dependency on RemoteJobHandle or any other concrete type — this is
    the reusable core; :func:`residual_for_handle` is the thin adapter."""
    exp = expected_phase(
        started=started,
        elapsed_s=elapsed_s,
        timeout_s=timeout_s,
        queue_grace_s=queue_grace_s,
        warn_fraction=warn_fraction,
    )
    obs = observed_phase(started=started, done=done)
    if (exp, obs) in _CONSISTENT_PAIRS:
        return None
    return Divergence(expected_phase=exp, observed_phase=obs, elapsed_s=elapsed_s)


def residual_for_handle(
    handle: JobHandleLike,
    *,
    elapsed_s: float,
    timeout_s: float | None,
    queue_grace_s: float = 5.0,
    warn_fraction: float = 1.0,
) -> Divergence | None:
    """Adapter: pull started()/done() off a real job handle and delegate
    to the pure :func:`residual`. ``timeout_s`` is passed explicitly
    rather than read off ``handle.request`` so this module never needs to
    import ``ResourceExecutionRequest`` — it only ever touches the two
    methods declared on :class:`JobHandleLike`."""
    return residual(
        started=handle.started(),
        warn_fraction=warn_fraction,
        done=handle.done(),
        elapsed_s=elapsed_s,
        timeout_s=timeout_s,
        queue_grace_s=queue_grace_s,
    )


__all__ = [
    "Divergence",
    "JobHandleLike",
    "expected_phase",
    "observed_phase",
    "residual",
    "residual_for_handle",
]
