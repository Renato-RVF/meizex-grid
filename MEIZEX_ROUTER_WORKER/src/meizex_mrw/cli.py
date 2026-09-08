"""MRW CLI — the machine-first contract for OpenCode/Codex/scripts/technical users.

This module is a thin shell over the worker and routing stack, not a second
implementation of the pipeline:

  * ``mrw run``  -> Worker -> WorkerResult -> serialized JSON on stdout;
  * ``mrw route`` -> CLASSIFY -> PROFILE -> MODEL ROUTE -> TOOL ROUTE, without
    executing anything;
  * ``mrw status`` -> provider health + profiles (informational);
  * ``mrw tools`` -> the real tools the worker would discover from MCP;
  * ``mrw profiles`` -> enumerate/show execution profiles.

Contract (machine-first):
  * stdout carries the result; stderr carries diagnostics. With ``--json``
    stdout is a single JSON document.
  * exit codes: 0 completed/pass, 1 execution failed, 2 invalid input/config,
    3 provider/runtime unavailable, 4 policy blocked.

``--cwd`` is treated as execution data/policy: the read-only filesystem MCP
server is confined to it, and it must be an existing directory.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from meizex_mrw import model_router, profiles, task_classifier, tool_router
from meizex_mrw.capabilities import (
    RegistryError,
)
from meizex_mrw.capabilities import (
    route as capability_route,
)
from meizex_mrw.events.models import RunCompleted, RunStarted
from meizex_mrw.events.store import EventStore, validate_run_id
from meizex_mrw.mcp.catalog import ToolCatalog, ToolOverride
from meizex_mrw.mcp.client import MCPClient, MCPError
from meizex_mrw.mcp.config import MCPServerConfig
from meizex_mrw.mcp_filesystem_readonly import filesystem_tool_overrides
from meizex_mrw.profiles.loader import ProfileNotFoundError
from meizex_mrw.profiles.schema import ExecutionProfile
from meizex_mrw.providers.base import InferenceProvider, ProviderError
from meizex_mrw.tools.metadata import ToolMetadata
from meizex_mrw.worker.engine import Worker
from meizex_mrw.worker.result import WorkerResult

EXIT_OK = 0
EXIT_EXECUTION_FAILED = 1
EXIT_INVALID_INPUT = 2
EXIT_RUNTIME_UNAVAILABLE = 3
EXIT_POLICY_BLOCKED = 4

DEFAULT_PROFILE = "filesystem_read"


def _provider_factory(profile: ExecutionProfile) -> InferenceProvider:
    """The seam `mrw run/status` uses to build a provider; tests monkeypatch this."""
    from meizex_mrw.providers.factory import create_provider

    return create_provider(profile)


def _build_mcp(profile_name: str, cwd: Path | None) -> MCPClient | None:
    """The seam `mrw run/route/tools` uses to attach the read-only FS server."""
    if cwd is None:
        return None
    return MCPClient(
        MCPServerConfig(
            name="mrw-filesystem-readonly",
            command=[
                sys.executable,
                "-m",
                "meizex_mrw.mcp_filesystem_readonly",
                "--root",
                str(cwd),
            ],
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mrw",
        description=(
            "MEIZEX Router Worker — headless-first local AI routing/governance/execution layer. "
            "Use --json for the machine contract (stdout = result, stderr = diagnostics)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Provider health and available profiles.")
    p_status.add_argument("--profile", default=None, help="Profile to check (default: first).")
    p_status.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")

    p_route = sub.add_parser(
        "route", help="Classify/profile/model-route/tool-route without executing."
    )
    p_route.add_argument("mission", help="The mission text to route.")
    p_route.add_argument("--profile", default=None, help="Execution profile (default: first).")
    p_route.add_argument(
        "--cwd", default=None, help="Root directory that confines read-only tools."
    )
    p_route.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")

    p_run = sub.add_parser("run", help="Execute one mission through the worker.")
    p_run.add_argument("mission", help="The mission text to execute.")
    p_run.add_argument("--profile", default=None, help="Execution profile (default: first).")
    p_run.add_argument("--cwd", default=None, help="Root directory that confines read-only tools.")
    p_run.add_argument("--run-id", default=None, help="Stable run id for the observation trail.")
    p_run.add_argument(
        "--auditor-profile",
        default=None,
        help=(
            "Profile to escalate to (ESCALATE_UNCERTAINTY) when the local "
            "Verifier exhausts retries or FAILs outright. Opt-in only: "
            "omitting this flag preserves the exact previous behavior "
            "(fail the turn immediately, no escalation)."
        ),
    )
    p_run.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")

    p_dispatch = sub.add_parser(
        "dispatch", help="Execute one mission via the Resource Dispatcher (MVP)."
    )
    p_dispatch.add_argument("mission", help="The mission text to route and execute.")
    p_dispatch.add_argument("--profile", default=None, help="Execution profile (default: first).")
    p_dispatch.add_argument(
        "--cwd", default=None, help="Root directory that confines read-only tools."
    )
    p_dispatch.add_argument(
        "--run-id", default=None, help="Stable run id for the observation trail."
    )
    p_dispatch.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")

    p_resume = sub.add_parser(
        "resume", help="Resume an escalated mission with an AssistanceResponse."
    )
    p_resume.add_argument("--token", required=True, help="The resume_token from the escalation.")
    p_resume.add_argument(
        "--response-file",
        required=False,
        help="JSON file containing the AssistanceResponse (or stdin if omitted).",
    )
    p_resume.add_argument("--profile", default=None, help="Execution profile (default: first).")
    p_resume.add_argument(
        "--cwd", default=None, help="Root directory that confines read-only tools."
    )
    p_resume.add_argument("--run-id", default=None, help="Stable run id for the observation trail.")
    p_resume.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")

    p_tools = sub.add_parser("tools", help="Discover the real tools the worker would see.")
    p_tools.add_argument("--profile", default=None, help="Execution profile (default: first).")
    p_tools.add_argument("--cwd", required=True, help="Root directory for real MCP tool discovery.")
    p_tools.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")

    p_profiles = sub.add_parser("profiles", help="List execution profiles, or show one.")
    p_profiles.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")
    profiles_sub = p_profiles.add_subparsers(dest="profile_action")
    p_profiles_list = profiles_sub.add_parser("list", help="List all profile names.")
    p_profiles_list.add_argument(
        "--json", action="store_true", help="Machine-readable JSON on stdout."
    )
    p_profiles_show = profiles_sub.add_parser("show", help="Show one profile in full.")
    p_profiles_show.add_argument("name", help="Profile name to show.")
    p_profiles_show.add_argument(
        "--json", action="store_true", help="Machine-readable JSON on stdout."
    )

    p_capabilities = sub.add_parser(
        "capabilities",
        help="Query the local Capability Registry (read-only; never executes).",
    )
    caps_sub = p_capabilities.add_subparsers(dest="capability_action")
    p_caps_list = caps_sub.add_parser("list", help="List registered resources.")
    p_caps_list.add_argument(
        "--capability",
        default=None,
        help="Only show resources that satisfy this canonical capability.",
    )
    p_caps_list.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")
    p_caps_show = caps_sub.add_parser("show", help="Show one resource in full.")
    p_caps_show.add_argument("id", help="Resource id to show.")
    p_caps_show.add_argument("--json", action="store_true", help="Machine-readable JSON on stdout.")

    p_model_lock = sub.add_parser(
        "model-lock", help="Verify local model files against a SHA256 lock file."
    )
    model_lock_sub = p_model_lock.add_subparsers(dest="model_lock_action")
    p_model_lock_verify = model_lock_sub.add_parser(
        "verify", help="Hash every locked file and compare against the lock."
    )
    p_model_lock_verify.add_argument("--lock", required=True, help="Path to a *.lock.json file.")
    p_model_lock_verify.add_argument(
        "--model-dir",
        default=None,
        help=(
            "Directory the locked filenames are resolved against "
            "(default: the lock file's own directory)."
        ),
    )
    p_model_lock_verify.add_argument(
        "--json", action="store_true", help="Machine-readable JSON on stdout."
    )

    return parser


def _emit(payload: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        _print_human(payload)


def _error(command: str, exit_code: int, message: str, json_mode: bool) -> int:
    if json_mode:
        print(
            json.dumps(
                {"command": command, "status": "error", "exit_code": exit_code, "error": message},
                ensure_ascii=False,
            )
        )
    print(f"mrw: error: {message}", file=sys.stderr)
    return exit_code


def _resolve_profile(command: str, name: str | None, json_mode: bool) -> ExecutionProfile | None:
    available = profiles.names()
    if name:
        candidate = name
    elif DEFAULT_PROFILE in available:
        candidate = DEFAULT_PROFILE
    elif available:
        candidate = available[0]
    else:
        candidate = DEFAULT_PROFILE
    try:
        return profiles.get(candidate)
    except ProfileNotFoundError as exc:
        _error(command, EXIT_INVALID_INPUT, str(exc), json_mode)
        return None


def _resolve_cwd(command: str, cwd: str, json_mode: bool) -> Path | None:
    path = Path(cwd).expanduser().resolve()
    if not path.is_dir():
        _error(command, EXIT_INVALID_INPUT, f"--cwd is not an existing directory: {cwd}", json_mode)
        return None
    return path


def _discover_tools(
    command: str, profile: ExecutionProfile, cwd: str | None, json_mode: bool
) -> tuple[list[ToolMetadata] | None, str | None, int]:
    """Return (tools, source, exit_code). A None tools list carries the exit
    code to use so callers don't collapse invalid input (exit 2) into a
    runtime failure (exit 3)."""
    if cwd:
        path = _resolve_cwd(command, cwd, json_mode)
        if path is None:
            return None, None, EXIT_INVALID_INPUT
        client = _build_mcp(profile.name, path)
        try:
            client.start()
            raw_tools = client.list_tools()
        except MCPError as exc:
            _error(command, EXIT_RUNTIME_UNAVAILABLE, f"MCP discovery failed: {exc}", json_mode)
            return None, None, EXIT_RUNTIME_UNAVAILABLE
        finally:
            if client.process is not None:
                client.stop()
        catalog = ToolCatalog.from_mcp_tools(
            raw_tools, server="mcp", overrides=filesystem_tool_overrides()
        )
        return catalog.all(), "discovered", EXIT_OK

    overrides = filesystem_tool_overrides()
    declared: list[ToolMetadata] = []
    for name in profile.allowed_tools:
        override = overrides.get(name, ToolOverride())
        declared.append(
            ToolMetadata(
                name=name,
                capabilities=override.capabilities,
                categories=override.categories,
                risk=override.risk,
                read_only=override.read_only,
                server="declared",
            )
        )
    return declared, "declared", EXIT_OK


def _resolve_run_id(raw: str | None, command: str, json_mode: bool) -> str | None:
    """Validate a caller-supplied --run-id; None means let the execution layer
    generate one."""
    if raw is None:
        return None
    try:
        return validate_run_id(raw)
    except ValueError as exc:
        _error(command, EXIT_INVALID_INPUT, str(exc), json_mode)
        return None


def _exit_code_for_result(result: WorkerResult) -> int:
    if result.status == "completed":
        return EXIT_OK
    if result.metrics.get("policy_blocks", 0) > 0:
        return EXIT_POLICY_BLOCKED
    error = (result.error or "").lower()
    if "provider" in error or "reachable" in error:
        return EXIT_RUNTIME_UNAVAILABLE
    return EXIT_EXECUTION_FAILED


def _cmd_model_lock(args) -> int:
    from meizex_mrw.model_lock import (
        ModelIntegrityError,
        ModelLockError,
        load_lock,
        verify_directory,
    )

    if args.model_lock_action != "verify":
        return _error("model-lock", EXIT_INVALID_INPUT, "expected subcommand: verify", args.json)

    lock_path = Path(args.lock).expanduser()
    model_dir = Path(args.model_dir).expanduser() if args.model_dir else lock_path.parent

    try:
        lock = load_lock(lock_path)
        resolved = verify_directory(model_dir, lock)
    except ModelIntegrityError as exc:
        return _error(
            "model-lock",
            EXIT_EXECUTION_FAILED,
            f"integrity check failed for {exc.filename}: "
            f"expected {exc.expected_sha256}, got {exc.actual_sha256}",
            args.json,
        )
    except ModelLockError as exc:
        return _error("model-lock", EXIT_INVALID_INPUT, str(exc), args.json)

    payload = {
        "command": "model-lock",
        "status": "ok",
        "pair_id": lock.pair_id,
        "verified": {role: str(path) for role, path in resolved.items()},
    }
    _emit(payload, args.json)
    return EXIT_OK


def _cmd_status(args) -> int:
    names = profiles.names()
    profile = None
    online = False
    models: list[dict] = []
    if names:
        profile_name = args.profile or (DEFAULT_PROFILE if DEFAULT_PROFILE in names else names[0])
        try:
            profile = profiles.get(profile_name)
        except ProfileNotFoundError as exc:
            return _error("status", EXIT_INVALID_INPUT, str(exc), args.json)
        if profile is not None:
            provider = _provider_factory(profile)
            online = provider.health()
            if online:
                try:
                    models = [m.model_dump(mode="json") for m in provider.list_models()]
                except ProviderError:
                    models = []
    _emit(
        {
            "command": "status",
            "mrw": "ready" if (online and names) else "degraded",
            "profiles": names,
            "profile": profile.model_dump(mode="json") if profile else None,
            "provider": {
                "name": profile.provider if profile else None,
                "online": online,
                "models": models,
            },
        },
        args.json,
    )
    return EXIT_OK


def _cmd_route(args) -> int:
    profile = _resolve_profile("route", args.profile, args.json)
    if profile is None:
        return EXIT_INVALID_INPUT
    task = task_classifier.classify(args.mission)
    try:
        cap = capability_route(args.mission)
    except RegistryError as exc:
        return _error("route", EXIT_INVALID_INPUT, f"capability registry: {exc}", args.json)
    model_route = model_router.route(profile)
    available, source, exit_code = _discover_tools("route", profile, args.cwd, args.json)
    if available is None:
        return exit_code
    selection = tool_router.select(task=task, profile=profile, available_tools=available)
    _emit(
        {
            "command": "route",
            "mission": args.mission,
            "task": task.model_dump(mode="json"),
            "capability_route": cap.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "model_route": model_route.model_dump(mode="json"),
            "tools": {
                "source": source,
                "available": [tool.name for tool in available],
                "selected": selection.selected,
                "rejected": selection.rejected,
                "reasons": selection.reasons,
            },
        },
        args.json,
    )
    return EXIT_OK


def _cmd_run(args) -> int:
    profile = _resolve_profile("run", args.profile, args.json)
    if profile is None:
        return EXIT_INVALID_INPUT
    cwd = _resolve_cwd("run", args.cwd, args.json) if args.cwd else None
    if args.cwd and cwd is None:
        return EXIT_INVALID_INPUT
    run_id = _resolve_run_id(args.run_id, "run", args.json)
    if args.run_id and run_id is None:
        return EXIT_INVALID_INPUT
    run_id = run_id or uuid.uuid4().hex

    auditor_profile_name = args.auditor_profile
    if auditor_profile_name:
        # Fail fast on a typo'd/unknown auditor profile rather than silently
        # never escalating -- _run_audit_escalation itself tolerates an
        # unknown profile name (returns None, falls through to fail()), but
        # that is a safety net for the library API, not acceptable CLI UX.
        try:
            profiles.get(auditor_profile_name)
        except ProfileNotFoundError as exc:
            _error("run", EXIT_INVALID_INPUT, f"--auditor-profile: {exc}", args.json)
            return EXIT_INVALID_INPUT

    mcp_client = _build_mcp(profile.name, cwd)
    try:
        store = EventStore.for_run(run_id)
        store.append(RunStarted(run_id=run_id, session_id=run_id, mission=args.mission))
        worker = Worker(
            profile_name=profile.name,
            mcp_client=mcp_client,
            event_store=store,
            provider_factory=_provider_factory,
            tool_overrides=filesystem_tool_overrides(),
            run_id=run_id,
            auditor_profile_name=auditor_profile_name,
        )
        result = worker.run(args.mission)
        store.append(
            RunCompleted(
                run_id=run_id,
                session_id=run_id,
                final_status="COMPLETED" if result.status == "completed" else "FAILED",
                step_count=0,
            )
        )
    finally:
        if mcp_client is not None and mcp_client.process is not None:
            mcp_client.stop()

    payload = result.model_dump(mode="json")
    payload["command"] = "run"
    payload["run_id"] = run_id
    _emit(payload, args.json)
    return _exit_code_for_result(result)


def _cmd_dispatch(args) -> int:
    from meizex_mrw.dispatch import ResourceDispatcher
    from meizex_mrw.dispatch.executors import (
        DeterministicExecutor,
        LLMExecutor,
        MCPExecutor,
        ProcessExecutor,
    )

    profile = _resolve_profile("dispatch", args.profile, args.json)
    if profile is None:
        return EXIT_INVALID_INPUT
    cwd = _resolve_cwd("dispatch", args.cwd, args.json) if args.cwd else None
    if args.cwd and cwd is None:
        return EXIT_INVALID_INPUT
    run_id = _resolve_run_id(args.run_id, "dispatch", args.json)
    if args.run_id and run_id is None:
        return EXIT_INVALID_INPUT
    run_id = run_id or uuid.uuid4().hex

    try:
        cap_route = capability_route(args.mission)
    except RegistryError as exc:
        return _error("dispatch", EXIT_INVALID_INPUT, f"capability registry: {exc}", args.json)

    mcp_client = _build_mcp(profile.name, cwd)

    from meizex_mrw.dispatch.store import FilePendingRouteStore

    executors = [
        DeterministicExecutor(root=cwd),
        MCPExecutor(mcp_client),
        LLMExecutor(
            mcp_client=mcp_client,
            provider_factory=_provider_factory,
            tool_overrides=filesystem_tool_overrides(),
        ),
        ProcessExecutor(),
    ]

    dispatcher = ResourceDispatcher(executors, store=FilePendingRouteStore())
    result = dispatcher.dispatch(cap_route, run_id=run_id)

    payload = result.model_dump(mode="json")
    payload["command"] = "dispatch"
    _emit(payload, args.json)

    if result.status == "COMPLETED":
        return EXIT_OK
    elif result.status == "ESCALATED":
        return EXIT_POLICY_BLOCKED
    else:
        return EXIT_EXECUTION_FAILED


def _cmd_resume(args) -> int:
    from meizex_mrw.dispatch import ResourceDispatcher
    from meizex_mrw.dispatch.executors import (
        DeterministicExecutor,
        LLMExecutor,
        MCPExecutor,
        ProcessExecutor,
    )
    from meizex_mrw.dispatch.models import AssistanceResponse
    from meizex_mrw.dispatch.store import FilePendingRouteStore

    profile = _resolve_profile("resume", args.profile, args.json)
    if profile is None:
        return EXIT_INVALID_INPUT
    cwd = _resolve_cwd("resume", args.cwd, args.json) if args.cwd else None
    if args.cwd and cwd is None:
        return EXIT_INVALID_INPUT

    try:
        if args.response_file:
            # utf-8-sig: accept UTF-8 files written by Windows tools that emit
            # a leading BOM (e.g. Set-Content/Out-File) while preserving plain
            # UTF-8 files.
            with open(args.response_file, encoding="utf-8-sig") as f:
                response_data = json.load(f)
        else:
            response_data = json.load(sys.stdin)
        response = AssistanceResponse.model_validate(response_data)
    except Exception as exc:
        return _error("resume", EXIT_INVALID_INPUT, f"Invalid AssistanceResponse: {exc}", args.json)

    run_id = _resolve_run_id(args.run_id, "resume", args.json)
    if args.run_id and run_id is None:
        return EXIT_INVALID_INPUT

    mcp_client = _build_mcp(profile.name, cwd)

    executors = [
        DeterministicExecutor(root=cwd),
        MCPExecutor(mcp_client),
        LLMExecutor(
            mcp_client=mcp_client,
            provider_factory=_provider_factory,
            tool_overrides=filesystem_tool_overrides(),
        ),
        ProcessExecutor(),
    ]

    # The dispatcher consumes the token, reopens the SAME run stream from the
    # envelope's run_id, and attaches it to the executors.
    dispatcher = ResourceDispatcher(executors, store=FilePendingRouteStore())
    result = dispatcher.resume(args.token, response, run_id=run_id)

    payload = result.model_dump(mode="json")
    payload["command"] = "resume"
    _emit(payload, args.json)

    if result.status == "COMPLETED":
        return EXIT_OK
    elif result.status == "ESCALATED":
        return EXIT_POLICY_BLOCKED
    else:
        return EXIT_EXECUTION_FAILED


def _cmd_tools(args) -> int:
    profile = _resolve_profile("tools", args.profile, args.json)
    if profile is None:
        return EXIT_INVALID_INPUT
    available, source, exit_code = _discover_tools("tools", profile, args.cwd, args.json)
    if available is None:
        return exit_code
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "read_only": tool.read_only,
            "capabilities": sorted(tool.capabilities),
            "categories": sorted(tool.categories),
            "risk": tool.risk,
            "server": tool.server,
            "schema_size": tool.schema_size,
        }
        for tool in available
    ]
    _emit(
        {"command": "tools", "profile": profile.name, "source": source, "tools": tools},
        args.json,
    )
    return EXIT_OK


def _cmd_profiles(args) -> int:
    if args.profile_action == "show":
        try:
            profile = profiles.get(args.name)
        except ProfileNotFoundError as exc:
            return _error("profiles", EXIT_INVALID_INPUT, str(exc), args.json)
        _emit(
            {
                "command": "profiles",
                "action": "show",
                "name": args.name,
                "profile": profile.model_dump(mode="json"),
            },
            args.json,
        )
        return EXIT_OK
    _emit({"command": "profiles", "action": "list", "profiles": profiles.names()}, args.json)
    return EXIT_OK


def _cmd_capabilities(args) -> int:
    from meizex_mrw.capabilities import CapabilityRegistry

    registry = CapabilityRegistry()
    try:
        resources = registry.load()
    except RegistryError as exc:
        return _error("capabilities", EXIT_INVALID_INPUT, str(exc), args.json)

    if args.capability_action == "show":
        resource = registry.get(args.id)
        if resource is None:
            return _error(
                "capabilities",
                EXIT_INVALID_INPUT,
                f"unknown capability resource id {args.id!r}",
                args.json,
            )
        _emit(
            {
                "command": "capabilities",
                "action": "show",
                "id": args.id,
                "resource": resource.model_dump(mode="json"),
            },
            args.json,
        )
        return EXIT_OK

    if args.capability:
        matching = registry.find(args.capability)
        resources = [r for r in resources if r.id in {m.id for m in matching}]

    _emit(
        {
            "command": "capabilities",
            "action": "list",
            "count": len(resources),
            "resources": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "status": r.status,
                    "local": r.local,
                    "cloud_required": r.cloud_required,
                    "runtime": r.runtime,
                    "declared_capabilities": sorted(r.declared_capabilities),
                    "validated_capabilities": dict(r.validated_capabilities),
                    "input_modalities": r.input_modalities,
                    "output_modalities": r.output_modalities,
                }
                for r in resources
            ],
        },
        args.json,
    )
    return EXIT_OK


def _print_human(payload: dict) -> None:
    command = payload.get("command")
    if command == "status":
        print(f"mrw: {payload['mrw']}")
        print(f"  profiles: {', '.join(payload['profiles']) or '(none)'}")
        provider = payload.get("provider") or {}
        print(f"  provider: {provider.get('name')} online={provider.get('online')}")
        return
    if command == "route":
        task = payload.get("task", {})
        cap = payload.get("capability_route", {})
        route = payload.get("model_route", {})
        tools = payload.get("tools", {})
        print(f"mrw: route for {payload.get('mission', '')!r}")
        print(f"  task: {task.get('task_type')} (confidence {task.get('confidence')})")
        if cap:
            print(
                f"  capabilities: {cap.get('required_capabilities')} "
                f"(llm_required={cap.get('llm_required')}, "
                f"plan={len(cap.get('execution_plan', []))} steps)"
            )
            for step in cap.get("execution_plan", []):
                print(f"    step: {step.get('capability')} -> {step.get('resource')}")
        print(f"  model: {route.get('provider')}/{route.get('model')}")
        print(f"  tools ({tools.get('source')}): selected={tools.get('selected')}")
        print(f"          rejected={tools.get('rejected')}")
        return
    if command == "run":
        print(f"mrw: {payload.get('status')} ({payload.get('profile')})")
        print(f"  task_type: {payload.get('task_type')}")
        print(f"  verification: {payload.get('verification')}")
        print(f"  tools_used: {payload.get('tools_used')}")
        print(f"  result: {payload.get('result')}")
        if payload.get("error"):
            print(f"  error: {payload.get('error')}")
        metrics = payload.get("metrics", {})
        if metrics:
            print(f"  metrics: {metrics}")
        return
    if command == "tools":
        print(f"mrw: tools for {payload.get('profile')} (source={payload.get('source')})")
        for tool in payload.get("tools", []):
            print(f"  {tool['name']} read_only={tool['read_only']} risk={tool['risk']}")
        return
    if command == "profiles":
        print(f"mrw: profiles ({payload.get('action')})")
        if payload.get("action") == "list":
            for name in payload.get("profiles", []):
                print(f"  {name}")
        else:
            print(json.dumps(payload.get("profile"), ensure_ascii=False, indent=2))
        return
    if command == "capabilities":
        action = payload.get("action")
        print(f"mrw: capabilities ({action})")
        if action == "list":
            print(f"  count: {payload.get('count')}")
            for resource in payload.get("resources", []):
                print(
                    f"  {resource['id']} [{resource['kind']}/{resource['status']}] "
                    f"local={resource['local']}"
                )
        elif action == "show":
            print(json.dumps(payload.get("resource"), ensure_ascii=False, indent=2))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    # Machine-first contract: stdout is the JSON result, so it must be UTF-8
    # regardless of the console codepage (reconfigure is a no-op where stdout
    # is already UTF-8, e.g. pytest's captured streams).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "status": _cmd_status,
        "route": _cmd_route,
        "run": _cmd_run,
        "dispatch": _cmd_dispatch,
        "resume": _cmd_resume,
        "tools": _cmd_tools,
        "profiles": _cmd_profiles,
        "capabilities": _cmd_capabilities,
        "model-lock": _cmd_model_lock,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        return _error(
            args.command or "mrw",
            EXIT_INVALID_INPUT,
            f"unknown command {args.command!r}",
            False,
        )
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
