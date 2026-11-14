/**
 * map-layers.js — global Mars layers (Esri astro.arcgis.com services) and the
 * shared Mars tiling scheme.
 *
 * All services were verified to use GCS_Mars_2000_Sphere (WKID 104971,
 * R = 3 396 190 m) with a 512-px tiling scheme anchored at (-180, 90).
 */

import { createCappedTileLayer } from "./terrain-loader.js";

export const MARS_WKID = 104971;
export const MARS_RADIUS = 3396190;
export const METERS_PER_DEGREE = (Math.PI * MARS_RADIUS) / 180; // ≈ 59 274.7 m

export const SERVICES = {
  mdim: "https://astro.arcgis.com/arcgis/rest/services/OnMars/MDIM/MapServer",
  elevation: "https://astro.arcgis.com/arcgis/rest/services/OnMars/MDEM200M/ImageServer",
  colorDem: "https://astro.arcgis.com/arcgis/rest/services/OnMars/MColorDEM/MapServer",
  hirise: "https://astro.arcgis.com/arcgis/rest/services/OnMars/HiRISE/MapServer",
};

/** Static descriptions shown in the UI (resolution / provenance). */
export const LAYER_INFO = {
  "global-imagery": {
    title: "Viking MDIM 2.1 colour mosaic",
    detail: "Global · ~232 m/px · USGS / NASA / JPL via Esri",
    scope: "global",
  },
  "global-colordem": {
    title: "MOLA/HRSC colour shaded relief",
    detail: "Global · USGS / NASA / ESA / DLR via Esri",
    scope: "global",
  },
  "global-elevation": {
    title: "MOLA/HRSC blended DEM",
    detail: "Global · 200 m · NASA / ESA / DLR / USGS via Esri",
    scope: "global",
  },
  "ctx-dem": {
    title: "CTX DEM — Jezero",
    detail: "JPL Mars 2020 science investigation map · 20 m",
    scope: "local",
  },
  "ctx-ortho": {
    title: "CTX orthomosaic — Jezero",
    detail: "JPL Mars 2020 science investigation map · 5 m",
    scope: "local",
  },
  "hirise-dtm": {
    title: "HiRISE DTM mosaic — Jezero",
    detail: "USGS M2020 TRN · 1 m source, tiled to ~2.5 m",
    scope: "local",
  },
  "hirise-imagery": {
    title: "HiRISE image mosaic",
    detail: "Esri / UA uncontrolled mosaic · ≤1 m · clipped to DTM coverage",
    scope: "local",
  },
};

/**
 * Esri tiling scheme used by every Mars service on astro.arcgis.com.
 * @param {any} esri  modules from sdk.js
 * @param {number} maxLevel
 */
export function createMarsTileInfo(esri, maxLevel = 17) {
  const lods = [];
  for (let level = 0; level <= maxLevel; level++) {
    lods.push({
      level,
      resolution: 0.3515625 / 2 ** level,
      scale: 78760513.4003741 / 2 ** level,
    });
  }
  return esri.TileInfo.fromJSON({
    rows: 512,
    cols: 512,
    dpi: 96,
    format: "png",
    origin: { x: -180, y: 90 },
    spatialReference: { wkid: MARS_WKID },
    lods,
  });
}

/**
 * Creates the global imagery basemap, colour-relief alternative and global elevation.
 * @param {any} esri
 */
export function createGlobalLayers(esri) {
  // Both services advertise 18 levels but are only cached to level 7 (verified 2026-11-14).
  const mdim = createCappedTileLayer(esri, {
    id: "global-imagery",
    url: SERVICES.mdim,
    maxCachedLevel: 7,
    title: "Viking MDIM 2.1",
    copyright: "USGS Astrogeology Science Center, NASA, JPL, Esri",
  });

  const colorDem = createCappedTileLayer(esri, {
    id: "global-colordem",
    url: SERVICES.colorDem,
    maxCachedLevel: 7,
    title: "MOLA/HRSC colour relief",
    copyright: "USGS Astrogeology Science Center, NASA, JPL, ESA, DLR, Esri",
    visible: false,
  });

  const basemap = new esri.Basemap({
    id: "mars-basemap",
    title: "Mars",
    baseLayers: [mdim, colorDem],
  });

  const elevation = new esri.ElevationLayer({
    id: "global-elevation",
    url: SERVICES.elevation,
    title: "MOLA/HRSC 200 m DEM",
    copyright: "NASA, ESA, HRSC, Goddard Space Flight Center, USGS Astrogeology Science Center, Esri",
  });

  return { basemap, mdim, colorDem, elevation };
}
