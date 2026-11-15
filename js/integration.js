/**
 * integration.js — bridges the 2D Mission Planner (map.js) and 3D Globe (mars3d.js)
 * - Syncs the selected point both ways (2D click → 3D camera, 3D click → 2D selection panel)
 * - Draws routes A and B on the 3D globe (draped on the terrain)
 * - Handles 2D ↔ 3D mode switching
 *
 * Coordinates everywhere: planetocentric latitude, east-positive longitude on the
 * Mars 2000 sphere. The 3D scene uses GCS_Mars_2000_Sphere (WKID 104971).
 */

import { Mars3D } from "../js/mars3d.js";
import { loadSdk } from "../js/sdk.js";
import { MARS_WKID } from "../js/map-layers.js";

const $ = (id) => document.getElementById(id);

// State shared between views
const SharedState = {
  selectedPoint: null,      // { lat, lon, elevation_m, slope_deg, name, kind }
  routeA: null,             // route analysis from map.js (profile [{lat, lon, …}], waypoints?)
  routeB: null,
  stops: null,
  maxSlope: 15,
  view: "2d",               // "2d" | "3d"
};

// Last point picked on the globe: used to avoid flying the camera back to a point the user just clicked in 3D.
let lastGlobePick = null;
const samePoint = (a, b) => a && b && Math.abs(a.lat - b.lat) < 1e-7 && Math.abs(a.lon - b.lon) < 1e-7;

// Listen for state changes from 2D map (map.js uses CustomEvent "appstate")
window.addEventListener("appstate", (e) => {
  const { state, changed } = e.detail;
  if (changed.includes("selected")) SharedState.selectedPoint = state.selected;
  if (changed.includes("routeA")) SharedState.routeA = state.routeA;
  if (changed.includes("routeB")) SharedState.routeB = state.routeB;
  if (changed.includes("stops")) SharedState.stops = state.stops;
  if (changed.includes("maxSlope")) SharedState.maxSlope = state.maxSlope;
  syncTo3D(changed);
});

// A click on the globe selects the same point in the 2D planner (right-hand "Selected location" panel),
// whichever view is visible. map.js select() asks the backend for elevation/slope as usual.
Mars3D.on("select", ({ latitude, longitude }) => {
  lastGlobePick = { lat: latitude, lon: longitude };
  if (typeof window.select === "function") window.select({ lat: latitude, lon: longitude, kind: "point" });
});

// Sync 2D → 3D (only while the globe is visible; setView("3d") re-syncs everything on entry)
function syncTo3D(changed) {
  if (SharedState.view !== "3d" || !Mars3D.isInitialized()) return;

  if (changed.includes("selected") && SharedState.selectedPoint) {
    const p = SharedState.selectedPoint;
    // during a route journey the journey camera owns the 3D view (js/journey3d.js)
    if (!samePoint(p, lastGlobePick) && !document.body.classList.contains("journey-active")) {
      // select:false so the globe does not emit "select" again (no 2D ↔ 3D ping-pong)
      Mars3D.goToLocation(p.lat, p.lon, { altitude: 4000, tilt: 55, select: false }).catch((err) => console.warn("[integration] goToLocation", err));
    }
  }
  if (changed.includes("routeA")) drawRoute3D("routeA", SharedState.routeA).catch((err) => console.warn("[integration] route A", err));
  if (changed.includes("routeB")) drawRoute3D("routeB", SharedState.routeB).catch((err) => console.warn("[integration] route B", err));
}

// ---------------------------------------------------------------- routes on the globe
const ROUTE_STYLE = {
  routeA: { color: [242, 193, 78, 0.95], width: 3, dash: "short-dash" },   // hand-drawn, as in 2D
  routeB: { color: [94, 200, 242, 0.95], width: 5, dash: "solid" },        // auto-route
};
let routeLayer = null;                    // GraphicsLayer draped on the terrain
const routeGraphics = { routeA: null, routeB: null };

/** Route geometry as [[lon, lat], …]: the analysed profile when present, else the waypoints. */
function routePath(analysis) {
  if (!analysis) return [];
  const pts = (analysis.profile && analysis.profile.length >= 2 ? analysis.profile : analysis.waypoints) || [];
  return pts.filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon)).map((p) => [p.lon, p.lat]);
}

async function drawRoute3D(id, analysis) {
  if (!Mars3D.isInitialized()) return;
  const view = Mars3D.getView();
  if (!view) return;
  const esri = await loadSdk();           // same SDK instance the globe already loaded
  if (!routeLayer || routeLayer.destroyed) {
    routeLayer = new esri.GraphicsLayer({ id: "marswalk-routes", title: "Routes", elevationInfo: { mode: "on-the-ground" }, listMode: "hide" });
    view.map.add(routeLayer);
  }
  if (routeGraphics[id]) {
    routeLayer.remove(routeGraphics[id]);
    routeGraphics[id] = null;
  }
  const path = routePath(analysis);
  if (path.length < 2) return;            // route cleared or not analysed yet
  const st = ROUTE_STYLE[id];
  routeGraphics[id] = new esri.Graphic({
    geometry: new esri.Polyline({ paths: [path], spatialReference: { wkid: MARS_WKID } }),
    symbol: { type: "simple-line", color: st.color, width: st.width, style: st.dash },
    attributes: { route: id },
  });
  routeLayer.add(routeGraphics[id]);
}

// ---------------------------------------------------------------- start-up of the globe
// app.js already calls Mars3D.init("viewDiv") when the page loads. Starting a second init here
// would race with it, so wait for that one instead.
function whenGlobeReady(timeoutMs = 60000) {
  if (Mars3D.isInitialized()) return Promise.resolve(true);
  return new Promise((resolve) => {
    const t0 = Date.now();
    const timer = setInterval(() => {
      if (Mars3D.isInitialized()) { clearInterval(timer); resolve(true); }
      else if (Date.now() - t0 > timeoutMs) { clearInterval(timer); resolve(false); }
    }, 200);
  });
}

// Mode switching
function setView(view) {
  SharedState.view = view;
  const is2d = view === "2d";
  $("view2d").hidden = !is2d;
  $("view3d").hidden = is2d;
  $("mode2d").classList.toggle("active", is2d);
  $("mode3d").classList.toggle("active", !is2d);
  window.dispatchEvent(new CustomEvent("marswalk:view", { detail: { view } }));   // e.g. the route journey follows the switch

  if (is2d) {
    setTimeout(() => { if (window.map && window.map.invalidateSize) window.map.invalidateSize(); }, 50);
    return;
  }
  whenGlobeReady().then((ok) => {
    if (!ok) { console.warn("[integration] 3D globe did not start (see the status bar in the 3D view)"); return; }
    if (SharedState.view === "3d") syncTo3D(["selected", "routeA", "routeB", "stops"]);
  });
}

// Expose for map.js to call when user clicks mode buttons
window.setView = setView;

// Also expose selectPoint2D for other modules (pans the 2D map and selects the point)
window.selectPoint2D = (lat, lon) => {
  if (window.map && window.select) {
    window.map.setView([lat, lon], Math.max(window.map.getZoom(), 14));
    window.select({ lat, lon, kind: "point" });
  }
};

// Initialize mode buttons
$("mode2d").onclick = () => setView("2d");
$("mode3d").onclick = () => setView("3d");

// Initial state: 2D
setView("2d");
