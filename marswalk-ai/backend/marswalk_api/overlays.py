"""DEM-derived raster overlays for the 2D map (slope classes and hillshade).

Rendered once from the finest configured DEM (HiRISE DTM when available) as PNG
images in the lon/lat grid Leaflet uses (EPSG4326 = planetocentric, east-positive),
then cached in marswalk-ai/var/overlays/ until the DEM or the thresholds change.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

from marswalk_ai.services.terrain import horn_slope

SIZE = 2048     # longest image side in pixels (~10 m/px over the HiRISE walk zone)


def _edges(win) -> list:
    dlat = abs(float(win.lats[0] - win.lats[1])) if len(win.lats) > 1 else 0.0
    dlon = abs(float(win.lons[1] - win.lons[0])) if len(win.lons) > 1 else 0.0
    south, north = float(win.lats[-1]) - dlat / 2, float(win.lats[0]) + dlat / 2
    west, east = float(win.lons[0]) - dlon / 2, float(win.lons[-1]) + dlon / 2
    return [[round(south, 7), round(west, 7)], [round(north, 7), round(east, 7)]]


def _hillshade(elev: np.ndarray, dx_rows: np.ndarray, dy: float, azimuth=315.0, altitude=45.0) -> np.ndarray:
    z = np.pad(elev, 1, mode="edge")
    dzdx = ((z[:-2, 2:] + 2 * z[1:-1, 2:] + z[2:, 2:]) - (z[:-2, :-2] + 2 * z[1:-1, :-2] + z[2:, :-2])) / (8 * dx_rows[:, None])
    dzdy = ((z[2:, :-2] + 2 * z[2:, 1:-1] + z[2:, 2:]) - (z[:-2, :-2] + 2 * z[:-2, 1:-1] + z[:-2, 2:])) / (8 * dy)
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    zen, az = math.radians(90 - altitude), math.radians((360 - azimuth + 90) % 360)
    hs = np.cos(zen) * np.cos(slope) + np.sin(zen) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1)


def build_overlays(terrain, out_dir: Path, max_slope: float, caution: float) -> dict:
    """Returns {"bounds", "dataset", "resolution_m", "files": {id: path}} or {"error": ...}."""
    from PIL import Image

    if terrain is None or not terrain.sources:
        return {"error": "No elevation dataset is configured."}
    src = terrain.sources[0]                       # finest first (HiRISE DTM, then CTX, then synthetic)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = {"source": getattr(src, "path", src.id), "mtime": os.path.getmtime(src.path) if hasattr(src, "path") else 0,
             "size": SIZE, "max_slope": max_slope, "caution": caution, "version": 1}
    meta_file = out_dir / "overlays.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if meta.get("stamp") == stamp and all((out_dir / f).exists() for f in meta["files"].values()):
            return meta

    b = src.bounds
    win = src.read_window(b["south"], b["west"], b["north"], b["east"], max_cells=SIZE * SIZE)
    slope = horn_slope(win.elev, win.dx_m, win.dy_m)
    valid = np.isfinite(slope)

    rgba = np.zeros(slope.shape + (4,), dtype=np.uint8)            # < caution: transparent
    amber = valid & (slope > caution) & (slope <= max_slope)
    over = valid & (slope > max_slope)
    rgba[amber] = (242, 193, 78, 170)                               # same colours as the 2D route legend
    rgba[over] = (249, 115, 22, 215)
    Image.fromarray(rgba, "RGBA").save(out_dir / "slope.png", optimize=True)

    hs = _hillshade(np.nan_to_num(win.elev, nan=float(np.nanmean(win.elev))), win.dx_m, win.dy_m)
    grey = (hs * 255).astype(np.uint8)
    hrgba = np.dstack([grey, grey, grey, np.where(np.isfinite(win.elev), 255, 0).astype(np.uint8)])
    Image.fromarray(hrgba, "RGBA").save(out_dir / "hillshade.png", optimize=True)

    meta = {"stamp": stamp, "bounds": _edges(win), "dataset": win.dataset["name"], "data_mode": win.dataset["data_mode"],
            "resolution_m": win.dataset["resolution_m"], "files": {"slope": "slope.png", "hillshade": "hillshade.png"},
            "valid_pct": round(float(valid.mean() * 100), 1)}
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    return meta
