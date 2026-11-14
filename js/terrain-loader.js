/**
 * terrain-loader.js — streams the locally prepared Jezero tiles
 * (data/processed, produced by scripts/prepare-terrain/prepare_terrain.py).
 *
 *  - LocalElevationLayer  : CTX DEM / HiRISE DTM as real terrain (Terrain-RGB PNG tiles)
 *  - LocalImageryLayer    : CTX orthomosaic draped on that terrain
 *  - ClippedTileLayer     : Esri HiRISE imagery, restricted to the verified DTM footprint
 *
 * Tiles are only requested by the SDK for the part of the globe in view and at
 * the level of detail the camera needs, so nothing is loaded until the camera
 * approaches Jezero. A tile index from manifest.json prevents requests for
 * tiles that do not exist.
 */

import { createMarsTileInfo, MARS_WKID } from "./map-layers.js";

const ELEV_NODATA = -99999;
const DECODE_CANVAS = typeof OffscreenCanvas !== "undefined"
  ? new OffscreenCanvas(257, 257)
  : Object.assign(document.createElement("canvas"), { width: 257, height: 257 });

/**
 * Loads data/processed/manifest.json.
 * @param {string} baseUrl  URL of the processed data folder (ending with "/")
 * @returns {Promise<object|null>} manifest, or null if not available
 */
export async function loadManifest(baseUrl) {
  try {
    const res = await fetch(new URL("manifest.json", baseUrl), { cache: "no-cache" });
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.warn("[Marswalk3D] terrain manifest unavailable:", err);
    return null;
  }
}

function buildIndex(ds) {
  const index = new Map();
  for (const [level, tiles] of Object.entries(ds.tiles || {})) {
    for (const t of tiles) index.set(`${level}/${t[0]}/${t[1]}`, t[2]);
  }
  return index;
}

function tileBounds(level, row, col) {
  const span = 180 / 2 ** level;
  const west = -180 + col * span;
  const north = 90 - row * span;
  return [west, north - span, west + span, north];
}

function intersects(a, b) {
  return a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
}

function makeExtent(esri, b) {
  return new esri.Extent({ xmin: b[0], ymin: b[1], xmax: b[2], ymax: b[3], spatialReference: { wkid: MARS_WKID } });
}

async function decodeTerrainRgb(blob) {
  const bmp = await createImageBitmap(blob, { premultiplyAlpha: "none", colorSpaceConversion: "none" });
  const { width, height } = bmp;
  if (DECODE_CANVAS.width !== width) DECODE_CANVAS.width = width;
  if (DECODE_CANVAS.height !== height) DECODE_CANVAS.height = height;
  const ctx = DECODE_CANVAS.getContext("2d", { willReadFrequently: true });
  ctx.clearRect(0, 0, width, height);
  ctx.drawImage(bmp, 0, 0);
  bmp.close?.();
  const px = ctx.getImageData(0, 0, width, height).data;
  const values = new Float32Array(width * height);
  for (let i = 0, j = 0; i < values.length; i++, j += 4) {
    values[i] = px[j + 3] === 0
      ? ELEV_NODATA
      : (px[j] * 65536 + px[j + 1] * 256 + px[j + 2]) * 0.1 - 10000;
  }
  return { values, width, height };
}

/**
 * Resamples part of a corner-inclusive elevation grid into a new size×size grid.
 * (r0, c0) is the top-left of the wanted region and `span` its size, both as
 * fractions of the source tile.
 */
