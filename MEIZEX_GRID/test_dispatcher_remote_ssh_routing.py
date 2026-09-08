"""Prova real: o ResourceDispatcher do MRW roteia por recurso, nao so pega
o primeiro executor da lista.

Dois executores registrados (RemoteSSHExecutor para o Dell-B + ProcessExecutor
local). Dois passos no mesmo "plano de execucao": um endereca explicitamente o
recurso do Grid, o outro endereca o recurso local do processo. O dispatcher
deve mandar cada um para o executor certo -- prova que RemoteSSHExecutor.
can_handle() por resource_id funciona dentro do fluxo real do dispatcher, nao
so isolado (ver test_remote_ssh_executor.py para o teste isolado).

NAO usa o Capability Router (capabilities/router.py) real, porque o modelo
local/cloud do roteador hoje nao tem uma terceira categoria para "rede
confiavel, maquina remota" -- ver NEXT-007 em MEIZEX_GRID/NEXT.md para o
achado completo. Este script monta o CapabilityRouteResult manualmente,
testando a camada de despacho, nao a camada de classificacao de missao.
"""

from __future__ import annotations

import sys

from meizex_mrw.capabilities.models import CapabilityRouteResult, ExecutionStep, ToolInvocation
from meizex_mrw.dispatch.dispatcher import ResourceDispatcher
from meizex_mrw.dispatch.executors.process import ProcessExecutor
from meizex_mrw.dispatch.executors.remote_ssh import RemoteSSHExecutor
from meizex_mrw.dispatch.planner import InvocationResolver

GRID_RESOURCE_ID = "grid.dell-b.remote-ssh"


class PassthroughResolver(InvocationResolver):
    """Keep the invocation already set on the step (same pattern MRW's own
    M9 process-boundary tests use: these steps exercise the process/network
    boundary, not the deterministic mission-classifier planner)."""

    def resolve(self, step, mission, previous_output):
        return step.invocation or super().resolve(step, mission, previous_output)


def main() -> int:
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

    route_result = CapabilityRouteResult(
        mission="MEIZEX Grid: roteamento real por recurso (local vs remoto)",
        execution_plan=[
            ExecutionStep(
                capability="environment_probe",
                resource=GRID_RESOURCE_ID,
                kind="tool",
                invocation=ToolInvocation(tool_name="process_runner", arguments={}),
                execution_boundary="PROCESS",
            ),
            ExecutionStep(
                capability="deterministic_processing",
                resource="mrw-process-boundary-local",
                kind="tool",
                invocation=ToolInvocation(tool_name="process_runner", arguments={}),
                execution_boundary="PROCESS",
            ),
        ],
        selected_resources=[GRID_RESOURCE_ID, "mrw-process-boundary-local"],
        execution_performed=False,
        route_status="NORMAL",
    )

    result = dispatcher.dispatch(route_result)

    print("=== DispatchResult ===")
    print(result.model_dump_json(indent=2))

    remote_step = next(s for s in result.step_results if s.resource_id == GRID_RESOURCE_ID)
    local_step = next(
        s for s in result.step_results if s.resource_id == "mrw-process-boundary-local"
    )

    ok = True
    if remote_step.executor_kind != "remote_ssh":
        print(
            f"\n[FALHA] passo do Grid foi para executor_kind={remote_step.executor_kind!r}, "
            "esperado 'remote_ssh'"
        )
        ok = False
    if local_step.executor_kind != "process":
        print(
            f"\n[FALHA] passo local foi para executor_kind={local_step.executor_kind!r}, "
            "esperado 'process'"
        )
        ok = False
    if remote_step.status != "COMPLETED":
        print(f"\n[FALHA] passo do Grid nao completou: {remote_step.error}")
        ok = False
    if local_step.status != "COMPLETED":
        print(f"\n[FALHA] passo local nao completou: {local_step.error}")
        ok = False

    if ok:
        print(
            "\n[SUCESSO] Dispatcher roteou corretamente por recurso: "
            f"Grid -> remote_ssh (PID remoto real), local -> process (PID local)."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
