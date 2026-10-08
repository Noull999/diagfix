"""Diagnósticos de conectividad de red.

Cada función devuelve un dict con:
  name    -> nombre legible del chequeo
  status  -> "ok" | "warning" | "critical" | "unknown" | "error"
  value   -> valor medido, ya formateado para mostrar (str) o None
  message -> resumen corto de qué pasa
  causes  -> lista de posibles causas (vacía si status == "ok")
  fix     -> lista de pasos sugeridos para solucionarlo (vacía si status == "ok")
"""
import json
import platform
import re
import socket
import time

from .base import result as _result
from .base import run as _run
from .base import run_parallel as _run_parallel
from .base import run_powershell as _run_powershell

TIMEOUT = 5

_NET_CONFIG_SCRIPT = r"""
$dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.ServerAddresses.Count -gt 0 } | Select-Object -First 1 -ExpandProperty ServerAddresses)
$proxy = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
$proxyOn = [bool]$proxy.ProxyEnable
$vpn = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -match "VPN|WireGuard|TAP|Tunnel" -and $_.Status -eq "Up" } | Select-Object -ExpandProperty Name)
[ordered]@{ dns = $dns; proxyOn = $proxyOn; proxyServer = $proxy.ProxyServer; vpnAdapters = $vpn } | ConvertTo-Json -Compress
""".strip()


def check_internet():
    """Ping a un host público estable, vía PowerShell (Test-Connection) para
    no depender de que `ping.exe` esté en el idioma esperado."""
    host = "8.8.8.8"
    output = _run_powershell(
        "$r = Test-Connection -ComputerName '%s' -Count 4 -ErrorAction SilentlyContinue; "
        "if ($r) { "
        "$loss = [math]::Round((4 - $r.Count) / 4 * 100); "
        "$avg = [math]::Round(($r | Measure-Object ResponseTime -Average).Average); "
        "\"$loss|$avg\" "
        "} else { \"100|\" }" % host,
        timeout=TIMEOUT + 3,
    )
    if output is None:
        return _result(
            "Conexión a Internet", "error", None,
            "No se pudo ejecutar la prueba de conexión.",
            causes=["Permisos insuficientes", "Firewall bloqueando el comando"],
            fix=["Ejecuta la terminal como administrador y vuelve a intentar."],
        )
    parts = output.strip().split("|")
    if not parts or parts[0] == "":
        return _result("Conexión a Internet", "unknown", None, "No se pudo interpretar la respuesta.")
    try:
        loss = int(parts[0])
    except ValueError:
        return _result("Conexión a Internet", "unknown", None, "No se pudo interpretar la respuesta.")
    latency = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else None

    if loss >= 100:
        return _result(
            "Conexión a Internet", "critical", "Sin respuesta",
            "No hay conexión a Internet.",
            causes=[
                "Cable de red desconectado o dañado",
                "Router/módem apagado, colgado o sin servicio del ISP",
                "Adaptador de red deshabilitado en el equipo",
                "Corte de servicio del proveedor de Internet",
            ],
            fix=[
                "Revisa que el cable esté bien conectado y las luces del módem/router estén normales.",
                "Reinicia el router: desconéctalo de la corriente 30 segundos y vuelve a conectarlo.",
                "Prueba con otro dispositivo en la misma red para descartar que sea solo este equipo.",
                "Si nada de lo anterior funciona, contacta al proveedor de Internet (puede ser un corte de zona).",
            ],
        )
    if loss >= 20:
        return _result(
            "Conexión a Internet", "warning", f"{loss}% perdido",
            "Pérdida de paquetes alta.",
            causes=["Interferencia Wi-Fi", "Cable de red dañado", "Saturación de la red", "Problema del ISP"],
            fix=[
                "Si es Wi-Fi, prueba conectando por cable para descartar interferencia.",
                "Revisa que el cable de red no esté doblado o dañado.",
                "Verifica cuántos dispositivos están usando la red al mismo tiempo.",
                "Si persiste, reinicia el router y, si no mejora, reporta al ISP.",
            ],
        )
    value = f"{latency} ms" if latency is not None else f"{loss}% perdido"
    if latency and latency > 150:
        return _result(
            "Conexión a Internet", "warning", value, "Latencia alta hacia Internet.",
            causes=["Saturación de ancho de banda (descargas o streaming)", "Congestión del ISP", "VPN activa"],
            fix=[
                "Cierra descargas, respaldos en la nube o streaming que puedan estar consumiendo ancho de banda.",
                "Si hay una VPN activa, pruébala desactivada.",
                "Prueba en otro horario para descartar congestión del proveedor.",
            ],
        )
    return _result("Conexión a Internet", "ok", value, "Conexión estable.")


