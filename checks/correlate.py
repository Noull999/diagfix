"""Correlación de causa raíz: combina los chequeos ya calculados por
network/hardware/software para señalar un diagnóstico probable cuando varias
señales apuntan a la misma causa. No vuelve a ejecutar nada — solo lee el
resultado de `run_all()` de los 3 módulos.
"""

SEVERITY_ORDER = {"critical": 0, "warning": 1}


def _by_name(checks):
    return {c["name"]: c for c in checks}


def _status(by_name, name):
    c = by_name.get(name)
    return c["status"] if c else None


def _insight(severity, title, message):
    return {"severity": severity, "title": title, "message": message}


def correlate(checks):
    by_name = _by_name(checks)
    insights = []

    disk_health = _status(by_name, "Salud del disco")
    disk_smart = _status(by_name, "S.M.A.R.T. del disco")
    if disk_health == "critical" or disk_smart == "critical":
        insights.append(_insight(
            "critical", "El disco probablemente está fallando",
            "El estado operacional y/o los contadores S.M.A.R.T. reportan problemas reales de hardware. "
            "Respalda los datos importantes ahora, antes de seguir usando el equipo.",
        ))

    internet = _status(by_name, "Conexión a Internet")
    gateway = _status(by_name, "Puerta de enlace (router)")
    if internet == "critical" and gateway == "ok":
        insights.append(_insight(
            "warning", "El corte parece ser del proveedor de Internet, no del equipo",
            "El router local responde correctamente pero no hay salida a Internet: es un patrón típico de "
            "corte del ISP o del módem, no del equipo del cliente.",
        ))
    elif internet == "critical" and gateway == "critical":
        insights.append(_insight(
            "critical", "El problema es local (router o cableado), no del proveedor",
            "Ni el router ni Internet responden: revisa primero la alimentación del router y el cableado "
            "antes de contactar al ISP.",
        ))

    battery = _status(by_name, "Salud de la batería")
    bsod = _status(by_name, "Pantallazos azules / reinicios inesperados")
    if battery in ("warning", "critical") and bsod in ("warning", "critical"):
        insights.append(_insight(
            "warning", "La batería podría estar causando los apagones inesperados",
            "Hay desgaste de batería y además se detectaron apagones/reinicios abruptos recientes: es un "
            "patrón consistente con una batería que ya no sostiene carga bajo carga real.",
        ))

    event_errors = _status(by_name, "Errores en el registro de eventos (24h)")
    if event_errors == "critical" and bsod in ("warning", "critical"):
        insights.append(_insight(
            "critical", "El sistema parece inestable (posibles archivos corruptos o driver con fallas)",
            "Muchos errores recientes en el Visor de Eventos coinciden con reinicios/colapsos inesperados. "
            "Ejecuta la verificación profunda (sfc) y revisa drivers recientes, especialmente de video o red.",
        ))

    cpu = _status(by_name, "Uso de CPU")
    defender = _status(by_name, "Antivirus y firewall")
    if cpu in ("warning", "critical") and defender in ("warning", "critical"):
        insights.append(_insight(
            "warning", "El uso alto de CPU combinado con protección desactivada es sospechoso",
            "Con el antivirus o firewall desactivados y CPU alta sostenida, no se puede descartar malware. "
            "Revisa el Administrador de tareas por procesos desconocidos y corre un análisis completo.",
        ))

    pending_reboot = _status(by_name, "Reinicio pendiente")
    last_update = _status(by_name, "Últimas actualizaciones")
    if pending_reboot == "warning" and last_update in ("warning", "critical"):
        insights.append(_insight(
            "warning", "Un reinicio pendiente puede estar bloqueando las actualizaciones",
            "Hay cambios esperando reinicio y además hace tiempo que no se instalan actualizaciones nuevas: "
            "reinicia el equipo y luego vuelve a buscar actualizaciones manualmente.",
        ))

    insights.sort(key=lambda i: SEVERITY_ORDER.get(i["severity"], 9))
    return insights
