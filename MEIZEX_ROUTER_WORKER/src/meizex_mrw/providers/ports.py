"""Diagnóstico local de portas TCP para evitar colisões entre serviços.

Ported near-verbatim from MEIZEX_HARNESS_V2/src/meizex_harness_v2/ports.py.
There was no Chassis process-lifecycle logic in this module to strip —
it was always pure socket diagnostics. MRW does not start or stop any
provider process (LM Studio is treated as already running); this module
is for provider health / connectivity checks only.
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse


def port_from_url(url: str) -> tuple[str, int]:
    """Extrai host e porta de uma URL HTTP(S) validada."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL HTTP(S) inválida")
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.hostname, parsed.port or default_port


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Retorna True quando a porta pode ser vinculada neste instante."""
    if not 1 <= port <= 65535:
        raise ValueError("A porta deve estar entre 1 e 65535")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def find_available_ports(
    start: int = 8000,
    count: int = 5,
    host: str = "127.0.0.1",
    scan_limit: int = 200,
) -> list[int]:
    """Lista portas livres próximas; não as reserva e não altera configurações."""
    if count < 1:
        raise ValueError("count deve ser positivo")
    if not 1 <= start <= 65535:
        raise ValueError("start deve estar entre 1 e 65535")
    available: list[int] = []
    stop = min(65536, start + scan_limit)
    for port in range(start, stop):
        if is_port_available(port, host):
            available.append(port)
            if len(available) == count:
                break
    return available
