# DiagFix

Panel local de diagnóstico técnico para soporte en Windows. En vez de tirar el
output crudo de `ping`, `wmic`, `Get-WinEvent`, etc., cada chequeo se
interpreta con umbrales de severidad y devuelve un veredicto (`ok` /
`warning` / `critical`) más causas y una solución concreta — muchas con un
botón para aplicarla directamente. Además identifica el equipo, correlaciona
señales para sugerir una causa raíz probable, permite monitorear en vivo por
60 segundos y guarda un historial por equipo entre visitas.

## Requisitos

- Windows 10 u 11 (los chequeos de disco, registro de eventos, batería,
  BitLocker, etc. usan comandos y registro propios de Windows).
- Python 3.9 o superior.
- Recomendado: ejecutar la terminal **como administrador**. Varios chequeos
  (S.M.A.R.T., batería, Visor de Eventos, `sfc`, algunas acciones) muestran
  "no disponible" sin permisos elevados, pero el resto del panel funciona
  igual sin admin.

## Instalación

```bash
cd diagfix
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```bash
python app.py
```

Se abre automáticamente `http://127.0.0.1:8000` en el navegador con el
dashboard. El botón **Ejecutar diagnóstico** corre los 21 chequeos rápidos
(unos segundos, corren en paralelo). **Verificación profunda (sfc)** ejecuta
`sfc /scannow`, que puede tardar varios minutos — úsalo solo cuando ya
sospechas de archivos de sistema corruptos.

## Qué revisa

| Categoría | Chequeos |
|---|---|
| Red | Internet, DNS, puerta de enlace, señal Wi-Fi, DNS/proxy/VPN configurados |
| Hardware | Espacio en disco, salud operacional y S.M.A.R.T. real del disco, RAM, CPU, salud de batería |
| Software | Reinicio pendiente, últimas actualizaciones, errores del Visor de Eventos, programas de inicio, pantallazos azules/reinicios inesperados, Defender + Firewall, dispositivos con error, servicio de impresión, BitLocker, sincronización de hora |

El puntaje general es un promedio ponderado sobre los chequeos que sí
pudieron completarse (`ok`=100, `warning`=60, `critical`=0); los que dan
`unknown`/`error` (típicamente por falta de permisos de administrador) no
penalizan el puntaje y se muestran aparte como "cobertura" (ej. `18/21`).

## Identificación del equipo

Arriba del puntaje se muestra marca, modelo, número de serie, edición y
build de Windows, activación, RAM (con slots usados/libres) y discos
instalados — la info de referencia que se pide en cualquier ticket.

## Diagnóstico probable (correlación)

Cuando varias señales apuntan a la misma causa (ej. disco con S.M.A.R.T.
crítico, o batería desgastada + apagones inesperados detectados), aparece
una sección "Diagnóstico probable" arriba del detalle por categoría. La
lógica vive en `checks/correlate.py` y solo combina chequeos ya calculados
— no vuelve a ejecutar nada.

## Acciones rápidas

Panel de acciones seguras (limpiar DNS, renovar IP, reiniciar Winsock,
reiniciar cola de impresión, limpiar temporales, reiniciar Explorer, DISM
`/RestoreHealth`) disponibles en cualquier momento, y también como botón
"Aplicar solución" contextual en los chequeos donde la acción es la
solución directa. Cada una pide confirmación y queda registrada en
`actions_log.jsonl` (timestamp, acción, resultado). La whitelist vive en
`checks/actions.py` — el cliente nunca puede mandar un comando arbitrario,
solo un `action_id` validado contra esa lista.

## Monitoreo en vivo (60s)

Para fallas intermitentes que un escaneo puntual no capta: un tick cada 2
segundos durante 60s con CPU, RAM, throughput de red, latencia y el top 5 de
procesos por uso de CPU. Se transmite por Server-Sent Events desde
`/api/monitor/stream`.

## Historial por equipo

Antes o después de escanear se pueden completar los campos **Cliente**,
**Ticket** y **Técnico** y guardar el escaneo con el botón "Guardar en
historial" — queda en `diagfix_history.db` (SQLite, sin dependencias
nuevas), indexado por el número de serie del equipo. "Ver historial de este
equipo" muestra los escaneos previos de esa misma máquina para comparar
entre visitas. El reporte también se puede exportar como `.html` (con esos
mismos datos de cliente/ticket/técnico) además del `.txt` original.

