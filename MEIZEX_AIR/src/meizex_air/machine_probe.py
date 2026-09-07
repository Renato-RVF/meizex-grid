import platform
import datetime
import psutil

from .models import (
    MachineSnapshot,
    MachineInfo,
    OsInfo,
    CpuInfo,
    MemoryInfo,
    SwapInfo,
    StorageInfo,
    ProcessPressure,
)
from .process_probe import get_process_data
from .pressure import evaluate_pressure_metrics
from .effective_capacity import calculate_effective_capacity
from .executor_probe import discover_all_executors, discover_all_resources
from .artifact_probe import discover_artifacts
from .formula_probe import discover_formulas

def take_snapshot(detailed: bool = False) -> MachineSnapshot:
    """Takes a read-only snapshot of the system capabilities, effective state, and execution resources."""
    try:
        uname = platform.uname()
        machine_info = MachineInfo(hostname=uname.node, architecture=uname.machine)
        os_info = OsInfo(name=uname.system, version=uname.version)
    except Exception:
        machine_info = MachineInfo()
        os_info = OsInfo()

    try:
        cpu_usage = psutil.cpu_percent(interval=0.1)
        cpu_info = CpuInfo(
            logical_processors=psutil.cpu_count(logical=True),
            physical_cores=psutil.cpu_count(logical=False),
            usage_percent=cpu_usage
        )
    except Exception:
        cpu_info = CpuInfo()
        cpu_usage = 0.0

    try:
        vm = psutil.virtual_memory()
        memory_info = MemoryInfo(
            total_bytes=vm.total,
            available_bytes=vm.available,
            used_bytes=vm.used,
            used_percent=vm.percent,
            committed_bytes=getattr(vm, 'committed', None)
        )
    except Exception:
        memory_info = MemoryInfo()

    try:
        sm = psutil.swap_memory()
        swap_info = SwapInfo(
            total_bytes=sm.total,
            used_bytes=sm.used,
            free_bytes=sm.free,
            used_percent=sm.percent
        )
        swap_percent = sm.percent
    except Exception:
        swap_info = SwapInfo()
        swap_percent = 0.0

    storage_info = []
    try:
        partitions = psutil.disk_partitions(all=False)
        for p in partitions:
            try:
                usage = psutil.disk_usage(p.mountpoint)
                storage_info.append(StorageInfo(
                    device=p.device,
                    mountpoint=p.mountpoint,
                    fstype=p.fstype,
                    total_bytes=usage.total,
                    free_bytes=usage.free,
                    used_percent=usage.percent
                ))
            except Exception:
                pass
    except Exception:
        pass

    try:
        total_procs, top_mem, top_cpu, top_apps = get_process_data(n=10 if detailed else 5)
        pressure = ProcessPressure(
            total_processes=total_procs,
            top_memory=top_mem,
            top_cpu=top_cpu,
            top_apps_by_memory=top_apps
        )
    except Exception:
        pressure = ProcessPressure(total_processes=0, top_memory=[], top_cpu=[], top_apps_by_memory=[])
        total_procs = 0

    pressure_metrics = evaluate_pressure_metrics(
        mem_percent=memory_info.used_percent or 0.0,
        cpu_percent=cpu_usage,
        swap_percent=swap_percent,
        process_count=total_procs
    )

    eff_cap = calculate_effective_capacity(
        total_ram=memory_info.total_bytes,
        available_ram=memory_info.available_bytes
    )
    
    resources = discover_all_resources()
    registry = discover_artifacts(compute_hashes=False)
    formulas = discover_formulas()

    return MachineSnapshot(
        timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        machine=machine_info,
        os=os_info,
        cpu=cpu_info,
        memory=memory_info,
        swap=swap_info,
        storage=storage_info,
        gpu=[],
        process_pressure=pressure,
        pressure_metrics=pressure_metrics,
        effective_capacity=eff_cap,
        resources=resources,
        executors=resources,  # Backward compat alias
        model_registry=registry,
        formula_registry=formulas
    )
