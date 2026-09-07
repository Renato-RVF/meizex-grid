"""
Probe standalone de capacidade efetiva de maquina, para rodar em uma maquina
sem o pacote meizex_air instalado. Reproduz fielmente a logica de
MEIZEX_AIR/src/meizex_air/machine_probe.py + effective_capacity.py +
pressure.py + process_probe.py (secoes de CPU/RAM/swap/disco/processos;
nao inclui executor/artifact/formula registries, que dependem de mais
modulos do pacote).

Requisito unico: psutil (pip install psutil).

Uso:
    python standalone_probe.py [--output arquivo.json]
"""

import argparse
import datetime
import json
import platform
import sys
from collections import defaultdict

try:
    import psutil
except ImportError:
    print("Erro: psutil nao instalado. Rode: pip install psutil", file=sys.stderr)
    sys.exit(1)

SAFETY_RESERVE_BYTES = 2 * 1024 * 1024 * 1024

APP_CATEGORIES = {
    "chrome.exe": "BROWSER",
    "msedge.exe": "BROWSER",
    "firefox.exe": "BROWSER",
    "python.exe": "DEVELOPMENT",
    "code.exe": "DEVELOPMENT",
    "pycharm64.exe": "DEVELOPMENT",
    "docker.exe": "VIRTUALIZATION",
    "wslservice.exe": "VIRTUALIZATION",
    "svchost.exe": "SYSTEM",
    "explorer.exe": "SYSTEM",
    "wininit.exe": "SYSTEM",
    "services.exe": "SYSTEM",
    "llama-server.exe": "AI_RUNTIME",
    "ollama.exe": "AI_RUNTIME",
}


class PressureThresholds:
    MEMORY_LOW, MEMORY_MODERATE, MEMORY_HIGH = 60.0, 80.0, 90.0
    CPU_LOW, CPU_MODERATE, CPU_HIGH = 50.0, 75.0, 90.0
    SWAP_LOW, SWAP_MODERATE, SWAP_HIGH = 20.0, 50.0, 80.0
    PROCESS_COUNT_LOW, PROCESS_COUNT_MODERATE, PROCESS_COUNT_HIGH = 150, 250, 500


def classify_pressure(val, low, mod, high):
    if val is None:
        return "UNKNOWN"
    if val < low:
        return "LOW"
    if val < mod:
        return "MODERATE"
    if val < high:
        return "HIGH"
    return "CRITICAL"


def classify_process(name: str) -> str:
    name_lower = name.lower()
    for exe, cat in APP_CATEGORIES.items():
        if name_lower == exe.lower():
            return cat
    return "UNKNOWN"


def get_process_data(n: int = 10):
    procs = []
    for p in psutil.process_iter(
        ["pid", "name", "exe", "username", "create_time", "num_threads", "memory_info", "cpu_percent"]
    ):
        try:
            info = p.info
            mem = info.get("memory_info")
            mem_bytes = mem.rss if mem else None
            proc_name = info["name"] or f"unknown_{info['pid']}"
            procs.append({
                "pid": info["pid"],
                "name": proc_name,
                "exe_path": info.get("exe"),
                "username": info.get("username"),
                "create_time": info.get("create_time"),
                "num_threads": info.get("num_threads"),
                "memory_bytes": mem_bytes,
                "cpu_percent": info.get("cpu_percent", 0.0),
                "category": classify_process(proc_name),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    total_procs = len(procs)
    top_mem = sorted(procs, key=lambda x: x["memory_bytes"] if x["memory_bytes"] is not None else -1, reverse=True)[:n]

    app_groups = defaultdict(lambda: {"category": "UNKNOWN", "mem": 0, "cpu": 0.0, "count": 0})
    for p in procs:
        g = app_groups[p["name"]]
        g["category"] = p["category"]
        g["mem"] += p["memory_bytes"] or 0
        g["cpu"] += p["cpu_percent"] or 0.0
        g["count"] += 1
    top_apps = sorted(
        ({"app_name": k, **v} for k, v in app_groups.items()),
        key=lambda x: x["mem"],
        reverse=True,
    )[:n]

    return total_procs, top_mem, top_apps


def take_snapshot(detailed: bool = True) -> dict:
    uname = platform.uname()
    machine = {"hostname": uname.node, "architecture": uname.machine}
    os_info = {"name": uname.system, "version": uname.version}

    cpu_usage = psutil.cpu_percent(interval=0.3)
    cpu_info = {
        "logical_processors": psutil.cpu_count(logical=True),
        "physical_cores": psutil.cpu_count(logical=False),
        "usage_percent": cpu_usage,
    }

    vm = psutil.virtual_memory()
    memory_info = {
        "total_bytes": vm.total,
        "available_bytes": vm.available,
        "used_bytes": vm.used,
        "used_percent": vm.percent,
    }

    sm = psutil.swap_memory()
    swap_info = {
        "total_bytes": sm.total,
        "used_bytes": sm.used,
        "free_bytes": sm.free,
        "used_percent": sm.percent,
    }

    storage = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            storage.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_bytes": usage.total,
                "free_bytes": usage.free,
                "used_percent": usage.percent,
            })
        except Exception:
            pass

    total_procs, top_mem, top_apps = get_process_data(n=10 if detailed else 5)

    pressure_metrics = {
        "memory_pressure": classify_pressure(vm.percent, PressureThresholds.MEMORY_LOW, PressureThresholds.MEMORY_MODERATE, PressureThresholds.MEMORY_HIGH),
        "cpu_pressure": classify_pressure(cpu_usage, PressureThresholds.CPU_LOW, PressureThresholds.CPU_MODERATE, PressureThresholds.CPU_HIGH),
        "swap_pressure": classify_pressure(sm.percent, PressureThresholds.SWAP_LOW, PressureThresholds.SWAP_MODERATE, PressureThresholds.SWAP_HIGH),
        "process_pressure": classify_pressure(total_procs, PressureThresholds.PROCESS_COUNT_LOW, PressureThresholds.PROCESS_COUNT_MODERATE, PressureThresholds.PROCESS_COUNT_HIGH),
    }

    effective_safe = vm.available - SAFETY_RESERVE_BYTES
    if effective_safe < 0:
        effective_safe = 0
    effective_capacity = {
        "memory": {
            "physical_total_bytes": vm.total,
            "currently_available_bytes": vm.available,
            "safety_reserve_bytes": SAFETY_RESERVE_BYTES,
            "effective_safe_available_bytes": effective_safe,
        }
    }

    return {
        "schema_version": "0.7-standalone",
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "machine": machine,
        "os": os_info,
        "cpu": cpu_info,
        "memory": memory_info,
        "swap": swap_info,
        "storage": storage,
        "gpu": [],
        "process_pressure": {
            "total_processes": total_procs,
            "top_memory": top_mem,
            "top_apps_by_memory": top_apps,
        },
        "pressure_metrics": pressure_metrics,
        "effective_capacity": effective_capacity,
        "nota": (
            "Gerado por standalone_probe.py (subconjunto de meizex_air probe --detailed: "
            "CPU/RAM/swap/disco/processos). Nao inclui executor/artifact/formula registries, "
            "que dependem do pacote meizex_air completo."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=str, help="Arquivo de saida")
    args = parser.parse_args()

    snapshot = take_snapshot(detailed=True)
    text = json.dumps(snapshot, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Snapshot salvo em {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
