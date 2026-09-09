"""Actualización de DiagFix desde GitHub Releases, para no depender de que
cada versión nueva se entregue a mano por SendUserFile.

Solo tiene sentido en el .exe empaquetado (PyInstaller) — en modo desarrollo
(`python app.py`) no hay un ejecutable propio que reemplazar.
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# Se bumpea a mano en cada release, junto con el tag de git (ver README).
CURRENT_VERSION = "1.4.0"

_REPO = "Noull999/diagfix"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases/latest"
_USER_AGENT = "DiagFix-Updater"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _version_tuple(v: str):
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def check_for_update() -> dict:
    """Consulta el último release público. El asset .exe se toma tal cual lo
    devuelve la API de GitHub (fuente confiable) — nunca de un dato que venga
    del cliente/navegador, para no abrir una descarga arbitraria."""
    if not is_frozen():
        return {
            "available": False,
            "reason": "La búsqueda de actualizaciones solo funciona en el .exe empaquetado, no en modo desarrollo (`python app.py`).",
        }
    req = urllib.request.Request(
        _API_URL, headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"available": False, "reason": f"No se pudo consultar GitHub: {exc}"}

    latest_tag = data.get("tag_name") or ""
    assets = data.get("assets") or []
    asset = next((a for a in assets if (a.get("name") or "").lower().endswith(".exe")), None)
    if not asset:
        return {"available": False, "reason": "El último release publicado no tiene un .exe adjunto."}

    return {
        "available": True,
        "current_version": CURRENT_VERSION,
        "latest_version": latest_tag,
        "update_available": _version_tuple(latest_tag) > _version_tuple(CURRENT_VERSION),
        "notes": data.get("body") or "",
        "download_url": asset.get("browser_download_url"),
        "asset_size": asset.get("size"),
    }


# Windows tiene el .exe en ejecución bloqueado para escritura — por eso el
# reemplazo lo hace un proceso aparte (este .bat), que espera a que el PID
# actual termine, recién ahí mueve el archivo nuevo encima del viejo, reabre
# la app, y se autoborra.
_UPDATER_BAT = r"""@echo off
:esperar
tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >NUL
    goto esperar
)
move /y "{nuevo}" "{actual}" >NUL
start "" "{actual}"
del "%~f0"
"""


def apply_update(download_url: str) -> None:
    if not is_frozen():
        raise RuntimeError("Solo se puede actualizar el .exe empaquetado.")

    actual = Path(sys.executable)
    carpeta = actual.parent
    nuevo = carpeta / "DiagFix_nuevo.exe"

    req = urllib.request.Request(download_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp:
        nuevo.write_bytes(resp.read())

    bat_path = carpeta / "diagfix_actualizador.bat"
    bat_path.write_text(
        _UPDATER_BAT.format(pid=os.getpid(), nuevo=str(nuevo), actual=str(actual)),
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        cwd=str(carpeta),
    )
