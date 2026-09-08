"""MCP Executor for single-tool read-only execution."""

import time

from meizex_mrw.capabilities.executor_compat import mcp_compatible
from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.executors.base import ResourceExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest, ResourceExecutionResult
from meizex_mrw.mcp.client import MCPClient, MCPError


class MCPExecutor(ResourceExecutor):
    """Executes an MCP tool."""

    def __init__(self, client: MCPClient | None = None) -> None:
        self._client = client

    @property
    def kind(self) -> str:
        return "mcp"

    def can_handle(self, step: ExecutionStep) -> bool:
        # Shared contract with the router's pre-selection filter.
        return mcp_compatible(step.kind, step.resource)

    def requires_invocation(self, step: ExecutionStep) -> bool:
        # The MCP executor calls the tool named by the resolved ToolInvocation,
        # so it must go through the InvocationResolver.
        return True

    def execute(self, request: ResourceExecutionRequest) -> ResourceExecutionResult:
        started = time.perf_counter()

        if self._client is None:
            return ResourceExecutionResult(
                step_id=f"{request.step.capability}-mcp",
                resource_id=request.step.resource,
                capability=request.step.capability,
                executor_kind=self.kind,
                status="FAILED",
                error="MCP client is not initialized or configured.",
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )

        started_client = False
        try:
            if self._client.process is None:
                self._client.start()
                started_client = True

            if not request.step.invocation or not request.step.invocation.tool_name:
                raise MCPError("ExecutionStep missing valid ToolInvocation (tool_name).")

            tool_name = request.step.invocation.tool_name
            arguments = request.step.invocation.arguments

            result_text = self._client.call_tool(tool_name, arguments)
            status = "COMPLETED"
            error = None

        except MCPError as exc:
            status = "FAILED"
            error = str(exc)
            result_text = None
        finally:
            if started_client:
                self._client.stop()

        latency = round((time.perf_counter() - started) * 1000, 3)

        return ResourceExecutionResult(
            step_id=f"{request.step.capability}-mcp",
            resource_id=request.step.resource,
            capability=request.step.capability,
            executor_kind=self.kind,
            status=status,
            output=result_text,
            error=error,
            latency_ms=latency,
        )
