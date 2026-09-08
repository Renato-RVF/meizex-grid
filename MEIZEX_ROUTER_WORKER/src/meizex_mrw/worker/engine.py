"""Worker engine — one request in, one WorkerResult out.

The single place that runs a full turn:

    classify -> profile -> model route -> tool select -> context build
    -> infer -> tool calls (policy-gated) -> verify -> result

Every step emits an event into the append-only store (reusing the existing
typed events — no schema inflation). The tool policy is enforced here, not
by the model: only tools that are read-only AND were actually offered are
auto-approved; anything else is rejected via HITLRejected and never
reaches the MCP server. Limits are hard: max_tool_rounds and max_retries;
crossing either fails the turn explicitly instead of looping.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from meizex_mrw import (
    deterministic_arithmetic,
    deterministic_code_trace,
    deterministic_counting,
    deterministic_dates_units,
    deterministic_sequences_logic,
    escalation,
    model_router,
    profiles,
    task_classifier,
    tool_router,
)
from meizex_mrw.context_builder import ContextBuilder
from meizex_mrw.context_contradiction import check_context_contradiction
from meizex_mrw.events.models import (
    AssistantMessage,
    AuditCompleted,
    AuditRequested,
    ExecutionProfileSelected,
    HITLApproved,
    HITLRejected,
    InferenceCompleted,
    InferenceRequested,
    ModelRouteSelected,
    ResourceUsageMeasured,
    TaskClassified,
    ToolExecuted,
    ToolFailed,
    ToolRequested,
    ToolsSelected,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    VerificationCompleted,
)
from meizex_mrw.events.store import EventStore
from meizex_mrw.format_constraint import check_format_constraint
from meizex_mrw.mcp.catalog import ToolCatalog, ToolOverride
from meizex_mrw.mcp.client import MCPClient, MCPError
from meizex_mrw.profiles.schema import ExecutionProfile
from meizex_mrw.providers.base import InferenceProvider, ProviderError, ToolSpec
from meizex_mrw.request_policy.models import RequestClass, RequestEffectIntent
from meizex_mrw.request_policy.service import evaluate as evaluate_request_policy
from meizex_mrw.verifier import ExecutedTool, Verifier
from meizex_mrw.worker.result import RouteInfo, WorkerResult

logger = logging.getLogger("meizex_mrw.worker")

ProviderFactory = Callable[[ExecutionProfile], InferenceProvider]

DEFAULT_SYSTEM_INSTRUCTION = (
    "You are the MEIZEX Router Worker (MRW), a local-first task executor. "
    "Complete the user's request by calling tools only when needed. "
    "Only call tools that are offered to you, and call them with exact "
    "arguments from the tool schemas. Never invent tool results, never "
    "claim to have read a file that you did not actually read, and never "
    "attempt write, delete, or shell operations. "
    "When exploring unknown filesystem structures, you MUST use discovery "
    "tools (stat_path, list_directory, search_files) before attempting to read "
    "files, otherwise the read will be rejected. For long files, use "
    "read_document_range to paginate. "
    "When you have enough information, answer directly and concisely in "
    "Portuguese, citing only evidence that came from real tool results."
)


DEFAULT_MAX_TOOL_ROUNDS = 3


class Worker:
    def __init__(
        self,
        *,
        profile_name: str,
        mcp_client: MCPClient | None,
        event_store: EventStore,
        provider_factory: ProviderFactory | None = None,
        tool_overrides: dict[str, ToolOverride] | None = None,
        mcp_server_name: str = "mcp",
        system_instruction: str = DEFAULT_SYSTEM_INSTRUCTION,
        max_tool_rounds: int | None = None,
        max_retries: int = 1,
        run_id: str | None = None,
        step_id: str | None = None,
        auditor_profile_name: str | None = None,
    ) -> None:
        self._profile_name = profile_name
        self._mcp_client = mcp_client
        self._store = event_store
        self._provider_factory = provider_factory
        self._tool_overrides = tool_overrides or {}
        self._mcp_server_name = mcp_server_name
        # None means "not specified": the profile's max_tool_rounds governs,
        # falling back to DEFAULT_MAX_TOOL_ROUNDS. An explicit value always
        # wins (backward compatible with callers that pass max_tool_rounds=3).
        self._max_tool_rounds = max_tool_rounds
        self._max_retries = max_retries
        self._builder = ContextBuilder(system_instruction=system_instruction)
        self._verifier = Verifier()
        self._run_id = run_id
        self._step_id = step_id
        # ESCALATE_UNCERTAINTY hook (Achado 60/62): when set, an exhausted
        # RETRY or an outright FAIL is escalated once to this profile before
        # the turn is reported failed. None (the default) preserves today's
        # behavior exactly -- no auditor call, fail immediately, same as
        # every existing caller/test expects.
        self._auditor_profile_name = auditor_profile_name

    def _tool_round_limit(self, profile: ExecutionProfile) -> int:
        """Effective tool-round cap: explicit constructor value, else the
        profile's max_tool_rounds, else the module default. Always a positive
        finite cap so loop protection is never disabled."""
        if self._max_tool_rounds is not None:
            return self._max_tool_rounds
        if getattr(profile, "max_tool_rounds", None) is not None:
            return profile.max_tool_rounds  # type: ignore[return-value]
        return DEFAULT_MAX_TOOL_ROUNDS

    def run(self, request: str) -> WorkerResult:
        """Run a full turn and return its WorkerResult."""
        session_id = str(uuid.uuid4())
        events: list[dict[str, Any]] = []

        def emit(event: Any) -> None:
            # M10: stamp run/step correlation on every turn event when the
            # worker is running inside a logical MRW run.
            if self._run_id is not None:
                event.run_id = self._run_id
            if self._step_id is not None:
                event.step_id = self._step_id
            self._store.append(event)
            events.append(event.model_dump(mode="json"))

        started_turn = TurnStarted(session_id=session_id, user_message=request)
        turn_id = started_turn.id
        emit(started_turn)
        start_wall = time.perf_counter()

        task = task_classifier.classify(request)
        emit(
            TaskClassified(
                session_id=turn_id,
                turn_id=turn_id,
                task_type=task.task_type,
                confidence=task.confidence,
                signals=task.signals,
            )
        )

        profile = profiles.get(self._profile_name)
        emit(
            ExecutionProfileSelected(
                session_id=turn_id,
                turn_id=turn_id,
                profile_name=profile.name,
                provider=profile.provider,
                model=profile.model,
                cloud_allowed=profile.cloud_allowed,
            )
        )

        route = model_router.route(profile)
        emit(
            ModelRouteSelected(
                session_id=turn_id,
                turn_id=turn_id,
                provider=route.provider,
                model=route.model,
                reason=route.reason,
                cloud_allowed=route.cloud_allowed,
            )
        )

        tools_used: list[str] = []
        executed: list[ExecutedTool] = []
        tool_rounds = 0
        retries_left = self._max_retries
        policy_blocks = 0
        inference_calls = 0
        inference_latency_ms = 0.0
        token_totals: dict[str, int] = {}
        verification = None
        final_content = ""

        def fail(reason: str) -> WorkerResult:
            emit(TurnFailed(session_id=turn_id, turn_id=turn_id, reason=reason))
            return WorkerResult(
                status="failed",
                task_type=task.task_type,
                profile=profile.name,
                route=RouteInfo(**route.model_dump()),
                tools_used=tools_used,
                verification=verification.verdict if verification else None,
                result=final_content,
                error=reason,
                metrics=metrics(),
                events=events,
            )

        def metrics() -> dict[str, Any]:
            base: dict[str, Any] = {
                "duration_ms": round((time.perf_counter() - start_wall) * 1000, 3),
                "tool_rounds": tool_rounds,
                "policy_blocks": policy_blocks,
                "inference_calls": inference_calls,
                "inference_latency_ms": round(inference_latency_ms, 3),
            }
            base.update(token_totals)
            return base

        # REQUEST-LEVEL GATE, before any provider/tool work: a natural-language
        # request whose own wording signals a mutating effect (delete/write/
        # create/etc.) is rejected outright when the profile forbids writes,
        # the same write_allowed flag tool_router already enforces per-tool
        # (see tool_router.py). This is a cheaper, earlier, defense-in-depth
        # layer on top of that — it never replaces the per-tool check, since
        # a MUTATING-worded request could still resolve to no tool call at
        # all, and a request with no mutating wording could still request a
        # mutating tool by name.
        policy_decision = evaluate_request_policy(request)

        # DETERMINISTIC ARITHMETIC, before any provider call: request_policy
        # already classifies this as RequestClass.ARITHMETIC with
        # ModelDirectAnswer.FORBIDDEN -- the model is not supposed to answer
        # it -- but until now nothing implemented that promise, so these
        # requests fell through to the LLM anyway. Confirmed wrong in a real
        # stress test (47*89 -> 4193 instead of 4183, 123*456 -> 46,778
        # instead of 56,088), and the structural Verifier has no way to catch
        # a wrong-but-well-formed numeric answer. Compute it here instead;
        # deterministic_arithmetic.compute() returns None for anything it
        # cannot safely evaluate (e.g. division by zero), in which case this
        # falls through to the normal inference path unchanged.
        if policy_decision.request_class == RequestClass.ARITHMETIC:
            arithmetic_result = deterministic_arithmetic.compute(request)
            if arithmetic_result is not None:
                emit(
                    AssistantMessage(
                        session_id=turn_id, turn_id=turn_id, content=arithmetic_result.formatted
                    )
                )
                emit(
                    TurnCompleted(
                        session_id=turn_id, turn_id=turn_id, detail="deterministic arithmetic"
                    )
                )
                return WorkerResult(
                    status="completed",
                    task_type=task.task_type,
                    profile=profile.name,
                    route=RouteInfo(**route.model_dump()),
                    tools_used=[],
                    verification=None,
                    result=arithmetic_result.formatted,
                    metrics=metrics(),
                    events=events,
                )

        # DETERMINISTIC TEXT COUNTING — same rationale as arithmetic above:
        # "quantas letras tem X" has exactly one correct answer, and a real
        # stress test caught a small local model getting it wrong
        # ("PROGRAMACAO" -> 20 letters instead of 11). Falls through
        # unchanged when the phrasing isn't one of the narrow patterns
        # deterministic_counting.compute() recognizes.
        if policy_decision.request_class == RequestClass.TEXT_COUNTING:
            counting_result = deterministic_counting.compute(request)
            if counting_result is not None:
                emit(
                    AssistantMessage(
                        session_id=turn_id, turn_id=turn_id, content=counting_result.formatted
                    )
                )
                emit(
                    TurnCompleted(
                        session_id=turn_id, turn_id=turn_id, detail="deterministic counting"
                    )
                )
                return WorkerResult(
                    status="completed",
                    task_type=task.task_type,
                    profile=profile.name,
                    route=RouteInfo(**route.model_dump()),
                    tools_used=[],
                    verification=None,
                    result=counting_result.formatted,
                    metrics=metrics(),
                    events=events,
                )

        # DETERMINISTIC DATE/UNIT COMPUTATION — same rationale as arithmetic
        # and counting above: "quantos dias há entre X e Y" and "quantos km
        # são N milhas" each have exactly one mechanically correct answer.
        # Falls through unchanged when the phrasing isn't one of the narrow
        # patterns deterministic_dates_units.compute() recognizes.
        if policy_decision.request_class in (
            RequestClass.DATE_COMPUTATION,
            RequestClass.UNIT_CONVERSION,
        ):
            date_unit_result = deterministic_dates_units.compute(request)
            if date_unit_result is not None:
                emit(
                    AssistantMessage(
                        session_id=turn_id, turn_id=turn_id, content=date_unit_result.formatted
                    )
                )
                emit(
                    TurnCompleted(
                        session_id=turn_id, turn_id=turn_id, detail="deterministic date/unit"
                    )
                )
                return WorkerResult(
                    status="completed",
                    task_type=task.task_type,
                    profile=profile.name,
                    route=RouteInfo(**route.model_dump()),
                    tools_used=[],
                    verification=None,
                    result=date_unit_result.formatted,
                    metrics=metrics(),
                    events=events,
                )

        # DETERMINISTIC SEQUENCE ANALYSIS / BOOLEAN LOGIC — same rationale as
        # the executors above: sorting/min/max/next-term and AND/OR/NOT/XOR
        # evaluation each have exactly one mechanically correct answer.
        # Falls through unchanged when the phrasing or expression isn't one
        # of the narrow patterns deterministic_sequences_logic.compute()
        # recognizes (e.g. a non-arithmetic/geometric sequence, or a
        # boolean expression mixing XOR with AND/OR chains).
        if policy_decision.request_class in (
            RequestClass.SEQUENCE_ANALYSIS,
            RequestClass.BOOLEAN_LOGIC,
        ):
            sequence_logic_result = deterministic_sequences_logic.compute(request)
            if sequence_logic_result is not None:
                emit(
                    AssistantMessage(
                        session_id=turn_id,
                        turn_id=turn_id,
                        content=sequence_logic_result.formatted,
                    )
                )
                emit(
                    TurnCompleted(
                        session_id=turn_id,
                        turn_id=turn_id,
                        detail="deterministic sequence/logic",
                    )
                )
                return WorkerResult(
                    status="completed",
                    task_type=task.task_type,
                    profile=profile.name,
                    route=RouteInfo(**route.model_dump()),
                    tools_used=[],
                    verification=None,
                    result=sequence_logic_result.formatted,
                    metrics=metrics(),
                    events=events,
                )

        # DETERMINISTIC CODE TRACE (Achado 73, category S): "what does
        # this code print" has exactly one correct answer, computed by
        # actually tracing it (restricted AST, never eval()/exec()).
        # Falls through unchanged when the code uses anything outside the
        # small supported subset.
        if policy_decision.request_class == RequestClass.CODE_TRACE:
            code_trace_result = deterministic_code_trace.compute(request)
            if code_trace_result is not None:
                emit(
                    AssistantMessage(
                        session_id=turn_id,
                        turn_id=turn_id,
                        content=code_trace_result.formatted,
                    )
                )
                emit(
                    TurnCompleted(
                        session_id=turn_id,
                        turn_id=turn_id,
                        detail="deterministic code trace",
                    )
                )
                return WorkerResult(
                    status="completed",
                    task_type=task.task_type,
                    profile=profile.name,
                    route=RouteInfo(**route.model_dump()),
                    tools_used=[],
                    verification=None,
                    result=code_trace_result.formatted,
                    metrics=metrics(),
                    events=events,
                )

        if (
            policy_decision.effect_intent == RequestEffectIntent.MUTATING
            and not profile.policy.write_allowed
        ):
            reason_codes = ", ".join(code.value for code in policy_decision.reason_codes)
            policy_blocks += 1
            return fail(
                "request_policy: MUTATING effect intent blocked — profile "
                f"{profile.name!r} has write_allowed=False (reason_codes: {reason_codes})"
            )

        started_client = False
        context_telemetry = None
        try:
            catalog: ToolCatalog | None = None
            selected_names: set[str] = set()
            selected_specs: list[ToolSpec] = []

            if self._mcp_client is not None:
                if self._mcp_client.process is None:
                    self._mcp_client.start()
                    started_client = True
                try:
                    raw_tools = self._mcp_client.list_tools()
                except MCPError as exc:
                    return fail(f"MCP error: {exc}")
                catalog = ToolCatalog.from_mcp_tools(
                    raw_tools,
                    server=self._mcp_server_name,
                    overrides=self._tool_overrides,
                )
                selection = tool_router.select(
                    task=task, profile=profile, available_tools=catalog.all()
                )
                selected_names = set(selection.selected)
                selected_specs = [
                    ToolSpec(
                        name=meta.name,
                        description=meta.description,
                        parameters=meta.parameters,
                    )
                    for meta in catalog.all()
                    if meta.name in selected_names
                ]
                emit(
                    ToolsSelected(
                        session_id=turn_id,
                        turn_id=turn_id,
                        selected=selection.selected,
                        rejected=selection.rejected,
                    )
                )
            else:
                emit(
                    ToolsSelected(
                        session_id=turn_id,
                        turn_id=turn_id,
                        selected=[],
                        rejected=[],
                        reason="no MCP server attached",
                    )
                )

            if selected_specs:
                if self._mcp_client is None:
                    return fail("model requested tools but no MCP server is attached")

            # PRE-FLIGHT CAPABILITY GATE (Achado 67, category I-2 of the
            # 2026-09-05 expanded questionnaire): a FILESYSTEM READ_ONLY
            # request with zero filesystem tools actually available this
            # turn must not reach the model at all. Letting it through
            # invites exactly the "silent hallucination" failure the
            # Verifier structurally cannot catch after the fact (it never
            # sees the original request, only the final answer) --
            # "Leia o arquivo X" with no tool offered used to complete via
            # the LLM inventing plausible-sounding file contents. Blocking
            # here, before any inference call, is cheaper and strictly more
            # reliable than trying to detect the lie afterward.
            if (
                policy_decision.request_class == RequestClass.FILESYSTEM
                and policy_decision.effect_intent == RequestEffectIntent.READ_ONLY
                and not selected_specs
            ):
                policy_blocks += 1
                return fail(
                    "request_policy: FILESYSTEM read requires a real tool, but none is "
                    "available this turn (no MCP server attached, or no matching tool "
                    "selected) — refusing rather than letting the model answer blind"
                )

            built = self._builder.build(user_request=request, profile=profile, tools=selected_specs)
            messages = built.messages
            context_telemetry = built.telemetry

            provider = self._resolve_provider(profile)
            if provider is None:
                return fail(f"provider {profile.provider!r} is not reachable")

            while True:
                emit(
                    InferenceRequested(
                        session_id=turn_id,
                        turn_id=turn_id,
                        provider=profile.provider,
                        model=route.model,
                        message_count=len(messages),
                        tool_count=len(selected_specs),
                    )
                )
                started = time.perf_counter()
                extra_params: dict[str, Any] = {}
                if profile.inference.reasoning_effort:
                    extra_params["reasoning_effort"] = profile.inference.reasoning_effort
                # ESCALATE_UNCERTAINTY precursor fix (Achado 82/83): only one
                # tool was deemed relevant AND no tool has executed yet this
                # turn (tool_rounds == 0) -- force tool_choice to that exact
                # function instead of leaving it "auto". "auto" mode with a
                # local llama-cpp-python engine (Achado 82) has a real,
                # confirmed-in-source bug where its internal classification
                # step sometimes leaves a trailing ":" attached to the
                # function name, silently failing to match any tool and
                # falling back to returning raw classification text as the
                # answer. Forcing tool_choice sidesteps that failure mode
                # entirely for the common single-relevant-tool case. Gated to
                # tool_rounds == 0 specifically so the model is never forced
                # to call a tool again on the synthesis round after a tool
                # result is already in hand -- that would make it impossible
                # to ever produce a final plain-text answer for a
                # single-tool profile.
                if tool_rounds == 0 and len(selected_specs) == 1:
                    extra_params["tool_choice"] = {
                        "type": "function",
                        "function": {"name": selected_specs[0].name},
                    }
                try:
                    chat = provider.chat(
                        model=route.model,
                        messages=messages,
                        tools=selected_specs or None,
                        max_tokens=profile.inference.max_output_tokens,
                        extra=extra_params or None,
                    )
                except ProviderError as exc:
                    emit(
                        ResourceUsageMeasured(
                            session_id=turn_id,
                            turn_id=turn_id,
                            resource_type="inference_local",
                            operation="chat",
                            duration_ms=round((time.perf_counter() - started) * 1000, 3),
                            status="error",
                        )
                    )
                    return fail(f"provider error: {exc}")
                latency_ms = round((time.perf_counter() - started) * 1000, 3)
                inference_calls += 1
                inference_latency_ms += latency_ms
                usage = chat.usage
                if usage is not None:
                    # Achado 88 (2026-09-05): this used to be
                    # `token_totals[key] = value`, which OVERWROTE the
                    # running total with each call's own usage instead of
                    # accumulating -- a turn with a retry or a tool round
                    # silently reported only the LAST inference call's
                    # tokens as if it were the whole turn's, making
                    # tokens/s look better than it really was for any
                    # multi-call turn. Found live while filling
                    # CHALLENGE_DEMO.md's metrics table with real numbers.
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        value = getattr(usage, key)
                        if value is not None:
                            token_totals[key] = token_totals.get(key, 0) + value
                emit(
                    InferenceCompleted(
                        session_id=turn_id,
                        turn_id=turn_id,
                        provider=profile.provider,
                        model=route.model,
                        finish_reason=chat.finish_reason,
                        tool_call_count=len(chat.tool_calls),
                        latency_ms=latency_ms,
                        prompt_tokens=usage.prompt_tokens if usage else None,
                        completion_tokens=usage.completion_tokens if usage else None,
                        total_tokens=usage.total_tokens if usage else None,
                    )
                )

                if not chat.tool_calls:
                    final_content = chat.content or ""
                    emit(
                        AssistantMessage(session_id=turn_id, turn_id=turn_id, content=final_content)
                    )

                    def escalate_or_fail(
                        doubt_reason: str, *, _produced_answer: str = final_content
                    ) -> WorkerResult:
                        audit_outcome = self._run_audit_escalation(
                            emit=emit,
                            turn_id=turn_id,
                            original_request=request,
                            produced_answer=_produced_answer,
                            doubt_reason=doubt_reason,
                            executed_tools=executed,
                        )
                        if audit_outcome is None:
                            return fail(doubt_reason)
                        audited_content, audited_by = audit_outcome
                        emit(
                            AssistantMessage(
                                session_id=turn_id, turn_id=turn_id, content=audited_content
                            )
                        )
                        emit(
                            TurnCompleted(
                                session_id=turn_id,
                                turn_id=turn_id,
                                detail=f"audited by {audited_by}",
                            )
                        )
                        final_metrics = metrics()
                        final_metrics["audit_calls"] = 1
                        return WorkerResult(
                            status="completed",
                            task_type=task.task_type,
                            profile=profile.name,
                            route=RouteInfo(**route.model_dump()),
                            tools_used=tools_used,
                            verification="PASS",
                            result=audited_content,
                            metrics=final_metrics,
                            events=events,
                        )

                    # FORMAT-COMPLIANCE CHECK (Achado 68, category H): a
                    # request can demand a specific output SHAPE ("responda
                    # apenas com SIM", "retorne JSON válido com as chaves a
                    # e b") independent of whether the content is factually
                    # correct. check_format_constraint() needs the original
                    # request text, which verify_synthesis() deliberately
                    # does not receive (same reasoning as the pre-flight
                    # FILESYSTEM gate, Achado 67) -- checked here, before
                    # the content-only Verifier, and treated exactly like a
                    # RETRY/exhausted-retry verification failure so it
                    # shares the same retry-then-audit-then-fail path.
                    format_result = check_format_constraint(request, final_content)
                    if format_result is not None and not format_result.satisfied:
                        emit(
                            VerificationCompleted(
                                session_id=turn_id,
                                turn_id=turn_id,
                                verdict="RETRY",
                                detail=format_result.detail,
                            )
                        )
                        if retries_left > 0:
                            retries_left -= 1
                            messages = self._builder.append_user_note(
                                messages,
                                f"[revisao] {format_result.detail}. Corrija o formato da "
                                "resposta, mantendo apenas o formato exigido.",
                            )
                            continue
                        return escalate_or_fail(f"FORMAT_VIOLATION: {format_result.detail}")

                    # CONTEXT-CONTRADICTION CHECK (Achado 70, category L):
                    # when the request's own context contains two
                    # conflicting pieces of information (two different
                    # measurements, two people making opposite claims), the
                    # answer must acknowledge the conflict, not silently
                    # pick one side. Needs the original request text for
                    # the same reason format_constraint.py does -- checked
                    # here, sharing the same retry-then-audit-then-fail
                    # path.
                    contradiction_result = check_context_contradiction(request, final_content)
                    if contradiction_result is not None and not contradiction_result.satisfied:
                        emit(
                            VerificationCompleted(
                                session_id=turn_id,
                                turn_id=turn_id,
                                verdict="RETRY",
                                detail=contradiction_result.detail,
                            )
                        )
                        if retries_left > 0:
                            retries_left -= 1
                            messages = self._builder.append_user_note(
                                messages,
                                f"[revisao] {contradiction_result.detail}. O contexto contém "
                                "informações conflitantes -- reconheça o conflito em vez de "
                                "escolher um lado arbitrariamente.",
                            )
                            continue
                        return escalate_or_fail(
                            f"CONTEXT_CONTRADICTION: {contradiction_result.detail}"
                        )

                    verification = self._verifier.verify_synthesis(
                        content=final_content,
                        executed_tools=executed,
                        tools_offered=selected_specs,
                    )
                    emit(
                        VerificationCompleted(
                            session_id=turn_id,
                            turn_id=turn_id,
                            verdict=verification.verdict,
                            detail=verification.detail,
                        )
                    )
                    if verification.verdict == "PASS":
                        break
                    if verification.verdict == "RETRY" and retries_left > 0:
                        retries_left -= 1
                        messages = self._builder.append_user_note(
                            messages,
                            "[revisao] Sua resposta anterior falhou na verificacao: "
                            f"{verification.detail}. Corrija a resposta final.",
                        )
                        continue
                    return escalate_or_fail(
                        f"verification {verification.verdict}: {verification.detail}"
                    )

                if not selected_specs:
                    return fail("model requested tools but none were offered")

                tool_rounds += 1
                if tool_rounds > self._tool_round_limit(profile):
                    return fail(f"max_tool_rounds exceeded ({self._tool_round_limit(profile)})")

                calls_results: list[tuple[Any, str]] = []
                for call in chat.tool_calls:
                    try:
                        arguments = json.loads(call.arguments)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    emit(
                        ToolRequested(
                            session_id=turn_id,
                            turn_id=turn_id,
                            call_id=call.id,
                            tool_name=call.name,
                            arguments=arguments,
                        )
                    )
                    block = self._policy_block_reason(call.name, catalog, selected_names)
                    if block:
                        policy_blocks += 1
                        emit(
                            HITLRejected(
                                session_id=turn_id,
                                turn_id=turn_id,
                                call_id=call.id,
                                reason=block,
                            )
                        )
                        calls_results.append(
                            (
                                call,
                                f"Policy blocked this tool call ({block}); nothing was executed.",
                            )
                        )
                        executed.append(
                            ExecutedTool(name=call.name, arguments=arguments, result="", ok=False)
                        )
                        continue
                    emit(
                        HITLApproved(
                            session_id=turn_id,
                            turn_id=turn_id,
                            call_id=call.id,
                            approved_by="policy",
                        )
                    )
                    started = time.perf_counter()
                    try:
                        text = self._mcp_client.call_tool(call.name, arguments)
                    except MCPError as exc:
                        duration = round((time.perf_counter() - started) * 1000, 3)
                        emit(
                            ToolFailed(
                                session_id=turn_id,
                                turn_id=turn_id,
                                call_id=call.id,
                                tool_name=call.name,
                                error=str(exc),
                            )
                        )
                        emit(
                            ResourceUsageMeasured(
                                session_id=turn_id,
                                turn_id=turn_id,
                                resource_type="tool",
                                operation=call.name,
                                duration_ms=duration,
                                status="error",
                            )
                        )
                        calls_results.append((call, f"Tool failed: {exc}; nothing was executed."))
                        executed.append(
                            ExecutedTool(name=call.name, arguments=arguments, result="", ok=False)
                        )
                        continue
                    duration = round((time.perf_counter() - started) * 1000, 3)
                    emit(
                        ToolExecuted(
                            session_id=turn_id,
                            turn_id=turn_id,
                            call_id=call.id,
                            tool_name=call.name,
                            result=text,
                            duration_ms=duration,
                        )
                    )
                    emit(
                        ResourceUsageMeasured(
                            session_id=turn_id,
                            turn_id=turn_id,
                            resource_type="tool",
                            operation=call.name,
                            duration_ms=duration,
                            status="success",
                        )
                    )
                    calls_results.append((call, text))
                    executed.append(
                        ExecutedTool(name=call.name, arguments=arguments, result=text, ok=True)
                    )
                    if call.name not in tools_used:
                        tools_used.append(call.name)

                messages, _append_stats = self._builder.append_tool_turn(messages, calls_results)
        finally:
            if started_client:
                self._mcp_client.stop()

        emit(TurnCompleted(session_id=turn_id, turn_id=turn_id, detail="verification PASS"))
        final_metrics = metrics()
        final_metrics.update(
            {
                "selected_tool_count": context_telemetry.selected_tool_count,
                "tool_schema_bytes": context_telemetry.tool_schema_bytes,
                "prompt_size_estimate_chars": context_telemetry.prompt_size_estimate_chars,
                "context_budget": context_telemetry.context_budget,
                "verification_result": verification.verdict,
            }
        )
        return WorkerResult(
            status="completed",
            task_type=task.task_type,
            profile=profile.name,
            route=RouteInfo(**route.model_dump()),
            tools_used=tools_used,
            verification=verification.verdict,
            result=final_content,
            metrics=final_metrics,
            events=events,
        )

    def _run_audit_escalation(
        self,
        *,
        emit: Callable[[Any], None],
        turn_id: str,
        original_request: str,
        produced_answer: str,
        doubt_reason: str,
        executed_tools: list[ExecutedTool],
    ) -> tuple[str, str] | None:
        """ESCALATE_UNCERTAINTY: the local Verifier already exhausted its
        retries or FAILed outright. Escalate ONCE to the configured auditor
        profile using a fresh-evidence prompt (escalation.build_audit_messages
        -- original request + real tool evidence + the produced answer +
        the Verifier's own objective doubt reason, never the local model's
        reasoning chain, per the InflationAgent finding this project adopted
        as a design rule: escalating evidence, not a failed reasoning chain).

        Returns (audited_content, "provider/model") only when the audited
        answer itself passes verification -- an audit is never trusted
        just because it came from a supposedly stronger model. Returns None
        in every other case (no auditor configured, unknown profile,
        auditor unreachable, provider error, or the audited answer still
        does not pass) -- the caller falls through to its normal fail()
        path unchanged."""
        if self._auditor_profile_name is None:
            return None

        try:
            auditor_profile = profiles.get(self._auditor_profile_name)
        except profiles.ProfileNotFoundError:
            return None

        emit(
            AuditRequested(
                session_id=turn_id,
                turn_id=turn_id,
                auditor_profile=auditor_profile.name,
                reason=doubt_reason,
            )
        )

        provider = self._resolve_provider(auditor_profile)
        if provider is None:
            emit(
                AuditCompleted(
                    session_id=turn_id,
                    turn_id=turn_id,
                    verdict="UNREACHABLE",
                    provider=auditor_profile.provider,
                    model=auditor_profile.model,
                    detail="auditor provider unreachable",
                )
            )
            return None

        messages = escalation.build_audit_messages(
            original_request=original_request,
            produced_answer=produced_answer,
            doubt_reason=doubt_reason,
            executed_tools=executed_tools,
        )
        try:
            chat = provider.chat(model=auditor_profile.model, messages=messages)
        except ProviderError as exc:
            emit(
                AuditCompleted(
                    session_id=turn_id,
                    turn_id=turn_id,
                    verdict="UNREACHABLE",
                    provider=auditor_profile.provider,
                    model=auditor_profile.model,
                    detail=f"provider error: {exc}",
                )
            )
            return None

        audited_content = chat.content or ""
        audit_verification = self._verifier.verify_synthesis(
            content=audited_content,
            executed_tools=executed_tools,
            tools_offered=None,
        )
        emit(
            AuditCompleted(
                session_id=turn_id,
                turn_id=turn_id,
                verdict=audit_verification.verdict,
                provider=auditor_profile.provider,
                model=auditor_profile.model,
                detail=audit_verification.detail,
            )
        )
        if audit_verification.verdict != "PASS":
            return None
        return audited_content, f"{auditor_profile.provider}/{auditor_profile.model}"

    def _resolve_provider(self, profile: ExecutionProfile) -> InferenceProvider | None:
        if self._provider_factory is not None:
            provider = self._provider_factory(profile)
        else:
            from meizex_mrw.providers.factory import create_provider

            provider = create_provider(profile)
        if not provider.health():
            return None
        return provider

    @staticmethod
    def _policy_block_reason(
        name: str,
        catalog: ToolCatalog | None,
        selected_names: set[str],
    ) -> str | None:
        if name not in selected_names:
            return "tool was not offered to the model"
        meta = catalog.get(name) if catalog else None
        if meta is None:
            return "tool is not present in the catalog"
        if not meta.read_only:
            return "profile policy requires read-only tools"
        if "shell" in meta.capabilities:
            return "profile policy forbids shell capability"
        return None
