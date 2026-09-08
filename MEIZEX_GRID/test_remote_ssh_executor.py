"""Prova de conceito: RemoteSSHExecutor do MRW rodando de verdade no Dell-B.

Nao e um teste automatizado do pacote MRW (nao roda em CI) -- e um script
manual de integracao do Grid, para provar que ResourceExecutor.execute()
atravessa a rede via SSH sem qualquer mudanca no contrato do dispatcher.

Requisitos externos (ja configurados via NEXT-005):
- SSH habilitado no Dell-B (192.168.15.42), conta meizexgrid, chave publica.
- Pacote meizex_mrw clonado/acessivel no Dell-B com httpx/pydantic/PyYAML
  instalados (ver README_deploy_mrw.md neste mesmo diretorio).

Uso (no Lenovo, com o pacote meizex_mrw local no PYTHONPATH):
    PYTHONPATH=../MEIZEX_ROUTER_WORKER/src python test_remote_ssh_executor.py
"""

from __future__ import annotations

import sys

from meizex_mrw.capabilities.models import ExecutionStep
from meizex_mrw.dispatch.executors.remote_ssh import RemoteSSHExecutor
from meizex_mrw.dispatch.models import ResourceExecutionRequest

DELL_B_HOST = "192.168.15.42"
DELL_B_SSH_USER = "meizexgrid"
DELL_B_SSH_KEY = r"C:\Users\MEIZEX-ADMIN\.ssh\meizex_grid_dell_b"
DELL_B_REMOTE_WORKDIR = r"C:\Users\usuario\meizex-grid\MEIZEX_ROUTER_WORKER"
DELL_B_REMOTE_PYTHON = r"C:\Users\usuario\AppData\Local\Programs\Python\Python313\python.exe"


def main() -> int:
    executor = RemoteSSHExecutor(
        host=DELL_B_HOST,
        ssh_user=DELL_B_SSH_USER,
        ssh_key_path=DELL_B_SSH_KEY,
        remote_workdir=DELL_B_REMOTE_WORKDIR,
        remote_python=DELL_B_REMOTE_PYTHON,
        timeout_s=20.0,
    )

    step = ExecutionStep(
        capability="environment_probe",
        resource="grid-dell-b-remote-ssh",
        kind="tool",
        execution_boundary="PROCESS",
    )
    request = ResourceExecutionRequest(
        mission="MEIZEX Grid: prova de execucao remota real via SSH (RemoteSSHExecutor)",
        step=step,
        run_id="grid-ssh-poc-001",
        step_id="probe-dell-b",
        timeout_s=20.0,
    )

    print(f"[*] kind do executor: {executor.kind}")
    print(f"[*] comando montado: {executor._command}")
    print("[*] executando...")

    result = executor.execute(request)

    print("\n=== ResourceExecutionResult ===")
    print(result.model_dump_json(indent=2))

    if result.status == "COMPLETED":
        print("\n[SUCESSO] Execucao remota real confirmada via RemoteSSHExecutor.")
        return 0
    else:
        print(f"\n[FALHA] status={result.status} error={result.error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
