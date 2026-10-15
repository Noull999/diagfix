"""Gestor de inicio: lista cada programa que arranca con Windows y permite
deshabilitarlo/habilitarlo de forma reversible.

Usa el mismo mecanismo que el Administrador de tareas y Configuración >
Aplicaciones > Inicio: la clave `Explorer\\StartupApproved`. La entrada del
programa (valor en `Run` o acceso directo en la carpeta Inicio) queda
intacta; al lado se marca como deshabilitada y Explorer la salta al iniciar
sesión. Por eso además se ve igual en el Administrador de tareas.

Por qué no otra cosa (para no repetir la historia):
- v1 renombraba el valor de `Run`: Windows ejecuta todos los valores de esa
  clave sin importar el nombre, así que nunca deshabilitó nada.
- v2 eliminaba el valor (con respaldo propio): Discord y OneDrive vuelven a
  escribir su entrada cada vez que se abren, así que volvían a arrancar al
  encender. `StartupApproved` es lo único que esos programas no tocan.
Lo que quedó de esas dos versiones se migra solo (ver `_migrate_legacy`).

Formato de cada valor de `StartupApproved` (no documentado por Microsoft,
estable desde Windows 8 y el mismo que leen Autoruns y las herramientas
forenses): 12 bytes binarios; primer byte par (02, 06) = habilitado, impar
(03; en este mismo equipo se vio 01 en un programa que no arranca) =
deshabilitado; bytes 4-11 = fecha (FILETIME) en que se deshabilitó. Sin
valor = habilitado.
"""
import json
import os
import time
import winreg
from pathlib import Path

from .base import app_dir as _app_dir

_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

# (id, colmena, clave Run, subclave de StartupApproved, etiqueta)
_RUN_SOURCES = [
    ("HKCU", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Run",
     "Registro (este usuario)"),
    ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "Run",
     "Registro (todos los usuarios)"),
    ("HKLM32", winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Run32",
     "Registro (todos los usuarios, 32 bits)"),
]
_HIVES = {source[0]: source for source in _RUN_SOURCES}

_IGNORED_FILES = {"desktop.ini"}

# Restos de las versiones anteriores, que se migran a StartupApproved.
_LEGACY_SUFFIX = "__DiagFixDisabled"
_LEGACY_FILE_SUFFIX = ".disabled"
_LEGACY_STORE = _app_dir() / "diagfix_startup_disabled.json"


class StartupError(Exception):
    pass