_DNS_HOSTS = ("www.google.com", "www.microsoft.com", "www.cloudflare.com")


def check_dns():
    """Resuelve dominios conocidos para confirmar que la resolución DNS
    funciona y no es lenta.

    Se miden tres dominios distintos y se toma el mejor tiempo: una sola
    consulta coincidía con el arranque de los ~20 procesos del escaneo y
    daba "DNS lento" falso (medido: 0-9 ms sola vs 875 ms dentro del
    escaneo). Un DNS lento de verdad es lento en las tres consultas; un pico
    de carga del propio escaneo afecta como mucho a una."""
    socket.setdefaulttimeout(TIMEOUT)
    times = []
    failed = 0
    for host in _DNS_HOSTS:
        start = time.time()
        try:
            socket.gethostbyname(host)
            times.append(round((time.time() - start) * 1000))
        except socket.gaierror:
            failed += 1
        except Exception:
            pass
    if failed == len(_DNS_HOSTS):
        return _result(
            "Resolución DNS", "critical", "Falla",
            "No se pudo resolver nombres de dominio.",
            causes=[
                "El servidor DNS configurado no responde",
                "Problema del ISP",
                "Malware que alteró la configuración DNS",
            ],
            fix=[
                "Cambia el DNS del adaptador de red a 8.8.8.8 / 1.1.1.1 (o el DNS de tu proveedor).",
                "Ejecuta `ipconfig /flushdns` para limpiar la caché DNS.",
                "Si el problema persiste en varios equipos de la misma red, es probable que sea el router o el ISP.",
            ],
            action_id="flush_dns",
        )
    if not times:
        return _result("Resolución DNS", "unknown", None, "No se pudo comprobar la resolución DNS.")
    elapsed = min(times)
    if elapsed > 300:
        return _result(
            "Resolución DNS", "warning", f"{elapsed} ms", "El DNS responde lento.",
            causes=["Servidor DNS del ISP saturado", "Latencia general de la red"],
            fix=["Prueba cambiar a un DNS público (8.8.8.8 o 1.1.1.1) y vuelve a medir."],
        )
    return _result("Resolución DNS", "ok", f"{elapsed} ms", "Resolución de nombres funcionando bien.")


def check_gateway():
    """Verifica que el router / puerta de enlace local responda.

    Usa Get-NetRoute en vez de parsear `ipconfig` — evita depender del
    idioma del sistema para encontrar la línea de "puerta de enlace".
    """
    if platform.system() != "Windows":
        return _result("Puerta de enlace (router)", "unknown", None, "Chequeo disponible solo en Windows.")
    gateway = _run_powershell(
        "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
        "Sort-Object RouteMetric | Select-Object -First 1 -ExpandProperty NextHop)"
    )
    gateway = (gateway or "").strip()
    if not gateway:
        return _result(
            "Puerta de enlace (router)", "unknown", None,
            "No se detectó una puerta de enlace configurada.",
            causes=["El adaptador de red no tiene IP asignada (sin DHCP)", "Cable o Wi-Fi desconectado"],
            fix=["Revisa la conexión física o Wi-Fi.", "Ejecuta `ipconfig /renew` para pedir una IP nueva."],
        )
    ok = _run_powershell(
        "if (Test-Connection -ComputerName '%s' -Count 2 -Quiet -ErrorAction SilentlyContinue) "
        "{ 'ok' } else { 'fail' }" % gateway
    )
    if ok is None:
        return _result("Puerta de enlace (router)", "unknown", gateway, "No se pudo hacer ping al router.")
    if ok.strip() != "ok":
        return _result(
            "Puerta de enlace (router)", "critical", gateway,
            "El router no responde.",
            causes=["Router apagado o colgado", "Cable de red desconectado", "Falla del switch/patchera intermedia"],
            fix=[
                "Revisa que el router tenga corriente y sus luces estén normales.",
                "Reinicia el router (desconectar 30 segundos).",
                "Revisa cables y conectores intermedios si hay switch de por medio.",
            ],
            action_id="renew_ip",
        )
    return _result("Puerta de enlace (router)", "ok", gateway, "El router responde correctamente.")


