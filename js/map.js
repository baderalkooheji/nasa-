/* =====================================================================
   Marswalk Mission Planner: map.js
   ---------------------------------------------------------------------
   Sections:
     1. Config + shared state (AppState / setState)
     2. Helpers (API calls, Mars distance for display, formatting)
     3. Notifications (in-app, triggered only by real events)
     4. Map setup, coordinates, Mars scale bar, reset
     5. Data layers (opacity, info, legend, unavailable layers)
     6. Mission stops + Perseverance traverse
     7. Selection panel (right side) + photos
     8. Route A: hand-drawn, editable waypoints
     9. Route B: auto-route + EVA budget
    10. Profile chart, route comparison, EVA bar
    11. Space weather badge
    12. PLACEHOLDERS wired to future backend routes:
        AI assistant (/api/chat), Environment (/api/meda),
        Mission log (/api/records), 3D view
    13. Tabs + start-up
   NASA data: base imagery (Viking, CTX, HiRISE) and overlays (MOLA,
   THEMIS) are NASA Mars Trek WMTS tiles; rover traverse/waypoints come
   from mars.nasa.gov. Endpoints + coordinate notes: JS/nasa-config.js.
   Rules: the map only DISPLAYS. Every distance, elevation, slope and
   time shown as a result comes from the backend. Never measure in pixels.
   ===================================================================== */

/* ---------- 1. Config + shared state ---------- */
// Backend base URL: the page's own server when it answers /api/health (marswalk-ai/backend/marswalk_api serves both),
// otherwise the local Marswalk backend, so the page also works from a plain static server or VS Code Live Server.
const API_CANDIDATES = ["", "http://127.0.0.1:8085"];
let apiBasePromise = null;
function apiBase() {
  apiBasePromise ??= (async () => {
    for (const base of API_CANDIDATES) {
      try {
        const r = await fetch(base + "/api/health", { cache: "no-store" });
        if (r.ok && (r.headers.get("content-type") || "").includes("json")) return base;
      } catch { /* not reachable: try the next one */ }
    }
    return "";
  })();
  return apiBasePromise;
}
const MARS_R = 3396190;               // metres, used ONLY for the on-screen scale bar
const SLOPE_CAUTION = 10;             // display band (degrees); same caution slope as the backend overlays and routes

const AppState = {
  layers: null,        // /api/layers response
  stops: null,         // /api/stops response
  selected: null,      // currently selected point (+ elevation/slope from backend)
  routeA: null,        // analysis of hand-drawn route
  routeB: null,        // analysis of auto route
  eva: null,           // EVA budget for route B
  maxSlope: 15,        // project-defined walking limit
  view: "2d",          // "2d" | "3d"
};

function setState(patch) {
  Object.assign(AppState, patch);
  window.dispatchEvent(new CustomEvent("appstate", { detail: { state: AppState, changed: Object.keys(patch) } }));
}
function onState(keys, fn) {          // run fn when any of these keys change
  window.addEventListener("appstate", e => { if (e.detail.changed.some(k => keys.includes(k))) fn(e.detail.state); });
}

/* ---------- 2. Helpers ---------- */
class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }

