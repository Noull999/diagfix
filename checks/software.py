"""Diagnósticos de sistema operativo y software (Windows)."""
import json
import platform
import socket
import struct
import time
from datetime import datetime

from .base import result as _result
from .base import run as _run
from .base import run_parallel as _run_parallel
from .base import run_powershell as _run_powershell

if platform.system() == "Windows":
    import winreg

_BSOD_SCRIPT = r"""
$dumps = Get-ChildItem -Path "$env:SystemRoot\Minidump" -Filter *.dmp -ErrorAction SilentlyContinue
$dumpCount = if ($dumps) { ($dumps | Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-30) }).Count } else { 0 }
$kp41 = 0
try {
    $kp41 = (Get-WinEvent -FilterHashtable @{LogName="System";ProviderName="Microsoft-Windows-Kernel-Power";Id=41;StartTime=(Get-Date).AddDays(-7)} -ErrorAction Stop | Measure-Object).Count
} catch {}
[ordered]@{ dumps30d = $dumpCount; kernelPower41_7d = $kp41 } | ConvertTo-Json -Compress
""".strip()

_DEFENDER_SCRIPT = r"""
$mp = Get-MpComputerStatus -ErrorAction SilentlyContinue
$fw = Get-NetFirewallProfile -ErrorAction SilentlyContinue
[ordered]@{
    available = [bool]$mp
    rtp       = if ($mp) { [bool]$mp.RealTimeProtectionEnabled } else { $null }
    sigAgeDays = if ($mp) { $mp.AntivirusSignatureAge } else { $null }
    fwAllOn   = if ($fw) { -not ($fw | Where-Object { -not $_.Enabled }) } else { $null }
} | ConvertTo-Json -Compress
""".strip()

_DEVICES_SCRIPT = r"""
Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
    Where-Object { $_.ConfigManagerErrorCode -and $_.ConfigManagerErrorCode -ne 0 } |
    Select-Object Name, ConfigManagerErrorCode |
    ConvertTo-Json -Compress
""".strip()

# Códigos de Win32_PnPEntity.ConfigManagerErrorCode — estables desde Windows
# XP, documentados por Microsoft (CM_PROB_* en cfgmgr32.h). Dan el motivo real
# en vez de solo el nombre del dispositivo fallado.
_DEVICE_ERROR_LABELS = {
    1: "no está configurado correctamente",
    3: "el driver puede estar dañado, o falta memoria/recursos del sistema",
    9: "el firmware no entrega la información de recursos correcta",
    10: "no puede iniciar",
    12: "no hay suficientes recursos libres (IRQ/memoria) para usarlo",
    14: "necesita que reinicies el equipo para terminar de configurarse",
    16: "Windows no pudo identificar todos sus recursos",
    18: "hay que reinstalar los drivers",
    19: "la configuración en el registro está dañada o incompleta",
    21: "Windows lo está quitando (reinicia el equipo)",
    22: "está deshabilitado",
    24: "no está presente, no funciona, o le faltan drivers",
    28: "no tiene los drivers instalados",
    29: "está deshabilitado por el firmware/BIOS",
    31: "no está funcionando correctamente (drivers faltantes)",
    32: "el servicio asociado está deshabilitado",
    33: "Windows no puede determinar qué recursos necesita",
    34: "hay que configurar sus recursos manualmente",
    35: "el firmware no da suficiente información sobre este dispositivo",
    36: "requiere una interrupción (IRQ) que no está disponible",
    37: "el driver devolvió un error al cargar",
    38: "no se pudo cargar el driver (ya hay una versión anterior en memoria)",
    39: "el driver falta o está dañado",
    40: "falta o está incorrecta la información del servicio en el registro",
    41: "el driver cargó pero no encontró el dispositivo (¿se desconectó?)",
    42: "hay un dispositivo duplicado",
    43: "el propio driver reportó una falla del dispositivo",
    44: "una aplicación o servicio lo apagó",
    45: "el dispositivo se desconectó (ya no está presente)",
    47: "se puede quitar de forma segura, pero no se puede iniciar ahora",
    48: "el driver está bloqueado por problemas conocidos",
}


