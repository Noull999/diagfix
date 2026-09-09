"""Historial de escaneos en SQLite, indexado por número de serie del equipo,
para comparar el estado de un equipo entre visitas.

Usa `sqlite3` de la biblioteca estándar — no agrega una dependencia nueva al
proyecto.
"""
import json
import sqlite3
from datetime import datetime

from .base import app_dir as _app_dir

DB_PATH = _app_dir() / "diagfix_history.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                serial TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                score INTEGER,
                coverage TEXT,
                client TEXT,
                ticket TEXT,
                technician TEXT,
                categories_json TEXT,
                insights_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scans_serial ON scans (serial)")


def save_scan(serial, score, coverage, categories, insights, client=None, ticket=None, technician=None):
    if not serial:
        raise ValueError("Falta el número de serie del equipo.")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO scans (serial, timestamp, score, coverage, client, ticket, technician, "
            "categories_json, insights_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                serial,
                datetime.now().isoformat(timespec="seconds"),
                score,
                coverage,
                client,
                ticket,
                technician,
                json.dumps(categories, ensure_ascii=False),
                json.dumps(insights, ensure_ascii=False),
            ),
        )
        return cur.lastrowid


def list_history(serial, limit=20):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, score, coverage, client, ticket, technician "
            "FROM scans WHERE serial = ? ORDER BY timestamp DESC LIMIT ?",
            (serial, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_scan(scan_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["categories"] = json.loads(data.pop("categories_json") or "[]")
        data["insights"] = json.loads(data.pop("insights_json") or "[]")
        return data
