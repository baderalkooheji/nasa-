/**
 * jezero-region.js — Jezero Crater definition and on-globe overlays
 * (marker, IAU crater outline, verified data-coverage outlines).
 */

import { MARS_WKID, METERS_PER_DEGREE } from "./map-layers.js";
import { footprintRings } from "./terrain-loader.js";

/**
 * Jezero facts from the IAU Gazetteer of Planetary Nomenclature
 * (feature 14300, via Esri's Mars nomenclature service).
 */
export const JEZERO = {
  name: "Jezero Crater",
  center: { lon: 77.6873, lat: 18.4082 },
  diameterKm: 47.52,
  /** Study area = verified extent of the CTX DEM (refined from manifest.json at runtime). */
  studyArea: [76.9974, 17.5805, 78.5826, 19.2902],
  source: "IAU Gazetteer of Planetary Nomenclature, feature 14300",
};

/** Camera framing the crater from the south-southwest. */
export function jezeroCamera() {
  return {
    position: { x: 77.56, y: 17.62, z: 38000, spatialReference: { wkid: MARS_WKID } },
    heading: 8,
    tilt: 58,
  };
}

function circle(center, radiusMeters, steps = 128) {
  const pts = [];
  const rDeg = radiusMeters / METERS_PER_DEGREE;
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    const lat = center.lat + rDeg * Math.cos(a);
    const lon = center.lon + (rDeg * Math.sin(a)) / Math.cos((lat * Math.PI) / 180);
    pts.push([lon, lat]);
  }
  return pts;
}

function rect(b) {
  return [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]];
}

/**
 * Creates the overlay layers.
 * @returns {{ markerLayer: any, footprintLayer: any, setFootprints: (fp: object) => void }}
 */
export function createJezeroOverlays(esri) {
  const sr = { wkid: MARS_WKID };

  const markerLayer = new esri.GraphicsLayer({
    id: "jezero-marker",
    title: "Jezero marker",
    elevationInfo: { mode: "relative-to-ground" },
    listMode: "hide",
  });

  const marker = new esri.Graphic({
    geometry: new esri.Point({ x: JEZERO.center.lon, y: JEZERO.center.lat, z: 0, spatialReference: sr }),
    attributes: { name: JEZERO.name },
    symbol: {
      type: "point-3d",
      verticalOffset: { screenLength: 46, maxWorldLength: 400000, minWorldLength: 2000 },
      callout: { type: "line", size: 1.25, color: [255, 138, 61, 0.9] },
      symbolLayers: [
        {
          type: "icon",
          size: 14,
          resource: { primitive: "circle" },
          material: { color: [255, 138, 61, 0.95] },
          outline: { color: [255, 255, 255, 0.95], size: 1.5 },
        },
        {
          type: "text",
          text: "Jezero Crater",
          size: 13,
          material: { color: [255, 255, 255] },
          halo: { color: [10, 12, 20, 0.85], size: 1.4 },
          font: { weight: "bold", family: "sans-serif" },
          verticalAlignment: "bottom",
          screenOffset: { x: 0, y: -12 },
        },
      ],
    },
  });

  const rim = new esri.Graphic({
    geometry: new esri.Polyline({ paths: [circle(JEZERO.center, (JEZERO.diameterKm * 1000) / 2)], spatialReference: sr }),
    attributes: { name: "Jezero rim (IAU diameter)" },
    symbol: {
      type: "line-3d",
      symbolLayers: [{ type: "line", size: 2, material: { color: [255, 138, 61, 0.85] } }],
    },
  });

  // Rim outline is draped; marker floats above the ground.
  const rimLayer = new esri.GraphicsLayer({ id: "jezero-rim", elevationInfo: { mode: "on-the-ground" }, listMode: "hide" });
  rimLayer.add(rim);
  markerLayer.add(marker);

  const footprintLayer = new esri.GraphicsLayer({
    id: "footprints",
    title: "Data coverage",
    elevationInfo: { mode: "on-the-ground" },
    listMode: "hide",
  });

  const STYLE = {
    "ctx-dem": { color: [78, 163, 255, 0.95], label: "CTX DEM / ortho coverage" },
    "hirise-dtm": { color: [120, 255, 200, 0.95], label: "HiRISE DTM coverage" },
  };

  function setFootprints(manifest) {
    footprintLayer.removeAll();
    const ds = manifest?.datasets || {};
    for (const key of Object.keys(STYLE)) {
      if (!ds[key]) continue;
      const rings = footprintRings(ds[key].footprint);
      footprintLayer.add(
        new esri.Graphic({
          geometry: new esri.Polyline({ paths: rings.length ? rings : [rect(ds[key].bounds)], spatialReference: sr }),
          attributes: { id: key, name: STYLE[key].label },
          symbol: {
            type: "line-3d",
            symbolLayers: [{
              type: "line",
              size: 1.5,
              material: { color: STYLE[key].color },
              pattern: { type: "style", style: "dash" },
            }],
          },
        }),
      );
    }
  }

  return { markerLayer, rimLayer, footprintLayer, setFootprints };
}