## Gestor de discos

Pestaña **Herramientas** → "Cargar discos": lista discos y particiones
reales (cada partición se numera "Parte N"), con formatear, crear
partición, eliminar partición, renombrar volumen y cambiar letra de unidad.

**El disco del sistema (donde está Windows) nunca se puede tocar** — se
calcula en el servidor en el momento de cada acción (no una vez cacheada) y
se rechaza cualquier operación destructiva sobre él, incluso llamando a la
API directamente sin pasar por la interfaz. En la UI ese disco aparece con
un badge "protegido" y sin ningún botón de acción. Formatear y eliminar
particiones además piden escribir una frase exacta (ej. `FORMATEAR DISCO 0
PARTE 1`) antes de habilitar el botón de confirmar.

## Selector de técnico

Pestaña **Visita e historial**: lista editable de técnicos (guardada en
`diagfix_settings.json`, local a esta instalación) que recuerda el último
usado. Cliente y Ticket siguen siendo texto libre.

## Guardar reporte en la red (NAS)

También en **Visita e historial**. DiagFix **nunca guarda tu contraseña de
red** — "Guardar credenciales de red (Windows)" la pasa una única vez al
Administrador de credenciales de Windows (`cmdkey`) y la descarta; de ahí en
más, Windows resuelve el acceso a la ruta UNC configurada sin que DiagFix
vuelva a manejarla. "Guardar reporte en la red" sube el mismo HTML del
botón de exportar a esa ruta.

## Pestañas y filtro

El dashboard está organizado con una **barra lateral** (Diagnóstico /
Herramientas / Visita e historial) en vez de pestañas arriba. La tarjeta de
identidad arranca colapsada (solo una línea resumen; "Ver detalle" para el
resto), y arriba de los resultados hay un checkbox **"Mostrar solo
problemas"** que oculta los chequeos en `ok` para que salten a la vista los
que sí necesitan atención (se recuerda entre sesiones vía `localStorage`).

## Modo consola (sin navegador)

```
DiagFix.exe --console
```

Pensado para un equipo recién formateado: en el instalador de Windows,
Shift+F10 abre un CMD sin navegador ni sesión de usuario. Ahí `--console`
levanta un menú de texto con el mismo motor de chequeos (diagnóstico
completo, ficha del equipo, discos, acciones rápidas, guardar reporte,
punto de restauración, zona de scripts). Se activa solo también cuando se
detecta WinPE o no hay navegador disponible, sin necesidad del flag.

## Punto de restauración y zona de scripts

Botón "Crear punto de restauración" (junto a Acciones rápidas) antes de
tocar cualquier cosa — requiere admin y que la Protección del sistema esté
activada en `C:`. La "Zona de scripts" lista y ejecuta `.ps1`/`.bat`/`.cmd`
que vos mismo copies a la carpeta `scripts/` junto al programa, con
confirmación y sin poder escapar de esa carpeta (bloqueo de path traversal).
DiagFix no revisa el contenido de tus scripts — son tu responsabilidad.

## Ficha de hardware profunda

La tarjeta de identidad incluye por módulo de RAM: fabricante, número de
parte, velocidad y tipo (DDR3/4/5) vía SPD, y los discos se muestran con su
**tamaño comercial** ("500 GB" en vez de "465.7 GB" — los fabricantes
marketean en GB decimal, Windows por defecto calcula en binario) y si hay
Microsoft Office instalado.

## Inventario y reporte PNG

Cada vez que guardás un escaneo en el historial, también se agrega una fila
a `diagfix_inventory.csv` (equipo, serie, part number, SO, score, cliente,
ticket, técnico) — una planilla acumulada de todas las visitas, descargable
desde "Visita e historial" → "Descargar CSV". El reporte también se puede
exportar como imagen `.png` (para pegar directo en un chat/ticket sin
adjuntar un archivo aparte), renderizada con Pillow.

## Mantenimiento de Windows

- **Reparar Windows Update** y **Actualizar todas las apps (winget)**: dos
  acciones más en el panel de Acciones rápidas.
- **Programas de inicio** (pestaña Herramientas): a diferencia del chequeo
  que solo cuenta, esto lista cada programa con su comando real y permite
  deshabilitarlo/habilitarlo — reversible, sin borrar nada (renombra la
  entrada de registro o el acceso directo en vez de eliminarlo).