async function api(path, body) {
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch((await apiBase()) + path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail?.error?.message || j.detail || msg; } catch { /* not JSON */ }
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

function marsDistance(a, b) {          // display only (scale bar)
  const r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLon = (b.lng - a.lng) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
  return 2 * MARS_R * Math.asin(Math.sqrt(h));
}
const $ = id => document.getElementById(id);
const fmtKm = m => m == null ? "no data" : m >= 1000 ? (m / 1000).toFixed(2) + " km" : Math.round(m) + " m";
const fmtHM = m => `${Math.floor(m / 60)} h ${String(Math.round(m % 60)).padStart(2, "0")}`;
const fmtLL = (lat, lon) => `${lat.toFixed(5)}°N ${lon.toFixed(5)}°E`;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- 3. Notifications ---------- */
// severity: "info" | "warning" | "unavailable" | "done"
const notifications = [];
function notify(severity, text) {
  notifications.unshift({ id: Date.now() + Math.random(), severity, text, time: new Date() });
  renderNotifications();
}
function renderNotifications() {
  const n = notifications.length;
  $("bellCount").hidden = n === 0;
  $("bellCount").textContent = n;
  $("notifList").innerHTML = n === 0 ? `<li class="muted small">No notifications.</li>` :
    notifications.map(x => `<li class="${x.severity}">
      <span><b>${{ info: "Info", warning: "Warning", unavailable: "Data unavailable", done: "Analysis completed" }[x.severity]}</b><br>
      <span class="small">${esc(x.text)}</span><br><span class="muted small">${x.time.toLocaleTimeString()}</span></span>
      <button class="link" data-dismiss="${x.id}" type="button" aria-label="Dismiss">✕</button></li>`).join("");
}
$("btnBell").onclick = () => {
  const open = $("notifPanel").hidden;
  $("notifPanel").hidden = !open;
  $("btnBell").setAttribute("aria-expanded", String(open));
};
$("notifList").onclick = e => {
  const id = e.target.dataset.dismiss;
  if (!id) return;
  notifications.splice(notifications.findIndex(x => String(x.id) === id), 1);
  renderNotifications();
};
$("btnClearNotifs").onclick = () => { notifications.length = 0; renderNotifications(); };

/* ---------- 4. Map setup ---------- */
// EPSG4326 = plain lon/lat degrees. NASA Mars Trek "EQ" tiles use the same
// grid (origin -180°/90°, 2×1 tiles at zoom 0), so map lon/lat = real Mars
// planetocentric, east-positive coordinates. See JS/nasa-config.js.
const map = L.map("map", {
  crs: L.CRS.EPSG4326, zoomSnap: 0.25, minZoom: 3, maxZoom: 19,
  center: [NASA.startView.lat, NASA.startView.lon], zoom: NASA.startView.zoom,
  worldCopyJump: false, maxBounds: [[-90, -180], [90, 180]],
});
// Expose for integration.js
window.map = map;
map.attributionControl.setPrefix(false);
map.attributionControl.addAttribution('Imagery: <a href="https://trek.nasa.gov/mars/" target="_blank" rel="noopener">NASA Mars Trek</a> · NASA/JPL-Caltech/MSSS/UArizona/USGS');
map.createPane("nasaBase").style.zIndex = 150;     // under backend image overlays (overlayPane = 400)
map.createPane("nasaOver").style.zIndex = 250;
const routeLayers = { A: L.layerGroup().addTo(map), B: L.layerGroup().addTo(map) };

/* Cursor readout: x = longitude (east-positive), y = latitude (planetocentric), z = elevation from the DEM.
   z is fetched from the backend once the mouse rests for a moment (no request storm while panning). */
let zTimer = 0, zSeq = 0;
function showCursor(lat, lon, zText) {
  $("coords").innerHTML = `<span class="xyz"><b>x</b> ${lon.toFixed(5)}°E</span><span class="xyz"><b>y</b> ${lat.toFixed(5)}°N</span>` +
    `<span class="xyz"><b>z</b> ${zText}</span>`;
}
map.on("mousemove", e => {
  const { lat, lng } = e.latlng, seq = ++zSeq;
  showCursor(lat, lng, "…");
  clearTimeout(zTimer);
  zTimer = setTimeout(async () => {
    try {
      const r = await api(`/api/elevation?lat=${lat.toFixed(6)}&lon=${lng.toFixed(6)}`);
      if (seq !== zSeq) return;                                   // mouse moved on: drop the stale answer
      const src = String(r.source_dataset || "").replace(/ mosaic.*?@/, " @").replace("Mars 2020 ", "");
      showCursor(lat, lng, r.elevation_m == null ? "no DEM data" :
        `${Math.round(r.elevation_m).toLocaleString()} m <small>slope ${r.slope_deg ?? "?"}° · ${esc(src)}</small>`);
    } catch { if (seq === zSeq) showCursor(lat, lng, "backend offline"); }
  }, 220);
});
map.on("mouseout", () => { clearTimeout(zTimer); zSeq++; });

/* NASA tile layers: one Leaflet tile layer per product, each limited to its own
   footprint and native zoom (Leaflet up-samples beyond it instead of 404s). */
const nasaLayers = {};
function nasaTileLayer(cfg, pane) {
  return L.tileLayer(cfg.url, {
    pane, bounds: L.latLngBounds(cfg.bounds), maxNativeZoom: cfg.maxNativeZoom, maxZoom: 19,
    minZoom: 0, tileSize: 256, noWrap: true, opacity: cfg.opacity ?? 1, crossOrigin: true,
    errorTileUrl: "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==",
  });
}
function layerRow(cfg, layer, { showOpacity }) {
  const row = document.createElement("div");
  row.className = "layer";
  row.innerHTML = `
    <label><input type="checkbox" ${map.hasLayer(layer) ? "checked" : ""}> ${esc(cfg.name)} <span class="tag nasa">NASA</span></label>
    ${showOpacity ? `<input type="range" min="0" max="100" value="${(cfg.opacity ?? 1) * 100}" aria-label="${esc(cfg.name)} opacity">` : ""}
    <details><summary>Info</summary>
      <small class="muted">Mission / instrument: ${esc(cfg.mission)}<br>
      Source: <a href="${esc(cfg.info)}" target="_blank" rel="noopener">${esc(cfg.source)}</a><br>
      Resolution: ${cfg.resolution_m} m/px · Coverage: ${cfg.bounds[0][0].toFixed(2)}–${cfg.bounds[1][0].toFixed(2)}°N, ${cfg.bounds[0][1].toFixed(2)}–${cfg.bounds[1][1].toFixed(2)}°E<br>
      Limits: ${esc(cfg.limitations)}</small>
    </details>`;
  row.querySelector("input[type=checkbox]").onchange = e => e.target.checked ? layer.addTo(map) : map.removeLayer(layer);
  const rng = row.querySelector("input[type=range]");
  if (rng) rng.oninput = e => layer.setOpacity(e.target.value / 100);
  return row;
}
function addNasaLayers() {
  const baseBox = $("basemaps");
  NASA.basemaps.forEach(cfg => {
    const l = nasaLayers[cfg.id] = nasaTileLayer(cfg, "nasaBase");
    if (cfg.default_on) l.addTo(map);
    l.on("tileerror", () => {});            // missing tile = no coverage there, not an error
    baseBox.appendChild(layerRow(cfg, l, { showOpacity: false }));
  });
  const box = $("layers");
  NASA.overlays.forEach(cfg => {
    const l = nasaLayers[cfg.id] = nasaTileLayer(cfg, "nasaOver");
    if (cfg.default_on) l.addTo(map);
    box.appendChild(layerRow(cfg, l, { showOpacity: true }));
  });
  // Landing site marker from NASA rover localisation (sol 13)
  L.circleMarker([NASA.landing.lat, NASA.landing.lon], { radius: 5, color: "#F2C14E", weight: 2, fillOpacity: 0.3 })
    .bindTooltip("Perseverance landing site · Octavia E. Butler Landing (sol 13)").addTo(map);
}

// Fetch the first URL in a list that answers (local copy first, then NASA live).
async function fetchFirst(urls) {
  let lastErr;
  for (const u of urls) {
    try { const r = await fetch(u); if (r.ok) return { data: await r.json(), url: u }; lastErr = new Error(r.status + " " + u); }
    catch (e) { lastErr = e; }
  }
  throw lastErr || new Error("no source");
}
const boundsOverlap = (a, b) => L.latLngBounds(a).intersects(L.latLngBounds(b));

function updateScale() {
  const y = map.getSize().y - 30;
  const m = marsDistance(map.containerPointToLatLng([20, y]), map.containerPointToLatLng([120, y]));
  $("scale").textContent = `100 px ≈ ${fmtKm(m)} (Mars)`;
}
map.on("zoomend moveend resize", updateScale);

$("btnReset").onclick = () => {
  const s = AppState.stops;
  if (s) map.fitBounds(L.latLngBounds([s.habitat, ...s.stops].map(p => [p.lat, p.lon])).pad(0.6));
  else map.setView([NASA.startView.lat, NASA.startView.lon], NASA.startView.zoom);
  setState({ selected: null });
};

/* ---------- 5. Data layers ---------- */
const overlays = {};

// Backend raster layers (DEM-derived: slope, hillshade…). NASA imagery above works without them.
async function loadLayers() {
  let meta;
  try { meta = await api("/api/layers"); }
  catch (err) {
    showBanner("Backend offline: showing NASA imagery and rover data only. Elevation, slope and routes need the backend (" + err.message + ").");
    return;
  }
  // Coordinate check: backend overlays must sit on Jezero, in the same lon/lat as the NASA tiles.
  if (!boundsOverlap(meta.bounds, NASA.jezeroBounds)) {
    showBanner(`Backend layer bounds ${JSON.stringify(meta.bounds)} are not at Jezero (≈18.2–18.7°N, 77.2–77.7°E). ` +
      "Check the DEM is planetocentric, east-positive lon/lat, and that bounds are [[south, west], [north, east]]. Overlays hidden.");
    setState({ layers: meta });
    return;
  }
  map.attributionControl.addAttribution(`Elevation: ${esc(meta.dem)}`);
  const box = $("layers");

  meta.layers.forEach(l => {
    const row = document.createElement("div");
    row.className = "layer";
    if (l.available === false) {                       // honest "unavailable" rows, never fake data
      row.classList.add("off");
      row.innerHTML = `<span>${esc(l.name)} <span class="tag">unavailable</span></span>
        <small class="muted">${esc(l.reason || "Not loaded yet")}</small>`;
      box.appendChild(row);
      return;
    }
    const startOpacity = l.opacity ?? (l.id === "slope" ? 0.8 : 1);
    const ov = L.imageOverlay(l.url, meta.bounds, { opacity: startOpacity });
    overlays[l.id] = ov;
    if (l.default_on) ov.addTo(map);
    const isTest = String(l.source).startsWith("TEST");
    row.innerHTML = `
      <label><input type="checkbox" ${l.default_on ? "checked" : ""}> ${esc(l.name)}${isTest ? '<span class="tag test">test data</span>' : ""}</label>
      <input type="range" min="0" max="100" value="${startOpacity * 100}" aria-label="${esc(l.name)} opacity">
      <details><summary>Info &amp; legend</summary>
        <small class="muted">${esc(l.description || "")}<br>
        Mission / instrument: ${esc(l.mission || "–")}<br>
        Source: ${esc(l.source)}<br>
        Resolution: ${l.resolution_m ?? "–"} m/px · Units: ${esc(l.units || "–")}<br>
        Observed: ${esc(l.observation_date || "not specified")}<br>
        Limits: ${esc(l.limitations || "–")}</small>
        ${l.legend ? `<img src="${esc(l.legend)}" alt="${esc(l.name)} legend" style="width:100%;margin-top:6px">` : ""}
      </details>`;
    row.querySelector("input[type=checkbox]").onchange = e => e.target.checked ? ov.addTo(map) : map.removeLayer(ov);
    row.querySelector("input[type=range]").oninput = e => ov.setOpacity(e.target.value / 100);
    box.appendChild(row);
  });

  if (String(meta.dem).startsWith("TEST")) {
    showBanner("Backend elevation is TEST terrain, not NASA data. Put the Mars 2020 CTX/HiRISE DTM in the backend before the demo.");
  }
  setState({ layers: meta });
  updateScale();
}
function showBanner(text) { $("dataBanner").hidden = false; $("dataBanner").textContent = text; }

/* ---------- 6. Mission stops + Perseverance traverse ---------- */
const pin = (label, cls = "") => L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13], html: `<div class="pin ${cls}">${label}</div>` });

