"""DiagFix — panel local de diagnóstico técnico para soporte en Windows.

Ejecutar con:  python app.py
Se abre automáticamente en http://127.0.0.1:8000
Recomendado ejecutar la terminal "Como administrador" para que todos los
chequeos (disco, registro de eventos, sfc) tengan permisos suficientes.

Modo consola (sin navegador, para correr desde un CMD en el OOBE de un
equipo recién formateado — Shift+F10 durante la instalación de Windows):
  DiagFix.exe --console
Se activa solo si no hay navegador disponible (detecta WinPE).
"""
import ctypes
import os
import platform
import subprocess
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Timer
from typing import List, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from checks import actions, correlate, disk_actions, disks, history, inventory, local_reports, maintenance, monitor, nas, network, hardware, report_image, restore, scripts_runner, settings, software, startup_manager, updater, win11_readiness
from checks import system_info as system_info_check

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="DiagFix")
history.init_db()

ALLOWED_HOSTS = {"127.0.0.1:8000", "localhost:8000"}

# Puntaje de cada status para el promedio ponderado (0-100). Los chequeos
# "unknown"/"error" no entran al promedio (no sabemos si el equipo está bien
# o mal en ese punto) y en cambio se reportan aparte como "cobertura".
STATUS_SCORE = {"ok": 100, "warning": 60, "critical": 0}


def is_admin() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def score_from_checks(checks: List[Dict]) -> Dict:
    """Promedio ponderado sobre los chequeos con status conocido (ok/warning/
    critical). Los "unknown"/"error" quedan fuera del promedio para no
    castigar mecánicamente a un equipo sano que corre sin permisos de admin,
    y se reportan como cobertura (cuántos chequeos pudieron completarse)."""
    known = [c for c in checks if c["status"] in STATUS_SCORE]
    score = round(sum(STATUS_SCORE[c["status"]] for c in known) / len(known)) if known else 0
    return {"score": score, "coverage": f"{len(known)}/{len(checks)}"}


@app.get("/api/system")
def system_info():
    return {
        "os": platform.system(),
        "os_version": platform.platform(),
        "is_admin": is_admin(),
    }


@app.get("/api/identity")
def identity():
    return system_info_check.get_identity()


@app.get("/api/win11-readiness")
def win11_readiness_endpoint():
    return win11_readiness.check_windows11_readiness()


@app.get("/api/update/check")
def update_check():
    return updater.check_for_update()


@app.post("/api/update/apply")
def update_apply(request: Request):
    # Cambia el propio .exe en disco: mismo chequeo de Host que el resto de
    # las rutas mutables, para que una página abierta en el navegador no
    # pueda dispararlo sola.
    if not _same_origin(request):
        return JSONResponse(status_code=403, content={"status": "error", "output": "Origen no permitido."})

    # El download_url se vuelve a pedir a GitHub acá adentro (fuente
    # confiable) en vez de aceptar uno que mande el cliente — así no se
    # puede usar este endpoint para hacer descargar cualquier URL.
    info = updater.check_for_update()
    if not info.get("available") or not info.get("update_available"):
        return {"status": "error", "output": "No hay una actualización disponible para aplicar."}

    try:
        updater.apply_update(info["download_url"])
    except Exception as exc:
        return {"status": "error", "output": str(exc)}

    # Da tiempo a que la respuesta HTTP llegue al navegador antes de cerrar
    # el proceso (Windows tiene el .exe bloqueado mientras esté corriendo).
    Timer(1.5, lambda: os._exit(0)).start()
    return {"status": "ok", "output": f"Descargando la versión {info['latest_version']}. La aplicación se va a cerrar y reabrir sola en un momento."}


@app.get("/api/scan")
def scan():
    # Cada módulo tarda varios segundos (comandos externos); correrlos en
    # paralelo baja el tiempo total de ~8-9s a ~4-5s en vez de sumarlos.
    with ThreadPoolExecutor(max_workers=3) as pool:
        red_f = pool.submit(network.run_all)
        hw_f = pool.submit(hardware.run_all)
        sw_f = pool.submit(software.run_all)
        categories = [
            {"id": "red", "name": "Red", "checks": red_f.result()},
            {"id": "hardware", "name": "Hardware", "checks": hw_f.result()},
            {"id": "software", "name": "Software y sistema", "checks": sw_f.result()},
        ]
    all_checks = [c for cat in categories for c in cat["checks"]]
    return {
        **score_from_checks(all_checks),
        "categories": categories,
        "insights": correlate.correlate(all_checks),
    }


def _same_origin(request: Request) -> bool:
    return request.headers.get("host", "") in ALLOWED_HOSTS


