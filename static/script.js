function switchTab(tabId) {
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.hidden = panel.id !== tabId;
  });
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });

  // Algunas secciones se cargan solas al entrar, para no obligar a un clic
  // extra en algo que el técnico va a querer ver de inmediato.
  if (tabId === "tab-discos") {
    loadDisks();
  } else if (tabId === "tab-monitoreo" && !monitorSource) {
    // No reinicia si ya hay un monitoreo en curso (evita perder el
    // historial acumulado si el técnico entra y sale de la pestaña).
    startMonitor();
  } else if (tabId !== "tab-pruebas") {
    // Cámara y micrófono quedan prendidos hasta que se apagan a mano — si
    // el técnico cambia de pestaña sin detenerlos manualmente, se cortan
    // solos para no dejar el micrófono/cámara activos de fondo.
    stopMicTest();
    stopCameraTest();
  }
}

const CIRCUMFERENCE = 2 * Math.PI * 52; // debe coincidir con r=52 del SVG

const statusLabel = {
  ok: "OK",
  warning: "Atención",
  critical: "Crítico",
  error: "Error",
  unknown: "No disponible",
};

let lastScan = null;
let actionsCatalog = {};
let currentSerial = null;

async function createRestorePoint() {
  const status = document.getElementById("restore-point-status");
  if (!window.confirm("¿Crear un punto de restauración del sistema ahora? Puede tardar un minuto.")) return;
  status.textContent = "Creando…";
  try {
    const res = await fetch("/api/restore-point", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: "DiagFix" }),
    });
    const data = await res.json();
    status.textContent = data.message || (data.status === "ok" ? "Creado." : "Error");
  } catch (e) {
    status.textContent = "No se pudo crear el punto de restauración.";
  }
}

async function loadScripts() {
  const status = document.getElementById("scripts-status");
  const list = document.getElementById("scripts-list");
  try {
    const res = await fetch("/api/scripts");
    const scripts = await res.json();
    list.innerHTML = "";
    if (!scripts.length) {
      status.textContent = "No hay scripts en la carpeta scripts/.";
      return;
    }
    status.textContent = "";
    scripts.forEach((s) => {
      const row = document.createElement("div");
      row.className = "quick-action";

      const btn = document.createElement("button");
      btn.className = "btn btn-ghost btn-small";
      btn.textContent = `Ejecutar ${s.name}`;

      const info = document.createElement("span");
      info.className = "action-status muted";
      info.textContent = `${s.size_kb} KB · modificado ${s.modified}`;

      btn.addEventListener("click", () => {
        openConfirmOverlay({
          title: `Ejecutar ${s.name}`,
          message: "Este script corre con los mismos permisos que DiagFix. Confirmá solo si conocés su contenido.",
          confirmLabel: "Ejecutar",
          onConfirm: async () => {
            info.textContent = "Ejecutando…";
            try {
              const res = await fetch("/api/scripts/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename: s.name }),
              });
              const data = await res.json();
              info.textContent = `[${data.status}] ${(data.output || "").slice(0, 300)}`;
            } catch (e) {
              info.textContent = "No se pudo ejecutar el script.";
            }
          },
        });
      });

      row.appendChild(btn);
      row.appendChild(info);
      list.appendChild(row);
    });
  } catch (e) {
    status.textContent = "No se pudo cargar la lista de scripts.";
  }
}

async function loadActionsCatalog() {
  try {
    const res = await fetch("/api/actions");
    const list = await res.json();
    actionsCatalog = Object.fromEntries(list.map((a) => [a.id, a]));

    const panel = document.getElementById("quick-actions");
    panel.innerHTML = "";
    list.forEach((a) => {
      const item = document.createElement("div");
      item.className = "quick-action";

      const btn = document.createElement("button");
      btn.className = "btn btn-ghost btn-small";
      btn.textContent = a.label;

      const status = document.createElement("span");
      status.className = "action-status muted";
      status.textContent = a.description;

      btn.addEventListener("click", () => {
        applyAction(a.id, status, `¿Aplicar "${a.label}" en este equipo ahora?\n\n${a.description}`);
      });

      item.appendChild(btn);
      item.appendChild(status);
      panel.appendChild(item);
    });
  } catch (e) {
    // Sin acciones rápidas disponibles; el resto del panel sigue funcionando.
  }
}

function scoreColor(score) {
  if (score >= 80) return "var(--ok)";
  if (score >= 50) return "var(--warn)";
  return "var(--critical)";
}

function scoreHeroText(score) {
  if (score >= 90) return "Equipo en buen estado";
  if (score >= 80) return "Estado general saludable, con detalles menores";
  if (score >= 50) return "Se detectaron fallas que conviene revisar";
  return "Estado crítico: hay fallas que requieren atención inmediata";
}

function setGauge(score) {
  const fill = document.getElementById("gauge-fill");
  const offset = CIRCUMFERENCE * (1 - score / 100);
  fill.style.stroke = scoreColor(score);
  fill.style.strokeDashoffset = offset;
  document.getElementById("score-value").textContent = score;
}

function identityField(label, value) {
  const row = document.createElement("div");
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value ?? "—";
  row.appendChild(dt);
  row.appendChild(dd);
  return row;
}

function specRow(tbody, cells) {
  const tr = document.createElement("tr");
  cells.forEach((v) => {
    const td = document.createElement("td");
    td.textContent = v ?? "—";
    tr.appendChild(td);
  });
  tbody.appendChild(tr);
}

async function loadIdentity() {
  const emptyCard = document.getElementById("specs-empty");
  const emptyText = emptyCard.querySelector("p");
  try {
    const res = await fetch("/api/identity");
    const data = await res.json();
    if (!data.available) {
      emptyText.textContent = data.reason || "No se pudo obtener la ficha del equipo.";
      emptyCard.hidden = false;
      document.getElementById("specs-content").hidden = true;
      return;
    }
    currentSerial = data.serial || null;

    document.getElementById("specs-empty").hidden = true;
    document.getElementById("specs-content").hidden = false;

    const equipo = document.getElementById("specs-equipo");
    equipo.innerHTML = "";
    equipo.appendChild(identityField("Equipo", [data.manufacturer, data.model].filter(Boolean).join(" ")));
    equipo.appendChild(identityField("Número de serie", data.serial));
    equipo.appendChild(identityField("Número de parte (P/N)", data.part_number));
    equipo.appendChild(identityField("Dominio / grupo", data.domain ? `${data.domain} (${data.part_of_domain ? "dominio" : "grupo de trabajo"})` : null));
    equipo.appendChild(identityField("Usuario", data.username));

    const sistema = document.getElementById("specs-sistema");
    sistema.innerHTML = "";
    sistema.appendChild(identityField("Sistema operativo", data.os_summary));
    sistema.appendChild(identityField("Compilación", data.os_build ? `build ${data.os_build}, ${data.os_arch ?? "?"}` : null));
    sistema.appendChild(identityField("Instalado", data.install_date));
    sistema.appendChild(identityField("Último arranque", data.last_boot));
    sistema.appendChild(identityField("Office", data.office));

    document.getElementById("specs-ram-summary").textContent =
      data.ram_total_gb != null
        ? `${data.ram_total_gb} GB total · ${data.ram_slots_used ?? "?"}/${data.ram_slots_total ?? "?"} slots usados` +
          (data.ram_channel_mode ? ` · ${data.ram_channel_mode}` : "")
        : "";
    const ramTable = document.getElementById("specs-ram-table");
    ramTable.innerHTML = "";
    (data.ram_modules || []).forEach((m) => {
      specRow(ramTable, [
        m.slot, m.manufacturer,
        m.capacity_gb != null ? `${m.capacity_gb} GB` : null,
        m.type, m.speed_mhz ? `${m.speed_mhz} MHz` : null, m.part_number,
      ]);
    });

    const disksTable = document.getElementById("specs-disks-table");
    disksTable.innerHTML = "";
    (data.disks || []).forEach((d) => {
      specRow(disksTable, [d.name, d.size_label, d.media_type, d.bus_type]);
    });

    const gpuTable = document.getElementById("specs-gpu-table");
    gpuTable.innerHTML = "";
    (data.gpus || []).forEach((g) => {
      specRow(gpuTable, [g.name, g.driver_version, g.driver_date, g.vram_mb != null ? `${g.vram_mb} MB` : null]);
    });

    const perifericos = document.getElementById("specs-perifericos");
    perifericos.innerHTML = "";
    perifericos.appendChild(identityField("Monitores conectados", data.monitor_count));
    perifericos.appendChild(identityField("Dispositivos USB activos", data.usb_device_count));
    perifericos.appendChild(identityField("Impresoras instaladas", data.printer_count));
  } catch (e) {
    emptyText.textContent = "No se pudo conectar con la app para obtener la ficha del equipo.";
    emptyCard.hidden = false;
    document.getElementById("specs-content").hidden = true;
  }
}

