"""Terrain access and point analysis for the standalone assistant.

Sources (in order of preference):
  * HiRISE DTM (Mars 2020 TRN, 1 m)       - real data, if HIRISE_DTM_PATH is set / found
  * CTX DEM (Mars 2020 SI, 20 m)            - real data, if CTX_DEM_PATH is set / found
  * Synthetic surface                        - only if no DEM is available; every result is labelled SIMULATED

The USGS/JPL rasters are equirectangular projections of the Mars 2000 sphere
(R = 3 396 190 m, central meridian 0):  x = R·cos(φts)·λ,  y = R·φ.
Ground spacing in x is res_x · cos(φ) / cos(φts), so slopes use true metres.
Elevations are metres relative to the Mars areoid, exactly as published.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

MARS_R = 3_396_190.0
DEG = math.pi / 180.0


@dataclass
class TerrainWindow:
    elev: np.ndarray          # (rows, cols) metres, NaN = no data
    lats: np.ndarray          # (rows,) cell-centre latitude
    lons: np.ndarray          # (cols,) cell-centre longitude
    dx_m: np.ndarray          # (rows,) ground spacing east-west per row
    dy_m: float               # ground spacing north-south
    dataset: dict             # id, name, resolution_m (effective), native_resolution_m, data_mode, source

    def latlon(self, r: int, c: int) -> tuple[float, float]:
        return float(self.lats[r]), float(self.lons[c])

    def cell_of(self, lat: float, lon: float) -> tuple[int, int]:
        r = int(np.argmin(np.abs(self.lats - lat)))
        c = int(np.argmin(np.abs(self.lons - lon)))
        return r, c


def horn_slope(elev: np.ndarray, dx_rows: np.ndarray, dy: float) -> np.ndarray:
    """Slope in degrees with Horn's 3x3 method; NaN wherever the 3x3 window touches no-data."""
    z = np.pad(elev, 1, mode="edge")
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8.0 * dx_rows[:, None])
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8.0 * dy)
    return np.degrees(np.arctan(np.hypot(dzdx, dzdy)))


