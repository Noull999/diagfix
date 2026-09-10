"""Consulta de garantía por número de serie contra la página del fabricante.

Estructura: un esqueleto común (detectar marca → consultar → normalizar) y
un adaptador por marca, porque cada fabricante tiene su propio endpoint,
autenticación y formato de respuesta. Hoy solo Lenovo funciona sin
credenciales; Dell y HP exigen registrarse para obtener una API key, y
ASUS/Acer solo ofrecen un formulario web con captcha.

Requiere que el equipo del cliente tenga salida a internet. Si no la tiene
(o hay un proxy corporativo bloqueando), se informa y no se rompe nada:
la ficha del equipo sigue mostrándose igual sin este dato.
"""
import json
import urllib.error
import urllib.request

from .base import windows_ssl_context as _ssl_context

_TIMEOUT = 12
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# Endpoint que usa la propia web de soporte de Lenovo. No está documentado
# como API pública ni pide credenciales: si algún día Lenovo lo cambia, esto
# deja de funcionar y la ficha simplemente muestra "no disponible".
_LENOVO_URL = "https://pcsupport.lenovo.com/us/en/api/v4/upsell/redport/getIbaseInfo"


def _detect_brand(manufacturer: str) -> str:
    m = (manufacturer or "").lower()
    if "lenovo" in m:
        return "lenovo"
    if "dell" in m:
        return "dell"
    if "hp" in m or "hewlett" in m:
        return "hp"
    if "asus" in m:
        return "asus"
    if "acer" in m:
        return "acer"
    return "desconocida"


def _lenovo(serial: str) -> dict:
    payload = json.dumps({
        "serialNumber": serial,
        "machineType": "",
        "country": "us",
        "language": "en",
    }).encode("utf-8")
    req = urllib.request.Request(
        _LENOVO_URL,
        data=payload,
        headers={"User-Agent": _USER_AGENT, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ssl_context()) as resp:
        data = json.loads(resp.read().decode("utf-8")).get("data") or {}

    if not data:
        return {"available": False, "brand": "lenovo", "reason": "Lenovo no reconoció este número de serie."}

    maquina = data.get("machineInfo") or {}
    actual = data.get("currentWarranty") or {}
    # Si no hay garantía "actual", se cae a la garantía base de fábrica para
    # al menos mostrar las fechas originales del equipo.
    if not actual:
        base = data.get("baseWarranties") or []
        actual = base[0] if base else {}

    en_garantia = not data.get("oow", True)
    return {
        "available": True,
        "brand": "lenovo",
        "in_warranty": en_garantia,
        "status": data.get("warrantyStatus"),
        "type": actual.get("name"),
        "service": actual.get("deliveryTypeName"),
        "start_date": actual.get("startDate"),
        "end_date": actual.get("endDate"),
        "remaining_days": actual.get("remainingDays"),
        "product_name": maquina.get("productName"),
        "build_date": maquina.get("buildDate"),
        "ship_date": maquina.get("shipDate"),
    }


# Marcas cuyo adaptador todavía no se puede implementar, con el motivo real
# para que quede claro qué haría falta en cada caso.
_SIN_ADAPTADOR = {
    "dell": "Dell exige una API key de una cuenta TechDirect de empresa.",
    "hp": "HP exige una API key y su web de consulta tiene protección anti-bot.",
    "asus": "ASUS solo ofrece un formulario web con captcha, sin API.",
    "acer": "Acer solo ofrece un formulario web con captcha, sin API.",
}


def lookup(serial: str, manufacturer: str) -> dict:
    serial = (serial or "").strip()
    if not serial:
        return {"available": False, "reason": "El equipo no reporta número de serie."}

    marca = _detect_brand(manufacturer)
    if marca in _SIN_ADAPTADOR:
        return {"available": False, "brand": marca, "reason": _SIN_ADAPTADOR[marca]}
    if marca != "lenovo":
        return {"available": False, "brand": marca, "reason": "No hay consulta de garantía para esta marca."}

    try:
        return _lenovo(serial)
    except urllib.error.HTTPError as exc:
        return {"available": False, "brand": marca,
                "reason": f"Lenovo respondió HTTP {exc.code}. Puede ser un bloqueo de red del cliente."}
    except urllib.error.URLError as exc:
        return {"available": False, "brand": marca,
                "reason": f"Sin conexión a Lenovo ({exc.reason}). Este equipo necesita internet para consultar la garantía."}
    except Exception as exc:
        return {"available": False, "brand": marca, "reason": f"No se pudo consultar la garantía: {exc}"}