async function loadWin11Readiness() {
  const badge = document.getElementById("win11-badge");
  const emptyEl = document.getElementById("win11-empty");
  const tableWrap = document.getElementById("win11-table-wrap");
  const table = document.getElementById("win11-table");
  try {
    const res = await fetch("/api/win11-readiness");
    const data = await res.json();
    if (!data.available) {
      badge.textContent = "";
      emptyEl.textContent = data.reason || "No se pudo consultar la compatibilidad.";
      emptyEl.hidden = false;
      tableWrap.hidden = true;
      return;
    }
    emptyEl.hidden = true;
    tableWrap.hidden = false;
    if (data.ya_es_windows11) {
      badge.textContent = "Ya tiene Windows 11";
      badge.className = "badge badge-ok";
    } else if (data.apto) {
      badge.textContent = "Apto para actualizar";
      badge.className = "badge badge-ok";
    } else {
      badge.textContent = "No apto";
      badge.className = "badge badge-critical";
    }
    table.innerHTML = "";
    (data.requisitos || []).forEach((r) => {
      const tr = document.createElement("tr");
      const tdLabel = document.createElement("td");
      tdLabel.textContent = r.label;
      const tdEstado = document.createElement("td");
      tdEstado.textContent = r.ok ? "OK" : "Falla";
      tdEstado.className = r.ok ? "text-ok" : "text-critical";
      const tdDetalle = document.createElement("td");
      tdDetalle.textContent = r.detail ?? "—";
      tr.appendChild(tdLabel);
      tr.appendChild(tdEstado);
      tr.appendChild(tdDetalle);
      table.appendChild(tr);
    });
  } catch (e) {
    badge.textContent = "";
    emptyEl.textContent = "No se pudo conectar con la app.";
    emptyEl.hidden = false;
    tableWrap.hidden = true;
  }
}

async function loadSystemInfo() {
  try {
    const res = await fetch("/api/system");
    const data = await res.json();
    document.getElementById("os-info").textContent = data.os_version;
    const badge = document.getElementById("admin-badge");
    if (data.is_admin) {
      badge.textContent = "Ejecutando como administrador";
      badge.className = "badge badge-ok";
    } else {
      badge.textContent = "Sin permisos de administrador";
      badge.className = "badge badge-warning";
    }
  } catch (e) {
    document.getElementById("os-info").textContent = "No se pudo leer el sistema";
  }
}

async function applyAction(actionId, statusEl, confirmMsg) {
  if (!window.confirm(confirmMsg)) return;
  const originalText = statusEl.textContent;
  statusEl.textContent = "Aplicando…";
  try {
    const res = await fetch(`/api/actions/${encodeURIComponent(actionId)}`, { method: "POST" });
    const data = await res.json();
    statusEl.textContent = data.status === "ok" ? `Listo: ${data.output || "acción aplicada"}` : `Error: ${data.output || "no se pudo aplicar"}`;
  } catch (e) {
    statusEl.textContent = "No se pudo aplicar la acción.";
  }
}

function renderActionButton(actionId, label) {
  const wrap = document.createElement("div");
  wrap.className = "action-row";

  const btn = document.createElement("button");
  btn.className = "btn btn-ghost btn-small";
  btn.textContent = label || "Aplicar solución";

  const status = document.createElement("span");
  status.className = "action-status muted";

  btn.addEventListener("click", () => {
    applyAction(actionId, status, `¿Aplicar "${label}" en este equipo ahora?`);
  });

  wrap.appendChild(btn);
  wrap.appendChild(status);
  return wrap;
}

function renderDetail(check) {
  const details = document.createElement("details");
  details.className = "check-detail";

  const summary = document.createElement("summary");
  summary.textContent = "Posibles causas y solución";
  details.appendChild(summary);

  if (check.causes && check.causes.length) {
    const causesTitle = document.createElement("p");
    causesTitle.className = "detail-label";
    causesTitle.textContent = "Posibles causas";
    details.appendChild(causesTitle);

    const ul = document.createElement("ul");
    check.causes.forEach((c) => {
      const li = document.createElement("li");
      li.textContent = c;
      ul.appendChild(li);
    });
    details.appendChild(ul);
  }

  if (check.fix && check.fix.length) {
    const fixTitle = document.createElement("p");
    fixTitle.className = "detail-label";
    fixTitle.textContent = "Cómo solucionarlo";
    details.appendChild(fixTitle);

    const ol = document.createElement("ol");
    check.fix.forEach((step) => {
      const li = document.createElement("li");
      li.textContent = step;
      ol.appendChild(li);
    });
    details.appendChild(ol);
  }

  if (check.action_id) {
    const label = actionsCatalog[check.action_id]?.label || "Aplicar solución";
    details.appendChild(renderActionButton(check.action_id, label));
  }

  return details;
}

function renderCategory(category) {
  const wrap = document.createElement("div");
  wrap.className = "category";

  const header = document.createElement("div");
  header.className = "category-header";
  const h3 = document.createElement("h3");
  h3.textContent = category.name;
  header.appendChild(h3);
  wrap.appendChild(header);

  category.checks.forEach((check) => {
    const item = document.createElement("div");
    item.className = `check-item status-${check.status}`;

    const row = document.createElement("div");
    row.className = `check-row status-${check.status}`;

    const dot = document.createElement("span");
    dot.className = "dot";
    dot.title = statusLabel[check.status] || check.status;
    row.appendChild(dot);

    const name = document.createElement("span");
    name.className = "check-name";
    name.textContent = check.name;
    row.appendChild(name);

    const value = document.createElement("span");
    value.className = "check-value";
    value.textContent = check.value ?? "—";
    row.appendChild(value);

    item.appendChild(row);

    if (check.message) {
      const msg = document.createElement("p");
      msg.className = "check-message";
      msg.textContent = check.message;
      item.appendChild(msg);
    }

    const hasDetail = (check.causes && check.causes.length) || (check.fix && check.fix.length);
    if (hasDetail && check.status !== "ok") {
      item.appendChild(renderDetail(check));
    }

    wrap.appendChild(item);
  });

  return wrap;
}

