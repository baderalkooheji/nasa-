/**
 * mars3d.js — public, framework-free API of the Marswalk 3D module.
 *
 *   import { Mars3D } from "./js/mars3d.js";
 *   await Mars3D.init(document.getElementById("viewDiv"), { dataUrl: "data/processed/" });
 *   Mars3D.goToJezero();
 *   Mars3D.setVisibleLayer("hirise-dtm", false);
 *   Mars3D.goToLocation(18.44, 77.45);
 *   Mars3D.goToGlobal();
 *   Mars3D.destroy();
 *
 * Events (Mars3D.on(name, cb) → returns an unsubscribe function):
 *   "status"   { state: "loading"|"ready"|"warning"|"error", message }
 *   "region"   { region: "global"|"jezero" }
 *   "camera"   { latitude, longitude, altitude, heading, tilt }
 *   "select"   { latitude, longitude, elevations: [{ id, label, value }] }
 *   "layers"   { id, visible }
 *
 * The module holds a single scene instance; call destroy() before init() again.
 */

import { loadSdk } from "./sdk.js";
import { createMarsScene } from "./mars-scene.js";
import { createCameraControls } from "./camera-controls.js";
import { createLocationSelection } from "./location-selection.js";
import { loadManifest, inBounds } from "./terrain-loader.js";
import { JEZERO } from "./jezero-region.js";

const DEFAULT_DATA_URL = new URL("../data/processed/", import.meta.url).href;
const JEZERO_REGION_ALTITUDE = 800_000; // below this, over the study area → "jezero" region

let state = null;
const listeners = new Map();

function emit(name, payload) {
  (listeners.get(name) || []).forEach((cb) => {
    try { cb(payload); } catch (err) { console.error(err); }
  });
}

function requireScene() {
  if (!state) throw new Error("Mars3D is not initialised — call Mars3D.init(container) first");
  return state;
}

/**
 * Create the 3D Mars scene inside `container`.
 * @param {HTMLElement|string} container  element or element id
 * @param {{ dataUrl?: string, sdkVersion?: string, autoSpin?: boolean,
 *           camera?: object, quality?: "low"|"medium"|"high" }} [options]
 * @returns {Promise<typeof Mars3D>}
 */
async function init(container, options = {}) {
  if (state) throw new Error("Mars3D already initialised — call Mars3D.destroy() first");
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) throw new Error("Mars3D.init: container not found");

  const dataUrl = new URL(options.dataUrl || DEFAULT_DATA_URL, document.baseURI).href.replace(/\/?$/, "/");
  emit("status", { state: "loading", message: "Loading ArcGIS Maps SDK…" });

  const esri = await loadSdk({ version: options.sdkVersion });
  emit("status", { state: "loading", message: "Loading Jezero data index…" });
  const manifest = await loadManifest(dataUrl);

  const scene = createMarsScene(esri, el, { manifest, dataUrl, camera: options.camera, quality: options.quality });
  const { view, registry } = scene;
  const camera = createCameraControls(esri, view);
  const handles = [];

  state = { esri, scene, view, registry, camera, manifest, dataUrl, handles, region: "global", selection: null };

  // ---- location selection
  state.selection = createLocationSelection(esri, view, {
    elevationSources: () =>
      ["hirise-dtm", "ctx-dem", "global-elevation"]
        .filter((id) => registry.has(id))
        .map((id) => {
          const e = registry.get(id);
          return { id, label: e.title, layer: e.layer, bounds: e.bounds, footprint: e.footprint };
        }),
    onSelect: (info) => emit("select", info),
  });

  // ---- status / region / camera watchers
  handles.push(
    esri.reactiveUtils.watch(
      () => view.updating,
      (updating) => emit("status", { state: updating ? "loading" : "ready", message: updating ? "Streaming tiles…" : "Ready" }),
    ),
    esri.reactiveUtils.watch(() => view.camera, emitCamera),
    // view.center lags behind the camera during animations; re-emit once settled.
    esri.reactiveUtils.when(() => view.stationary, emitCamera),
  );

  const self = state;
  function emitCamera() {
    const c = view.camera;
    if (!c || state !== self) return; // ignore late callbacks after destroy()
    const centre = view.center;
    const lon = centre?.longitude ?? c.position.longitude;
    const lat = centre?.latitude ?? c.position.latitude;
    const region = c.position.z < JEZERO_REGION_ALTITUDE && inBounds(expand(JEZERO.studyArea, 1.5), lon, lat) ? "jezero" : "global";
    if (region !== self.region) {
      self.region = region;
      emit("region", { region });
    }
    emit("camera", {
      latitude: c.position.latitude, longitude: c.position.longitude, altitude: c.position.z,
      heading: c.heading, tilt: c.tilt, centerLatitude: lat, centerLongitude: lon,
    });
  }

  try {
    await view.when();
  } catch (err) {
    destroy(); // leave the module re-initialisable
    emit("status", { state: "error", message: "3D view failed to start (WebGL unavailable?)" });
    throw err;
  }
  emit("status", { state: view.updating ? "loading" : "ready", message: view.updating ? "Streaming tiles…" : "Ready" });

  // Report layers that failed to load (e.g. service offline) instead of breaking the scene.
  for (const [id, entry] of registry) {
    entry.layer.when?.().catch?.((err) => {
      entry.failed = true;
      console.warn(`[Marswalk3D] layer ${id} unavailable`, err);
      emit("layers", { id, visible: false, failed: true });
      emit("status", { state: "warning", message: `${entry.title || id} unavailable` });
    });
  }

  if (!manifest) {
    emit("status", { state: "warning", message: "Jezero terrain not prepared — see docs/SETUP_GUIDE.md" });
  }
  if (options.autoSpin !== false) camera.startSpin();
  return Mars3D;
}

