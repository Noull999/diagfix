"""Diagnósticos de hardware: disco, RAM y CPU."""
import json
import platform
import re
import tempfile
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from .base import result as _result
from .base import run as _run
from .base import run_powershell as _run_powershell

_SMART_SCRIPT = r"""
$disks = Get-PhysicalDisk -ErrorAction SilentlyContinue
$out = @()
foreach ($d in $disks) {
    $rel = $d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue
    $out += [ordered]@{
        name         = $d.FriendlyName
        wear         = $rel.Wear
        powerOnHours = $rel.PowerOnHours
        readErrors   = $rel.ReadErrorsUncorrected
        writeErrors  = $rel.WriteErrorsUncorrected
    }
}
$out | ConvertTo-Json -Compress
""".strip()

_BATTERY_PRESENT_SCRIPT = "[bool](Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue)"

# Etiquetas de la tabla "Battery information" del reporte — se probó en este
# proyecto que quedan en inglés incluso con Windows en español (parecen no
# estar localizadas), pero se agrega la variante en español como red de
# seguridad tolerante, mismo patrón que el resto del código (`perdidos|loss`).
_DESIGN_CAPACITY_RE = re.compile(
    r'(?:DESIGN CAPACITY|CAPACIDAD DE DISE[ÑN]O)</span></td><td[^>]*>\s*([\d.,]+)\s*mWh', re.IGNORECASE
)
_FULL_CHARGE_CAPACITY_RE = re.compile(
    r'(?:FULL CHARGE CAPACITY|CAPACIDAD DE CARGA COMPLETA)</span></td><td[^>]*>\s*([\d.,]+)\s*mWh', re.IGNORECASE
)


def check_disk_space():
    """Revisa el espacio libre en la unidad del sistema."""
    if psutil is None:
        return _result("Espacio en disco (C:)", "unknown", None, "Falta el paquete psutil.")
    try:
        target = "C:\\" if platform.system() == "Windows" else "/"
        usage = psutil.disk_usage(target)
    except Exception:
        return _result("Espacio en disco (C:)", "unknown", None, "No se pudo leer el uso de disco.")
    free_pct = round(100 - usage.percent, 1)
    causes = ["Archivos temporales y caché acumulados", "Descargas y puntos de restauración antiguos", "Programas o juegos pesados instalados"]
    fix = [
        "Ejecuta 'Liberador de espacio en disco' (cleanmgr) y limpia archivos temporales.",
        "Revisa 'Configuración > Sistema > Almacenamiento' para ver qué ocupa espacio.",
        "Desinstala programas que ya no se usen o mueve archivos grandes a un disco externo/nube.",
    ]
    if free_pct < 5:
        return _result("Espacio en disco (C:)", "critical", f"{free_pct}% libre",
                        "Espacio crítico: con menos de 5% libre Windows puede volverse inestable.",
                        causes=causes, fix=fix, action_id="clear_temp_files")
    if free_pct < 15:
        return _result("Espacio en disco (C:)", "warning", f"{free_pct}% libre",
                        "Poco espacio libre.", causes=causes, fix=fix, action_id="clear_temp_files")
    return _result("Espacio en disco (C:)", "ok", f"{free_pct}% libre", "Espacio suficiente.")