function cropElevation(src, r0, c0, span, size = 257) {
  const { values, width, height, noDataValue } = src;
  const out = new Float32Array(size * size);
  for (let i = 0; i < size; i++) {
    const y = (r0 + (i / (size - 1)) * span) * (height - 1);
    const y0 = Math.min(height - 2, Math.floor(y)), fy = y - y0;
    for (let j = 0; j < size; j++) {
      const x = (c0 + (j / (size - 1)) * span) * (width - 1);
      const x0 = Math.min(width - 2, Math.floor(x)), fx = x - x0;
      const a = values[y0 * width + x0], b = values[y0 * width + x0 + 1];
      const c = values[(y0 + 1) * width + x0], d = values[(y0 + 1) * width + x0 + 1];
      out[i * size + j] =
        a === noDataValue || b === noDataValue || c === noDataValue || d === noDataValue
          ? [a, b, c, d][Math.round(fy) * 2 + Math.round(fx)]
          : (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
    }
  }
  return { values: out, width: size, height: size, noDataValue };
}

/**
 * Fetches tile (level, row, col) from `layer`; for levels deeper than the
 * layer's finest level, the finest ancestor is cropped and upsampled.
 */
function fetchFromFallback(layer, level, row, col, options) {
  const maxLevel = layer.tileInfo.lods.length - 1;
  if (level <= maxLevel) return layer.fetchTile(level, row, col, options);
  const n = 2 ** (level - maxLevel);
  return layer
    .fetchTile(maxLevel, Math.floor(row / n), Math.floor(col / n), options)
    .then((t) => cropElevation(t, (row % n) / n, (col % n) / n, 1 / n));
}

/**
 * Elevation layer backed by local Terrain-RGB tiles.
 *
 * Every tile the SDK asks for must be answered with complete data: the terrain
 * mesh does not honour per-pixel no-data (empty pixels become deep trenches) and
 * a rejected tile is logged as an error. So, where this dataset has no tile
 * (outside its levels or footprint), the matching tile of `fallback` (the next
 * dataset down: HiRISE → CTX → global MOLA/HRSC) is served instead. Inside its
 * footprint the tiles themselves are complete, because the preparation script
 * fills their margins from the same surrounding dataset.
 */
export function createLocalElevationLayer(esri, { id, title, dataset, baseUrl, copyright, fallback, version = "" }) {
  const index = buildIndex(dataset);
  const bounds = dataset.bounds;
  const root = new URL(dataset.path.replace(/\/?$/, "/"), baseUrl);

  const Layer = esri.BaseElevationLayer.createSubclass({
    load() {
      this.tileInfo = createMarsTileInfo(esri, dataset.maxLevel);
      this.spatialReference = new esri.SpatialReference({ wkid: MARS_WKID });
      this.fullExtent = makeExtent(esri, bounds);
      if (fallback) this.addResolvingPromise(fallback.load());
    },

    fetchTile(level, row, col, options) {
      const key = `${level}/${row}/${col}`;
      if (!index.has(key)) {
        if (fallback) return fetchFromFallback(fallback, level, row, col, options);
        return Promise.reject(new Error(`no ${id} tile ${key}`));
      }
      const url = new URL(`${key}.${dataset.ext || "png"}?v=${encodeURIComponent(version)}`, root);
      return fetch(url, { signal: options?.signal })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
          return r.blob();
        })
        .then(decodeTerrainRgb)
        .then((t) => ({ ...t, noDataValue: ELEV_NODATA, maxZError: 0.05 }));
    },
  });

  const layer = new Layer({ id, title, copyright });
  layer.marswalk = { bounds, footprint: dataset.footprint, dataset };
  return layer;
}

let transparentTile = null;
function getTransparentTile() {
  if (!transparentTile) {
    transparentTile = document.createElement("canvas");
    transparentTile.width = transparentTile.height = 512;
  }
  return transparentTile;
}

/** Imagery layer backed by local 512-px tiles (CTX orthomosaic). */
export function createLocalImageryLayer(esri, { id, title, dataset, baseUrl, copyright, opacity = 1, version = "" }) {
  const index = buildIndex(dataset);
  const root = new URL(dataset.path.replace(/\/?$/, "/"), baseUrl);

  const Layer = esri.BaseTileLayer.createSubclass({
    load() {
      this.tileInfo = createMarsTileInfo(esri, dataset.maxLevel);
      this.spatialReference = new esri.SpatialReference({ wkid: MARS_WKID });
      this.fullExtent = makeExtent(esri, dataset.bounds);
    },

    fetchTile(level, row, col, options) {
      const fmt = index.get(`${level}/${row}/${col}`);
      if (!fmt) return Promise.resolve(getTransparentTile());
      const url = new URL(`${level}/${row}/${col}.${fmt}?v=${encodeURIComponent(version)}`, root).href;
      return esri.esriRequest(url, { responseType: "image", signal: options?.signal })
        .then((r) => r.data)
        .catch((err) => {
          if (err?.name === "AbortError") throw err;
          return getTransparentTile();
        });
    },
  });

  const layer = new Layer({ id, title, copyright, opacity });
  layer.marswalk = { bounds: dataset.bounds, footprint: dataset.footprint, dataset };
  return layer;
}

/**
 * Wraps a remote tiled service and only shows it inside a footprint polygon
 * (pixels outside are cleared). Used for HiRISE imagery so it never appears
 * outside the verified HiRISE DTM coverage.
 */