def _device_error_detail(code):
    label = _DEVICE_ERROR_LABELS.get(code)
    if label:
        return f"{label} (código {code})"
    return f"código de error {code}"

_SPOOLER_SCRIPT = "(Get-Service -Name Spooler -ErrorAction SilentlyContinue).Status"

_BITLOCKER_SCRIPT = r"""
try {
    $vols = Get-BitLockerVolume -ErrorAction Stop
    $out = @($vols | ForEach-Object { [ordered]@{ mount = $_.MountPoint; status = [string]$_.ProtectionStatus } })
    [ordered]@{ available = $true; volumes = $out } | ConvertTo-Json -Compress
} catch {
    [ordered]@{ available = $false } | ConvertTo-Json -Compress
}
""".strip()


def _ntp_offset_seconds(host="pool.ntp.org", timeout=3):
    """Diferencia (segundos) entre la hora local y un servidor NTP público.
    Se implementa en Python puro para no depender de parsear `w32tm`
    (localizado y de formato inestable entre versiones de Windows)."""
    packet = b"\x1b" + 47 * b"\0"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        t0 = time.time()
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(48)
        t1 = time.time()
    finally:
        sock.close()
    unpacked = struct.unpack("!12I", data)
    ntp_time = unpacked[10] + float(unpacked[11]) / 2**32 - 2208988800
    local_mid = (t0 + t1) / 2
    return ntp_time - local_mid


def check_pending_reboot():
    """Revisa llaves de registro típicas que indican un reinicio pendiente."""
    if platform.system() != "Windows":
        return _result("Reinicio pendiente", "unknown", None, "Chequeo disponible solo en Windows.")
    keys_to_check = [
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\PendingFileRenameOperations"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"),
    ]
    for hive, path in keys_to_check:
        try:
            with winreg.OpenKey(hive, path):
                return _result(
                    "Reinicio pendiente", "warning", "Sí",
                    "Hay cambios que requieren reiniciar el equipo.",
                    causes=["Actualización de Windows instalada recientemente", "Instalación de software que requirió reinicio"],
                    fix=["Guarda el trabajo abierto y reinicia el equipo para aplicar los cambios pendientes."],
                )
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return _result("Reinicio pendiente", "ok", "No", "No hay reinicios pendientes.")


