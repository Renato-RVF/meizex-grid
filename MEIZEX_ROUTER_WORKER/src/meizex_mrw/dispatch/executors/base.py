"""Executor Protocol for MRW."""

from typing import Protocol

from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.models import ResourceExecutionRequest, ResourceExecutionResult


class ResourceExecutor(Protocol):
    """Protocol for all heterogeneous resource executors."""

    @property
    def kind(self) -> str:
        """The logical kind of this executor (e.g., 'llm', 'mcp', 'deterministic')."""
        ...

    def can_handle(self, step: ExecutionStep) -> bool:
        """Return True if this executor is capable of handling the given step."""
        ...

    def requires_invocation(self, step: ExecutionStep) -> bool:
        """Whether this executor needs a resolved ToolInvocation on the step.

        Executors that consume ``step.invocation`` (e.g. a deterministic tool
        runner, an MCP tool caller) return True and use the InvocationResolver.
        Executors that operate on the mission text directly (e.g. the LLM
        Worker) return False so the resolver never blocks them.
        """
        ...

    def execute(self, request: ResourceExecutionRequest) -> ResourceExecutionResult:
        """Execute the request and return a structured result."""
        ...
