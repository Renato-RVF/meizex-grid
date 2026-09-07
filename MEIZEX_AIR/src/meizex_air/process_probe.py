import psutil
from typing import List, Tuple, Dict
from collections import defaultdict
from .models import ProcessInfo, AppGroupInfo, ProcessCategory
from .config import APP_CATEGORIES

def _classify_process(name: str) -> ProcessCategory:
    name_lower = name.lower()
    for cat_name, exe_list in APP_CATEGORIES.items():
        if name_lower in [exe.lower() for exe in exe_list]:
            try:
                return ProcessCategory[cat_name.upper()]
            except KeyError:
                pass
    return ProcessCategory.UNKNOWN

def get_process_data(n: int = 5) -> Tuple[int, List[ProcessInfo], List[ProcessInfo], List[AppGroupInfo]]:
    """Coleta informações de processos, garantindo isolamento contra NoSuchProcess/AccessDenied."""
    procs = []
    
    for p in psutil.process_iter(['pid', 'name', 'exe', 'username', 'create_time', 'num_threads', 'memory_info', 'cpu_percent']):
        try:
            info = p.info
            mem = info.get('memory_info')
            mem_bytes = mem.rss if mem else None
            
            proc_name = info['name'] or f"unknown_{info['pid']}"
            
            procs.append({
                'pid': info['pid'],
                'name': proc_name,
                'exe_path': info.get('exe'),
                'username': info.get('username'),
                'create_time': info.get('create_time'),
                'num_threads': info.get('num_threads'),
                'memory_bytes': mem_bytes,
                'cpu_percent': info.get('cpu_percent', 0.0),
                'category': _classify_process(proc_name)
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # Total Process Count
    total_procs = len(procs)

    # Sort by memory
    top_mem_raw = sorted(
        procs,
        key=lambda x: x['memory_bytes'] if x['memory_bytes'] is not None else -1,
        reverse=True
    )[:n]

    # Sort by CPU
    top_cpu_raw = sorted(
        procs,
        key=lambda x: x['cpu_percent'] if x['cpu_percent'] is not None else -1.0,
        reverse=True
    )[:n]

    def _to_obj(p_dict):
        return ProcessInfo(
            pid=p_dict['pid'],
            name=p_dict['name'],
            exe_path=p_dict['exe_path'],
            username=p_dict['username'],
            create_time=p_dict['create_time'],
            num_threads=p_dict['num_threads'],
            memory_bytes=p_dict['memory_bytes'],
            cpu_percent=p_dict['cpu_percent'],
            category=p_dict['category']
        )

    top_mem = [_to_obj(p) for p in top_mem_raw]
    top_cpu = [_to_obj(p) for p in top_cpu_raw]

    # App Aggregation
    app_groups: Dict[str, dict] = defaultdict(lambda: {
        'category': ProcessCategory.UNKNOWN,
        'mem': 0,
        'cpu': 0.0,
        'count': 0
    })

    for p in procs:
        name = p['name']
        app_groups[name]['category'] = p['category']
        app_groups[name]['mem'] += (p['memory_bytes'] or 0)
        app_groups[name]['cpu'] += (p['cpu_percent'] or 0.0)
        app_groups[name]['count'] += 1

    aggregated_apps = []
    for app_name, data in app_groups.items():
        aggregated_apps.append(AppGroupInfo(
            app_name=app_name,
            category=data['category'],
            total_memory_bytes=data['mem'],
            total_cpu_percent=data['cpu'],
            process_count=data['count']
        ))

    # Sort apps by memory
    top_apps = sorted(aggregated_apps, key=lambda x: x.total_memory_bytes, reverse=True)[:n]

    return total_procs, top_mem, top_cpu, top_apps