def check_last_update():
    """Consulta la fecha de la última actualización de Windows instalada.

    Se le pide a PowerShell que formatee la fecha (ISO, sin nombre de día ni
    mes en el idioma del sistema) para no depender de `strptime` sobre texto
    localizado — antes fallaba con "unknown" porque `Get-HotFix` devuelve
    fechas tipo "Wednesday, August 12, 2026 12:00:00 AM".
    """
    if platform.system() != "Windows":
        return _result("Últimas actualizaciones", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(
        "Get-HotFix | Sort-Object InstalledOn -Descending | "
        "Select-Object -First 1 -ExpandProperty InstalledOn | "
        "ForEach-Object { $_.ToString('yyyy-MM-dd') }"
    )
    if not output:
        return _result("Últimas actualizaciones", "unknown", None,
                        "No se pudo obtener el historial de actualizaciones.")
    try:
        installed = datetime.strptime(output.strip(), "%Y-%m-%d")
        days_ago = (datetime.now() - installed).days
    except Exception:
        return _result("Últimas actualizaciones", "unknown", output, "No se pudo interpretar la fecha.")
    causes = ["Windows Update deshabilitado o pausado", "El equipo lleva tiempo sin estar conectado a Internet", "El servicio 'Windows Update' está detenido"]
    fix = [
        "Ve a Configuración > Windows Update y ejecuta 'Buscar actualizaciones' manualmente.",
        "Revisa en services.msc que el servicio 'Windows Update' esté en modo automático y en ejecución.",
        "Confirma que las actualizaciones no estén pausadas manualmente.",
    ]
    if days_ago > 90:
        return _result("Últimas actualizaciones", "critical", f"hace {days_ago} días",
                        "Hace más de 3 meses que no se instalan actualizaciones.", causes=causes, fix=fix,
                        action_id="repair_windows_update")
    if days_ago > 30:
        return _result("Últimas actualizaciones", "warning", f"hace {days_ago} días",
                        "Han pasado más de 30 días desde la última actualización.", causes=causes, fix=fix,
                        action_id="repair_windows_update")
    return _result("Últimas actualizaciones", "ok", f"hace {days_ago} días", "Actualizaciones al día.")


def check_event_log_errors():
    """Cuenta errores del Visor de Eventos (log System) en las últimas 24 horas.

    Distingue explícitamente "no se pudo consultar" (sin permisos, servicio
    caído) de "0 errores" — antes ambos casos devolvían `ok, 0`, un falso
    verde cuando en realidad el chequeo no corrió.
    """
    if platform.system() != "Windows":
        return _result("Errores en el registro de eventos (24h)", "unknown", None,
                        "Chequeo disponible solo en Windows.")
    output = _run_powershell(
        "try { "
        "(Get-WinEvent -FilterHashtable @{LogName='System';Level=2;StartTime=(Get-Date).AddHours(-24)} "
        "-ErrorAction Stop | Measure-Object).Count "
        "} catch [Exception] { "
        "if ($_.Exception.Message -match 'No events|No se encontraron') { 0 } else { 'ERROR' } "
        "}"
    )
    if output is None or output == "" or output == "ERROR":
        return _result("Errores en el registro de eventos (24h)", "unknown", None,
                        "No se pudo consultar el registro de eventos (puede requerir permisos de administrador).")
    try:
        count = int(output)
    except ValueError:
        return _result("Errores en el registro de eventos (24h)", "unknown", None,
                        "No se pudo leer el registro de eventos (puede requerir permisos de administrador).")
    causes = ["Driver con fallas o desactualizado", "Un servicio que falla al iniciar", "Hardware con problemas intermitentes", "Archivos de sistema corruptos"]
    fix = [
        "Abre el Visor de Eventos (eventvwr.msc) y revisa el detalle de los errores más frecuentes.",
        "Actualiza los drivers desde el Administrador de dispositivos o el sitio del fabricante.",
        "Ejecuta `sfc /scannow` para revisar archivos de sistema (usa el botón de verificación profunda de este panel).",
    ]
    if count > 20:
        return _result("Errores en el registro de eventos (24h)", "critical", str(count),
                        "Cantidad alta de errores del sistema.", causes=causes, fix=fix,
                        action_id="dism_restore_health")
    if count > 5:
        return _result("Errores en el registro de eventos (24h)", "warning", str(count),
                        "Se registraron varios errores del sistema recientes.", causes=causes, fix=fix)
    return _result("Errores en el registro de eventos (24h)", "ok", str(count), "Pocos o ningún error reciente.")


def check_startup_items():
    """Cuenta los programas configurados para iniciar junto con Windows."""
    if platform.system() != "Windows":
        return _result("Programas de inicio", "unknown", None, "Chequeo disponible solo en Windows.")
    total = 0
    paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ]
    for hive, path in paths:
        try:
            with winreg.OpenKey(hive, path) as key:
                total += winreg.QueryInfoKey(key)[1]
        except Exception:
            continue
    if total > 15:
        return _result(
            "Programas de inicio", "warning", str(total),
            "Muchos programas inician con Windows; puede ralentizar el arranque.",
            causes=["Programas que se agregan solos al instalarse (barras de herramientas, actualizadores, etc.)"],
            fix=["Abre el Administrador de tareas > pestaña 'Inicio' y deshabilita los que no sean necesarios."],
        )
    return _result("Programas de inicio", "ok", str(total), "Cantidad normal de programas de inicio.")


