"""
Marswalk 3D - terrain & imagery tile preparation
=================================================

Converts the USGS / JPL Mars 2020 GeoTIFFs for Jezero Crater into small static
tiles that the browser streams on demand. The original GeoTIFFs are opened
read-only and are never modified; intermediate rasters go to <out>/_work.

Pipeline
--------
1. Inspect every source (size, resolution, CRS, bounds) and verify the GDAL
   bounds against the analytic equirectangular formula on the Mars sphere.
2. Download the global MOLA/HRSC 200 m DEM (Esri MDEM200M) for the region.
3. Edge-feather the CTX DEM into the global DEM over its outer --ctx-feather-m
   metres, and the HiRISE DTM into the (feathered) CTX DEM over its outer
   --hirise-feather-m metres. Interior values are unchanged; the feathering only
   removes the vertical steps (tens of metres) where datasets meet, which would
   otherwise render as walls. The maximum adjustment is recorded in the manifest.
4. Cut tiles in the Esri Mars tiling scheme (see below).
5. Extract the true valid-data footprints (polygons) of every dataset.

Tiling scheme
-------------
Identical to the global Mars services on astro.arcgis.com (MDIM, MDEM200M, HiRISE):

    spatial reference : GCS_Mars_2000_Sphere (WKID 104971), R = 3 396 190 m
    origin            : (-180, 90)
    tile size         : 512 x 512 px
    level L tile span : 180 / 2**L degrees  (L0 res = 0.3515625 deg/px)

Outputs (relative to --out)
---------------------------
terrain/<dataset>/<L>/<row>/<col>.png   257 x 257 elevation grid, corner-inclusive
                                        (pixel centres lie on the tile corners/edges),
                                        "Terrain-RGB" encoded:
                                            h = (R*65536 + G*256 + B) * 0.1 - 10000  [m]
                                        alpha = 0 -> no data. Metres relative to the Mars
                                        areoid as in the source; NO vertical exaggeration.
imagery/<dataset>/<L>/<row>/<col>.jpg|png
                                        512 x 512 grayscale; PNG (+alpha) where the tile
                                        touches the edge of the data, JPEG otherwise.
manifest.json                           extents, footprints, levels, tile index, stats,
                                        provenance - read by js/terrain-loader.js.
footprints.geojson                      the same footprints for GIS use (QGIS etc.).

Usage (from this folder)
------------------------
    python prepare_terrain.py ^
        --ctx-dem    ../../data/raw/ctx/M20_JezeroCrater_CTXDEM_20m.tif ^
        --ctx-ortho  ../../data/raw/ctx/M20_JezeroCrater_CTXortho_mosaic_5m.tif ^
        --hirise-dtm ../../data/raw/hirise/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif ^
        --out ../../data/processed

Requires: rasterio (bundles GDAL), numpy, scipy, shapely, pillow.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field

import numpy as np
import rasterio
from PIL import Image
from rasterio import features
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.transform import Affine
from rasterio.warp import reproject, transform_bounds, transform_geom
from rasterio.windows import Window
from scipy import ndimage
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union

MARS_RADIUS = 3396190.0
M_PER_DEG = MARS_RADIUS * math.pi / 180.0
L0_RES = 0.3515625  # deg / px for 512-px tiles at level 0
TILE_PX = 512
ELEV_PX = 257  # corner-inclusive elevation grid per tile
MDEM_URL = "https://astro.arcgis.com/arcgis/rest/services/OnMars/MDEM200M/ImageServer"

# Geographic CRS identical to the Esri services (WKID 104971).
MARS_GCS_WKT = (
    'GEOGCS["GCS_Mars_2000_Sphere",DATUM["D_Mars_2000_Sphere",'
    'SPHEROID["Mars_2000_Sphere_IAU_IAG",3396190.0,0.0]],'
    'PRIMEM["Reference_Meridian",0.0],UNIT["Degree",0.0174532925199433]]'
)


# ------------------------------------------------------------------ helpers

def tile_span(level: int) -> float:
    return 180.0 / (2 ** level)


def tile_range(bounds, level):
    """Rows/cols of all tiles at `level` that intersect lon/lat `bounds`."""
    w, s, e, n = bounds
    span = tile_span(level)
    c0 = int(math.floor((w + 180.0) / span))
    c1 = int(math.floor((e + 180.0) / span - 1e-12))
    r0 = int(math.floor((90.0 - n) / span))
    r1 = int(math.floor((90.0 - s) / span - 1e-12))
    return r0, r1, c0, c1


def tile_bounds(level, row, col):
    span = tile_span(level)
    west = -180.0 + col * span
    north = 90.0 - row * span
    return west, north - span, west + span, north


def geographic_crs_of(src_crs: CRS) -> CRS:
    """Geographic CRS on the same body as the source (keeps PROJ from attempting
    a datum shift between differently-named but identical Mars spheres)."""
    wkt = src_crs.to_wkt()
    start = wkt.find("GEOGCS[")
    if start < 0:
        return CRS.from_wkt(MARS_GCS_WKT)
    depth = 0
    for i in range(start, len(wkt)):
        if wkt[i] == "[":
            depth += 1
        elif wkt[i] == "]":
            depth -= 1
            if depth == 0:
                return CRS.from_wkt(wkt[start: i + 1])
    return CRS.from_wkt(MARS_GCS_WKT)


def ensure_dir(p):
    os.makedirs(p, exist_ok=True)


def valid_elev(a, nodata=None):
    v = np.isfinite(a) & (a > -20000) & (a < 30000)
    if nodata is not None and np.isfinite(nodata):
        v &= a != nodata
    return v


@dataclass
class SourceInfo:
    path: str
    crs_wkt: str
    width: int
    height: int
    res_m: tuple
    bounds_native: tuple
    bounds_lonlat: tuple
    nodata: float | None
    lat_ts: float | None
    geo_crs: CRS = field(repr=False)
    crs: CRS = field(repr=False)


def inspect(path: str) -> SourceInfo:
    with rasterio.open(path) as src:
        geo = geographic_crs_of(src.crs)
        lonlat = transform_bounds(src.crs, geo, *src.bounds, densify_pts=21)
        try:
            params = src.crs.to_dict()
        except Exception:
            params = {}
        lat_ts = params.get("lat_ts")
        if lat_ts is not None:
            # Independent analytic check: lon = x / (R cos(lat_ts) pi/180), lat = y / (R pi/180)
            c = math.cos(math.radians(lat_ts))
            analytic = (src.bounds.left / (M_PER_DEG * c), src.bounds.bottom / M_PER_DEG,
                        src.bounds.right / (M_PER_DEG * c), src.bounds.top / M_PER_DEG)
            err = max(abs(a - b) for a, b in zip(analytic, lonlat))
            if err > 1e-6:
                print(f"  WARNING: GDAL vs analytic bounds differ by {err:.2e} deg", file=sys.stderr)
        info = SourceInfo(
            path=os.path.abspath(path), crs_wkt=src.crs.to_wkt(), width=src.width, height=src.height,
            res_m=tuple(float(r) for r in src.res), bounds_native=tuple(float(b) for b in src.bounds),
            bounds_lonlat=tuple(float(b) for b in lonlat), nodata=src.nodata, lat_ts=lat_ts,
            geo_crs=geo, crs=src.crs,
        )
    b = info.bounds_lonlat
    print(f"  {os.path.basename(path)}")
    print(f"    {info.width} x {info.height} px, res {info.res_m[0]:.3f} m, nodata={info.nodata}, lat_ts={lat_ts}")
    print(f"    lon {b[0]:.5f} .. {b[2]:.5f} E   lat {b[1]:.5f} .. {b[3]:.5f} N")
    return info


# --------------------------------------------------------------- global DEM

def fetch_global_dem(bounds, crs: CRS, out_path, level=7, pad=0.25):
    """Mosaic the Esri MDEM200M LERC tiles (finest level 7, ~163 m/px) covering
    `bounds` (+pad) into a GeoTIFF. The service has exportImage disabled, so the
    public tile cache is read directly."""
    if os.path.exists(out_path):
        print(f"  using cached {os.path.basename(out_path)}")
        return out_path
    import lerc  # Esri's LERC codec (pip install lerc)

    padded = (bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad)
    r0, r1, c0, c1 = tile_range(padded, level)
    size = TILE_PX
    mosaic = np.full(((r1 - r0 + 1) * size, (c1 - c0 + 1) * size), np.nan, dtype=np.float32)
    print(f"  downloading MDEM200M L{level} tiles rows {r0}-{r1}, cols {c0}-{c1}")
    for row in range(r0, r1 + 1):
        for col in range(c0, c1 + 1):
            with urllib.request.urlopen(f"{MDEM_URL}/tile/{level}/{row}/{col}", timeout=120) as r:
                blob = r.read()
            res = lerc.decode(blob)
            if res[0] != 0:
                raise RuntimeError(f"LERC decode failed for tile {level}/{row}/{col}")
            data, valid = np.asarray(res[1], dtype=np.float32), np.asarray(res[2])
            if valid is not None and valid.shape == data.shape:
                data = np.where(valid > 0, data, np.nan)
            # Esri elevation tiles are (size+1)^2 corner-inclusive grids: sample (i, j)
            # sits exactly on the tile corner/edge. Drop the shared last row/col.
            if data.shape != (size + 1, size + 1):
                raise RuntimeError(f"unexpected MDEM tile shape {data.shape}")
            mosaic[(row - r0) * size:(row - r0 + 1) * size, (col - c0) * size:(col - c0 + 1) * size] = data[:size, :size]
    mosaic[~valid_elev(mosaic)] = np.nan
    px = tile_span(level) / size
    west, _, _, north = tile_bounds(level, r0, c0)
    ensure_dir(os.path.dirname(out_path))
    # pixel centres on grid nodes => origin shifted by half a pixel
    with rasterio.open(out_path, "w", driver="GTiff", width=mosaic.shape[1], height=mosaic.shape[0], count=1,
                       dtype="float32", crs=crs, transform=Affine(px, 0, west - px / 2, 0, -px, north + px / 2),
                       nodata=np.nan) as dst:
        dst.write(mosaic, 1)
    print(f"    MDEM range {np.nanmin(mosaic):.0f} .. {np.nanmax(mosaic):.0f} m")
    return out_path


# --------------------------------------------------------------- feathering

def feather_dem(src_path, base_path, out_path, feather_m, decimate=1, coarse_m=None, block_rows=512):
    """Write `src` (optionally decimated by averaging) with its outer `feather_m`
    metres blended smoothly into `base`. Works block-wise to keep memory low.
    Re-uses a previous result if it is newer than both inputs and used the same settings."""
    stats_path = out_path + ".json"
    settings = {"src": os.path.basename(src_path), "base": os.path.basename(base_path),
                "feather_m": feather_m, "decimate": decimate}
    if os.path.exists(out_path) and os.path.exists(stats_path) and \
            os.path.getmtime(out_path) > max(os.path.getmtime(src_path), os.path.getmtime(base_path)):
        with open(stats_path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("settings") == settings:
            print(f"    re-using {os.path.basename(out_path)}")
            return cached["stats"]
    stats = _feather_dem(src_path, base_path, out_path, feather_m, decimate, coarse_m, block_rows)
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump({"settings": settings, "stats": stats}, f)
    return stats


def _feather_dem(src_path, base_path, out_path, feather_m, decimate, coarse_m, block_rows):
    with rasterio.open(src_path) as src, rasterio.open(base_path) as base:
        W, H = src.width // decimate, src.height // decimate
        t = src.transform * Affine.scale(src.width / W, src.height / H)
        res_m = abs(t.a)
        coarse_m = coarse_m or max(res_m, feather_m / 25.0)
        k = max(1, int(round(coarse_m / res_m)))
        cw, ch = math.ceil(W / k), math.ceil(H / k)
        cell = res_m * k

        coarse = src.read(1, out_shape=(ch, cw), resampling=Resampling.nearest).astype(np.float32)
        cvalid = valid_elev(coarse, src.nodata)
        dist = ndimage.distance_transform_edt(np.pad(cvalid, 1, constant_values=False))[1:-1, 1:-1] * cell
        wc = np.clip((dist - cell / 2) / feather_m, 0, 1)
        wc = (wc * wc * (3 - 2 * wc)).astype(np.float32)  # smoothstep

        profile = dict(driver="GTiff", width=W, height=H, count=1, dtype="float32", crs=src.crs,
                       transform=t, nodata=np.nan, tiled=True, blockxsize=512, blockysize=512,
                       compress="deflate", predictor=3, BIGTIFF="IF_SAFER")
        max_adj, n_adj = 0.0, 0
        ensure_dir(os.path.dirname(out_path))
        with rasterio.open(out_path, "w", **profile) as dst:
            cols = np.arange(W) // k
            for r0 in range(0, H, block_rows):
                rows = min(block_rows, H - r0)
                win = Window(0, r0 * decimate, src.width, min(rows * decimate, src.height - r0 * decimate))
                a = src.read(1, window=win, out_shape=(rows, W),
                             resampling=Resampling.average if decimate > 1 else Resampling.nearest).astype(np.float32)
                v = valid_elev(a, src.nodata)
                b = np.full((rows, W), np.nan, dtype=np.float32)
                reproject(source=rasterio.band(base, 1), destination=b, src_nodata=np.nan,
                          dst_transform=t * Affine.translation(0, r0), dst_crs=src.crs, dst_nodata=np.nan,
                          resampling=Resampling.bilinear)
                wb = wc[np.ix_(np.minimum((np.arange(r0, r0 + rows)) // k, ch - 1), np.minimum(cols, cw - 1))]
                out = np.where(v, a, np.nan).astype(np.float32)
                blend = v & np.isfinite(b) & (wb < 1)
                out[blend] = wb[blend] * a[blend] + (1 - wb[blend]) * b[blend]
                if blend.any():
                    d = np.abs(out[blend] - a[blend])
                    max_adj = max(max_adj, float(d.max()))
                    n_adj += int(blend.sum())
                dst.write(out, 1, window=Window(0, r0, W, rows))
    print(f"    feathered {os.path.basename(out_path)}: {W}x{H} px @ {res_m:.2f} m, "
          f"feather {feather_m:.0f} m, {n_adj} px adjusted, max adjustment {max_adj:.1f} m")
    return {"featherMeters": feather_m, "gridMeters": res_m, "pixelsAdjusted": n_adj,
            "maxAdjustmentMeters": round(max_adj, 2)}


# --------------------------------------------------------------- footprints

def footprint(path, is_valid, max_px=1500, simplify_deg=0.0015, min_area_deg2=2e-5):
    """Valid-data footprint as a lon/lat (Multi)Polygon, holes removed."""
    with rasterio.open(path) as src:
        f = max(1, max(src.width, src.height) // max_px)
        W, H = src.width // f, src.height // f
        a = src.read(1, out_shape=(H, W), resampling=Resampling.nearest)
        t = src.transform * Affine.scale(src.width / W, src.height / H)
        m = is_valid(a, src.nodata)
        m = ndimage.binary_opening(m, iterations=1)
        m = ndimage.binary_fill_holes(m)
        polys = []
        for geom, val in features.shapes(m.astype(np.uint8), mask=m, transform=t):
            g = transform_geom(src.crs, geographic_crs_of(src.crs), geom)
            p = shape(g)
            if p.area >= min_area_deg2:
                polys.append(Polygon(p.exterior))
    u = unary_union(polys).simplify(simplify_deg, preserve_topology=True)
    if isinstance(u, Polygon):
        u = MultiPolygon([u])
    return mapping(u)


# --------------------------------------------------------------------- DEM

def encode_terrain_rgb(h: np.ndarray) -> Image.Image:
    valid = np.isfinite(h)
    v = np.zeros(h.shape, dtype=np.int64)
    v[valid] = np.round((h[valid] + 10000.0) * 10.0).astype(np.int64)
    v = np.clip(v, 0, 2 ** 24 - 1)
    rgba = np.zeros(h.shape + (4,), dtype=np.uint8)
    rgba[..., 0] = (v >> 16) & 255
    rgba[..., 1] = (v >> 8) & 255
    rgba[..., 2] = v & 255
    rgba[..., 3] = np.where(valid, 255, 0)
    return Image.fromarray(rgba, "RGBA")


def build_dem_tiles(name, path, out_dir, min_level, max_level, fill_path=None):
    """Tiles `path`. Pixels of a tile that fall outside the dataset are filled from
    `fill_path` (the surrounding coarser DEM): the SDK's terrain mesh does not honour
    per-pixel no-data, so a partially empty tile would render as a deep trench.
    Because the dataset edge is feathered into that same surface, the fill is seamless."""
    index = {}
    stats_min, stats_max = math.inf, -math.inf
    fill = rasterio.open(fill_path) if fill_path else None
    with rasterio.open(path) as src:
        geo = geographic_crs_of(src.crs)
        bounds = transform_bounds(src.crs, geo, *src.bounds, densify_pts=21)
        for level in range(min_level, max_level + 1):
            r0, r1, c0, c1 = tile_range(bounds, level)
            px = tile_span(level) / (ELEV_PX - 1)
            fine = level == max_level
            tiles, t0 = [], time.time()
            for row in range(r0, r1 + 1):
                for col in range(c0, c1 + 1):
                    west, south, east, north = tile_bounds(level, row, col)
                    # pixel centres on the tile corners => shift origin by half a pixel
                    dst_t = Affine(px, 0, west - px / 2, 0, -px, north + px / 2)
                    dst = np.full((ELEV_PX, ELEV_PX), np.nan, dtype=np.float32)
                    reproject(source=rasterio.band(src, 1), destination=dst, src_nodata=src.nodata,
                              dst_transform=dst_t, dst_crs=geo, dst_nodata=np.nan,
                              resampling=Resampling.bilinear if fine else Resampling.average)
                    dst[~valid_elev(dst)] = np.nan
                    has_data = bool(np.isfinite(dst).any())
                    if not has_data and fill is None:
                        continue
                    if has_data:
                        stats_min = min(stats_min, float(np.nanmin(dst)))
                        stats_max = max(stats_max, float(np.nanmax(dst)))
                    # Every tile inside the dataset's bounding box is written (gaps filled from
                    # `fill`), so the browser never meets a missing tile inside fullExtent.
                    if fill is not None and not np.isfinite(dst).all():
                        f = np.full_like(dst, np.nan)
                        reproject(source=rasterio.band(fill, 1), destination=f, src_nodata=fill.nodata,
                                  dst_transform=dst_t, dst_crs=geo, dst_nodata=np.nan,
                                  resampling=Resampling.bilinear)
                        holes = ~np.isfinite(dst) & np.isfinite(f)
                        dst[holes] = f[holes]
                    if not np.isfinite(dst).any():
                        continue
                    d = os.path.join(out_dir, str(level), str(row))
                    ensure_dir(d)
                    encode_terrain_rgb(dst).save(os.path.join(d, f"{col}.png"), optimize=True)
                    tiles.append([row, col, 1 if np.isfinite(dst).all() else 0])
            index[str(level)] = tiles
            print(f"    {name} L{level}: {len(tiles)} tiles ({time.time() - t0:.1f}s)", flush=True)
    if fill is not None:
        fill.close()
    return index, (stats_min, stats_max), bounds


# ----------------------------------------------------------------- imagery

def build_image_tiles(name, info: SourceInfo, out_dir, min_level, max_level, treat_zero_as_nodata=True,
                      footprint_geom=None):
    """Imagery tiles. Value 0 marks gaps in these mosaics. Outside `footprint_geom`
    (the mosaic collar) zero pixels become transparent; inside it they are drawn as
    black (the recorded value) instead of revealing a different dataset underneath."""
    index = {}
    with rasterio.open(info.path) as src:
        src_nodata = src.nodata if src.nodata is not None else (0 if treat_zero_as_nodata else None)
        for level in range(min_level, max_level + 1):
            r0, r1, c0, c1 = tile_range(info.bounds_lonlat, level)
            px = tile_span(level) / TILE_PX
            fine = level == max_level
            tiles, t0 = [], time.time()
            for row in range(r0, r1 + 1):
                for col in range(c0, c1 + 1):
                    west, south, east, north = tile_bounds(level, row, col)
                    dst = np.zeros((TILE_PX, TILE_PX), dtype=np.float32)
                    tile_t = Affine(px, 0, west, 0, -px, north)
                    reproject(source=rasterio.band(src, 1), destination=dst, src_nodata=src_nodata,
                              dst_transform=tile_t, dst_crs=info.geo_crs,
                              dst_nodata=0, resampling=Resampling.bilinear if fine else Resampling.average)
                    valid = dst > 0
                    if footprint_geom is not None:
                        inside = features.rasterize([footprint_geom], out_shape=dst.shape, transform=tile_t,
                                                    fill=0, default_value=1, dtype="uint8").astype(bool)
                        valid |= inside
                    if not valid.any():
                        continue
                    gray = np.clip(np.round(dst), 1, 255).astype(np.uint8)
                    d = os.path.join(out_dir, str(level), str(row))
                    ensure_dir(d)
                    if valid.all():
                        Image.fromarray(gray, "L").save(os.path.join(d, f"{col}.jpg"), quality=85, optimize=True)
                        fmt = "jpg"
                    else:
                        la = np.dstack([np.where(valid, gray, 0), np.where(valid, 255, 0)]).astype(np.uint8)
                        Image.fromarray(la, "LA").save(os.path.join(d, f"{col}.png"), optimize=True)
                        fmt = "png"
                    tiles.append([row, col, fmt])
            index[str(level)] = tiles
            print(f"    {name} L{level}: {len(tiles)} tiles ({time.time() - t0:.1f}s)", flush=True)
    return index


def _save_image_tile(out_dir, level, row, col, gray, valid):
    d = os.path.join(out_dir, str(level), str(row))
    ensure_dir(d)
    if valid.all():
        Image.fromarray(gray, "L").save(os.path.join(d, f"{col}.jpg"), quality=85, optimize=True)
        return "jpg"
    la = np.dstack([np.where(valid, gray, 0), np.where(valid, 255, 0)]).astype(np.uint8)
    Image.fromarray(la, "LA").save(os.path.join(d, f"{col}.png"), optimize=True)
    return "png"


def _load_image_tile(out_dir, level, row, col, fmt):
    img = Image.open(os.path.join(out_dir, str(level), str(row), f"{col}.{fmt}"))
    if fmt == "jpg":
        g = np.asarray(img.convert("L"), dtype=np.float32)
        return g, np.ones(g.shape, dtype=bool)
    la = np.asarray(img.convert("LA"))
    return la[..., 0].astype(np.float32), la[..., 1] > 127


def build_image_pyramid(name, info: SourceInfo, out_dir, min_level, max_level, footprint_geom=None):
    """Imagery tiles for very large, strip-organised sources (the 6.9 GB HiRISE ortho).

    The finest level is cut from the source one tile-row at a time: the source rows
    under that tile-row are read into memory once and every tile in the row is warped
    from them (area-averaging when the source is finer than the tile). Coarser levels
    are built by 2x2 averaging of the four child tiles, so the source is read only once.
    Zero/no-data handling is the same as build_image_tiles."""
    index = {}
    t_all = time.time()
    with rasterio.open(info.path) as src:
        nodata = src.nodata if src.nodata is not None else 0
        bounds = info.bounds_lonlat
        level = max_level
        r0, r1, c0, c1 = tile_range(bounds, level)
        span = tile_span(level)
        px = span / TILE_PX
        src_px_deg = info.res_m[1] / M_PER_DEG
        resampling = Resampling.average if src_px_deg < 0.75 * px else Resampling.bilinear
        print(f"    {name} L{level}: source {src_px_deg * M_PER_DEG:.2f} m -> tile {px * M_PER_DEG:.2f} m "
              f"({resampling.name}), {r1 - r0 + 1} tile rows", flush=True)
        tiles = []
        for row in range(r0, r1 + 1):
            north = 90.0 - row * span
            south = north - span
            # source rows under this tile row (+ small margin for the resampling kernel)
            m = 4 * px
            nb = transform_bounds(info.geo_crs, src.crs, bounds[0] - m, south - m, bounds[2] + m, north + m)
            win = rasterio.windows.from_bounds(*nb, transform=src.transform)
            yo = max(0, int(math.floor(win.row_off)))
            y1 = min(src.height, int(math.ceil(win.row_off + win.height)))
            if y1 <= yo:
                continue
            win = Window(0, yo, src.width, y1 - yo)
            band = src.read(1, window=win)
            band_t = src.window_transform(win)
            for col in range(c0, c1 + 1):
                west, _, east, _ = tile_bounds(level, row, col)
                # slice the columns this tile needs
                tb = transform_bounds(info.geo_crs, src.crs, west - m, south - m, east + m, north + m)
                x0 = max(0, int(math.floor((tb[0] - band_t.c) / band_t.a)))
                x1 = min(band.shape[1], int(math.ceil((tb[2] - band_t.c) / band_t.a)))
                if x1 <= x0:
                    continue
                sub = band[:, x0:x1]
                if not (sub != nodata).any():
                    continue
                sub_t = band_t * Affine.translation(x0, 0)
                tile_t = Affine(px, 0, west, 0, -px, north)
                dst = np.zeros((TILE_PX, TILE_PX), dtype=np.float32)
                reproject(source=sub, src_transform=sub_t, src_crs=src.crs, src_nodata=nodata,
                          destination=dst, dst_transform=tile_t, dst_crs=info.geo_crs, dst_nodata=0,
                          resampling=resampling)
                valid = dst > 0
                if footprint_geom is not None:
                    valid |= features.rasterize([footprint_geom], out_shape=dst.shape, transform=tile_t,
                                                fill=0, default_value=1, dtype="uint8").astype(bool)
                if not valid.any():
                    continue
                gray = np.clip(np.round(dst), 1, 255).astype(np.uint8)
                tiles.append([row, col, _save_image_tile(out_dir, level, row, col, gray, valid)])
            del band
            done = row - r0 + 1
            if done % 5 == 0 or row == r1:
                el = time.time() - t_all
                print(f"      row {done}/{r1 - r0 + 1}, {len(tiles)} tiles, {el:.0f}s elapsed, "
                      f"~{el / done * (r1 - row):.0f}s left", flush=True)
        index[str(level)] = tiles

        for level in range(max_level - 1, min_level - 1, -1):
            t0 = time.time()
            child = {(t[0], t[1]): t[2] for t in index[str(level + 1)]}
            r0, r1, c0, c1 = tile_range(bounds, level)
            tiles = []
            for row in range(r0, r1 + 1):
                for col in range(c0, c1 + 1):
                    g = np.zeros((2 * TILE_PX, 2 * TILE_PX), dtype=np.float32)
                    a = np.zeros((2 * TILE_PX, 2 * TILE_PX), dtype=np.float32)
                    found = False
                    for dy in (0, 1):
                        for dx in (0, 1):
                            fmt = child.get((2 * row + dy, 2 * col + dx))
                            if not fmt:
                                continue
                            found = True
                            cg, cv = _load_image_tile(out_dir, level + 1, 2 * row + dy, 2 * col + dx, fmt)
                            sl = (slice(dy * TILE_PX, (dy + 1) * TILE_PX), slice(dx * TILE_PX, (dx + 1) * TILE_PX))
                            g[sl], a[sl] = cg, cv
                    if not found:
                        continue
                    ws = a.reshape(TILE_PX, 2, TILE_PX, 2).sum(axis=(1, 3))
                    gs = (g * a).reshape(TILE_PX, 2, TILE_PX, 2).sum(axis=(1, 3))
                    valid = ws > 0
                    gray = np.clip(np.round(np.where(valid, gs / np.maximum(ws, 1), 0)), 1, 255).astype(np.uint8)
                    tiles.append([row, col, _save_image_tile(out_dir, level, row, col, gray, valid)])
            index[str(level)] = tiles
            print(f"    {name} L{level}: {len(tiles)} tiles ({time.time() - t0:.1f}s)", flush=True)
    return index


def alignment_check(dem: SourceInfo, ortho: SourceInfo):
    d, o = dem.bounds_lonlat, ortho.bounds_lonlat
    lat = (d[1] + d[3]) / 2
    dx = [(o[i] - d[i]) * M_PER_DEG * math.cos(math.radians(lat)) for i in (0, 2)]
    dy = [(o[i] - d[i]) * M_PER_DEG for i in (1, 3)]
    same = (dem.lat_ts is not None and ortho.lat_ts is not None and abs(dem.lat_ts - ortho.lat_ts) < 1e-9)
    print(f"    ortho - DEM edge offsets: W {dx[0]:+.1f} m, E {dx[1]:+.1f} m, S {dy[0]:+.1f} m, N {dy[1]:+.1f} m;"
          f" identical projection: {same}")
    return {"edgeOffsetsMeters": {"west": dx[0], "east": dx[1], "south": dy[0], "north": dy[1]}, "sameProjection": same}


def src_meta(i: SourceInfo):
    return {"file": os.path.basename(i.path), "widthPx": i.width, "heightPx": i.height,
            "resolutionMeters": i.res_m[0], "boundsNative": i.bounds_native, "boundsLonLat": i.bounds_lonlat,
            "latTrueScale": i.lat_ts, "crsWkt": i.crs_wkt}


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ctx-dem", required=True)
    ap.add_argument("--ctx-ortho", required=True)
    ap.add_argument("--hirise-dtm")
    ap.add_argument("--hirise-ortho", help="optional controlled HiRISE orthomosaic (very large)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ctx-dem-levels", default="6-11", help="L11 = 257-px grid at ~20 m spacing")
    ap.add_argument("--ctx-ortho-levels", default="7-12", help="L12 = 512-px tiles at ~5 m")
    ap.add_argument("--hirise-dtm-levels", default="11-14", help="L14 = ~2.5 m spacing (257-px grid)")
    ap.add_argument("--hirise-ortho-levels", default="12-15", help="L15 = 512-px tiles at ~0.64 m (L16 ~0.32 m, ~4x the tiles)")
    ap.add_argument("--ctx-feather-m", type=float, default=2000)
    ap.add_argument("--hirise-feather-m", type=float, default=300)
    ap.add_argument("--hirise-decimate", type=int, default=2, help="HiRISE DTM averaging factor before tiling (2 -> 2 m)")
    ap.add_argument("--only", help="comma list: ctx-dem,ctx-ortho,hirise-dtm,hirise-ortho")
    args = ap.parse_args()

    lv = lambda s: tuple(int(x) for x in s.split("-"))
    only = set(args.only.split(",")) if args.only else None
    want = lambda k: only is None or k in only

    out = os.path.abspath(args.out)
    work = os.path.join(out, "_work")
    ensure_dir(work)
    manifest_path = os.path.join(out, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    manifest.update({
        "version": 2,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tilingScheme": {
            "wkid": 104971, "origin": [-180, 90], "tilePx": TILE_PX, "elevationPx": ELEV_PX,
            "level0Resolution": L0_RES,
            "elevationEncoding": "terrain-rgb: h = (R*65536 + G*256 + B) * 0.1 - 10000; alpha 0 = nodata",
            "verticalDatum": "Mars areoid (MOLA geoid), metres - as in source; no vertical exaggeration",
        },
    })
    datasets = manifest.setdefault("datasets", {})

    print("Inspecting sources")
    ctx_dem = inspect(args.ctx_dem)
    ctx_ortho = inspect(args.ctx_ortho)
    hir_dtm = inspect(args.hirise_dtm) if args.hirise_dtm else None
    hir_ortho = inspect(args.hirise_ortho) if args.hirise_ortho else None
    print("Alignment check (CTX)")
    align = alignment_check(ctx_dem, ctx_ortho)

    ctx_feathered = os.path.join(work, "ctx_dem_feathered.tif")
    if want("ctx-dem") or (hir_dtm and want("hirise-dtm") and not os.path.exists(ctx_feathered)):
        print("CTX DEM")
        mdem = fetch_global_dem(ctx_dem.bounds_lonlat, ctx_dem.geo_crs, os.path.join(work, "mdem200m_jezero.tif"))
        feather = feather_dem(ctx_dem.path, mdem, ctx_feathered, args.ctx_feather_m)
        lo, hi = lv(args.ctx_dem_levels)
        idx, (mn, mx), b = build_dem_tiles("ctx-dem", ctx_feathered, os.path.join(out, "terrain", "ctx"), lo, hi,
                                           fill_path=mdem)
        fp = footprint(ctx_dem.path, lambda a, nd: valid_elev(a, nd))
        datasets["ctx-dem"] = {
            "kind": "elevation", "path": "terrain/ctx", "ext": "png", "bounds": b, "footprint": fp,
            "minLevel": lo, "maxLevel": hi, "tiles": idx, "elevationRange": [mn, mx],
            "feathering": {**feather, "blendedInto": "Esri MDEM200M (MOLA/HRSC 200 m)"},
            "source": src_meta(ctx_dem), "alignment": align,
        }

    if want("ctx-ortho"):
        print("CTX ortho")
        lo, hi = lv(args.ctx_ortho_levels)
        fp = footprint(ctx_ortho.path, lambda a, nd: a > 0)
        idx = build_image_tiles("ctx-ortho", ctx_ortho, os.path.join(out, "imagery", "ctx"), lo, hi,
                                footprint_geom=fp)
        datasets["ctx-ortho"] = {
            "kind": "imagery", "path": "imagery/ctx", "bounds": ctx_ortho.bounds_lonlat, "footprint": fp,
            "minLevel": lo, "maxLevel": hi, "tiles": idx, "source": src_meta(ctx_ortho),
        }
    elif "ctx-ortho" in datasets and "footprint" not in datasets["ctx-ortho"]:
        datasets["ctx-ortho"]["footprint"] = footprint(ctx_ortho.path, lambda a, nd: a > 0)

    if hir_dtm and want("hirise-dtm"):
        print("HiRISE DTM")
        hir_feathered = os.path.join(work, "hirise_dtm_feathered.tif")
        feather = feather_dem(hir_dtm.path, ctx_feathered, hir_feathered, args.hirise_feather_m,
                              decimate=args.hirise_decimate)
        lo, hi = lv(args.hirise_dtm_levels)
        idx, (mn, mx), b = build_dem_tiles("hirise-dtm", hir_feathered, os.path.join(out, "terrain", "hirise"), lo, hi,
                                           fill_path=ctx_feathered)
        fp = footprint(hir_dtm.path, lambda a, nd: valid_elev(a, nd))
        datasets["hirise-dtm"] = {
            "kind": "elevation", "path": "terrain/hirise", "ext": "png", "bounds": b, "footprint": fp,
            "minLevel": lo, "maxLevel": hi, "tiles": idx, "elevationRange": [mn, mx],
            "averagedTo": f"{hir_dtm.res_m[0] * args.hirise_decimate:g} m before tiling",
            "feathering": {**feather, "blendedInto": "CTX DEM (feathered)"},
            "source": src_meta(hir_dtm),
        }

    if hir_ortho and want("hirise-ortho"):
        print("HiRISE ortho")
        lo, hi = lv(args.hirise_ortho_levels)
        if hir_dtm:
            d, o = hir_dtm.bounds_native, hir_ortho.bounds_native
            same = hir_dtm.crs == hir_ortho.crs or hir_dtm.crs.to_wkt() == hir_ortho.crs.to_wkt()
            print(f"    ortho vs DTM: identical CRS {same}, max edge offset "
                  f"{max(abs(a - b) for a, b in zip(d, o)):.2f} m")
        fp = footprint(hir_ortho.path, lambda a, nd: a > 0)
        idx = build_image_pyramid("hirise-ortho", hir_ortho, os.path.join(out, "imagery", "hirise"), lo, hi,
                                  footprint_geom=fp)
        datasets["hirise-ortho"] = {
            "kind": "imagery", "path": "imagery/hirise", "bounds": hir_ortho.bounds_lonlat, "footprint": fp,
            "minLevel": lo, "maxLevel": hi, "tiles": idx, "source": src_meta(hir_ortho),
            "tileResolutionMeters": round(tile_span(hi) / TILE_PX * M_PER_DEG, 3),
        }

    feats = [{"type": "Feature", "properties": {"id": k, "kind": d["kind"], "file": d["source"]["file"]},
              "geometry": d.get("footprint")} for k, d in datasets.items() if d.get("footprint")]
    with open(os.path.join(out, "footprints.geojson"), "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, separators=(",", ":"))
    print("Wrote", manifest_path)


if __name__ == "__main__":
    main()
