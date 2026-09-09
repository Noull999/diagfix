"""Mantenimiento de Windows con parámetros (no encajan en la whitelist sin
argumentos de `checks/actions.py`): backup de drivers antes de formatear.
"""
from pathlib import Path

from .actions import _log as _audit_log
from .base import ps_quote as _ps_quote
from .base import run_powershell as _run_powershell


class MaintenanceError(Exception):
    pass


def backup_drivers(destination: str):
    """Exporta los drivers de terceros instalados (no los de Windows) a una
    carpeta, para poder reinstalarlos después de formatear sin tener que
    volver a buscarlos. Usa DISM (`Export-WindowsDriver`), requiere admin."""
    dest = (destination or "").strip()
    if not dest:
        raise MaintenanceError("Falta la carpeta de destino.")
    try:
        Path(dest).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MaintenanceError(f"No se pudo crear la carpeta de destino: {exc}") from exc

    safe_dest = _ps_quote(dest)
    script = (
        f"try {{ "
        f"$r = Export-WindowsDriver -Online -Destination '{safe_dest}' -ErrorAction Stop; "
        f'"OK: " + $r.Count + " drivers exportados" '
        f'}} catch {{ "ERROR: " + $_.Exception.Message }}'
    )
    output = _run_powershell(script, timeout=300)
    success = (output or "").strip().startswith("OK")
    _audit_log("backup_drivers", success, f"dest={safe_dest} output={output}")
    if not success:
        raise MaintenanceError(output or "No se pudieron exportar los drivers (requiere permisos de administrador).")
    return {"status": "ok", "message": output.strip()}
