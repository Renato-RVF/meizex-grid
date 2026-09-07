"""
Descoberta headless da LAN para o MEIZEX Grid.

Reaproveita as funcoes de rede ja comprovadas em C:/PROJETOS/REDE/network_mapper.py
(ping sweep + tabela ARP + hostname + TTL), sem a camada Tkinter, para produzir
um relatorio JSON com identidade observada, origem da observacao e instante da
coleta. Nao declara uma maquina ausente so por nao responder a ping: hosts sem
resposta de ICMP mas presentes na tabela ARP tambem sao relatados.

Uso:
    python discovery.py [CIDR]

Sem argumento, assume a faixa /24 do IP local (ex.: 192.168.15.0/24).
"""

from __future__ import annotations

import ipaddress
import json
import platform
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def guess_default_cidr() -> str:
    ip = get_local_ip()
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{'.'.join(parts[:3])}.0/24"
    return "192.168.1.0/24"


def get_default_gateway() -> str:
    system = platform.system()
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if system == "Windows" else 0
        if system == "Windows":
            ps_cmd = (
                "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' "
                "-ErrorAction SilentlyContinue | "
                "Sort-Object -Property RouteMetric | "
                "Select-Object -First 1 -ExpandProperty NextHop)"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=6,
                creationflags=creationflags,
            ).stdout.decode(errors="ignore").strip()
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
            if m and m.group(1) != "0.0.0.0":
                return m.group(1)
        else:
            out = subprocess.run(
                ["ip", "route"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ).stdout.decode(errors="ignore")
            m = re.search(r"default via ([\d.]+)", out)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def ping_host(ip: str, timeout_ms: int = 800):
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        timeout_s = max(1, timeout_ms // 1000)
        cmd = ["ping", "-c", "1", "-W", str(timeout_s), ip]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if system == "Windows" else 0
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=(timeout_ms / 1000.0) + 2,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            return False, None
        out = result.stdout.decode(errors="ignore")
        m = re.search(r"[Tt][Tt][Ll][=:]\s*(\d+)", out)
        ttl = int(m.group(1)) if m else None
        return True, ttl
    except Exception:
        return False, None


def estimate_os_from_ttl(ttl):
    if ttl is None:
        return "desconhecido"
    if ttl <= 64:
        return "Linux/Android/macOS/IoT (TTL~64)"
    if ttl <= 128:
        return "Windows (TTL~128)"
    return "Roteador/Rede (TTL~255)"


def get_arp_table() -> dict:
    table = {}
    system = platform.system()
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if system == "Windows" else 0
        if system == "Windows":
            out = subprocess.run(
                ["arp", "-a"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            ).stdout.decode(errors="ignore")
            for line in out.splitlines():
                m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})\s+(\w+)", line)
                if m:
                    ip, mac, _kind = m.groups()
                    table[ip] = mac.upper().replace("-", ":")
        else:
            out = subprocess.run(
                ["arp", "-n"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ).stdout.decode(errors="ignore")
            for line in out.splitlines():
                m = re.match(r"(\d+\.\d+\.\d+\.\d+)\s+\S+\s+([0-9a-fA-F:]{17})", line)
                if m:
                    ip, mac = m.groups()
                    table[ip] = mac.upper()
    except Exception:
        pass
    return table


def resolve_hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def run_discovery(cidr: str) -> dict:
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = list(network.hosts()) or [network.network_address]
    local_ip = get_local_ip()
    gateway_ip = get_default_gateway()

    alive = {}
    with ThreadPoolExecutor(max_workers=64) as executor:
        futures = {executor.submit(ping_host, str(ip)): str(ip) for ip in hosts}
        for future in as_completed(futures):
            ip = futures[future]
            try:
                reachable, ttl = future.result()
                if reachable:
                    alive[ip] = ttl
            except Exception:
                pass

    arp_table = get_arp_table()

    # Uniao: hosts que responderam ping OU aparecem na tabela ARP (evita
    # declarar "ausente" um host que so nao respondeu a ICMP).
    all_ips = set(alive.keys()) | set(arp_table.keys())
    devices = []
    for ip in sorted(all_ips, key=lambda x: tuple(int(p) for p in x.split("."))):
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if ip_obj not in network:
            continue
        responded_ping = ip in alive
        mac = arp_table.get(ip, "")
        devices.append({
            "ip": ip,
            "hostname": resolve_hostname(ip) or None,
            "mac": mac or None,
            "is_gateway": ip == gateway_ip,
            "is_local": ip == local_ip,
            "observation": {
                "ping_respondeu": responded_ping,
                "presente_na_tabela_arp": ip in arp_table,
                "so_estimado_por_ttl": estimate_os_from_ttl(alive.get(ip)) if responded_ping else None,
            },
        })

    return {
        "cidr": str(network),
        "coletado_em": datetime.now(timezone.utc).isoformat(),
        "ip_local": local_ip,
        "gateway": gateway_ip or None,
        "total_enderecos_varridos": len(hosts),
        "dispositivos_observados": devices,
        "nota": (
            "Um dispositivo so aparece aqui se respondeu ping OU esta na tabela "
            "ARP local no momento da coleta. Ausencia nesta lista nao comprova "
            "que a maquina esta desligada; pode estar bloqueando ICMP e fora do "
            "cache ARP. Capacidade de execucao (worker) nao e verificada aqui."
        ),
    }


def main():
    cidr = sys.argv[1] if len(sys.argv) > 1 else guess_default_cidr()
    print(f"[*] Varrendo {cidr} ...", file=sys.stderr)
    t0 = time.time()
    report = run_discovery(cidr)
    elapsed = time.time() - t0
    print(f"[+] {len(report['dispositivos_observados'])} dispositivo(s) observado(s) em {elapsed:.1f}s", file=sys.stderr)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
