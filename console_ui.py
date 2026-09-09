"""Modo consola de DiagFix — para usarlo sin navegador.

El caso de uso real: un PC recién formateado, todavía en el OOBE de Windows
(Shift+F10 abre un CMD). Ahí no hay navegador ni sesión de usuario, así que
el modo web no sirve. Este módulo expone el mismo motor de chequeos con un
menú de texto que funciona en cualquier consola de Windows, incluido WinPE.

Se activa con `DiagFix.exe --console`, o solo cuando se detecta que no hay
navegador disponible (típico en WinPE/OOBE).
"""
import ctypes
import os
import platform
import sys
import webbrowser
from datetime import datetime
from pathlib import Path


def setup_console_encoding():
    """La consola de Windows usa cp850/cp437 pero Python escribe en cp1252 —
    los acentos salen rotos o levantan UnicodeEncodeError. Se fuerza UTF-8 en
    ambos lados, con `errors='replace'` como red de seguridad."""
    if platform.system() == "Windows":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def is_winpe():
    """WinPE deja esta llave en el registro; sirve para detectar que estamos
    en un entorno de instalación/recuperación y no en un Windows normal."""
    if platform.system() != "Windows":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\MiniNT"):
            return True
    except Exception:
        return False


def browser_available():
    try:
        webbrowser.get()
        return True
    except Exception:
        return False


def should_use_console(argv):
    """Decide si arrancar en modo consola: por flag explícito, o porque el
    entorno no puede abrir un navegador (WinPE/OOBE)."""
    if "--console" in argv or "-c" in argv:
        return True
    if "--web" in argv:
        return False
    return is_winpe() or not browser_available()


# ---------------------------------------------------------------- utilidades

_STATUS_TAG = {
    "ok": "[ OK ]",
    "warning": "[ !! ]",
    "critical": "[CRIT]",
    "error": "[ERR ]",
    "unknown": "[ -- ]",
}


def _hr(char="-", width=78):
    print(char * width)


def _title(text):
    print()
    _hr("=")
    print(f" {text}")
    _hr("=")


def _pause():
    try:
        input("\nEnter para volver al menú...")
    except (EOFError, KeyboardInterrupt):
        pass


def _ask(prompt, default=""):
    try:
        value = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return value or default


def _ask_yes_no(prompt, default=False):
    suffix = " [s/N]: " if not default else " [S/n]: "
    value = _ask(prompt + suffix).lower()
    if not value:
        return default
    return value.startswith("s") or value.startswith("y")


# ------------------------------------------------------------------ acciones


def show_identity():
    from checks import system_info

    _title("Ficha del equipo")
    data = system_info.get_identity()
    if not data.get("available"):
        print(" No disponible:", data.get("reason"))
        return
    rows = [
        ("Equipo", " ".join(filter(None, [data.get("manufacturer"), data.get("model")]))),
        ("Número de serie", data.get("serial")),
        ("Número de parte", data.get("part_number")),
        ("Sistema operativo", data.get("os_summary")),
        ("Compilación", f"build {data.get('os_build')}, {data.get('os_arch')}" if data.get("os_build") else None),
        ("Instalado", data.get("install_date")),
        ("Último arranque", data.get("last_boot")),
        ("Dominio / grupo", data.get("domain")),
        ("Usuario", data.get("username")),
        ("RAM", f"{data.get('ram_total_gb')} GB ({data.get('ram_slots_used')}/{data.get('ram_slots_total')} slots)"
            if data.get("ram_total_gb") is not None else None),
        ("Discos", ", ".join(data.get("disks") or []) or None),
        ("Office", data.get("office")),
    ]
    for label, value in rows:
        print(f" {label:<20} {value if value else '—'}")

    modules = data.get("ram_modules") or []
    if modules:
        print("\n Módulos de RAM:")
        for m in modules:
            parts = [m.get("manufacturer"), f"{m['capacity_gb']}GB" if m.get("capacity_gb") is not None else None,
                     m.get("type"), f"{m['speed_mhz']}MHz" if m.get("speed_mhz") else None]
            desc = " ".join(p for p in parts if p)
            pn = f" ({m['part_number']})" if m.get("part_number") else ""
            print(f"   {m.get('slot', '?'):<10} {desc}{pn}")


