"""Zona de scripts: carpeta `scripts/` junto al proyecto donde el técnico
deja sus propios `.ps1`/`.bat`/`.cmd` para correrlos desde el panel, con
confirmación y quedando en el mismo log de auditoría que las demás acciones.

Solo se permite ejecutar archivos que:
1. Existen realmente dentro de `SCRIPTS_DIR` (se resuelve la ruta real y se
   verifica que no se haya escapado con `..` — nunca se ejecuta un archivo
   fuera de esta carpeta).
2. Tienen una extensión de la whitelist (`.ps1`, `.bat`, `.cmd`).

DiagFix no revisa el contenido de estos scripts — son responsabilidad del
técnico que los coloca ahí. No se ejecuta nada automáticamente: siempre
requiere que alguien lo dispare desde el panel.
"""
import platform
import subprocess
from datetime import datetime
from pathlib import Path

from .actions import _log as _audit_log
from .base import app_dir as _app_dir
from .base import flags as _flags

SCRIPTS_DIR = _app_dir() / "scripts"
_ALLOWED_EXT = {".ps1", ".bat", ".cmd"}


class ScriptError(Exception):
    pass


def list_scripts():
    if not SCRIPTS_DIR.exists():
        return []
    out = []
    for p in sorted(SCRIPTS_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in _ALLOWED_EXT:
            stat = p.stat()
            out.append({
                "name": p.name,
                "extension": p.suffix.lower(),
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return out


def _resolve_safe(filename: str) -> Path:
    """Resuelve `filename` dentro de SCRIPTS_DIR y rechaza cualquier intento
    de escapar la carpeta (`..`, rutas absolutas, symlinks apuntando afuera)."""
    candidate = (SCRIPTS_DIR / filename).resolve()
    scripts_root = SCRIPTS_DIR.resolve()
    if scripts_root not in candidate.parents and candidate != scripts_root:
        raise ScriptError("Ruta de script inválida.")
    if not candidate.is_file():
        raise ScriptError("El script no existe.")
    if candidate.suffix.lower() not in _ALLOWED_EXT:
        raise ScriptError("Tipo de archivo no permitido.")
    return candidate


def run_script(filename: str, timeout: int = 600):
    if platform.system() != "Windows":
        raise ScriptError("Solo disponible en Windows.")
    path = _resolve_safe(filename)

    if path.suffix.lower() == ".ps1":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    else:
        cmd = ["cmd", "/c", str(path)]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_flags())
        output = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        output, success = f"El script no terminó en {timeout}s (se interrumpió).", False
    except Exception as exc:
        output, success = str(exc), False

    _audit_log("run_script", success, f"script={path.name} output={output[:1500]}")
    return {"status": "ok" if success else "error", "output": output[:5000]}
