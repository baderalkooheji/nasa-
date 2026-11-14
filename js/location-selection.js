/**
 * location-selection.js — click-to-inspect: coordinates, elevation from every
 * elevation source that covers the point, and a pin on the surface.
 */

import { MARS_WKID } from "./map-layers.js";
import { covers } from "./terrain-loader.js";

/**
 * @param {any} esri
 * @param {any} view SceneView
 * @param {{ elevationSources: () => Array<{id:string,label:string,layer:any,bounds?:number[],footprint?:object}>,
 *           onSelect: (info: object) => void }} opts
 */
export function createLocationSelection(esri, view, { elevationSources, onSelect }) {
  const pinLayer = new esri.GraphicsLayer({ id: "selection", elevationInfo: { mode: "relative-to-ground" }, listMode: "hide" });
  view.map.add(pinLayer);

  const pinSymbol = {
    type: "point-3d",
    verticalOffset: { screenLength: 24, maxWorldLength: 50000, minWorldLength: 5 },
    callout: { type: "line", size: 1, color: [78, 163, 255, 1] },
    symbolLayers: [{
      type: "icon",
      size: 10,
      resource: { primitive: "circle" },
      material: { color: [78, 163, 255, 1] },
      outline: { color: [255, 255, 255, 1], size: 1.5 },
    }],
  };

  async function inspect(lon, lat) {
    const point = new esri.Point({ x: lon, y: lat, spatialReference: { wkid: MARS_WKID } });
    pinLayer.removeAll();
    pinLayer.add(new esri.Graphic({ geometry: point, symbol: pinSymbol }));

    const sources = elevationSources().filter((s) => s.layer?.visible !== false && (!s.bounds || covers(s, lon, lat)));
    const elevations = await Promise.all(
      sources.map(async (s) => {
        try {
          const r = await s.layer.queryElevation(point, { demResolution: "finest-contiguous" });
          const z = r?.geometry?.z;
          const valid = Number.isFinite(z) && z > -20000;
          return { id: s.id, label: s.label, value: valid ? z : null, resolution: r?.sampleInfo?.[0]?.demResolution ?? null };
        } catch (err) {
          return { id: s.id, label: s.label, value: null, error: String(err?.message || err) };
        }
      }),
    );

    const info = { latitude: lat, longitude: lon, elevations };
    onSelect?.(info);
    return info;
  }

  const handle = view.on("click", (event) => {
    const p = event.mapPoint;
    if (!p) return;
    inspect(p.longitude ?? p.x, p.latitude ?? p.y);
  });

  return {
    inspect,
    clear: () => pinLayer.removeAll(),
    destroy: () => { handle.remove(); view.map?.remove(pinLayer); },
  };
}
