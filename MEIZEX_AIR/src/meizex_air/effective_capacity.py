from .models import EffectiveCapacity, EffectiveMemory
from .config import SAFETY_RESERVE_BYTES

def calculate_effective_capacity(total_ram: int, available_ram: int) -> EffectiveCapacity:
    """Calcula a capacidade efetiva aplicando as políticas de segurança."""
    
    # Prevenção contra tipos None e math errors
    total = total_ram if total_ram else 0
    available = available_ram if available_ram else 0
    
    effective_safe = available - SAFETY_RESERVE_BYTES
    if effective_safe < 0:
        effective_safe = 0
        
    return EffectiveCapacity(
        memory=EffectiveMemory(
            physical_total_bytes=total,
            currently_available_bytes=available,
            safety_reserve_bytes=SAFETY_RESERVE_BYTES,
            effective_safe_available_bytes=effective_safe
        )
    )
