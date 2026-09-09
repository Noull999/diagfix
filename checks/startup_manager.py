"""Gestor de inicio real: a diferencia de `software.check_startup_items`
(que solo cuenta), esto lista cada programa con su comando real y permite
deshabilitarlo/habilitarlo sin borrar nada.

Mecanismo de deshabilitado — deliberadamente NO se usa el formato binario
interno de `StartupApproved\\Run` que usa el Administrador de tareas (no está
documentado oficialmente y cambia entre versiones de Windows; escribirlo mal
puede dejar el estado ambiguo). En cambio:

- Entradas de registro (Run): se renombra el *nombre* del valor agregándole
  un sufijo — Windows solo ejecuta los valores con el nombre esperado, así
  que renombrado deja de ejecutarse, y el comando original se conserva
  intacto para poder revertirlo con solo volver a renombrar.
- Accesos directos en la carpeta de Inicio: se les agrega `.disabled` al
  nombre de archivo completo — Windows no reconoce esa extensión, así que
  dejan de ejecutarse, y se restauran quitando el sufijo.

Todo reversible, transparente (se puede confirmar a simple vista en
regedit/el explorador) y sin depender de un formato no documentado.
"""
import os
import winreg
from pathlib import Path

_DISABLED_SUFFIX = "__DiagFixDisabled"
_FILE_DISABLED_SUFFIX = ".disabled"
_IGNORED_FILES = {"desktop.ini"}

_RUN_PATHS = [
    ("HKCU", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
]


class StartupError(Exception):
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
                    enabled = not name.endswith(_DISABLED_SUFFIX)
                    display_name = name[: -len(_DISABLED_SUFFIX)] if not enabled else name
                    items.append({
                        "id": f"reg|{hive_label}|{name}",
                        "name": display_name,
                        "command": value,
                        "location": f"Registro ({hive_label})",
                        "enabled": enabled,
                        "kind": "registry",
                    })
        except OSError:
            continue

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
        hive_label, _, current_name = rest.partition("|")
        hive = winreg.HKEY_CURRENT_USER if hive_label == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        new_name = item["name"] if enabled else f"{item['name']}{_DISABLED_SUFFIX}"
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
                value, value_type = winreg.QueryValueEx(key, current_name)
                winreg.SetValueEx(key, new_name, 0, value_type, value)
                winreg.DeleteValue(key, current_name)
        except OSError as exc:
            raise StartupError(f"No se pudo modificar el registro: {exc}") from exc
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
