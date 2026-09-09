"""Guardado local automático de reportes.

Cada vez que se guarda un escaneo en el historial, se guarda también una
copia del reporte en PNG en una carpeta `reportes/` local, al lado del
programa (mismo patrón que `scripts/`: no se versiona, vive junto al
`.exe`). Es el mismo comportamiento que tenía la herramienta de referencia
del usuario (`SaveLocalReport` / `LocalInformesRoot`): un respaldo
garantizado que no depende de si hay NAS configurado, si el equipo está en
el dominio, o si la red está disponible en ese momento.
"""
from datetime import datetime

from .base import app_dir as _app_dir

REPORTS_DIR = _app_dir() / "reportes"


def save_local_report(serial: str, png_bytes: bytes) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    safe_serial = "".join(c for c in (serial or "") if c.isalnum() or c in "-_") or "equipo"
    filename = f"{safe_serial}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    path = REPORTS_DIR / filename
    path.write_bytes(png_bytes)
    return str(path)
