"""Guardar reportes en la unidad de red que ya usa el equipo de soporte.

DiagFix nunca guarda la contraseña de red. `save_credentials` la pasa una
única vez a `cmdkey` (Administrador de credenciales de Windows) y la
descarta de inmediato; de ahí en más Windows resuelve el acceso a la ruta
UNC solo, igual que con cualquier recurso de red ya autenticado. La
contraseña no se escribe nunca en `actions_log.jsonl` ni en ningún otro
archivo de este proyecto.

Nota de seguridad: mientras `cmdkey` corre, la contraseña es visible por un
instante en la línea de comandos del proceso para otras herramientas con
privilegios suficientes (Administrador de tareas en modo detallado,
`Get-CimInstance Win32_Process`) — es una limitación del propio `cmdkey`,
no de este código; no existe una forma de pasarle la contraseña por stdin.
"""
import platform
import subprocess
from pathlib import Path

from . import settings
from .base import flags as _flags


class NasError(Exception):
    pass


def get_nas_path():
    return settings.get_nas_path()


def set_nas_path(path: str):
    return settings.set_nas_path(path)


def save_credentials(server: str, user: str, password: str):
    if platform.system() != "Windows":
        raise NasError("Solo disponible en Windows.")
    server = (server or "").strip()
    user = (user or "").strip()
    if not server or not user or not password:
        raise NasError("Servidor, usuario y contraseña son obligatorios.")
    try:
        result = subprocess.run(
            ["cmdkey", f"/add:{server}", f"/user:{user}", f"/pass:{password}"],
            capture_output=True, timeout=15, creationflags=_flags(),
        )
    except Exception as exc:
        raise NasError(str(exc)) from exc
    if result.returncode != 0:
        raise NasError(f"No se pudieron guardar las credenciales (código {result.returncode}).")
    return {"status": "ok", "message": f"Credenciales guardadas para {server}."}


def upload_file(filename: str, content: str):
    nas_path = get_nas_path()
    if not nas_path:
        raise NasError("No hay una ruta de red configurada todavía.")
    safe_name = Path(filename).name or "reporte.html"  # nunca usar la ruta tal cual la manda el cliente
    dest = Path(nas_path) / safe_name
    try:
        dest.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise NasError(f"No se pudo escribir en la red (¿guardaste las credenciales?): {exc}") from exc
    return {"status": "ok", "message": f"Guardado como {dest}"}