- **Backup de drivers**: exporta los drivers de terceros instalados a una
  carpeta antes de formatear (`Export-WindowsDriver`, requiere admin).

## Estructura del proyecto

```
diagfix/
  app.py                  # FastAPI: endpoints y orquestación, sirve el dashboard
  console_ui.py            # modo consola (--console / WinPE, sin navegador)
  checks/
    base.py               # ejecución de comandos/PowerShell con encoding correcto,
                           # + commercial_size_label (GB decimal vs binario)
    network.py            # internet, DNS, gateway, wifi, DNS/proxy/VPN
    hardware.py           # disco, S.M.A.R.T., RAM, CPU, batería
    software.py           # reinicio pendiente, updates, event log, BSOD,
                           # Defender/Firewall, dispositivos, spooler, BitLocker,
                           # hora, sfc
    system_info.py        # identificación del equipo: marca/modelo/serie/part
                           # number/RAM por módulo (SPD)/discos/Office
    actions.py            # whitelist de acciones aplicables + log de auditoría
    monitor.py            # monitoreo en vivo (SSE) de 60s
    correlate.py          # correlación de causa raíz entre chequeos
    history.py            # historial de escaneos en SQLite
    disks.py              # listado de discos/particiones + bloqueo del disco del sistema
    disk_actions.py        # formatear/crear/eliminar partición, renombrar, cambiar letra
    settings.py            # técnicos y ruta de red (diagfix_settings.json)
    nas.py                 # credenciales (cmdkey) y subida de reportes a la red
    restore.py              # punto de restauración del sistema
    scripts_runner.py        # ejecutor de scripts de la carpeta scripts/
    inventory.py             # inventario acumulado en CSV
    report_image.py          # reporte renderizado como PNG (Pillow)
    maintenance.py           # backup de drivers
    startup_manager.py       # gestor de inicio real (listar/deshabilitar/habilitar)
  static/
    index.html
    style.css
    script.js
  scripts/                 # tus propios .ps1/.bat/.cmd (no versionar)
  requirements.txt
  actions_log.jsonl        # se crea al usar una acción (no versionar)
  diagfix_history.db       # se crea al guardar el primer escaneo (no versionar)
  diagfix_settings.json    # se crea al agregar un técnico o ruta de red (no versionar)
  diagfix_inventory.csv    # se crea al guardar el primer escaneo (no versionar)
```

## Cómo agregar un nuevo chequeo (para hacerlo más preciso con el tiempo)

1. En el módulo que corresponda (`checks/network.py`, `hardware.py` o
   `software.py`), agrega una función que devuelva (usando el helper
   `result` de `checks/base.py`):
   ```python
   result(name, status, value, message, causes=[...], fix=[...], action_id="...")
   ```
   `status` es `ok|warning|critical|unknown|error`; `action_id` es opcional
   y debe existir en `checks/actions.py` si se usa.
2. Agrégala a la lista que retorna `run_all()` de ese módulo.
3. Si el chequeo ejecuta un comando de consola (no PowerShell), usa
   `checks.base.run` en vez de `subprocess` directo — decodifica UTF-8,
   UTF-16LE y code pages de consola automáticamente. Para PowerShell, usa
   `checks.base.run_powershell`, que ya fuerza salida UTF-8.
4. Ajusta los umbrales según lo que veas en terreno.
5. Si el chequeo puede combinarse con otro para sugerir una causa raíz más
   específica, agrega una regla en `checks/correlate.py`.

## Empaquetar como .exe

```bash
pyinstaller --onefile --name DiagFix --add-data "static;static" app.py
```

El `.exe` queda en `dist/DiagFix.exe`, no necesita Python instalado.
**Reconstruilo cada vez que cambie algo en `checks/` o `static/`** — un
`.exe` viejo no incluye módulos nuevos como el gestor de discos.

## Próximos pasos posibles

- Temperaturas de CPU/GPU (requiere una librería de terceros tipo
  LibreHardwareMonitor, Windows no expone esto de forma nativa y confiable).
- Suite de tests con `pytest` mockeando `subprocess.run` para cubrir el
  parseo de cada chequeo sin depender del equipo donde se ejecuta.
