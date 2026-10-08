"""Controladores faltantes después de formatear.

Dos fuentes, porque ninguna alcanza sola:

- Windows Update: tiene controladores para casi todo el hardware común y es
  lo mismo que Windows esconde en "Actualizaciones opcionales". Se consulta
  con la API de Windows Update (COM), igual que lo hace el propio Windows.
- La herramienta del fabricante (Dell Command Update, Lenovo System Update,
  HP Support Assistant, MyASUS, Acer Care Center): en notebooks es la que
  trae chipset, teclas de función, audio y administración de energía, que
  Windows Update muchas veces no tiene.

Deliberadamente no se juzga "desactualizado" por la fecha del driver: los
de Microsoft que vienen con Windows dicen 2006 y están bien — sería una
alerta falsa en cada equipo.
"""
import json
import platform

from .actions import _log
from .base import run_powershell as _run_powershell
from .warranty import _detect_brand

# Primero contra Windows Update directo (ServerSelection 2): en equipos de
# empresa administrados por WSUS, el servidor interno casi nunca tiene
# controladores aprobados y la búsqueda normal daría "0 pendientes" falso.
# Si una directiva bloquea ir directo a Windows Update, se usa el servidor
# configurado.
_SEARCH = r"""
$session = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$source = 'Windows Update'
try {
    $searcher.ServerSelection = 2
    $r = $searcher.Search("IsInstalled=0 and Type='Driver'")
} catch {
    $searcher = $session.CreateUpdateSearcher()
    $source = 'servidor de actualizaciones de la empresa'
    $r = $searcher.Search("IsInstalled=0 and Type='Driver'")
}
""".strip()

_SEARCH_SCRIPT = _SEARCH + r"""
$updates = @(foreach ($u in $r.Updates) {
    [ordered]@{
        title        = $u.Title
        driverClass  = [string]$u.DriverClass
        manufacturer = [string]$u.DriverManufacturer
        date         = if ($u.DriverVerDate) { ([datetime]$u.DriverVerDate).ToString('yyyy-MM-dd') } else { $null }
    }
})
[ordered]@{ source = $source; updates = $updates } | ConvertTo-Json -Compress -Depth 4
"""

_INSTALL_SCRIPT = _SEARCH + r"""
$col = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $r.Updates) {
    if (-not $u.EulaAccepted) { $u.AcceptEula() }
    [void]$col.Add($u)
}
if ($col.Count -eq 0) {
    [ordered]@{ source = $source; results = @(); reboot = $false } | ConvertTo-Json -Compress
} else {
    $downloader = $session.CreateUpdateDownloader()
    $downloader.Updates = $col
    [void]$downloader.Download()
    $installer = $session.CreateUpdateInstaller()
    $installer.Updates = $col
    $res = $installer.Install()
    $results = @(for ($k = 0; $k -lt $col.Count; $k++) {
        [ordered]@{ title = $col.Item($k).Title; code = [int]$res.GetUpdateResult($k).ResultCode }
    })
    [ordered]@{ source = $source; results = $results; reboot = [bool]$res.RebootRequired } | ConvertTo-Json -Compress -Depth 4
}
"""

# OperationResultCode de la API de Windows Update.
_RESULT_LABELS = {2: "instalado", 3: "instalado con advertencias", 4: "falló", 5: "cancelado"}


def _wrap(script):
    # Cualquier excepción COM se devuelve como JSON en vez de salida vacía,
    # para poder mostrar el motivo real (sin internet, servicio detenido...).
    return "try {\n" + script + "\n} catch { [ordered]@{ error = $_.Exception.Message } | ConvertTo-Json -Compress }"


def _parse(output):
    if not output:
        return None
    try:
        return json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return None


def search_windows_update():
    if platform.system() != "Windows":
        return {"available": False, "message": "Solo disponible en Windows.", "updates": []}
    data = _parse(_run_powershell(_wrap(_SEARCH_SCRIPT), timeout=300))
    if data is None:
        return {"available": False, "message": "No se pudo consultar Windows Update.", "updates": []}
    if data.get("error"):
        return {"available": False, "message": f"Windows Update respondió con error: {data['error']}", "updates": []}
    updates = data.get("updates") or []
    if isinstance(updates, dict):
        updates = [updates]
    return {"available": True, "message": None, "source": data.get("source"), "updates": updates}