async function loadStops() {
  let s;
  try { s = await api("/api/stops"); }
  catch { s = (await fetchFirst([NASA.stopsFile])).data; }   // backend offline → local stops.json
  const outside = [s.habitat, ...s.stops].filter(p => !L.latLngBounds(NASA.jezeroBounds).contains([p.lat, p.lon]));
  if (outside.length) notify("warning", `${outside.length} stop(s) are outside Jezero (check lat/lon order and east-positive longitude): ${outside.map(p => p.name).join(", ")}.`);
  L.marker([s.habitat.lat, s.habitat.lon], { icon: pin("H", "square"), title: "Habitat" }).addTo(map)
    .on("click", () => select({ ...s.habitat, kind: "habitat" }));
  const list = $("stops");
  list.innerHTML = "";
  s.stops.forEach(st => {
    L.marker([st.lat, st.lon], { icon: pin(st.id), title: st.name }).addTo(map).on("click", () => select({ ...st, kind: "stop" }));
    const li = document.createElement("li");
    li.textContent = st.name;
    li.tabIndex = 0;
    li.onclick = li.onkeydown = ev => { if (ev.type === "click" || ev.key === "Enter") { map.panTo([st.lat, st.lon]); select({ ...st, kind: "stop" }); } };
    list.appendChild(li);
  });
  if (s._note) notify("info", "Stops sit on real Perseverance positions but their names/reasons are a starter set. Data & Science should confirm them.");
  setState({ stops: s });
  $("btnReset").onclick();                        // frame habitat + stops
}

