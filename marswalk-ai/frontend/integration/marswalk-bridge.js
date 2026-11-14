/* Marswalk AI → original Marswalk 2D planner bridge.
 *
 * Add to the ORIGINAL Marswalk index.html, after <script type="module" src="JS/integration.js">:
 *
 *   <link rel="stylesheet" href="http://127.0.0.1:8090/css/assistant.css">
 *   <script src="http://127.0.0.1:8090/js/action-bridge.js"></script>
 *   <script src="http://127.0.0.1:8090/js/assistant.js"></script>
 *   <script src="http://127.0.0.1:8090/integration/marswalk-bridge.js" data-api="http://127.0.0.1:8090"></script>
 *
 * It uses only names the original code already exposes (MARSWALK_TECHNICAL_REFERENCE.md §4.9):
 *   window.map (Leaflet)  ·  nasaLayers / overlays / AppState / chatContext (top-level bindings of js/map.js)
 *   NASA (js/nasa-config.js)  ·  window.setView (js/integration.js)  ·  .tab[data-tab] buttons
 * It does not modify Route A / Route B: assistant routes live in their own Leaflet layer group.
 * The existing "AI assistant" button opens the new panel instead of the old drawer.
 */
(function () {
  "use strict";
  const script = document.currentScript;
  const API = (script && script.dataset.api) || "http://127.0.0.1:8090";
  const g = (name) => { try { return eval(name); } catch { return undefined; } };   // top-level const/let of classic scripts are not window props
  const lmap = window.map;
  if (!lmap || !window.L) { console.warn("[marswalk-ai] Marswalk map not found; bridge not installed"); return; }

  const assistantRoutes = L.layerGroup().addTo(lmap);
  const assistantMarks = L.layerGroup().addTo(lmap);
  const routeById = {};
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------------------------------------------------------------- layers (NASA tiles + backend overlays)
  function layerCatalogue() {
    const NASA = g("NASA") || { basemaps: [], overlays: [] };
    const kw = { viking: ["viking", "global imagery"], ctx_regional: ["ctx regional", "regional ctx"], ctx_trn: ["ctx", "context camera"],
      hirise: ["hirise", "high resolution imagery"], mola: ["terrain map", "elevation map", "mola", "relief", "topography"],
      themis: ["themis", "infrared", "thermal"] };
    const out = [...NASA.basemaps, ...NASA.overlays].map((c) => ({ id: c.id, label: c.name, keywords: kw[c.id] || [] }));
    const st = g("AppState");
    ((st && st.layers && st.layers.layers) || []).filter((l) => l.available !== false)
      .forEach((l) => out.push({ id: l.id, label: l.name, keywords: [l.id.replace(/_/g, " ")] }));
    return out;
  }
  function leafletLayer(id) {
    const nl = g("nasaLayers") || {};
    const ov = g("overlays") || {};
    return nl[id] || ov[id];
  }
  function syncCheckbox(id, visible) {
    // the original layer rows have no ids: match the row by its visible name
    const cat = layerCatalogue().find((x) => x.id === id);
    if (!cat) return;
    document.querySelectorAll("#basemaps .layer, #layers .layer").forEach((row) => {
      const cb = row.querySelector("input[type=checkbox]");
      if (cb && row.textContent.includes(cat.label) && cb.checked !== visible) { cb.checked = visible; }
    });
  }

  // ---------------------------------------------------------------- sections (tabs + 2D/3D)
  const SECTIONS = [
    { id: "layers", label: "Layers tab", keywords: ["layers tab", "layer panel"], tab: "layers" },
    { id: "route", label: "Route planner tab", keywords: ["route planner", "route tab", "route panel", "planner"], tab: "route" },
    { id: "env", label: "Environment tab", keywords: ["environment panel", "environmental panel", "environment tab", "weather panel"], tab: "env" },
    { id: "records", label: "Mission log", keywords: ["mission log", "mission history", "history", "records"], tab: "records" },
    { id: "view_2d", label: "2D map", keywords: ["main map", "2d map", "map view", "2d view"], view: "2d" },
    { id: "view_3d", label: "3D globe", keywords: ["3d globe", "globe", "3d view", "3d map"], view: "3d" },
  ];
  const activeTab = () => (document.querySelector(".tab.active") || {}).dataset?.tab || null;
  const is3d = () => { const v = document.getElementById("view3d"); return v ? !v.hidden : false; };

  // ---------------------------------------------------------------- handlers
  const CLASS_COLOR = { caution: "#F2C14E", over: "#F97316", nodata: "#9AA6B6" };
  const handlers = {
    async display_route(p) {
      if (routeById[p.route_id]) assistantRoutes.removeLayer(routeById[p.route_id]);
      const grp = L.layerGroup();
      const base = p.emphasis === "alternative" ? "#B9A8FF" : "#5EC8F2";
      const cls = p.slope_classes || [];
      let start = 0;
      for (let i = 1; i < p.geometry.length; i++) {          // one polyline per run of the same slope class
        const c = cls[i - 1] || "ok";
        if (i === p.geometry.length - 1 || (cls[i] || "ok") !== c) {
          L.polyline(p.geometry.slice(start, i + 1), { color: CLASS_COLOR[c] || base, weight: p.emphasis === "alternative" ? 3 : 5,
            dashArray: p.emphasis === "alternative" ? "6 6" : null }).addTo(grp);
          start = i;
        }
      }
      grp.eachLayer((l) => l.bindTooltip(`Marswalk AI · ${esc(p.route_id)} ${esc(p.label)}`, { sticky: true }));
      grp.addTo(assistantRoutes);
      routeById[p.route_id] = grp;
      if (is3d()) window.setView && window.setView("2d");
      if (p.fit) lmap.fitBounds(L.latLngBounds(p.geometry).pad(0.25));
      return `${p.route_id} drawn on the Marswalk map`;
    },
    async highlight_location(p) {
      const color = { caution: "#F97316", rover: "#B9A8FF", destination: "#F2C14E" }[p.style] || "#5EC8F2";
      L.circleMarker([p.lat, p.lon], { radius: 9, color, weight: 3, fillOpacity: 0.25 })
        .bindTooltip(esc(p.label || "Marswalk AI"), { permanent: true, direction: "top" }).addTo(assistantMarks);
      return "highlighted";
    },
    async zoom_to_location(p) {
      if (is3d() && window.Mars3D && window.Mars3D.isInitialized() && !p.bounds) {
        await window.Mars3D.goToLocation(p.lat, p.lon, { select: false, altitude: 4000 });
        return "3D camera moved";
      }
      if (p.bounds) lmap.fitBounds(L.latLngBounds(p.bounds).pad(0.2)); else lmap.setView([p.lat, p.lon], p.zoom || 16);
      return "map moved";
    },
    async change_layer(p) {
      const l = leafletLayer(p.layer_id);
      if (!l) throw new Error(`layer '${p.layer_id}' not found on the Marswalk map`);
      const visible = p.visible !== false;
      visible ? l.addTo(lmap) : lmap.removeLayer(l);
      if (p.opacity != null && l.setOpacity) l.setOpacity(p.opacity);
      syncCheckbox(p.layer_id, visible);
      return `${p.layer_id} ${visible ? "on" : "off"}`;
    },
    async clear_route(p) {
      const ids = p.route_id ? [p.route_id] : Object.keys(routeById);
      ids.forEach((id) => { if (routeById[id]) { assistantRoutes.removeLayer(routeById[id]); delete routeById[id]; } });
      assistantMarks.clearLayers();
      return `${ids.length} assistant route(s) cleared (Route A/B untouched)`;
    },
    async navigate_to_section(p) {
      const s = SECTIONS.find((x) => x.id === p.section_id);
      if (!s) throw new Error(`unknown section ${p.section_id}`);
      if (s.view) { if (!window.setView) throw new Error("view switching not available"); window.setView(s.view); return `${s.label} shown`; }
      const tab = document.querySelector(`.tab[data-tab="${s.tab}"]`);
      if (!tab) throw new Error(`tab ${s.tab} not found`);
      if (is3d() && window.setView) window.setView("2d");
      tab.click();
      return `${s.label} opened`;
    },
  };

  const bridge = new MarswalkActionBridge({
    client: "marswalk-2d",
    handlers,
    sections: () => SECTIONS.map(({ id, label, keywords }) => ({ id, label, keywords })),
    layers: layerCatalogue,
    getContext: () => {
      const st = g("AppState") || {};
      const cc = typeof g("chatContext") === "function" ? g("chatContext")() : {};
      const nl = g("nasaLayers") || {};
      const sel = st.selected;
      return {
        marswalk_context: cc,
        section: activeTab(),
        view: is3d() ? "3d" : "2d",
        visible_layers: [...Object.keys(nl).filter((id) => lmap.hasLayer(nl[id])), ...(cc.active_layers || [])],
        map: { center: { lat: +lmap.getCenter().lat.toFixed(6), lon: +lmap.getCenter().lng.toFixed(6) }, zoom: lmap.getZoom() },
        selected: sel ? { lat: sel.lat, lon: sel.lon, name: sel.name || null, elevation_m: sel.elevation_m ?? null,
                          slope_deg: sel.slope_deg ?? null, source_dataset: sel.source_dataset || null } : null,
        displayed_routes: Object.keys(routeById),
      };
    },
  });

  const assistant = MarswalkAssistant.mount({ apiBase: API, bridge });
  // One AI entry point: the Marswalk AI orb. The old top-bar button and its drawer are hidden while the
  // assistant is active (they remain the fallback when the assistant server is not running).
  const btn = document.getElementById("btnChat");
  if (btn) { btn.classList.add("mwai-replaced"); btn.onclick = () => assistant.toggle(true); }
  const oldDrawer = document.getElementById("chat");
  if (oldDrawer) oldDrawer.hidden = true;
  window.marswalkAI = { assistant, bridge };
})();
