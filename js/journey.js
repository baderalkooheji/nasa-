/* Marswalk route journey: shared journey state + 2D animation + mission control panel.
 *
 * One journey at a time, shared by both views:
 *   window.MarswalkJourney = { start(key), pause(), resume(), toggle(), stop(), restart(), close(),
 *                              setSpeed(mps), seek(fraction), setCam3d(mode), state, on(event, fn), sample(d) }
 * Events: "start", "tick" (position), "status", "speed", "cam3d", "close".
 *
 * The journey follows the ACTUAL route geometry from the backend analysis (AppState.routeA / routeB):
 * lat/lon points, cumulative distance on the Mars sphere, and DEM elevation (metres, Mars areoid).
 * Movement uses elapsed time (performance.now), not frames, so speed is identical at any frame rate.
 * The 2D renderer lives here; the 3D renderer (js/journey3d.js) subscribes to the same state.
 * Loaded as a classic script after map.js (uses its map, AppState, onState, notify, fmtKm, esc).
 */
(function () {
  "use strict";
  const R = 3396190, DEG = Math.PI / 180;
  const SPEEDS = [2, 5, 10, 25, 50, 100, 200];          // simulation speeds, metres per second (walking is ~1 m/s)
  const NAMES = { A: "Route A · hand-drawn", B: "Route B · auto-route" };
  const $id = (id) => document.getElementById(id);

  const J = { key: null, analysis: null, path: null, status: "idle", dist: 0, speed: 25, cam3d: "follow", last: 0, pos: null };
  const listeners = {};
  const emit = (ev, data) => (listeners[ev] || []).forEach((f) => { try { f(data); } catch (e) { console.error("[journey]", e); } });
  const on = (ev, f) => { (listeners[ev] ||= []).push(f); };

  // ------------------------------------------------------------------ route geometry
  function hav(aLat, aLon, bLat, bLon) {
    const p1 = aLat * DEG, p2 = bLat * DEG, dp = p2 - p1, dl = (bLon - aLon) * DEG;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /** Dense route polyline with cumulative distance and DEM elevation, from the backend analysis. */
  function buildPath(analysis) {
    const ok = (p) => p && Number.isFinite(p.lat) && Number.isFinite(p.lon);
    const prof = (analysis.profile || []).filter(ok);
    const way = (analysis.waypoints || []).filter(ok);
    const base = way.length > prof.length ? way : prof;    // the denser geometry, so every bend is followed
    const lat = [], lon = [], cum = [];
    for (const p of base) {
      if (lat.length && Math.abs(p.lat - lat.at(-1)) < 1e-9 && Math.abs(p.lon - lon.at(-1)) < 1e-9) continue;
      cum.push(lat.length ? cum.at(-1) + hav(lat.at(-1), lon.at(-1), p.lat, p.lon) : 0);
      lat.push(p.lat); lon.push(p.lon);
    }
    if (lat.length < 2) return null;
    const total = cum.at(-1);
    // elevation: the profile carries DEM values; map them onto the geometry by fraction of distance
    const pd = prof.filter((p) => Number.isFinite(p.elevation_m));
    const pTot = pd.length ? (pd.at(-1).dist_m || 1) : 1;
    const elev = cum.map((c) => {
      if (!pd.length) return null;
      const f = (c / total) * pTot;
      let i = 0;
      while (i < pd.length - 2 && pd[i + 1].dist_m < f) i++;
      const a = pd[i], b = pd[Math.min(i + 1, pd.length - 1)];
      const t = b.dist_m > a.dist_m ? Math.min(1, Math.max(0, (f - a.dist_m) / (b.dist_m - a.dist_m))) : 0;
      return a.elevation_m + (b.elevation_m - a.elevation_m) * t;
    });
    return { lat, lon, cum, elev, total };
  }

  function bearing(aLat, aLon, bLat, bLon) {
    const dx = (bLon - aLon) * Math.cos(((aLat + bLat) / 2) * DEG), dy = bLat - aLat;
    return (Math.atan2(dx, dy) / DEG + 360) % 360;              // 0 = north, clockwise (same as the 3D camera)
  }

  function pointAt(d) {
    const P = J.path, n = P.cum.length;
    d = Math.max(0, Math.min(P.total, d));
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (P.cum[m] <= d) lo = m; else hi = m; }
    const seg = P.cum[hi] - P.cum[lo], t = seg > 0 ? (d - P.cum[lo]) / seg : 0;
    const e = P.elev[lo] == null ? null : P.elev[lo] + ((P.elev[hi] ?? P.elev[lo]) - P.elev[lo]) * t;
    return { lat: P.lat[lo] + (P.lat[hi] - P.lat[lo]) * t, lon: P.lon[lo] + (P.lon[hi] - P.lon[lo]) * t, elev: e, index: lo };
  }

  /** Position, elevation and direction of travel at distance d (direction looks 20 m ahead to round off corners). */
  function sample(d) {
    const P = J.path;
    const p = pointAt(d);
    const ahead = pointAt(Math.min(P.total, d + 20)), behind = pointAt(Math.max(0, d - 20));
    const a = d + 20 <= P.total ? p : behind, b = d + 20 <= P.total ? ahead : p;
    return { ...p, dist: Math.max(0, Math.min(P.total, d)), heading: bearing(a.lat, a.lon, b.lat, b.lon),
             total: P.total, fraction: P.total ? Math.min(1, d / P.total) : 1, status: J.status, speed: J.speed };
  }

  // ------------------------------------------------------------------ clock
  function frame(now) {
    if (J.status === "running") {
      const dt = Math.min(0.25, (now - J.last) / 1000);          // clamp after tab switches
      J.last = now;
      J.dist += J.speed * dt;
      if (J.dist >= J.path.total) { J.dist = J.path.total; setStatus("completed"); }
      J.pos = sample(J.dist);
      emit("tick", J.pos);
      requestAnimationFrame(frame);
    }
  }
  function setStatus(s) {
    J.status = s;
    if (J.path) { J.pos = sample(J.dist); emit("tick", J.pos); }
    emit("status", s);
    if (s === "running") { J.last = performance.now(); requestAnimationFrame(frame); }
  }

  // ------------------------------------------------------------------ public API
  const api = {
    get state() { return J; },
    on, sample: (d) => sample(d),
    start(key) {
      const analysis = AppState["route" + key];                 // AppState is a top-level const of map.js (not a window property)
      if (!analysis) { notify("info", `No ${NAMES[key]} yet: create it first in the Route tab.`); return false; }
      const path = buildPath(analysis);
      if (!path) { notify("unavailable", `${NAMES[key]} has no usable geometry.`); return false; }
      Object.assign(J, { key, analysis, path, dist: 0 });
      document.body.classList.add("journey-active");
      emit("start", { key, analysis, path });
      setStatus("running");
      return true;
    },
    pause() { if (J.status === "running") setStatus("paused"); },
    resume() { if (J.status === "paused") setStatus("running"); else if (J.status === "completed") api.restart(); },
    toggle() { J.status === "running" ? api.pause() : api.resume(); },
    stop() { if (!J.path) return; J.dist = 0; setStatus("stopped"); },
    restart() { if (!J.path) return; J.dist = 0; setStatus("running"); },
    seek(f) { if (!J.path) return; J.dist = Math.max(0, Math.min(1, f)) * J.path.total; if (J.status === "completed") J.status = "paused";
              J.pos = sample(J.dist); emit("tick", J.pos); emit("status", J.status); },
    setSpeed(v) { J.speed = +v; emit("speed", J.speed); renderPanel(); },
    setCam3d(m) { J.cam3d = m; emit("cam3d", m); renderPanel(); },
    close() { J.status = "idle"; J.path = null; J.key = null; document.body.classList.remove("journey-active"); emit("close"); emit("status", "idle"); },
  };
  window.MarswalkJourney = api;

  // A changed route invalidates the running journey (the geometry it follows no longer exists).
  onState(["routeA", "routeB"], (s) => {
    if (J.key && s["route" + J.key] !== J.analysis) { api.close(); notify("info", "The route changed, so the journey was closed."); }
    updateStartButton();
  });

  // ------------------------------------------------------------------ 2D renderer: tracking on the map
  const layer = L.layerGroup();
  let remainLine = null, doneLine = null, marker = null, startDot = null, endDot = null, follow2d = true, lastPan = 0;
  const is2d = () => !$id("view2d").hidden;

  on("start", ({ path }) => {
    layer.clearLayers();
    const all = path.lat.map((la, i) => [la, path.lon[i]]);
    L.polyline(all, { color: "#0B0E13", weight: 11, opacity: 0.55, lineCap: "round", lineJoin: "round", interactive: false }).addTo(layer);
    remainLine = L.polyline(all, { color: "#E6EAF0", weight: 4, opacity: 0.85, dashArray: "2 9", lineCap: "round", interactive: false }).addTo(layer);
    doneLine = L.polyline([all[0]], { color: "#FF8A3D", weight: 6, opacity: 1, lineCap: "round", lineJoin: "round", interactive: false }).addTo(layer);
    startDot = L.circleMarker(all[0], { radius: 6, color: "#FF8A3D", weight: 3, fillColor: "#0B0E13", fillOpacity: 1 }).bindTooltip("Journey start").addTo(layer);
    endDot = L.circleMarker(all.at(-1), { radius: 7, color: "#4ADE80", weight: 3, fillColor: "#0B0E13", fillOpacity: 1 }).bindTooltip("Destination").addTo(layer);
    marker = L.marker(all[0], { interactive: false, zIndexOffset: 2000, icon: L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17],
      html: `<div class="jr-marker"><div class="jr-pulse"></div><svg class="jr-arrow" viewBox="0 0 24 24" width="22" height="22"><path d="M12 2 L20 21 L12 16.5 L4 21 Z"/></svg></div>` }) }).addTo(layer);
    layer.addTo(map);
    if (is2d()) map.fitBounds(L.latLngBounds(all).pad(0.25), { animate: true });
    follow2d = true;
  });

  on("tick", (p) => {
    if (!marker) return;
    const P = J.path;
    const done = P.lat.slice(0, p.index + 1).map((la, i) => [la, P.lon[i]]);
    done.push([p.lat, p.lon]);
    doneLine.setLatLngs(done);
    remainLine.setLatLngs([[p.lat, p.lon], ...P.lat.slice(p.index + 1).map((la, i) => [la, P.lon[p.index + 1 + i]])]);
    marker.setLatLng([p.lat, p.lon]);
    const arrow = marker.getElement() && marker.getElement().querySelector(".jr-arrow");
    if (arrow) arrow.style.transform = `rotate(${p.heading}deg)`;
    // follow from above: re-centre smoothly when the marker approaches the edge of the view
    if (follow2d && is2d() && J.status === "running") {
      const now = performance.now();
      if (now - lastPan > 600 && !map.getBounds().pad(-0.3).contains([p.lat, p.lon])) {
        lastPan = now;
        map.panTo([p.lat, p.lon], { animate: true, duration: 0.6, easeLinearity: 0.4 });
      }
    }
    renderPanel();
  });
  map.on("dragstart", () => { if (J.status === "running") follow2d = false; });     // the user takes over the 2D view
  on("close", () => { layer.clearLayers(); map.removeLayer(layer); marker = null; renderPanel(); });
  on("status", () => renderPanel());

  // ------------------------------------------------------------------ mission control panel
  const panel = document.createElement("section");
  panel.className = "jr-panel";
  panel.setAttribute("aria-label", "Route journey");
  panel.hidden = true;
  panel.innerHTML = `
    <div class="jr-row jr-head" data-ref="head" title="Drag to move · double-click to put back">
      <span class="jr-grip" aria-hidden="true">⠿</span>
      <span class="jr-status" data-ref="status">Ready</span>
      <div class="jr-title"><b data-ref="name"></b><small data-ref="fromto"></small></div>
      <span class="jr-pct" data-ref="pct">0%</span>
      <button type="button" class="jr-x jr-miniplay" data-ref="miniplay" title="Pause / resume" aria-label="Pause or resume">⏸</button>
      <button type="button" class="jr-x" data-ref="min" title="Minimise panel" aria-label="Minimise panel">▾</button>
      <button type="button" class="jr-x" data-ref="close" title="Close journey" aria-label="Close journey">✕</button>
    </div>
    <div class="jr-bar" data-ref="bar" title="Click to jump to a point on the route"><div class="jr-fill" data-ref="fill"></div><div class="jr-knob" data-ref="knob"></div></div>
    <div class="jr-row jr-stats">
      <span><small>Travelled</small><b data-ref="done">0 m</b></span>
      <span><small>Remaining</small><b data-ref="left">–</b></span>
      <span><small>Elevation</small><b data-ref="elev">–</b></span>
      <span><small>Position</small><b data-ref="pos">–</b></span>
    </div>
    <div class="jr-row jr-ctrls">
      <button type="button" class="jr-btn jr-primary" data-ref="play">⏸ Pause</button>
      <button type="button" class="jr-btn" data-ref="stop" title="Stop and go back to the start">⏹ Stop</button>
      <button type="button" class="jr-btn" data-ref="restart" title="Start again from the beginning">↺ Restart</button>
      <label class="jr-speed">Speed <select data-ref="speed">${SPEEDS.map((s) => `<option value="${s}">${s} m/s</option>`).join("")}</select></label>
      <div class="jr-cam" data-ref="cam" role="group" aria-label="3D camera">
        <button type="button" data-cam="follow" title="Ground-level camera behind the rover">Immersive</button>
        <button type="button" data-cam="overview" title="Elevated camera that keeps following">Elevated</button>
        <button type="button" data-cam="free" title="Stop following: normal 3D overview">Overview</button>
      </div>
    </div>`;
  document.body.appendChild(panel);
  const ui = {};
  panel.querySelectorAll("[data-ref]").forEach((el) => (ui[el.dataset.ref] = el));
  ui.play.onclick = () => api.toggle();
  ui.miniplay.onclick = () => api.toggle();

  // ---- minimise to a small pill, and drag the panel anywhere (both remembered in this browser)
  const store = { get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } } };
  function setMin(min) {
    panel.classList.toggle("jr-min", min);
    ui.min.textContent = min ? "▴" : "▾";
    ui.min.title = min ? "Expand panel" : "Minimise panel";
    ui.min.setAttribute("aria-label", ui.min.title);
    store.set("marswalk.journey.min", min);
    keepInView();
  }
  ui.min.onclick = () => setMin(!panel.classList.contains("jr-min"));
  function placeAt(x, y) {
    const w = panel.offsetWidth, h = panel.offsetHeight;
    x = Math.max(8, Math.min(innerWidth - w - 8, x)); y = Math.max(8, Math.min(innerHeight - h - 8, y));
    Object.assign(panel.style, { left: x + "px", top: y + "px", bottom: "auto", transform: "none" });
    return { x, y };
  }
  function keepInView() {
    if (!panel.style.top || panel.hidden) return;
    const r = panel.getBoundingClientRect();
    placeAt(r.left, r.top);
  }
  function resetPlace() { Object.assign(panel.style, { left: "", top: "", bottom: "", transform: "" }); store.set("marswalk.journey.pos", null); }
  let drag = null;
  ui.head.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest("button")) return;
    const r = panel.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    ui.head.setPointerCapture(e.pointerId);
    panel.classList.add("jr-dragging");
  });
  ui.head.addEventListener("pointermove", (e) => { if (drag) placeAt(e.clientX - drag.dx, e.clientY - drag.dy); });
  const endDrag = () => {
    if (!drag) return;
    drag = null; panel.classList.remove("jr-dragging");
    const r = panel.getBoundingClientRect();
    store.set("marswalk.journey.pos", { x: r.left, y: r.top });
  };
  ui.head.addEventListener("pointerup", endDrag);
  ui.head.addEventListener("pointercancel", endDrag);
  ui.head.addEventListener("dblclick", (e) => { if (!e.target.closest("button")) resetPlace(); });
  addEventListener("resize", keepInView);
  if (store.get("marswalk.journey.min")) panel.classList.add("jr-min"), (ui.min.textContent = "▴");
  on("start", () => requestAnimationFrame(() => {             // restore the user's spot once the panel has a size
    const pos = store.get("marswalk.journey.pos");
    if (pos) placeAt(pos.x, pos.y);
  }));
  ui.stop.onclick = () => api.stop();
  ui.restart.onclick = () => api.restart();
  ui.close.onclick = () => api.close();
  ui.speed.value = String(J.speed);
  ui.speed.onchange = (e) => api.setSpeed(e.target.value);
  ui.cam.querySelectorAll("button").forEach((b) => (b.onclick = () => api.setCam3d(b.dataset.cam)));
  ui.bar.onclick = (e) => { const r = ui.bar.getBoundingClientRect(); api.seek((e.clientX - r.left) / r.width); };
  document.addEventListener("keydown", (e) => {
    if (!J.path || /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName || "")) return;
    if (e.code === "Space") { e.preventDefault(); api.toggle(); }
  });

  const LABEL = { running: "In progress", paused: "Paused", completed: "Completed", stopped: "Ready", idle: "Ready" };
  function renderPanel() {
    panel.hidden = !J.path;
    if (!J.path) return;
    const p = J.pos || sample(J.dist), a = J.analysis;
    const st = J.status;
    ui.status.textContent = LABEL[st] || st;
    ui.status.dataset.state = st;
    ui.name.textContent = NAMES[J.key];
    const s = AppState.stops;
    const from = J.key === "B" && s ? s.habitat.name.split(" · ")[0] : "Start", to = J.key === "B" && s ? s.stops.at(-1).name.split(" · ")[0] : "Destination";
    ui.fromto.textContent = `${from} → ${to} · ${fmtKm(J.path.total)}${a.max_slope_deg != null ? ` · max ${a.max_slope_deg}°` : ""}`;
    const pct = Math.round(p.fraction * 100);
    ui.pct.textContent = pct + "%";
    ui.fill.style.width = pct + "%";
    ui.knob.style.left = pct + "%";
    ui.done.textContent = fmtKm(p.dist);
    ui.left.textContent = fmtKm(J.path.total - p.dist);
    ui.elev.textContent = p.elev == null ? "no data" : `${Math.round(p.elev).toLocaleString()} m`;
    ui.pos.textContent = `${p.lat.toFixed(5)}°N ${p.lon.toFixed(5)}°E`;
    ui.play.textContent = st === "running" ? "⏸ Pause" : st === "completed" ? "↺ Replay" : "▶ Resume";
    if (st === "stopped") ui.play.textContent = "▶ Start";
    ui.miniplay.textContent = st === "running" ? "⏸" : "▶";
    ui.speed.value = String(J.speed);
    ui.cam.hidden = $id("view3d").hidden;                      // camera modes only matter in 3D
    ui.cam.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.cam === J.cam3d));
  }
  window.addEventListener("marswalk:view", (e) => {
    renderPanel();
    if (e.detail.view === "2d" && J.path) {                     // back to the map: centre on the same journey position
      const p = J.pos || sample(J.dist);
      setTimeout(() => { map.invalidateSize(); map.setView([p.lat, p.lon], Math.max(map.getZoom(), 16), { animate: false }); follow2d = true; }, 80);
    }
  });

  // ------------------------------------------------------------------ "Start journey" in the Route tab
  const box = document.createElement("div");
  box.className = "jr-start";
  box.innerHTML = `<h2>Route journey</h2>
    <div class="jr-start-row">
      <select id="journeyRoute" aria-label="Route to travel"><option value="B">Route B · auto</option><option value="A">Route A · hand</option></select>
      <button id="btnJourney" type="button" class="primary">▶ Start journey</button>
    </div>
    <p class="muted small" id="journeyHint">Create a route first (auto-route or hand-drawn), then start the journey. Switch to 3D at any time for the ground-level view.</p>`;
  const clearBtn = $id("btnClear");
  clearBtn.parentNode.insertBefore(box, clearBtn.nextSibling);
  function updateStartButton() {
    const sel = $id("journeyRoute"), btn = $id("btnJourney");
    for (const o of sel.options) o.disabled = !AppState["route" + o.value];
    if (sel.selectedOptions[0]?.disabled) { const ok = [...sel.options].find((o) => !o.disabled); if (ok) sel.value = ok.value; }
    btn.disabled = !AppState["route" + sel.value];
  }
  $id("journeyRoute").onchange = updateStartButton;
  $id("btnJourney").onclick = () => api.start($id("journeyRoute").value);
  updateStartButton();
})();
