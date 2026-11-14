/* Standalone demo environment: a stand-in for the Marswalk map so the assistant's frontend
 * actions can be developed and demonstrated. Same CRS and NASA Mars Trek tiles as Marswalk
 * (js/nasa-config.js): EPSG4326 grid = planetocentric lat / east-positive lon.
 * When integrating, the real page provides its own bridge (see integration/marswalk-bridge.js).
 */
(function () {
  "use strict";
  const API = "";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtM = (m) => (m == null ? "–" : m >= 1000 ? (m / 1000).toFixed(2) + " km" : Math.round(m) + " m");

  // ------------------------------------------------------------------ map + layers
  const TREK = "https://trek.nasa.gov/tiles/Mars/EQ";
  const trek = (layer, ext) => `${TREK}/${layer}/1.0.0/default/default028mm/{z}/{y}/{x}.${ext}`;
  const map = L.map("map", { crs: L.CRS.EPSG4326, zoomSnap: 0.25, minZoom: 3, maxZoom: 19, center: [18.4545, 77.404], zoom: 15,
    maxBounds: [[-90, -180], [90, 180]], worldCopyJump: false });
  map.attributionControl.setPrefix(false);
  map.createPane("base").style.zIndex = 150;
  map.createPane("over").style.zIndex = 250;
  const tile = (cfg, pane) => L.tileLayer(cfg.url, { pane, bounds: L.latLngBounds(cfg.bounds), maxNativeZoom: cfg.maxNativeZoom, maxZoom: 19,
    tileSize: 256, noWrap: true, opacity: cfg.opacity ?? 1, crossOrigin: true,
    errorTileUrl: "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==" });

  const LAYERS = [
    { id: "viking", label: "Viking colour mosaic (232 m)", keywords: ["viking", "global imagery", "colour mosaic", "color mosaic"], on: true, pane: "base",
      url: trek("Mars_Viking_MDIM21_ClrMosaic_global_232m", "jpg"), maxNativeZoom: 7, bounds: [[-90, -180], [90, 180]] },
    { id: "ctx", label: "CTX orthomosaic (6 m)", keywords: ["ctx", "context camera", "ctx imagery"], on: true, pane: "base",
      url: trek("JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0", "png"), maxNativeZoom: 13, bounds: [[18.2110274, 77.1604655], [18.7211945, 77.6991776]] },
    { id: "hirise", label: "HiRISE orthomosaic (25 cm)", keywords: ["hirise", "high resolution", "high-resolution imagery", "satellite imagery", "imagery map"], on: true, pane: "base",
      url: trek("JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first_dd", "png"), maxNativeZoom: 17, bounds: [[18.3067994, 77.2229331], [18.669315, 77.583964]] },
    { id: "mola", label: "MOLA shaded elevation (terrain)", keywords: ["terrain map", "terrain layer", "terrain view", "elevation map", "elevation layer", "relief", "topography", "topographic map", "mola", "shaded relief"],
      on: false, pane: "over", opacity: 0.6, url: trek("Mars_MGS_MOLA_ClrShade_merge_global_463m", "jpg"), maxNativeZoom: 7, bounds: [[-90, -180], [90, 180]] },
    { id: "themis", label: "THEMIS day infrared", keywords: ["themis", "infrared", "thermal", "thermal infrared"], on: false, pane: "over", opacity: 0.6,
      url: trek("THEMIS_DayIR_ControlledMosaics_100m_v2_oct2018", "png"), maxNativeZoom: 9, bounds: [[-65.0006576, -180], [65.0007535, 180]] },
    { id: "traverse", label: "Perseverance localisations", keywords: ["rover path", "rover track", "traverse", "perseverance path", "rover traverse"], on: true },
    { id: "stops", label: "Mission places", keywords: ["stops", "mission stops", "places", "waypoints"], on: true },
  ];
  const layerObj = {};
  LAYERS.forEach((l) => { if (l.url) layerObj[l.id] = tile(l, l.pane); });
  layerObj.traverse = L.layerGroup();
  layerObj.stops = L.layerGroup();

  function setLayer(id, visible, opacity) {
    const l = layerObj[id];
    if (!l) throw new Error(`unknown layer ${id}`);
    visible ? l.addTo(map) : map.removeLayer(l);
    if (opacity != null && l.setOpacity) l.setOpacity(opacity);
    const cb = document.querySelector(`input[data-layer="${id}"]`);
    if (cb) cb.checked = visible;                         // keep the UI in sync with assistant actions
  }
  $("layerList").innerHTML = LAYERS.map((l) => `<label class="layer-row"><input type="checkbox" data-layer="${l.id}" ${l.on ? "checked" : ""}> ${esc(l.label)}</label>`).join("");
  document.querySelectorAll("input[data-layer]").forEach((cb) => (cb.onchange = () => setLayer(cb.dataset.layer, cb.checked)));
  LAYERS.forEach((l) => l.on && layerObj[l.id].addTo(map));

  // ------------------------------------------------------------------ mission data
  const pin = (label, cls) => L.divIcon({ className: "", iconSize: [24, 24], iconAnchor: [12, 12], html: `<div class="poi-pin ${cls}">${esc(label)}</div>` });
  fetch(API + "/api/assistant/mission").then((r) => r.json()).then((m) => {
    $("missionName").textContent = m.name;
    $("missionSub").textContent = `Status: ${m.status} (mock) · position: ${m.current_position.name}`;
    m.pois.forEach((p) => {
      const label = p.kind === "habitat" ? "H" : p.kind === "science_stop" ? p.id.replace("stop-", "") : "◆";
      L.marker([p.lat, p.lon], { icon: pin(label, p.kind), title: p.name }).addTo(layerObj.stops)
        .bindTooltip(`<b>${esc(p.name)}</b><br><span style="color:#9AA6B6">${esc(p.reason || "")}</span>`);
    });
  }).catch(() => { $("missionName").textContent = "Backend not reachable"; });
  fetch(API + "/api/assistant/rover/track").then((r) => r.json()).then((t) => {
    L.polyline(t.points.map((p) => [p[0], p[1]]), { color: "#B9A8FF", weight: 2, opacity: 0.7 }).addTo(layerObj.traverse)
      .bindTooltip("Perseverance end-of-drive localisations (NASA/JPL-Caltech MMGIS, historical snapshot)", { sticky: true });
  }).catch(() => {});

  // ------------------------------------------------------------------ selection
  let selected = null, selMarker = null;
  map.on("click", (e) => {
    selected = { lat: +e.latlng.lat.toFixed(6), lon: +e.latlng.lng.toFixed(6), name: "Selected point" };
    if (selMarker) map.removeLayer(selMarker);
    selMarker = L.circleMarker(e.latlng, { radius: 7, color: "#fff", weight: 2, fillOpacity: 0 }).addTo(map);
    $("coords").textContent = `Selected ${selected.lat.toFixed(5)}°N ${selected.lon.toFixed(5)}°E · ask "Analyze this location"`;
  });

  // ------------------------------------------------------------------ assistant overlays (routes, highlights)
  const routeLayers = {};
  const highlights = L.layerGroup().addTo(map);
  const CLASS_COLOR = { caution: "#F2C14E", over: "#F97316", nodata: "#6b7685" };

  function drawRoute(p) {
    if (routeLayers[p.route_id]) map.removeLayer(routeLayers[p.route_id]);
    const base = p.emphasis === "alternative" ? "#B9A8FF" : "#5EC8F2";
    const g = L.layerGroup();
    const pts = p.geometry, cls = p.slope_classes || [];
    // halo for readability on bright imagery
    L.polyline(pts, { color: "#071018", weight: p.emphasis === "alternative" ? 6 : 8, opacity: 0.55 }).addTo(g);
    let start = 0;
    for (let i = 1; i <= pts.length - 1; i++) {                // one polyline per run of the same slope class
      const c = cls[i - 1] || "ok";
      const next = cls[i] || "ok";
      if (i === pts.length - 1 || next !== c) {
        L.polyline(pts.slice(start, i + 1), { color: CLASS_COLOR[c] || base, weight: p.emphasis === "alternative" ? 3.5 : 5, opacity: 0.95,
          dashArray: p.emphasis === "alternative" ? "7 6" : null }).addTo(g);
        start = i;
      }
    }
    const s = p.summary || {};
    g.eachLayer((l) => l.bindTooltip(`<b>${esc(p.route_id)}</b> ${esc(p.label)}<br>${fmtM(s.distance_m)} · max ${s.max_slope_deg ?? "–"}° · ~${Math.round(s.walk_time_out_min ?? 0)} min out
      ${p.data_mode && p.data_mode !== "real" ? `<br><b style="color:#F2C14E">${esc(p.data_mode.toUpperCase())}</b>` : ""}`, { sticky: true }));
    g.addTo(map);
    routeLayers[p.route_id] = g;
    if (p.fit) map.fitBounds(L.latLngBounds(pts).pad(0.25));
    return `${p.route_id} drawn (${pts.length} points)`;
  }

  const HL_COLOR = { info: "#5EC8F2", caution: "#F97316", destination: "#F2C14E", rover: "#B9A8FF" };
  function highlight(p) {
    const color = HL_COLOR[p.style] || HL_COLOR.info;
    const m = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: `<div class="hl" style="--c:${color}"></div>` }) })
      .bindTooltip(esc(p.label || "Highlighted"), { permanent: true, direction: "top", offset: [0, -10] });
    if (highlights.getLayers().length >= 6) highlights.removeLayer(highlights.getLayers()[0]);
    highlights.addLayer(m);
    return `highlighted ${p.label || ""}`.trim();
  }

  // ------------------------------------------------------------------ sections
  const SECTIONS = [
    { id: "map", label: "Main map", keywords: ["main map", "map view", "the map"] },
    { id: "dashboard", label: "Mission dashboard", keywords: ["dashboard", "mission dashboard", "mission overview", "overview"] },
    { id: "terrain", label: "Terrain analysis panel", keywords: ["terrain analysis", "terrain panel", "analysis panel", "terrain section"] },
    { id: "environment", label: "Environment panel", keywords: ["environment panel", "environmental panel", "environment section", "weather panel", "environment tab"] },
    { id: "history", label: "Mission history", keywords: ["mission history", "history", "mission log", "log"] },
  ];
  let currentSection = "map";
  let assistant = null;
  const lastCards = {};

  async function openSection(id) {
    const sec = SECTIONS.find((s) => s.id === id);
    if (!sec) throw new Error(`unknown section ${id}`);
    currentSection = id;
    document.querySelectorAll(".sec").forEach((b) => b.classList.toggle("active", b.dataset.section === id));
    const btn = document.querySelector(`.sec[data-section="${id}"]`);
    if (btn) { btn.classList.remove("flash"); void btn.offsetWidth; btn.classList.add("flash"); }
    if (id === "map") { $("sheet").hidden = true; return "map shown"; }
    $("sheetTitle").textContent = sec.label;
    $("sheet").hidden = false;
    await renderSection(id);
    return `${sec.label} opened`;
  }
  document.querySelectorAll(".sec").forEach((b) => (b.onclick = () => openSection(b.dataset.section)));
  $("sheetClose").onclick = () => openSection("map");

  async function sessionState() {
    if (!assistant || !assistant.sessionId) return null;
    const r = await fetch(`${API}/api/assistant/sessions/${assistant.sessionId}`);
    return r.ok ? r.json() : null;
  }

  async function renderSection(id) {
    const body = $("sheetBody");
    const st = await sessionState();
    const ms = st ? st.mission_state : null;
    if (id === "dashboard") {
      const routes = st ? Object.values(st.routes) : [];
      const active = ms && ms.active_route_id;
      body.innerHTML = `<dl class="kv"><dt>Mission</dt><dd>${esc(ms ? ms.mission.name : $("missionName").textContent)}</dd>
        <dt>Status</dt><dd>${esc(ms ? ms.mission.status : "–")} <span class="pill warn">mock</span></dd>
        <dt>Position</dt><dd>${esc(ms && ms.mission.current_position ? ms.mission.current_position.name : "–")}</dd>
        <dt>Destination</dt><dd>${esc(ms && ms.destination ? ms.destination.name : "none")}</dd>
        <dt>Active route</dt><dd>${esc(active || "none")}</dd></dl>
        <h3>Routes in this session</h3>
        ${routes.length ? `<table><tr><th>Route</th><th>Distance</th><th>Max slope</th><th>Walk out</th><th></th></tr>${routes.map((r) =>
          `<tr><td><b>${esc(r.route_id)}</b> ${esc(r.label)}</td><td>${fmtM(r.metrics.distance_m)}</td><td>${r.metrics.max_slope_deg}°</td>
           <td>${Math.round(r.metrics.walk_time_out_min)} min</td><td>${r.recommended ? '<span class="pill ok">recommended</span>' : ""}</td></tr>`).join("")}</table>`
          : '<p class="muted">No routes yet. Ask the assistant for one.</p>'}`;
    } else if (id === "terrain") {
      const c = lastCards.terrain || lastCards.route_detail;
      body.innerHTML = c ? `<p><b>${esc(c.title)}</b></p><pre style="white-space:pre-wrap;font-size:11.5px;color:#c7cfda">${esc(JSON.stringify(c.data, null, 1)).slice(0, 4000)}</pre>`
        : '<p class="muted">No terrain analysis yet. Select a point and ask "Analyze this location", or ask for the steepest section of a route.</p>';
    } else if (id === "environment") {
      const c = lastCards.environment;
      body.innerHTML = c ? `<pre style="white-space:pre-wrap;font-size:11.5px">${esc(JSON.stringify(c.data.measurements || c.data, null, 1))}</pre>`
        : `<p class="muted">No environmental data source is connected in this environment. MEDA readings require the Scientific Intelligence Engine
           or the Marswalk backend; the assistant will not invent them.</p>`;
    } else if (id === "history") {
      const a = st ? st.analyses : [];
      const acts = st ? Object.entries(st.actions) : [];
      body.innerHTML = `<h3>Analyses</h3>${a.length ? `<table>${a.slice().reverse().map((x) => `<tr><td>${esc(x.analysis_id)}</td><td>${esc(x.type.replace(/_/g, " "))}</td>
          <td>${esc(x.summary)}</td></tr>`).join("")}</table>` : '<p class="muted">None yet.</p>'}
        <h3>Map actions</h3>${acts.length ? `<table>${acts.slice(-12).reverse().map(([id, x]) => `<tr><td>${esc(x.type)}</td><td>${esc(x.summary)}</td>
          <td><span class="pill ${x.status === "succeeded" ? "ok" : x.status === "failed" ? "bad" : "warn"}">${esc(x.status)}</span></td></tr>`).join("")}</table>`
          : '<p class="muted">None yet.</p>'}`;
    }
  }

  // ------------------------------------------------------------------ bridge + assistant
  const bridge = new MarswalkActionBridge({
    client: "marswalk-ai-demo",
    sections: () => SECTIONS,
    layers: () => LAYERS.map(({ id, label, keywords }) => ({ id, label, keywords })),
    getContext: () => ({
      section: currentSection,
      visible_layers: Object.keys(layerObj).filter((id) => map.hasLayer(layerObj[id])),
      map: { center: { lat: +map.getCenter().lat.toFixed(6), lon: +map.getCenter().lng.toFixed(6) }, zoom: map.getZoom() },
      selected,
      displayed_routes: Object.keys(routeLayers).filter((id) => map.hasLayer(routeLayers[id])),
    }),
    handlers: {
      display_route: async (p) => drawRoute(p),
      highlight_location: async (p) => highlight(p),
      zoom_to_location: async (p) => {
        if (p.bounds) map.flyToBounds(L.latLngBounds(p.bounds).pad(0.2), { duration: 0.8 });
        else map.flyTo([p.lat, p.lon], p.zoom || 16, { duration: 0.8 });
        return "map moved";
      },
      change_layer: async (p) => { setLayer(p.layer_id, p.visible !== false, p.opacity); return `${p.layer_id} ${p.visible !== false ? "on" : "off"}`; },
      clear_route: async (p) => {
        const ids = p.route_id ? [p.route_id] : Object.keys(routeLayers);
        ids.forEach((id) => { if (routeLayers[id]) { map.removeLayer(routeLayers[id]); delete routeLayers[id]; } });
        highlights.clearLayers();
        return `${ids.length} route overlay(s) cleared`;
      },
      navigate_to_section: async (p) => openSection(p.section_id),
    },
  });

  assistant = MarswalkAssistant.mount({
    apiBase: API, bridge,
    onResponse: (r) => {
      (r.cards || []).forEach((c) => { lastCards[c.type] = c; });
      if (currentSection !== "map") renderSection(currentSection);
    },
  });
  setTimeout(() => assistant.toggle(true), 400);
  window.marswalkDemo = { map, bridge, assistant };   // for debugging in the console
})();
