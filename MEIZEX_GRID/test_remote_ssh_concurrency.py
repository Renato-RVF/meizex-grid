"""Prova real: max_concurrent limita quantos jobs rodam de verdade contra
o Dell-B ao mesmo tempo, sem bloquear quem submete.

Parte 1: max_concurrent=2, submete 5 jobs de uma vez (execute_async nao
bloqueia nenhuma das 5 chamadas). Mede quantos ficam "started()" (rodando
de verdade, ssh ja spawnado) simultaneamente via amostragem -- nunca deve
passar de 2. Mede o tempo total: com round-trip real de ~0.8-1.2s e 5 jobs
em lotes de 2, o tempo total deve ficar por volta de 3 "ondas"
(ceil(5/2)=3), nao um unico round-trip (o que provaria que o limite nao
fez nada).

Parte 2: cancela um job que ainda esta na fila (antes de started()).
Prova que ele nunca chega a rodar de verdade -- termina quase instantaneo,
com status FAILED e evidencia CANCELLED_BEFORE_START, sem nunca ter gasto
um round-trip SSH.
"""

from __future__ import annotations

import sys
import time

from meizex_mrw.capabilities.models import ExecutionStep, ToolInvocation
from meizex_mrw.dispatch.executors.remote_ssh import RemoteSSHExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest

GRID_RESOURCE_ID = "grid.dell-b.remote-ssh"


def _make_executor(max_concurrent: int) -> RemoteSSHExecutor:
    return RemoteSSHExecutor(
        resource_id=GRID_RESOURCE_ID,
        host="192.168.15.42",
        ssh_user="meizexgrid",
        ssh_key_path=r"C:\Users\MEIZEX-ADMIN\.ssh\meizex_grid_dell_b",
        remote_workdir=r"C:\Users\usuario\meizex-grid\MEIZEX_ROUTER_WORKER",
        remote_python=r"C:\Users\usuario\AppData\Local\Programs\Python\Python313\python.exe",
        timeout_s=30.0,
        max_concurrent=max_concurrent,
    )


def _make_request(step_id: str) -> ResourceExecutionRequest:
    step = ExecutionStep(
        capability="environment_probe",
        resource=GRID_RESOURCE_ID,
        kind="tool",
        invocation=ToolInvocation(tool_name="process_runner", arguments={}),
        execution_boundary="PROCESS",
    )
    return ResourceExecutionRequest(
        mission="MEIZEX Grid: prova de limite de concorrencia",
        step=step,
        run_id="grid-concurrency-poc-001",
        step_id=step_id,
        timeout_s=30.0,
    )


def part1_concurrency_limit() -> bool:
    print("=== Parte 1: max_concurrent=2, 5 jobs simultaneos ===")
    executor = _make_executor(max_concurrent=2)

    t0 = time.perf_counter()
    handles = [executor.execute_async(_make_request(f"job-{i}")) for i in range(5)]
    submit_elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"[*] 5x execute_async retornaram em {submit_elapsed_ms:.1f}ms total (nao bloquearam)")

    max_started_observed = 0
    while not all(h.done() for h in handles):
        running_now = sum(1 for h in handles if h.started() and not h.done())
        max_started_observed = max(max_started_observed, running_now)
        time.sleep(0.05)

    total_elapsed_s = time.perf_counter() - t0
    print(f"[*] Todos os 5 jobs terminaram em {total_elapsed_s:.2f}s")
    print(f"[*] Maximo observado rodando ao mesmo tempo (amostrado): {max_started_observed}")

    results = [h.wait(timeout=1.0) for h in handles]
    all_completed = all(r.status == "COMPLETED" for r in results)
    print(f"[*] Todos completaram com sucesso: {all_completed}")

    ok = True
    if submit_elapsed_ms > 200:
        print("[FALHA] submissao das 5 chamadas nao foi instantanea -- execute_async bloqueou.")
        ok = False
    if max_started_observed > 2:
        print(f"[FALHA] observado {max_started_observed} rodando ao mesmo tempo, limite era 2.")
        ok = False
    if total_elapsed_s < 1.5:
        print(
            "[AVISO] tempo total baixo demais para provar que o limite fez algo -- "
            "pode ter sido rapido demais para amostrar corretamente."
        )
    if not all_completed:
        print("[FALHA] nem todos os jobs completaram com sucesso.")
        ok = False

    return ok


def part2_cancel_queued() -> bool:
    print("\n=== Parte 2: cancelar um job ainda na fila ===")
    executor = _make_executor(max_concurrent=1)

    # Ocupa o unico slot com um job real (nao cancelado).
    occupying = executor.execute_async(_make_request("occupying-the-slot"))

    # Submete um segundo job -- ele fica na fila, esperando o slot.
    t0 = time.perf_counter()
    queued = executor.execute_async(_make_request("cancel-while-queued"))
    time.sleep(0.02)  # da tempo do _run() entrar no with e checar cancelled
    was_started_before_cancel = queued.started()
    cancelled = queued.cancel()

    result = queued.wait(timeout=15.0)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"[*] job na fila estava started() antes de cancelar? {was_started_before_cancel}")
    print(f"[*] cancel() retornou {cancelled}")
    print(f"[*] job cancelado terminou em {elapsed_ms:.1f}ms, status={result.status}")
    print(f"[*] evidencia: {result.evidence}")

    occupying.wait(timeout=15.0)  # nao deixa o job ocupante pendurado

    ok = True
    if was_started_before_cancel:
        print("[FALHA] job ja tinha comecado a rodar antes do cancel -- teste nao prova a fila.")
        ok = False
    if result.status != "FAILED":
        print(f"[FALHA] esperado status FAILED, veio {result.status}.")
        ok = False
    if not result.evidence or result.evidence[0].get("status") != "CANCELLED_BEFORE_START":
        print("[FALHA] evidencia nao indica CANCELLED_BEFORE_START.")
        ok = False
    if elapsed_ms > 300:
        print("[AVISO] cancelamento na fila demorou mais do que o esperado para algo instantaneo.")

    return ok


def main() -> int:
    ok1 = part1_concurrency_limit()
    ok2 = part2_cancel_queued()

    if ok1 and ok2:
        print("\n[SUCESSO] Limite de concorrencia e cancelamento de job na fila confirmados.")
        return 0
    print("\n[FALHA] Uma ou mais partes do teste falharam -- ver acima.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
