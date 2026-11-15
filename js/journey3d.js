/**
 * journey3d.js — immersive 3D rendering of the shared route journey (state in js/journey.js).
 *
 * Uses the existing ArcGIS SceneView of the 3D globe (Mars3D) and its real terrain:
 *  - a rover-like marker that sits on the rendered ground (elevationInfo "relative-to-ground")
 *    and turns with the direction of travel
 *  - the travelled part of the route draped on the terrain
 *  - a third-person camera behind and slightly above the rover, close to the surface
 *
 * Camera height comes from the terrain: the DEM elevation of the route (from the backend profile) and the
 * ground under the camera itself (scene elevation sampler when the view is rendering, otherwise
 * Ground.queryElevation, refreshed a few times per second). The camera never goes below ground + clearance.
 * Heading and height are smoothed with time-based exponential easing, so the camera does not jump or shake.
 * Coordinates: planetocentric lat / east-positive lon on GCS_Mars_2000_Sphere (WKID 104971); z in metres.
 */
import { Mars3D } from "./mars3d.js";
import { loadSdk } from "./sdk.js";
import { MARS_WKID } from "./map-layers.js";

const J = window.MarswalkJourney;
const R = 3396190, DEG = Math.PI / 180;
const SR = { wkid: MARS_WKID };
// back: metres behind the rover · height: metres above the ground under the rover · ahead: look-at point beyond it
const RIG = { follow: { back: 70, height: 20, ahead: 110, minClear: 6 }, overview: { back: 900, height: 650, ahead: 250, minClear: 80 } };

let esri = null, view = null, roverLayer = null, trailLayer = null, rover = null, trail = null;
let transitioning = false, camHeading = null, camZ = null, lastT = 0, lastTrail = 0, lastHeadingDrawn = -999, lastDist = null;
const groundCam = { lat: null, lon: null, z: null, busy: false, at: 0 };

const is3d = () => !document.getElementById("view3d").hidden && Mars3D.isInitialized();
const angDiff = (a, b) => ((b - a + 540) % 360) - 180;

async function ensure() {
  if (!Mars3D.isInitialized()) return false;
  esri ||= await loadSdk();
  view = Mars3D.getView();
  if (!roverLayer || roverLayer.destroyed) {
    trailLayer = new esri.GraphicsLayer({ id: "journey-trail", elevationInfo: { mode: "on-the-ground" }, listMode: "hide" });
    roverLayer = new esri.GraphicsLayer({ id: "journey-rover", elevationInfo: { mode: "relative-to-ground" }, listMode: "hide" });
    view.map.addMany([trailLayer, roverLayer]);
    rover = trail = null;
  }
  return true;
}

function roverSymbol(heading) {
  return {
    type: "point-3d",
    symbolLayers: [
      // body (white, oriented along the direction of travel)
      { type: "object", resource: { primitive: "cube" }, material: { color: [232, 236, 242] }, width: 3.4, depth: 5, height: 1.6, heading, anchor: "bottom" },
      // wheels / chassis shadow line
      { type: "object", resource: { primitive: "cube" }, material: { color: [40, 44, 52] }, width: 4, depth: 5.4, height: 0.7, heading, anchor: "bottom" },
      // orange beacon mast on top, visible from far away
      { type: "object", resource: { primitive: "cone" }, material: { color: [255, 138, 61] }, width: 1.4, depth: 1.4, height: 3.2, heading,
        anchor: "relative", anchorPosition: { x: 0, y: 0, z: -1.0 } },
    ],
  };
}

function offsetPoint(lat, lon, headingDeg, metres) {
  const h = headingDeg * DEG;
  return { lat: lat + (metres * Math.cos(h)) / (R * DEG), lon: lon + (metres * Math.sin(h)) / (R * DEG * Math.cos(lat * DEG)) };
}

