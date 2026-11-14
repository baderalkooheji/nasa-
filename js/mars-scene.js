/**
 * mars-scene.js â€” builds the Mars Map + SceneView (no UI code here).
 *
 * Layer stack
 *   ground   : global MOLA/HRSC 200 m  â†’  CTX DEM 20 m  â†’  HiRISE DTM (later wins where it has data)
 *   basemap  : Viking MDIM 2.1 colour  |  MOLA/HRSC colour relief (alternative)
 *   draped   : CTX orthomosaic 5 m  â†’  HiRISE imagery (clipped to HiRISE DTM coverage)
 *   overlays : data-coverage outlines, Jezero rim + marker, selection pin
 */

import { createGlobalLayers, LAYER_INFO, MARS_WKID, SERVICES } from "./map-layers.js";
import { createClippedTileLayer, createLocalElevationLayer, createLocalImageryLayer } from "./terrain-loader.js";
import { createJezeroOverlays, JEZERO } from "./jezero-region.js";
import { initialCamera } from "./camera-controls.js";

/**
 * @param {any} esri  modules from sdk.js
 * @param {HTMLElement|string} container
 * @param {{ manifest: object|null, dataUrl: string, camera?: object, quality?: "low"|"medium"|"high" }} opts
 */
export function createMarsScene(esri, container, { manifest, dataUrl, camera, quality = "high" }) {
  const global = createGlobalLayers(esri);
  const overlays = createJezeroOverlays(esri);
  const ds = manifest?.datasets || {};
  const version = manifest?.generated || ""; // cache-buster for regenerated tiles

  /** registry: id -> { layer, kind, bounds?, footprint?, title, detail, scope } */
  const registry = new Map();
  const register = (id, layer, kind, d) =>
    registry.set(id, { id, layer, kind, bounds: d?.bounds, footprint: d?.footprint, ...(LAYER_INFO[id] || {}) });

  register("global-imagery", global.mdim, "imagery");
  register("global-colordem", global.colorDem, "imagery");
  register("global-elevation", global.elevation, "elevation");

  const groundLayers = [global.elevation];
  const drapedLayers = [];

  // Fallback chain for tiles a local dataset does not have: HiRISE → CTX → global.
  let ctxDem = null;
  if (ds["ctx-dem"]) {
    const l = createLocalElevationLayer(esri, {
      id: "ctx-dem", title: "CTX DEM 20 m", dataset: ds["ctx-dem"], baseUrl: dataUrl, version, fallback: global.elevation,
      copyright: "NASA/JPL-Caltech/MSSS, USGS Astrogeology (Mars 2020 CTX DEM)",
    });
    ctxDem = l;
    groundLayers.push(l);
    register("ctx-dem", l, "elevation", ds["ctx-dem"]);
  }
  if (ds["hirise-dtm"]) {
    const l = createLocalElevationLayer(esri, {
      id: "hirise-dtm", title: "HiRISE DTM", dataset: ds["hirise-dtm"], baseUrl: dataUrl, version, fallback: ctxDem || global.elevation,
      copyright: "NASA/JPL-Caltech/UArizona, USGS Astrogeology (Mars 2020 TRN HiRISE DTM)",
    });
    groundLayers.push(l);
    register("hirise-dtm", l, "elevation", ds["hirise-dtm"]);
  }
  if (ds["ctx-ortho"]) {
    const l = createLocalImageryLayer(esri, {
      id: "ctx-ortho", title: "CTX orthomosaic 5 m", dataset: ds["ctx-ortho"], baseUrl: dataUrl, version,
      copyright: "NASA/JPL-Caltech/MSSS, USGS Astrogeology (Mars 2020 CTX orthomosaic)",
    });
    drapedLayers.push(l);
    register("ctx-ortho", l, "imagery", ds["ctx-ortho"]);
  }
  if (ds["hirise-ortho"]) {
    // Controlled USGS HiRISE ortho, if it was processed locally.
    const l = createLocalImageryLayer(esri, {
      id: "hirise-imagery", title: "HiRISE orthomosaic", dataset: ds["hirise-ortho"], baseUrl: dataUrl, version,
      copyright: "NASA/JPL-Caltech/UArizona, USGS Astrogeology (Mars 2020 TRN HiRISE ortho)",
    });
    drapedLayers.push(l);
    register("hirise-imagery", l, "imagery", ds["hirise-ortho"]);
    const res = ds["hirise-ortho"].tileResolutionMeters;
    Object.assign(registry.get("hirise-imagery"), {
      title: "HiRISE orthomosaic — Jezero",
      detail: `USGS M2020 TRN controlled mosaic · 25 cm source${res ? `, tiled to ${res.toFixed(2)} m` : ""}`,
      controlled: true,
    });
  } else if (ds["hirise-dtm"]) {
    // Esri-hosted HiRISE mosaic, shown only inside the verified HiRISE DTM footprint.
    const l = createClippedTileLayer(esri, {
      id: "hirise-imagery", title: "HiRISE imagery", url: SERVICES.hirise, bounds: ds["hirise-dtm"].bounds, footprint: ds["hirise-dtm"].footprint, minLevel: 12,
      copyright: "NASA/JPL/University of Arizona, Esri",
    });
    drapedLayers.push(l);
    register("hirise-imagery", l, "imagery", ds["hirise-dtm"]);
  }

  register("jezero-marker", overlays.markerLayer, "overlay");
  register("jezero-rim", overlays.rimLayer, "overlay");
  register("footprints", overlays.footprintLayer, "overlay");
  overlays.setFootprints(manifest);

  const map = new esri.Map({
    basemap: global.basemap,
    ground: { layers: groundLayers, surfaceColor: [120, 78, 60], navigationConstraint: { type: "stay-above" } },
    layers: [...drapedLayers, overlays.footprintLayer, overlays.rimLayer, overlays.markerLayer],
  });

  const view = new esri.SceneView({
    container,
    map,
    spatialReference: { wkid: MARS_WKID },
    viewingMode: "global",
    qualityProfile: quality,
    camera: camera || initialCamera(),
    environment: {
      lighting: { type: "virtual", directShadowsEnabled: false },
      atmosphereEnabled: false, // Earth atmosphere model is not physically valid for Mars
      starsEnabled: true,
      background: { type: "color", color: [6, 9, 20, 1] },
    },
    constraints: { altitude: { max: 40_000_000 } },
    popupEnabled: false,
  });
  // Custom zoom / reset buttons live in the host UI. (Passing ui.components to the
  // constructor throws in SDK 5.1.27, so the defaults are trimmed afterwards.)
  try {
    view.ui.remove(["zoom", "navigation-toggle"]);
    view.ui.move("compass", "top-right");
  } catch (err) {
    console.warn("[Marswalk3D] could not trim default UI", err);
  }

  return { map, view, registry, overlays, jezero: JEZERO };
}