function expand(b, d) {
  return [b[0] - d, b[1] - d, b[2] + d, b[3] + d];
}

/** Fly from wherever the camera is into the Jezero crater view. */
function goToJezero(opts) { return requireScene().camera.goToJezero(opts); }

/** Pull back to the whole planet. */
function goToGlobal(opts) { return requireScene().camera.goToGlobal(opts); }

/**
 * Fly to a location.
 * @param {number} latitude  planetocentric, degrees N
 * @param {number} longitude degrees E (−180…180 or 0…360)
 * @param {{altitude?:number, tilt?:number, heading?:number, duration?:number, select?:boolean}} [opts]
 */
async function goToLocation(latitude, longitude, opts = {}) {
  const s = requireScene();
  const lon = longitude > 180 ? longitude - 360 : longitude;
  await s.camera.goToLocation(latitude, lon, opts);
  if (opts.select !== false) await s.selection.inspect(lon, latitude);
}

/**
 * Show or hide a layer.
 * Ids: global-imagery, global-colordem, global-elevation, ctx-dem, ctx-ortho,
 *      hirise-dtm, hirise-imagery, jezero-marker, jezero-rim, footprints
 */
function setVisibleLayer(layerId, visible) {
  const s = requireScene();
  const entry = s.registry.get(layerId);
  if (!entry) return false;
  entry.layer.visible = !!visible;
  emit("layers", { id: layerId, visible: !!visible });
  return true;
}

/** Switch basemap between "mdim" (colour imagery) and "colordem" (colour shaded relief). */
function setBasemap(name) {
  const s = requireScene();
  const relief = name === "colordem";
  s.registry.get("global-imagery").layer.visible = !relief;
  s.registry.get("global-colordem").layer.visible = relief;
  emit("layers", { id: "global-imagery", visible: !relief });
  emit("layers", { id: "global-colordem", visible: relief });
}

/** Description of every registered layer (for building UIs). */
function getLayers() {
  const s = requireScene();
  return [...s.registry.values()].map(({ id, kind, title, detail, scope, bounds, footprint, layer, failed, controlled }) => ({
    id, kind, title, detail, scope, bounds, footprint, visible: layer.visible, failed: !!failed, controlled: !!controlled,
  }));
}

function zoomIn() { requireScene().camera.zoomIn(); }
function zoomOut() { requireScene().camera.zoomOut(); }
function resetCamera() { requireScene().camera.reset(); }
function getRegion() { return requireScene().region; }
function getView() { return state?.view ?? null; }
function getManifest() { return state?.manifest ?? null; }
function isInitialized() { return !!state; }

/** Subscribe to an event; returns an unsubscribe function. */
function on(name, cb) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(cb);
  return () => listeners.get(name)?.delete(cb);
}

/** Tear down the scene and free GPU / network resources. Safe to call twice. */
function destroy() {
  if (!state) return;
  const s = state;
  state = null;
  s.camera.stopSpin();
  s.handles.forEach((h) => h.remove());
  s.selection.destroy();
  const container = s.view.container;
  s.view.destroy(); // also destroys map, layers and graphics it owns
  if (container && container.replaceChildren) container.replaceChildren();
}

export const Mars3D = {
  init, destroy, isInitialized,
  goToJezero, goToGlobal, goToLocation,
  setVisibleLayer, setBasemap, getLayers,
  zoomIn, zoomOut, resetCamera,
  getRegion, getView, getManifest,
  on,
  JEZERO,
};

// Convenience global for non-module hosts and the browser console.
if (typeof window !== "undefined") window.Mars3D = Mars3D;
export default Mars3D;
