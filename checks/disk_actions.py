"""Acciones sobre discos/particiones. Separado de `checks/actions.py`
porque estas acciones llevan parámetros (disco/partición) en vez de ser una
whitelist fija sin argumentos.

GUARDA DE SEGURIDAD NO NEGOCIABLE: toda función destructiva recalcula cuál
es el disco del sistema en el momento de ejecutar (`disks.system_disk_number`,
sin cachear) y rechaza la operación si coincide. Esto protege incluso si
alguien llama al endpoint HTTP directo, saltándose la confirmación de la UI.
"""
from . import disks
from .actions import _log as _audit_log
from .base import ps_quote as _ps_quote
from .base import run_powershell as _run_powershell


class DiskActionError(Exception):
    pass


def _guard_not_system_disk(disk_number: int):
    sys_disk = disks.system_disk_number()
    if sys_disk is not None and disk_number == sys_disk:
        message = (
            f"Operación rechazada: el disco {disk_number} es el disco del sistema (contiene la unidad de arranque)."
        )
        # Un intento bloqueado sobre el disco del sistema es justo lo que
        # interesa auditar — se registra aunque nunca llegue a ejecutarse.
        _audit_log("disk_action_blocked_system_disk", False, f"disk={disk_number} — {message}")
        raise DiskActionError(message)


def format_partition(disk_number: int, partition_number: int, filesystem: str = "NTFS", label: str = ""):
    _guard_not_system_disk(disk_number)
    fs = filesystem if filesystem in ("NTFS", "FAT32", "exFAT", "ReFS") else "NTFS"
    safe_label = _ps_quote((label or "")[:32])
    script = (
        f"Get-Partition -DiskNumber {int(disk_number)} -PartitionNumber {int(partition_number)} "
        f"-ErrorAction Stop | Format-Volume -FileSystem {fs} -NewFileSystemLabel '{safe_label}' "
        f"-Confirm:$false -Force | Out-Null; \"OK\""
    )
    output = _run_powershell(script, timeout=600)
    success = (output or "").strip() == "OK"
    _audit_log(
        "disk_format_partition", success,
        f"disk={disk_number} partition={partition_number} fs={fs} label={safe_label} output={output}",
    )
    if not success:
        raise DiskActionError(output or "No se pudo formatear (¿permisos de administrador?).")
    return {"status": "ok", "message": f"Parte {partition_number} del disco {disk_number} formateada como {fs}."}


def create_partition(disk_number: int, size_mb: int, filesystem: str = "NTFS", label: str = ""):
    _guard_not_system_disk(disk_number)
    fs = filesystem if filesystem in ("NTFS", "FAT32", "exFAT", "ReFS") else "NTFS"
    safe_label = _ps_quote((label or "")[:32])
    size_bytes = int(size_mb) * 1024 * 1024
    script = (
        f"$p = New-Partition -DiskNumber {int(disk_number)} -Size {size_bytes} -AssignDriveLetter -ErrorAction Stop; "
        f"$p | Format-Volume -FileSystem {fs} -NewFileSystemLabel '{safe_label}' -Confirm:$false | Out-Null; "
        f"[string]$p.DriveLetter"
    )
    output = _run_powershell(script, timeout=120)
    letter = (output or "").strip()
    success = bool(letter) and len(letter) == 1
    _audit_log(
        "disk_create_partition", success,
        f"disk={disk_number} size_mb={size_mb} fs={fs} label={safe_label} output={output}",
    )
    if not success:
        raise DiskActionError(output or "No se pudo crear la partición (¿hay espacio sin asignar?).")
    return {"status": "ok", "message": f"Partición creada en el disco {disk_number} con letra {letter}."}


def delete_partition(disk_number: int, partition_number: int):
    _guard_not_system_disk(disk_number)
    script = (
        f"Remove-Partition -DiskNumber {int(disk_number)} -PartitionNumber {int(partition_number)} "
        f"-Confirm:$false -ErrorAction Stop; \"OK\""
    )
    output = _run_powershell(script, timeout=60)
    success = (output or "").strip() == "OK"
    _audit_log(
        "disk_delete_partition", success,
        f"disk={disk_number} partition={partition_number} output={output}",
    )
    if not success:
        raise DiskActionError(output or "No se pudo eliminar la partición (¿permisos de administrador?).")
    return {"status": "ok", "message": f"Parte {partition_number} del disco {disk_number} eliminada."}


def rename_volume(drive_letter: str, label: str):
    letter = (drive_letter or "").strip().rstrip(":")[:1]
    if not letter.isalpha():
        raise DiskActionError("Letra de unidad inválida.")
    safe_label = _ps_quote((label or "")[:32])
    script = f"Set-Volume -DriveLetter {letter} -NewFileSystemLabel '{safe_label}' -ErrorAction Stop; \"OK\""
    output = _run_powershell(script, timeout=30)
    success = (output or "").strip() == "OK"
    _audit_log("disk_rename_volume", success, f"drive={letter} label={safe_label} output={output}")
    if not success:
        raise DiskActionError(output or "No se pudo renombrar el volumen.")
    return {"status": "ok", "message": f"Unidad {letter}: renombrada a \"{safe_label}\"."}


def set_drive_letter(disk_number: int, partition_number: int, new_letter: str):
    # A diferencia de format/create/delete, esto no borra datos — pero
    # cambiarle la letra a la unidad de arranque puede dejar Windows sin
    # poder iniciar. Misma guarda por las dudas.
    _guard_not_system_disk(disk_number)
    letter = (new_letter or "").strip().rstrip(":")[:1]
    if not letter.isalpha():
        raise DiskActionError("Letra de unidad inválida.")
    script = (
        f"Set-Partition -DiskNumber {int(disk_number)} -PartitionNumber {int(partition_number)} "
        f"-NewDriveLetter {letter} -ErrorAction Stop; \"OK\""
    )
    output = _run_powershell(script, timeout=30)
    success = (output or "").strip() == "OK"
    _audit_log(
        "disk_set_letter", success,
        f"disk={disk_number} partition={partition_number} new_letter={letter} output={output}",
    )
    if not success:
        raise DiskActionError(output or "No se pudo cambiar la letra de unidad (¿ya está en uso?).")
    return {"status": "ok", "message": f"Letra de unidad cambiada a {letter}:"}
