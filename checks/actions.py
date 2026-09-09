"""Acciones que el dashboard puede ejecutar con un clic.

`action_id` es el único dato que llega desde el cliente y se valida contra
esta whitelist explícita — nunca se interpola texto libre del navegador en
un comando. Cada ejecución queda registrada en `actions_log.jsonl` (en la
raíz del proyecto) para tener trazabilidad de qué se aplicó y cuándo.
"""
import json
import platform
import subprocess
from datetime import datetime

from .base import app_dir as _app_dir
from .base import flags as _flags
from .base import run as _run
from .base import run_powershell as _run_powershell

LOG_PATH = _app_dir() / "actions_log.jsonl"


def _log(action_id: str, success: bool, output: str) -> None:
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action_id": action_id,
        "success": success,
        "output": (output or "")[:2000],
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _flush_dns():
    output = _run(["ipconfig", "/flushdns"])
    return output is not None, output or "Sin salida."


def _renew_ip():
    out1 = _run(["ipconfig", "/release"], timeout=20)
    out2 = _run(["ipconfig", "/renew"], timeout=30)
    ok = out1 is not None and out2 is not None
    return ok, f"{out1 or ''}\n{out2 or ''}".strip()


def _reset_winsock():
    output = _run(["netsh", "winsock", "reset"])
    return output is not None, (output or "") + "\n\nRequiere reiniciar el equipo para que el cambio surta efecto."


def _restart_spooler():
    output = _run_powershell("Restart-Service -Name Spooler -Force -ErrorAction Stop; (Get-Service Spooler).Status", timeout=20)
    ok = (output or "").strip().lower() == "running"
    return ok, output or "No se pudo reiniciar (requiere permisos de administrador)."


def _clear_temp_files():
    output = _run_powershell(
        "$before = (Get-ChildItem $env:TEMP -Recurse -Force -ErrorAction SilentlyContinue | "
        "Measure-Object -Property Length -Sum).Sum; "
        "Get-ChildItem $env:TEMP -Force -ErrorAction SilentlyContinue | "
        "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; "
        "$after = (Get-ChildItem $env:TEMP -Recurse -Force -ErrorAction SilentlyContinue | "
        "Measure-Object -Property Length -Sum).Sum; "
        "$freed = [math]::Round((([double]$before - [double]$after) / 1MB), 1); "
        "\"Liberado: $freed MB\"",
        timeout=60,
    )
    return output is not None, output or "Sin salida."


def _restart_explorer():
    subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"], capture_output=True, creationflags=_flags())
    subprocess.Popen(["explorer.exe"], creationflags=_flags())
    return True, "Explorer reiniciado."


