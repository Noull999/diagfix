"""Prueba de estrés rápida de CPU/RAM (patrón OCCT/Prime95): satura todos
los núcleos y reserva memoria por un tiempo fijo, mientras se observa
temperatura y reloj de CPU para detectar throttling o inestabilidad real en
el momento — a diferencia del resto de los chequeos, que solo leen el
estado del equipo en reposo.

Se generan la carga con procesos de PowerShell (no `multiprocessing` de
Python) a propósito: el .exe empaquetado con PyInstaller reejecuta el
propio binario al spawnear un `multiprocessing.Process` (necesita
`freeze_support()` y sigue siendo frágil en `--onefile`), mientras que un
`subprocess.Popen` de PowerShell es el mismo patrón que ya usa el resto del
proyecto para todo lo demás.
"""
import json
import os
import platform
import subprocess
import time

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from .base import flags as _flags
from .base import result as _result
from .hardware import read_temperatures as _read_temperatures

DURATION_SECONDS = 60
HARD_CAP_SECONDS = 90
TICK_SECONDS = 2
# Igual que en monitor.py: leer temperatura implica un proceso de
# PowerShell aparte, se muestrea 1 de cada 5 ticks (~cada 10s) en vez de
# cada tick, para no sumarle más procesos a los que ya está generando la
# propia prueba de estrés.
TEMP_SAMPLE_EVERY_TICKS = 5

_CRITICAL_TEMP_C = 90
_WARNING_TEMP_C = 80
_THROTTLE_RATIO = 0.7  # reloj actual bajo el 70% del máximo con carga completa = throttling

def _cpu_burn_script(seconds):
    # `powershell -Command` no vincula argumentos posicionales a un
    # `param()` del texto del comando (eso solo pasa invocando un archivo
    # .ps1 real) — los valores se hornean directo en el script, como ya
    # hace el resto del proyecto con `_run_powershell`. `seconds` siempre
    # es un int calculado acá mismo, no hay entrada externa que sanear.
    return f"""
$stopAt = (Get-Date).AddSeconds({seconds})
$x = 1.0000001
while ((Get-Date) -lt $stopAt) {{
    for ($i = 0; $i -lt 3000000; $i++) {{ $x = $x * 1.0000001 + 1 }}
}}
""".strip()


def _ram_stress_script(seconds, byte_count):
    return f"""
$arr = [byte[]]::new({byte_count})
$stopAt = (Get-Date).AddSeconds({seconds})
while ((Get-Date) -lt $stopAt) {{
    for ($i = 0; $i -lt $arr.Length; $i += 4096) {{ $arr[$i] = ($arr[$i] + 1) -band 0xFF }}
    Start-Sleep -Milliseconds 10
}}
""".strip()


_active_processes = []


def _popen_powershell(script):
    return subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", script],
        creationflags=_flags(), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _start_load(duration_seconds):
    """Un proceso de PowerShell por núcleo lógico (satura CPU) más uno que
    reserva y recorre ~70% de la RAM libre (nunca toda, para no dejar el
    equipo del cliente sin memoria para lo demás)."""
    procs = [_popen_powershell(_cpu_burn_script(duration_seconds)) for _ in range(os.cpu_count() or 4)]
    if psutil is not None:
        try:
            ram_bytes = int(psutil.virtual_memory().available * 0.7)
            procs.append(_popen_powershell(_ram_stress_script(duration_seconds, ram_bytes)))
        except Exception:
            pass
    return procs


def _stop_processes(procs):
    for p in procs:
        if p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except OSError:
                pass


def stop_stress():
    """Corta la prueba en curso de inmediato — botón "Detener" en el
    frontend. No depende del ciclo de vida del generador SSE: un cliente
    sincrónico corriendo en un hilo no se interrumpe solo porque el
    navegador cierre la conexión a mitad de un `time.sleep()`."""
    global _active_processes
    _stop_processes(_active_processes)
    _active_processes = []