def run_full_scan():
    from concurrent.futures import ThreadPoolExecutor
    from checks import correlate, hardware, network, software

    _title("Diagnóstico completo")
    print(" Ejecutando chequeos (unos segundos)...\n")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            "Red": pool.submit(network.run_all),
            "Hardware": pool.submit(hardware.run_all),
            "Software y sistema": pool.submit(software.run_all),
        }
        categories = {name: f.result() for name, f in futures.items()}

    all_checks = [c for checks in categories.values() for c in checks]
    known = [c for c in all_checks if c["status"] in ("ok", "warning", "critical")]
    score_map = {"ok": 100, "warning": 60, "critical": 0}
    score = round(sum(score_map[c["status"]] for c in known) / len(known)) if known else 0

    for name, checks in categories.items():
        print(f"\n {name}")
        _hr()
        for c in checks:
            tag = _STATUS_TAG.get(c["status"], "[ ?? ]")
            print(f" {tag} {c['name']:<45} {c.get('value') or '—'}")
            if c["status"] not in ("ok",) and c.get("message"):
                print(f"        {c['message']}")

    print()
    _hr("=")
    print(f" PUNTAJE: {score}/100     Cobertura: {len(known)}/{len(all_checks)} chequeos")
    _hr("=")

    insights = correlate.correlate(all_checks)
    if insights:
        print("\n DIAGNÓSTICO PROBABLE")
        _hr()
        for i in insights:
            print(f" [{i['severity'].upper()}] {i['title']}")
            print(f"        {i['message']}")

    return {"score": score, "categories": categories, "insights": insights, "all_checks": all_checks}


def show_disks():
    from checks import disks as disks_mod

    _title("Discos y particiones")
    data = disks_mod.list_disks()
    if not data.get("available"):
        print(" No disponible:", data.get("message"))
        return
    for d in data["disks"]:
        flag = "  << DISCO DEL SISTEMA (protegido)" if d.get("isSystemDisk") else ""
        print(f"\n Disco {d['number']}: {d.get('friendlyName') or 'sin nombre'}{flag}")
        print(f"   {d['sizeGB']} GB · {d.get('partitionStyle')} · {d.get('busType')} · Salud: {d.get('healthStatus')}")
        for p in d.get("partitions") or []:
            letter = f"{p['driveLetter']}:" if p.get("driveLetter") else "—"
            label = p.get("label") or ""
            free = f"{p['freeGB']} GB libres" if p.get("freeGB") is not None else ""
            print(f"     Parte {p['partNumber']:<3} {letter:<4} {label:<18} {p.get('filesystem') or '—':<7} "
                  f"{p['sizeGB']:>8} GB  {free}")


def run_quick_action():
    from checks import actions

    _title("Acciones rápidas")
    catalog = actions.list_actions()
    for idx, a in enumerate(catalog, 1):
        print(f" {idx:>2}. {a['label']}")
        print(f"     {a['description']}")
    print("  0. Volver")

    choice = _ask("\n Acción: ")
    if not choice.isdigit() or int(choice) == 0:
        return
    idx = int(choice)
    if not 1 <= idx <= len(catalog):
        print(" Opción inválida.")
        return
    action = catalog[idx - 1]
    if not _ask_yes_no(f"\n ¿Ejecutar \"{action['label']}\" en este equipo?"):
        print(" Cancelado.")
        return
    print(" Ejecutando...")
    result = actions.run_action(action["id"])
    print(f"\n Resultado: {result.get('status')}")
    print(f" {result.get('output', '')[:1500]}")


def create_restore_point():
    from checks import restore

    _title("Punto de restauración")
    if not _ask_yes_no(" ¿Crear un punto de restauración ahora?", default=True):
        print(" Cancelado.")
        return
    print(" Creando (puede tardar un minuto)...")
    try:
        result = restore.create_restore_point("DiagFix - consola")
        print(f" {result['message']}")
    except restore.RestoreError as exc:
        print(f" No se pudo crear: {exc}")