def check_bsod():
    """Cuenta pantallazos azules recientes: minidumps (30 días) y eventos
    Kernel-Power ID 41 (7 días, apagones/reinicios inesperados no
    controlados). No requiere permisos de administrador."""
    if platform.system() != "Windows":
        return _result("Pantallazos azules / reinicios inesperados", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_BSOD_SCRIPT, timeout=20)
    if not output:
        return _result("Pantallazos azules / reinicios inesperados", "unknown", None, "No se pudo consultar.")
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return _result("Pantallazos azules / reinicios inesperados", "unknown", None, "No se pudo interpretar la respuesta.")
    dumps = data.get("dumps30d") or 0
    kp41 = data.get("kernelPower41_7d") or 0
    causes = ["Driver con fallas (video, red, chipset)", "Sobrecalentamiento o fuente de poder insuficiente", "Módulo de RAM defectuoso", "Archivos de sistema corruptos"]
    fix = [
        "Actualiza los drivers, en especial el de video y chipset, desde el sitio del fabricante.",
        "Revisa temperaturas y que los ventiladores no estén tapados con polvo.",
        "Ejecuta `sfc /scannow` y un diagnóstico de memoria (`mdsched.exe`) si persiste.",
    ]
    if dumps > 2 or kp41 > 2:
        return _result("Pantallazos azules / reinicios inesperados", "critical",
                        f"{dumps} minidumps (30d), {kp41} apagones abruptos (7d)",
                        "El equipo se reinició o colapsó de forma inesperada varias veces.",
                        causes=causes, fix=fix)
    if dumps > 0 or kp41 > 0:
        return _result("Pantallazos azules / reinicios inesperados", "warning",
                        f"{dumps} minidumps (30d), {kp41} apagones abruptos (7d)",
                        "Se detectó al menos un reinicio o colapso inesperado reciente.",
                        causes=causes, fix=fix)
    return _result("Pantallazos azules / reinicios inesperados", "ok", "0", "Sin colapsos ni apagones abruptos recientes.")


def check_defender_firewall():
    """Estado de Microsoft Defender (protección en tiempo real, antigüedad de
    firmas) y de los perfiles del Firewall de Windows."""
    if platform.system() != "Windows":
        return _result("Antivirus y firewall", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_DEFENDER_SCRIPT, timeout=15)
    if not output:
        return _result("Antivirus y firewall", "unknown", None, "No se pudo consultar.")
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return _result("Antivirus y firewall", "unknown", None, "No se pudo interpretar la respuesta.")
    if not data.get("available"):
        return _result("Antivirus y firewall", "unknown", None,
                        "No se pudo consultar Defender (puede haber otro antivirus instalado).")

    rtp = data.get("rtp")
    sig_age = data.get("sigAgeDays")
    fw_on = data.get("fwAllOn")
    causes = ["Protección en tiempo real desactivada manualmente o por otro antivirus", "Firewall desactivado en alguno de los perfiles de red", "Definiciones de virus desactualizadas por falta de conexión"]
    fix = [
        "Activa la protección en tiempo real en Seguridad de Windows > Protección contra virus y amenazas.",
        "Revisa que el Firewall de Windows esté activado en Seguridad de Windows > Firewall y protección de red.",
        "Ejecuta 'Buscar actualizaciones' en Seguridad de Windows para refrescar las definiciones de virus.",
    ]
    problems = []
    if rtp is False:
        problems.append("protección en tiempo real desactivada")
    if fw_on is False:
        problems.append("firewall desactivado en algún perfil")
    if sig_age is not None and sig_age > 14:
        problems.append(f"definiciones con {sig_age} días de antigüedad")

    if rtp is False or fw_on is False:
        return _result("Antivirus y firewall", "critical", "; ".join(problems), "El equipo tiene protección desactivada.", causes=causes, fix=fix)
    if problems:
        # Las firmas viejas sí se arreglan con un comando; la protección
        # desactivada no (Tamper Protection bloquea reactivarla por script).
        stale_signatures = sig_age is not None and sig_age > 14
        return _result("Antivirus y firewall", "warning", "; ".join(problems),
                        "Hay detalles de seguridad para revisar.", causes=causes, fix=fix,
                        action_id="update_defender" if stale_signatures else None)
    return _result("Antivirus y firewall", "ok", "Activo", "Protección en tiempo real y firewall activos.")