def check_wifi_signal():
    """Si hay un adaptador Wi-Fi activo, revisa la intensidad de señal.

    `netsh` emite su salida en UTF-8 al redirigirla; antes se decodificaba
    con la code page de la consola y el regex de "Señal" nunca matcheaba.
    """
    if platform.system() != "Windows":
        return _result("Señal Wi-Fi", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run(["netsh", "wlan", "show", "interfaces"])
    if not output or re.search(r"no hay ninguna interfaz|there is no wireless interface", output, re.IGNORECASE):
        return _result("Señal Wi-Fi", "unknown", "N/D", "No se detectó adaptador Wi-Fi activo (puede estar por cable).")
    match = re.search(r"(?:Se[ñn]al|Signal)\s*:?\s*(\d+)%", output, re.IGNORECASE)
    if not match:
        return _result("Señal Wi-Fi", "unknown", None, "No se pudo leer la intensidad de señal.")
    signal = int(match.group(1))
    causes = ["Distancia al router", "Paredes de concreto o metal entre el equipo y el router", "Interferencia de otros dispositivos (microondas, otros routers)"]
    fix = ["Acerca el equipo al router o usa un repetidor/mesh.", "Cambia el canal Wi-Fi del router (2.4GHz suele tener más interferencia que 5GHz).", "Si es posible, conecta por cable de red."]
    if signal < 30:
        return _result("Señal Wi-Fi", "critical", f"{signal}%", "Señal muy débil.", causes=causes, fix=fix)
    if signal < 60:
        return _result("Señal Wi-Fi", "warning", f"{signal}%", "Señal débil, puede causar cortes intermitentes.", causes=causes, fix=fix)
    return _result("Señal Wi-Fi", "ok", f"{signal}%", "Buena señal Wi-Fi.")


def check_network_config():
    """Reporta el DNS realmente en uso, si hay un proxy manual activo y si hay
    una VPN conectada — configuraciones que explican fallas que a simple
    vista parecen "internet no funciona" pero son específicas del equipo."""
    if platform.system() != "Windows":
        return _result("Configuración de red (DNS/Proxy/VPN)", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_NET_CONFIG_SCRIPT, timeout=15)
    if not output:
        return _result("Configuración de red (DNS/Proxy/VPN)", "unknown", None, "No se pudo consultar.")
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return _result("Configuración de red (DNS/Proxy/VPN)", "unknown", None, "No se pudo interpretar la respuesta.")

    dns = data.get("dns") or []
    if isinstance(dns, str):
        dns = [dns]
    proxy_on = bool(data.get("proxyOn"))
    proxy_server = data.get("proxyServer")
    vpn = data.get("vpnAdapters") or []
    if isinstance(vpn, str):
        vpn = [vpn]

    parts = [f"DNS: {', '.join(dns) if dns else 'N/D'}"]
    if proxy_on:
        parts.append(f"Proxy manual activo ({proxy_server or 'sin datos'})")
    if vpn:
        parts.append(f"VPN activa: {', '.join(vpn)}")
    value = " | ".join(parts)

    if proxy_on:
        return _result(
            "Configuración de red (DNS/Proxy/VPN)", "warning", value,
            "Hay un proxy manual configurado en el navegador/sistema.",
            causes=["Proxy configurado manualmente (a propósito o por malware/adware)", "Configuración heredada de una red corporativa anterior"],
            fix=[
                "Ve a Configuración > Red e Internet > Proxy y revisa si el proxy manual debería estar activo.",
                "Si el usuario no lo configuró a propósito, desactívalo — es una causa común de 'no carga ninguna página' con Internet funcionando.",
            ],
        )
    if vpn:
        return _result(
            "Configuración de red (DNS/Proxy/VPN)", "warning", value,
            "Hay una VPN activa, puede afectar velocidad o acceso a recursos locales.",
            causes=["VPN corporativa o personal conectada"],
            fix=["Si el problema reportado no requiere VPN, pide desactivarla para descartarla como causa."],
        )
    return _result("Configuración de red (DNS/Proxy/VPN)", "ok", value, "Sin proxy manual ni VPN activa.")


def run_all():
    return _run_parallel([
        check_internet, check_dns, check_gateway, check_wifi_signal,
        check_network_config, check_link_speed, check_network_profile,
        check_internet_speed,
    ])


_LINK_SPEED_SCRIPT = r"""
$a = Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
     Where-Object { $_.Status -eq 'Up' } | Select-Object -First 1
if ($a) {
    [ordered]@{
        name      = $a.Name
        speedBps  = [int64]$a.Speed
        media     = [string]$a.MediaType
    } | ConvertTo-Json -Compress
}
""".strip()


def check_link_speed():
    """Velocidad negociada del adaptador de red activo. Un cable de red en mal
    estado negocia a 100 Mbps en una placa y un switch de 1 Gbps — el equipo
    "tiene internet" pero va diez veces más lento, y no lo reporta nadie."""
    if platform.system() != "Windows":
        return _result("Velocidad del enlace de red", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_LINK_SPEED_SCRIPT, timeout=20)
    if not output:
        return _result("Velocidad del enlace de red", "unknown", None, "No se detectó un adaptador de red activo.")
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return _result("Velocidad del enlace de red", "unknown", None, "No se pudo interpretar la respuesta.")

    speed_bps = data.get("speedBps") or 0
    mbps = round(speed_bps / 1_000_000)
    name = data.get("name") or "adaptador"
    is_wifi = "802.11" in (data.get("media") or "") or "wireless" in (data.get("media") or "").lower()
    value = f"{mbps} Mbps ({name})"

    if mbps <= 0:
        return _result("Velocidad del enlace de red", "unknown", None, "El adaptador no reporta velocidad.")
    # En Wi-Fi la velocidad negociada baja sola con la distancia al router: no
    # es un defecto. En cable, 10/100 sobre hardware gigabit sí lo es.
    if not is_wifi and mbps <= 100:
        return _result(
            "Velocidad del enlace de red", "warning", value,
            "El cable de red está negociando a 100 Mbps o menos.",
            causes=[
                "Cable de red dañado o de categoría vieja (Cat5 en vez de Cat5e/Cat6)",
                "El puerto del switch o roseta está limitado a 100 Mbps",
                "Conector RJ45 mal crimpado o con pines sueltos",
            ],
            fix=[
                "Prueba con otro cable de red, idealmente Cat5e o Cat6.",
                "Prueba el equipo en otro puerto del switch para descartar el puerto.",
                "Si con otro cable sube a 1000 Mbps, el cable original está dañado.",
            ],
        )
    return _result("Velocidad del enlace de red", "ok", value, "El enlace negoció a una velocidad normal.")


_PROFILE_SCRIPT = r"""
[ordered]@{
    categories = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.NetworkCategory })
    domain     = [bool](Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue).PartOfDomain
} | ConvertTo-Json -Compress
""".strip()


def check_network_profile():
    """Perfil de red (Público/Privado/Dominio).

    "Público" por sí solo no es un problema — es lo recomendado en redes no
    confiables, y antes se marcaba como alerta en cualquier notebook en una
    casa o un café. El caso que sí es una falla real es un equipo del
    dominio que NO reconoce la red como "Dominio": ahí el firewall le aplica
    reglas de red pública y fallan unidades de red, impresoras y directivas
    aunque el equipo esté enchufado en la oficina."""
    if platform.system() != "Windows":
        return _result("Perfil de red", "unknown", None, "Chequeo disponible solo en Windows.")
    output = _run_powershell(_PROFILE_SCRIPT, timeout=20)
    try:
        data = json.loads(output) if output else {}
    except (json.JSONDecodeError, ValueError):
        data = {}
    categories = data.get("categories") or []
    if isinstance(categories, str):
        categories = [categories]
    if not categories:
        return _result("Perfil de red", "unknown", None, "No se pudo leer el perfil de la red activa.")
    etiquetas = {"Public": "Público", "Private": "Privado", "DomainAuthenticated": "Dominio"}
    legible = ", ".join(etiquetas.get(c, c) for c in categories)

    if data.get("domain") and "DomainAuthenticated" not in categories:
        return _result(
            "Perfil de red", "warning", legible,
            "Equipo del dominio que no reconoce la red como red del dominio: unidades de red, impresoras y directivas pueden fallar.",
            causes=[
                "El equipo no está en la red de la oficina (en casa o fuera es normal)",
                "El servicio de reconocimiento de red (NLA) detectó la red antes de que el controlador de dominio respondiera",
                "Problema de DNS: el equipo no encuentra el controlador de dominio",
            ],
            fix=[
                "Si está en la oficina, desconecta y vuelve a conectar la red (o reinicia el servicio 'Reconocimiento de ubicación de red').",
                "Verifica que el DNS configurado sea el de la empresa (chequeo 'Configuración de red').",
                "Si persiste, ejecuta `gpupdate /force` y reinicia el equipo.",
            ],
        )
    if "Public" in categories:
        return _result(
            "Perfil de red", "ok", legible,
            "Red marcada como Pública (lo recomendado en redes no confiables). Si el usuario necesita ver impresoras o carpetas compartidas de esa red, cámbiala a Privada.",
        )
    return _result("Perfil de red", "ok", legible, "La red permite el descubrimiento de equipos y recursos compartidos.")


# Endpoint público de Cloudflare para medir descarga. Se piden 5 MB: alcanza
# para estimar el ancho de banda sin castigar una conexión lenta.
_SPEED_URL = "https://speed.cloudflare.com/__down?bytes=5000000"
_SPEED_BYTES = 5_000_000


def check_internet_speed():
    """Mide la velocidad de descarga real. La diferencia con `check_internet`
    es que ahí se responde "¿hay internet?" y acá "¿sirve?": una conexión que
    responde al ping pero baja a 1 Mbps se siente caída para el usuario."""
    import urllib.request

    from .base import windows_ssl_context as _ssl_context

    # El cronómetro corre solo durante la transferencia del cuerpo: antes
    # incluía armar el contexto TLS (carga cientos de certificados de
    # Windows), DNS, conexión y handshake — bajo la carga del propio escaneo
    # eso sumaba >1s a una descarga de ~0.5s y una conexión de ~78 Mbps
    # salía en 15 Mbps "lenta".
    ctx = _ssl_context()

    def medir():
        # Cloudflare responde 403 al user-agent por defecto de urllib.
        peticion = urllib.request.Request(_SPEED_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(peticion, timeout=15, context=ctx) as resp:
            start = time.perf_counter()
            downloaded = len(resp.read())
            elapsed = time.perf_counter() - start
        if elapsed <= 0 or downloaded < _SPEED_BYTES / 2:
            return None
        return round((downloaded * 8) / elapsed / 1_000_000, 1)

    try:
        mbps = medir()
    except Exception:
        return _result("Velocidad de descarga", "unknown", None,
                       "No se pudo medir (sin salida a internet, proxy o firewall bloqueando).")
    # Descifrar TLS compite por CPU con los procesos del escaneo: si la
    # primera medición sale baja se repite una vez, pasado el pico, y se
    # toma la mejor. Una conexión lenta de verdad sale lenta las dos veces.
    if mbps is not None and mbps < 20:
        time.sleep(2)
        try:
            mbps = max(mbps, medir() or 0)
        except Exception:
            pass
    if mbps is None:
        return _result("Velocidad de descarga", "unknown", None, "La medición no se completó.")

    value = f"{mbps} Mbps"
    causes = [
        "Saturación de la red (otros equipos descargando o actualizándose)",
        "Señal Wi-Fi débil o cable de red negociando a baja velocidad",
        "Plan contratado bajo, o degradación del servicio del proveedor",
    ]
    fix = [
        "Repite la medición con el equipo conectado por cable para descartar el Wi-Fi.",
        "Revisa el chequeo de velocidad del enlace y el de señal Wi-Fi de este mismo panel.",
        "Si por cable sigue bajo, el problema está en la red o en el proveedor, no en el equipo.",
    ]
    if mbps < 5:
        return _result("Velocidad de descarga", "critical", value,
                       "La velocidad de descarga es muy baja.", causes=causes, fix=fix)
    if mbps < 20:
        return _result("Velocidad de descarga", "warning", value,
                       "La velocidad de descarga es baja para trabajar cómodo.", causes=causes, fix=fix)
    return _result("Velocidad de descarga", "ok", value, "La velocidad de descarga es adecuada.")
