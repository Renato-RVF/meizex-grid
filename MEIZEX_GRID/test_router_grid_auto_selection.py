"""Prova real: o Capability Router (route()) escolhe o Grid sozinho quando
o perfil autoriza (grid_allowed=True), e NUNCA quando nao autoriza --
sem mock, sem montar o ExecutionStep na mao (isso ja foi provado em
test_dispatcher_remote_ssh_routing.py; aqui o alvo e a camada de
CLASSIFICACAO DE MISSAO, nao so o despacho).

Parte 1: route() sem profile (grid_allowed default False) -- o recurso do
Grid deve aparecer excluido com "grid_forbidden", nunca selecionado.

Parte 2: route() com profile grid_allowed=True -- o recurso do Grid deve
aparecer no execution_plan de verdade, escolhido pelo roteador.

Parte 3: o CapabilityRouteResult da Parte 2 (produzido pelo roteador, nao
montado a mao) e despachado de verdade contra o Dell-B via
ResourceDispatcher -- fecha o circuito: missao -> roteador escolhe Grid ->
despacho real -> resultado real.
"""

from __future__ import annotations

import sys

from meizex_mrw.capabilities.models import ToolInvocation
from meizex_mrw.capabilities.registry import CapabilityRegistry
from meizex_mrw.capabilities.router import route
from meizex_mrw.dispatch.dispatcher import ResourceDispatcher
from meizex_mrw.dispatch.executors.process import ProcessExecutor
from meizex_mrw.dispatch.executors.remote_ssh import RemoteSSHExecutor
from meizex_mrw.dispatch.planner import InvocationResolver
from meizex_mrw.profiles.schema import ExecutionProfile

GRID_RESOURCE_ID = "grid.dell-b.remote-ssh"
MISSION = "some os valores do json e agrupe por categoria"  # aciona deterministic_processing


class PassthroughResolver(InvocationResolver):
    def resolve(self, step, mission, previous_output):
        return step.invocation or super().resolve(step, mission, previous_output)


def _profile(grid_allowed: bool) -> ExecutionProfile:
    return ExecutionProfile(
        name="grid-poc",
        provider="none",
        model="none",
        context_budget=1000,
        grid_allowed=grid_allowed,
    )


def part1_grid_forbidden_by_default() -> bool:
    print("=== Parte 1: route() sem autorizar o Grid (padrao) ===")
    registry = CapabilityRegistry()
    result = route(MISSION, registry=registry)

    grid_selected = GRID_RESOURCE_ID in result.selected_resources
    grid_excluded = any(
        e.resource_id == GRID_RESOURCE_ID and e.reason == "grid_forbidden"
        for e in result.exclusions
    )
    print(f"[*] Grid selecionado? {grid_selected}")
    print(f"[*] Grid excluido com grid_forbidden? {grid_excluded}")

    ok = not grid_selected and grid_excluded
    if not ok:
        print("[FALHA] Sem autorizacao, o Grid nao deveria ser selecionado.")
    return ok


def part2_grid_auto_selected() -> tuple[bool, object]:
    print("\n=== Parte 2: route() com profile.grid_allowed=True ===")
    registry = CapabilityRegistry()
    profile = _profile(grid_allowed=True)
    result = route(MISSION, registry=registry, profile=profile)

    grid_selected = GRID_RESOURCE_ID in result.selected_resources
    grid_step = next(
        (s for s in result.execution_plan if s.resource == GRID_RESOURCE_ID), None
    )
    print(f"[*] Grid selecionado? {grid_selected}")
    print(f"[*] Passo no plano: {grid_step}")

    ok = grid_selected and grid_step is not None and grid_step.execution_boundary == "PROCESS"
    if not ok:
        print("[FALHA] Com autorizacao explicita, o Grid deveria ser escolhido pelo roteador.")
    return ok, result


def part3_dispatch_the_real_route_result(route_result) -> bool:
    print("\n=== Parte 3: despachar de verdade o resultado do roteador ===")

    # Lacuna conhecida e separada desta mudanca: o InvocationResolver
    # deterministico ainda nao sabe montar a invocacao para capacidades de
    # fronteira PROCESS (so conhece filesystem_read/filesystem_discovery).
    # O roteador ja fez a parte que importa aqui -- escolheu o recurso
    # certo (Parte 2); preencher a invocacao manualmente e um contorno do
    # planner, nao uma alteracao no resultado real do roteador.
    for step in route_result.execution_plan:
        if step.resource == GRID_RESOURCE_ID and step.invocation is None:
            step.invocation = ToolInvocation(tool_name="process_runner", arguments={})

    remote_executor = RemoteSSHExecutor(
        resource_id=GRID_RESOURCE_ID,
        host="192.168.15.42",
        ssh_user="meizexgrid",
        ssh_key_path=r"C:\Users\MEIZEX-ADMIN\.ssh\meizex_grid_dell_b",
        remote_workdir=r"C:\Users\usuario\meizex-grid\MEIZEX_ROUTER_WORKER",
        remote_python=r"C:\Users\usuario\AppData\Local\Programs\Python\Python313\python.exe",
        timeout_s=20.0,
    )
    local_executor = ProcessExecutor(timeout_s=20.0)
    dispatcher = ResourceDispatcher(
        executors=[remote_executor, local_executor],
        resolver=PassthroughResolver(),
    )

    dispatch_result = dispatcher.dispatch(route_result)
    print(dispatch_result.model_dump_json(indent=2))

    grid_step_result = next(
        (r for r in dispatch_result.step_results if r.resource_id == GRID_RESOURCE_ID), None
    )
    ok = (
        grid_step_result is not None
        and grid_step_result.executor_kind == "remote_ssh"
        and grid_step_result.status == "COMPLETED"
    )
    if not ok:
        print("[FALHA] O passo do Grid, produzido pelo roteador de verdade, nao executou.")
    return ok


def main() -> int:
    ok1 = part1_grid_forbidden_by_default()
    ok2, route_result = part2_grid_auto_selected()
    ok3 = part3_dispatch_the_real_route_result(route_result) if ok2 else False

    if ok1 and ok2 and ok3:
        print(
            "\n[SUCESSO] Roteador escolhe o Grid sozinho quando autorizado, nunca quando nao "
            "autorizado, e o resultado real despacha de verdade contra o Dell-B."
        )
        return 0
    print("\n[FALHA] Uma ou mais partes falharam -- ver acima.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