// Perseverance traverse + waypoints (NASA/JPL-Caltech MMGIS). Local copy first, NASA live second.
let roverWaypoints = [];
async function loadTraverse() {
  const box = $("layers");
  const row = document.createElement("div");
  row.className = "layer";
  try {
    const [trav, wps] = await Promise.all([fetchFirst(NASA.rover.traverse), fetchFirst(NASA.rover.waypoints).catch(() => null)]);
    const group = L.layerGroup();
    L.geoJSON(trav.data, {
      coordsToLatLng: c => L.latLng(c[1], c[0]),          // GeoJSON is [lon, lat, (elev)]: drop elevation
      style: { color: "#B9A8FF", weight: 2.5, opacity: 0.9 },
      onEachFeature: (f, lyr) => lyr.bindTooltip(`Perseverance drive · sol ${f.properties?.sol ?? "?"}`, { sticky: true }),
    }).addTo(group);
    if (wps) {
      roverWaypoints = wps.data.features.map(f => ({
        lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1],
        sol: f.properties.sol, elev: f.properties.elev_geoid,
      }));
      L.geoJSON(wps.data, {
        pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 3, color: "#B9A8FF", weight: 1, fillOpacity: 0.9 }),
        onEachFeature: (f, lyr) => lyr.bindTooltip(`Perseverance · sol ${f.properties?.sol ?? "?"} · ${f.properties?.elev_geoid?.toFixed?.(1) ?? "?"} m (areoid)`),
      }).addTo(group);
    }
    group.addTo(map);
    const live = trav.url.startsWith("http");
    row.innerHTML = `<label><input type="checkbox" checked> Perseverance traverse <span class="tag nasa">NASA</span></label>
      <small class="muted">Rover path and end-of-drive positions · NASA/JPL-Caltech ·
      <a href="${esc(NASA.rover.traverse[1])}" target="_blank" rel="noopener">${live ? "live from mars.nasa.gov" : "local copy"}</a></small>`;
    row.querySelector("input").onchange = e => e.target.checked ? group.addTo(map) : map.removeLayer(group);
    if (live) notify("info", "Rover traverse loaded live from mars.nasa.gov. Run tools/fetch_nasa_data.py to keep an offline copy for the demo.");
  } catch {
    row.classList.add("off");
    row.innerHTML = `<span>Perseverance traverse <span class="tag">unavailable</span></span>
      <small class="muted">Could not reach mars.nasa.gov. Run tools/fetch_nasa_data.py to save M20_traverse.json and M20_waypoints.json in data/.</small>`;
  }
  box.appendChild(row);
}

// Nearest rover localisation (great-circle on the Mars sphere)
function nearestWaypoint(lat, lon) {
  let best = null;
  for (const w of roverWaypoints) {
    const d = marsDistance({ lat, lng: lon }, { lat: w.lat, lng: w.lon });
    if (!best || d < best.d) best = { ...w, d };
  }
  return best;
}

/* ---------- 7. Selection panel ---------- */
async function select(pt) {
  try {
    const e = await api(`/api/elevation?lat=${pt.lat}&lon=${pt.lon}`);
    setState({ selected: { ...e, ...pt } });        // pt last so stop name/reason survive
    if (!e.data_available) notify("unavailable", `No elevation data at ${fmtLL(pt.lat, pt.lon)}: ${e.no_data_reason}.`);
  } catch (err) {
    // Backend offline: still show the point. Use a real NASA number only if the rover stood there.
    const w = nearestWaypoint(pt.lat, pt.lon);
    const near = w && w.d <= NASA.rover.nearWaypoint_m;
    setState({ selected: {
      ...pt,
      elevation_m: near ? +w.elev.toFixed(1) : null,
      slope_deg: null,
      source_dataset: near ? `Perseverance localisation, sol ${w.sol}, ${Math.round(w.d)} m away (NASA/JPL-Caltech M20_waypoints)` : "DEM backend offline",
      data_available: near,
      no_data_reason: near ? "Elevation is the rover's measured height above the Mars areoid, not a DEM sample at this exact point."
        : `Elevation/slope come from the CTX/HiRISE DTM in the backend, which is not reachable (${err.message}).`
        + (w ? ` Nearest rover position: sol ${w.sol}, ${fmtKm(w.d)} away.` : ""),
    } });
  }
}
map.on("click", ev => { if (!drawing) select({ lat: ev.latlng.lat, lon: ev.latlng.lng, kind: "point" }); });