def stream_stress(duration_seconds=DURATION_SECONDS):
    global _active_processes

    if platform.system() != "Windows":
        yield f"data: {json.dumps({'error': 'Prueba disponible solo en Windows.'})}\n\n"
        return
    if psutil is None:
        yield f"data: {json.dumps({'error': 'Falta el paquete psutil.'})}\n\n"
        return

    duration_seconds = min(duration_seconds, HARD_CAP_SECONDS)
    stop_stress()  # por si quedó algo vivo de una prueba anterior mal cerrada
    _active_processes = _start_load(duration_seconds)

    psutil.cpu_percent(interval=None)  # descarta la primera lectura (siempre da 0)
    max_temp_c = None
    max_cpu_percent = 0
    freq_max_mhz = None
    min_freq_ratio = None
    last_temps = {"cpu_c": None, "disk_c": None}

    ticks = duration_seconds // TICK_SECONDS
    try:
        for tick in range(ticks):
            time.sleep(TICK_SECONDS)
            cpu_percent = psutil.cpu_percent(interval=None)
            max_cpu_percent = max(max_cpu_percent, cpu_percent)

            freq = psutil.cpu_freq() if hasattr(psutil, "cpu_freq") else None
            current_mhz = round(freq.current) if freq and freq.current else None
            if freq and freq.max:
                freq_max_mhz = round(freq.max)
                if cpu_percent >= 95:
                    ratio = freq.current / freq.max
                    min_freq_ratio = ratio if min_freq_ratio is None else min(min_freq_ratio, ratio)

            if tick % TEMP_SAMPLE_EVERY_TICKS == 0:
                last_temps = _read_temperatures() or last_temps
            cpu_temp = last_temps.get("cpu_c")
            if cpu_temp is not None:
                max_temp_c = cpu_temp if max_temp_c is None else max(max_temp_c, cpu_temp)

            payload = {
                "elapsed": (tick + 1) * TICK_SECONDS,
                "total": duration_seconds,
                "cpu_percent": cpu_percent,
                "cpu_freq_mhz": current_mhz,
                "cpu_freq_max_mhz": freq_max_mhz,
                "cpu_temp_c": cpu_temp,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    finally:
        stop_stress()

    verdict = _build_verdict(max_temp_c, max_cpu_percent, min_freq_ratio)
    yield f"data: {json.dumps({'done': True, 'result': verdict}, ensure_ascii=False)}\n\n"


def _build_verdict(max_temp_c, max_cpu_percent, min_freq_ratio):
    causes = [
        "Ventiladores con polvo acumulado o pasta térmica seca",
        "Rejillas de ventilación obstruidas",
        "Sistema de enfriamiento insuficiente para uso sostenido a full carga",
    ]
    fix = [
        "Limpia el polvo de ventiladores y disipadores (aire comprimido).",
        "Si es una notebook con varios años de uso, considera un cambio de pasta térmica.",
        "Evita uso sostenido a full carga (renderizado, juegos largos) sin mejorar el enfriamiento.",
    ]
    detalles = []
    if max_temp_c is not None:
        detalles.append(f"temperatura máxima {max_temp_c}°C")
    if max_cpu_percent:
        detalles.append(f"carga máxima {round(max_cpu_percent)}%")
    if min_freq_ratio is not None:
        detalles.append(f"reloj bajó al {round(min_freq_ratio * 100)}% del máximo bajo carga completa")
    value = ", ".join(detalles) if detalles else None

    if max_temp_c is not None and max_temp_c >= _CRITICAL_TEMP_C:
        return _result(
            "Estrés CPU/RAM", "critical", value,
            "Temperatura crítica durante la prueba de estrés: riesgo real de throttling o apagado por protección térmica.",
            causes=causes, fix=fix,
        )
    if min_freq_ratio is not None and min_freq_ratio < _THROTTLE_RATIO:
        return _result(
            "Estrés CPU/RAM", "warning", value,
            "Se detectó una caída de reloj significativa bajo carga completa (posible throttling térmico).",
            causes=causes, fix=fix,
        )
    if max_temp_c is not None and max_temp_c >= _WARNING_TEMP_C:
        return _result("Estrés CPU/RAM", "warning", value, "Temperatura elevada durante la prueba de estrés.", causes=causes, fix=fix)
    if max_temp_c is None and min_freq_ratio is None:
        return _result(
            "Estrés CPU/RAM", "unknown", value,
            "La prueba corrió sin errores, pero este equipo no expone sensores de temperatura ni reloj por WMI para evaluar el resultado.",
        )
    return _result("Estrés CPU/RAM", "ok", value, "El equipo sostuvo carga completa de CPU/RAM sin señales de inestabilidad.")
