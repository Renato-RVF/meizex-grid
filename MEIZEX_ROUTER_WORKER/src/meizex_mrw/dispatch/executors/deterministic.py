"""Deterministic Executor for safe, predictable processing tasks.

The mrw-deterministic-router capabilities are materialized here with real,
read-only, deterministic work — never a fabricated ``processed: true``:

* ``filesystem_read`` / ``filesystem_discovery`` reuse the same confined
  read-only routines as the MCP filesystem server
  (:class:`~meizex_mrw.mcp_filesystem_readonly.ReadOnlyFilesystem`), driven
  by the resolved :class:`ToolInvocation`;
* ``routing`` / ``classification`` run the real deterministic capability
  pipeline (requirement derivation / task classification) on the mission.

Any other capability fails explicitly with :class:`PlanningError` instead of
pretending it succeeded. No LLM is ever called here.
"""

import time
from pathlib import Path

from meizex_mrw.capabilities.executor_compat import deterministic_compatible
from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.capabilities.requirements import requirements as derive_requirements
from meizex_mrw.dispatch.executors.base import ResourceExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest, ResourceExecutionResult
from meizex_mrw.dispatch.planner import PlanningError
from meizex_mrw.mcp_filesystem_readonly import ReadOnlyFilesystem
from meizex_mrw.task_classifier import classify


class DeterministicExecutor(ResourceExecutor):
    """Executes deterministic routines, requiring no LLM."""

    FILESYSTEM_CAPABILITIES = frozenset({"filesystem_read", "filesystem_discovery"})

    def __init__(self, root: str | Path | None = None) -> None:
        # Root confines every filesystem operation the executor performs.
        self._root = Path(root).resolve() if root is not None else None

    @property
    def kind(self) -> str:
        return "deterministic"

    def can_handle(self, step: ExecutionStep) -> bool:
        # Shared contract with the router's pre-selection filter.
        return deterministic_compatible(step.kind, step.resource)

    def requires_invocation(self, step: ExecutionStep) -> bool:
        # The deterministic runner executes the resolved ToolInvocation, so it
        # must go through the InvocationResolver.
        return True

    def execute(self, request: ResourceExecutionRequest) -> ResourceExecutionResult:
        started = time.perf_counter()
        step_id = request.step_id or f"{request.step.capability}-det"

        try:
            output = self._execute_deterministic(request)
            status = "COMPLETED"
            error = None
        except (PlanningError, ValueError, OSError) as exc:
            output = None
            status = "FAILED"
            error = str(exc)

        latency = round((time.perf_counter() - started) * 1000, 3)

        return ResourceExecutionResult(
            step_id=step_id,
            resource_id=request.step.resource,
            capability=request.step.capability,
            executor_kind=self.kind,
            status=status,
            output=output,
            error=error,
            latency_ms=latency,
        )

    def _execute_deterministic(self, request: ResourceExecutionRequest) -> dict:
        capability = request.step.capability
        if capability in self.FILESYSTEM_CAPABILITIES:
            return self._run_filesystem(request.step)
        if capability == "routing":
            # Real deterministic capability routing on the mission, no LLM.
            return {
                "processed": True,
                "capability": capability,
                "received_input": request.previous_output,
                "requirements": [
                    r.model_dump(mode="json") for r in derive_requirements(request.mission)
                ],
                "note": "Executed deterministically with no LLM.",
            }
        if capability == "classification":
            # Real deterministic task classification on the mission, no LLM.
            return {
                "processed": True,
                "capability": capability,
                "received_input": request.previous_output,
                "classification": classify(request.mission).model_dump(mode="json"),
                "note": "Executed deterministically with no LLM.",
            }
        raise PlanningError(f"no deterministic routine for capability {capability!r}")

    def _run_filesystem(self, step: ExecutionStep) -> dict:
        if self._root is None:
            raise PlanningError("filesystem deterministic execution requires a root (pass --cwd)")
        if not step.invocation or not step.invocation.tool_name:
            raise PlanningError(
                f"filesystem capability {step.capability!r} has no resolved tool invocation"
            )
        tool = step.invocation.tool_name
        args = step.invocation.arguments or {}
        # require_grounding=False: this instance is single-use, one tool call
        # per ExecutionStep, with the path already resolved deterministically
        # from the user's own mission text (InvocationResolver), never a
        # model improvising a path across tool-call rounds -- the hazard
        # path grounding defends against does not apply here.
        fs = ReadOnlyFilesystem(self._root, require_grounding=False)

        if tool == "list_directory":
            data = fs.list_directory_data(args.get("path", "."))
        elif tool == "stat_path":
            data = fs.stat_path_data(args.get("path", ""))
        elif tool == "search_files":
            data = fs.search_files_data(args.get("pattern", ""), args.get("root", ""))
        elif tool == "read_document_bounded":
            path = args.get("path", "")
            text = fs.read_document_bounded_data(path, args.get("max_chars"))
            data = {"path": str(fs._contained(path)), "content": text, "char_count": len(text)}
        else:
            raise PlanningError(f"deterministic filesystem cannot execute tool {tool!r}")

        return {
            "processed": True,
            "capability": step.capability,
            "operation": tool,
            "root": str(self._root),
            "result": data,
            "note": "Executed deterministically with no LLM.",
        }
