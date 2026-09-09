"""Compatibilidad con Windows 11: TPM 2.0, Secure Boot, UEFI, CPU soportada,
RAM y disco. Se muestra como una tarjeta de veredicto único ("apto"/"no
apto"), no como chequeo de mantenimiento — no hay una "solución de un clic"
para un requisito de hardware, así que no aplica el patrón action_id.
"""
import json
import platform

from .base import run_powershell as _run_powershell

_SCRIPT = r"""
$tpmOk = $false
$tpmVersion = $null
try {
    $tpm = Get-Tpm -ErrorAction Stop
    $tpmOk = [bool]$tpm.TpmPresent -and [bool]$tpm.TpmReady
} catch {}
try {
    $v = (Get-CimInstance -Namespace "root/CIMV2/Security/MicrosoftTpm" -ClassName Win32_Tpm -ErrorAction Stop).SpecVersion
    if ($v) { $tpmVersion = $v.Split(',')[0].Trim() }
} catch {}

$secureBoot = $null
try { $secureBoot = [bool](Confirm-SecureBootUEFI -ErrorAction Stop) } catch { $secureBoot = $null }

$firmware = $env:firmware_type
if (-not $firmware) {
    try { $firmware = (Get-CimInstance -Namespace root/wmi -ClassName MS_SystemInformation -ErrorAction Stop).BiosFirmwareType } catch {}
}

$cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
$ramGb = [math]::Round(((Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue).TotalPhysicalMemory) / 1GB, 1)
$sysDiskGb = [math]::Round(((Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'" -ErrorAction SilentlyContinue).Size) / 1GB, 1)
$osArch = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).OSArchitecture
$osCaption = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption

[ordered]@{
    tpmPresent    = $tpmOk
    tpmVersion    = $tpmVersion
    secureBoot    = $secureBoot
    firmwareType  = [string]$firmware
    cpuName       = $cpu.Name
    cpuCores      = $cpu.NumberOfCores
    cpuMhz        = $cpu.MaxClockSpeed
    ramGb         = $ramGb
    sysDiskGb     = $sysDiskGb
    osArch        = $osArch
    osCaption     = $osCaption
} | ConvertTo-Json -Compress
""".strip()


def check_windows11_readiness():
    """Devuelve un veredicto único de compatibilidad con Windows 11, con el
    detalle de qué requisito falla si no es apto. No evalúa la lista completa
    de CPUs soportadas por Microsoft (cambia con cada actualización) — usa la
    aproximación práctica: 2+ núcleos, 1+ GHz, 64 bits, que cubre la enorme
    mayoría de los casos reales de equipos ya viejos para actualizar."""
    if platform.system() != "Windows":
        return {"available": False, "reason": "Solo disponible en Windows."}

    output = _run_powershell(_SCRIPT, timeout=30)
    if not output:
        return {"available": False, "reason": "No se pudo consultar los requisitos (requiere permisos de administrador)."}
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return {"available": False, "reason": "No se pudo interpretar la respuesta."}

    ya_es_win11 = "windows 11" in (data.get("osCaption") or "").lower()

    requisitos = []

    tpm_version = data.get("tpmVersion")
    tpm_ok = bool(data.get("tpmPresent")) and (tpm_version is None or tpm_version.startswith("2."))
    requisitos.append({
        "label": "TPM 2.0",
        "ok": tpm_ok,
        "detail": f"versión {tpm_version}" if tpm_version else ("presente" if data.get("tpmPresent") else "no detectado"),
    })

    secure_boot = data.get("secureBoot")
    requisitos.append({
        "label": "Secure Boot",
        "ok": secure_boot is True,
        "detail": "activado" if secure_boot is True else ("desactivado" if secure_boot is False else "no se pudo determinar"),
    })

    firmware = (data.get("firmwareType") or "").lower()
    uefi_ok = "uefi" in firmware or firmware == "2"
    requisitos.append({
        "label": "Firmware UEFI",
        "ok": uefi_ok,
        "detail": "UEFI" if uefi_ok else "Legacy BIOS (o no se pudo determinar)",
    })

    cores = data.get("cpuCores") or 0
    mhz = data.get("cpuMhz") or 0
    arch_64 = "64" in (data.get("osArch") or "")
    cpu_ok = cores >= 2 and mhz >= 1000 and arch_64
    requisitos.append({
        "label": "Procesador",
        "ok": cpu_ok,
        "detail": f"{data.get('cpuName') or '?'} ({cores} núcleos, {mhz} MHz, {data.get('osArch') or '?'})",
    })

    ram_gb = data.get("ramGb") or 0
    requisitos.append({"label": "Memoria RAM (mínimo 4 GB)", "ok": ram_gb >= 4, "detail": f"{ram_gb} GB"})

    disk_gb = data.get("sysDiskGb") or 0
    requisitos.append({"label": "Disco del sistema (mínimo 64 GB)", "ok": disk_gb >= 64, "detail": f"{disk_gb} GB"})

    apto = all(r["ok"] for r in requisitos)
    return {
        "available": True,
        "ya_es_windows11": ya_es_win11,
        "apto": apto,
        "requisitos": requisitos,
    }