export function createClippedTileLayer(esri, { id, title, url, bounds, footprint, minLevel = 0, copyright }) {
  const rings = footprintRings(footprint);
  const Layer = esri.BaseTileLayer.createSubclass({
    load() {
      this.inner = new esri.TileLayer({ url });
      this.addResolvingPromise(
        this.inner.load().then(() => {
          this.tileInfo = this.inner.tileInfo;
          this.spatialReference = this.inner.spatialReference;
          this.fullExtent = makeExtent(esri, bounds);
        }),
      );
    },

    fetchTile(level, row, col, options) {
      const tb = tileBounds(level, row, col);
      if (level < minLevel || !intersects(tb, bounds)) return Promise.resolve(getTransparentTile());
      return this.inner.fetchTile(level, row, col, { signal: options?.signal })
        .then((img) => {
          const size = this.tileInfo.size[0];
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = size;
          const ctx = canvas.getContext("2d");
          // clip to the footprint polygon(s), or the bounding box if none
          const sx = (lon) => ((lon - tb[0]) / (tb[2] - tb[0])) * size;
          const sy = (lat) => ((tb[3] - lat) / (tb[3] - tb[1])) * size;
          ctx.beginPath();
          if (rings.length) {
            for (const ring of rings) {
              ring.forEach(([lon, lat], i) => (i ? ctx.lineTo(sx(lon), sy(lat)) : ctx.moveTo(sx(lon), sy(lat))));
              ctx.closePath();
            }
          } else {
            ctx.rect(sx(bounds[0]), sy(bounds[3]), sx(bounds[2]) - sx(bounds[0]), sy(bounds[1]) - sy(bounds[3]));
          }
          ctx.clip();
          ctx.drawImage(img, 0, 0, size, size);
          return canvas;
        })
        .catch((err) => {
          if (err?.name === "AbortError") throw err;
          return getTransparentTile();
        });
    },
  });

  const layer = new Layer({ id, title, copyright });
  layer.marswalk = { bounds, footprint };
  return layer;
}

/**
 * Wraps a remote tiled service whose cache stops at `maxCachedLevel` although
 * its tiling scheme advertises deeper levels (true for MDIM and MColorDEM, which
 * are cached to level 7 ≈ 163 m/px). Deeper tiles are cut from the cached
 * ancestor tile instead of being requested, which avoids hundreds of failing
 * requests while zooming into Jezero.
 */
export function createCappedTileLayer(esri, { id, title, url, maxCachedLevel, copyright, visible = true }) {
  const cache = new Map(); // "L/r/c" -> Promise<image>
  const MAX_CACHE = 96;

  function ancestor(inner, level, row, col, signal) {
    const key = `${level}/${row}/${col}`;
    let p = cache.get(key);
    if (!p) {
      p = inner.fetchTile(level, row, col, {}).catch((err) => {
        cache.delete(key);
        throw err;
      });
      cache.set(key, p);
      if (cache.size > MAX_CACHE) cache.delete(cache.keys().next().value);
    }
    if (signal?.aborted) return Promise.reject(Object.assign(new Error("Aborted"), { name: "AbortError" }));
    return p;
  }

  const Layer = esri.BaseTileLayer.createSubclass({
    load() {
      this.inner = new esri.TileLayer({ url });
      this.addResolvingPromise(
        this.inner.load().then(() => {
          this.tileInfo = this.inner.tileInfo;
          this.spatialReference = this.inner.spatialReference;
          this.fullExtent = this.inner.fullExtent;
        }),
      );
    },

    fetchTile(level, row, col, options) {
      if (level <= maxCachedLevel) return this.inner.fetchTile(level, row, col, { signal: options?.signal });
      const d = level - maxCachedLevel;
      const n = 2 ** d;
      return ancestor(this.inner, maxCachedLevel, Math.floor(row / n), Math.floor(col / n), options?.signal).then((img) => {
        const size = this.tileInfo.size[0];
        const part = size / n;
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = size;
        const ctx = canvas.getContext("2d");
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(img, (col % n) * part, (row % n) * part, part, part, 0, 0, size, size);
        return canvas;
      });
    },
  });

  return new Layer({ id, title, copyright, visible });
}

/** True if lon/lat lies inside a [w, s, e, n] box. */
export function inBounds(bounds, lon, lat) {
  return !!bounds && lon >= bounds[0] && lon <= bounds[2] && lat >= bounds[1] && lat <= bounds[3];
}

/** Outer rings ([[lon, lat], …]) of a GeoJSON Polygon / MultiPolygon. */
export function footprintRings(geom) {
  if (!geom) return [];
  if (geom.type === "Polygon") return [geom.coordinates[0]];
  if (geom.type === "MultiPolygon") return geom.coordinates.map((p) => p[0]);
  return [];
}

function inRing(ring, x, y) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/**
 * True if lon/lat is covered by a dataset: inside its verified footprint polygon
 * when one exists, otherwise inside its bounding box.
 * @param {{bounds?: number[], footprint?: object}} coverage
 */
export function covers(coverage, lon, lat) {
  if (!coverage) return false;
  if (!inBounds(coverage.bounds, lon, lat)) return false;
  const rings = footprintRings(coverage.footprint);
  return rings.length ? rings.some((r) => inRing(r, lon, lat)) : true;
}
