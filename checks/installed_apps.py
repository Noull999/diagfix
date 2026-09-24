"""Lista de apps instaladas (registro Uninstall) con banderas de interés
para un técnico en terreno: instalado hace poco, sin editor declarado,
coincide con adware/PUP conocido, o es una herramienta de acceso remoto.

Esta última bandera es la más relevante en la práctica: el fraude de
"soporte técnico falso" que convence a la víctima de instalar
AnyDesk/TeamViewer y darle el código de acceso es uno de los engaños más
comunes que un técnico se encuentra al revisar el equipo de un cliente —
no es "malware" que un antivirus detecte, es una herramienta legítima
usada sin que el dueño del equipo lo sepa.
"""
import json
import platform
from datetime import datetime, timedelta

from .base import run_powershell as _run_powershell

_RECENT_DAYS = 30

_SCRIPT = r"""
$paths = @(
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$out = @()
foreach ($p in $paths) {
    Get-ItemProperty -Path $p -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.DisplayName -and -not $_.SystemComponent -and -not $_.ParentKeyName) {
            $out += [ordered]@{
                name        = $_.DisplayName
                version     = $_.DisplayVersion
                publisher   = $_.Publisher
                installDate = $_.InstallDate
            }
        }
    }
}
$out | ConvertTo-Json -Compress -Depth 3
""".strip()

# Heurística, no una lista exhaustiva: familias de adware/PUP conocidas por
# nombre de producto. Deliberadamente conservadora (solo secuestradores de
# navegador y barras de herramientas históricamente documentados) para no
# marcar como "adware" una herramienta legítima que el técnico o el cliente
# instalaron a propósito.
_ADWARE_MARKERS = [
    "conduit", "search protect", "ask toolbar", "ask.com", "mywebsearch",
    "babylon", "delta search", "ilivid", "wajam", "superfish",
    "relevantknowledge", "pricegong", "webdiscover", "sweetim",
    "searchqu", "iminent", "hao123", "zwangi", "vopackage", "bandoo",
    "mindspark",
]

# Herramientas de acceso remoto conocidas — no son maliciosas por sí
# mismas (muchas son legítimas), pero un técnico necesita saber que están
# instaladas para confirmar con el cliente si él las instaló o no.
_REMOTE_ACCESS_MARKERS = [
    "anydesk", "teamviewer", "ultravnc", "tightvnc", "realvnc",
    "logmein", "gotoassist", "gotomypc", "rustdesk", "screenconnect",
    "connectwise control", "splashtop", "dwservice", "ammyy admin",
    "remote utilities", "showmypc", "supremo", "aeroadmin",
    "chrome remote desktop",
]


def _parse_install_date(raw):
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        return datetime.strptime(raw, "%Y%m%d")
    except ValueError:
        return None


def list_installed_apps():
    if platform.system() != "Windows":
        return {"available": False, "message": "Solo disponible en Windows.", "apps": []}
    output = _run_powershell(_SCRIPT, timeout=20)
    if not output:
        return {"available": False, "message": "No se pudo consultar el registro.", "apps": []}
    try:
        raw = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return {"available": False, "message": "No se pudo interpretar la respuesta.", "apps": []}
    if isinstance(raw, dict):
        raw = [raw]

    cutoff = datetime.now() - timedelta(days=_RECENT_DAYS)
    seen = set()
    apps = []
    for a in raw:
        name = (a.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        publisher = (a.get("publisher") or "").strip()
        install_date = _parse_install_date(a.get("installDate"))
        name_lower = name.lower()
        apps.append({
            "name": name,
            "version": a.get("version") or None,
            "publisher": publisher or None,
            "install_date": install_date.strftime("%Y-%m-%d") if install_date else None,
            "recent": bool(install_date and install_date >= cutoff),
            "no_publisher": not publisher,
            "adware_match": any(m in name_lower for m in _ADWARE_MARKERS),
            "remote_access": any(m in name_lower for m in _REMOTE_ACCESS_MARKERS),
        })

    apps.sort(key=lambda a: a["install_date"] or "", reverse=True)
    return {"available": True, "message": None, "apps": apps}
