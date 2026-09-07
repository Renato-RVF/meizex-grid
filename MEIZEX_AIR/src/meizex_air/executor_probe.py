import sys
import psutil
import shutil
import urllib.request
import json
import importlib.util
import datetime
from typing import List

from .models import (
    ExecutorRecord, ExecutorType, EvidenceConfidence, 
    ResourceKind, ResourceCapability, ExecutionResource,
    ResourceRelation, RelationType
)

def find_processes_by_names(names: List[str]) -> List[psutil.Process]:
    found = []
    for p in psutil.process_iter(['name', 'exe']):
        try:
            if p.info['name'] and p.info['name'].lower() in names:
                found.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return found

def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def discover_python() -> ExecutorRecord:
    return ExecutorRecord(
        resource_id="python_current",
        executor_id="python_current",
        executor_type=ExecutorType.PYTHON,
        resource_kind=ResourceKind.DETERMINISTIC_EXECUTOR,
        display_name="Python (Current)",
        declared_present=True,
        observed_present=True,
        confidence=EvidenceConfidence.CONFIRMED,
        capabilities_declared=[ResourceCapability.RUN_CODE],
        capabilities_observed=[ResourceCapability.RUN_CODE],
        executable_path=sys.executable,
        version=sys.version.split(" ")[0],
        process_running=True,
        process_ids=[psutil.Process().pid],
        detection_sources=["PYTHON_RUNTIME"],
        last_verified_utc=_now_utc()
    )

def discover_powershell() -> ExecutorRecord:
    pwsh_path = shutil.which("pwsh") or shutil.which("powershell")
    
    record = ExecutorRecord(
        resource_id="powershell_sys",
        executor_id="powershell_sys",
        executor_type=ExecutorType.POWERSHELL,
        resource_kind=ResourceKind.DETERMINISTIC_EXECUTOR,
        display_name="PowerShell",
        declared_present=pwsh_path is not None,
        observed_present=False,
        confidence=EvidenceConfidence.PARTIAL if pwsh_path else EvidenceConfidence.NOT_FOUND,
        capabilities_declared=[ResourceCapability.RUN_SHELL],
        executable_path=pwsh_path,
        detection_sources=["PATH"] if pwsh_path else [],
        last_verified_utc=_now_utc()
    )
    
    procs = find_processes_by_names(["pwsh.exe", "powershell.exe"])
    if procs:
        record.observed_present = True
        record.process_running = True
        record.process_ids = [p.pid for p in procs]
        record.confidence = EvidenceConfidence.CONFIRMED
        record.capabilities_observed = [ResourceCapability.RUN_SHELL]
        record.detection_sources.append("PROCESS_TABLE")
        
    return record