def run_script_menu():
    from checks import scripts_runner

    _title("Zona de scripts")
    scripts = scripts_runner.list_scripts()
    if not scripts:
        print(f" No hay scripts en: {scripts_runner.SCRIPTS_DIR}")
        print(" Copiá ahí tus .ps1/.bat/.cmd y volvé a entrar a este menú.")
        return
    for idx, s in enumerate(scripts, 1):
        print(f" {idx:>2}. {s['name']:<35} {s['size_kb']} KB   {s['modified']}")
    print("  0. Volver")

    choice = _ask("\n Script: ")
    if not choice.isdigit() or int(choice) == 0:
        return
    idx = int(choice)
    if not 1 <= idx <= len(scripts):
        print(" Opción inválida.")
        return
    name = scripts[idx - 1]["name"]
    if not _ask_yes_no(f"\n ¿Ejecutar \"{name}\" en este equipo?"):
        print(" Cancelado.")
        return
    print(" Ejecutando...")
    try:
        result = scripts_runner.run_script(name)
        print(f"\n Resultado: {result['status']}")
        print(f" {result['output'][:2000]}")
    except scripts_runner.ScriptError as exc:
        print(f" Error: {exc}")


def save_report(scan):
    if not scan:
        print(" Primero ejecutá un diagnóstico (opción 1).")
        return
    from checks import system_info

    identity = system_info.get_identity()
    serial = identity.get("serial") or "equipo"
    out_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    filename = out_dir / f"diagfix-{serial}-{datetime.now():%Y%m%d-%H%M%S}.txt"

    lines = [
        "DiagFix — Reporte de diagnóstico",
        f"Fecha: {datetime.now():%Y-%m-%d %H:%M}",
        f"Equipo: {' '.join(filter(None, [identity.get('manufacturer'), identity.get('model')]))}",
        f"Serie: {identity.get('serial')}   Parte: {identity.get('part_number')}",
        f"SO: {identity.get('os_summary')}",
        "",
        f"PUNTAJE: {scan['score']}/100",
        "",
    ]
    for name, checks in scan["categories"].items():
        lines.append(f"== {name} ==")
        for c in checks:
            tag = _STATUS_TAG.get(c["status"], "[ ?? ]")
            lines.append(f"{tag} {c['name']}: {c.get('value') or '—'}")
            if c.get("message"):
                lines.append(f"      {c['message']}")
            for cause in c.get("causes") or []:
                lines.append(f"      - causa: {cause}")
            for step in c.get("fix") or []:
                lines.append(f"      > {step}")
        lines.append("")
    if scan.get("insights"):
        lines.append("== Diagnóstico probable ==")
        for i in scan["insights"]:
            lines.append(f"[{i['severity'].upper()}] {i['title']}")
            lines.append(f"      {i['message']}")

    try:
        filename.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n Reporte guardado en:\n   {filename}")
    except OSError as exc:
        print(f" No se pudo guardar el reporte: {exc}")


# --------------------------------------------------------------------- menú


MENU = """
  1. Diagnóstico completo
  2. Ficha del equipo
  3. Discos y particiones
  4. Acciones rápidas
  5. Guardar reporte (.txt)
  6. Crear punto de restauración
  7. Zona de scripts
  0. Salir
"""


def main_loop():
    setup_console_encoding()
    is_admin = False
    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        pass

    _title("DiagFix — modo consola")
    print(f" Equipo: {platform.node()}   |   {platform.platform()}")
    print(f" Permisos: {'ADMINISTRADOR' if is_admin else 'usuario normal (algunos chequeos no estarán disponibles)'}")
    if is_winpe():
        print(" Entorno: WinPE / instalación de Windows detectado")

    last_scan = None
    while True:
        print(MENU)
        choice = _ask(" Opción: ")
        if choice == "0":
            print(" Saliendo.")
            return
        elif choice == "1":
            last_scan = run_full_scan()
            _pause()
        elif choice == "2":
            show_identity()
            _pause()
        elif choice == "3":
            show_disks()
            _pause()
        elif choice == "4":
            run_quick_action()
            _pause()
        elif choice == "5":
            save_report(last_scan)
            _pause()
        elif choice == "6":
            create_restore_point()
            _pause()
        elif choice == "7":
            run_script_menu()
            _pause()
        elif choice == "":
            continue
        else:
            print(" Opción inválida.")


if __name__ == "__main__":
    main_loop()