let selMarker = null;
onState(["selected"], s => {
  const p = s.selected;
  $("btnSaveRecord").disabled = !p;
  if (selMarker) { map.removeLayer(selMarker); selMarker = null; }
  if (!p) { $("info").innerHTML = "Click the map or a stop."; $("photos").hidden = true; return; }
  if (p.kind === "point") selMarker = L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fff", weight: 2, fillOpacity: 0 }).addTo(map);
  const label = p.kind === "point" ? "" : `<span class="label illus">${AppState.stops?._note ? "illustrative placeholder" : "mission stop"}</span>`;
  $("info").innerHTML = `
    ${p.name ? `<h3>${esc(p.name)} ${label}</h3>` : ""}
    ${p.reason ? `<p>${esc(p.reason)} ${p.source ? `<a href="${esc(p.source)}" target="_blank" rel="noopener">source</a>` : ""}</p>` : ""}
    <table>
      <tr><th>Lat / lon</th><td class="mono">${fmtLL(p.lat, p.lon)}</td></tr>
      <tr><th>Elevation</th><td>${p.elevation_m != null ? p.elevation_m + ` m <span class='label measured'>${String(p.source_dataset).startsWith("Perseverance") ? "rover-measured" : "DEM"}</span>` : "no data"}</td></tr>
      <tr><th>Slope</th><td>${p.slope_deg != null ? p.slope_deg + "° <span class='label calc'>calculated</span>" : "no data"}</td></tr>
      <tr><th>Source</th><td>${esc(p.source_dataset)}</td></tr>
    </table>
    ${p.no_data_reason ? `<p class="over small">${esc(p.no_data_reason)}</p>` : ""}`;
  // Photos: Integration fills stops.json "photos": [{url, sol, camera, dist_m, credit}]
  const photos = p.photos || [];
  $("photos").hidden = p.kind !== "stop";
  $("photos").innerHTML = photos.length
    ? photos.map(ph => `<figure><img src="${esc(ph.url)}" alt="${esc(ph.camera)} photo, sol ${ph.sol}" loading="lazy">
        <figcaption>${esc(ph.camera)} · sol ${ph.sol}${ph.dist_m ? ` · ~${ph.dist_m} m away` : ""}<br>${esc(ph.credit || "NASA/JPL-Caltech")}</figcaption></figure>`).join("")
    : `<div class="ph">Rover photo<br>not added yet</div><div class="ph">Rover photo<br>not added yet</div><div class="ph">Rover photo<br>not added yet</div>`;
});

// Expose select for integration.js
window.select = select;

/* ---------- 8. Route A: hand-drawn, editable ---------- */
let drawing = false, wpMarkers = [], draftLine = null;

function draftPoints() { return wpMarkers.map(m => ({ lat: m.getLatLng().lat, lon: m.getLatLng().lng })); }
function redrawDraft() {
  if (draftLine) map.removeLayer(draftLine);
  draftLine = wpMarkers.length ? L.polyline(wpMarkers.map(m => m.getLatLng()), { color: "#F2C14E", dashArray: "6 6" }).addTo(map) : null;
  wpMarkers.forEach((m, i) => m.setIcon(pin(i + 1, "wp")));
  $("waypointList").innerHTML = wpMarkers.map((m, i) =>
    `<li><span class="mono">${i + 1}. ${fmtLL(m.getLatLng().lat, m.getLatLng().lng)}</span>
     <button type="button" data-del="${i}" aria-label="Delete waypoint ${i + 1}">Delete</button></li>`).join("");
}
function addWaypoint(latlng) {
  const m = L.marker(latlng, { draggable: true, icon: pin(wpMarkers.length + 1, "wp") }).addTo(map);
  m.on("drag", redrawDraft);
  m.on("dragend", () => { if (!drawing) analyzeA(); });            // edit after finishing → re-analyze
  m.on("contextmenu", () => removeWaypoint(wpMarkers.indexOf(m)));
  wpMarkers.push(m);
  redrawDraft();
}
function removeWaypoint(i) {
  if (i < 0) return;
  map.removeLayer(wpMarkers[i]);
  wpMarkers.splice(i, 1);
  redrawDraft();
  if (!drawing) wpMarkers.length >= 2 ? analyzeA() : (routeLayers.A.clearLayers(), setState({ routeA: null }));
}
$("waypointList").onclick = e => { if (e.target.dataset.del) removeWaypoint(+e.target.dataset.del); };

function startDrawing() {
  drawing = true;
  wpMarkers.forEach(m => map.removeLayer(m)); wpMarkers = []; redrawDraft();
  routeLayers.A.clearLayers();
  $("btnDraw").classList.add("active"); $("btnDraw").textContent = "Finish route";
  $("drawHint").hidden = false;
  map.doubleClickZoom.disable();
}
async function finishDrawing() {
  drawing = false;
  $("btnDraw").classList.remove("active"); $("btnDraw").textContent = "Draw route by hand";
  $("drawHint").hidden = true;
  map.doubleClickZoom.enable();
  if (wpMarkers.length < 2) { notify("info", "A route needs at least 2 points."); return; }
  await analyzeA();
}
async function analyzeA() {
  try {
    const a = await api("/api/route/analyze", { waypoints: draftPoints(), route_id: "A" });
    if (draftLine) { map.removeLayer(draftLine); draftLine = null; }
    drawRoute("A", a);
    setState({ routeA: a });
    routeNotifications(a, "Route A");
  } catch (err) { notify("unavailable", "Route A analysis failed: " + err.message); }
}
$("btnDraw").onclick = () => (drawing ? finishDrawing() : startDrawing());
map.on("click", ev => { if (drawing) addWaypoint(ev.latlng); });
map.on("dblclick", () => { if (drawing) finishDrawing(); });

/* Draws a route coloured by slope, using the backend profile:
   dark casing for contrast on bright imagery, one polyline per run of the same slope class,
   start/end markers and a summary tooltip. */
const ROUTE_STYLE = { A: { base: "#E6EAF0", weight: 4, dash: "10 8", name: "Route A · hand" },
                      B: { base: "#5EC8F2", weight: 6, dash: null, name: "Route B · auto" } };
const slopeColor = (s, base) => s == null ? "#9AA6B6" : s > AppState.maxSlope ? "#F97316" : s > SLOPE_CAUTION ? "#F2C14E" : base;