function renderInsights(insights) {
  const section = document.getElementById("insights");
  const list = document.getElementById("insights-list");
  list.innerHTML = "";
  if (!insights || !insights.length) {
    section.hidden = true;
    return;
  }
  insights.forEach((insight) => {
    const card = document.createElement("div");
    card.className = `insight-card severity-${insight.severity}`;

    const dot = document.createElement("span");
    dot.className = "insight-dot";
    card.appendChild(dot);

    const body = document.createElement("div");
    const h3 = document.createElement("h3");
    h3.textContent = insight.title;
    const p = document.createElement("p");
    p.textContent = insight.message;
    body.appendChild(h3);
    body.appendChild(p);
    card.appendChild(body);

    list.appendChild(card);
  });
  section.hidden = false;
}

function renderScan(data) {
  lastScan = data;
  setGauge(data.score);
  document.getElementById("hero-status").textContent = scoreHeroText(data.score);
  document.getElementById("hero-detail").textContent =
    `Detalle por categoría abajo. Los puntos en rojo o amarillo indican qué revisar primero. ` +
    `Cobertura: ${data.coverage} chequeos completados.`;
  document.getElementById("scan-time").textContent =
    "Último escaneo: " + new Date().toLocaleTimeString();

  renderInsights(data.insights);

  const results = document.getElementById("results");
  results.innerHTML = "";
  data.categories.forEach((cat) => results.appendChild(renderCategory(cat)));
}

async function runScan() {
  const btn = document.getElementById("scan-btn");
  btn.disabled = true;
  btn.textContent = "Escaneando…";
  try {
    const res = await fetch("/api/scan");
    const data = await res.json();
    renderScan(data);
  } catch (e) {
    document.getElementById("hero-status").textContent = "No se pudo completar el diagnóstico";
    document.getElementById("hero-detail").textContent = String(e);
  } finally {
    btn.disabled = false;
    btn.textContent = "Ejecutar diagnóstico";
  }
}

async function runSfc() {
  if (!window.confirm("Esto ejecuta sfc /scannow y puede tardar varios minutos. ¿Continuar?")) return;
  const btn = document.getElementById("sfc-btn");
  const status = document.getElementById("sfc-status");
  btn.disabled = true;
  status.textContent = "Ejecutando sfc /scannow… esto puede tardar varios minutos.";
  try {
    const res = await fetch("/api/scan/sfc", { method: "POST" });
    const data = await res.json();
    status.textContent = `Resultado: ${statusLabel[data.status] || data.status}`;
  } catch (e) {
    status.textContent = "No se pudo ejecutar la verificación.";
  } finally {
    btn.disabled = false;
  }
}

let monitorSource = null;

function startMonitor() {
  const btn = document.getElementById("monitor-btn");
  const status = document.getElementById("monitor-status");
  const live = document.getElementById("monitor-live");
  const history = document.getElementById("monitor-history");
  const topList = document.getElementById("monitor-top-processes");

  if (monitorSource) {
    monitorSource.close();
    monitorSource = null;
  }
  history.innerHTML = "";
  topList.innerHTML = "";
  live.hidden = false;
  btn.disabled = true;
  status.textContent = "Monitoreando… (60s)";

  monitorSource = new EventSource("/api/monitor/stream");

  monitorSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.error) {
      status.textContent = data.error;
      monitorSource.close();
      monitorSource = null;
      btn.disabled = false;
      return;
    }

    document.getElementById("monitor-cpu").textContent = `${data.cpu_percent}%`;
    document.getElementById("monitor-ram").textContent = `${data.ram_percent}%`;
    document.getElementById("monitor-net").textContent = `${data.net_sent_kbps} / ${data.net_recv_kbps} KB/s`;
    document.getElementById("monitor-latency").textContent =
      data.latency_ms != null ? `${data.latency_ms} ms` : "sin respuesta";

    const row = document.createElement("tr");
    [
      data.timestamp,
      `${data.cpu_percent}%`,
      `${data.ram_percent}%`,
      data.net_sent_kbps,
      data.net_recv_kbps,
      data.latency_ms != null ? `${data.latency_ms} ms` : "—",
    ].forEach((v) => {
      const td = document.createElement("td");
      td.textContent = v;
      row.appendChild(td);
    });
    history.prepend(row);

    topList.innerHTML = "";
    (data.top_processes || []).forEach((p) => {
      const li = document.createElement("li");
      li.textContent = `${p.name} (PID ${p.pid}) — CPU ${p.cpu}%, RAM ${p.mem}%`;
      topList.appendChild(li);
    });
  };

  monitorSource.onerror = () => {
    if (monitorSource) {
      monitorSource.close();
      monitorSource = null;
    }
    btn.disabled = false;
    status.textContent = "Monitoreo finalizado.";
  };
}

const ADD_TECHNICIAN_VALUE = "__add_new__";

function populateTechnicianSelect(data) {
  const select = document.getElementById("visit-technician");
  select.innerHTML = "";

  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "— Sin asignar —";
  select.appendChild(blank);

  (data.technicians || []).forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });

  const addOpt = document.createElement("option");
  addOpt.value = ADD_TECHNICIAN_VALUE;
  addOpt.textContent = "+ Agregar técnico nuevo…";
  select.appendChild(addOpt);

  if (data.last_technician && data.technicians.includes(data.last_technician)) {
    select.value = data.last_technician;
  }
}

async function loadTechnicians() {
  try {
    const res = await fetch("/api/technicians");
    const data = await res.json();
    populateTechnicianSelect(data);
  } catch (e) {
    // Sin lista de técnicos disponible; el campo queda vacío pero usable.
  }
}

async function handleTechnicianChange() {
  const select = document.getElementById("visit-technician");
  if (select.value !== ADD_TECHNICIAN_VALUE) return;
  const name = window.prompt("Nombre del nuevo técnico:");
  if (!name || !name.trim()) {
    select.value = "";
    return;
  }
  try {
    const res = await fetch("/api/technicians", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    const data = await res.json();
    if (data.status === "ok") {
      populateTechnicianSelect(data);
      select.value = name.trim();
    } else {
      select.value = "";
    }
  } catch (e) {
    select.value = "";
  }
}

function visitMeta() {
  return {
    client: document.getElementById("visit-client").value.trim() || null,
    ticket: document.getElementById("visit-ticket").value.trim() || null,
    technician: document.getElementById("visit-technician").value.trim() || null,
  };
}

async function saveToHistory() {
  const status = document.getElementById("history-status");
  if (!lastScan) {
    status.textContent = "Ejecuta un diagnóstico primero.";
    return;
  }
  if (!currentSerial) {
    status.textContent = "No se detectó el número de serie del equipo (no disponible en este sistema).";
    return;
  }
  status.textContent = "Guardando…";
  try {
    const res = await fetch("/api/scan/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        serial: currentSerial,
        score: lastScan.score,
        coverage: lastScan.coverage,
        categories: lastScan.categories,
        insights: lastScan.insights || [],
        ...visitMeta(),
      }),
    });
    const data = await res.json();
    status.textContent = data.status === "ok" ? "Guardado en el historial." : `Error: ${data.message || "no se pudo guardar"}`;
  } catch (e) {
    status.textContent = "No se pudo guardar el historial.";
  }
}

