"""Resource Dispatcher for composed execution of the capability plan."""

import re
import uuid
from typing import Any

from meizex_mrw.capabilities.models import CapabilityRouteResult
from meizex_mrw.dispatch.executors.base import ResourceExecutor
from meizex_mrw.dispatch.models import (
    AssistanceResponse,
    DispatchResult,
    PendingStateEnvelope,
    ResourceExecutionRequest,
    ResourceExecutionResult,
)
from meizex_mrw.dispatch.planner import InvocationResolver, PlanningError
from meizex_mrw.dispatch.store import InMemoryRouteStore, PendingRouteStore, TokenNotFoundError
from meizex_mrw.events.models import (
    RunCompleted,
    RunEscalated,
    RunStarted,
    StepCompleted,
    StepEscalated,
    StepFailed,
    StepStarted,
)
from meizex_mrw.events.store import EventStore


class ResourceDispatcher:
    """Executes a CapabilityRouteResult's ExecutionPlan sequentially.

    M10 run ownership: the dispatcher (the execution layer) is the owner of
    ``run_id``. A caller may supply one; if absent the dispatcher generates it.
    The dispatcher also owns the generic lifecycle observation trail
    (RunStarted/StepStarted/StepCompleted/StepFailed/StepEscalated/RunEscalated/
    RunCompleted); executors only emit their nature-specific events.
    """

    def __init__(
        self,
        executors: list[ResourceExecutor],
        resolver: InvocationResolver | None = None,
        store: PendingRouteStore | None = None,
        max_escalations: int = 3,
        event_store: EventStore | None = None,
        observations_dir: str | None = None,
    ) -> None:
        self._executors = executors
        self._resolver = resolver or InvocationResolver()
        self._store = store or InMemoryRouteStore()
        self._max_escalations = max_escalations
        # An explicit run-scoped EventStore wins; otherwise the dispatcher
        # resolves the per-run stream itself (observations_dir or the default).
        self._event_store = event_store
        self._observations_dir = observations_dir

    def _emit(self, event_store: EventStore | None, event) -> None:
        if event_store is not None:
            event_store.append(event)

    @staticmethod
    def _step_id(index: int, step) -> str:
        """A stable, run-unique id for one logical step (index + capability)."""
        base = step.capability or "step"
        safe = re.sub(r"[^A-Za-z0-9_-]", "-", base)
        return f"{safe}-{index}"

    def _resolve_run(self, run_id: str | None) -> tuple[str, EventStore | None]:
        """Return (run_id, event_store). The run owns its id: a supplied
        run_id is honoured; otherwise the execution layer generates one.
        The resolved run-scoped stream is also attached to any executor that
        emits nature-specific events, so its events land in the same run."""
        if run_id is None:
            run_id = uuid.uuid4().hex
        if self._event_store is not None:
            store = self._event_store
        else:
            store = EventStore.for_run(run_id, directory=self._observations_dir)
        for ex in self._executors:
            attach = getattr(ex, "attach_event_store", None)
            if attach is not None:
                attach(store)
        return run_id, store

    def dispatch(
        self, route_result: CapabilityRouteResult, *, run_id: str | None = None
    ) -> DispatchResult:
        run_id, event_store = self._resolve_run(run_id)
        session_id = run_id

        if route_result.route_status in {"NO_ELIGIBLE_RESOURCE", "NO_EXECUTABLE_RESOURCE"}:
            self._emit(
                event_store,
                RunStarted(run_id=run_id, session_id=session_id, mission=route_result.mission),
            )
            self._emit(
                event_store,
                RunCompleted(
                    run_id=run_id,
                    session_id=session_id,
                    final_status="FAILED",
                    step_count=0,
                ),
            )
            return DispatchResult(
                mission=route_result.mission,
                status="FAILED",
                final_output=None,
                run_id=run_id,
            )

        self._emit(
            event_store,
            RunStarted(run_id=run_id, session_id=session_id, mission=route_result.mission),
        )
        result = self._execute_loop(
            mission=route_result.mission,
            plan=route_result.execution_plan,
            start_index=0,
            results=[],
            current_output=None,
            route_result=route_result,
            run_id=run_id,
            event_store=event_store,
        )
        if result.status in ("COMPLETED", "FAILED"):
            self._emit(
                event_store,
                RunCompleted(
                    run_id=run_id,
                    session_id=session_id,
                    final_status=result.status,
                    step_count=len(result.step_results),
                ),
            )
        return result

    def _execute_loop(
        self,
        mission: str,
        plan: list,
        start_index: int,
        results: list[ResourceExecutionResult],
        current_output: Any,
        route_result: CapabilityRouteResult,
        escalation_count: int = 0,
        run_id: str | None = None,
        event_store: EventStore | None = None,
    ) -> DispatchResult:
        status = "COMPLETED"
        resume_token = None
        if run_id is None:
            run_id = uuid.uuid4().hex
        session_id = run_id

        for i in range(start_index, len(plan)):
            step = plan[i]
            step_id = self._step_id(i, step)
            executor = self._select_executor(step)
            executor_kind = executor.kind if executor else step.kind

            self._emit(
                event_store,
                StepStarted(
                    run_id=run_id,
                    session_id=session_id,
                    step_id=step_id,
                    index=i,
                    capability=step.capability,
                    resource=step.resource,
                    executor_kind=executor_kind,
                ),
            )

            if not executor:
                res = ResourceExecutionResult(
                    step_id=step_id,
                    resource_id=step.resource,
                    capability=step.capability,
                    executor_kind=step.kind,
                    status="FAILED",
                    error=(
                        f"No executor found for kind '{step.kind}' and resource '{step.resource}'"
                    ),
                )
                results.append(res)
                status = "FAILED"
                self._emit(
                    event_store,
                    StepFailed(
                        run_id=run_id,
                        session_id=session_id,
                        step_id=step_id,
                        index=i,
                        capability=step.capability,
                        resource=step.resource,
                        executor_kind=step.kind,
                        error=res.error,
                    ),
                )
                break

            try:
                needs_invocation = getattr(executor, "requires_invocation", True)
                if callable(needs_invocation):
                    needs_invocation = bool(needs_invocation(step))
                if needs_invocation:
                    step.invocation = self._resolver.resolve(step, mission, current_output)
            except PlanningError as e:
                res = ResourceExecutionResult(
                    step_id=step_id,
                    resource_id=step.resource,
                    capability=step.capability,
                    executor_kind=step.kind,
                    status="ASSISTANCE_REQUIRED",
                    error=str(e),
                    escalation_reason="planning_error",
                )
                results.append(res)
                if escalation_count >= self._max_escalations:
                    res.status = "FAILED"
                    res.error = f"Max escalations ({self._max_escalations}) exceeded."
                    status = "FAILED"
                    self._emit(
                        event_store,
                        StepFailed(
                            run_id=run_id,
                            session_id=session_id,
                            step_id=step_id,
                            index=i,
                            capability=step.capability,
                            resource=step.resource,
                            executor_kind=step.kind,
                            error=res.error,
                        ),
                    )
                    break

                status = "ESCALATED"
                resume_token = f"mrw-exec-{uuid.uuid4().hex[:8]}"
                envelope = PendingStateEnvelope(
                    resume_token=resume_token,
                    run_id=run_id,
                    route_result=route_result,
                    step_index=i,
                    results=results,
                    current_output=current_output,
                    escalation_count=escalation_count + 1,
                )
                self._store.save(resume_token, envelope)
                self._emit(
                    event_store,
                    StepEscalated(
                        run_id=run_id,
                        session_id=session_id,
                        step_id=step_id,
                        index=i,
                        capability=step.capability,
                        resource=step.resource,
                        executor_kind=step.kind,
                        status=res.status,
                        escalation_reason="planning_error",
                    ),
                )
                self._emit(
                    event_store,
                    RunEscalated(
                        run_id=run_id,
                        session_id=session_id,
                        resume_token=resume_token,
                        step_index=i,
                        escalation_count=escalation_count + 1,
                    ),
                )
                break

            request = ResourceExecutionRequest(
                mission=mission,
                step=step,
                previous_output=current_output,
                run_id=run_id,
                step_id=step_id,
            )

            res = executor.execute(request)
            results.append(res)

            if res.status == "COMPLETED":
                current_output = res.output
                self._emit(
                    event_store,
                    StepCompleted(
                        run_id=run_id,
                        session_id=session_id,
                        step_id=step_id,
                        index=i,
                        capability=step.capability,
                        resource=step.resource,
                        executor_kind=executor_kind,
                        status=res.status,
                        latency_ms=res.latency_ms,
                    ),
                )
            elif res.status in ("ASSISTANCE_REQUIRED", "APPROVAL_REQUIRED"):
                if escalation_count >= self._max_escalations:
                    res.status = "FAILED"
                    res.error = f"Max escalations ({self._max_escalations}) exceeded."
                    status = "FAILED"
                    self._emit(
                        event_store,
                        StepFailed(
                            run_id=run_id,
                            session_id=session_id,
                            step_id=step_id,
                            index=i,
                            capability=step.capability,
                            resource=step.resource,
                            executor_kind=executor_kind,
                            error=res.error,
                        ),
                    )
                    break

                status = "ESCALATED"
                resume_token = f"mrw-exec-{uuid.uuid4().hex[:8]}"
                envelope = PendingStateEnvelope(
                    resume_token=resume_token,
                    run_id=run_id,
                    route_result=route_result,
                    step_index=i,
                    results=results,
                    current_output=current_output,
                    escalation_count=escalation_count + 1,
                )
                self._store.save(resume_token, envelope)
                self._emit(
                    event_store,
                    StepEscalated(
                        run_id=run_id,
                        session_id=session_id,
                        step_id=step_id,
                        index=i,
                        capability=step.capability,
                        resource=step.resource,
                        executor_kind=executor_kind,
                        status=res.status,
                        escalation_reason=res.escalation_reason,
                    ),
                )
                self._emit(
                    event_store,
                    RunEscalated(
                        run_id=run_id,
                        session_id=session_id,
                        resume_token=resume_token,
                        step_index=i,
                        escalation_count=escalation_count + 1,
                    ),
                )
                break
            else:
                # FAILED
                status = "FAILED"
                self._emit(
                    event_store,
                    StepFailed(
                        run_id=run_id,
                        session_id=session_id,
                        step_id=step_id,
                        index=i,
                        capability=step.capability,
                        resource=step.resource,
                        executor_kind=executor_kind,
                        error=res.error or f"step {step_id} failed",
                    ),
                )
                break

        return DispatchResult(
            mission=mission,
            status=status,
            step_results=results,
            final_output=current_output,
            resume_token=resume_token,
            run_id=run_id,
        )

    def resume(
        self,
        token: str,
        response: AssistanceResponse,
        *,
        run_id: str | None = None,
    ) -> DispatchResult:
        try:
            state = self._store.consume(token)
        except TokenNotFoundError:
            return DispatchResult(
                mission="unknown",
                status="FAILED",
                final_output="Invalid, consumed, or unknown resume token.",
            )
        except Exception as e:
            return DispatchResult(
                mission="unknown",
                status="FAILED",
                final_output=f"Failed to load resume state: {e}",
            )

        # The run owns its id: a resume MUST reopen the same stream the
        # dispatch wrote to, so the envelope's run_id wins over any caller id.
        run_id = state.run_id or run_id or uuid.uuid4().hex
        _, event_store = self._resolve_run(run_id)
        session_id = run_id

        if response.status != "ASSISTANCE_COMPLETED":
            return DispatchResult(
                mission=state.route_result.mission,
                status="FAILED",
                final_output=f"Assistance rejected or failed: {response.status}",
                run_id=run_id,
            )

        # Evidence safety: downgrade any VALIDATED to FOUND_NOT_TESTED
        for ev in response.evidence:
            if ev.get("status") == "VALIDATED":
                ev["status"] = "FOUND_NOT_TESTED"
        for dr in response.discovered_resources:
            if dr.get("status") == "VALIDATED":
                dr["status"] = "FOUND_NOT_TESTED"

        # Patch the escalated step and observe its completion. The step_id is
        # recomputed from the plan index so it matches the StepStarted the
        # original dispatch emitted (stable across the escalation/retry).
        last_res = state.results[-1]
        last_res.status = "COMPLETED"
        last_res.output = response.answer or "Assistance provided."
        last_res.evidence.extend(response.evidence)
        plan = state.route_result.execution_plan
        escalated_step = plan[state.step_index]
        step_id = self._step_id(state.step_index, escalated_step)
        self._emit(
            event_store,
            StepCompleted(
                run_id=run_id,
                session_id=session_id,
                step_id=step_id,
                index=state.step_index,
                capability=escalated_step.capability,
                resource=escalated_step.resource,
                executor_kind=last_res.executor_kind,
                status="COMPLETED",
            ),
        )

        result = self._execute_loop(
            mission=state.route_result.mission,
            plan=plan,
            start_index=state.step_index + 1,
            results=state.results,
            current_output=last_res.output,
            route_result=state.route_result,
            escalation_count=state.escalation_count,
            run_id=run_id,
            event_store=event_store,
        )
        if result.status in ("COMPLETED", "FAILED"):
            self._emit(
                event_store,
                RunCompleted(
                    run_id=run_id,
                    session_id=session_id,
                    final_status=result.status,
                    step_count=len(result.step_results),
                ),
            )
        return result

    def _select_executor(self, step) -> ResourceExecutor | None:
        for ex in self._executors:
            # Execution boundary policy, centralized at the execution layer:
            # a step whose contract requires an explicit process boundary must
            # NOT be handled by an in-process executor, no matter how capable
            # it looks. Only executors that explicitly declare the boundary
            # (e.g. ProcessExecutor) may run it.
            if step.execution_boundary == "PROCESS" and not getattr(
                ex, "requires_process_boundary", False
            ):
                continue
            if ex.can_handle(step):
                return ex
        return None