function drawRoute(id, analysis) {
  const g = routeLayers[id];
  g.clearLayers();
  const prof = (analysis.profile || []).filter(p => p.lat != null && p.lon != null);
  if (prof.length < 2) return;
  const st = ROUTE_STYLE[id];
  const pts = prof.map(p => [p.lat, p.lon]);
  const tip = `<b>${st.name}</b><br>${fmtKm(analysis.distance_m)} · max slope ${analysis.max_slope_deg ?? "?"}°` +
    ` · ↑${analysis.elevation_gain_m ?? "?"} m ↓${analysis.elevation_loss_m ?? "?"} m`;
  L.polyline(pts, { color: "#0B0E13", weight: st.weight + 5, opacity: 0.65, lineCap: "round", lineJoin: "round", interactive: false }).addTo(g);
  let start = 0;
  for (let i = 1; i < prof.length; i++) {
    const c = slopeColor(prof[i].slope_deg, st.base);
    const next = i + 1 < prof.length ? slopeColor(prof[i + 1].slope_deg, st.base) : null;
    if (c !== next) {
      L.polyline(pts.slice(start, i + 1), { color: c, weight: st.weight, dashArray: st.dash, lineCap: "round", lineJoin: "round" })
        .bindTooltip(tip, { sticky: true }).addTo(g);
      start = i;
    }
  }
  [[pts[0], "Start"], [pts.at(-1), "End"]].forEach(([ll, label]) =>
    L.circleMarker(ll, { radius: 6, color: st.base, weight: 3, fillColor: "#0B0E13", fillOpacity: 1 })
      .bindTooltip(`${st.name} · ${label}`).addTo(g));
}

function routeNotifications(r, name) {
  if (r.over_limit) notify("warning", `${name} has slopes up to ${r.max_slope_deg}°, above the ${AppState.maxSlope}° project-defined limit.`);
  if (r.data_coverage_pct < 100) notify("unavailable", `${name}: only ${r.data_coverage_pct}% of the route has elevation data.`);
  notify("done", `${name} analysed: ${fmtKm(r.distance_m)}, max slope ${r.max_slope_deg ?? "?"}°.`);
}

/* ---------- 9. Route B: auto-route + EVA ---------- */
$("btnAuto").onclick = async () => {
  const s = AppState.stops;
  if (!s) return;
  const btn = $("btnAuto");
  btn.disabled = true; btn.textContent = "Finding route…";
  try {
    const out = await api("/api/route/auto", { stops: [s.habitat, ...s.stops], max_slope: AppState.maxSlope });
    const back = await api("/api/route/auto", { stops: [s.stops.at(-1), s.habitat], max_slope: AppState.maxSlope });
    drawRoute("B", out);
    const eva = await api("/api/eva", { out_waypoints: out.waypoints, back_waypoints: back.waypoints, n_stops: s.stops.length });
    setState({ routeB: out, eva });
    routeNotifications(out, "Route B");
    if (eva.status !== "ok") notify("warning", `EVA reserve below target: ${eva.reserve_min} min left of ${eva.suit_limit_min} (project-defined).`);
  } catch (err) {
    notify("unavailable", "Auto-route failed: " + err.message);
  } finally { btn.disabled = false; btn.textContent = "Auto-route around hazards"; }
};

$("btnClear").onclick = () => {
  routeLayers.A.clearLayers(); routeLayers.B.clearLayers();
  wpMarkers.forEach(m => map.removeLayer(m)); wpMarkers = []; redrawDraft();
  setState({ routeA: null, routeB: null, eva: null });
};

$("slope").oninput = e => { $("slopeVal").textContent = e.target.value; setState({ maxSlope: +e.target.value }); };
onState(["maxSlope"], s => {                       // recolour existing routes with the new limit
  if (s.routeA) drawRoute("A", s.routeA);
  if (s.routeB) drawRoute("B", s.routeB);
});

/* ---------- 10. Profile, comparison, EVA bar ---------- */
/* Elevation profile: height (metres above the Mars areoid) against distance along each route.
   The line is coloured by slope like the map; hovering a point marks the same spot on the map. */
let chart = null;
const profileMarker = L.circleMarker([0, 0], { radius: 7, color: "#fff", weight: 3, fillColor: "#5EC8F2", fillOpacity: 1, interactive: false });
const profilePoints = r => r.profile.map(p => ({ x: p.dist_m / 1000, y: p.elevation_m, s: p.slope_deg, lat: p.lat, lon: p.lon }));
onState(["routeA", "routeB", "maxSlope"], s => {
  const ds = [];
  const seg = base => ({ borderColor: c => slopeColor(c.p1.raw && c.p1.raw.s, base) });
  if (s.routeB) ds.push({ label: "Route B · auto", data: profilePoints(s.routeB), borderColor: "#5EC8F2", segment: seg("#5EC8F2"),
    borderWidth: 2.5, fill: "start", backgroundColor: "rgba(94,200,242,0.10)" });
  if (s.routeA) ds.push({ label: "Route A · hand", data: profilePoints(s.routeA), borderColor: "#E6EAF0", segment: seg("#E6EAF0"),
    borderWidth: 2, borderDash: [6, 4], fill: false });
  ds.forEach(d => Object.assign(d, { pointRadius: 0, pointHoverRadius: 5, pointHoverBackgroundColor: "#fff", spanGaps: false, tension: 0.15 }));
  $("profileEmpty").hidden = ds.length > 0;
  if (!chart) {
    const axis = (text) => ({ title: { display: true, text, color: "#9AA6B6", font: { size: 11 } },
                              ticks: { color: "#9AA6B6", font: { size: 11 }, maxTicksLimit: 7 }, grid: { color: "rgba(255,255,255,0.06)" }, border: { display: false } });
    chart = new Chart($("profile"), { type: "line", data: { datasets: ds },
      options: { animation: false, parsing: { xAxisKey: "x", yAxisKey: "y" }, maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: false, axis: "x" },
        plugins: {
          legend: { labels: { color: "#C2CBD6", boxWidth: 14, boxHeight: 3, font: { size: 11 } } },
          tooltip: { backgroundColor: "rgba(11,14,19,0.95)", borderColor: "#2E3A49", borderWidth: 1, padding: 8,
            callbacks: { title: it => `${it[0].raw.x.toFixed(2)} km along the route`,
                         label: it => `${it.dataset.label}: ${it.raw.y == null ? "no data" : Math.round(it.raw.y).toLocaleString() + " m"}` +
                                      (it.raw.s != null ? ` · slope ${it.raw.s}°` : "") } },
        },
        onHover: (evt, els) => {
          const raw = els.length && chart.data.datasets[els[0].datasetIndex].data[els[0].index];
          if (raw && raw.lat != null) { profileMarker.setLatLng([raw.lat, raw.lon]).addTo(map); } else map.removeLayer(profileMarker);
        },
        scales: { x: { type: "linear", ...axis("distance along the route (km)") }, y: axis("elevation (m, Mars areoid)") } } });
    $("profile").addEventListener("mouseleave", () => map.removeLayer(profileMarker));
  } else { chart.data.datasets = ds; chart.update(); }
  const r = s.routeB || s.routeA;
  $("method").textContent = r ? `Data: ${r.source_dataset}` : "";
});