@app.post("/api/scan/sfc")
def scan_sfc(request: Request):
    """Chequeo profundo y lento: verificación de archivos de sistema.

    Requiere que el request venga del propio dashboard (Host esperado) para
    evitar que una página abierta en el navegador del cliente dispare un
    `sfc /scannow` sin que el técnico lo pida.
    """
    if not _same_origin(request):
        return JSONResponse({"status": "error", "output": "Origen no permitido."}, status_code=403)
    return software.run_sfc_scan()


@app.get("/api/actions")
def actions_list():
    return actions.list_actions()


@app.post("/api/actions/{action_id}")
def actions_run(action_id: str, request: Request):
    """Ejecuta una acción de la whitelist. `action_id` se valida contra
    `checks.actions.ACTIONS` — nunca se interpola directamente en un
    comando. Misma protección de Host que `/api/scan/sfc`: son cambios
    reales al equipo, no deben poder dispararse desde otra pestaña."""
    if not _same_origin(request):
        return JSONResponse({"status": "error", "output": "Origen no permitido."}, status_code=403)
    return actions.run_action(action_id)


class SaveScanRequest(BaseModel):
    serial: str
    score: int
    coverage: str
    categories: list
    insights: list = []
    client: Optional[str] = None
    ticket: Optional[str] = None
    technician: Optional[str] = None


@app.post("/api/scan/save")
def scan_save(payload: SaveScanRequest, request: Request):
    """Guarda el escaneo actual en el historial, indexado por número de
    serie, para poder comparar el estado del equipo entre visitas."""
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    try:
        scan_id = history.save_scan(
            payload.serial, payload.score, payload.coverage, payload.categories, payload.insights,
            client=payload.client, ticket=payload.ticket, technician=payload.technician,
        )
    except ValueError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)

    # Cada escaneo guardado también queda como una fila en el inventario CSV
    # acumulado — no depende de datos que mande el cliente, se lee de nuevo
    # la identidad del equipo en el servidor para que sea confiable.
    identity = system_info_check.get_identity()
    if identity.get("available"):
        computer = " ".join(filter(None, [identity.get("manufacturer"), identity.get("model")]))
        inventory.append_row(
            serial=payload.serial, computer=computer, part_number=identity.get("part_number"),
            os_summary=identity.get("os_summary"), score=payload.score, coverage=payload.coverage,
            client=payload.client, ticket=payload.ticket, technician=payload.technician,
        )

    # Copia local garantizada del reporte (PNG) — funciona siempre, sin
    # depender de si hay NAS configurado o si el equipo está en el dominio.
    try:
        png_bytes = report_image.render_report_png(
            {"score": payload.score, "coverage": payload.coverage, "categories": payload.categories, "insights": payload.insights},
            identity,
            {"client": payload.client, "ticket": payload.ticket, "technician": payload.technician},
        )
        local_reports.save_local_report(payload.serial, png_bytes)
    except Exception:
        pass  # el guardado local es best-effort: nunca debe romper el guardado del historial

    return {"status": "ok", "id": scan_id}


@app.get("/api/history/{serial}")
def scan_history(serial: str, request: Request):
    """Historial de escaneos previos de un equipo, para comparar entre
    visitas. Mismo chequeo de Host: es información de tickets/clientes, no
    debe quedar expuesta a otra pestaña."""
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    return history.list_history(serial)


@app.get("/api/disks")
def disks_list(request: Request):
    """Lista discos y particiones reales del equipo. Mismo chequeo de Host
    que el resto de rutas — revela letras/etiquetas de unidades."""
    if not _same_origin(request):
        return JSONResponse({"available": False, "message": "Origen no permitido.", "disks": []}, status_code=403)
    return disks.list_disks()


class FormatPartitionRequest(BaseModel):
    disk_number: int
    partition_number: int
    filesystem: str = "NTFS"
    label: str = ""


class CreatePartitionRequest(BaseModel):
    disk_number: int
    size_mb: int
    filesystem: str = "NTFS"
    label: str = ""


class DeletePartitionRequest(BaseModel):
    disk_number: int
    partition_number: int


class RenameVolumeRequest(BaseModel):
    drive_letter: str
    label: str = ""


class SetDriveLetterRequest(BaseModel):
    disk_number: int
    partition_number: int
    new_letter: str


def _disk_action(request: Request, fn, *args):
    """Envoltorio común para las rutas de acciones de disco: chequeo de
    Host + traducir `DiskActionError` (incluido el rechazo del disco del
    sistema) a una respuesta 400 en vez de un 500 genérico."""
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    try:
        return fn(*args)
    except disk_actions.DiskActionError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


@app.post("/api/disks/format")
def disks_format(payload: FormatPartitionRequest, request: Request):
    return _disk_action(
        request, disk_actions.format_partition,
        payload.disk_number, payload.partition_number, payload.filesystem, payload.label,
    )