/** Ground elevation at the camera position: sync sampler when available, else async queries (cached). */
function groundAt(lat, lon) {
  try {
    const es = view.groundView && view.groundView.elevationSampler;
    if (es) {
      const q = es.queryElevation(new esri.Point({ x: lon, y: lat, z: 0, spatialReference: SR }));
      if (q && Number.isFinite(q.z)) return q.z;
    }
  } catch { /* sampler not available while the view is hidden */ }
  const now = performance.now();
  if (!groundCam.busy && now - groundCam.at > 200) {
    groundCam.busy = true; groundCam.at = now;
    view.map.ground.queryElevation(new esri.Point({ x: lon, y: lat, spatialReference: SR }))
      .then((r) => { groundCam.lat = lat; groundCam.lon = lon; groundCam.z = r && r.geometry ? r.geometry.z : null; })
      .catch(() => {}).finally(() => { groundCam.busy = false; });
  }
  return groundCam.z;
}

/** Camera behind the rover for a given rig, heading and route position. */
function cameraAt(p, mode, heading, smooth) {
  const rig = RIG[mode];
  const cam = offsetPoint(p.lat, p.lon, heading, -rig.back);
  const gRover = Number.isFinite(p.elev) ? p.elev : groundAt(p.lat, p.lon) ?? 0;
  const gCam = groundAt(cam.lat, cam.lon);
  const floor = Math.max(gRover, Number.isFinite(gCam) ? gCam : gRover) + rig.minClear;  // never below the ground
  let z = Math.max(gRover + rig.height, floor);
  if (smooth && camZ != null) z = Math.max(floor, camZ + (z - camZ) * smooth);
  camZ = z;
  const tilt = Math.min(84, Math.atan2(rig.back + rig.ahead, Math.max(5, z - gRover)) / DEG);
  return new esri.Camera({ position: new esri.Point({ x: cam.lon, y: cam.lat, z, spatialReference: SR }), heading, tilt });
}

/** Fold the 3D Layers/Details panels away during a journey (restored afterwards) to keep the terrain visible. */
let savedPanels = null;
function setPanel(id, collapsed) {
  const panel = document.getElementById(id), btn = document.querySelector(`#app .panel-toggle[data-target="${id}"]`);
  if (!panel) return;
  panel.classList.toggle("is-collapsed", collapsed);
  if (btn) btn.setAttribute("aria-expanded", String(!collapsed));
  if (id === "panel-right") document.getElementById("app")?.classList.toggle("right-collapsed", collapsed);
}
function foldPanels(fold) {
  const ids = ["panel-left", "panel-right"];
  if (fold) {
    if (!savedPanels) savedPanels = Object.fromEntries(ids.map((id) => [id, !!document.getElementById(id)?.classList.contains("is-collapsed")]));
    ids.forEach((id) => setPanel(id, true));
  } else if (savedPanels) {
    ids.forEach((id) => setPanel(id, savedPanels[id]));       // restore exactly what the user had
    savedPanels = null;
  }
}

/** Smooth move from the current camera to the journey camera (prediction compensates for movement during it). */
async function transitionTo(mode) {
  if (!(await ensure()) || !J.state.path) return;
  foldPanels(true);
  const s = J.state, dur = 2600;
  const lead = s.status === "running" ? s.speed * (dur / 1000) : 0;
  const p = J.sample(Math.min(s.path.total, s.dist + lead));
  camHeading = p.heading; camZ = null;
  transitioning = true;
  try {
    // never wait forever: if the flight is interrupted or the view stops rendering, follow anyway
    await Promise.race([view.goTo(cameraAt(p, mode, p.heading, 0), { duration: dur, easing: "in-out-cubic" }),
                        new Promise((r) => setTimeout(r, dur + 1500))]);
  } catch { /* interrupted */ } finally { transitioning = false; lastT = performance.now(); }
  if (J.state.path) render(J.state.pos || J.sample(J.state.dist));
}

async function overviewOfRoute() {
  if (!(await ensure()) || !J.state.path) return;
  const P = J.state.path;
  const lats = P.lat, lons = P.lon;
  const cLat = (Math.min(...lats) + Math.max(...lats)) / 2, cLon = (Math.min(...lons) + Math.max(...lons)) / 2;
  const extent = Math.max((Math.max(...lats) - Math.min(...lats)) * R * DEG, (Math.max(...lons) - Math.min(...lons)) * R * DEG * Math.cos(cLat * DEG));
  await Mars3D.goToLocation(cLat, cLon, { altitude: Math.max(1800, extent * 1.4), tilt: 50, heading: 0, duration: 2200, select: false });
}