onState(["routeA", "routeB"], s => {
  const row = (label, f) => `<tr><th>${label}</th><td>${s.routeA ? f(s.routeA) : "–"}</td><td>${s.routeB ? f(s.routeB) : "–"}</td></tr>`;
  $("compare").querySelector("tbody").innerHTML =
    row("Distance", r => fmtKm(r.distance_m)) +
    row("Gain / loss", r => `+${r.elevation_gain_m} / −${r.elevation_loss_m} m`) +
    row("Max slope", r => r.max_slope_deg == null ? "no data" : `<span class="${r.over_limit ? "over" : ""}">${r.max_slope_deg}°${r.over_limit ? " · over limit" : ""}</span>`) +
    row("Avg slope", r => r.avg_slope_deg == null ? "no data" : r.avg_slope_deg + "°") +
    row("Data coverage", r => r.data_coverage_pct + "%");
  // Transparent explanation, never "safe"
  let note = "";
  if (s.routeA && s.routeB) {
    const a = s.routeA, b = s.routeB;
    const lower = a.max_slope_deg < b.max_slope_deg ? "A" : "B";
    const shorter = a.distance_m < b.distance_m ? "A" : "B";
    note = `Route ${lower} has the lower maximum slope; route ${shorter} is shorter. Preliminary terrain assessment from elevation data only.`;
    if (Math.abs(a.distance_m - b.distance_m) > 0.25 * Math.min(a.distance_m, b.distance_m) || Math.abs(a.max_slope_deg - b.max_slope_deg) > 5)
      notify("info", "Routes A and B differ substantially in distance or slope. See Compare routes.");
  }
  $("compareNote").textContent = note;
});

onState(["eva"], s => {
  const e = s.eva, bar = $("evaBar");
  if (!e) { bar.innerHTML = `<span class="muted small">Run auto-route to see the time budget.</span>`; $("dose").textContent = ""; $("evaLabel").textContent = ""; return; }
  const seg = (cls, m, label) => m > 0 ? `<div class="${cls}" style="flex:${Math.max(m, 45)} 1 0" title="${label} ${fmtHM(m)}">${label} ${fmtHM(m)}</div>` : "";
  bar.innerHTML = seg("seg-walk", e.walk_out_min, "Out") + seg("seg-sci", e.science_min, "Science") + seg("seg-walk", e.walk_back_min, "Back") +
    (e.reserve_min >= 0 ? seg(e.status === "ok" ? "seg-res" : "seg-bad", e.reserve_min, e.status === "ok" ? "Reserve" : "Reserve LOW")
                        : `<div class="seg-bad" style="flex:60 1 0">Over suit limit by ${fmtHM(-e.reserve_min)}</div>`);
  $("evaLabel").textContent = "· " + e.label;
  $("dose").textContent = `Dose estimate ≈ ${e.dose_estimate_mgy} mGy (model estimate). ${e.dose_note}.`;
});

/* ---------- 11. Space weather ---------- */
async function loadSpaceWeather() {
  const el = $("sw");
  try {
    const w = await api("/api/spaceweather");
    el.className = "badge " + w.status;
    if (w.status === "unknown") { el.textContent = "Space weather: unavailable"; notify("unavailable", "Space weather (NASA DONKI) could not be reached."); return; }
    el.textContent = `Space weather: ${w.status} · ${w.flares.length} flares, ${w.sep_events.length} SEP (7 d)${w.cached ? " · saved copy" : ""}`;
    el.title = `${w.rule}. Source: ${w.source || "NASA DONKI"}`;
    if (w.status === "caution") notify("warning", "Solar particle activity in the last 7 days (project-defined rule).");
  } catch { el.textContent = "Space weather: unavailable"; }
}

/* ---------- 12. PLACEHOLDERS wired to future backend routes ---------- */

