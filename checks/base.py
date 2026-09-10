"""Utilidades compartidas por los módulos de chequeo.

Centraliza la ejecución de comandos y PowerShell para evitar el bug más
costoso de este proyecto: `subprocess.run(..., text=True)` decodifica con la
code page de la consola (cp1252/OEM), pero `netsh`/PowerShell suelen emitir
UTF-8 o UTF-16LE. Eso rompe cualquier acento ("Señal" -> no matchea nunca) y
hace fallar chequeos en silencio.
"""
import platform
import ssl
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def app_dir() -> Path:
    """Carpeta donde viven los archivos locales de DiagFix (historial,
    settings, log de acciones, inventario, carpeta scripts/).

    En modo desarrollo (`python app.py`) es la raíz del proyecto. Empaquetado
    con PyInstaller (`--onefile`), `__file__` de los módulos apunta a la
    carpeta temporal de extracción (`sys._MEIPASS`) — que se borra al cerrar
    el programa — así que ahí en cambio se usa la carpeta real donde está el
    `.exe` (`sys.executable`), para que los datos persistan entre ejecuciones
    y el técnico pueda encontrar/editar esos archivos (ej. la carpeta
    `scripts/`) al lado del programa.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def flags():
    return subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0


def ps_quote(value: str) -> str:
    """Prepara texto para interpolarlo dentro de un string de PowerShell
    entre **comillas simples** ('...'). A diferencia de las comillas dobles,
    las simples no interpolan variables ni subexpresiones (`$(...)`) — es la
    única forma segura de meter texto que viene del técnico (una etiqueta de
    disco, una ruta) directo en un comando armado con f-string, sin abrir una
    inyección de comandos. Iba a bastar con sacar comillas dobles, pero eso
    no bloquea `$(Remove-Item ...)` dentro de un string entre comillas
    dobles — PowerShell sí lo ejecuta."""
    return (value or "").replace("'", "''")


def _decode(raw: bytes) -> str:
    if raw is None:
        return ""
    if raw[:2] == b"\xff\xfe" or (len(raw) > 3 and raw[1] == 0 and raw[3] == 0):
        # UTF-16LE con o sin BOM: lo usan algunas herramientas de consola
        # como `sfc` cuando su salida se redirige a un pipe.
        try:
            return raw.decode("utf-16le")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "cp850", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run(cmd, timeout=10):
    """Ejecuta un comando externo (ping, netsh, ipconfig...) y decodifica su
    salida probando varias code pages en vez de asumir la de la consola."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=flags(),
            # Sin stdin: si un comando decide preguntar algo (`gpupdate` pide
            # confirmación cuando una directiva exige reinicio), lee EOF y
            # sigue, en vez de colgarse hasta agotar el timeout.
            stdin=subprocess.DEVNULL,
        )
        return _decode(result.stdout)
    except Exception:
        return None


def run_powershell(command: str, timeout=15):
    """Ejecuta un comando de PowerShell forzando salida UTF-8, para que el
    resultado no dependa de la code page de la consola ni del idioma del
    sistema cuando el propio comando ya devuelve datos formateados."""
    wrapped = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "$OutputEncoding = [Text.Encoding]::UTF8; " + command
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", wrapped],
            capture_output=True, timeout=timeout, creationflags=flags(),
            stdin=subprocess.DEVNULL,
        )
        return _decode(result.stdout).strip()
    except Exception:
        return None


# Tamaños "de caja" en GB decimal (1 GB = 1000^3 bytes). Los fabricantes
# marketean en decimal; Windows suele mostrar binario (1024^3), por eso un
# disco "de 500GB" se ve como "465.7 GB" — confunde al técnico sin necesidad.
_COMMERCIAL_SIZES_GB = [
    32, 64, 120, 128, 160, 180, 200, 240, 250, 256, 320, 400, 480, 500,
    512, 640, 750, 960, 1000, 1500, 2000, 3000, 4000, 8000,
]


def commercial_size_label(size_bytes):
    """Redondea un tamaño en bytes al tamaño comercial más cercano (ej. un
    disco de 500.107.862.016 bytes se muestra "500 GB", no "465.8 GB")."""
    if not size_bytes:
        return None
    decimal_gb = size_bytes / 1_000_000_000
    closest = min(_COMMERCIAL_SIZES_GB, key=lambda g: abs(g - decimal_gb))
    if abs(closest - decimal_gb) / closest > 0.08:
        # Muy lejos de cualquier tamaño comercial conocido: mejor mostrar el
        # valor real que inventar una etiqueta engañosa.
        return f"{round(decimal_gb)} GB"
    if closest >= 1000:
        tb = closest / 1000
        return f"{tb:g} TB"
    return f"{closest} GB"


def windows_ssl_context() -> ssl.SSLContext:
    """Contexto TLS que además confía en los certificados del almacén de
    Windows (ROOT + CA), no solo en el paquete de certificados que trae
    Python empaquetado (PyInstaller). Sin esto, un antivirus o proxy
    corporativo que inspecciona HTTPS con su propio certificado raíz —
    instalado y confiable para Windows/Edge, pero desconocido para el
    Python embebido— hace fallar la conexión con
    `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`,
    aunque el equipo tenga internet perfectamente. `ssl.enum_certificates`
    es una API de la stdlib exclusiva de Windows, no requiere ninguna
    dependencia nueva. Usarlo en cualquier `urllib.request.urlopen(...,
    context=...)` que salga a internet."""
    ctx = ssl.create_default_context()
    if hasattr(ssl, "enum_certificates"):
        for store in ("ROOT", "CA"):
            try:
                entries = ssl.enum_certificates(store)
            except OSError:
                continue
            for cert_der, encoding, _trust in entries:
                if encoding != "x509_asn":
                    continue
                try:
                    ctx.load_verify_locations(cadata=cert_der)
                except ssl.SSLError:
                    pass  # certificado repetido o inválido: se ignora, no es fatal.
    return ctx


def run_parallel(funcs):
    """Ejecuta una lista de funciones sin argumentos en paralelo, preservando
    el orden de salida. Cada chequeo es independiente (ninguno usa el
    resultado de otro) y casi todo el tiempo lo pasan esperando un
    subprocess o una conexión de red, no CPU — por eso el `run_all()` de
    cada módulo corría 8-10 chequeos uno detrás del otro dejando el tiempo
    total atado al que sumaban todos, en vez de al más lento del grupo.
    """
    if not funcs:
        return []
    with ThreadPoolExecutor(max_workers=len(funcs)) as pool:
        futures = [pool.submit(fn) for fn in funcs]
        return [f.result() for f in futures]


def result(name, status, value, message, causes=None, fix=None, action_id=None):
    return {
        "name": name,
        "status": status,
        "value": value,
        "message": message,
        "causes": causes or [],
        "fix": fix or [],
        "action_id": action_id,
    }