def _dism_restore_health():
    output = _run(["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], timeout=1800)
    return output is not None, output or "Sin salida."


def _repair_windows_update():
    """Reinicia los componentes de Windows Update: para los servicios,
    renombra (no borra) las carpetas de caché para que se regeneren solas,
    y vuelve a arrancar los servicios. Es el fix clásico para updates que
    quedan trabados o dan error repetido."""
    script = r"""
    Stop-Service -Name wuauserv,bits,cryptsvc,msiserver -Force -ErrorAction SilentlyContinue
    $stamp = Get-Date -Format "yyyyMMddHHmmss"
    $sd = "$env:windir\SoftwareDistribution"
    $cr = "$env:windir\System32\catroot2"
    if (Test-Path $sd) { Rename-Item $sd "SoftwareDistribution.bak_$stamp" -ErrorAction SilentlyContinue }
    if (Test-Path $cr) { Rename-Item $cr "catroot2.bak_$stamp" -ErrorAction SilentlyContinue }
    Start-Service -Name cryptsvc,bits,wuauserv,msiserver -ErrorAction SilentlyContinue
    (Get-Service wuauserv).Status
    """
    output = _run_powershell(script, timeout=60)
    ok = (output or "").strip().lower() == "running"
    return ok, (output or "No se pudo reiniciar el servicio Windows Update (requiere permisos de administrador).")


def _winget_update_all():
    output = _run(
        ["winget", "upgrade", "--all", "--silent", "--accept-package-agreements", "--accept-source-agreements"],
        timeout=1800,
    )
    return output is not None, output or "Sin salida (¿winget no está instalado?)."


def _sync_time():
    """Resincroniza el reloj contra el servidor de hora. Arranca el servicio
    W32Time primero porque la causa más común de reloj desfasado es que el
    servicio esté detenido — sin eso, `w32tm /resync` falla igual."""
    _run_powershell(
        "Set-Service -Name W32Time -StartupType Automatic -ErrorAction SilentlyContinue; "
        "Start-Service -Name W32Time -ErrorAction SilentlyContinue",
        timeout=30,
    )
    output = _run(["w32tm", "/resync", "/force"], timeout=60)
    ok = output is not None and "error" not in (output or "").lower()
    return ok, output or "No se pudo resincronizar (requiere permisos de administrador)."


def _reset_tcpip():
    output = _run(["netsh", "int", "ip", "reset"], timeout=60)
    return output is not None, (output or "") + "\n\nRequiere reiniciar el equipo para que el cambio surta efecto."


def _flush_arp():
    out1 = _run(["netsh", "interface", "ip", "delete", "arpcache"], timeout=30)
    out2 = _run(["nbtstat", "-R"], timeout=30)
    ok = out1 is not None or out2 is not None
    return ok, f"{out1 or ''}\n{out2 or ''}".strip() or "Cachés ARP/NetBIOS vaciadas."


def _clear_print_queue():
    """Vacía la cola de impresión de raíz: para el spooler, borra los trabajos
    atascados en disco y lo vuelve a arrancar. Más efectivo que solo reiniciar
    el servicio cuando hay un trabajo corrupto que lo tumba al arrancar."""
    script = r"""
    Stop-Service -Name Spooler -Force -ErrorAction SilentlyContinue
    $spool = "$env:windir\System32\spool\PRINTERS"
    $n = 0
    if (Test-Path $spool) {
        $items = Get-ChildItem $spool -Force -ErrorAction SilentlyContinue
        $n = ($items | Measure-Object).Count
        $items | Remove-Item -Force -ErrorAction SilentlyContinue
    }
    Start-Service -Name Spooler -ErrorAction SilentlyContinue
    "$((Get-Service Spooler).Status)|$n"
    """
    output = _run_powershell(script, timeout=60)
    parts = (output or "").strip().split("|")
    ok = len(parts) == 2 and parts[0].lower() == "running"
    if ok:
        return True, f"Cola vaciada ({parts[1]} trabajos eliminados). Servicio de impresión en ejecución."
    return False, output or "No se pudo vaciar la cola (requiere permisos de administrador)."


def _update_defender():
    output = _run_powershell(
        'try { Update-MpSignature -ErrorAction Stop; "OK: firmas actualizadas" } '
        'catch { "ERROR: " + $_.Exception.Message }',
        timeout=300,
    )
    ok = (output or "").startswith("OK")
    return ok, output or "No se pudieron actualizar las firmas de Defender."


def _quick_scan_defender():
    output = _run_powershell(
        'try { Start-MpScan -ScanType QuickScan -ErrorAction Stop; '
        '$t = (Get-MpComputerStatus).QuickScanEndTime; "OK: análisis rápido completado ($t)" } '
        'catch { "ERROR: " + $_.Exception.Message }',
        timeout=1800,
    )
    ok = (output or "").startswith("OK")
    return ok, output or "No se pudo ejecutar el análisis rápido."


def _cleanup_component_store():
    """Borra las versiones viejas de componentes que Windows guarda tras cada
    actualización (WinSxS). Suele liberar varios GB. Es irreversible en el
    sentido de que ya no se pueden desinstalar esas actualizaciones."""
    output = _run(["DISM", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], timeout=1800)
    return output is not None, output or "Sin salida."


def _empty_recycle_bin():
    output = _run_powershell(
        'try { Clear-RecycleBin -Force -ErrorAction Stop; "OK: papelera vaciada" } '
        'catch { if ($_.Exception.Message -match "empty|vac") { "OK: la papelera ya estaba vacía" } '
        'else { "ERROR: " + $_.Exception.Message } }',
        timeout=300,
    )
    ok = (output or "").startswith("OK")
    return ok, output or "No se pudo vaciar la papelera."


def _optimize_volume():
    """Optimiza la unidad C:. Windows elige solo la operación correcta según
    el medio: TRIM en SSD, desfragmentación en disco mecánico."""
    output = _run_powershell(
        'try { Optimize-Volume -DriveLetter C -ErrorAction Stop; "OK: unidad C: optimizada" } '
        'catch { "ERROR: " + $_.Exception.Message }',
        timeout=1800,
    )
    ok = (output or "").startswith("OK")
    return ok, output or "No se pudo optimizar la unidad (requiere permisos de administrador)."


def _chkdsk_scan():
    """Escaneo online de la unidad C: — solo detecta y reporta, no repara ni
    requiere reiniciar (a diferencia de `chkdsk /f`, que desmonta el volumen)."""
    output = _run(["chkdsk", "C:", "/scan"], timeout=1800)
    return output is not None, output or "Sin salida (requiere permisos de administrador)."


def _reset_store():
    output = _run(["wsreset.exe"], timeout=180)
    return output is not None, (output or "") + "\n\nCaché de la Microsoft Store reiniciada."


def _gpupdate():
    output = _run(["gpupdate", "/force"], timeout=300)
    ok = output is not None
    return ok, output or "No se pudieron actualizar las directivas de grupo."


def _restart_audio():
    output = _run_powershell(
        "Restart-Service -Name Audiosrv,AudioEndpointBuilder -Force -ErrorAction SilentlyContinue; "
        "(Get-Service Audiosrv).Status",
        timeout=60,
    )
    ok = (output or "").strip().lower() == "running"
    return ok, output or "No se pudieron reiniciar los servicios de audio (requiere permisos de administrador)."


def _restart_search():
    output = _run_powershell(
        "Restart-Service -Name WSearch -Force -ErrorAction SilentlyContinue; "
        "(Get-Service WSearch).Status",
        timeout=60,
    )
    ok = (output or "").strip().lower() == "running"
    return ok, output or "No se pudo reiniciar Windows Search (requiere permisos de administrador)."


def _rebuild_icon_cache():
    """Borra las bases de caché de íconos/miniaturas y reinicia Explorer. Se
    regeneran solas; es el fix para íconos en blanco o cambiados."""
    subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"], capture_output=True, creationflags=_flags())
    removed = _run_powershell(
        r'$p = "$env:LOCALAPPDATA\Microsoft\Windows\Explorer"; '
        r'$n = 0; '
        r'Get-ChildItem "$p\iconcache*.db","$p\thumbcache*.db" -Force -ErrorAction SilentlyContinue | '
        r'ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue; $n++ }; '
        r'Remove-Item "$env:LOCALAPPDATA\IconCache.db" -Force -ErrorAction SilentlyContinue; '
        r'"$n"',
        timeout=60,
    )
    subprocess.Popen(["explorer.exe"], creationflags=_flags())
    return True, f"Caché de íconos reconstruida ({(removed or '0').strip()} archivos borrados). Explorer reiniciado."


ACTIONS = {
    "flush_dns": {
        "label": "Limpiar caché DNS",
        "description": "Ejecuta ipconfig /flushdns.",
        "run": _flush_dns,
    },
    "renew_ip": {
        "label": "Renovar IP",
        "description": "Ejecuta ipconfig /release y /renew. Corta la red un momento.",
        "run": _renew_ip,
    },
    "reset_winsock": {
        "label": "Reiniciar Winsock",
        "description": "Ejecuta netsh winsock reset. Requiere reiniciar el equipo después.",
        "run": _reset_winsock,
    },
    "restart_spooler": {
        "label": "Reiniciar cola de impresión",
        "description": "Reinicia el servicio Spooler (requiere permisos de administrador).",
        "run": _restart_spooler,
    },
    "clear_temp_files": {
        "label": "Limpiar archivos temporales",
        "description": "Borra el contenido de %TEMP% del usuario actual.",
        "run": _clear_temp_files,
    },
    "restart_explorer": {
        "label": "Reiniciar Explorer",
        "description": "Cierra y vuelve a abrir explorer.exe (para íconos/barra de tareas trabados).",
        "run": _restart_explorer,
    },
    "dism_restore_health": {
        "label": "Reparar imagen de Windows (DISM)",
        "description": "Ejecuta DISM /RestoreHealth. Puede tardar 15-30 minutos.",
        "run": _dism_restore_health,
    },
    "repair_windows_update": {
        "label": "Reparar Windows Update",
        "description": "Reinicia los servicios de Update y renombra la caché para que se regenere (requiere administrador).",
        "run": _repair_windows_update,
    },
    "winget_update_all": {
        "label": "Actualizar todas las apps (winget)",
        "description": "Ejecuta winget upgrade --all. Puede tardar varios minutos según cuántas apps tengan actualización.",
        "run": _winget_update_all,
    },
    "sync_time": {
        "label": "Sincronizar la hora",
        "description": "Arranca el servicio 'Hora de Windows' y ejecuta w32tm /resync. Arregla errores de certificado por reloj desfasado.",
        "run": _sync_time,
    },
    "reset_tcpip": {
        "label": "Reiniciar la pila TCP/IP",
        "description": "Ejecuta netsh int ip reset. Requiere reiniciar el equipo después.",
        "run": _reset_tcpip,
    },
    "flush_arp": {
        "label": "Vaciar cachés ARP y NetBIOS",
        "description": "Ejecuta netsh interface ip delete arpcache y nbtstat -R.",
        "run": _flush_arp,
    },
    "clear_print_queue": {
        "label": "Vaciar cola de impresión",
        "description": "Para el spooler, borra los trabajos atascados en spool\\PRINTERS y lo vuelve a arrancar (requiere administrador).",
        "run": _clear_print_queue,
    },
    "update_defender": {
        "label": "Actualizar firmas de Defender",
        "description": "Ejecuta Update-MpSignature para descargar las definiciones de virus más recientes.",
        "run": _update_defender,
    },
    "quick_scan_defender": {
        "label": "Análisis rápido de Defender",
        "description": "Ejecuta un análisis rápido de Microsoft Defender. Puede tardar varios minutos.",
        "run": _quick_scan_defender,
    },
    "cleanup_component_store": {
        "label": "Limpiar componentes viejos de Windows",
        "description": "DISM /StartComponentCleanup: borra versiones viejas de actualizaciones (suele liberar varios GB). Después ya no se pueden desinstalar esas actualizaciones.",
        "run": _cleanup_component_store,
    },
    "empty_recycle_bin": {
        "label": "Vaciar papelera de reciclaje",
        "description": "Elimina definitivamente el contenido de la papelera de todas las unidades.",
        "run": _empty_recycle_bin,
    },
    "optimize_volume": {
        "label": "Optimizar la unidad C:",
        "description": "Ejecuta Optimize-Volume: TRIM si es SSD, desfragmentación si es disco mecánico (Windows elige solo).",
        "run": _optimize_volume,
    },
    "chkdsk_scan": {
        "label": "Escanear el disco (chkdsk /scan)",
        "description": "Escaneo en caliente de la unidad C:. Solo detecta y reporta, no repara ni pide reiniciar.",
        "run": _chkdsk_scan,
    },
    "reset_store": {
        "label": "Reiniciar caché de Microsoft Store",
        "description": "Ejecuta wsreset.exe, para apps de la Store que no abren o no actualizan.",
        "run": _reset_store,
    },
    "gpupdate": {
        "label": "Actualizar directivas de grupo",
        "description": "Ejecuta gpupdate /force. Útil en equipos de dominio con políticas recién aplicadas.",
        "run": _gpupdate,
    },
    "restart_audio": {
        "label": "Reiniciar servicios de audio",
        "description": "Reinicia Audiosrv y AudioEndpointBuilder (sin sonido, dispositivos que no aparecen).",
        "run": _restart_audio,
    },
    "restart_search": {
        "label": "Reiniciar Windows Search",
        "description": "Reinicia el servicio de búsqueda, para cuando el menú Inicio no encuentra nada.",
        "run": _restart_search,
    },
    "rebuild_icon_cache": {
        "label": "Reconstruir caché de íconos",
        "description": "Borra las bases de caché de íconos y miniaturas y reinicia Explorer (íconos en blanco o cambiados).",
        "run": _rebuild_icon_cache,
    },
}


def run_action(action_id: str) -> dict:
    if platform.system() != "Windows":
        return {"status": "error", "output": "Solo disponible en Windows."}
    action = ACTIONS.get(action_id)
    if action is None:
        return {"status": "error", "output": "Acción no reconocida."}
    try:
        success, output = action["run"]()
    except Exception as exc:
        success, output = False, str(exc)
    _log(action_id, success, output)
    return {"status": "ok" if success else "error", "output": output}


def list_actions() -> list:
    return [{"id": aid, "label": a["label"], "description": a["description"]} for aid, a in ACTIONS.items()]
