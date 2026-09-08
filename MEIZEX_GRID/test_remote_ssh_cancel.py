"""Prova real: RemoteJobHandle.cancel() derruba um job em voo de verdade.

Submete um job para o Dell-B e cancela quase imediatamente (antes do
round-trip SSH normal, que fica em torno de 800ms-1.2s nas medicoes
anteriores, terminar). Se o cancelamento funcionar, o resultado chega bem
mais rapido que o round-trip normal, com status FAILED (processo morto),
nao COMPLETED.

Comparacao de controle: um segundo job, submetido e NAO cancelado, deve
completar normalmente -- prova que cancel() so afeta o job cancelado, nao
quebra o executor para chamadas seguintes.
"""

from __future__ import annotations

import sys
import time

from meizex_mrw.capabilities.models import ExecutionStep, ToolInvocation
from meizex_mrw.dispatch.executors.remote_ssh import RemoteSSHExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest

GRID_RESOURCE_ID = "grid.dell-b.remote-ssh"


def _make_request(step_id: str) -> ResourceExecutionRequest:
    step = ExecutionStep(
        capability="environment_probe",
        resource=GRID_RESOURCE_ID,
        kind="tool",
        invocation=ToolInvocation(tool_name="process_runner", arguments={}),
        execution_boundary="PROCESS",
    )
    return ResourceExecutionRequest(
        mission="MEIZEX Grid: prova de cancelamento real",
        step=step,
        run_id="grid-cancel-poc-001",
        step_id=step_id,
        timeout_s=30.0,
    )


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

    print("[*] Job A: submeter e cancelar quase na hora...")
    t0 = time.perf_counter()
    handle_a = executor.execute_async(_make_request("cancel-me"))
    time.sleep(0.05)  # deixa o Popen do ssh nascer (on_spawn captura o proc)
    cancelled = handle_a.cancel()
    print(f"[*] cancel() retornou {cancelled} em ~{(time.perf_counter()-t0)*1000:.1f}ms")

    result_a = handle_a.wait(timeout=15.0)
    elapsed_a_ms = (time.perf_counter() - t0) * 1000
    print(f"[*] Job A terminou em {elapsed_a_ms:.1f}ms, status={result_a.status}")

    print("\n[*] Job B: submeter e NAO cancelar (controle)...")
    t1 = time.perf_counter()
    handle_b = executor.execute_async(_make_request("dont-cancel-me"))
    result_b = handle_b.wait(timeout=15.0)
    elapsed_b_ms = (time.perf_counter() - t1) * 1000
    print(f"[*] Job B terminou em {elapsed_b_ms:.1f}ms, status={result_b.status}")

    ok = True
    if not cancelled:
        print("\n[AVISO] cancel() nao capturou o processo a tempo (janela de corrida) "
              "-- nao e necessariamente falha da implementacao, mas o teste nao prova nada.")
        ok = False
    if result_a.status == "COMPLETED":
        print("\n[FALHA] Job A completou normalmente -- cancelamento nao teve efeito real.")
        ok = False
    if result_b.status != "COMPLETED":
        print(f"\n[FALHA] Job B (controle, nao cancelado) deveria completar: {result_b.error}")
        ok = False
    if elapsed_b_ms < 300:
        print("\n[AVISO] Job B completou rapido demais para ser um round-trip SSH real "
              "-- verificar se o teste esta medindo o que pensa.")

    if ok:
        print(
            f"\n[SUCESSO] Cancelamento real confirmado: Job A cancelado em {elapsed_a_ms:.0f}ms "
            f"(status={result_a.status}), Job B controle completou normalmente em "
            f"{elapsed_b_ms:.0f}ms (status={result_b.status})."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
