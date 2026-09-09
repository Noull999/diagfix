"""Gestor de discos: lectura de discos/particiones reales del equipo.

Cada partición se numera como "Parte N" en la UI (convención tomada de la
herramienta de referencia del usuario, donde el campo se llama
`PartNumber` — no tiene relación con "número de repuesto" de hardware).

`is_system_disk` se calcula acá (server-side) comparando contra el disco
que contiene la unidad de arranque (`$env:SystemDrive`). Es el dato que
`checks/disk_actions.py` usa para bloquear cualquier operación destructiva
sobre el disco del sistema — ver ese módulo para la guarda real.
"""
import json
import platform

from .base import run_powershell as _run_powershell

_LIST_SCRIPT = r"""
$sysDiskNumber = (Get-Partition -DriveLetter ($env:SystemDrive.TrimEnd(":")) -ErrorAction SilentlyContinue).DiskNumber
$disks = Get-Disk -ErrorAction SilentlyContinue
$out = @()
foreach ($d in $disks) {
    $parts = Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue
    $partList = @()
    foreach ($p in $parts) {
        $vol = $null
        if ($p.DriveLetter) { $vol = Get-Volume -DriveLetter $p.DriveLetter -ErrorAction SilentlyContinue }
        $partList += [ordered]@{
            partNumber  = $p.PartitionNumber
            driveLetter = if ($p.DriveLetter) { [string]$p.DriveLetter } else { $null }
            label       = if ($vol) { $vol.FileSystemLabel } else { $null }
            filesystem  = if ($vol) { $vol.FileSystem } else { $null }
            sizeGB      = [math]::Round($p.Size / 1GB, 1)
            freeGB      = if ($vol) { [math]::Round($vol.SizeRemaining / 1GB, 1) } else { $null }
            type        = [string]$p.Type
        }
    }
    $out += [ordered]@{
        number         = $d.Number
        friendlyName   = $d.FriendlyName
        sizeGB         = [math]::Round($d.Size / 1GB, 1)
        partitionStyle = [string]$d.PartitionStyle
        healthStatus   = [string]$d.HealthStatus
        busType        = [string]$d.BusType
        isSystemDisk   = ($d.Number -eq $sysDiskNumber)
        partitions     = $partList
    }
}
$out | ConvertTo-Json -Depth 5 -Compress
""".strip()


def list_disks():
    if platform.system() != "Windows":
        return {"available": False, "message": "Solo disponible en Windows.", "disks": []}
    output = _run_powershell(_LIST_SCRIPT, timeout=20)
    if not output:
        return {"available": False, "message": "No se pudo consultar (requiere permisos de administrador).", "disks": []}
    try:
        disks = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return {"available": False, "message": "No se pudo interpretar la respuesta.", "disks": []}
    if isinstance(disks, dict):
        disks = [disks]
    for d in disks:
        if isinstance(d.get("partitions"), dict):
            d["partitions"] = [d["partitions"]]
    return {"available": True, "message": None, "disks": disks}


def system_disk_number():
    """Recalcula el número del disco del sistema en este mismo momento (no
    cachea) — se llama de nuevo justo antes de cada acción destructiva en
    `disk_actions.py`, para que no se pueda confiar en un valor viejo."""
    output = _run_powershell(
        '(Get-Partition -DriveLetter ($env:SystemDrive.TrimEnd(":")) -ErrorAction SilentlyContinue).DiskNumber',
        timeout=10,
    )
    output = (output or "").strip()
    try:
        return int(output)
    except ValueError:
        return None
