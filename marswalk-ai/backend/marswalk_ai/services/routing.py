"""Least-cost route planning and route evaluation on a slope grid.

This is the STANDALONE route service used until the assistant is connected to the
real Marswalk route engine. It follows the project plan:

    cell cost = 1 + (slope_deg / MAX_SLOPE)^2,  cells above MAX_SLOPE or without data are blocked

and finds the least-cost 8-connected path with Dijkstra (scipy.sparse.csgraph).
All numbers come from the elevation grid; nothing is estimated by the LLM.

Walking time uses the project's modified Tobler function (same constants and the same
0.2-5 km/h clamp as the Scientific Intelligence Engine):
    v = 6 · exp(-3.5 · |tan θ + 0.05|) · k_suit   [km/h]
This is a terrestrial hiking model adapted for a simulation, not a validated
Martian EVA mobility model.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from .terrain import TerrainService, TerrainWindow, ground_distance_m, horn_slope, offset_deg

OBJECTIVES = {
    "balanced": ("Balanced: lowest terrain cost", lambda s, m: 1.0 + (s / m) ** 2),
    "min_slope": ("Gentlest slopes", lambda s, m: 1.0 + 6.0 * (s / m) ** 2),
    "shortest": ("Most direct", lambda s, m: np.ones_like(s)),
}
MAX_CELLS = 160_000


class RouteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def tobler_kmh(grade_deg: np.ndarray, k_suit: float) -> np.ndarray:
    v = 6.0 * np.exp(-3.5 * np.abs(np.tan(np.radians(grade_deg)) + 0.05)) * k_suit
    return np.clip(v, 0.2, 5.0)


def _graph(cost: np.ndarray, dx: np.ndarray, dy: float) -> csr_matrix:
    h, w = cost.shape
    idx = np.arange(h * w).reshape(h, w)
    passable = np.isfinite(cost)
    src_all, dst_all, wt_all = [], [], []
    for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
        r0, r1 = max(0, -dr), h - max(0, dr)
        c0, c1 = max(0, -dc), w - max(0, dc)
        s = idx[r0:r1, c0:c1]
        d = idx[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        ok = passable[r0:r1, c0:c1] & passable[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        rows_dx = dx[r0:r1][:, None] * np.ones((1, c1 - c0))
        dist = np.sqrt((dr * dy) ** 2 + (dc * rows_dx) ** 2)
        wgt = dist * (cost[r0:r1, c0:c1] + cost[r0 + dr:r1 + dr, c0 + dc:c1 + dc]) / 2.0
        src_all.append(s[ok]); dst_all.append(d[ok]); wt_all.append(wgt[ok])
    return csr_matrix((np.concatenate(wt_all), (np.concatenate(src_all), np.concatenate(dst_all))), shape=(h * w, h * w))


def _shortest_path(cost: np.ndarray, win: TerrainWindow, start_rc, end_rc) -> list[tuple[int, int]] | None:
    h, w = cost.shape
    g = _graph(cost, win.dx_m, win.dy_m)
    s, e = start_rc[0] * w + start_rc[1], end_rc[0] * w + end_rc[1]
    dist, pred = dijkstra(g, directed=True, indices=s, return_predecessors=True)
    if not np.isfinite(dist[e]):
        return None
    path, node = [], e
    while node != s and node >= 0:
        path.append(divmod(int(node), w))
        node = pred[node]
    path.append(divmod(int(s), w))
    return path[::-1]


def evaluate_cells(win: TerrainWindow, slope: np.ndarray, cells: list[tuple[int, int]], max_slope: float,
                   caution: float, k_suit: float, cost_ref_slope: float | None = None) -> dict:
    """Metrics for a path given as grid cells. Every value is computed from the grid.

    weighted_cost always uses the same reference slope (the project default limit), so routes planned
    with different limits remain comparable."""
    ref = cost_ref_slope or max_slope
    rr = np.array([c[0] for c in cells]); cc = np.array([c[1] for c in cells])
    lat, lon = win.lats[rr], win.lons[cc]
    z = win.elev[rr, cc]
    s_all = slope[rr, cc]
    # The start and destination cells are fixed by the mission (e.g. a stop on a scarp), so the slope
    # constraint and the statistics are evaluated on the cells the route CHOOSES; endpoints are reported apart.
    s = s_all.copy()
    if len(s) > 2:
        s[0] = s[-1] = np.nan
    ds = np.sqrt(((np.diff(rr)) * win.dy_m) ** 2 + ((np.diff(cc)) * win.dx_m[rr[:-1]]) ** 2)
    dz = np.diff(z)
    grade = np.degrees(np.arctan2(dz, np.maximum(ds, 1e-6)))
    seg_slope = np.fmax(s[:-1], s[1:])
    dist = float(ds.sum())
    cum = np.concatenate([[0.0], np.cumsum(ds)])
    cost = 1.0 + (np.nan_to_num(seg_slope, nan=ref * 3) / ref) ** 2
    t_out = float((ds / 1000.0 / tobler_kmh(grade, k_suit)).sum() * 60)
    t_back = float((ds / 1000.0 / tobler_kmh(-grade, k_suit)).sum() * 60)
    valid_s = s[np.isfinite(s)]
    max_s = float(np.nanmax(s)) if valid_s.size else None
    imax = int(np.nanargmax(s)) if valid_s.size else 0

    # contiguous sections above the caution threshold
    sections, cur = [], None
    for i in range(len(cells)):
        steep = np.isfinite(s[i]) and s[i] > caution
        if steep and cur is None:
            cur = [i, i]
        elif steep:
            cur[1] = i
        if (not steep or i == len(cells) - 1) and cur is not None:
            a, b = cur
            k = a + int(np.nanargmax(s[a:b + 1]))
            sections.append({"from_m": round(float(cum[a]), 0), "to_m": round(float(cum[b]), 0),
                             "length_m": round(float(cum[b] - cum[a]), 0), "max_slope_deg": round(float(s[k]), 1),
                             "lat": round(float(lat[k]), 6), "lon": round(float(lon[k]), 6),
                             "over_limit": bool(s[k] > max_slope)})
            cur = None
    sections.sort(key=lambda x: -x["max_slope_deg"])

    def cls(v):
        return "nodata" if not np.isfinite(v) else "over" if v > max_slope else "caution" if v > caution else "ok"

    return {
        "geometry": [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lat, lon)],
        "slope_classes": [cls(v) for v in seg_slope],
        # profile: at most ~400 samples, always including the last point; lat/lon let clients draw it directly
        "profile": [{"lat": round(float(lat[i]), 6), "lon": round(float(lon[i]), 6), "d_m": round(float(cum[i]), 1),
                     "elev_m": None if not np.isfinite(z[i]) else round(float(z[i]), 1),
                     "slope_deg": None if not np.isfinite(s_all[i]) else round(float(s_all[i]), 1)}
                    for i in sorted(set(range(0, len(cum), max(1, len(cum) // 400))) | {len(cum) - 1})],
        "metrics": {
            "distance_m": round(dist, 0),
            "straight_line_m": round(ground_distance_m(lat[0], lon[0], lat[-1], lon[-1]), 0),
            "elevation_start_m": None if not np.isfinite(z[0]) else round(float(z[0]), 1),
            "elevation_end_m": None if not np.isfinite(z[-1]) else round(float(z[-1]), 1),
            "elevation_gain_m": round(float(dz[dz > 0].sum()), 1),
            "elevation_loss_m": round(float(-dz[dz < 0].sum()), 1),
            "max_slope_deg": None if max_s is None else round(max_s, 1),
            "avg_slope_deg": round(float(np.nansum(seg_slope * ds) / max(dist, 1e-6)), 1),
            "max_grade_deg": round(float(np.nanmax(np.abs(grade))), 1) if grade.size else 0.0,
            "length_above_caution_m": round(float(ds[np.nan_to_num(seg_slope) > caution].sum()), 0),
            "weighted_cost": round(float((cost * ds).sum() / max(dist, 1e-6)), 3),
            "walk_time_out_min": round(t_out, 1),
            "walk_time_back_min": round(t_back, 1),
            "within_constraints": bool(max_s is not None and max_s <= max_slope),
            "start_cell_slope_deg": None if not np.isfinite(s_all[0]) else round(float(s_all[0]), 1),
            "end_cell_slope_deg": None if not np.isfinite(s_all[-1]) else round(float(s_all[-1]), 1),
            "data_coverage_pct": round(float(np.isfinite(z).mean() * 100), 1),
        },
        "steepest_point": {"lat": round(float(lat[imax]), 6), "lon": round(float(lon[imax]), 6),
                           "slope_deg": None if max_s is None else round(max_s, 1), "at_m": round(float(cum[imax]), 0)},
        "steep_sections": sections[:8],
    }


class RoutePlanner:
    def __init__(self, terrain: TerrainService, k_suit: float = 0.6, cost_ref_slope: float = 15.0):
        self.terrain = terrain
        self.k_suit = k_suit
        self.cost_ref_slope = cost_ref_slope

    def _window(self, src, start, end) -> TerrainWindow:
        d = ground_distance_m(start[0], start[1], end[0], end[1])
        margin = max(350.0, 0.45 * d)
        mid = (start[0] + end[0]) / 2
        dlat, dlon = offset_deg(mid, margin)
        return src.read_window(min(start[0], end[0]) - dlat, min(start[1], end[1]) - dlon,
                               max(start[0], end[0]) + dlat, max(start[1], end[1]) + dlon, max_cells=MAX_CELLS)

    def plan(self, start: tuple[float, float], end: tuple[float, float], max_slope: float,
             objectives: list[str], caution: float, avoid: list[list] | None = None) -> tuple[list[dict], dict]:
        """Returns (routes, context). Raises RouteError with an honest reason."""
        if ground_distance_m(*start, *end) < 5:
            raise RouteError("same_location", "Start and destination are the same place.")
        sources = self.terrain.sources_covering([start, end])
        if not sources:
            raise RouteError("out_of_coverage", "No configured elevation dataset covers both the start and the destination.")
        failures = []
        for src in sources:                                   # finest dataset first; fall back if no path
            try:
                win = self._window(src, start, end)
            except ValueError as e:
                failures.append(f"{src.id}: {e}"); continue
            slope = horn_slope(win.elev, win.dx_m, win.dy_m)
            sr, er = win.cell_of(*start), win.cell_of(*end)
            routes, penalty = [], np.ones_like(slope)
            for geom in avoid or []:                          # steer away from routes the user wants an alternative to
                cells = [win.cell_of(p[0], p[1]) for p in geom]
                penalty = _corridor_penalty(penalty, cells, 4, 3.0)
            for obj in objectives:
                label, fn = OBJECTIVES[obj]
                base = np.where(np.isfinite(slope) & (slope <= max_slope), fn(np.nan_to_num(slope), max_slope), np.inf)
                for rc in (sr, er):                           # endpoints are always enterable if they have data
                    if np.isfinite(slope[rc]):
                        base[rc] = fn(np.array(min(slope[rc], max_slope)), max_slope)
                path = _shortest_path(base * penalty, win, sr, er)
                if path is None:
                    continue
                if routes and _overlap(path, routes) > 0.85:    # near-duplicate: push the next search away from it
                    path = _shortest_path(base * _corridor_penalty(penalty, path, 4, 2.5), win, sr, er) or path
                penalty = _corridor_penalty(penalty, path, 3, 1.6)
                ev = evaluate_cells(win, slope, path, max_slope, caution, self.k_suit, self.cost_ref_slope)
                ev.update({"objective": obj, "label": label, "_cells": path})
                routes.append(ev)
            if routes:
                for r in routes:
                    r.pop("_cells", None)
                ctx = {"dataset": win.dataset, "grid_shape": list(slope.shape),
                       "start_on_steep_cell": bool(np.isfinite(slope[sr]) and slope[sr] > max_slope),
                       "end_on_steep_cell": bool(np.isfinite(slope[er]) and slope[er] > max_slope)}
                return routes, ctx
            failures.append(f"{src.id}: no path keeps every cell at or below {max_slope:g}°")
        raise RouteError("no_path_within_constraints",
                         f"No route was found that keeps slopes at or below {max_slope:g}° ({'; '.join(failures)}).")

    def evaluate_polyline(self, points: list[tuple[float, float]], max_slope: float, caution: float) -> dict:
        """Evaluate an externally supplied route (lat, lon) by sampling it on the finest covering DEM."""
        sources = self.terrain.sources_covering(points[:1] + points[-1:])
        if not sources:
            raise RouteError("out_of_coverage", "No configured elevation dataset covers this route.")
        src = sources[0]
        lats, lons = [p[0] for p in points], [p[1] for p in points]
        dlat, dlon = offset_deg(sum(lats) / len(lats), 200)
        win = src.read_window(min(lats) - dlat, min(lons) - dlon, max(lats) + dlat, max(lons) + dlon, max_cells=MAX_CELLS)
        slope = horn_slope(win.elev, win.dx_m, win.dy_m)
        cells: list[tuple[int, int]] = []
        for (a_lat, a_lon), (b_lat, b_lon) in zip(points[:-1], points[1:]):
            n = max(2, int(ground_distance_m(a_lat, a_lon, b_lat, b_lon) / max(win.dy_m, 1)) + 1)
            for t in np.linspace(0, 1, n):
                rc = win.cell_of(a_lat + (b_lat - a_lat) * t, a_lon + (b_lon - a_lon) * t)
                if not cells or cells[-1] != rc:
                    cells.append(rc)
        ev = evaluate_cells(win, slope, cells, max_slope, caution, self.k_suit, self.cost_ref_slope)
        ev["dataset"] = win.dataset
        return ev


def _overlap(path, routes) -> float:
    cells = set(path)
    return max(len(cells & set(map(tuple, r["_cells"]))) / max(len(cells), 1) for r in routes)


def _corridor_penalty(penalty: np.ndarray, path, radius: int, factor: float) -> np.ndarray:
    out = penalty.copy()
    h, w = out.shape
    mask = np.zeros_like(out, dtype=bool)
    for r, c in path:
        mask[max(0, r - radius):min(h, r + radius + 1), max(0, c - radius):min(w, c + radius + 1)] = True
    out[mask] *= factor
    return out


def recommend(routes: list[dict]) -> tuple[int | None, list[str]]:
    """Deterministic recommendation: lowest terrain cost among routes that meet the slope constraint."""
    ok = [i for i, r in enumerate(routes) if r["metrics"]["within_constraints"]]
    if not ok:
        return None, ["No candidate keeps every traversed cell within the selected slope limit."]
    best = min(ok, key=lambda i: (routes[i]["metrics"]["weighted_cost"], routes[i]["metrics"]["walk_time_out_min"]))
    b = routes[best]["metrics"]
    why = [f"lowest terrain cost among routes that meet the slope limit (weighted cost {b['weighted_cost']})",
           f"maximum slope {b['max_slope_deg']}° and average {b['avg_slope_deg']}°",
           f"{b['distance_m']:.0f} m, about {b['walk_time_out_min']:.0f} min walking out"]
    others = [routes[i] for i in ok if i != best]
    if others:
        shortest = min(others, key=lambda r: r["metrics"]["distance_m"])
        if shortest["metrics"]["distance_m"] < b["distance_m"]:
            why.append(f"'{shortest['label']}' is shorter ({shortest['metrics']['distance_m']:.0f} m) "
                       f"but has a higher terrain cost ({shortest['metrics']['weighted_cost']})")
    return best, why