function drawTrail(p, force) {
  const now = performance.now();
  if (!force && now - lastTrail < 150) return;
  lastTrail = now;
  const P = J.state.path;
  const path = P.lon.slice(0, p.index + 1).map((lo, i) => [lo, P.lat[i]]);
  path.push([p.lon, p.lat]);
  if (path.length < 2) path.unshift([P.lon[0], P.lat[0]]);
  const geometry = new esri.Polyline({ paths: [path], spatialReference: SR });
  if (!trail) {
    trail = new esri.Graphic({ geometry, symbol: { type: "simple-line", color: [255, 138, 61, 0.95], width: 5 } });
    trailLayer.add(trail);
  } else trail.geometry = geometry;
}

function render(p) {
  if (!p || !J.state.path || !is3d() || !esri || !roverLayer) return;   // journey may have closed during a fly-in
  const now = performance.now();
  const dt = Math.min(0.25, lastT ? (now - lastT) / 1000 : 0.016);
  lastT = now;
  const point = new esri.Point({ x: p.lon, y: p.lat, z: 0, spatialReference: SR });
  if (!rover) { rover = new esri.Graphic({ geometry: point, symbol: roverSymbol(p.heading) }); roverLayer.add(rover); lastHeadingDrawn = p.heading; }
  else {
    rover.geometry = point;
    if (Math.abs(angDiff(lastHeadingDrawn, p.heading)) > 2) { rover.symbol = roverSymbol(p.heading); lastHeadingDrawn = p.heading; }
  }
  drawTrail(p, J.state.status !== "running");

  const mode = J.state.cam3d;
  if (mode === "free" || transitioning) return;
  if (view.interacting) { J.setCam3d("free"); return; }        // the user grabbed the camera: hand control over
  // a jump along the route (progress bar, Restart, Stop) re-frames at once; normal movement eases smoothly
  const jumped = lastDist == null || Math.abs(p.dist - lastDist) > Math.max(30, J.state.speed * 0.5);
  lastDist = p.dist;
  if (jumped) { camHeading = p.heading; camZ = null; }
  else camHeading = camHeading == null ? p.heading : camHeading + angDiff(camHeading, p.heading) * (1 - Math.exp(-dt / 0.7));
  view.camera = cameraAt(p, mode, (camHeading + 360) % 360, jumped ? 0 : 1 - Math.exp(-dt / 0.35));
}

// ------------------------------------------------------------------ wiring to the shared journey
J.on("tick", (p) => render(p));
J.on("start", async () => {
  if (!(await ensure())) return;
  roverLayer.removeAll(); trailLayer.removeAll(); rover = trail = null;
  if (is3d()) transitionTo(J.state.cam3d === "free" ? "follow" : J.state.cam3d);
});
J.on("cam3d", (mode) => { if (!is3d() || !J.state.path) return; mode === "free" ? overviewOfRoute() : transitionTo(mode); });
J.on("close", () => {
  if (roverLayer && !roverLayer.destroyed) { roverLayer.removeAll(); trailLayer.removeAll(); }
  rover = trail = null;
  foldPanels(false);
});

// Entering 3D during a journey: same route, same position — fly down to it and keep following.
window.addEventListener("marswalk:view", async (e) => {
  if (e.detail.view !== "3d" || !J.state.path) return;
  for (let i = 0; i < 300 && !Mars3D.isInitialized(); i++) await new Promise((r) => setTimeout(r, 200));
  if (!(await ensure())) return;
  rover = trail = null; roverLayer.removeAll(); trailLayer.removeAll();
  const p = J.state.pos || J.sample(J.state.dist);
  if (J.state.cam3d === "free") { render(p); overviewOfRoute(); } else transitionTo(J.state.cam3d);
});