@app.post("/api/disks/create-partition")
def disks_create_partition(payload: CreatePartitionRequest, request: Request):
    return _disk_action(
        request, disk_actions.create_partition,
        payload.disk_number, payload.size_mb, payload.filesystem, payload.label,
    )


@app.post("/api/disks/delete-partition")
def disks_delete_partition(payload: DeletePartitionRequest, request: Request):
    return _disk_action(request, disk_actions.delete_partition, payload.disk_number, payload.partition_number)


@app.post("/api/disks/rename-volume")
def disks_rename_volume(payload: RenameVolumeRequest, request: Request):
    return _disk_action(request, disk_actions.rename_volume, payload.drive_letter, payload.label)


@app.post("/api/disks/set-letter")
def disks_set_letter(payload: SetDriveLetterRequest, request: Request):
    return _disk_action(
        request, disk_actions.set_drive_letter,
        payload.disk_number, payload.partition_number, payload.new_letter,
    )


@app.get("/api/technicians")
def technicians_get():
    return settings.get_settings()


class AddTechnicianRequest(BaseModel):
    name: str


@app.post("/api/technicians")
def technicians_add(payload: AddTechnicianRequest, request: Request):
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    try:
        data = settings.add_technician(payload.name)
    except ValueError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
    return {"status": "ok", **data}


@app.get("/api/nas")
def nas_get():
    return {"nas_path": nas.get_nas_path()}


class SetNasPathRequest(BaseModel):
    path: str


@app.post("/api/nas/path")
def nas_set_path(payload: SetNasPathRequest, request: Request):
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    nas.set_nas_path(payload.path)
    return {"status": "ok", "nas_path": nas.get_nas_path()}


class NasCredentialsRequest(BaseModel):
    server: str
    user: str
    password: str


@app.post("/api/nas/credentials")
def nas_save_credentials(payload: NasCredentialsRequest, request: Request):
    """La contraseña llega en este único POST a localhost y se pasa directo
    a `cmdkey`; nunca se escribe a disco ni se registra en el log de
    auditoría (a diferencia de las demás acciones)."""
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    try:
        return nas.save_credentials(payload.server, payload.user, payload.password)
    except nas.NasError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


class NasUploadRequest(BaseModel):
    filename: str
    content: str


@app.post("/api/nas/upload")
def nas_upload(payload: NasUploadRequest, request: Request):
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    try:
        return nas.upload_file(payload.filename, payload.content)
    except nas.NasError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


class RestorePointRequest(BaseModel):
    description: str = "DiagFix"


@app.post("/api/restore-point")
def restore_point_create(payload: RestorePointRequest, request: Request):
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    try:
        return restore.create_restore_point(payload.description)
    except restore.RestoreError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


@app.get("/api/scripts")
def scripts_list(request: Request):
    if not _same_origin(request):
        return JSONResponse([], status_code=403)
    return scripts_runner.list_scripts()


class RunScriptRequest(BaseModel):
    filename: str


@app.post("/api/scripts/run")
def scripts_run(payload: RunScriptRequest, request: Request):
    if not _same_origin(request):
        return JSONResponse({"status": "error", "output": "Origen no permitido."}, status_code=403)
    try:
        return scripts_runner.run_script(payload.filename)
    except scripts_runner.ScriptError as exc:
        return JSONResponse({"status": "error", "output": str(exc)}, status_code=400)


@app.get("/api/inventory")
def inventory_get(request: Request):
    if not _same_origin(request):
        return JSONResponse([], status_code=403)
    return inventory.read_rows()


@app.get("/api/inventory/export")
def inventory_export(request: Request):
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    if not inventory.INVENTORY_PATH.exists():
        return JSONResponse({"error": "Todavía no hay inventario guardado."}, status_code=404)
    return FileResponse(inventory.INVENTORY_PATH, filename="diagfix-inventario.csv", media_type="text/csv")


class ReportPngRequest(BaseModel):
    score: int
    coverage: str
    categories: list
    insights: list = []
    client: Optional[str] = None
    ticket: Optional[str] = None
    technician: Optional[str] = None


@app.post("/api/report/png")
def report_png(payload: ReportPngRequest, request: Request):
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    identity = system_info_check.get_identity()
    png_bytes = report_image.render_report_png(
        {"score": payload.score, "coverage": payload.coverage, "categories": payload.categories, "insights": payload.insights},
        identity,
        {"client": payload.client, "ticket": payload.ticket, "technician": payload.technician},
    )
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/report/specs-png")
def report_specs_png(request: Request):
    """Ficha del equipo sola, como imagen — el equivalente al screenshot de
    especificaciones de la herramienta de referencia, separado del reporte
    de diagnóstico general."""
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    identity = system_info_check.get_identity()
    if not identity.get("available"):
        return JSONResponse({"error": "No se pudo obtener la información del equipo."}, status_code=400)
    png_bytes = report_image.render_specs_png(identity)
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/startup")
def startup_list(request: Request):
    if not _same_origin(request):
        return JSONResponse([], status_code=403)
    return startup_manager.list_items()


