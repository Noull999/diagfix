"""Monitoreo en vivo (60s) para fallas intermitentes que un escaneo puntual
no alcanza a capturar: top procesos por CPU/RAM, throughput de red y
latencia continua.

La latencia se mide con una conexión TCP directa (socket) a un puerto
conocido en vez de invocar `ping` repetidamente — evita 30 arranques de
proceso en 60s y no depende del idioma de la salida de `ping`.
"""
import json
import socket
import time

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

DURATION_SECONDS = 60
INTERVAL_SECONDS = 2
PING_HOST = ("8.8.8.8", 53)


def _tcp_latency_ms(host=PING_HOST, timeout=1.0):
    start = time.time()
    try:
        with socket.create_connection(host, timeout=timeout):
            pass
        return round((time.time() - start) * 1000, 1)
    except OSError:
        return None


_IGNORED_PROCESS_NAMES = {"system idle process", "idle"}


def _top_processes(limit=5):
    if psutil is None:
        return []
    cpu_count = psutil.cpu_count() or 1
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            name = info["name"] or ""
            if name.lower() in _IGNORED_PROCESS_NAMES:
                continue
            # psutil suma el % de cada núcleo (puede superar 100%); se
            # normaliza para que se lea igual que en el Administrador de
            # tareas, donde el total de todos los procesos es ~100%.
            cpu = (info["cpu_percent"] or 0) / cpu_count
            procs.append({
                "pid": info["pid"],
                "name": name,
                "cpu": round(cpu, 1),
                "mem": round(info["memory_percent"] or 0, 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return procs[:limit]


def stream_ticks():
    """Generador síncrono de eventos SSE (`data: {...}\\n\\n`), un tick cada
    INTERVAL_SECONDS durante DURATION_SECONDS en total."""
    if psutil is None:
        yield f"data: {json.dumps({'error': 'Falta el paquete psutil.'})}\n\n"
        return

    # Primera lectura de cpu_percent siempre da 0 (no hay muestra previa).
    for p in psutil.process_iter(["cpu_percent"]):
        pass
    psutil.cpu_percent(interval=None)
    prev_net = psutil.net_io_counters()
    prev_time = time.time()
    time.sleep(INTERVAL_SECONDS)

    ticks = DURATION_SECONDS // INTERVAL_SECONDS
    for _ in range(ticks):
        now = time.time()
        net = psutil.net_io_counters()
        elapsed = now - prev_time
        sent_kbps = round((net.bytes_sent - prev_net.bytes_sent) / 1024 / elapsed, 1) if elapsed > 0 else 0
        recv_kbps = round((net.bytes_recv - prev_net.bytes_recv) / 1024 / elapsed, 1) if elapsed > 0 else 0
        prev_net, prev_time = net, now

        payload = {
            "timestamp": time.strftime("%H:%M:%S"),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "ram_percent": psutil.virtual_memory().percent,
            "net_sent_kbps": sent_kbps,
            "net_recv_kbps": recv_kbps,
            "latency_ms": _tcp_latency_ms(),
            "top_processes": _top_processes(),
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        time.sleep(INTERVAL_SECONDS)
