"""ProcessExecutor — runs an executable capability out-of-process (M9).

This executor is the execution-layer response to the ``execution_boundary``
contract: it decides HOW to apply the process boundary for steps that were
classified ``PROCESS``. The planner/router only described WHAT to execute; this
executor owns subprocess knowledge and keeps it out of the rest of the system.

The child runs with an explicitly allowlisted environment, a hard timeout
enforced by the parent, and a structured JSON transport. A child crash,
non-zero exit, timeout, or transport failure is mapped onto the existing
:class:`~meizex_mrw.dispatch.models.ResourceExecutionResult` contract and can
never raise out of :meth:`execute` — the MRW parent keeps running.
"""

from __future__ import annotations

import sys
import time
import uuid
from typing import Any

from meizex_mrw.capabilities.executor_compat import process_compatible
from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.executors.base import ResourceExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest, ResourceExecutionResult
from meizex_mrw.dispatch.process_boundary import (
    ProcessBoundaryOutcome,
    build_child_environment,
    run_payload,
)
from meizex_mrw.events.models import ResourceUsageMeasured
from meizex_mrw.events.store import EventStore

DEFAULT_PROCESS_TIMEOUT_S = 30.0


class ProcessExecutor(ResourceExecutor):
    """Executes a capability in a real child process."""

    # The dispatcher uses this flag to enforce the execution boundary: steps
    # classified PROCESS may only be handled by boundary-aware executors.
    requires_process_boundary = True

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        timeout_s: float | None = None,
        extra_env: dict[str, str] | None = None,
        event_store: EventStore | None = None,
    ) -> None:
        self._command = command or [
            sys.executable,
            "-m",
            "meizex_mrw.dispatch.process_runner",
        ]
        self._timeout_s = timeout_s or DEFAULT_PROCESS_TIMEOUT_S
        self._extra_env = extra_env or {}
        self._store = event_store

    @property
    def kind(self) -> str:
        return "process"

    def attach_event_store(self, event_store: EventStore) -> None:
        """Point this executor at the run-scoped stream (dispatcher-owned run)."""
        self._store = event_store

    def can_handle(self, step: ExecutionStep) -> bool:
        # Shared contract with the router's pre-selection filter.
        return process_compatible(step.execution_boundary)

    def requires_invocation(self, step: ExecutionStep) -> bool:
        # The process executor forwards step.invocation.arguments to the child.
        # Keep the existing contract (it may use optional args), so do not
        # harden: resolution stays required to preserve current behavior.
        return True

    def execute(self, request: ResourceExecutionRequest) -> ResourceExecutionResult:
        started = time.perf_counter()
        args = (request.step.invocation.arguments if request.step.invocation else {}) or {}
        payload: dict[str, Any] = {
            "capability": request.step.capability,
            "mission": request.mission,
            "previous_output": request.previous_output,
            "args": args,
        }

        timeout = request.timeout_s or self._timeout_s
        outcome = run_payload(
            self._command,
            payload,
            timeout_s=timeout,
            extra_env=self._extra_env,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 3)

        result = self._map_outcome(request, outcome, timeout, latency_ms)
        if self._store is not None:
            self._emit_event(request, result, latency_ms)
        return result

    def _map_outcome(
        self,
        request: ResourceExecutionRequest,
        outcome: ProcessBoundaryOutcome,
        timeout: float,
        latency_ms: float,
    ) -> ResourceExecutionResult:
        evidence = [
            {
                "kind": "process_boundary",
                "status": outcome.status,
                "pid": outcome.pid,
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
            }
        ]

        if outcome.status == "SUCCESS" and outcome.ok:
            return ResourceExecutionResult(
                step_id=request.step_id or f"{request.step.capability}-process",
                resource_id=request.step.resource,
                capability=request.step.capability,
                executor_kind=self.kind,
                status="COMPLETED",
                output=outcome.result,
                evidence=evidence,
                latency_ms=latency_ms,
            )

        if outcome.status == "NONZERO_EXIT" and outcome.exit_code is not None:
            error = f"child process exited with code {outcome.exit_code} (pid={outcome.pid})"
        elif outcome.status == "TIMEOUT":
            error = f"child process exceeded the {timeout}s timeout (pid={outcome.pid})"
        elif outcome.status == "SPAWN_FAILURE":
            error = f"process boundary spawn failure: {outcome.error}"
        else:
            error = outcome.error or "child process failed"
            if outcome.pid is not None:
                error = f"{error} (pid={outcome.pid})"

        return ResourceExecutionResult(
            step_id=request.step_id or f"{request.step.capability}-process",
            resource_id=request.step.resource,
            capability=request.step.capability,
            executor_kind="process",
            status="FAILED",
            output=None,
            evidence=evidence,
            error=error,
            latency_ms=latency_ms,
        )

    def _emit_event(
        self,
        request: ResourceExecutionRequest,
        result: ResourceExecutionResult,
        latency_ms: float,
    ) -> None:
        # M10: the process usage event correlates to the run/step the request
        # belongs to; a standalone executor keeps its own identity as fallback.
        identity = request.run_id or str(uuid.uuid4())
        if result.status == "COMPLETED":
            status = "success"
        elif result.evidence and result.evidence[0].get("status") == "TIMEOUT":
            status = "timeout"
        else:
            status = "error"
        self._store.append(
            ResourceUsageMeasured(
                session_id=identity,
                turn_id=identity,
                run_id=request.run_id,
                step_id=request.step_id,
                resource_type="process",
                operation=f"process:{request.step.capability}",
                duration_ms=latency_ms,
                status=status,  # type: ignore[arg-type]
            )
        )

    def child_environment(self) -> dict[str, str]:
        """Expose the allowlisted environment this executor would hand a child."""
        return build_child_environment(self._extra_env)


__all__ = ["ProcessExecutor"]
