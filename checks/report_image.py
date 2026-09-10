"""Renderiza el reporte de diagnóstico como una imagen PNG — útil para
pegarlo directo en un chat/ticket sin adjuntar un archivo aparte. Usa
Pillow (ya es dependencia del proyecto) y la fuente Consolas de Windows
si está disponible, para que se vea como una tabla prolija.
"""
import io
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_STATUS_COLOR = {
    "ok": (63, 191, 130),
    "warning": (232, 168, 59),
    "critical": (232, 96, 76),
    "error": (232, 96, 76),
    "unknown": (140, 148, 160),
}
_BG = (255, 255, 255)
_TEXT = (28, 36, 48)
_MUTED = (100, 110, 125)
_LINE = (226, 230, 236)

_WIDTH = 860
_PAD = 24
_LINE_H = 22


def _font(size, bold=False):
    candidates = (["consolab.ttf", "consola.ttf"] if bold else ["consola.ttf"]) + ["cour.ttf"]
    for name in candidates:
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_report_png(scan: dict, identity: dict, meta: dict) -> bytes:
    font = _font(14)
    font_bold = _font(14, bold=True)
    font_title = _font(22, bold=True)
    font_small = _font(12)

    categories = scan.get("categories") or []
    insights = scan.get("insights") or []

    # Cantidad de líneas total para calcular el alto de la imagen de antemano.
    n_lines = 6  # título + meta + score + separadores
    n_lines += len(insights) * 2
    for cat in categories:
        n_lines += 1
        for c in cat.get("checks", []):
            n_lines += 1
            if c.get("status") != "ok" and c.get("message"):
                n_lines += 1

    height = _PAD * 2 + n_lines * _LINE_H + 40
    img = Image.new("RGB", (_WIDTH, height), _BG)
    draw = ImageDraw.Draw(img)
    y = _PAD

    draw.text((_PAD, y), "DiagFix — Reporte de diagnóstico", font=font_title, fill=_TEXT)
    y += 34

    meta_parts = []
    if meta.get("client"):
        meta_parts.append(f"Cliente: {meta['client']}")
    if meta.get("ticket"):
        meta_parts.append(f"Ticket: {meta['ticket']}")
    if meta.get("technician"):
        meta_parts.append(f"Técnico: {meta['technician']}")
    if identity.get("serial"):
        meta_parts.append(f"Serie: {identity['serial']}")
    draw.text((_PAD, y), "  ·  ".join(meta_parts) or "—", font=font_small, fill=_MUTED)
    y += _LINE_H

    draw.text((_PAD, y), f"Puntaje: {scan.get('score')}/100   (cobertura {scan.get('coverage')})",
              font=font_bold, fill=_TEXT)
    y += _LINE_H + 6
    draw.line([(_PAD, y), (_WIDTH - _PAD, y)], fill=_LINE, width=1)
    y += 14

    if insights:
        draw.text((_PAD, y), "DIAGNÓSTICO PROBABLE", font=font_bold, fill=_TEXT)
        y += _LINE_H
        for ins in insights:
            color = _STATUS_COLOR.get(ins.get("severity"), _MUTED)
            draw.ellipse([_PAD, y + 6, _PAD + 8, y + 14], fill=color)
            draw.text((_PAD + 16, y), ins.get("title", ""), font=font_bold, fill=_TEXT)
            y += _LINE_H
            draw.text((_PAD + 16, y), ins.get("message", ""), font=font_small, fill=_MUTED)
            y += _LINE_H
        y += 8
        draw.line([(_PAD, y), (_WIDTH - _PAD, y)], fill=_LINE, width=1)
        y += 14

    for cat in categories:
        draw.text((_PAD, y), cat.get("name", "").upper(), font=font_bold, fill=_TEXT)
        y += _LINE_H
        for c in cat.get("checks", []):
            color = _STATUS_COLOR.get(c.get("status"), _MUTED)
            draw.ellipse([_PAD, y + 7, _PAD + 8, y + 15], fill=color)
            name_text = c.get("name", "")
            value_text = str(c.get("value") if c.get("value") is not None else "—")
            draw.text((_PAD + 16, y), name_text, font=font, fill=_TEXT)
            value_w = draw.textlength(value_text, font=font)
            draw.text((_WIDTH - _PAD - value_w, y), value_text, font=font, fill=_MUTED)
            y += _LINE_H
            if c.get("status") != "ok" and c.get("message"):
                draw.text((_PAD + 16, y), c["message"], font=font_small, fill=_MUTED)
                y += _LINE_H
        y += 4

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_specs_png(identity: dict) -> bytes:
    """Ficha del equipo sola (Equipo/Sistema/RAM/Discos) — equivalente al
    "screenshot de especificaciones" que hacía la herramienta de referencia
    del usuario, separado del reporte de diagnóstico."""
    font = _font(14)
    font_small = _font(12)
    font_title = _font(22, bold=True)
    font_header = _font(15, bold=True)

    ram_modules = identity.get("ram_modules") or []
    disks = identity.get("disks") or []
    gpus = identity.get("gpus") or []
    perifericos_rows = [
        ("Monitores conectados", identity.get("monitor_count")),
        ("Dispositivos USB activos", identity.get("usb_device_count")),
        ("Impresoras instaladas", identity.get("printer_count")),
    ]

    equipo_rows = [
        ("Equipo", " ".join(filter(None, [identity.get("manufacturer"), identity.get("model")])) or "—"),
        ("Número de serie", identity.get("serial") or "—"),
        ("Número de parte (P/N)", identity.get("part_number") or "—"),
        ("Dominio / grupo", identity.get("domain") or "—"),
        ("Usuario", identity.get("username") or "—"),
    ]
    bateria = identity.get("battery")
    if bateria and "error" in bateria:
        equipo_rows.append(("Batería", f"No se pudo leer ({bateria['error']})"))
    elif bateria:
        equipo_rows.append((
            "Batería",
            f"{bateria['health_pct']}% de capacidad original "
            f"({bateria['full_charge_mwh']:,} de {bateria['design_mwh']:,} mWh)".replace(",", "."),
        ))
    sistema_rows = [
        ("Sistema operativo", identity.get("os_summary") or "—"),
        ("Compilación", f"build {identity.get('os_build')}, {identity.get('os_arch')}" if identity.get("os_build") else "—"),
        ("Instalado", identity.get("install_date") or "—"),
        ("Último arranque", identity.get("last_boot") or "—"),
        ("Office", identity.get("office") or "—"),
    ]

    # Resumen condensado (CPU/RAM/disco/GPU) — el mismo que muestra el panel
    # "Componentes" en pantalla, para que el PNG exportado no obligue a
    # buscar estos datos más abajo, en las tablas detalladas.
    primer_modulo = ram_modules[0] if ram_modules else {}
    primer_disco = disks[0] if disks else {}
    primera_gpu = gpus[0] if gpus else {}
    detalle_ram = " · ".join(filter(None, [
        primer_modulo.get("type"),
        f"{primer_modulo['speed_mhz']} MHz" if primer_modulo.get("speed_mhz") else None,
    ]))
    ram_total_resumen = f"{identity.get('ram_total_gb')} GB" if identity.get("ram_total_gb") is not None else "—"
    detalle_disco = " · ".join(filter(None, [primer_disco.get("media_type"), primer_disco.get("bus_type")]))
    componentes_rows = [
        ("CPU", identity.get("cpu_name") or "—"),
        ("Memoria", f"{ram_total_resumen} ({detalle_ram})" if detalle_ram else ram_total_resumen),
        ("Almacenamiento", f"{primer_disco.get('size_label') or '—'}" + (f" ({detalle_disco})" if detalle_disco else "")),
        ("Video", primera_gpu.get("name") or "—"),
    ]

    n_lines = (
        4 + len(equipo_rows) + 2 + len(componentes_rows) + 2 + len(sistema_rows) + 2 + max(len(ram_modules), 1) + 2
        + max(len(disks), 1) + 2 + max(len(gpus), 1) + len(perifericos_rows) + 2
    )
    height = _PAD * 2 + n_lines * _LINE_H + 60
    img = Image.new("RGB", (_WIDTH, height), _BG)
    draw = ImageDraw.Draw(img)
    y = _PAD

    draw.text((_PAD, y), "DiagFix — Ficha del equipo", font=font_title, fill=_TEXT)
    y += 34
    draw.text((_PAD, y), datetime.now().strftime("%Y-%m-%d %H:%M"), font=font_small, fill=_MUTED)
    y += _LINE_H + 6
    draw.line([(_PAD, y), (_WIDTH - _PAD, y)], fill=_LINE, width=1)
    y += 14

    def section(title, rows):
        nonlocal y
        draw.text((_PAD, y), title.upper(), font=font_header, fill=_TEXT)
        y += _LINE_H
        for label, value in rows:
            draw.text((_PAD + 8, y), label, font=font_small, fill=_MUTED)
            draw.text((_PAD + 230, y), str(value), font=font, fill=_TEXT)
            y += _LINE_H
        y += 8
        draw.line([(_PAD, y), (_WIDTH - _PAD, y)], fill=_LINE, width=1)
        y += 14

    section("Equipo", equipo_rows)
    section("Componentes", componentes_rows)
    section("Sistema operativo", sistema_rows)

    ram_total = identity.get("ram_total_gb", "?")
    ram_used = identity.get("ram_slots_used", "?")
    ram_total_slots = identity.get("ram_slots_total", "?")
    draw.text((_PAD, y), f"MEMORIA RAM — {ram_total} GB total, {ram_used}/{ram_total_slots} slots", font=font_header, fill=_TEXT)
    y += _LINE_H
    if ram_modules:
        for m in ram_modules:
            line = (
                f"{m.get('slot') or '?'}: {m.get('manufacturer') or '?'} · {m.get('capacity_gb') or '?'} GB · "
                f"{m.get('type') or '?'} · {m.get('speed_mhz') or '?'} MHz"
                + (f" · {m.get('part_number')}" if m.get("part_number") else "")
            )
            draw.text((_PAD + 8, y), line, font=font_small, fill=_TEXT)
            y += _LINE_H
    else:
        draw.text((_PAD + 8, y), "Sin datos disponibles.", font=font_small, fill=_MUTED)
        y += _LINE_H
    y += 8
    draw.line([(_PAD, y), (_WIDTH - _PAD, y)], fill=_LINE, width=1)
    y += 14

    draw.text((_PAD, y), "ALMACENAMIENTO", font=font_header, fill=_TEXT)
    y += _LINE_H
    if disks:
        for d in disks:
            line = (
                f"{d.get('name') or '?'}  ·  {d.get('size_label') or '?'}  ·  {d.get('media_type') or '?'}"
                + (f"  ·  {d.get('bus_type')}" if d.get("bus_type") else "")
            )
            draw.text((_PAD + 8, y), line, font=font_small, fill=_TEXT)
            y += _LINE_H
    else:
        draw.text((_PAD + 8, y), "Sin datos disponibles.", font=font_small, fill=_MUTED)
        y += _LINE_H
    y += 8
    draw.line([(_PAD, y), (_WIDTH - _PAD, y)], fill=_LINE, width=1)
    y += 14

    draw.text((_PAD, y), "VIDEO Y PERIFÉRICOS", font=font_header, fill=_TEXT)
    y += _LINE_H
    if gpus:
        for g in gpus:
            line = (
                f"{g.get('name') or '?'}"
                + (f"  ·  driver {g.get('driver_version')}" if g.get("driver_version") else "")
                + (f" ({g.get('driver_date')})" if g.get("driver_date") else "")
                + (f"  ·  {g.get('vram_mb')} MB VRAM" if g.get("vram_mb") else "")
            )
            draw.text((_PAD + 8, y), line, font=font_small, fill=_TEXT)
            y += _LINE_H
    else:
        draw.text((_PAD + 8, y), "Sin datos disponibles.", font=font_small, fill=_MUTED)
        y += _LINE_H
    for label, value in perifericos_rows:
        draw.text((_PAD + 8, y), f"{label}: {value if value is not None else '—'}", font=font_small, fill=_TEXT)
        y += _LINE_H

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
