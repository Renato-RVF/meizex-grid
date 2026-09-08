"""Explicit Execution Planner and Invocation Resolver."""

from typing import Any

from meizex_mrw.capabilities.models import ExecutionStep, ToolInvocation


class PlanningError(Exception):
    """Raised when an explicit execution plan cannot be resolved."""


class InvocationResolver:
    """Resolves abstract capabilities into concrete tool invocations dynamically."""

    # Deterministic capability -> concrete read-only tool invocation. The
    # explicit small table is the source of truth for what this resolver can
    # materialize; anything else fails honestly so the Dispatcher can escalate.
    _CAPABILITY_INVOCATIONS: dict[str, ToolInvocation] = {
        "filesystem_read": ToolInvocation(
            tool_name="list_directory",
            arguments={"path": "."},
        ),
        "filesystem_discovery": ToolInvocation(
            tool_name="list_directory",
            arguments={"path": "."},
        ),
    }

    def resolve(self, step: ExecutionStep, mission: str, previous_output: Any) -> ToolInvocation:
        """Resolve the deterministic ToolInvocation for a given step.

        Raises:
            PlanningError: if the capability cannot be resolved to a known tool,
                or if required arguments cannot be constructed.
        """
        invocation = self._CAPABILITY_INVOCATIONS.get(step.capability)
        if invocation is not None:
            return invocation

        # If the output from a previous step explicitly returns what tool to run next
        # (which is currently the debt we are clearing from MCPExecutor).
        if isinstance(previous_output, dict):
            tool_name = previous_output.get("tool_name")
            if tool_name:
                return ToolInvocation(
                    tool_name=tool_name,
                    arguments=previous_output.get("arguments", {}),
                )

        # Fallback to failing cleanly so the Dispatcher can escalate to ASSISTANCE_REQUIRED
        raise PlanningError(
            f"Cannot deterministically resolve invocation for capability {step.capability!r}"
        )
