from .models import PressureLevel, PressureMetrics
from .config import PressureThresholds

def classify_pressure(val: float, low: float, mod: float, high: float) -> PressureLevel:
    """Classifica o nível de pressão determinístico."""
    if val is None:
        return PressureLevel.UNKNOWN
    if val < low:
        return PressureLevel.LOW
    elif val < mod:
        return PressureLevel.MODERATE
    elif val < high:
        return PressureLevel.HIGH
    else:
        return PressureLevel.CRITICAL

def evaluate_pressure_metrics(
    mem_percent: float, 
    cpu_percent: float, 
    swap_percent: float, 
    process_count: int
) -> PressureMetrics:
    """Gera o objeto de métricas de pressão a partir dos valores brutos."""
    
    return PressureMetrics(
        memory_pressure=classify_pressure(
            mem_percent, 
            PressureThresholds.MEMORY_LOW, 
            PressureThresholds.MEMORY_MODERATE, 
            PressureThresholds.MEMORY_HIGH
        ),
        cpu_pressure=classify_pressure(
            cpu_percent, 
            PressureThresholds.CPU_LOW, 
            PressureThresholds.CPU_MODERATE, 
            PressureThresholds.CPU_HIGH
        ),
        swap_pressure=classify_pressure(
            swap_percent, 
            PressureThresholds.SWAP_LOW, 
            PressureThresholds.SWAP_MODERATE, 
            PressureThresholds.SWAP_HIGH
        ),
        process_pressure=classify_pressure(
            process_count, 
            PressureThresholds.PROCESS_COUNT_LOW, 
            PressureThresholds.PROCESS_COUNT_MODERATE, 
            PressureThresholds.PROCESS_COUNT_HIGH
        )
    )