class StartupToggleRequest(BaseModel):
    id: str
    enabled: bool


@app.post("/api/startup/toggle")
def startup_toggle(payload: StartupToggleRequest, request: Request):
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    try:
        return startup_manager.set_enabled(payload.id, payload.enabled)
    except startup_manager.StartupError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


class BackupDriversRequest(BaseModel):
    destination: str


@app.post("/api/maintenance/backup-drivers")
def maintenance_backup_drivers(payload: BackupDriversRequest, request: Request):
    if not _same_origin(request):
        return JSONResponse({"status": "error", "message": "Origen no permitido."}, status_code=403)
    try:
        return maintenance.backup_drivers(payload.destination)
    except maintenance.MaintenanceError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


@app.get("/api/monitor/stream")
def monitor_stream(request: Request):
    """Monitoreo en vivo (60s): top procesos, red y latencia cada 2s.

    Mismo chequeo de Host que las demás rutas sensibles — no expone datos de
    procesos en ejecución a una pestaña de otro origen."""
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    return StreamingResponse(monitor.stream_ticks(), media_type="text/event-stream")


@app.get("/api/battery/stream")
def battery_stream(request: Request):
    """Prueba de batería en vivo (pestaña Pruebas): % y estado cada 2s,
    para que el técnico vea si sostiene carga real al desconectar el
    cargador, sin depender del reporte estático de powercfg."""
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    return StreamingResponse(hardware.battery_live_stream(), media_type="text/event-stream")


@app.get("/api/ethernet/stream")
def ethernet_stream(request: Request):
    """Prueba de puerto Ethernet en vivo: estado de los adaptadores
    cableados cada 2s, para conectar un cable y ver si pasa a "Conectado"."""
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    return StreamingResponse(hardware.ethernet_live_stream(), media_type="text/event-stream")


@app.get("/api/usb/stream")
def usb_stream(request: Request):
    """Prueba de puertos USB en vivo: cantidad de dispositivos activos
    cada 2s, para insertar algo puerto por puerto y ver el contador subir."""
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    return StreamingResponse(hardware.usb_live_stream(), media_type="text/event-stream")


@app.get("/api/monitor-output/stream")
def monitor_output_stream(request: Request):
    """Prueba de salida de video en vivo: cantidad de monitores detectados
    cada 2s, para conectar un cable HDMI/DP externo y ver el contador subir."""
    if not _same_origin(request):
        return JSONResponse({"error": "Origen no permitido."}, status_code=403)
    return StreamingResponse(hardware.monitor_output_live_stream(), media_type="text/event-stream")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    # Sin caché: el HTML es lo único que no lleva `?v=` para invalidarlo, así
    # que un index.html cacheado sigue pidiendo el CSS/JS viejo y la app
    # queda en una versión anterior después de actualizarse. Es una carga
    # local de 20 KB, no hay nada que ahorrar cacheándola.
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


def _open_browser():
    """Abre el panel en una ventana de Edge/Chrome en modo `--app` (sin barra
    de direcciones ni pestañas — se ve como una aplicación propia, no como
    "alguien abrió el navegador"), con flags que saltan el asistente de
    bienvenida/términos y el aviso de "hacer default" que de otro modo
    aparecen la primera vez que se usa ese navegador en una cuenta de
    Windows recién iniciada. Si no encuentra un navegador Chromium instalado,
    cae al navegador predeterminado normal (webbrowser.open)."""
    url = "http://127.0.0.1:8000"
    candidatos = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    exe = next((c for c in candidatos if c.exists()), None)
    if exe:
        try:
            subprocess.Popen([str(exe), f"--app={url}", "--no-first-run", "--no-default-browser-check"])
            return
        except OSError:
            pass
    webbrowser.open(url)


if __name__ == "__main__":
    import sys

    from console_ui import should_use_console

    if should_use_console(sys.argv[1:]):
        # Sin navegador disponible (típico en WinPE/OOBE con Shift+F10) o se
        # pidió --console explícitamente: se corre el menú de texto en vez
        # de levantar el servidor web.
        from console_ui import main_loop

        main_loop()
    else:
        import uvicorn

        Timer(1.2, _open_browser).start()
        uvicorn.run(app, host="127.0.0.1", port=8000)
