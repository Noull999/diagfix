"""Gestor de inicio real: a diferencia de `software.check_startup_items`
(que solo cuenta), esto lista cada programa con su comando real y permite
deshabilitarlo/habilitarlo sin borrar nada para siempre.

Mecanismo de deshabilitado:

- Entradas de registro (Run): ¡ojo! Windows ejecuta TODOS los valores
  presentes en la clave `Run` al iniciar sesión, sin importar cómo se
  llamen — la primera versión de esto renombraba el valor (agregándole un
  sufijo) asumiendo que Windows solo corría el nombre "esperado", pero eso
  es falso: el programa seguía abriéndose igual (confirmado por un
  usuario con Discord). Por eso ahora se **elimina** el valor de verdad
  al deshabilitar, guardando una copia (nombre, dato, tipo) en
  `diagfix_startup_disabled.json` junto al programa — y al rehabilitar se
  vuelve a crear el valor a partir de esa copia. Deliberadamente NO se usa
  el formato binario interno de `StartupApproved\\Run` que usa el
  Administrador de tareas (no está documentado oficialmente y cambia entre
  versiones de Windows; además de que WOW6432Node/vistas de 32 y 64 bits lo
  complican) — eliminar + respaldo propio es más simple y 100% efectivo.
- Accesos directos en la carpeta de Inicio: se les agrega `.disabled` al
  nombre de archivo completo — Windows solo lanza extensiones reconocidas
  (.lnk, .exe, .bat...) al escanear esa carpeta, así que un archivo con esa
  extensión ya no es reconocido y no se ejecuta. Esto sí funciona (a
  diferencia del caso de registro) porque Windows escanea la carpeta por
  extensión de archivo, no por un nombre esperado.
"""
import json
import os
import winreg
from pathlib import Path

from .base import app_dir as _app_dir

_FILE_DISABLED_SUFFIX = ".disabled"
_IGNORED_FILES = {"desktop.ini"}
_DISABLED_STORE_PATH = _app_dir() / "diagfix_startup_disabled.json"

_RUN_PATHS = [
    ("HKCU", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
]


class StartupError(Exception):
    pass


def _load_disabled_registry_backup():
    if not _DISABLED_STORE_PATH.exists():
        return {}
    try:
        return json.loads(_DISABLED_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _save_disabled_registry_backup(data):
    try:
        _DISABLED_STORE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _startup_folders():
    paths = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(("Carpeta de inicio (usuario)", Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"))
    programdata = os.environ.get("PROGRAMDATA")
    if programdata:
        paths.append(("Carpeta de inicio (todos los usuarios)", Path(programdata) / "Microsoft/Windows/Start Menu/Programs/Startup"))
    return paths


def list_items():
    items = []
    seen_reg_ids = set()

    for hive_label, hive, path in _RUN_PATHS:
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    item_id = f"reg|{hive_label}|{name}"
                    seen_reg_ids.add(item_id)
                    items.append({
                        "id": item_id,
                        "name": name,
                        "command": value,
                        "location": f"Registro ({hive_label})",
                        "enabled": True,
                        "kind": "registry",
                    })
        except OSError:
            continue

    # Entradas deshabilitadas: ya no están en el registro (se eliminaron de
    # verdad), así que se listan desde el respaldo propio para que sigan
    # apareciendo y se puedan rehabilitar.
    for key_id, backup in _load_disabled_registry_backup().items():
        hive_label, _, name = key_id.partition("|")
        item_id = f"reg|{hive_label}|{name}"
        if item_id in seen_reg_ids:
            continue
        items.append({
            "id": item_id,
            "name": name,
            "command": backup.get("value"),
            "location": f"Registro ({hive_label})",
            "enabled": False,
            "kind": "registry",
        })

    for label, folder in _startup_folders():
        if not folder.exists():
            continue
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for f in entries:
            if not f.is_file() or f.name.lower() in _IGNORED_FILES:
                continue
            enabled = not f.name.endswith(_FILE_DISABLED_SUFFIX)
            display_name = f.name[: -len(_FILE_DISABLED_SUFFIX)] if not enabled else f.name
            items.append({
                "id": f"file|{f}",
                "name": display_name,
                "command": str(f),
                "location": label,
                "enabled": enabled,
                "kind": "file",
            })

    return items


def _find_item(item_id):
    for item in list_items():
        if item["id"] == item_id:
            return item
    return None


def set_enabled(item_id: str, enabled: bool):
    item = _find_item(item_id)
    if item is None:
        raise StartupError("El elemento de inicio no existe (¿ya fue modificado?).")
    if item["enabled"] == enabled:
        return {"status": "ok", "message": "Sin cambios (ya estaba en ese estado)."}

    kind, _, rest = item["id"].partition("|")
    if kind == "reg":
        hive_label, _, name = rest.partition("|")
        hive = winreg.HKEY_CURRENT_USER if hive_label == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key_id = f"{hive_label}|{name}"
        backup = _load_disabled_registry_backup()
        if enabled:
            entry = backup.get(key_id)
            if entry is None:
                raise StartupError("No se encontró el respaldo para restaurar este elemento.")
            try:
                with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, name, 0, entry["type"], entry["value"])
            except OSError as exc:
                raise StartupError(f"No se pudo restaurar el registro: {exc}") from exc
            backup.pop(key_id, None)
            _save_disabled_registry_backup(backup)
        else:
            try:
                with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
                    value, value_type = winreg.QueryValueEx(key, name)
                    winreg.DeleteValue(key, name)
            except OSError as exc:
                raise StartupError(f"No se pudo modificar el registro: {exc}") from exc
            backup[key_id] = {"value": value, "type": value_type}
            _save_disabled_registry_backup(backup)
    else:  # file
        current_path = Path(rest)
        new_path = current_path.with_name(
            item["name"] if enabled else f"{item['name']}{_FILE_DISABLED_SUFFIX}"
        )
        try:
            current_path.rename(new_path)
        except OSError as exc:
            raise StartupError(f"No se pudo renombrar el archivo: {exc}") from exc

    return {"status": "ok", "message": f"{item['name']}: {'habilitado' if enabled else 'deshabilitado'}."}