def check_error_devices():
    """Dispositivos con error en el Administrador de dispositivos. A
    diferencia de solo listar el nombre, acá se traduce el
    ConfigManagerErrorCode (1-49, estable desde Windows XP) a texto — para
    que diga "driver no instalado" en vez de dejar al técnico adivinar por
    qué ese dispositivo en particular quedó marcado con la advertencia."""
    if platform.system() != "Windows":
        return _result("Dispositivos con error", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_DEVICES_SCRIPT, timeout=15)
    if output is None:
        return _result("Dispositivos con error", "unknown", None, "No se pudo consultar.")
    output = output.strip()
    if not output:
        return _result("Dispositivos con error", "ok", "0", "Ningún dispositivo reporta error.")
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return _result("Dispositivos con error", "unknown", None, "No se pudo interpretar la respuesta.")
    if isinstance(data, dict):
        data = [data]

    detalles = []
    for d in data:
        nombre = (d.get("Name") or "Dispositivo desconocido").strip()
        codigo = d.get("ConfigManagerErrorCode")
        detalles.append(f"{nombre}: {_device_error_detail(codigo)}")
    if not detalles:
        return _result("Dispositivos con error", "ok", "0", "Ningún dispositivo reporta error.")
    return _result(
        "Dispositivos con error", "warning", "; ".join(detalles),
        "Hay dispositivos con error en el Administrador de dispositivos.",
        causes=["Driver faltante, dañado o incompatible", "Dispositivo defectuoso o mal conectado", "El dispositivo está deshabilitado o su servicio no arranca"],
        fix=[
            "Abre el Administrador de dispositivos (devmgmt.msc) y busca el dispositivo por su nombre.",
            "El detalle de este chequeo ya indica la causa más probable según el código de error de Windows.",
            "Si dice 'faltan drivers', reinstala el driver desde el sitio del fabricante. Si dice 'deshabilitado', habilítalo con clic derecho > Habilitar dispositivo.",
        ],
    )


def check_print_spooler():
    """Estado del servicio de cola de impresión (clásico de tickets de
    'no puedo imprimir')."""
    if platform.system() != "Windows":
        return _result("Servicio de impresión", "unknown", None, "Chequeo disponible solo en Windows.")
    status = _run_powershell(_SPOOLER_SCRIPT, timeout=10)
    status = (status or "").strip()
    if not status:
        return _result("Servicio de impresión", "unknown", None, "No se detectó el servicio Spooler.")
    if status.lower() != "running":
        return _result(
            "Servicio de impresión", "critical", status,
            "El servicio de cola de impresión no está en ejecución.",
            causes=["El servicio Spooler se detuvo o falló", "Un trabajo de impresión corrupto lo bloqueó"],
            fix=[
                "Abre 'Servicios' (services.msc), busca 'Cola de impresión' e inícialo.",
                "Si vuelve a caerse, borra los archivos en C:\\Windows\\System32\\spool\\PRINTERS y reinicia el servicio.",
            ],
            action_id="clear_print_queue",
        )
    return _result("Servicio de impresión", "ok", "Running", "El servicio de cola de impresión funciona normalmente.")