def ground_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance on the Mars sphere."""
    p1, p2 = lat1 * DEG, lat2 * DEG
    dp, dl = p2 - p1, (lon2 - lon1) * DEG
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * MARS_R * math.asin(min(1.0, math.sqrt(h)))


def offset_deg(lat: float, metres: float) -> tuple[float, float]:
    """Degrees of latitude / longitude spanned by `metres` at latitude `lat`."""
    dlat = metres / (MARS_R * DEG)
    dlon = metres / (MARS_R * DEG * max(math.cos(lat * DEG), 1e-6))
    return dlat, dlon


class EqcRasterSource:
    """A single-band equirectangular DEM GeoTIFF read with rasterio (windowed, never fully loaded)."""

    data_mode = "real"

    def __init__(self, source_id: str, name: str, path: str, citation: str):
        import rasterio  # optional dependency, only needed for real DEMs
        from rasterio.enums import Resampling
        self._rasterio = rasterio
        self._resampling = Resampling
        self.id = source_id
        self.name = name
        self.path = path
        self.citation = citation
        self.ds = rasterio.open(path)
        self.lat_ts = self._standard_parallel()
        self.kx = MARS_R * math.cos(self.lat_ts * DEG) * DEG   # projected metres per degree of longitude
        self.ky = MARS_R * DEG
        b = self.ds.bounds
        self.bounds = {"west": b.left / self.kx, "east": b.right / self.kx, "south": b.bottom / self.ky, "north": b.top / self.ky}
        self.native_res_m = float(self.ds.res[1])
        self.nodata = self.ds.nodata

    def _standard_parallel(self) -> float:
        wkt = self.ds.crs.to_wkt()
        m = re.search(r'standard_parallel_1",\s*(-?[\d.]+)', wkt) or re.search(r'Standard_Parallel_1",\s*(-?[\d.]+)', wkt)
        if m:
            return float(m.group(1))
        proj = self.ds.crs.to_proj4()
        m = re.search(r"\+lat_ts=(-?[\d.]+)", proj)
        return float(m.group(1)) if m else 0.0

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "native_resolution_m": self.native_res_m, "data_mode": "real",
                "source": self.citation, "bounds": self.bounds, "standard_parallel_deg": self.lat_ts}

    def in_bounds(self, lat: float, lon: float) -> bool:
        b = self.bounds
        return b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]

    def _clean(self, a: np.ndarray) -> np.ndarray:
        a = a.astype(np.float64)
        a[~np.isfinite(a) | (a < -1e20) | (a > 1e20)] = np.nan
        if self.nodata is not None and math.isfinite(self.nodata):
            a[a == self.nodata] = np.nan
        return a

    def sample(self, lat: float, lon: float) -> float | None:
        if not self.in_bounds(lat, lon):
            return None
        row, col = self.ds.index(lon * self.kx, lat * self.ky)
        if not (0 <= row < self.ds.height and 0 <= col < self.ds.width):
            return None
        from rasterio.windows import Window
        v = self._clean(self.ds.read(1, window=Window(col, row, 1, 1)))[0, 0]
        return None if math.isnan(v) else float(v)

    def covers(self, lat: float, lon: float) -> bool:
        return self.sample(lat, lon) is not None

    def read_window(self, south: float, west: float, north: float, east: float, max_cells: int = 160_000) -> TerrainWindow:
        from rasterio.windows import Window, from_bounds
        win = from_bounds(west * self.kx, south * self.ky, east * self.kx, north * self.ky, self.ds.transform)
        win = win.round_offsets().round_lengths()
        win = win.intersection(Window(0, 0, self.ds.width, self.ds.height))
        h, w = int(win.height), int(win.width)
        if h < 3 or w < 3:
            raise ValueError("window too small or outside the dataset")
        factor = max(1.0, math.sqrt(h * w / max_cells))
        oh, ow = max(3, int(round(h / factor))), max(3, int(round(w / factor)))
        data = self.ds.read(1, window=win, out_shape=(oh, ow), resampling=self._resampling.average if factor > 1 else self._resampling.nearest)
        elev = self._clean(data)
        t = self.ds.window_transform(win)
        res_x, res_y = t.a * w / ow, -t.e * h / oh                 # projected metres per output cell
        xs = t.c + (np.arange(ow) + 0.5) * res_x
        ys = t.f - (np.arange(oh) + 0.5) * res_y
        lons, lats = xs / self.kx, ys / self.ky
        dx = res_x * np.cos(lats * DEG) / math.cos(self.lat_ts * DEG)
        return TerrainWindow(elev=elev, lats=lats, lons=lons, dx_m=dx, dy_m=float(res_y),
                             dataset={"id": self.id, "name": self.name, "resolution_m": round(float(res_y), 2),
                                      "native_resolution_m": self.native_res_m, "data_mode": "real", "source": self.citation})


class SyntheticTerrainSource:
    """Deterministic smooth surface for development without DEMs. NOT Mars data."""

    data_mode = "simulated"
    id = "synthetic"
    name = "SIMULATED terrain (no DEM configured)"
    native_res_m = 20.0
    citation = "Procedural surface generated by marswalk-ai for development. Not a measurement."

    def __init__(self, bounds: dict):
        self.bounds = bounds

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "native_resolution_m": self.native_res_m, "data_mode": "simulated",
                "source": self.citation, "bounds": self.bounds}

    def in_bounds(self, lat: float, lon: float) -> bool:
        b = self.bounds
        return b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]

    @staticmethod
    def _z(lat, lon):
        x, y = (lon - 77.40) * 59_000, (lat - 18.45) * 59_000
        return (-2520 + 35 * np.sin(x / 900) * np.cos(y / 700) + 25 * np.tanh((x + 0.6 * y) / 250)
                + 8 * np.sin(x / 140 + y / 190))

    def sample(self, lat: float, lon: float) -> float | None:
        return float(self._z(lat, lon)) if self.in_bounds(lat, lon) else None

    def covers(self, lat: float, lon: float) -> bool:
        return self.in_bounds(lat, lon)

    def read_window(self, south, west, north, east, max_cells: int = 160_000) -> TerrainWindow:
        mid = (south + north) / 2
        h_m = (north - south) * MARS_R * DEG
        w_m = (east - west) * MARS_R * DEG * math.cos(mid * DEG)
        res = max(self.native_res_m, math.sqrt(h_m * w_m / max_cells))
        rows, cols = max(3, int(h_m / res)), max(3, int(w_m / res))
        lats = north - (np.arange(rows) + 0.5) * (north - south) / rows
        lons = west + (np.arange(cols) + 0.5) * (east - west) / cols
        elev = self._z(lats[:, None], lons[None, :])
        dx = (east - west) / cols * MARS_R * DEG * np.cos(lats * DEG)
        dy = (north - south) / rows * MARS_R * DEG
        return TerrainWindow(elev=elev, lats=lats, lons=lons, dx_m=dx, dy_m=float(dy),
                             dataset={"id": self.id, "name": self.name, "resolution_m": round(float(dy), 2),
                                      "native_resolution_m": self.native_res_m, "data_mode": "simulated", "source": self.citation})


class TerrainService:
    def __init__(self, sources: list, max_slope_deg: float = 15.0, caution_slope_deg: float = 10.0):
        self.sources = sources
        self.max_slope_deg = max_slope_deg
        self.caution_slope_deg = caution_slope_deg

    @classmethod
    def from_settings(cls, settings, mission_bounds: list) -> "TerrainService":
        sources, errors = [], []
        for sid, name, path, cite in (
            ("hirise-dtm", "Mars 2020 TRN HiRISE DTM mosaic (1 m)", settings.hirise_dtm_path,
             "USGS Astrogeology, JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m"),
            ("ctx-dem", "Mars 2020 Science Investigation CTX DEM mosaic (20 m)", settings.ctx_dem_path,
             "JPL / USGS, M20_JezeroCrater_CTXDEM_20m"),
        ):
            if not path:
                continue
            try:
                sources.append(EqcRasterSource(sid, name, path, cite))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{sid}: {e}")
        if not sources and settings.allow_synthetic_terrain:
            (s, w), (n, e) = mission_bounds
            sources.append(SyntheticTerrainSource({"south": s, "west": w, "north": n, "east": e}))
        svc = cls(sources, settings.max_slope_deg, settings.caution_slope_deg)
        svc.load_errors = errors
        return svc

    load_errors: list[str] = []

    @property
    def data_mode(self) -> str:
        modes = {s.data_mode for s in self.sources}
        return "none" if not modes else modes.pop() if len(modes) == 1 else "mixed"

    def describe(self) -> list[dict]:
        return [s.info() for s in self.sources]

    def sources_covering(self, points: list[tuple[float, float]]) -> list:
        return [s for s in self.sources if all(s.covers(lat, lon) for lat, lon in points)]

    def classify(self, max_slope: float | None, avg_slope: float | None, limit: float) -> dict:
        """Project-defined terrain difficulty classes (same thresholds as the Science Engine)."""
        if max_slope is None:
            return {"terrain_difficulty": "Insufficient data", "within_limit": None}
        if max_slope > limit:
            d = "Exceeds project slope limit"
        elif max_slope > self.caution_slope_deg:
            d = "Challenging"
        elif (avg_slope or 0) > 5.0:
            d = "Moderate"
        else:
            d = "Low slope"
        return {"terrain_difficulty": d, "within_limit": max_slope <= limit,
                "rule": f"project-defined: >{limit:g}° exceeds limit, >{self.caution_slope_deg:g}° challenging, mean >5° moderate"}

    def analyze_point(self, lat: float, lon: float, radius_m: float = 150.0, max_slope_deg: float | None = None) -> dict:
        limit = max_slope_deg or self.max_slope_deg
        covering = self.sources_covering([(lat, lon)])
        if not covering:
            return {"data_available": False, "lat": lat, "lon": lon,
                    "reason": "No configured elevation dataset covers this point.",
                    "datasets_checked": [s.id for s in self.sources]}
        src = covering[0]
        dlat, dlon = offset_deg(lat, radius_m + 3 * src.native_res_m)
        win = src.read_window(lat - dlat, lon - dlon, lat + dlat, lon + dlon, max_cells=90_000)
        slope = horn_slope(win.elev, win.dx_m, win.dy_m)
        yy = (win.lats[:, None] - lat) * MARS_R * DEG
        xx = (win.lons[None, :] - lon) * MARS_R * DEG * math.cos(lat * DEG)
        inside = (xx ** 2 + yy ** 2) <= radius_m ** 2
        e_in, s_in = win.elev[inside], slope[inside]
        e_valid, s_valid = e_in[np.isfinite(e_in)], s_in[np.isfinite(s_in)]
        r, c = win.cell_of(lat, lon)
        centre = win.elev[r, c]
        res = {
            "data_available": True, "lat": lat, "lon": lon, "radius_m": radius_m,
            "dataset": win.dataset,
            "elevation_m": None if not np.isfinite(centre) else round(float(centre), 1),
            "elevation_min_m": round(float(e_valid.min()), 1) if e_valid.size else None,
            "elevation_max_m": round(float(e_valid.max()), 1) if e_valid.size else None,
            "relief_m": round(float(e_valid.max() - e_valid.min()), 1) if e_valid.size else None,
            "slope_mean_deg": round(float(s_valid.mean()), 1) if s_valid.size else None,
            "slope_p90_deg": round(float(np.percentile(s_valid, 90)), 1) if s_valid.size else None,
            "slope_max_deg": round(float(s_valid.max()), 1) if s_valid.size else None,
            "area_above_caution_pct": round(float((s_valid > self.caution_slope_deg).mean() * 100), 1) if s_valid.size else None,
            "area_above_limit_pct": round(float((s_valid > limit).mean() * 100), 1) if s_valid.size else None,
            "valid_cells_pct": round(float(np.isfinite(s_in).mean() * 100), 1) if s_in.size else 0.0,
            "thresholds": {"max_slope_deg": limit, "caution_slope_deg": self.caution_slope_deg, "source": "project-defined"},
        }
        cs = slope[r, c]
        res["slope_at_point_deg"] = None if not np.isfinite(cs) else round(float(cs), 1)
        # Classify on the 90th-percentile slope: at metre-scale resolution the single steepest cell is
        # usually an isolated boulder or scarp edge. The maximum is still reported.
        res["classification"] = self.classify(res["slope_p90_deg"], res["slope_mean_deg"], limit)
        res["classification"]["basis"] = f"90th-percentile slope within {radius_m:.0f} m"
        return res