def install_windows_update():
    if platform.system() != "Windows":
        return {"status": "error", "message": "Solo disponible en Windows."}
    data = _parse(_run_powershell(_wrap(_INSTALL_SCRIPT), timeout=3600))
    if data is None or data.get("error"):
        motivo = (data or {}).get("error") or "sin respuesta de Windows Update"
        _log("install_drivers_windows_update", False, motivo)
        return {"status": "error", "message": f"No se pudieron instalar los controladores: {motivo}"}
    results = data.get("results") or []
    if isinstance(results, dict):
        results = [results]
    for r in results:
        r["result"] = _RESULT_LABELS.get(r.get("code"), f"código {r.get('code')}")
    ok = all(r.get("code") in (2, 3) for r in results)
    resumen = "; ".join(f"{r.get('title')}: {r['result']}" for r in results) or "no había controladores pendientes"
    _log("install_drivers_windows_update", ok, resumen)
    return {
        "status": "ok" if ok else "error",
        "message": None if results else "No había controladores pendientes.",
        "results": results,
        "reboot_required": bool(data.get("reboot")),
    }


# Enlaces solo a dominios oficiales de cada fabricante (o la Microsoft Store
# en el caso de MyASUS, que se distribuye por ahí). El patrón busca la
# herramienta instalada tanto en programas clásicos como en apps de la
# Store (MyASUS y Lenovo Vantage son apps de la Store).
_VENDOR_TOOLS = {
    "dell": ("Dell Command | Update", r"Dell Command \| Update|DellCommandUpdate|SupportAssist",
             "https://www.dell.com/support/product-details/en-ca/product/command-update/drivers"),
    "lenovo": ("Lenovo System Update", r"Lenovo System Update|Vantage|LenovoCompanion",
               "https://www.lenovo.com/us/en/software/lenovo-system-update/"),
    "hp": ("HP Support Assistant", r"HP Support Assistant|HPSupportAssistant|HP Image Assistant",
           "https://support.hp.com/us-en/help/hp-support-assistant"),
    "asus": ("MyASUS", r"MyASUS|ASUSPCAssistant",
             "https://www.microsoft.com/en-us/p/myasus/9n7r5s6b0zzh"),
    "acer": ("Acer Care Center", r"Acer Care Center|AcerCareCenter",
             "https://www.acer.com/us-en/support/drivers-and-manuals"),
}

_INSTALLED_CHECK = r"""
$pattern = '{pattern}'
$classic = Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
    Where-Object {{ $_.DisplayName -match $pattern }} | Select-Object -First 1 -ExpandProperty DisplayName
# -AllUsers necesita administrador (con la cuenta del técnico elevada, sin
# él solo se verían las apps del técnico, no las del usuario del equipo);
# si no hay permiso se cae al usuario actual en vez de responder "no está".
try {{ $apps = Get-AppxPackage -AllUsers -ErrorAction Stop }} catch {{ $apps = Get-AppxPackage -ErrorAction SilentlyContinue }}
$store = $apps | Where-Object {{ $_.Name -match $pattern }} | Select-Object -First 1 -ExpandProperty Name
if ($classic) {{ $classic }} elseif ($store) {{ $store }}
"""


def vendor_tool(manufacturer: str):
    brand = _detect_brand(manufacturer)
    tool = _VENDOR_TOOLS.get(brand)
    if tool is None:
        return {"available": False, "brand": brand,
                "message": "Para esta marca no hay una herramienta de controladores conocida: usa la página de soporte del fabricante con el número de serie."}
    name, pattern, url = tool
    installed_as = None
    if platform.system() == "Windows":
        installed_as = (_run_powershell(_INSTALLED_CHECK.format(pattern=pattern), timeout=30) or "").strip() or None
    return {"available": True, "brand": brand, "tool": name, "installed": bool(installed_as),
            "installed_as": installed_as, "url": url}
