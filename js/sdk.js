/**
 * sdk.js — loads the ArcGIS Maps SDK for JavaScript exactly once and resolves
 * the modules Marswalk 3D needs.
 *
 * If the host page already loaded the SDK (global `$arcgis`), it is reused, so
 * embedding this module never creates a second SDK instance. Otherwise the CDN
 * script and theme stylesheet are injected.
 */

const DEFAULT_VERSION = "5.1";

const MODULES = {
  Map: "@arcgis/core/Map.js",
  SceneView: "@arcgis/core/views/SceneView.js",
  Basemap: "@arcgis/core/Basemap.js",
  TileLayer: "@arcgis/core/layers/TileLayer.js",
  ElevationLayer: "@arcgis/core/layers/ElevationLayer.js",
  BaseElevationLayer: "@arcgis/core/layers/BaseElevationLayer.js",
  BaseTileLayer: "@arcgis/core/layers/BaseTileLayer.js",
  GraphicsLayer: "@arcgis/core/layers/GraphicsLayer.js",
  Graphic: "@arcgis/core/Graphic.js",
  TileInfo: "@arcgis/core/layers/support/TileInfo.js",
  Extent: "@arcgis/core/geometry/Extent.js",
  Point: "@arcgis/core/geometry/Point.js",
  Polyline: "@arcgis/core/geometry/Polyline.js",
  SpatialReference: "@arcgis/core/geometry/SpatialReference.js",
  Camera: "@arcgis/core/Camera.js",
  esriRequest: "@arcgis/core/request.js",
  reactiveUtils: "@arcgis/core/core/reactiveUtils.js",
  promiseUtils: "@arcgis/core/core/promiseUtils.js",
  scheduling: "@arcgis/core/core/scheduling.js",
};

let sdkPromise = null;

function injectOnce(tag, attrs) {
  const selector = `${tag}[${tag === "link" ? "href" : "src"}="${attrs.href || attrs.src}"]`;
  if (document.querySelector(selector)) return null;
  const el = document.createElement(tag);
  Object.assign(el, attrs);
  document.head.appendChild(el);
  return el;
}

function waitForGlobal(script) {
  if (window.$arcgis) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = script || document.querySelector('script[src*="js.arcgis.com"]');
    existing?.addEventListener("error", () => reject(new Error("Failed to load the ArcGIS Maps SDK from the CDN")));
    const t = setInterval(() => { if (window.$arcgis) { clearInterval(t); resolve(); } }, 50);
    setTimeout(() => { clearInterval(t); if (!window.$arcgis) reject(new Error("Timed out loading the ArcGIS Maps SDK")); }, 30000);
  });
}

/**
 * @param {{version?: string, theme?: "dark"|"light"}} [opts]
 * @returns {Promise<Record<string, any>>} resolved SDK modules keyed by short name
 */
export function loadSdk(opts = {}) {
  if (sdkPromise) return sdkPromise;
  const version = opts.version || DEFAULT_VERSION;
  const theme = opts.theme || "dark";

  sdkPromise = (async () => {
    let script = null;
    if (!window.$arcgis) {
      injectOnce("link", { rel: "stylesheet", href: `https://js.arcgis.com/${version}/esri/themes/${theme}/main.css` });
      script = injectOnce("script", { type: "module", src: `https://js.arcgis.com/${version}/` });
    }
    await waitForGlobal(script);
    const names = Object.keys(MODULES);
    const mods = await window.$arcgis.import(names.map((n) => MODULES[n]));
    const out = {};
    names.forEach((n, i) => (out[n] = mods[i]));
    return out;
  })();

  sdkPromise.catch(() => (sdkPromise = null));
  return sdkPromise;
}
