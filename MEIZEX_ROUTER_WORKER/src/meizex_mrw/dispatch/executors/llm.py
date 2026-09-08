"""LLM Executor acting as an adapter for the existing Worker engine."""

import json
import time
from collections.abc import Callable

from meizex_mrw.capabilities.executor_compat import llm_compatible
from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.executors.base import ResourceExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest, ResourceExecutionResult
from meizex_mrw.events.store import EventStore
from meizex_mrw.mcp.catalog import ToolOverride
from meizex_mrw.mcp.client import MCPClient
from meizex_mrw.worker.engine import Worker


class LLMExecutor(ResourceExecutor):
    """Executes a request via the existing local LLM Worker."""

    def __init__(
        self,
        event_store: EventStore | None = None,
        mcp_client: MCPClient | None = None,
        provider_factory: Callable | None = None,
        tool_overrides: dict[str, ToolOverride] | None = None,
    ) -> None:
        self._store = event_store
        self._mcp_client = mcp_client
        self._provider_factory = provider_factory
        self._tool_overrides = tool_overrides

    @property
    def kind(self) -> str:
        return "llm"

    def attach_event_store(self, event_store: EventStore) -> None:
        """Point this executor at the run-scoped stream (dispatcher-owned run)."""
        self._store = event_store

    def can_handle(self, step: ExecutionStep) -> bool:
        # Shared contract with the router's pre-selection filter.
        return llm_compatible(step.kind)

    def requires_invocation(self, step: ExecutionStep) -> bool:
        # The LLM executor drives the Worker on the mission text; it never
        # consumes a deterministic ToolInvocation. Returning False means the
        # InvocationResolver must not block an LLM step (e.g. general_reasoning).
        return False

    def execute(self, request: ResourceExecutionRequest) -> ResourceExecutionResult:
        if self._store is None:
            raise RuntimeError(
                "LLMExecutor requires an event_store (attach via dispatcher or constructor)."
            )
        started = time.perf_counter()

        mission_text = request.mission
        if request.previous_output:
            context_str = request.previous_output
            if isinstance(context_str, dict):
                context_str = json.dumps(context_str, ensure_ascii=False)
            mission_text = f"{mission_text}\n\n[CONTEXTO PREVIO]\n{context_str}"

        # Determine profile based on resource or fallback to default
        profile_name = "filesystem_read"  # default

        worker = Worker(
            profile_name=profile_name,
            mcp_client=self._mcp_client,
            event_store=self._store,
            provider_factory=self._provider_factory,
            tool_overrides=self._tool_overrides,
            run_id=request.run_id,
            step_id=request.step_id,
        )

        result = worker.run(mission_text)

        status = "COMPLETED" if result.status == "completed" else "FAILED"
        if result.metrics.get("policy_blocks", 0) > 0 and status == "FAILED":
            status = "APPROVAL_REQUIRED"

        latency = round((time.perf_counter() - started) * 1000, 3)

        return ResourceExecutionResult(
            step_id=request.step_id or f"{request.step.capability}-llm",
            resource_id=request.step.resource,
            capability=request.step.capability,
            executor_kind=self.kind,
            status=status,
            output=result.result,
            error=result.error,
            latency_ms=latency,
        )