async function viewHistory() {
  const status = document.getElementById("history-status");
  const list = document.getElementById("history-list");
  if (!currentSerial) {
    status.textContent = "No se detectó el número de serie del equipo.";
    return;
  }
  try {
    const res = await fetch(`/api/history/${encodeURIComponent(currentSerial)}`);
    const rows = await res.json();
    list.innerHTML = "";
    if (!rows.length) {
      status.textContent = "Sin escaneos guardados para este equipo todavía.";
      list.hidden = true;
      return;
    }
    status.textContent = "";
    const table = document.createElement("table");
    table.className = "history-table";
    table.innerHTML = "<thead><tr><th>Fecha</th><th>Score</th><th>Cobertura</th><th>Cliente</th><th>Ticket</th><th>Técnico</th></tr></thead>";
    const tbody = document.createElement("tbody");
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      [r.timestamp, r.score, r.coverage, r.client ?? "—", r.ticket ?? "—", r.technician ?? "—"].forEach((v) => {
        const td = document.createElement("td");
        td.textContent = v;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    list.appendChild(table);
    list.hidden = false;
  } catch (e) {
    status.textContent = "No se pudo cargar el historial.";
  }
}

async function loadNasSettings() {
  try {
    const res = await fetch("/api/nas");
    const data = await res.json();
    document.getElementById("nas-path").value = data.nas_path || "";
  } catch (e) {
    // Sin ruta de red configurada todavía; el campo queda vacío.
  }
}

async function saveNasPath() {
  const status = document.getElementById("nas-status");
  const path = document.getElementById("nas-path").value.trim();
  try {
    const res = await fetch("/api/nas/path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    status.textContent = data.status === "ok" ? "Ruta guardada." : "No se pudo guardar la ruta.";
  } catch (e) {
    status.textContent = "No se pudo guardar la ruta.";
  }
}

function openNasCredentialsForm() {
  const overlay = document.createElement("div");
  overlay.className = "confirm-overlay";

  const box = document.createElement("div");
  box.className = "confirm-box";
  box.innerHTML = `
    <h3 style="color:var(--text);">Guardar credenciales de red</h3>
    <p>Se guardan en el Administrador de credenciales de Windows (<code>cmdkey</code>).
    DiagFix no las almacena ni las registra en ningún archivo.</p>
  `;

  const serverInput = document.createElement("input");
  serverInput.type = "text";
  serverInput.placeholder = "Servidor (ej. 192.168.1.5 o nombre)";
  box.appendChild(serverInput);

  const userInput = document.createElement("input");
  userInput.type = "text";
  userInput.placeholder = "Usuario";
  box.appendChild(userInput);

  const passInput = document.createElement("input");
  passInput.type = "password";
  passInput.placeholder = "Contraseña";
  passInput.autocomplete = "new-password";
  box.appendChild(passInput);

  const actionsRow = document.createElement("div");
  actionsRow.className = "confirm-box-actions";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "btn btn-ghost btn-small";
  cancelBtn.textContent = "Cancelar";
  cancelBtn.addEventListener("click", () => overlay.remove());

  const saveBtn = document.createElement("button");
  saveBtn.className = "btn btn-primary btn-small";
  saveBtn.textContent = "Guardar";
  saveBtn.addEventListener("click", async () => {
    const server = serverInput.value.trim();
    const user = userInput.value.trim();
    const password = passInput.value;
    if (!server || !user || !password) return;
    saveBtn.disabled = true;
    try {
      const res = await fetch("/api/nas/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server, user, password }),
      });
      const data = await res.json();
      document.getElementById("nas-status").textContent = data.message || (data.status === "ok" ? "Credenciales guardadas." : "Error");
    } catch (e) {
      document.getElementById("nas-status").textContent = "No se pudieron guardar las credenciales.";
    } finally {
      overlay.remove();
    }
  });

  actionsRow.appendChild(cancelBtn);
  actionsRow.appendChild(saveBtn);
  box.appendChild(actionsRow);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  serverInput.focus();
}

async function uploadReportToNas() {
  const status = document.getElementById("nas-status");
  const html = buildReportHtml();
  if (!html) {
    status.textContent = "Ejecuta un diagnóstico primero.";
    return;
  }
  status.textContent = "Subiendo…";
  const filename = `diagfix-reporte-${currentSerial || "equipo"}-${Date.now()}.html`;
  try {
    const res = await fetch("/api/nas/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, content: html }),
    });
    const data = await res.json();
    status.textContent = data.message || (data.status === "ok" ? "Guardado en la red." : "Error al subir.");
  } catch (e) {
    status.textContent = "No se pudo subir el reporte.";
  }
}

function buildReportHtml() {
  if (!lastScan) return null;
  const meta = visitMeta();
  const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const rows = lastScan.categories
    .map((cat) => {
      const checks = cat.checks
        .map((c) => {
          const causes = c.causes && c.causes.length
            ? `<p class="d-label">Posibles causas</p><ul>${c.causes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
            : "";
          const fix = c.fix && c.fix.length
            ? `<p class="d-label">Cómo solucionarlo</p><ol>${c.fix.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>`
            : "";
          return `<div class="check status-${esc(c.status)}">
            <div class="check-row"><span class="dot"></span><strong>${esc(c.name)}</strong><span class="value">${esc(c.value ?? "—")}</span></div>
            ${c.message ? `<p class="msg">${esc(c.message)}</p>` : ""}
            ${causes}${fix}
          </div>`;
        })
        .join("");
      return `<section><h2>${esc(cat.name)}</h2>${checks}</section>`;
    })
    .join("");
  const insights = (lastScan.insights || [])
    .map((i) => `<div class="insight severity-${esc(i.severity)}"><strong>${esc(i.title)}</strong><p>${esc(i.message)}</p></div>`)
    .join("");

  const html = `<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Reporte DiagFix</title>
<style>
  body { font-family: Segoe UI, Arial, sans-serif; max-width: 800px; margin: 24px auto; color: #1c2430; }
  h1 { margin-bottom: 4px; }
  .meta { color: #556; font-size: 13px; margin-bottom: 20px; }
  .score { font-size: 32px; font-weight: 700; }
  section { margin-bottom: 20px; border: 1px solid #e2e6ec; border-radius: 6px; padding: 12px 16px; }
  h2 { font-size: 15px; margin: 0 0 10px; }
  .check { padding: 8px 0; border-top: 1px solid #eef1f5; }
  .check:first-of-type { border-top: none; }
  .check-row { display: flex; align-items: baseline; gap: 8px; }
  .value { margin-left: auto; color: #667; font-family: monospace; font-size: 13px; }
  .msg { margin: 4px 0 0; color: #667; font-size: 13px; }
  .d-label { font-size: 11px; color: #889; margin: 8px 0 2px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #99a; display: inline-block; }
  .status-ok .dot { background: #3fbf82; }
  .status-warning .dot { background: #e8a83b; }
  .status-critical .dot, .status-error .dot { background: #e8604c; }
  .insight { border-left: 3px solid #99a; padding: 8px 12px; margin-bottom: 8px; background: #f7f8fa; }
  .insight.severity-critical { border-color: #e8604c; }
  .insight.severity-warning { border-color: #e8a83b; }
  ul, ol { margin: 2px 0; padding-left: 18px; font-size: 13px; }
</style></head>
<body>
  <h1>DiagFix — Reporte de diagnóstico</h1>
  <p class="meta">
    ${meta.client ? `Cliente: ${esc(meta.client)} · ` : ""}${meta.ticket ? `Ticket: ${esc(meta.ticket)} · ` : ""}${meta.technician ? `Técnico: ${esc(meta.technician)} · ` : ""}
    Fecha: ${new Date().toLocaleString()}
  </p>
  <p class="score">${lastScan.score}/100 <span style="font-size:14px;color:#889;">(cobertura ${esc(lastScan.coverage)})</span></p>
  ${insights ? `<h2>Diagnóstico probable</h2>${insights}` : ""}
  ${rows}
</body></html>`;

  return html;
}

function exportReportHtml() {
  const html = buildReportHtml();
  if (!html) return;
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `diagfix-reporte-${Date.now()}.html`;
  a.click();
  URL.revokeObjectURL(url);
}

async function loadStartupItems() {
  const status = document.getElementById("startup-status");
  const list = document.getElementById("startup-list");
  try {
    const res = await fetch("/api/startup");
    const items = await res.json();
    list.innerHTML = "";
    if (!items.length) {
      status.textContent = "No se detectaron programas de inicio.";
      return;
    }
    status.textContent = "";
    const table = document.createElement("table");
    table.className = "partition-table";
    table.innerHTML = "<thead><tr><th>Programa</th><th>Ubicación</th><th>Estado</th><th>Acción</th></tr></thead>";
    const tbody = document.createElement("tbody");
    items.forEach((item) => {
      const tr = document.createElement("tr");
      const tdName = document.createElement("td");
      tdName.textContent = item.name;
      tdName.title = item.command;
      const tdLoc = document.createElement("td");
      tdLoc.textContent = item.location;
      const tdState = document.createElement("td");
      tdState.textContent = item.enabled ? "Habilitado" : "Deshabilitado";
      const tdAction = document.createElement("td");
      const btn = document.createElement("button");
      btn.className = "btn btn-ghost btn-tiny";
      btn.textContent = item.enabled ? "Deshabilitar" : "Habilitar";
      btn.addEventListener("click", async () => {
        const verb = item.enabled ? "deshabilitar" : "habilitar";
        if (!window.confirm(`¿${verb.charAt(0).toUpperCase() + verb.slice(1)} "${item.name}"? Es reversible.`)) return;
        try {
          const r = await fetch("/api/startup/toggle", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: item.id, enabled: !item.enabled }),
          });
          const data = await r.json();
          status.textContent = data.message || (data.status === "ok" ? "Listo." : "Error");
          loadStartupItems();
        } catch (e) {
          status.textContent = "No se pudo aplicar el cambio.";
        }
      });
      tdAction.appendChild(btn);
      tr.appendChild(tdName);
      tr.appendChild(tdLoc);
      tr.appendChild(tdState);
      tr.appendChild(tdAction);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    list.appendChild(table);
  } catch (e) {
    status.textContent = "No se pudo cargar la lista de programas de inicio.";
  }
}

async function backupDrivers() {
  const status = document.getElementById("drivers-status");
  const dest = document.getElementById("drivers-dest").value.trim();
  if (!dest) {
    status.textContent = "Indicá una carpeta de destino.";
    return;
  }
  if (!window.confirm(`¿Exportar los drivers instalados a "${dest}"? Requiere permisos de administrador.`)) return;
  status.textContent = "Exportando… puede tardar varios minutos.";
  try {
    const res = await fetch("/api/maintenance/backup-drivers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination: dest }),
    });
    const data = await res.json();
    status.textContent = data.message || (data.status === "ok" ? "Listo." : "Error");
  } catch (e) {
    status.textContent = "No se pudo exportar los drivers.";
  }
}

async function exportSpecsPng() {
  try {
    const res = await fetch("/api/report/specs-png");
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `diagfix-ficha-${Date.now()}.png`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    // Sin conexión al backend; no se pudo generar la imagen.
  }
}

async function exportReportPng() {
  if (!lastScan) return;
  const meta = visitMeta();
  try {
    const res = await fetch("/api/report/png", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        score: lastScan.score,
        coverage: lastScan.coverage,
        categories: lastScan.categories,
        insights: lastScan.insights || [],
        ...meta,
      }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `diagfix-reporte-${Date.now()}.png`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    // Sin conexión al backend; no se pudo generar la imagen.
  }
}

async function viewInventory() {
  const list = document.getElementById("inventory-list");
  try {
    const res = await fetch("/api/inventory");
    const rows = await res.json();
    list.innerHTML = "";
    if (!rows.length) {
      list.hidden = false;
      list.textContent = "Sin equipos en el inventario todavía.";
      return;
    }
    const table = document.createElement("table");
    table.className = "history-table";
    const cols = ["fecha", "serie", "equipo", "part_number", "score", "cliente", "ticket", "tecnico"];
    table.innerHTML = `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead>`;
    const tbody = document.createElement("tbody");
    rows.slice().reverse().forEach((r) => {
      const tr = document.createElement("tr");
      cols.forEach((c) => {
        const td = document.createElement("td");
        td.textContent = r[c] || "—";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    list.appendChild(table);
    list.hidden = false;
  } catch (e) {
    // Sin inventario disponible todavía.
  }
}

function openConfirmOverlay({ title, message, phrase, confirmLabel, onConfirm }) {
  const overlay = document.createElement("div");
  overlay.className = "confirm-overlay";

  const box = document.createElement("div");
  box.className = "confirm-box";

  const h3 = document.createElement("h3");
  h3.textContent = title;
  box.appendChild(h3);

  const p = document.createElement("p");
  p.textContent = message;
  box.appendChild(p);

  let input = null;
  if (phrase) {
    const phraseEl = document.createElement("code");
    phraseEl.className = "confirm-phrase";
    phraseEl.textContent = phrase;
    box.appendChild(phraseEl);

    input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Escribe la frase exacta de arriba";
    box.appendChild(input);
  }

  const actionsRow = document.createElement("div");
  actionsRow.className = "confirm-box-actions";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "btn btn-ghost btn-small";
  cancelBtn.textContent = "Cancelar";
  cancelBtn.addEventListener("click", () => overlay.remove());

  const confirmBtn = document.createElement("button");
  confirmBtn.className = "btn btn-primary btn-small";
  confirmBtn.textContent = confirmLabel || "Confirmar";
  if (phrase) confirmBtn.disabled = true;
  confirmBtn.addEventListener("click", async () => {
    overlay.remove();
    await onConfirm();
  });

  if (input) {
    input.addEventListener("input", () => {
      confirmBtn.disabled = input.value.trim() !== phrase;
    });
  }

  actionsRow.appendChild(cancelBtn);
  actionsRow.appendChild(confirmBtn);
  box.appendChild(actionsRow);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  if (input) input.focus();
}

async function callDiskApi(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

function renderDiskCard(disk) {
  const card = document.createElement("div");
  card.className = "disk-card";

  const header = document.createElement("div");
  header.className = "disk-card-header";
  const h3 = document.createElement("h3");
  h3.textContent = `Disco ${disk.number} — ${disk.friendlyName || "sin nombre"}`;
  header.appendChild(h3);
  if (disk.isSystemDisk) {
    const badge = document.createElement("span");
    badge.className = "badge-system";
    badge.textContent = "Disco del sistema — protegido";
    header.appendChild(badge);
  }
  card.appendChild(header);

  const meta = document.createElement("p");
  meta.className = "disk-card-meta";
  meta.textContent = `${disk.sizeGB} GB · ${disk.partitionStyle} · ${disk.busType} · Salud: ${disk.healthStatus}`;
  card.appendChild(meta);

  const table = document.createElement("table");
  table.className = "partition-table";
  table.innerHTML = "<thead><tr><th>Parte</th><th>Letra</th><th>Etiqueta</th><th>Sistema de archivos</th><th>Tamaño</th><th>Libre</th><th>Tipo</th><th>Acciones</th></tr></thead>";
  const tbody = document.createElement("tbody");

  (disk.partitions || []).forEach((part) => {
    const tr = document.createElement("tr");
    [
      `Parte ${part.partNumber}`,
      part.driveLetter ? `${part.driveLetter}:` : "—",
      part.label || "—",
      part.filesystem || "—",
      `${part.sizeGB} GB`,
      part.freeGB != null ? `${part.freeGB} GB` : "—",
      part.type || "—",
    ].forEach((v) => {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    });

    const actionsTd = document.createElement("td");
    actionsTd.className = "partition-actions";
    if (!disk.isSystemDisk) {
      const formatBtn = document.createElement("button");
      formatBtn.className = "btn btn-ghost btn-tiny btn-danger";
      formatBtn.textContent = "Formatear";
      formatBtn.addEventListener("click", () => {
        openConfirmOverlay({
          title: "Formatear partición",
          message: "Esto borra todos los datos de esta partición de forma irreversible.",
          phrase: `FORMATEAR DISCO ${disk.number} PARTE ${part.partNumber}`,
          confirmLabel: "Formatear",
          onConfirm: async () => {
            const r = await callDiskApi("/api/disks/format", { disk_number: disk.number, partition_number: part.partNumber });
            document.getElementById("disks-status").textContent = r.message || (r.status === "ok" ? "Formateado." : "Error");
            if (r.status === "ok") loadDisks();
          },
        });
      });
      actionsTd.appendChild(formatBtn);

      const deleteBtn = document.createElement("button");
      deleteBtn.className = "btn btn-ghost btn-tiny btn-danger";
      deleteBtn.textContent = "Eliminar";
      deleteBtn.addEventListener("click", () => {
        openConfirmOverlay({
          title: "Eliminar partición",
          message: "Esto borra la partición y todos sus datos de forma irreversible.",
          phrase: `ELIMINAR DISCO ${disk.number} PARTE ${part.partNumber}`,
          confirmLabel: "Eliminar",
          onConfirm: async () => {
            const r = await callDiskApi("/api/disks/delete-partition", { disk_number: disk.number, partition_number: part.partNumber });
            document.getElementById("disks-status").textContent = r.message || (r.status === "ok" ? "Eliminada." : "Error");
            if (r.status === "ok") loadDisks();
          },
        });
      });
      actionsTd.appendChild(deleteBtn);

      if (part.driveLetter) {
        const renameBtn = document.createElement("button");
        renameBtn.className = "btn btn-ghost btn-tiny";
        renameBtn.textContent = "Renombrar";
        renameBtn.addEventListener("click", async () => {
          const newLabel = window.prompt("Nueva etiqueta para esta unidad:", part.label || "");
          if (newLabel === null) return;
          const r = await callDiskApi("/api/disks/rename-volume", { drive_letter: part.driveLetter, label: newLabel });
          document.getElementById("disks-status").textContent = r.message || (r.status === "ok" ? "Renombrada." : "Error");
          if (r.status === "ok") loadDisks();
        });
        actionsTd.appendChild(renameBtn);

        const letterBtn = document.createElement("button");
        letterBtn.className = "btn btn-ghost btn-tiny";
        letterBtn.textContent = "Cambiar letra";
        letterBtn.addEventListener("click", async () => {
          const newLetter = window.prompt("Nueva letra de unidad (una sola letra):", part.driveLetter || "");
          if (!newLetter || !newLetter.trim()) return;
          if (!window.confirm(`¿Cambiar la letra de esta unidad a ${newLetter.trim().toUpperCase()}:?`)) return;
          const r = await callDiskApi("/api/disks/set-letter", { disk_number: disk.number, partition_number: part.partNumber, new_letter: newLetter.trim() });
          document.getElementById("disks-status").textContent = r.message || (r.status === "ok" ? "Letra cambiada." : "Error");
          if (r.status === "ok") loadDisks();
        });
        actionsTd.appendChild(letterBtn);
      }
    } else {
      actionsTd.textContent = "—";
    }
    tr.appendChild(actionsTd);
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  card.appendChild(table);

  if (!disk.isSystemDisk) {
    const createRow = document.createElement("div");
    createRow.className = "disk-create-row";

    const sizeInput = document.createElement("input");
    sizeInput.type = "number";
    sizeInput.placeholder = "Tamaño en MB";
    sizeInput.min = "1";

    const fsSelect = document.createElement("select");
    ["NTFS", "exFAT", "FAT32"].forEach((fs) => {
      const opt = document.createElement("option");
      opt.value = fs;
      opt.textContent = fs;
      fsSelect.appendChild(opt);
    });

    const labelInput = document.createElement("input");
    labelInput.type = "text";
    labelInput.placeholder = "Etiqueta (opcional)";

    const createBtn = document.createElement("button");
    createBtn.className = "btn btn-ghost btn-tiny";
    createBtn.textContent = "Crear partición en espacio libre";
    createBtn.addEventListener("click", async () => {
      const sizeMb = parseInt(sizeInput.value, 10);
      if (!sizeMb || sizeMb <= 0) {
        document.getElementById("disks-status").textContent = "Indica un tamaño válido en MB.";
        return;
      }
      if (!window.confirm(`¿Crear una partición de ${sizeMb} MB en el disco ${disk.number}?`)) return;
      const r = await callDiskApi("/api/disks/create-partition", {
        disk_number: disk.number, size_mb: sizeMb, filesystem: fsSelect.value, label: labelInput.value,
      });
      document.getElementById("disks-status").textContent = r.message || (r.status === "ok" ? "Partición creada." : "Error");
      if (r.status === "ok") loadDisks();
    });

    createRow.appendChild(sizeInput);
    createRow.appendChild(fsSelect);
    createRow.appendChild(labelInput);
    createRow.appendChild(createBtn);
    card.appendChild(createRow);
  }

  return card;
}

async function loadDisks() {
  const status = document.getElementById("disks-status");
  const list = document.getElementById("disks-list");
  status.textContent = "Cargando…";
  try {
    const res = await fetch("/api/disks");
    const data = await res.json();
    list.innerHTML = "";
    if (!data.available) {
      status.textContent = data.message || "No se pudo cargar la lista de discos.";
      return;
    }
    status.textContent = "";
    data.disks.forEach((disk) => list.appendChild(renderDiskCard(disk)));
  } catch (e) {
    status.textContent = "No se pudo cargar la lista de discos.";
  }
}

function exportReport() {
  if (!lastScan) return;
  const lines = [`DiagFix — Reporte de diagnóstico`, `Puntaje general: ${lastScan.score}/100`, ""];
  lastScan.categories.forEach((cat) => {
    lines.push(`== ${cat.name} ==`);
    cat.checks.forEach((c) => {
      lines.push(`[${(statusLabel[c.status] || c.status).padEnd(12)}] ${c.name}: ${c.value ?? "—"}`);
      if (c.message) lines.push(`    ${c.message}`);
      if (c.causes && c.causes.length) {
        lines.push("    Posibles causas:");
        c.causes.forEach((cause) => lines.push(`      - ${cause}`));
      }
      if (c.fix && c.fix.length) {
        lines.push("    Cómo solucionarlo:");
        c.fix.forEach((step, i) => lines.push(`      ${i + 1}. ${step}`));
      }
    });
    lines.push("");
  });
  const blob = new Blob([lines.join("\n")], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `diagfix-reporte-${Date.now()}.txt`;
  a.click();
  URL.revokeObjectURL(url);
}

async function checkForUpdate() {
  const info = document.getElementById("update-info");
  const status = document.getElementById("update-status");
  info.innerHTML = "";
  status.textContent = "Buscando…";
  try {
    const res = await fetch("/api/update/check");
    const data = await res.json();
    if (!data.available) {
      status.textContent = data.reason || "No se pudo buscar actualizaciones.";
      return;
    }
    status.textContent = "";
    if (!data.update_available) {
      info.textContent = `Ya tenés la última versión (${data.current_version}).`;
      return;
    }
    const p = document.createElement("p");
    p.className = "card-desc";
    p.style.margin = "0 0 10px";
    p.textContent = `Versión actual: ${data.current_version} → nueva: ${data.latest_version}`;
    const btn = document.createElement("button");
    btn.className = "btn btn-ghost btn-small";
    btn.textContent = "Descargar e instalar";
    btn.addEventListener("click", () => applyUpdate(data.latest_version));
    info.appendChild(p);
    info.appendChild(btn);
  } catch (e) {
    status.textContent = "No se pudo conectar con la app.";
  }
}

async function applyUpdate(version) {
  if (!window.confirm(`¿Descargar e instalar la versión ${version}? La aplicación se va a cerrar y reabrir sola.`)) return;
  const status = document.getElementById("update-status");
  status.textContent = "Descargando…";
  try {
    const res = await fetch("/api/update/apply", { method: "POST" });
    const data = await res.json();
    status.textContent = data.output || (data.status === "ok" ? "Actualizando…" : "No se pudo actualizar.");
  } catch (e) {
    // Si la actualización ya arrancó, el cierre del proceso puede cortar
    // esta misma petición antes de que llegue la respuesta — no es un error.
    status.textContent = "Actualizando y reiniciando la aplicación…";
  }
}

// ============================== PRUEBAS ==============================
// Tests manuales de hardware: no hay una fuente de datos por WMI para
// confirmar que una tecla, botón, parlante, micrófono o cámara realmente
// funcionan — el técnico tiene que probarlos él mismo, y esta sección le da
// un lugar ordenado para hacerlo sin salir de la app.

const KEYBOARD_LAYOUT = [
  ["Escape", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"],
  ["Backquote", "Digit1", "Digit2", "Digit3", "Digit4", "Digit5", "Digit6", "Digit7", "Digit8", "Digit9", "Digit0", "Minus", "Equal", "Backspace"],
  // Sin "Backslash": en el teclado ISO latinoamericano esa posición no
  // existe como tecla separada — la ocupa el Enter, más alto y en forma de L.
  ["Tab", "KeyQ", "KeyW", "KeyE", "KeyR", "KeyT", "KeyY", "KeyU", "KeyI", "KeyO", "KeyP", "BracketLeft", "BracketRight"],
  ["CapsLock", "KeyA", "KeyS", "KeyD", "KeyF", "KeyG", "KeyH", "KeyJ", "KeyK", "KeyL", "Semicolon", "Quote", "Enter"],
  ["ShiftLeft", "IntlBackslash", "KeyZ", "KeyX", "KeyC", "KeyV", "KeyB", "KeyN", "KeyM", "Comma", "Period", "Slash", "ShiftRight"],
  ["ControlLeft", "MetaLeft", "AltLeft", "Space", "AltRight", "ControlRight", "ArrowLeft", "ArrowUp", "ArrowDown", "ArrowRight"],
];
// KeyboardEvent.code identifica la posición física de la tecla, no el
// carácter impreso — por eso el mismo "Semicolon" es ";" en teclado US pero
// "Ñ" en el teclado ISO latinoamericano que se usa en Chile. Los labels acá
// reflejan lo que un técnico chileno ve impreso en su propio teclado.
const KEY_LABELS = {
  Escape: "Esc", Backquote: "|°", Minus: "'?", Equal: "¿¡", Backspace: "⌫",
  Tab: "Tab", BracketLeft: "´", BracketRight: "+*",
  CapsLock: "Caps", Semicolon: "Ñ", Quote: "{[", Enter: "Enter",
  ShiftLeft: "Shift", ShiftRight: "Shift", IntlBackslash: "<>", Comma: ",;", Period: ".:", Slash: "-_",
  ControlLeft: "Ctrl", ControlRight: "Ctrl", MetaLeft: "Win", AltLeft: "Alt", AltRight: "Alt Gr",
  Space: "␣", ArrowLeft: "←", ArrowUp: "↑", ArrowDown: "↓", ArrowRight: "→",
};
function keyLabel(code) {
  if (KEY_LABELS[code]) return KEY_LABELS[code];
  if (code.startsWith("Digit")) return code.slice(5);
  if (code.startsWith("Key")) return code.slice(3);
  return code;
}

function buildKeyboardTest() {
  const container = document.getElementById("test-keyboard");
  container.innerHTML = "";
  KEYBOARD_LAYOUT.forEach((row) => {
    const rowEl = document.createElement("div");
    rowEl.className = "test-keyboard-row";
    row.forEach((code) => {
      const key = document.createElement("div");
      key.className = "test-key";
      key.dataset.code = code;
      key.textContent = keyLabel(code);
      rowEl.appendChild(key);
    });
    container.appendChild(rowEl);
  });
}

document.addEventListener("keydown", (e) => {
  if (document.getElementById("tab-pruebas").hidden) return;
  const key = document.querySelector(`#test-keyboard .test-key[data-code="${e.code}"]`);
  if (key) {
    e.preventDefault();
    key.classList.add("pressed");
  }
});

document.getElementById("test-keyboard-reset").addEventListener("click", () => {
  document.querySelectorAll("#test-keyboard .test-key.pressed").forEach((k) => k.classList.remove("pressed"));
});

// ---- Mouse ----
document.querySelectorAll(".test-mouse-zone").forEach((zone) => {
  zone.addEventListener("mousedown", (e) => {
    const want = zone.dataset.btn;
    const clicked = e.button === 0 ? "left" : e.button === 1 ? "middle" : e.button === 2 ? "right" : null;
    if (clicked === want) zone.classList.add("hit");
  });
  zone.addEventListener("contextmenu", (e) => e.preventDefault());
  zone.addEventListener("wheel", (e) => {
    const want = zone.dataset.btn;
    if ((e.deltaY < 0 && want === "scrollup") || (e.deltaY > 0 && want === "scrolldown")) {
      zone.classList.add("hit");
    }
  });
});
document.getElementById("test-mouse-reset").addEventListener("click", () => {
  document.querySelectorAll(".test-mouse-zone.hit").forEach((z) => z.classList.remove("hit"));
});

// ---- Parlantes ----
function playSpeakerTest(channel) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const osc = ctx.createOscillator();
  const panner = ctx.createStereoPanner();
  const gain = ctx.createGain();
  osc.frequency.value = 440;
  panner.pan.value = channel === "left" ? -1 : channel === "right" ? 1 : 0;
  gain.gain.value = 0.25;
  osc.connect(gain).connect(panner).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.6);
  osc.onended = () => ctx.close();
}
document.querySelectorAll("[data-speaker]").forEach((btn) => {
  btn.addEventListener("click", () => playSpeakerTest(btn.dataset.speaker));
});

// ---- Micrófono ----
let micStream = null;
let micAnimationId = null;

async function startMicTest() {
  const status = document.getElementById("test-mic-status");
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    status.textContent = "No se pudo acceder al micrófono (¿permiso denegado o no hay uno conectado?).";
    return;
  }
  status.textContent = "Escuchando…";
  document.getElementById("test-mic-btn").textContent = "Detener prueba";
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const source = ctx.createMediaStreamSource(micStream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);
  const data = new Uint8Array(analyser.frequencyBinCount);
  const bar = document.getElementById("test-mic-level");
  function tick() {
    analyser.getByteFrequencyData(data);
    const avg = data.reduce((a, b) => a + b, 0) / data.length;
    bar.style.width = `${Math.min(100, Math.round((avg / 255) * 220))}%`;
    micAnimationId = requestAnimationFrame(tick);
  }
  tick();
  micStream._audioCtx = ctx;
}

function stopMicTest() {
  if (micAnimationId) cancelAnimationFrame(micAnimationId);
  micAnimationId = null;
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    if (micStream._audioCtx) micStream._audioCtx.close();
    micStream = null;
  }
  document.getElementById("test-mic-level").style.width = "0%";
  document.getElementById("test-mic-status").textContent = "";
  document.getElementById("test-mic-btn").textContent = "Iniciar prueba";
}

document.getElementById("test-mic-btn").addEventListener("click", () => {
  if (micStream) stopMicTest();
  else startMicTest();
});

// ---- Cámara ----
let cameraStream = null;

async function startCameraTest() {
  const status = document.getElementById("test-camera-status");
  const video = document.getElementById("test-camera-video");
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
  } catch (e) {
    status.textContent = "No se pudo acceder a la cámara (¿permiso denegado o no hay una conectada?).";
    return;
  }
  video.srcObject = cameraStream;
  video.hidden = false;
  status.textContent = "";
  document.getElementById("test-camera-btn").textContent = "Detener prueba";
}

function stopCameraTest() {
  const video = document.getElementById("test-camera-video");
  if (cameraStream) {
    cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
  }
  video.srcObject = null;
  video.hidden = true;
  document.getElementById("test-camera-status").textContent = "";
  document.getElementById("test-camera-btn").textContent = "Iniciar prueba";
}

document.getElementById("test-camera-btn").addEventListener("click", () => {
  if (cameraStream) stopCameraTest();
  else startCameraTest();
});

// ---- Pantalla (píxeles muertos) ----
const SCREEN_TEST_COLORS = ["#ff0000", "#00ff00", "#0000ff", "#ffffff", "#000000"];
let screenTestIndex = 0;

function showScreenTestColor() {
  document.getElementById("test-screen-overlay").style.background = SCREEN_TEST_COLORS[screenTestIndex];
}
function advanceScreenTest() {
  screenTestIndex++;
  if (screenTestIndex >= SCREEN_TEST_COLORS.length) {
    closeScreenTest();
    return;
  }
  showScreenTestColor();
}
function closeScreenTest() {
  document.getElementById("test-screen-overlay").hidden = true;
  document.removeEventListener("keydown", handleScreenTestKey);
}
function handleScreenTestKey(e) {
  if (e.key === "Escape") closeScreenTest();
  else advanceScreenTest();
}
document.getElementById("test-screen-overlay").addEventListener("click", advanceScreenTest);

document.getElementById("test-screen-btn").addEventListener("click", () => {
  screenTestIndex = 0;
  document.getElementById("test-screen-overlay").hidden = false;
  showScreenTestColor();
  document.removeEventListener("keydown", handleScreenTestKey);
  document.addEventListener("keydown", handleScreenTestKey);
});

buildKeyboardTest();

document.getElementById("update-check-btn").addEventListener("click", checkForUpdate);
document.getElementById("scan-btn").addEventListener("click", runScan);
document.getElementById("monitor-btn").addEventListener("click", startMonitor);
document.getElementById("save-history-btn").addEventListener("click", saveToHistory);
document.getElementById("view-history-btn").addEventListener("click", viewHistory);
document.getElementById("export-html-btn").addEventListener("click", exportReportHtml);
document.getElementById("sfc-btn").addEventListener("click", runSfc);
document.getElementById("export-btn").addEventListener("click", exportReport);
document.getElementById("disks-load-btn").addEventListener("click", loadDisks);
document.getElementById("restore-point-btn").addEventListener("click", createRestorePoint);
document.getElementById("scripts-reload-btn").addEventListener("click", loadScripts);
document.getElementById("export-png-btn").addEventListener("click", exportReportPng);
document.getElementById("inventory-view-btn").addEventListener("click", viewInventory);
document.getElementById("startup-reload-btn").addEventListener("click", loadStartupItems);
document.getElementById("drivers-backup-btn").addEventListener("click", backupDrivers);
document.getElementById("visit-technician").addEventListener("change", handleTechnicianChange);
document.getElementById("nas-save-path-btn").addEventListener("click", saveNasPath);
document.getElementById("nas-credentials-btn").addEventListener("click", openNasCredentialsForm);
document.getElementById("nas-upload-btn").addEventListener("click", uploadReportToNas);
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

document.getElementById("specs-export-btn").addEventListener("click", exportSpecsPng);

const filterOnlyProblems = document.getElementById("filter-only-problems");
filterOnlyProblems.addEventListener("change", () => {
  document.getElementById("results").classList.toggle("hide-ok", filterOnlyProblems.checked);
  try {
    localStorage.setItem("diagfix_hide_ok", filterOnlyProblems.checked ? "1" : "0");
  } catch (e) {
    // Sin localStorage disponible; el filtro sigue funcionando, solo no se recuerda.
  }
});
try {
  if (localStorage.getItem("diagfix_hide_ok") === "1") {
    filterOnlyProblems.checked = true;
    document.getElementById("results").classList.add("hide-ok");
  }
} catch (e) {
  // Ignorar: preferencia no recordada entre sesiones.
}

loadSystemInfo();
loadIdentity();
loadWin11Readiness();
loadActionsCatalog();
loadScripts();
loadStartupItems();
loadTechnicians();
loadNasSettings();
runScan();
