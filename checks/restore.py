"""Punto de restauración del sistema — red de seguridad antes de tocar
cualquier cosa (acciones rápidas, gestor de discos, scripts). Requiere
permisos de administrador; si falla por eso se informa tal cual en vez de
fallar en silencio.
"""
from .actions import _log as _audit_log
from .base import ps_quote as _ps_quote
from .base import run_powershell as _run_powershell


class RestoreError(Exception):
    pass


def create_restore_point(description: str = "DiagFix"):
    safe_desc = _ps_quote((description or "DiagFix")[:120])
    script = (
        f"try {{ Checkpoint-Computer -Description '{safe_desc}' "
        f'-RestorePointType "MODIFY_SETTINGS" -ErrorAction Stop; "OK" }} '
        f'catch {{ "ERROR: " + $_.Exception.Message }}'
    )
    output = _run_powershell(script, timeout=180)
    success = (output or "").strip() == "OK"
    _audit_log("create_restore_point", success, output or "Sin salida.")
    if not success:
        raise RestoreError(
            (output or "No se pudo crear el punto de restauración.")
            + " (Requiere permisos de administrador y que la Protección del sistema esté activada en la unidad C:.)"
        )
    return {"status": "ok", "message": "Punto de restauración creado."}


def list_restore_points():
    output = _run_powershell(
        "Get-ComputerRestorePoint -ErrorAction SilentlyContinue | "
        "Select-Object SequenceNumber, Description, CreationTime | ConvertTo-Json -Compress",
        timeout=20,
    )
    if not output:
        return []
    import json
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return []
    if isinstance(data, dict):
        data = [data]
    return data