def check_bitlocker():
    """Estado de cifrado BitLocker de la unidad del sistema. No disponible en
    ediciones Home de Windows (solo Pro/Enterprise/Education)."""
    if platform.system() != "Windows":
        return _result("Cifrado de disco (BitLocker)", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_BITLOCKER_SCRIPT, timeout=15)
    if not output:
        return _result("Cifrado de disco (BitLocker)", "unknown", None, "No se pudo consultar.")
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return _result("Cifrado de disco (BitLocker)", "unknown", None, "No se pudo interpretar la respuesta.")
    if not data.get("available"):
        return _result("Cifrado de disco (BitLocker)", "unknown", None,
                        "No disponible (edición Home de Windows, o requiere permisos de administrador).")

    volumes = data.get("volumes") or []
    if isinstance(volumes, dict):
        volumes = [volumes]
    os_vol = next((v for v in volumes if v.get("mount") == "C:"), volumes[0] if volumes else None)
    if not os_vol:
        return _result("Cifrado de disco (BitLocker)", "unknown", None, "Sin datos de volúmenes.")

    status = (os_vol.get("status") or "").lower()
    if status == "on":
        return _result("Cifrado de disco (BitLocker)", "ok", "Activado", "La unidad del sistema está cifrada.")
    return _result(
        "Cifrado de disco (BitLocker)", "warning", "Desactivado",
        "La unidad del sistema no está cifrada.",
        causes=["BitLocker nunca se activó en este equipo"],
        fix=["Si el equipo maneja información sensible o es una notebook que puede perderse/robarse, evalúa activar BitLocker desde el Panel de Control > Cifrado de unidad BitLocker."],
    )


def check_time_sync():
    """Compara la hora local contra un servidor NTP público. Una hora
    desincronizada explica errores de certificado/HTTPS que a simple vista
    parecen "problema de internet"."""
    try:
        offset = _ntp_offset_seconds()
    except Exception:
        return _result("Sincronización de hora", "unknown", None, "No se pudo consultar un servidor de hora.")
    abs_offset = abs(offset)
    causes = ["El servicio 'Hora de Windows' está detenido", "El equipo lleva mucho tiempo sin conectarse a Internet", "La batería del reloj (CMOS) está agotada"]
    fix = [
        "Ve a Configuración > Hora e idioma > Fecha y hora y presiona 'Sincronizar ahora'.",
        "Revisa en services.msc que el servicio 'Hora de Windows' esté en ejecución.",
        "Si el reloj se atrasa incluso con Internet, la pila del CMOS puede estar agotada (equipos de escritorio antiguos).",
    ]
    if abs_offset > 300:
        return _result("Sincronización de hora", "critical", f"{round(offset)} s de diferencia",
                        "La hora del equipo está muy desincronizada.", causes=causes, fix=fix,
                        action_id="sync_time")
    if abs_offset > 30:
        return _result("Sincronización de hora", "warning", f"{round(offset)} s de diferencia",
                        "La hora del equipo tiene una diferencia notoria.", causes=causes, fix=fix,
                        action_id="sync_time")
    return _result("Sincronización de hora", "ok", f"{round(offset, 1)} s de diferencia", "La hora del equipo está sincronizada.")


def run_all():
    return _run_parallel([
        check_pending_reboot,
        check_last_update,
        check_event_log_errors,
        check_startup_items,
        check_bsod,
        check_defender_firewall,
        check_error_devices,
        check_print_spooler,
        check_bitlocker,
        check_time_sync,
    ])


def run_sfc_scan():
    """Ejecuta 'sfc /scannow'. Puede tardar varios minutos y requiere permisos de administrador.

    `sfc` escribe su salida en UTF-16LE cuando se redirige a un pipe; usar
    `checks.base.run` (que detecta y decodifica UTF-16LE) en vez de
    `text=True` evita que el resultado quede ilegible/no matcheable.
    """
    if platform.system() != "Windows":
        return {"status": "unknown", "output": "Solo disponible en Windows."}
    output = _run(["sfc", "/scannow"], timeout=1200)
    if output is None:
        return {"status": "error", "output": "No se pudo ejecutar sfc."}
    lowered = output.lower()
    needs_admin = (
        "debe ser administrador" in lowered
        or "must be an administrator" in lowered
        or "no pudo realizar la operación solicitada" in lowered
        or "was unable to perform the requested operation" in lowered
    )
    if needs_admin:
        status = "unknown"
    elif "no encontró ninguna infracción" in lowered or "did not find any integrity violations" in lowered:
        status = "ok"
    else:
        status = "warning"
    return {"status": status, "output": output}
