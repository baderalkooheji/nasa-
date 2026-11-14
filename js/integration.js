/**
 * integration.js — bridges the 2D Mission Planner (map.js) and 3D Globe (mars3d.js)
 * - Syncs camera/location between views
 * - Shares selected point, route waypoints, stops
 * - Handles 2D ↔ 3D mode switching
 */

import { Mars3D } from "../js/mars3d.js";

const $ = (id) => document.getElementById(id);

// State shared between views
const SharedState = {
  selectedPoint: null,      // { lat, lon, elevation_m, slope_deg, name, kind }
  routeA: null,             // { waypoints: [{lat,lon}], analysis: ... }
  routeB: null,             // { waypoints: [{lat,lon}], analysis: ... }
  stops: null,              // { habitat: {lat,lon}, stops: [{lat,lon,name,id}] }
  maxSlope: 15,
  view: "2d",               // "2d" | "3d"
};

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

// Listen for 3D globe events
Mars3D.on("select", ({ latitude, longitude, elevations }) => {
  const elev = elevations.find(e => e.id === "ctx-dem") || elevations[0];
  SharedState.selectedPoint = {
    lat: latitude,
    lon: longitude,
    elevation_m: elev?.value ?? null,
    slope_deg: null, // 3D doesn't compute slope yet
    kind: "point",
  };
  syncTo2D(["selected"]);
});

Mars3D.on("camera", (c) => {
  // Could sync 2D map center when in 3D mode
});

Mars3D.on("region", ({ region }) => {
  // Region changed in 3D (global/jezero)
});

// Sync 2D → 3D
function syncTo3D(changed) {
  if (SharedState.view !== "3d") return;

  if (changed.includes("selected") && SharedState.selectedPoint) {
    const p = SharedState.selectedPoint;
    Mars3D.goTo({ latitude: p.lat, longitude: p.lon, altitude: 2000 });
  }

  if (changed.includes("routeA") && SharedState.routeA) {
    drawRoute3D("routeA", SharedState.routeA.waypoints, "#F2C14E", "8 6");
  }

  if (changed.includes("routeB") && SharedState.routeB) {
    drawRoute3D("routeB", SharedState.routeB.waypoints, "#5EC8F2", null);
  }

  if (changed.includes("stops") && SharedState.stops) {
    // 3D globe shows stops via its own layer system; could add markers here
  }
}

// Sync 3D → 2D (when user interacts in 3D)
function syncTo2D(changed) {
  if (SharedState.view !== "2d") return;

  if (changed.includes("selected") && SharedState.selectedPoint) {
    const p = SharedState.selectedPoint;
    // map.js select() is internal; simulate by calling the internal handler
    if (window.selectPoint2D) {
      window.selectPoint2D(p.lat, p.lon);
    } else if (window.map) {
      // Fallback: pan map and trigger click handler
      window.map.setView([p.lat, p.lon], Math.max(window.map.getZoom(), 14));
      // The map click handler in map.js will call select()
    }
  }
}

// Draw route on 3D globe (uses ArcGIS polyline graphics)
let routeGraphics3D = { routeA: null, routeB: null };
async function drawRoute3D(id, waypoints, color, dashArray) {
  if (!waypoints || waypoints.length < 2) return;
  if (!Mars3D.isInitialized()) return;

  const view = Mars3D.getView();
  if (!view) return;

  // Remove existing
  if (routeGraphics3D[id]) {
    view.graphics.remove(routeGraphics3D[id]);
  }

  // Create polyline
  const path = waypoints.map(w => [w.lon, w.lat]);
  const { Polyline, Graphic } = await import("esri/geometry/Polyline", "esri/Graphic");
  const polyline = new Polyline({
    paths: [path],
    spatialReference: { wkid: 4326 }, // WGS84 / planetocentric
  });

  const { SimpleLineSymbol } = await import("esri/symbols/SimpleLineSymbol");
  const symbol = new SimpleLineSymbol({
    color,
    width: id === "routeB" ? 5 : 3,
    style: dashArray ? "short-dash" : "solid",
  });

  routeGraphics3D[id] = new Graphic({ geometry: polyline, symbol });
  view.graphics.add(routeGraphics3D[id]);
}

// Clear 3D routes
function clearRoutes3D() {
  if (Mars3D.isInitialized()) {
    const view = Mars3D.getView();
    Object.values(routeGraphics3D).forEach(g => g && view.graphics.remove(g));
    routeGraphics3D = { routeA: null, routeB: null };
  }
}

// Mode switching
function setView(view) {
  SharedState.view = view;
  const is2d = view === "2d";
  $("view2d").hidden = !is2d;
  $("view3d").hidden = is2d;
  $("mode2d").classList.toggle("active", is2d);
  $("mode3d").classList.toggle("active", !is2d);

  if (is2d) {
    // 2D: invalidate map size, sync state
    setTimeout(() => {
      if (window.map && window.map.invalidateSize) {
        window.map.invalidateSize();
      }
      syncTo2D(["selected", "routeA", "routeB", "stops"]);
    }, 50);
  } else {
    // 3D: ensure initialized, sync state
    if (!Mars3D.isInitialized()) {
      Mars3D.init("viewDiv", { dataUrl: "data/processed/" }).then(() => {
        syncTo3D(["selected", "routeA", "routeB", "stops"]);
      });
    } else {
      syncTo3D(["selected", "routeA", "routeB", "stops"]);
    }
  }
}

// Expose for map.js to call when user clicks mode buttons
window.setView = setView;

// Also expose selectPoint2D for 3D→2D sync
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