def check_disk_health():
    """Consulta el estado operacional de los discos vía PowerShell (Get-PhysicalDisk)."""
    if platform.system() != "Windows":
        return _result("Salud del disco", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell("Get-PhysicalDisk | Select-Object -ExpandProperty HealthStatus")
    if output is None:
        return _result("Salud del disco", "unknown", None,
                        "No se pudo consultar (puede requerir permisos de administrador).")
    statuses = [s.strip() for s in output.splitlines() if s.strip()]
    if not statuses:
        return _result("Salud del disco", "unknown", None, "Sin datos disponibles.")
    if any(s.lower() != "healthy" for s in statuses):
        return _result(
            "Salud del disco", "critical", ", ".join(statuses),
            "Uno o más discos reportan problemas de salud.",
            causes=["Sectores dañados o degradación física del disco", "Disco cerca del fin de su vida útil (más frecuente en HDD y SSD antiguos)"],
            fix=[
                "Respalda los datos importantes de inmediato, antes de seguir usando el equipo.",
                "Ejecuta `chkdsk C: /f /r` (requiere reiniciar) para intentar reparar sectores.",
                "Si el estado sigue en falla después de eso, planifica el reemplazo del disco.",
            ],
            action_id="chkdsk_scan",
        )
    return _result("Salud del disco", "ok", "Healthy", "Los discos reportan buen estado.")


def check_disk_smart():
    """Lee contadores S.M.A.R.T. reales (Get-StorageReliabilityCounter) en vez
    de solo el HealthStatus operacional, que suele decir "Healthy" hasta el
    día que el disco falla. Requiere permisos de administrador."""
    if platform.system() != "Windows":
        return _result("S.M.A.R.T. del disco", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_SMART_SCRIPT, timeout=20)
    if not output:
        return _result("S.M.A.R.T. del disco", "unknown", None,
                        "No se pudo consultar (requiere permisos de administrador).")
    try:
        disks = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return _result("S.M.A.R.T. del disco", "unknown", None, "No se pudo interpretar la respuesta.")
    if isinstance(disks, dict):
        disks = [disks]
    if not disks or all(d.get("wear") is None and d.get("readErrors") is None for d in disks):
        return _result("S.M.A.R.T. del disco", "unknown", None,
                        "Sin datos disponibles (requiere permisos de administrador).")

    causes_err = ["Sectores dañados detectados por el firmware del disco", "Disco con fallas de hardware activas"]
    fix_err = [
        "Respalda los datos importantes de inmediato.",
        "Ejecuta `chkdsk C: /f /r` (requiere reiniciar) para intentar reparar sectores.",
        "Si los errores persisten, planifica el reemplazo del disco.",
    ]
    causes_wear = ["Disco SSD cerca del fin de su vida útil de escritura", "Uso intensivo prolongado (servidores, edición de video, swap constante)"]
    fix_wear = ["Respalda los datos importantes.", "Planifica el reemplazo del disco antes de que falle por completo."]

    worst = "ok"
    details = []
    for d in disks:
        name = d.get("name") or "disco"
        read_err = d.get("readErrors") or 0
        write_err = d.get("writeErrors") or 0
        wear = d.get("wear")
        if read_err > 0 or write_err > 0:
            worst = "critical"
            details.append(f"{name}: errores no corregidos (lectura {read_err}, escritura {write_err})")
        elif wear is not None and wear > 90:
            worst = "critical" if worst != "critical" else worst
            details.append(f"{name}: desgaste {wear}%")
        elif wear is not None and wear > 70 and worst == "ok":
            worst = "warning"
            details.append(f"{name}: desgaste {wear}%")

    if worst == "ok":
        return _result("S.M.A.R.T. del disco", "ok", "Sin errores", "Los discos no reportan errores S.M.A.R.T.")
    causes = causes_err if any("errores" in d for d in details) else causes_wear
    fix = fix_err if any("errores" in d for d in details) else fix_wear
    return _result("S.M.A.R.T. del disco", worst, "; ".join(details), "Se detectaron problemas en el estado físico del disco.", causes=causes, fix=fix)


def battery_capacity():
    """Capacidad de diseño vs. capacidad real de la batería, en mWh.

    Devuelve `None` si el equipo no tiene batería (desktop), y un dict con
    `error` si tiene pero no se pudo medir. Lo usan tanto el chequeo con
    veredicto como la ficha del equipo (`system_info`), para no parsear el
    reporte dos veces con lógica duplicada.

    Usa `powercfg /batteryreport` en vez de `Get-CimInstance ... root\\wmi
    BatteryStaticData` — se probó en este proyecto que esa clase WMI no
    siempre expone `DesignedCapacity` (queda `null` incluso con permisos de
    administrador, es una limitación real del proveedor WMI de la batería en
    ciertos equipos/fabricantes) mientras que el reporte de `powercfg` sí
    trae ambos valores de forma confiable, y no requiere admin.
    """
    if platform.system() != "Windows":
        return None
    presence = _run_powershell(_BATTERY_PRESENT_SCRIPT, timeout=10)
    if (presence or "").strip().lower() != "true":
        return None

    tmp_path = Path(tempfile.gettempdir()) / "diagfix_battery_report.html"
    output = _run(["powercfg", "/batteryreport", "/output", str(tmp_path)], timeout=20)
    if output is None or not tmp_path.exists():
        return {"error": "No se pudo generar el reporte de batería."}
    try:
        html = tmp_path.read_text(encoding="utf-8-sig", errors="ignore")
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    design_match = _DESIGN_CAPACITY_RE.search(html)
    full_match = _FULL_CHARGE_CAPACITY_RE.search(html)
    if not design_match or not full_match:
        return {"error": "No se pudo leer la capacidad en el reporte de batería."}

    # El separador de miles del reporte sigue el idioma de Windows: coma en
    # inglés ("45,730 mWh"), punto en español latinoamericano ("45.730 mWh").
    # Nunca aparecen decimales acá (son mWh enteros), así que sacar ambos
    # caracteres es seguro en los dos casos.
    design = int(design_match.group(1).replace(",", "").replace(".", ""))
    full = int(full_match.group(1).replace(",", "").replace(".", ""))
    if design <= 0:
        return {"error": "El reporte no tiene una capacidad de diseño válida."}

    return {
        "design_mwh": design,
        "full_charge_mwh": full,
        "health_pct": round(full / design * 100),
    }


def check_battery_health():
    """Veredicto de desgaste de la batería sobre los valores de
    `battery_capacity()`. Devuelve None si el equipo no tiene batería
    (desktop), para que run_all() no lo incluya."""
    datos = battery_capacity()
    if datos is None:
        return None
    if "error" in datos:
        return _result("Salud de la batería", "unknown", None, datos["error"])

    causes = ["Ciclos de carga/descarga acumulados con el tiempo (desgaste normal)", "Batería mantenida cargada al 100% constantemente por largos periodos", "Batería con varios años de uso"]
    fix = [
        "Si el equipo se apaga inesperadamente con batería, es señal de que ya no sostiene carga.",
        "Evita mantener el equipo enchufado al 100% todo el tiempo cuando sea posible.",
        "Si el desgaste es alto, evalúa el reemplazo de la batería con el fabricante.",
    ]

    health_pct = datos["health_pct"]
    full = f"{datos['full_charge_mwh']:,}".replace(",", ".")
    design = f"{datos['design_mwh']:,}".replace(",", ".")
    value = f"{health_pct}% de capacidad original ({full} de {design} mWh)"
    if health_pct < 50:
        return _result("Salud de la batería", "critical", value, "La batería está muy desgastada.", causes=causes, fix=fix)
    if health_pct < 80:
        return _result("Salud de la batería", "warning", value, "La batería tiene desgaste notorio.", causes=causes, fix=fix)
    return _result("Salud de la batería", "ok", value, "La batería conserva buena parte de su capacidad original.")


_BATTERY_LIVE_SCRIPT = r"""
$b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
$s = Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue
[ordered]@{
    percent  = if ($b) { $b.EstimatedChargeRemaining } else { $null }
    status   = if ($b) { [int]$b.BatteryStatus } else { $null }
    acOnline = if ($s) { [bool]$s.PowerOnline } else { $null }
} | ConvertTo-Json -Compress
""".strip()

# BatteryStatus de Win32_Battery. El código 2 ("Unknown") aparece en la
# práctica en muchos equipos cuando están enchufados y ya cargados del
# todo — no es un error, es una rareza conocida del proveedor WMI de varios
# fabricantes — por eso el label final no se basa solo en esta tabla, sino
# combinado con `acOnline` y el propio porcentaje.
_BATTERY_STATUS_LABELS = {
    3: "Completa", 4: "Baja", 5: "Crítica",
    6: "Cargando", 7: "Cargando (alta)", 8: "Cargando (baja)", 9: "Cargando (crítica)",
    11: "Carga parcial",
}


def battery_live_snapshot():
    """Una lectura instantánea de % y estado de batería, para el test en
    vivo de la pestaña Pruebas — a diferencia de `battery_capacity()`
    (una foto fija del reporte de powercfg, que puede quedar con datos
    viejos si la batería casi no se usó), esto consulta el estado actual
    cada vez que se llama."""
    output = _run_powershell(_BATTERY_LIVE_SCRIPT, timeout=10)
    if not output:
        return {"available": False}
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return {"available": False}
    percent = data.get("percent")
    if percent is None:
        return {"available": False}

    ac = data.get("acOnline")
    status_code = data.get("status")
    charging = status_code in (6, 7, 8, 9)

    if charging:
        label = _BATTERY_STATUS_LABELS.get(status_code, "Cargando")
    elif ac and percent >= 99:
        label = "Completa (con cargador)"
    elif ac:
        label = "Con cargador (sin cargar)"
    elif ac is False:
        label = "Con batería (sin cargador)"
    else:
        label = _BATTERY_STATUS_LABELS.get(status_code, "Desconocido")

    return {"available": True, "percent": percent, "label": label, "ac_online": ac}


def _live_stream(snapshot_fn, duration_seconds=600, interval_seconds=2):
    """Generador SSE genérico: llama a `snapshot_fn()` cada `interval_seconds`
    y emite el resultado como evento, hasta `duration_seconds` en total.
    Comparten esta cadencia todas las pruebas "en vivo" de la pestaña
    Pruebas (batería, Ethernet, USB, monitor) — el técnico observa el
    número cambiar en tiempo real en vez de mirar una foto fija."""
    ticks = duration_seconds // interval_seconds
    for _ in range(ticks):
        yield f"data: {json.dumps(snapshot_fn(), ensure_ascii=False)}\n\n"
        time.sleep(interval_seconds)


def battery_live_stream(duration_seconds=600, interval_seconds=2):
    """El técnico desconecta el cargador y observa: si el % baja de forma
    realista, la batería sostiene carga real; si se congela o el equipo se
    apaga, no la sostiene — sin depender del reporte estático de powercfg."""
    return _live_stream(battery_live_snapshot, duration_seconds, interval_seconds)


_ETHERNET_LIVE_SCRIPT = r"""
$out = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
    Where-Object { $_.MediaType -notmatch '802.11' } |
    ForEach-Object { [ordered]@{ name = $_.Name; status = [string]$_.Status } })
ConvertTo-Json -InputObject $out -Compress
""".strip()

_ETHERNET_STATUS_LABELS = {"Up": "Conectado", "Disconnected": "Desconectado", "Disabled": "Deshabilitado"}


def ethernet_live_snapshot():
    """Estado de los puertos de red cableada — para conectar un cable y ver
    en vivo si pasa de "Desconectado" a "Conectado", confirmando que el
    puerto físico y la placa de red funcionan de verdad."""
    output = _run_powershell(_ETHERNET_LIVE_SCRIPT, timeout=10)
    if output is None:
        return {"available": False}
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return {"available": False}
    if isinstance(data, dict):
        data = [data]
    if not data:
        return {"available": False}
    adaptadores = [
        {"name": a.get("name"), "status": _ETHERNET_STATUS_LABELS.get(a.get("status"), a.get("status"))}
        for a in data
    ]
    return {"available": True, "adapters": adaptadores}


def ethernet_live_stream(duration_seconds=600, interval_seconds=2):
    return _live_stream(ethernet_live_snapshot, duration_seconds, interval_seconds)


_USB_LIVE_SCRIPT = r"""
$d = @(Get-PnpDevice -Class USB -Status OK -ErrorAction SilentlyContinue | ForEach-Object { $_.FriendlyName })
ConvertTo-Json -InputObject ([ordered]@{ count = $d.Count; names = $d }) -Compress -Depth 3
""".strip()


def usb_live_snapshot():
    """Cantidad de dispositivos USB activos, para insertar algo puerto por
    puerto y ver el contador subir — sin tener que abrir el Administrador
    de dispositivos entre cada prueba."""
    output = _run_powershell(_USB_LIVE_SCRIPT, timeout=10)
    if not output:
        return {"available": False}
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return {"available": False}
    names = data.get("names")
    if names is None:
        names = []
    elif isinstance(names, str):
        names = [names]
    return {"available": True, "count": data.get("count", len(names)), "names": names}


def usb_live_stream(duration_seconds=600, interval_seconds=2):
    return _live_stream(usb_live_snapshot, duration_seconds, interval_seconds)


_MONITOR_LIVE_SCRIPT = r"""
$m = @(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBasicDisplayParams -ErrorAction SilentlyContinue)
ConvertTo-Json -InputObject ([ordered]@{ count = $m.Count }) -Compress
""".strip()


def monitor_output_live_snapshot():
    """Cantidad de monitores detectados, para conectar un cable HDMI/DP
    externo y ver el contador subir — confirma que el puerto de salida de
    video funciona sin instalar nada."""
    output = _run_powershell(_MONITOR_LIVE_SCRIPT, timeout=10)
    if not output:
        return {"available": False}
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return {"available": False}
    return {"available": True, "count": data.get("count", 0)}


def monitor_output_live_stream(duration_seconds=600, interval_seconds=2):
    return _live_stream(monitor_output_live_snapshot, duration_seconds, interval_seconds)


def check_ram():
    """Revisa el uso actual de memoria RAM."""
    if psutil is None:
        return _result("Memoria RAM", "unknown", None, "Falta el paquete psutil.")
    mem = psutil.virtual_memory()
    causes = ["Demasiados programas o pestañas abiertas", "Un proceso con fuga de memoria", "Malware consumiendo recursos", "RAM insuficiente para el uso actual del equipo"]
    fix = [
        "Abre el Administrador de tareas (Ctrl+Shift+Esc) y ordena por uso de memoria.",
        "Cierra programas o pestañas que no estés usando.",
        "Reinicia el equipo si un proceso específico no libera memoria.",
        "Si el problema es recurrente, evalúa ampliar la RAM instalada.",
    ]
    if mem.percent > 90:
        return _result("Memoria RAM", "critical", f"{mem.percent}%", "Uso de RAM muy alto.", causes=causes, fix=fix)
    if mem.percent > 75:
        return _result("Memoria RAM", "warning", f"{mem.percent}%", "Uso de RAM elevado.", causes=causes, fix=fix)
    return _result("Memoria RAM", "ok", f"{mem.percent}%", "Uso de memoria normal.")


def check_cpu():
    """Revisa la carga actual del procesador (muestra de 1 segundo)."""
    if psutil is None:
        return _result("Uso de CPU", "unknown", None, "Falta el paquete psutil.")
    percent = psutil.cpu_percent(interval=1)
    causes = ["Proceso específico consumiendo CPU (actualización, indexación, malware)", "Sobrecalentamiento causando throttling", "Virus o minero de criptomonedas oculto"]
    fix = [
        "Abre el Administrador de tareas y ordena por uso de CPU para identificar el proceso.",
        "Si el proceso es desconocido o sospechoso, ejecuta un análisis con el antivirus.",
        "Revisa que los ventiladores no estén tapados con polvo (sobrecalentamiento reduce el rendimiento).",
    ]
    if percent > 90:
        return _result("Uso de CPU", "critical", f"{percent}%", "CPU saturada.", causes=causes, fix=fix)
    if percent > 70:
        return _result("Uso de CPU", "warning", f"{percent}%", "Carga de CPU alta.", causes=causes, fix=fix)
    return _result("Uso de CPU", "ok", f"{percent}%", "Carga de CPU normal.")


_TEMP_SCRIPT = r"""
$cpuC = $null
try {
    $z = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |
         Sort-Object CurrentTemperature -Descending | Select-Object -First 1
    if ($z) { $cpuC = [math]::Round(($z.CurrentTemperature / 10) - 273.15, 1) }
} catch {}

$diskC = $null
try {
    $d = Get-PhysicalDisk -ErrorAction Stop | Get-StorageReliabilityCounter -ErrorAction Stop |
         Where-Object { $_.Temperature -gt 0 } | Sort-Object Temperature -Descending | Select-Object -First 1
    if ($d) { $diskC = $d.Temperature }
} catch {}

[ordered]@{ cpuC = $cpuC; diskC = $diskC } | ConvertTo-Json -Compress
""".strip()


def check_temperatures():
    """Temperatura de CPU (zona térmica ACPI) y del disco más caliente (SMART).
    No todos los fabricantes exponen la del CPU por WMI —cuando no está
    disponible, se informa así en vez de mostrar 'unknown' sin explicación."""
    if platform.system() != "Windows":
        return _result("Temperaturas", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_TEMP_SCRIPT, timeout=15)
    if not output:
        return _result("Temperaturas", "unknown", None, "No se pudo consultar (requiere permisos de administrador).")
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return _result("Temperaturas", "unknown", None, "No se pudo interpretar la respuesta.")

    cpu_c = data.get("cpuC")
    disk_c = data.get("diskC")
    if cpu_c is None and disk_c is None:
        return _result(
            "Temperaturas", "unknown", None,
            "Este equipo no expone sensores de temperatura por WMI (frecuente en notebooks; el fabricante no publica el dato).",
        )
    parts = []
    if cpu_c is not None:
        parts.append(f"CPU {cpu_c}°C")
    if disk_c is not None:
        parts.append(f"Disco {disk_c}°C")
    value = " / ".join(parts)
    causes = ["Ventiladores con polvo acumulado o pasta térmica seca", "Rejillas de ventilación obstruidas", "El equipo está sobre una superficie blanda (cama, sillón) que tapa la salida de aire"]
    fix = [
        "Limpia el polvo de ventiladores y disipadores (aire comprimido).",
        "Usa el equipo sobre una superficie dura y despejada, no sobre tela.",
        "Si es una notebook con varios años de uso, considera un cambio de pasta térmica.",
    ]
    if (cpu_c is not None and cpu_c >= 90) or (disk_c is not None and disk_c >= 60):
        return _result("Temperaturas", "critical", value, "Temperatura crítica: riesgo de throttling o apagado por protección térmica.", causes=causes, fix=fix)
    if (cpu_c is not None and cpu_c >= 80) or (disk_c is not None and disk_c >= 50):
        return _result("Temperaturas", "warning", value, "Temperatura elevada.", causes=causes, fix=fix)
    return _result("Temperaturas", "ok", value, "Temperaturas dentro de rango normal.")


def check_power_plan():
    """Plan de energía activo. 'Economizador de energía' limita la frecuencia
    del CPU incluso con el equipo enchufado — un clásico de "esta notebook
    anda lenta" que se soluciona sin tocar hardware."""
    if platform.system() != "Windows":
        return _result("Plan de energía", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(
        r"$g = (powercfg /getactivescheme); if ($g -match '\(([^)]+)\)') { $matches[1] }",
        timeout=15,
    )
    plan = (output or "").strip()
    if not plan:
        return _result("Plan de energía", "unknown", None, "No se pudo consultar el plan de energía activo.")
    bajo_rendimiento = any(p in plan.lower() for p in ("economizador", "power saver", "battery saver"))
    if bajo_rendimiento:
        return _result(
            "Plan de energía", "warning", plan,
            "El plan de energía activo limita el rendimiento del equipo.",
            causes=["Se activó 'Economizador de energía' manualmente o por baja batería en algún momento"],
            fix=["Cambia a 'Equilibrado' o 'Alto rendimiento' desde Configuración > Energía, o con `powercfg /setactive SCHEME_BALANCED`."],
        )
    return _result("Plan de energía", "ok", plan, "El plan de energía activo no limita el rendimiento.")


def check_uptime():
    """Días desde el último arranque. Un Windows que no se reinicia hace
    semanas acumula fugas de memoria, actualizaciones pendientes y procesos
    colgados — todo eso se siente como "el equipo cada vez anda más lento"."""
    if platform.system() != "Windows":
        return _result("Tiempo encendido sin reiniciar", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(
        "((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalHours",
        timeout=15,
    )
    try:
        hours = float((output or "").strip())
    except ValueError:
        return _result("Tiempo encendido sin reiniciar", "unknown", None, "No se pudo calcular el tiempo de actividad.")
    days = round(hours / 24, 1)
    value = f"{days} días"
    if days > 30:
        return _result(
            "Tiempo encendido sin reiniciar", "warning", value,
            "El equipo lleva mucho tiempo sin reiniciarse.",
            causes=["El equipo se suspende en vez de apagarse/reiniciarse", "Actualizaciones de Windows quedaron pendientes de un reinicio"],
            fix=["Reinicia el equipo: libera memoria acumulada y aplica actualizaciones pendientes."],
        )
    return _result("Tiempo encendido sin reiniciar", "ok", value, "Tiempo de actividad normal.")


def check_reliability_index():
    """Índice de confiabilidad de Windows (0-10): resume crasheos, instalaciones
    fallidas y errores de driver de los últimos días en un solo número, la
    misma fuente que usa el Monitor de confiabilidad (perfmon /rel)."""
    if platform.system() != "Windows":
        return _result("Índice de estabilidad de Windows", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(
        "(Get-CimInstance Win32_ReliabilityStabilityMetrics -ErrorAction Stop | "
        "Sort-Object TimeGenerated -Descending | Select-Object -First 1 -ExpandProperty SystemStabilityIndex)",
        timeout=20,
    )
    try:
        index = float((output or "").strip())
    except ValueError:
        return _result("Índice de estabilidad de Windows", "unknown", None, "No se pudo consultar (puede requerir que el equipo lleve más tiempo encendido).")
    value = f"{round(index, 1)}/10"
    causes = ["Crasheos o cierres inesperados de programas recientes", "Instalaciones o desinstalaciones fallidas", "Drivers que fallan al cargar"]
    fix = ["Abre el Monitor de confiabilidad (perfmon /rel) para ver el detalle día por día de qué generó cada baja."]
    if index < 4:
        return _result("Índice de estabilidad de Windows", "critical", value, "El sistema muestra inestabilidad importante en los últimos días.", causes=causes, fix=fix)
    if index < 7:
        return _result("Índice de estabilidad de Windows", "warning", value, "El sistema muestra cierta inestabilidad reciente.", causes=causes, fix=fix)
    return _result("Índice de estabilidad de Windows", "ok", value, "El sistema se muestra estable.")


def run_all():
    checks = [
        check_disk_space(), check_disk_health(), check_disk_smart(), check_ram(), check_cpu(),
        check_temperatures(), check_power_plan(), check_uptime(), check_reliability_index(),
    ]
    battery = check_battery_health()
    if battery is not None:
        checks.append(battery)
    return checks
