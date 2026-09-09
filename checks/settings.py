"""Configuración local persistente de DiagFix: lista de técnicos y ruta de
red (NAS). Vive en `diagfix_settings.json` junto al proyecto — igual que
`diagfix_history.db` y `actions_log.jsonl`, es un archivo local que no se
versiona y es propio de cada instalación (no lleva nada de otra empresa).
"""
import json

from .base import app_dir as _app_dir

SETTINGS_PATH = _app_dir() / "diagfix_settings.json"

_DEFAULTS = {"technicians": [], "last_technician": None, "nas_path": None}


def _read():
    if not SETTINGS_PATH.exists():
        return dict(_DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)
    return {**_DEFAULTS, **data}


def _write(data):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_settings():
    return _read()


def add_technician(name: str):
    name = (name or "").strip()
    if not name:
        raise ValueError("El nombre no puede estar vacío.")
    data = _read()
    if name not in data["technicians"]:
        data["technicians"].append(name)
    data["last_technician"] = name
    _write(data)
    return data


def set_last_technician(name: str):
    data = _read()
    data["last_technician"] = (name or "").strip() or None
    _write(data)
    return data


def get_nas_path():
    return _read().get("nas_path")


def set_nas_path(path: str):
    data = _read()
    data["nas_path"] = (path or "").strip() or None
    _write(data)
    return data
