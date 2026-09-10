"""Identificación del equipo: marca, modelo, número de serie, SO, RAM, discos.

A diferencia de `checks/network.py`, `hardware.py` y `software.py`, esto no
son "chequeos" con un veredicto ok/warning/critical — es información de
referencia que un técnico pide en cualquier ticket (marca/modelo/serie del
equipo, edición y build de Windows, etc.), así que se expone en su propio
endpoint (`/api/identity`) y se muestra como una tarjeta, no como un check
más en las columnas de categorías.

Todo se junta en una sola llamada a PowerShell (en vez de una por dato) para
no sumar 8-10 arranques de proceso solo para esta info.
"""
import json
import platform

from .base import commercial_size_label as _commercial_size_label
from .base import run_powershell as _run_powershell

_SCRIPT = r"""
$cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
$bios = Get-CimInstance Win32_BIOS -ErrorAction SilentlyContinue
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$mem = Get-CimInstance Win32_PhysicalMemory -ErrorAction SilentlyContinue
$memArray = Get-CimInstance Win32_PhysicalMemoryArray -ErrorAction SilentlyContinue
$physDisks = Get-PhysicalDisk -ErrorAction SilentlyContinue
$disks = Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue
$lic = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey is not null" -ErrorAction SilentlyContinue | Select-Object -First 1
$csProduct = Get-CimInstance Win32_ComputerSystemProduct -ErrorAction SilentlyContinue
$baseBoard = Get-CimInstance Win32_BaseBoard -ErrorAction SilentlyContinue
$displayVersion = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -Name DisplayVersion -ErrorAction SilentlyContinue).DisplayVersion
$partNumber = if ($csProduct.SKUNumber) { $csProduct.SKUNumber } elseif ($baseBoard.Product) { $baseBoard.Product } else { $null }

$ramTotalGb = if ($mem) { [math]::Round((($mem | Measure-Object Capacity -Sum).Sum) / 1GB, 1) } else { $null }
$slotsUsed = if ($mem) { ($mem | Measure-Object).Count } else { $null }
$slotsTotal = if ($memArray) { ($memArray | Measure-Object -Property MemoryDevices -Sum).Sum } else { $null }

$ramModules = @($mem | ForEach-Object {
    [ordered]@{
        slot               = $_.DeviceLocator
        manufacturer       = $_.Manufacturer
        partNumber         = if ($_.PartNumber) { $_.PartNumber.Trim() } else { $null }
        speedMhz           = $_.Speed
        capacityGB         = [math]::Round($_.Capacity / 1GB, 1)
        memoryTypeCode     = $_.SMBIOSMemoryType
    }
})

$diskInfo = @()
if ($physDisks) {
    $diskInfo = @($physDisks | ForEach-Object {
        [ordered]@{
            name      = $_.FriendlyName
            mediaType = [string]$_.MediaType
            busType   = [string]$_.BusType
            sizeBytes = $_.Size
        }
    })
} elseif ($disks) {
    $diskInfo = @($disks | ForEach-Object {
        [ordered]@{ name = $_.Model; mediaType = $null; busType = $null; sizeBytes = $_.Size }
    })
}

$cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
$cpuName = if ($cpu.Name) { ($cpu.Name -replace '\s+', ' ').Trim() } else { $null }

$gpuInfo = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | ForEach-Object {
    [ordered]@{
        name           = $_.Name
        driverVersion  = $_.DriverVersion
        driverDate     = if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { $null }
        vramMb         = if ($_.AdapterRAM -and $_.AdapterRAM -gt 0) { [math]::Round($_.AdapterRAM / 1MB) } else { $null }
    }
})

$monitorCount = 0
try { $monitorCount = (Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBasicDisplayParams -ErrorAction Stop | Measure-Object).Count } catch {}

$usbCount = 0
try {
    # Igual que en la prueba en vivo de Pruebas: sin filtrar, esto también
    # cuenta los controladores de host (bus PCI) y los concentradores raíz,
    # que están siempre presentes aunque no haya nada conectado.
    $usbCount = (Get-PnpDevice -Class USB -Status OK -ErrorAction Stop |
        Where-Object { $_.InstanceId -notlike 'PCI\*' -and $_.Service -notin @('USBHUB', 'USBHUB3', 'USBHUB30') } |
        Measure-Object).Count
} catch {}

$printerCount = 0
try { $printerCount = (Get-Printer -ErrorAction Stop | Measure-Object).Count } catch {}

$office = $null
try {
    $c2r = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration" -ErrorAction Stop
    $office = $c2r.ProductReleaseIds
} catch {}
if (-not $office) {
    $officeKey = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match "Microsoft Office|Microsoft 365" } | Select-Object -First 1
    $office = $officeKey.DisplayName
}

$result = [ordered]@{
    manufacturer     = $cs.Manufacturer
    model            = $cs.Model
    serial           = $bios.SerialNumber
    part_number      = $partNumber
    cpu_name         = $cpuName
    os_caption       = $os.Caption
    os_display_version = $displayVersion
    os_build         = $os.BuildNumber
    os_arch          = $os.OSArchitecture
    install_date     = if ($os.InstallDate) { $os.InstallDate.ToString('yyyy-MM-dd') } else { $null }
    last_boot        = if ($os.LastBootUpTime) { $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm') } else { $null }
    domain           = $cs.Domain
    part_of_domain   = [bool]$cs.PartOfDomain
    username         = $env:USERNAME
    ram_total_gb     = $ramTotalGb
    ram_slots_used   = $slotsUsed
    ram_slots_total  = $slotsTotal
    ram_modules      = $ramModules
    disks            = $diskInfo
    gpus             = $gpuInfo
    monitor_count    = $monitorCount
    usb_device_count = $usbCount
    printer_count    = $printerCount
    activation_code  = if ($lic) { [int]$lic.LicenseStatus } else { $null }
    office           = $office
}
$result | ConvertTo-Json -Compress
""".strip()