def _startup_folders():
    """(id de colmena para StartupApproved, etiqueta, carpeta)."""
    folders = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        folders.append(("HKCU", "Carpeta de inicio (este usuario)",
                        Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"))
    programdata = os.environ.get("PROGRAMDATA")
    if programdata:
        folders.append(("HKLM", "Carpeta de inicio (todos los usuarios)",
                        Path(programdata) / "Microsoft/Windows/Start Menu/Programs/Startup"))
    return folders


def _hive(hive_id):
    return winreg.HKEY_CURRENT_USER if hive_id == "HKCU" else winreg.HKEY_LOCAL_MACHINE


def _is_enabled(hive, subkey, name):
    try:
        with winreg.OpenKey(hive, f"{_APPROVED}\\{subkey}") as key:
            data, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return True
    if isinstance(data, bytes) and data:
        return data[0] % 2 == 0
    return True


def _set_approved(hive, subkey, name, enabled):
    if enabled:
        data = b"\x02\x00\x00\x00" + b"\x00" * 8
    else:
        filetime = int((time.time() + 11644473600) * 10_000_000)
        data = b"\x03\x00\x00\x00" + filetime.to_bytes(8, "little")
    with winreg.CreateKeyEx(hive, f"{_APPROVED}\\{subkey}", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data)


def _migrate_legacy():
    """Pasa lo que dejaron las versiones anteriores al mecanismo actual,
    manteniendo la intención del técnico (esos programas los deshabilitó él):
    valores renombrados `X__DiagFixDisabled` vuelven a llamarse `X`, los
    eliminados con respaldo vuelven a `Run`, y los accesos directos
    `.disabled` recuperan su nombre — todos marcados como deshabilitados."""
    for hive_id, hive, run_path, subkey, _ in _RUN_SOURCES:
        try:
            with winreg.OpenKey(hive, run_path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                legacy = []
                i = 0
                while True:
                    try:
                        name, value, value_type = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    if name.endswith(_LEGACY_SUFFIX):
                        legacy.append((name, value, value_type))
                for name, value, value_type in legacy:
                    original = name[: -len(_LEGACY_SUFFIX)]
                    _set_approved(hive, subkey, original, False)
                    winreg.SetValueEx(key, original, 0, value_type, value)
                    winreg.DeleteValue(key, name)
        except OSError:
            continue

    if _LEGACY_STORE.exists():
        try:
            backup = json.loads(_LEGACY_STORE.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            backup = {}
        pending = {}
        for key_id, entry in backup.items():
            hive_id, _, name = key_id.partition("|")
            source = _HIVES.get(hive_id)
            try:
                _, hive, run_path, subkey, _ = source
                _set_approved(hive, subkey, name, False)
                with winreg.OpenKey(hive, run_path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, name, 0, entry["type"], entry["value"])
            except (OSError, TypeError, KeyError):
                pending[key_id] = entry
        try:
            if pending:
                _LEGACY_STORE.write_text(json.dumps(pending, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                _LEGACY_STORE.unlink()
        except OSError:
            pass

    for hive_id, _, folder in _startup_folders():
        try:
            files = [f for f in folder.iterdir() if f.is_file() and f.name.endswith(_LEGACY_FILE_SUFFIX)]
        except OSError:
            continue
        for f in files:
            original = f.with_name(f.name[: -len(_LEGACY_FILE_SUFFIX)])
            try:
                _set_approved(_hive(hive_id), "StartupFolder", original.name, False)
                if not original.exists():
                    f.rename(original)
            except OSError:
                continue


def list_items():
    _migrate_legacy()
    items = []
    for hive_id, hive, run_path, subkey, label in _RUN_SOURCES:
        try:
            with winreg.OpenKey(hive, run_path, 0, winreg.KEY_READ) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    items.append({
                        "id": f"reg|{hive_id}|{name}",
                        "name": name,
                        "command": value,
                        "location": label,
                        "enabled": _is_enabled(hive, subkey, name),
                        "kind": "registry",
                    })
        except OSError:
            continue

    for hive_id, label, folder in _startup_folders():
        try:
            files = [f for f in folder.iterdir() if f.is_file()]
        except OSError:
            continue
        for f in files:
            if f.name.lower() in _IGNORED_FILES or f.name.endswith(_LEGACY_FILE_SUFFIX):
                continue
            items.append({
                "id": f"file|{hive_id}|{f}",
                "name": f.name,
                "command": str(f),
                "location": label,
                "enabled": _is_enabled(_hive(hive_id), "StartupFolder", f.name),
                "kind": "file",
            })
    return items


def set_enabled(item_id: str, enabled: bool):
    item = next((i for i in list_items() if i["id"] == item_id), None)
    if item is None:
        raise StartupError("El elemento de inicio no existe (¿ya fue modificado?).")
    if item["enabled"] == enabled:
        return {"status": "ok", "message": "Sin cambios (ya estaba en ese estado)."}

    kind, _, rest = item_id.partition("|")
    hive_id, _, target = rest.partition("|")
    try:
        if kind == "reg":
            _, hive, _, subkey, _ = _HIVES[hive_id]
            _set_approved(hive, subkey, target, enabled)
        else:
            _set_approved(_hive(hive_id), "StartupFolder", Path(target).name, enabled)
    except OSError as exc:
        raise StartupError(f"No se pudo cambiar (los programas de todos los usuarios requieren administrador): {exc}") from exc

    return {"status": "ok", "message": f"{item['name']}: {'habilitado' if enabled else 'deshabilitado'}."}