/* 12a. AI assistant → POST /api/chat {question, context} → {answer, sources:[{title,url}]}  (AI role) */
function chatContext() {          // everything the assistant needs to answer about "this location"
  const s = AppState, sel = s.selected;
  return {
    selected: sel && { lat: sel.lat, lon: sel.lon, name: sel.name, elevation_m: sel.elevation_m, slope_deg: sel.slope_deg, source: sel.source_dataset },
    active_layers: Object.entries(overlays).filter(([, ov]) => map.hasLayer(ov)).map(([id]) => id),
    route_a: s.routeA && { distance_m: s.routeA.distance_m, max_slope_deg: s.routeA.max_slope_deg, gain_m: s.routeA.elevation_gain_m },
    route_b: s.routeB && { distance_m: s.routeB.distance_m, max_slope_deg: s.routeB.max_slope_deg, gain_m: s.routeB.elevation_gain_m },
    eva: s.eva, max_slope_deg: s.maxSlope, dem: s.layers?.dem,
  };
}
function addMsg(cls, html) { const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html; $("chatLog").appendChild(d); $("chatLog").scrollTop = 1e9; return d; }
async function askAssistant(q) {
  if (!q.trim()) return;
  addMsg("user", esc(q));
  const wait = addMsg("bot", "<span class='muted'>Thinking…</span>");
  try {
    const r = await api("/api/chat", { question: q, context: chatContext() });
    wait.innerHTML = esc(r.answer) + (r.sources?.length ? `<br><small>Sources: ${r.sources.map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>`).join(", ")}</small>` : "");
  } catch (err) {
    wait.className = "msg err";
    wait.textContent = [404, 405, 501].includes(err.status) ? "The AI assistant isn't connected yet (backend route /api/chat not built). The rest of the app works normally."
      : err.status === 503 ? "The AI assistant needs an API key. Add it to .env on the server." : "Assistant error: " + err.message;
  }
}
$("btnChat").onclick = () => { $("chat").hidden = !$("chat").hidden; if (!$("chat").hidden) $("chatInput").focus(); };
$("btnChatClose").onclick = () => { $("chat").hidden = true; };
$("btnChatClear").onclick = () => { $("chatLog").innerHTML = ""; };
$("chatForm").onsubmit = e => { e.preventDefault(); const q = $("chatInput").value; $("chatInput").value = ""; askAssistant(q); };
document.querySelectorAll(".chip").forEach(c => c.onclick = () => askAssistant(c.textContent));

/* 12b. Environment → GET /api/meda → {sol_range, rows:[{sol, lmst, air_temp_c, pressure_pa, wind_ms}], source}  (Integration role) */
async function loadEnvironment() {
  try {
    const m = await api("/api/meda");
    $("envPanel").className = "card";
    $("envPanel").innerHTML = `<h3>MEDA · measured at Perseverance</h3>
      <table><tr><th>Sol</th><th>Time</th><th>Air °C</th><th>Pa</th><th>Wind m/s</th></tr>
      ${m.rows.slice(0, 12).map(r => `<tr><td>${r.sol}</td><td>${esc(r.lmst)}</td><td>${r.air_temp_c ?? "–"}</td><td>${r.pressure_pa ?? "–"}</td><td>${r.wind_ms ?? "–"}</td></tr>`).join("")}</table>
      <p class="muted small">Historical measurements at the rover's location and times, not live crater-wide weather. Source: ${esc(m.source)}</p>`;
  } catch { /* keep the placeholder text in index.html */ }
}

/* 12c. Mission log → POST /api/records, GET /api/records?q=  (Integration role, SQLite) */
$("btnSaveRecord").onclick = async () => {
  const p = AppState.selected;
  if (!p) return;
  const record = {
    label: p.name || "Map point", lat: p.lat, lon: p.lon,
    analysis_type: AppState.routeB || AppState.routeA ? "route" : "location",
    result: AppState.routeB ? `Route B ${fmtKm(AppState.routeB.distance_m)}, max ${AppState.routeB.max_slope_deg}°` : `Elevation ${p.elevation_m ?? "n/a"} m, slope ${p.slope_deg ?? "n/a"}°`,
    elevation_m: p.elevation_m, slope_deg: p.slope_deg, dataset: p.source_dataset,
    status: p.data_available ? "complete" : "data unavailable",
  };
  try { await api("/api/records", record); notify("done", `Saved "${record.label}" to the mission log.`); loadRecords(); }
  catch (err) { notify("unavailable", err.status === 404 || err.status === 405 ? "Mission log not connected yet (backend /api/records not built)." : "Could not save: " + err.message); }
};
async function loadRecords(q = "") {
  try {
    const rows = await api("/api/records?q=" + encodeURIComponent(q));
    $("recordList").innerHTML = rows.length ? rows.map(r => `<li data-lat="${r.lat}" data-lon="${r.lon}">
      <b>${esc(r.label)}</b> <span class="tag">${esc(r.analysis_type)}</span><br>
      <span class="small">${esc(r.result)}</span><br><span class="muted small">${esc(r.created_at || "")} · ${esc(r.status)}</span></li>`).join("")
      : `<li class="muted small">No matching records.</li>`;
  } catch { /* backend not ready: keep placeholder */ }
}
$("recordSearch").oninput = e => loadRecords(e.target.value);
$("recordList").onclick = e => {               // open a record's location on the map
  const li = e.target.closest("li[data-lat]");
  if (!li) return;
  const lat = +li.dataset.lat, lon = +li.dataset.lon;
  map.setView([lat, lon], Math.max(map.getZoom(), 14));
  select({ lat, lon, kind: "point" });
};

/* 12d. 3D view (optional). State stays in AppState, so returning to 2D loses nothing.
   The actual view switching is now handled by integration.js's window.setView.
   We keep this stub for compatibility but it delegates to the global setView. */
function setView(v) {
  if (window.setView) window.setView(v);
}
$("mode2d").onclick = () => setView("2d");
$("mode3d").onclick = () => setView("3d");
// btnBack2d doesn't exist in combined UI; if present, hook it up
if ($("btnBack2d")) $("btnBack2d").onclick = () => setView("2d");

/* ---------- 13. Tabs + start-up ---------- */
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
  document.querySelectorAll(".tabpane").forEach(p => { p.hidden = p.id !== "tab-" + t.dataset.tab; });
  if (t.dataset.tab === "env") loadEnvironment();
  if (t.dataset.tab === "records") loadRecords($("recordSearch").value);
  setTimeout(() => map.invalidateSize(), 50);
});

renderNotifications();
addNasaLayers();                                   // NASA imagery first: the map is never blank
updateScale();
loadLayers()
  .then(loadStops)
  .catch(err => notify("unavailable", "Stops could not be loaded: " + err.message))
  .then(loadTraverse);
loadSpaceWeather();