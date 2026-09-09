# DiagFix

Panel local de diagnóstico y soporte técnico para Windows. Se corre en el
equipo de un cliente, escanea red/hardware/software en unos segundos e
interpreta cada resultado con un veredicto (`ok` / `warning` / `critical`)
más causas probables y una solución concreta — muchas con un botón para
aplicarla directamente ahí mismo, sin salir de la app ni copiar comandos a
mano.

No es un visor de logs crudos: en vez de mostrarte el output de `ping`,
`wmic` o `Get-WinEvent`, cada chequeo ya viene traducido a algo que se
entiende de un vistazo, para que en una visita de soporte se vaya directo a
lo que importa.

## Qué hace, en corto

- **Diagnostica** 24+ chequeos de red, hardware y software con severidad,
  causas y solución sugerida.
- **Identifica el equipo**: marca, modelo, número de serie, RAM por módulo,
  discos, GPU, monitores/USB/impresoras, activación de Windows, y si el
  equipo es apto para actualizar a Windows 11.
- **Corrige con un clic**: 24 acciones (limpiar DNS, reparar Windows Update,
  vaciar cola de impresión, sincronizar hora, etc.), cada una con
  confirmación y registro de auditoría.
- **Prueba hardware a mano**: teclado, mouse, parlantes, micrófono, cámara y
  píxeles muertos de pantalla — para lo que ninguna consulta de Windows
  puede confirmar por sí sola.
- **Administra discos**: formatear, crear/eliminar particiones, con el disco
  del sistema bloqueado a nivel de servidor (no solo en la interfaz).
- **Monitorea en vivo** 60 segundos para fallas intermitentes que un
  escaneo puntual no capta.
- **Guarda historial** por equipo (SQLite + CSV acumulado) para comparar
  entre visitas, con reportes exportables a `.html`/`.png`.
- **Se actualiza sola** desde este mismo repo de GitHub, sin depender de que
  alguien te pase el `.exe` a mano cada vez.
- **Arranca sin navegador visible**: abre en una ventana propia (modo app),
  y también tiene un modo consola de puro texto para usar durante la
  instalación de Windows (Shift+F10), antes de que exista un navegador.

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

