"""Inventario acumulado de equipos revisados: un CSV que crece con cada
escaneo guardado, para tener una planilla con todas las visitas sin entrar
al historial por equipo uno por uno.
"""
import csv
from datetime import datetime

from .base import app_dir as _app_dir

INVENTORY_PATH = _app_dir() / "diagfix_inventory.csv"

_FIELDS = [
    "fecha", "serie", "equipo", "part_number", "sistema_operativo",
    "score", "cobertura", "cliente", "ticket", "tecnico",
]


def append_row(*, serial, computer, part_number, os_summary, score, coverage,
                client=None, ticket=None, technician=None):
    is_new = not INVENTORY_PATH.exists()
    with open(INVENTORY_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "serie": serial or "",
            "equipo": computer or "",
            "part_number": part_number or "",
            "sistema_operativo": os_summary or "",
            "score": score if score is not None else "",
            "cobertura": coverage or "",
            "cliente": client or "",
            "ticket": ticket or "",
            "tecnico": technician or "",
        })


def read_rows(limit=200):
    if not INVENTORY_PATH.exists():
        return []
    with open(INVENTORY_PATH, "r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return rows[-limit:]
