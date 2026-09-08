"""Prova real: execute_async nao bloqueia o chamador.

Simula o cenario que motivou esta implementacao: um orquestrador manda um
job pesado para o Dell-B e continua trabalhando em outra coisa (aqui,
"outros sprints" e simulado por um loop que conta ate a resposta remota
voltar, provando que o laco roda ENQUANTO o SSH ainda esta em voo -- se
fosse bloqueante, esse loop nunca apareceria antes do resultado).

Nao mede desempenho de stress test de verdade (isso e outro dia); mede que
o padrao submit -> continue -> poll/wait funciona contra o Dell-B real.
"""

from __future__ import annotations

import sys
import time

from meizex_mrw.capabilities.models import ExecutionStep, ToolInvocation
from meizex_mrw.dispatch.executors.remote_ssh import RemoteSSHExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest

GRID_RESOURCE_ID = "grid.dell-b.remote-ssh"


def main() -> int:
    executor = RemoteSSHExecutor(
        resource_id=GRID_RESOURCE_ID,
        host="192.168.15.42",
        ssh_user="meizexgrid",
        ssh_key_path=r"C:\Users\MEIZEX-ADMIN\.ssh\meizex_grid_dell_b",
        remote_workdir=r"C:\Users\usuario\meizex-grid\MEIZEX_ROUTER_WORKER",
        remote_python=r"C:\Users\usuario\AppData\Local\Programs\Python\Python313\python.exe",
        timeout_s=30.0,
    )

    step = ExecutionStep(
        capability="environment_probe",
        resource=GRID_RESOURCE_ID,
        kind="tool",
        invocation=ToolInvocation(tool_name="process_runner", arguments={}),
        execution_boundary="PROCESS",
    )
    request = ResourceExecutionRequest(
        mission="MEIZEX Grid: prova de despacho assincrono (nao bloqueante)",
        step=step,
        run_id="grid-async-poc-001",
        step_id="stress-test-dell-b",
        timeout_s=30.0,
    )

    print("[*] Submetendo job assincrono para o Dell-B (execute_async)...")
    t0 = time.perf_counter()
    handle = executor.execute_async(request)
    submit_elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"[*] execute_async retornou em {submit_elapsed_ms:.1f}ms (nao bloqueou)")

    if submit_elapsed_ms > 200:
        print(
            "\n[FALHA] execute_async demorou demais para retornar -- "
            "nao parece estar rodando em background de verdade."
        )
        return 1

    print("[*] Fazendo 'outro trabalho' (simulando outros sprints) enquanto o SSH roda...")
    ticks_before_done = 0
    while not handle.done():
        ticks_before_done += 1
        print(f"    ... trabalhando em outra coisa, tick {ticks_before_done} "
              f"(job do Dell-B ainda em voo)")
        time.sleep(0.15)
        if ticks_before_done > 100:
            print("\n[FALHA] job nunca terminou depois de muitos ticks.")
            return 1

    print(f"[*] Job do Grid terminou depois de {ticks_before_done} tick(s) de outro trabalho.")

    result = handle.wait(timeout=1.0)
    print("\n=== ResourceExecutionResult (via handle.wait) ===")
    print(result.model_dump_json(indent=2))

    ok = ticks_before_done > 0 and result.status == "COMPLETED"
    if ok:
        print(
            "\n[SUCESSO] Despacho assincrono confirmado: o chamador seguiu trabalhando "
            f"({ticks_before_done} tick(s)) enquanto o job real rodava no Dell-B."
        )
        return 0
    print(f"\n[FALHA] status={result.status} error={result.error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
