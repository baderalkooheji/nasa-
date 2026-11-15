/* Marswalk AI - embedded mission-control panel for the Marswalk page (index.html).
 *
 * Loaded by the Marswalk page from the assistant server (http://127.0.0.1:8095/embed/marswalk-embed.js) after js/map.js.
 * It talks to the assistant backend (/chat), sends the page state with every message (view, selected point, routes A/B,
 * visible layers, layer catalogue, journey) and executes the map actions the assistant returns, reporting each outcome
 * back (/actions/result). An action is shown as done only after the page has actually executed it.
 *
 * Page APIs used (all exist in the Marswalk code):
 *   map, AppState, nasaLayers, overlays, NASA, setState, drawRoute, select   (js/map.js, js/nasa-config.js: top-level bindings)
 *   window.setView (js/integration.js) · window.Mars3D (3D globe) · window.MarswalkJourney (js/journey.js)
 *   window.MarswalkPresentation (js/presentation.js) · window.MarswalkLog (js/map.js, mission log on /api/records)
 * All DOM ids/classes are prefixed "mwx-".
 */
(function () {
  "use strict";
  const script = document.currentScript;
  const API = ((script && script.dataset.api) || "http://127.0.0.1:8095").replace(/\/$/, "");
  const g = (name) => { try { return eval(name); } catch { return undefined; } };   // top-level const/let of classic scripts
  const lmap = window.map;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const fmtM = (m) => (m == null ? "–" : m >= 1000 ? (m / 1000).toFixed(2) + " km" : Math.round(m) + " m");
  const store = { get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } } };
  const TOOL_LABEL = {
    get_current_mission_state: "Reading mission state", generate_route: "Planning route on the DEM", compare_routes: "Comparing routes",
    display_route_on_map: "Route to map", analyse_terrain: "Analysing terrain", calculate_slope: "Calculating slope",
    estimate_eva: "EVA time budget", analyse_environment: "Environment & space weather", search_knowledge_base: "Searching knowledge base",
    retrieve_mission_information: "Mission information", highlight_map_location: "Marking location", zoom_map: "Moving map",
    change_map_layer: "Map layer", navigate_application: "Navigation", start_route_journey: "Route journey",
    save_to_mission_log: "Saving to mission log", get_mission_log: "Reading mission log",
  };

  // ================================================================== page state sent with every message
  function layerCatalogue() {
    const NASA = g("NASA") || { basemaps: [], overlays: [] };
    const kw = { viking: ["viking", "global imagery", "colour mosaic"], ctx_regional: ["ctx regional", "regional ctx", "ctx"],
      ctx_trn: ["ctx orthomosaic", "trn"], hirise: ["hirise", "high resolution"], mola: ["mola", "elevation", "relief", "topography"],
      themis: ["themis", "infrared", "thermal"] };
    const out = [...(NASA.basemaps || []), ...(NASA.overlays || [])].map((c) => ({ id: c.id, label: c.name, keywords: kw[c.id] || [] }));
    const st = g("AppState");
    ((st && st.layers && st.layers.layers) || []).filter((l) => l.available !== false)
      .forEach((l) => out.push({ id: l.id, label: l.name, keywords: [l.id.replace(/_/g, " ")] }));
    return out;
  }
  const leafletLayer = (id) => (g("nasaLayers") || {})[id] || (g("overlays") || {})[id];
  const is3d = () => { const v = document.getElementById("view3d"); return v ? !v.hidden : false; };

  function routeForContext(r) {
    if (!r) return null;
    const pts = (r.waypoints && r.waypoints.length ? r.waypoints : (r.profile || [])).map((p) => ({ lat: p.lat, lon: p.lon }));
    const step = Math.max(1, Math.ceil(pts.length / 300));
    const wps = pts.filter((_, i) => i % step === 0);
    if (pts.length && wps[wps.length - 1] !== pts[pts.length - 1]) wps.push(pts[pts.length - 1]);
    return { label: r.route_id === "A" ? "Hand-drawn Route A" : "Route B", distance_m: r.distance_m, max_slope_deg: r.max_slope_deg,
             avg_slope_deg: r.avg_slope_deg, elevation_gain_m: r.elevation_gain_m, elevation_loss_m: r.elevation_loss_m,
             over_limit: r.over_limit, data_coverage_pct: r.data_coverage_pct, source_dataset: r.source_dataset, limit_deg: r.limit_deg,
             waypoints: wps };
  }

  function getContext() {
    const st = g("AppState") || {};
    const nl = g("nasaLayers") || {}, ov = g("overlays") || {};
    const sel = st.selected;
    const J = window.MarswalkJourney && window.MarswalkJourney.state;
    return {
      view: is3d() ? "3d" : "2d",
      selected: sel ? { lat: sel.lat, lon: sel.lon, name: sel.name || null, elevation_m: sel.elevation_m ?? null, slope_deg: sel.slope_deg ?? null,
                        source_dataset: sel.source_dataset || null } : null,
      routes: Object.fromEntries([["A", routeForContext(st.routeA)], ["B", routeForContext(st.routeB)]].filter(([, v]) => v)),
      visible_layers: [...Object.keys(nl).filter((id) => lmap && lmap.hasLayer(nl[id])), ...Object.keys(ov).filter((id) => lmap && lmap.hasLayer(ov[id]))],
      layers: layerCatalogue(),
      map: lmap ? { lat: +lmap.getCenter().lat.toFixed(6), lon: +lmap.getCenter().lng.toFixed(6), zoom: lmap.getZoom() } : null,
      journey: J && J.path ? { route: J.key, status: J.status, progress_pct: Math.round((J.pos ? J.pos.fraction : 0) * 100) } : null,
    };
  }

  // ================================================================== map / app actions (executed here, then reported)
  const marks = lmap && window.L ? L.layerGroup().addTo(lmap) : null;
  const ACTIONS = {
    async display_route(a) {
      const setState = g("setState"), drawRoute = g("drawRoute");
      if (typeof setState !== "function" || typeof drawRoute !== "function") throw new Error("Marswalk route drawing not available");
      const analysis = { ...a.analysis, route_id: "B" };
      if (is3d() && window.setView) window.setView("2d");
      drawRoute("B", analysis);
      setState({ routeB: analysis });                      // profile chart, compare table, 3D route and journey follow
      if (a.fit !== false && lmap) {
        const pts = (analysis.waypoints || analysis.profile || []).map((p) => [p.lat, p.lon]);
        if (pts.length) lmap.fitBounds(L.latLngBounds(pts).pad(0.25));
      }
      return `${a.route_id} shown as Route B`;
    },
    async highlight_location(a) {
      if (!marks) throw new Error("2D map not available");
      const color = a.style === "rover" ? "#B9A8FF" : "#FF8C00";
      L.circleMarker([a.lat, a.lon], { radius: 10, color, weight: 3, fillOpacity: 0.25 })
        .bindTooltip(esc(a.label || "Marswalk AI"), { permanent: true, direction: "top" }).addTo(marks);
      if (is3d() && window.Mars3D && window.Mars3D.isInitialized()) { await window.Mars3D.goToLocation(a.lat, a.lon, { select: false, altitude: 3000 }); return "marked (3D camera moved)"; }
      lmap.setView([a.lat, a.lon], Math.max(lmap.getZoom(), a.zoom || 15));
      return "marked on the map";
    },
    async zoom_to(a) {
      if (is3d() && window.Mars3D && window.Mars3D.isInitialized()) {
        const alt = a.zoom <= 11 ? 120000 : a.zoom <= 13 ? 20000 : 4000;
        await window.Mars3D.goToLocation(a.lat, a.lon, { select: false, altitude: alt });
        return "3D camera moved";
      }
      if (!lmap) throw new Error("2D map not available");
      lmap.setView([a.lat, a.lon], a.zoom || 16);
      return "map moved";
    },
    async fit_bounds(a) {
      if (!lmap) throw new Error("2D map not available");
      if (is3d() && window.setView) window.setView("2d");
      lmap.fitBounds(L.latLngBounds(a.bounds).pad(0.2));
      return "map fitted";
    },
    async change_layer(a) {
      const l = leafletLayer(a.layer_id);
      if (!l) throw new Error(`layer '${a.layer_id}' not found on the Marswalk map`);
      a.visible ? l.addTo(lmap) : lmap.removeLayer(l);
      const cat = layerCatalogue().find((x) => x.id === a.layer_id);           // keep the Layers-tab checkbox in step
      document.querySelectorAll("#basemaps .layer, #layers .layer").forEach((row) => {
        const cb = row.querySelector("input[type=checkbox]");
        if (cb && cat && row.textContent.includes(cat.label) && cb.checked !== a.visible) cb.checked = a.visible;
      });
      return `${a.layer_id} ${a.visible ? "on" : "off"}`;
    },
    async navigate(a) {
      const t = a.target;
      if (t === "2d" || t === "3d") { if (!window.setView) throw new Error("view switching not available"); window.setView(t); return `${t.toUpperCase()} view`; }
      if (t === "mission_log") { ui.showRecords(true); return "mission log opened"; }
      if (t === "presentation" || t === "exit_presentation") {
        const P = window.MarswalkPresentation;
        if (!P) throw new Error("presentation mode not available");
        t === "presentation" ? P.enter() : P.exit();
        return t === "presentation" ? "presentation mode on" : "presentation mode off";
      }
      const tab = document.querySelector(`.tab[data-tab="${t === "environment" ? "env" : t}"]`);
      if (!tab) throw new Error(`section ${t} not found`);
      if (is3d() && window.setView) window.setView("2d");
      tab.click();
      return `${t} tab opened`;
    },
    async start_journey(a) {
      const J = window.MarswalkJourney;
      if (!J) throw new Error("journey feature not available");
      if (!J.start(a.route || "B")) throw new Error(`Route ${a.route} is not on the map`);
      return `journey along Route ${a.route} started`;
    },
    async refresh_log() { if (!ui.records.hidden) await ui.loadRecords(); return "log refreshed"; },
  };

  // ================================================================== panel
  const root = el("div", "mwx-root");
  root.innerHTML = `
    <button class="mwx-orb" type="button" aria-label="Open Marswalk AI" title="Marswalk AI"><span class="mwx-orb-core"></span><span class="mwx-orb-ring"></span></button>
    <section class="mwx-panel" role="dialog" aria-label="Marswalk AI mission assistant" hidden>
      <header class="mwx-head" title="Drag to move · double-click to reset">
        <div class="mwx-title"><span class="mwx-dot"></span><b>MARSWALK AI</b><span class="mwx-sub" data-r="status">connecting…</span></div>
        <div class="mwx-hbtns">
          <button type="button" class="mwx-ib mwx-logbtn" data-r="logbtn" title="Mission log" aria-pressed="false">☰ Log</button>
          <button type="button" class="mwx-ib" data-r="ctxbtn" title="Mission context" aria-label="Mission context">◎</button>
          <button type="button" class="mwx-ib" data-r="reset" title="New session" aria-label="New session">↺</button>
          <button type="button" class="mwx-ib" data-r="close" title="Close (Esc)" aria-label="Close">✕</button>
        </div>
      </header>
      <div class="mwx-ctx" data-r="ctx"></div>
      <div class="mwx-chat" data-r="chat" aria-live="polite"></div>
      <div class="mwx-records" data-r="records" hidden>
        <div class="mwx-rec-save"><div data-r="recsel"></div><button type="button" class="mwx-btn mwx-primary" data-r="recsave">Save selected location</button></div>
        <input type="search" class="mwx-input" data-r="recq" placeholder="Search the mission log" aria-label="Search the mission log">
        <ul class="mwx-rec-list" data-r="reclist"></ul>
        <button type="button" class="mwx-btn" data-r="recback">← Back to chat</button>
      </div>
      <div class="mwx-sugg" data-r="sugg"></div>
      <form class="mwx-form" data-r="form">
        <textarea class="mwx-input" data-r="input" rows="1" placeholder="Command Marswalk AI…" aria-label="Message to Marswalk AI" maxlength="2000"></textarea>
        <button type="submit" class="mwx-send" data-r="send" aria-label="Send">➤</button>
      </form>
    </section>`;
  document.body.appendChild(root);
  const $ = (r) => root.querySelector(`[data-r="${r}"]`);
  const panel = root.querySelector(".mwx-panel"), orb = root.querySelector(".mwx-orb"), head = root.querySelector(".mwx-head");

  const state = { conv: store.get("mwx.conversation"), busy: false, health: null };

  const ui = {
    records: $("records"),
    toggle(open) {
      if (open && panel.hidden) place();
      panel.hidden = !open;
      root.classList.toggle("mwx-open", open);
      if (open) { renderCtx(); setTimeout(() => $("input").focus(), 30); }
    },
    showRecords(show) {
      ui.toggle(true);
      ui.records.hidden = !show;
      [$("chat"), $("sugg"), $("form")].forEach((n) => n.classList.toggle("mwx-gone", show));
      $("logbtn").classList.toggle("mwx-on", show);
      $("logbtn").setAttribute("aria-pressed", String(show));
      if (show) { refreshSelection(); ui.loadRecords(); }
    },
    async loadRecords() {
      const L_ = window.MarswalkLog, list = $("reclist");
      if (!L_) { list.innerHTML = `<li class="mwx-muted">Mission log not available on this page.</li>`; return; }
      try {
        const rows = await L_.list($("recq").value.trim());
        ui._rows = rows;
        list.innerHTML = rows.length ? rows.map((r, i) => `<li data-i="${i}" title="Show on the map"><div class="mwx-rec-h"><b>${esc(r.label)}</b>
            <span class="mwx-tag">${esc(r.analysis_type)}</span></div><div>${esc(r.result)}</div><div class="mwx-muted">${esc(r.created_at || "")} · ${esc(r.status)}</div></li>`).join("")
          : `<li class="mwx-muted">${$("recq").value.trim() ? "No matching entries." : "No entries yet. Select a point on the map and press Save."}</li>`;
      } catch (e) { list.innerHTML = `<li class="mwx-err">Mission log unavailable (${esc(e.message)}).</li>`; }
    },
  };

  function refreshSelection() {
    const s = window.MarswalkLog && window.MarswalkLog.selected();
    $("recsel").innerHTML = s ? `<b>${esc(s.label)}</b><div class="mwx-muted">${(+s.lat).toFixed(5)}°N ${(+s.lon).toFixed(5)}°E · ${s.elevation_m != null ? esc(s.elevation_m) + " m" : "no elevation"} · slope ${s.slope_deg != null ? esc(s.slope_deg) + "°" : "n/a"}</div>`
      : `<span class="mwx-muted">Click a point on the map (or a stop), then save it here.</span>`;
    $("recsave").disabled = !s;
    renderCtx();
  }

  function renderCtx() {
    const c = getContext(), h = state.health;
    const chips = [
      `<span class="mwx-chip">${c.view.toUpperCase()}</span>`,
      c.selected ? `<span class="mwx-chip mwx-chip-on" title="Selected point">◉ ${esc(c.selected.name || `${(+c.selected.lat).toFixed(4)}, ${(+c.selected.lon).toFixed(4)}`)}</span>`
                 : `<span class="mwx-chip" title="Nothing selected">◉ no selection</span>`,
      ...Object.entries(c.routes).map(([k, r]) => `<span class="mwx-chip mwx-chip-on">Route ${k} · ${fmtM(r.distance_m)}</span>`),
      c.journey ? `<span class="mwx-chip mwx-chip-on">Journey ${esc(c.journey.status)} ${c.journey.progress_pct}%</span>` : "",
      h ? `<span class="mwx-chip ${h.model_installed ? "" : "mwx-chip-warn"}" title="Local model (Ollama)">⬡ ${esc(h.model)}${h.model_installed ? "" : " · not ready"}</span>` : "",
      h ? `<span class="mwx-chip ${h.marswalk_backend ? "" : "mwx-chip-warn"}" title="Marswalk data backend">${h.marswalk_backend ? "DEM data ✓" : "DEM data offline"}</span>` : "",
      h ? `<span class="mwx-chip" title="Knowledge base">${h.knowledge_documents} docs</span>` : "",
    ];
    $("ctx").innerHTML = chips.join("");
  }

  // ---- drag / place (same behaviour as the journey panel)
  const GEOM = "mwx.geometry";
  function place(reset) {
    const gm = reset ? null : store.get(GEOM);
    const w = Math.min(gm ? gm.w : 440, innerWidth - 16), h = Math.min(gm ? gm.h : Math.min(640, innerHeight * 0.78), innerHeight - 16);
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    Object.assign(panel.style, { width: w + "px", height: h + "px", left: clamp(gm ? gm.x : innerWidth - w - 20, 8, innerWidth - w - 8) + "px",
                                 top: clamp(gm ? gm.y : innerHeight - h - 20, 8, innerHeight - h - 8) + "px" });
  }
  const saveGeom = () => { if (panel.hidden) return; const r = panel.getBoundingClientRect(); store.set(GEOM, { x: r.left, y: r.top, w: r.width, h: r.height }); };
  let drag = null;
  head.addEventListener("pointerdown", (e) => { if (e.button || e.target.closest("button")) return; const r = panel.getBoundingClientRect(); drag = { dx: e.clientX - r.left, dy: e.clientY - r.top }; head.setPointerCapture(e.pointerId); });
  head.addEventListener("pointermove", (e) => { if (!drag) return; const r = panel.getBoundingClientRect();
    panel.style.left = Math.max(8, Math.min(innerWidth - r.width - 8, e.clientX - drag.dx)) + "px";
    panel.style.top = Math.max(8, Math.min(innerHeight - r.height - 8, e.clientY - drag.dy)) + "px"; });
  const endDrag = () => { if (drag) { drag = null; saveGeom(); } };
  head.addEventListener("pointerup", endDrag); head.addEventListener("pointercancel", endDrag);
  head.addEventListener("dblclick", (e) => { if (!e.target.closest("button")) { place(true); saveGeom(); } });
  if (window.ResizeObserver) { let t = 0; new ResizeObserver(() => { clearTimeout(t); t = setTimeout(saveGeom, 300); }).observe(panel); }

  // ---- chat rendering
  const chat = $("chat");
  const scroll = () => { chat.scrollTop = chat.scrollHeight; };
  function richText(t) {
    return esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\[(DOC-\d+)\]/g, '<span class="mwx-cite">[$1]</span>')
      .split("\n").map((l) => (/^\s*[•\-*]\s+/.test(l) ? `<div class="mwx-bullet">${l.replace(/^\s*[•\-*]\s+/, "")}</div>` : l ? `<p>${l}</p>` : "")).join("");
  }
  function addMsg(role, text) {
    const m = el("div", `mwx-msg mwx-${role}`, `<div class="mwx-who">${role === "user" ? "COMMAND" : role === "ai" ? "MARSWALK AI" : "SYSTEM"}</div><div class="mwx-text">${richText(text)}</div>`);
    chat.appendChild(m); scroll(); return m;
  }
  function suggest(list) {
    const s = $("sugg"); s.innerHTML = "";
    list.forEach((q) => { const b = el("button", "mwx-sbtn", esc(q)); b.type = "button"; b.onclick = () => ask(q); s.appendChild(b); });
  }
  function greet() {
    addMsg("ai", "Marswalk AI online. I plan and compare walking routes on NASA's Jezero elevation data, analyse terrain, estimate EVA time, "
      + "answer Mars questions from the mission knowledge base and control the map. Every number I give comes from a tool; I say when data is missing.");
    suggest(["Plan a route from the habitat to the delta front", "Give me three route options to stop 3 and compare them",
             "Analyse the terrain at the selected point", "Where is Perseverance?", "Can astronauts make oxygen on Mars?",
             "Save this location to the log", "Zoom to Jezero crater"]);
  }

  function toolBlock(r) {
    const d = r.data || {};
    let detail = "";
    if (r.success && r.name === "generate_route") detail = (d.routes || []).map((x) => `${x.route_id} ${esc(x.label)}: ${fmtM(x.distance_m)}, max ${x.max_slope_deg ?? "–"}°, +${x.elevation_gain_m ?? "–"} m`).join("<br>");
    else if (r.success && r.name === "analyse_terrain" && d.data_available !== false) detail = `${esc(d.place)} · ${d.elevation_m ?? "–"} m · slope P90 ${d.slope_p90_deg ?? "–"}° · ${esc(d.difficulty || "")}`;
    else if (r.success && r.name === "estimate_eva") detail = `${d.route_id}: ${d.total_min} min total · ${esc(d.status)}`;
    else if (r.success && (d.results || []).length) detail = d.results.map((x) => `[${x.cite}] ${esc(x.title || "")}${x.section ? " · " + esc(x.section) : ""}`).join("<br>");
    const b = el("details", `mwx-tool ${r.success ? "" : "mwx-tool-bad"}`);
    b.innerHTML = `<summary><span class="mwx-ti">${r.success ? "✓" : "✕"}</span><b>${esc(TOOL_LABEL[r.name] || r.name)}</b> <code>${esc(r.name)}</code></summary>
      ${detail ? `<div class="mwx-tool-d">${detail}</div>` : ""}${r.error ? `<div class="mwx-err">${esc(r.error)}</div>` : ""}
      <pre class="mwx-pre">${esc(JSON.stringify(r.arguments || {}, null, 1))}</pre>`;
    return b;
  }

  function sourcesBlock(results) {
    const seen = new Map();
    results.forEach((r) => (r.success && r.data && r.data.results || []).forEach((x) => { if (!seen.has(x.cite)) seen.set(x.cite, x); }));
    if (!seen.size) return null;
    const d = el("details", "mwx-sources");
    d.innerHTML = `<summary>Sources (${seen.size})</summary>` + [...seen.values()].map((x) =>
      `<div class="mwx-src"><b>[${esc(x.cite)}] ${esc(x.title || "")}</b>${x.section ? ` · ${esc(x.section)}` : ""}
       ${x.source_url ? ` <a href="${esc(x.source_url)}" target="_blank" rel="noopener">source</a>` : ""}<div class="mwx-muted">${esc((x.text || "").slice(0, 220))}…</div></div>`).join("");
    return d;
  }

  async function runAction(a, box) {
    const chip = el("div", "mwx-act mwx-pending", `<span class="mwx-spin"></span>${esc(a.summary)}`);
    box.appendChild(chip);
    let ok = false, detail;
    try { detail = await (ACTIONS[a.type] || (async () => { throw new Error(`unknown action ${a.type}`); }))(a); ok = true; }
    catch (e) { detail = e.message; }
    chip.className = `mwx-act ${ok ? "mwx-done" : "mwx-failed"}`;
    chip.innerHTML = `${ok ? "✓" : "✕"} ${esc(a.summary)}${ok ? "" : ` <span class="mwx-muted">(${esc(detail)})</span>`}`;
    try { await fetch(API + "/actions/result", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: state.conv, action_id: a.action_id, ok, detail: String(detail || "").slice(0, 300), type: a.type }) }); }
    catch { /* acknowledgement is best effort */ }
    renderCtx();
  }

  async function ask(text) {
    if (state.busy || !text.trim()) return;
    ui.toggle(true);
    if (!ui.records.hidden) ui.showRecords(false);
    addMsg("user", text);
    suggest([]);
    state.busy = true; root.classList.add("mwx-busy"); $("send").disabled = true;
    const wait = el("div", "mwx-msg mwx-ai mwx-thinking", `<div class="mwx-who">MARSWALK AI</div><div class="mwx-text"><span class="mwx-pulse"></span><span class="mwx-pulse"></span><span class="mwx-pulse"></span> <span class="mwx-muted" data-t>Thinking… 0 s</span></div>`);
    chat.appendChild(wait); scroll();
    const t0 = Date.now();
    const tick = setInterval(() => { const s = wait.querySelector("[data-t]"); if (s) s.textContent = `Thinking… ${Math.round((Date.now() - t0) / 1000)} s (local model on CPU)`; }, 1000);
    try {
      const r = await fetch(API + "/chat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: state.conv || undefined, client_context: getContext() }) });
      const j = await r.json().catch(() => null);
      if (!r.ok || !j) {
        if (r.status === 404 && state.conv) { state.conv = null; store.set("mwx.conversation", null); }
        throw new Error((j && j.detail) || `HTTP ${r.status}`);
      }
      state.conv = j.conversation_id; store.set("mwx.conversation", j.conversation_id);
      wait.remove();
      const m = addMsg("ai", j.message || "(no answer)");
      if (j.metadata && j.metadata.error) m.classList.add("mwx-warn-msg");
      const results = (j.tool_calls || []).map((c, i) => ({ name: c.name, arguments: c.arguments, ...(j.tool_results || [])[i] }));
      if (results.length) { const tb = el("div", "mwx-tools"); results.forEach((x) => tb.appendChild(toolBlock(x))); m.appendChild(tb); }
      const src = sourcesBlock(results); if (src) m.appendChild(src);
      if (j.metadata) m.appendChild(el("div", "mwx-meta", `${esc(j.metadata.model || "")} · ${((j.metadata.timing_ms || 0) / 1000).toFixed(1)} s${j.metadata.tool_rounds ? ` · ${j.metadata.tool_rounds} tool round${j.metadata.tool_rounds > 1 ? "s" : ""}` : ""}`));
      if ((j.actions || []).length) { const box = el("div", "mwx-actions"); m.appendChild(box); for (const a of j.actions) await runAction(a, box); }
      scroll();
    } catch (e) {
      wait.remove();
      addMsg("sys", `Cannot reach the Marswalk AI backend (${e.message}). Start it: marswalk-assistant\\backend → python main.py`);
    } finally {
      clearInterval(tick);
      state.busy = false; root.classList.remove("mwx-busy"); $("send").disabled = false;
      renderCtx();
    }
  }

  async function health() {
    try {
      state.health = await (await fetch(API + "/health")).json();
      $("status").textContent = state.health.model_installed ? `${state.health.model} · local` : "model not installed";
      root.classList.toggle("mwx-offline", !state.health.model_installed);
    } catch {
      state.health = null; $("status").textContent = "backend offline"; root.classList.add("mwx-offline");
    }
    renderCtx();
  }

  async function restore() {
    if (!state.conv) { greet(); return; }
    try {
      const r = await fetch(`${API}/conversations/${state.conv}/messages?limit=20`);
      if (!r.ok) throw new Error();
      const msgs = await r.json();
      if (!msgs.length) { greet(); return; }
      addMsg("sys", "Previous session restored.");
      msgs.slice(-10).forEach((m) => addMsg(m.role === "user" ? "user" : "ai", m.content));
    } catch { state.conv = null; store.set("mwx.conversation", null); greet(); }
  }

  // ---- wiring
  orb.onclick = () => ui.toggle(true);
  $("close").onclick = () => ui.toggle(false);
  $("ctxbtn").onclick = () => $("ctx").classList.toggle("mwx-gone");
  $("reset").onclick = () => { state.conv = null; store.set("mwx.conversation", null); chat.innerHTML = ""; greet(); };
  $("logbtn").onclick = () => ui.showRecords(ui.records.hidden);
  $("recback").onclick = () => ui.showRecords(false);
  $("recsave").onclick = async () => {
    $("recsave").disabled = true;
    try { await window.MarswalkLog.save(); await ui.loadRecords(); const f = $("reclist").querySelector("li[data-i]"); if (f) f.classList.add("mwx-new"); }
    catch (e) { $("recsel").insertAdjacentHTML("beforeend", `<div class="mwx-err">Could not save: ${esc(e.message)}</div>`); }
    refreshSelection();
  };
  let qt = 0; $("recq").oninput = () => { clearTimeout(qt); qt = setTimeout(ui.loadRecords, 200); };
  $("reclist").onclick = (e) => { const li = e.target.closest("li[data-i]"); if (li && ui._rows && window.MarswalkLog) window.MarswalkLog.open(ui._rows[+li.dataset.i]); };
  $("form").onsubmit = (e) => { e.preventDefault(); const q = $("input").value.trim(); if (q) { $("input").value = ""; ask(q); } };
  $("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden && !document.fullscreenElement) ui.toggle(false); });
  addEventListener("resize", () => { if (!panel.hidden) place(); });
  addEventListener("appstate", (e) => { if (e.detail && e.detail.changed && e.detail.changed.some((c) => ["selected", "routeA", "routeB"].includes(c))) refreshSelection(); });
  addEventListener("marswalk:view", renderCtx);

  // One AI entry point: this orb. The old header button and drawer stay as the fallback when this server is not running.
  const oldBtn = document.getElementById("btnChat");
  if (oldBtn) { oldBtn.classList.add("mwai-replaced"); oldBtn.onclick = () => ui.toggle(true); }
  const oldDrawer = document.getElementById("chat");
  if (oldDrawer) oldDrawer.hidden = true;

  window.marswalkAssistant = { ask, toggle: ui.toggle, showRecords: ui.showRecords, getContext, actions: ACTIONS };
  health(); restore(); setInterval(health, 30000);
})();