# SMBIOSMemoryType (DMTF SMBIOS Type 17 "Memory Device", campo "Type").
_RAM_TYPE_LABELS = {
    20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5",
}

# Ver Microsoft.Windows.Foundation.Diagnostics / SoftwareLicensingProduct.LicenseStatus
_ACTIVATION_LABELS = {
    0: "Sin licencia",
    1: "Activado",
    2: "Periodo de gracia inicial",
    3: "Periodo de gracia fuera de tolerancia",
    4: "Periodo de gracia (no genuino)",
    5: "Notificación",
    6: "Periodo de gracia extendido",
}


def get_identity():
    if platform.system() != "Windows":
        return {"available": False, "reason": "Solo disponible en Windows."}

    output = _run_powershell(_SCRIPT, timeout=120)
    if not output:
        return {"available": False, "reason": "No se pudo consultar la información del equipo."}

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return {"available": False, "reason": "No se pudo interpretar la información del equipo."}

    disks_raw = data.get("disks")
    if disks_raw is None:
        disks_raw = []
    elif isinstance(disks_raw, dict):
        disks_raw = [disks_raw]
    disks = []
    for d in disks_raw:
        disks.append({
            "name": d.get("name"),
            "size_label": _commercial_size_label(d.get("sizeBytes")),
            "media_type": d.get("mediaType") or None,
            "bus_type": d.get("busType") or None,
        })

    gpus_raw = data.get("gpus")
    if gpus_raw is None:
        gpus_raw = []
    elif isinstance(gpus_raw, dict):
        gpus_raw = [gpus_raw]
    gpus = [{
        "name": g.get("name"),
        "driver_version": g.get("driverVersion"),
        "driver_date": g.get("driverDate"),
        "vram_mb": g.get("vramMb"),
    } for g in gpus_raw]

    ram_modules_raw = data.get("ram_modules")
    if ram_modules_raw is None:
        ram_modules_raw = []
    elif isinstance(ram_modules_raw, dict):
        ram_modules_raw = [ram_modules_raw]
    ram_modules = []
    for m in ram_modules_raw:
        ram_modules.append({
            "slot": m.get("slot"),
            "manufacturer": m.get("manufacturer"),
            "part_number": m.get("partNumber"),
            "speed_mhz": m.get("speedMhz"),
            "capacity_gb": m.get("capacityGB"),
            "type": _RAM_TYPE_LABELS.get(m.get("memoryTypeCode"), None),
        })

    slots_used = data.get("ram_slots_used")
    # Heurística simple: 2 o más módulos habilitan doble canal en la
    # inmensa mayoría de los equipos de consumo (no hay forma genérica de
    # confirmarlo por WMI sin leer registros de memory controller por modelo).
    if slots_used is None:
        ram_channel_mode = None
    elif slots_used >= 2:
        ram_channel_mode = "Doble canal (o superior)"
    elif slots_used == 1:
        ram_channel_mode = "Canal único"
    else:
        ram_channel_mode = None

    activation_code = data.get("activation_code")
    activation_label = _ACTIVATION_LABELS.get(activation_code, "Desconocido" if activation_code is not None else None)

    os_caption = data.get("os_caption")
    display_version = data.get("os_display_version")
    os_summary_parts = [p for p in (os_caption, f"versión {display_version}" if display_version else None) if p]
    os_summary = ", ".join(os_summary_parts) if os_summary_parts else None
    if os_summary and activation_label:
        os_summary = f"{os_summary} — {activation_label}"

    return {
        "available": True,
        "manufacturer": data.get("manufacturer"),
        "model": data.get("model"),
        "serial": data.get("serial"),
        "part_number": data.get("part_number"),
        "cpu_name": data.get("cpu_name"),
        "os_summary": os_summary,
        "os_build": data.get("os_build"),
        "os_arch": data.get("os_arch"),
        "install_date": data.get("install_date"),
        "last_boot": data.get("last_boot"),
        "domain": data.get("domain"),
        "part_of_domain": bool(data.get("part_of_domain")),
        "username": data.get("username"),
        "ram_total_gb": data.get("ram_total_gb"),
        "ram_slots_used": data.get("ram_slots_used"),
        "ram_slots_total": data.get("ram_slots_total"),
        "ram_modules": ram_modules,
        "ram_channel_mode": ram_channel_mode,
        "disks": disks,
        "gpus": gpus,
        "monitor_count": data.get("monitor_count"),
        "usb_device_count": data.get("usb_device_count"),
        "printer_count": data.get("printer_count"),
        "activation_status": activation_label,
        "office": data.get("office"),
        "battery": _battery_info(),
    }


def _battery_info():
    """Salud de la batería para la ficha. Es `None` solo en equipos de
    escritorio (sin batería) — la fila no aparece. Si el equipo SÍ tiene
    batería pero no se pudo leer su capacidad (reporte de powercfg vacío o
    con formato inesperado), se propaga el error en vez de esconderlo: antes
    ambos casos se trataban igual y un error real quedaba indistinguible de
    "no tiene batería", lo que llevó a pensar que la app no la detectaba."""
    from .hardware import battery_capacity

    return battery_capacity()