def discover_ollama() -> ExecutorRecord:
    exe_path = shutil.which("ollama")
    record = ExecutorRecord(
        resource_id="ollama_local",
        executor_id="ollama_local",
        executor_type=ExecutorType.OLLAMA,
        resource_kind=ResourceKind.RUNTIME_ENVIRONMENT,
        display_name="Ollama",
        declared_present=exe_path is not None,
        observed_present=False,
        confidence=EvidenceConfidence.PARTIAL if exe_path else EvidenceConfidence.NOT_FOUND,
        capabilities_declared=[ResourceCapability.SERVE_MODEL, ResourceCapability.INFERENCE],
        executable_path=exe_path,
        endpoint_declared="http://localhost:11434",
        detection_sources=["PATH"] if exe_path else [],
        last_verified_utc=_now_utc()
    )

    procs = find_processes_by_names(["ollama.exe", "ollama"])
    if procs:
        record.observed_present = True
        record.process_running = True
        record.process_ids = [p.pid for p in procs]
        record.confidence = EvidenceConfidence.CONFIRMED
        record.detection_sources.append("PROCESS_TABLE")

    try:
        req = urllib.request.Request("http://localhost:11434/api/version", method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                record.version = data.get("version")
                record.observed_present = True
                record.endpoint_observed_reachable = True
                record.capabilities_observed = [ResourceCapability.SERVE_MODEL, ResourceCapability.INFERENCE]
                record.confidence = EvidenceConfidence.CONFIRMED
                if "LOCAL_ENDPOINT" not in record.detection_sources:
                    record.detection_sources.append("LOCAL_ENDPOINT")
    except Exception:
        pass
        
    return record

def discover_lm_studio() -> ExecutorRecord:
    procs_lm = find_processes_by_names(["lm studio.exe"])
    procs_llama = find_processes_by_names(["llama-server.exe"])
    
    record = ExecutorRecord(
        resource_id="lm_studio_local",
        executor_id="lm_studio_local",
        executor_type=ExecutorType.LM_STUDIO,
        resource_kind=ResourceKind.RUNTIME_ENVIRONMENT,
        display_name="LM Studio",
        declared_present=False,
        observed_present=False,
        confidence=EvidenceConfidence.NOT_FOUND,
        capabilities_declared=[ResourceCapability.SERVE_MODEL, ResourceCapability.INFERENCE],
        endpoint_declared="http://localhost:1234",
        detection_sources=[],
        last_verified_utc=_now_utc()
    )

    if procs_lm:
        record.declared_present = True
        record.confidence = EvidenceConfidence.PARTIAL
        record.detection_sources.append("PROCESS_TABLE_GUI")
        record.process_ids.extend([p.pid for p in procs_lm])
        record.process_running = True
        
    if procs_llama and procs_lm:
        record.process_ids.extend([p.pid for p in procs_llama])
        record.limitations.append("llama-server.exe partially associated with LM Studio")
        # Relation setup
        record.relations.append(ResourceRelation(
            target_resource_id="llama_cpp_local",
            relation_type=RelationType.HOSTS,
            relation_confidence=EvidenceConfidence.PARTIAL
        ))

    try:
        req = urllib.request.Request("http://localhost:1234/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as response:
            if response.status == 200:
                record.declared_present = True
                record.observed_present = True
                record.endpoint_observed_reachable = True
                record.capabilities_observed = [ResourceCapability.SERVE_MODEL, ResourceCapability.INFERENCE]
                record.confidence = EvidenceConfidence.CONFIRMED
                if "LOCAL_ENDPOINT" not in record.detection_sources:
                    record.detection_sources.append("LOCAL_ENDPOINT")
    except Exception:
        pass

    return record

def discover_llama_cpp() -> ExecutorRecord:
    exe_server = shutil.which("llama-server") or shutil.which("llama-server.exe")
    exe_cli = shutil.which("llama-cli") or shutil.which("llama-cli.exe")
    
    declared = exe_server is not None or exe_cli is not None
    record = ExecutorRecord(
        resource_id="llama_cpp_local",
        executor_id="llama_cpp_local",
        executor_type=ExecutorType.LLAMA_CPP,
        resource_kind=ResourceKind.MODEL_RUNTIME,
        display_name="llama.cpp",
        declared_present=declared,
        observed_present=False,
        confidence=EvidenceConfidence.PARTIAL if declared else EvidenceConfidence.NOT_FOUND,
        capabilities_declared=[ResourceCapability.INFERENCE, ResourceCapability.SERVE_MODEL],
        executable_path=exe_server or exe_cli,
        detection_sources=["PATH"] if declared else [],
        last_verified_utc=_now_utc()
    )
    
    procs = find_processes_by_names(["llama-server.exe", "llama-cli.exe"])
    if procs:
        record.observed_present = True
        record.process_running = True
        record.process_ids = [p.pid for p in procs]
        record.confidence = EvidenceConfidence.PARTIAL
        record.capabilities_observed = [ResourceCapability.INFERENCE]
        record.detection_sources.append("PROCESS_TABLE")
        record.limitations.append("Attribution uncertain (could be bundled in another UI)")

    return record

def discover_onnx_runtime() -> ExecutorRecord:
    spec = importlib.util.find_spec("onnxruntime")
    declared = spec is not None
    
    record = ExecutorRecord(
        resource_id="onnx_runtime_local",
        executor_id="onnx_runtime_local",
        executor_type=ExecutorType.ONNX_RUNTIME,
        resource_kind=ResourceKind.MODEL_RUNTIME,
        display_name="ONNX Runtime",
        declared_present=declared,
        observed_present=declared,
        confidence=EvidenceConfidence.CONFIRMED if declared else EvidenceConfidence.NOT_FOUND,
        capabilities_declared=[ResourceCapability.INFERENCE],
        capabilities_observed=[ResourceCapability.INFERENCE] if declared else [],
        detection_sources=["PACKAGE_METADATA"] if declared else [],
        last_verified_utc=_now_utc()
    )
    return record

def discover_all_executors() -> List[ExecutorRecord]:
    """Returns the list of executor aliases maintaining AIR-003 compatibility."""
    return [
        discover_python(),
        discover_powershell(),
        discover_ollama(),
        discover_lm_studio(),
        discover_llama_cpp(),
        discover_onnx_runtime()
    ]

def discover_all_resources() -> List[ExecutionResource]:
    """Returns the same objects cast as generic ExecutionResources for AIR-004 taxonomy."""
    # Since ExecutorRecord extends ExecutionResource, we can just return them natively.
    return discover_all_executors()