Se abre automáticamente el dashboard en una ventana propia (ver
[Cómo abre la app](#cómo-abre-la-app)). El botón **Ejecutar diagnóstico**
corre los chequeos rápidos (unos segundos, corren en paralelo).
**Verificación profunda (sfc)** ejecuta `sfc /scannow`, que puede tardar
varios minutos — úsalo solo cuando ya sospechas de archivos de sistema
corruptos.

## Qué revisa

| Categoría | Chequeos |
|---|---|
| Red | Internet, DNS, puerta de enlace, señal Wi-Fi, DNS/proxy/VPN configurados, velocidad del enlace (cable/Wi-Fi), perfil de red (Público/Privado), velocidad de descarga real |
| Hardware | Espacio en disco, salud operacional y S.M.A.R.T. real del disco, RAM, CPU, salud de batería |
| Software | Reinicio pendiente, últimas actualizaciones, errores del Visor de Eventos, programas de inicio, pantallazos azules/reinicios inesperados, Defender + Firewall, dispositivos con error (con el motivo real, no solo el nombre), servicio de impresión, BitLocker, sincronización de hora |

El puntaje general es un promedio ponderado sobre los chequeos que sí
pudieron completarse (`ok`=100, `warning`=60, `critical`=0); los que dan
`unknown`/`error` (típicamente por falta de permisos de administrador) no
penalizan el puntaje y se muestran aparte como "cobertura" (ej. `21/24`).

## Identificación del equipo

Pestaña propia **Especificaciones**: marca, modelo, número de serie y de
parte, dominio/usuario, edición y build de Windows, activación, RAM por
módulo (fabricante, velocidad, tipo DDR3/4/5 vía SPD), discos con tamaño
comercial, GPU con driver, y monitores/USB/impresoras conectados. Se puede
exportar como imagen `.png` para pegar directo en un ticket. También se
guarda automáticamente una copia local en `reportes/` cada vez que se
guarda un escaneo — de respaldo, sin depender de que el equipo esté en un
dominio con acceso a la red de la empresa.

La misma pestaña muestra si el equipo **cumple los requisitos para
Windows 11** (TPM 2.0, Secure Boot, UEFI, RAM, disco, CPU soportada) con un
veredicto único.

## Diagnóstico probable (correlación)

Cuando varias señales apuntan a la misma causa (ej. disco con S.M.A.R.T.
crítico, o batería desgastada + apagones inesperados detectados), aparece
una sección "Diagnóstico probable" arriba del detalle por categoría. La
lógica vive en `checks/correlate.py` y solo combina chequeos ya calculados
— no vuelve a ejecutar nada.

## Acciones rápidas

24 acciones seguras (limpiar DNS, renovar IP, reiniciar Winsock, vaciar cola
de impresión, sincronizar hora, reparar Windows Update, actualizar firmas
de Defender, limpiar temporales, optimizar disco, reconstruir caché de
íconos, entre otras) disponibles en cualquier momento, y también como botón
"Aplicar solución" contextual en los chequeos donde la acción es la
solución directa. Cada una pide confirmación y queda registrada en
`actions_log.jsonl` (timestamp, acción, resultado). La whitelist vive en
`checks/actions.py` — el cliente nunca puede mandar un comando arbitrario,
solo un `action_id` validado contra esa lista.

**Apagar / Reiniciar** el equipo están en la barra superior (no en la lista
de acciones), accesibles desde cualquier pestaña — pensado para cuando solo
hace falta revisar y apagar, sin tener que navegar la interfaz.

## Pruebas manuales de hardware

Pestaña **Pruebas**: no hay forma de confirmar por WMI que un teclado, un
mouse, los parlantes, el micrófono o la cámara realmente funcionan — hay que
probarlos. Esta sección da un lugar ordenado para hacerlo sin salir de la
app:

- **Teclado**: layout ISO latinoamericano (con Ñ), cada tecla se marca al
  presionarla.
- **Mouse**: clic izquierdo/derecho/medio y rueda arriba/abajo.
- **Parlantes**: tono de prueba por canal (izquierdo/derecho/ambos).
- **Micrófono**: medidor de nivel en vivo.
- **Cámara**: vista previa en vivo.
- **Pantalla**: colores sólidos a pantalla completa para detectar píxeles
  muertos.

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

Pestaña **Discos**: lista discos y particiones reales (cada partición se
numera "Parte N"), con formatear, crear partición, eliminar partición,
renombrar volumen y cambiar letra de unidad.

**El disco del sistema (donde está Windows) nunca se puede tocar** — se
calcula en el servidor en el momento de cada acción (no una vez cacheada) y
se rechaza cualquier operación destructiva sobre él, incluso llamando a la
API directamente sin pasar por la interfaz. En la UI ese disco aparece con
un badge "protegido" y sin ningún botón de acción. Formatear y eliminar
particiones además piden escribir una frase exacta (ej. `FORMATEAR DISCO 0
PARTE 1`) antes de habilitar el botón de confirmar.

## Selector de técnico

Pestaña **Reportes**: lista editable de técnicos (guardada en
`diagfix_settings.json`, local a esta instalación) que recuerda el último
usado. Cliente y Ticket siguen siendo texto libre.

## Guardar reporte en la red (NAS)

También en **Reportes**. DiagFix **nunca guarda tu contraseña de red** —
"Guardar credenciales de red (Windows)" la pasa una única vez al
Administrador de credenciales de Windows (`cmdkey`) y la descarta; de ahí en
más, Windows resuelve el acceso a la ruta UNC configurada sin que DiagFix
vuelva a manejarla. "Guardar reporte en la red" sube el mismo HTML del
botón de exportar a esa ruta.

## Interfaz

Barra lateral con 3 grupos (Diagnóstico / Herramientas / Registro) en vez
de todo apilado en una sola pantalla. Arriba de los resultados hay un
checkbox **"Mostrar solo problemas"** que oculta los chequeos en `ok` para
que salten a la vista los que sí necesitan atención (se recuerda entre
sesiones vía `localStorage`). Discos y Monitoreo se cargan/arrancan solos
al entrar a esa pestaña.

## Cómo abre la app

Al arrancar, DiagFix abre una **ventana propia** de Edge en modo `--app`
(sin barra de direcciones ni pestañas — se ve como una aplicación, no como
"alguien abrió el navegador"), con flags que evitan el asistente de
bienvenida/términos que Edge muestra la primera vez que se usa en una
cuenta de Windows. Si no encuentra un navegador Chromium instalado, cae al
navegador predeterminado normal.

### Modo consola (sin navegador)

```
DiagFix.exe --console
```

Pensado para un equipo recién formateado: en el instalador de Windows,
Shift+F10 abre un CMD sin navegador ni sesión de usuario. Ahí `--console`
levanta un menú de texto con el mismo motor de chequeos (diagnóstico
completo, ficha del equipo, discos, acciones rápidas, guardar reporte,
punto de restauración, zona de scripts). Se activa solo también cuando se
detecta WinPE o no hay navegador disponible, sin necesidad del flag.

## Actualizaciones

Pestaña **Sistema** → "Buscar actualizaciones": consulta el último release
de este mismo repo en GitHub y, si hay uno más nuevo, lo descarga e instala
solo (un script espera a que el proceso actual cierre, reemplaza el `.exe`
y vuelve a abrir la app). Solo funciona en el `.exe` empaquetado, no en
`python app.py`. Ver [Publicar una actualización](#publicar-una-actualización)
para el lado de quien mantiene el proyecto.

## Punto de restauración y zona de scripts

Botón "Crear punto de restauración" (pestaña Acciones) antes de tocar
cualquier cosa — requiere admin y que la Protección del sistema esté
activada en `C:`. La "Zona de scripts" (pestaña Scripts) lista y ejecuta
`.ps1`/`.bat`/`.cmd` que vos mismo copies a la carpeta `scripts/` junto al
programa, con confirmación y sin poder escapar de esa carpeta (bloqueo de
path traversal). DiagFix no revisa el contenido de tus scripts — son tu
responsabilidad.

## Inventario y reporte PNG

Cada vez que guardás un escaneo en el historial, también se agrega una fila
a `diagfix_inventory.csv` (equipo, serie, part number, SO, score, cliente,
ticket, técnico) — una planilla acumulada de todas las visitas, descargable
desde la pestaña Inventario. El reporte también se puede exportar como
imagen `.png` (para pegar directo en un chat/ticket sin adjuntar un archivo
aparte), renderizada con Pillow.

## Mantenimiento de Windows

- **Reparar Windows Update**, **Actualizar todas las apps (winget)**,
  **limpiar componentes viejos**, entre otras acciones del panel de
  Acciones rápidas.
- **Programas de inicio** (pestaña Sistema): a diferencia del chequeo que
  solo cuenta, esto lista cada programa con su comando real y permite
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
                           # ps_quote (interpolación segura), commercial_size_label
    network.py            # internet, DNS, gateway, wifi, DNS/proxy/VPN, velocidad
    hardware.py            # disco, S.M.A.R.T., RAM, CPU, batería
    software.py            # reinicio pendiente, updates, event log, BSOD,
                           # Defender/Firewall, dispositivos (con motivo), spooler,
                           # BitLocker, hora, sfc
    system_info.py        # identificación del equipo: marca/modelo/serie/part
                           # number/RAM por módulo (SPD)/discos/GPU/periféricos/Office
    win11_readiness.py     # requisitos de Windows 11 (TPM, Secure Boot, UEFI, etc.)
    actions.py             # whitelist de acciones aplicables + log de auditoría
                           # (incluye apagar/reiniciar el equipo)
    monitor.py             # monitoreo en vivo (SSE) de 60s
    correlate.py           # correlación de causa raíz entre chequeos
    history.py             # historial de escaneos en SQLite
    disks.py               # listado de discos/particiones + bloqueo del disco del sistema
    disk_actions.py        # formatear/crear/eliminar partición, renombrar, cambiar letra
    settings.py            # técnicos y ruta de red (diagfix_settings.json)
    nas.py                 # credenciales (cmdkey) y subida de reportes a la red
    restore.py              # punto de restauración del sistema
    scripts_runner.py        # ejecutor de scripts de la carpeta scripts/
    inventory.py             # inventario acumulado en CSV
    local_reports.py         # copia local automática de la ficha en reportes/
    report_image.py          # reporte y ficha renderizados como PNG (Pillow)
    maintenance.py           # backup de drivers
    startup_manager.py       # gestor de inicio real (listar/deshabilitar/habilitar)
    updater.py               # búsqueda/descarga de actualizaciones desde GitHub Releases
  static/
    index.html              # incluye la pestaña Pruebas (teclado/mouse/parlantes/
                           # micrófono/cámara/pantalla)
    style.css
    script.js
  scripts/                 # tus propios .ps1/.bat/.cmd (no versionar)
  requirements.txt
  actions_log.jsonl        # se crea al usar una acción (no versionar)
  diagfix_history.db       # se crea al guardar el primer escaneo (no versionar)
  diagfix_settings.json    # se crea al agregar un técnico o ruta de red (no versionar)
  diagfix_inventory.csv    # se crea al guardar el primer escaneo (no versionar)
  reportes/                # copias locales automáticas de la ficha (no versionar)
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
   `checks.base.run_powershell`, que ya fuerza salida UTF-8. Si interpolás
   texto que no controlás (una etiqueta, una ruta) dentro de un comando de
   PowerShell armado con f-string, pasalo por `checks.base.ps_quote` primero
   y usá comillas simples — las dobles permiten `$(...)` (ejecución de
   código) aunque saques las comillas del valor.
4. Ajusta los umbrales según lo que veas en terreno.
5. Si el chequeo puede combinarse con otro para sugerir una causa raíz más
   específica, agrega una regla en `checks/correlate.py`.

## Empaquetar como .exe

```bash
pyinstaller --onefile --name DiagFix --uac-admin --add-data "static;static" app.py
```

El `.exe` queda en `dist/DiagFix.exe`, no necesita Python instalado.
`--uac-admin` hace que Windows pida elevación automáticamente al abrirlo.
**Reconstruilo cada vez que cambie algo en `checks/` o `static/`** — un
`.exe` viejo no incluye módulos nuevos.

## Publicar una actualización

La app tiene un botón "Buscar actualizaciones" (pestaña Sistema) que consulta
el último release de este mismo repo en GitHub. Para publicar uno nuevo:

```bash
# 1. Bumpear CURRENT_VERSION en checks/updater.py (ej. "1.0.0" -> "1.1.0")
# 2. Compilar el .exe (paso anterior)
git add -A
git commit -m "Describe el cambio"
git push
git tag v1.1.0
git push origin v1.1.0
gh release create v1.1.0 "dist/DiagFix.exe" --title "v1.1.0" --notes "Qué cambió"
```

El número de versión en el tag de git (`v1.1.0`) y en
`checks/updater.py::CURRENT_VERSION` (`1.1.0`) tienen que coincidir — la app
compara ambos para decidir si hay una versión más nueva.

## Próximos pasos posibles

- Temperaturas de CPU/GPU (requiere una librería de terceros tipo
  LibreHardwareMonitor, Windows no expone esto de forma nativa y confiable).
- Suite de tests con `pytest` mockeando `subprocess.run` para cubrir el
  parseo de cada chequeo sin depender del equipo donde se ejecuta.
