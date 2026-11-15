"""Marswalk backend: the /api/* routes the original 2D planner (js/map.js) calls, plus the Marswalk page itself.

Run from marswalk-ai/:
    .venv\\Scripts\\python -m uvicorn marswalk_api.main:app --app-dir backend --port 8085
then open http://127.0.0.1:8085/  (same origin, so map.js's  const API = ""  works unchanged).

Every number comes from the real Mars 2020 DEMs (HiRISE DTM 1 m, CTX DEM 20 m) through the
same terrain and routing code as the Marswalk AI assistant. Thresholds and the walking /
dose models are project-defined and labelled as such in the responses.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from marswalk_ai.config import AI_ROOT, VAR_DIR, Settings
from marswalk_ai.services.routing import RouteError, RoutePlanner, tobler_kmh  # noqa: F401  (tobler documented in EVA)
from marswalk_ai.services.terrain import TerrainService

from .donki import Donki
from .overlays import build_overlays

MARSWALK_ROOT = Path(os.environ.get("MARSWALK_ROOT") or AI_ROOT.parent)
JEZERO_BOUNDS = [[18.2110274, 77.1604655], [18.7211945, 77.6991776]]     # NASA.jezeroBounds in js/nasa-config.js
ALGORITHM = "Least-cost path (Dijkstra, 8-connected) on the slope grid; cell cost 1 + (slope/limit)²; cells above the limit blocked"


# ------------------------------------------------------------------ request models (contracts from js/map.js)
class Point(BaseModel):
    model_config = ConfigDict(extra="allow")
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class AnalyzeRequest(BaseModel):
    waypoints: list[Point] = Field(..., min_length=2, max_length=5000)
    route_id: str | None = None
    max_slope: float | None = Field(None, ge=5, le=25, description="optional; map.js does not send it (default 15°)")


class AutoRequest(BaseModel):
    stops: list[Point] = Field(..., min_length=2, max_length=20)
    max_slope: float = Field(15, ge=5, le=25)


class OptionsRequest(BaseModel):
    start: Point
    end: Point
    max_slope: float = Field(15, ge=5, le=25)
    objectives: list[str] = Field(["balanced", "min_slope", "shortest"], min_length=1, max_length=3)


class EvaRequest(BaseModel):
    out_waypoints: list[Point] = Field(..., min_length=2, max_length=20000)
    back_waypoints: list[Point] = Field(..., min_length=2, max_length=20000)
    n_stops: int = Field(0, ge=0, le=20)


class RecordIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = Field(..., min_length=1, max_length=200)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    analysis_type: str = Field("location", max_length=40)
    result: str = Field("", max_length=1000)
    elevation_m: float | None = None
    slope_deg: float | None = None
    dataset: str | None = Field(None, max_length=300)
    status: str = Field("complete", max_length=40)


class ChatIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    context: dict[str, Any] | None = None


# ------------------------------------------------------------------ app state
class State:
    settings: Settings
    terrain: TerrainService
    planner: RoutePlanner | None
    overlays: dict
    donki: Donki
    http: httpx.AsyncClient
    db: sqlite3.Connection
    lock = threading.Lock()          # rasterio datasets are not thread-safe
    assistant_url: str


S = State()


def _dataset_label(ds: dict) -> str:
    tag = "" if ds.get("data_mode") == "real" else " (SIMULATED)"
    return f"{ds.get('name')} @ {ds.get('resolution_m')} m{tag}"


def _analysis(ev: dict, dataset: dict, limit: float) -> dict:
    """Assistant route evaluation → the RouteAnalysis shape map.js reads (technical reference §3.4)."""
    m = ev["metrics"]
    profile = [{"lat": p["lat"], "lon": p["lon"], "dist_m": p["d_m"], "elevation_m": p["elev_m"], "slope_deg": p["slope_deg"]}
               for p in ev["profile"]]
    mx = m.get("max_slope_deg")
    return {
        "profile": profile,
        "distance_m": m["distance_m"],
        "elevation_gain_m": m["elevation_gain_m"],
        "elevation_loss_m": m["elevation_loss_m"],
        "max_slope_deg": mx,
        "avg_slope_deg": m.get("avg_slope_deg"),
        "over_limit": bool(mx is not None and mx > limit),
        "data_coverage_pct": m.get("data_coverage_pct"),
        "walk_time_min": m.get("walk_time_out_min"),
        "limit_deg": limit,
        "method": f"Slope = Horn 3×3 on the DEM; limit {limit:g}° (project-defined)",
        "source_dataset": _dataset_label(dataset),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.settings = Settings.from_env()
    S.terrain = TerrainService.from_settings(S.settings, JEZERO_BOUNDS)
    S.planner = RoutePlanner(S.terrain, S.settings.k_suit, S.settings.max_slope_deg) if S.terrain.sources else None
    S.overlay_dir = Path(os.environ.get("MARSWALK_OVERLAY_DIR") or VAR_DIR / "overlays")
    S.overlays = await run_in_threadpool(build_overlays, S.terrain, S.overlay_dir, S.settings.max_slope_deg,
                                         S.settings.caution_slope_deg)
    S.http = httpx.AsyncClient()
    S.donki = Donki(VAR_DIR / "donki_cache.json", os.environ.get("NASA_API_KEY") or "DEMO_KEY",
                    (os.environ.get("DONKI_ENABLED", "true").lower() not in ("0", "false", "no")))
    S.assistant_url = (os.environ.get("ASSISTANT_URL") or "http://127.0.0.1:8090").rstrip("/")
    db_path = os.environ.get("MARSWALK_RECORDS_DB") or str(VAR_DIR / "marswalk_records.sqlite3")
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    S.db = sqlite3.connect(db_path, check_same_thread=False)
    S.db.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL, created_at TEXT NOT NULL)")
    S.db.commit()
    try:
        yield
    finally:
        await S.http.aclose()
        S.db.close()


app = FastAPI(title="Marswalk backend", version="1.0.0", lifespan=lifespan,
              description="The /api/* contract used by the Marswalk 2D Mission Planner (js/map.js).")
# The page may also be opened from another local server (python http.server, VS Code Live Server); map.js then calls this one.
app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


def _need_terrain():
    if S.planner is None:
        raise HTTPException(503, detail="No elevation dataset is configured on the server (set CTX_DEM_PATH / HIRISE_DTM_PATH).")


# ------------------------------------------------------------------ layers, stops, elevation
@app.get("/api/layers")
async def layers(request: Request):
    o = S.overlays
    base = str(request.base_url).rstrip("/")             # absolute URLs: the page may be served from another origin
    dem = " + ".join(d["name"] for d in S.terrain.describe()) or "none"
    if S.terrain.data_mode != "real":
        dem = "TEST/SIMULATED terrain (no DEM configured)"           # map.js shows a warning banner for TEST data
    if o.get("error"):
        return {"bounds": JEZERO_BOUNDS, "dem": dem, "layers": [
            {"id": "slope", "name": "Slope classes", "available": False, "reason": o["error"], "source": "-"},
            {"id": "hillshade", "name": "Hillshade", "available": False, "reason": o["error"], "source": "-"}]}
    common = {"source": o["dataset"] if o["data_mode"] == "real" else "TEST " + o["dataset"], "mission": "Mars 2020 TRN / MRO HiRISE & CTX",
              "resolution_m": o["resolution_m"]}
    return {
        "bounds": o["bounds"],
        "dem": dem,
        "layers": [
            {"id": "slope", "name": "Slope classes (DEM)", "url": f"{base}/api/overlays/slope.png", "default_on": False, "opacity": 0.8,
             "units": "degrees", **common,
             "description": f"Amber: {S.settings.caution_slope_deg:g}–{S.settings.max_slope_deg:g}°, orange: above "
                            f"{S.settings.max_slope_deg:g}° (project-defined). Gentler terrain is transparent.",
             "limitations": f"Slope computed on the DEM averaged to ~{o['resolution_m']} m; boulders and small ledges are not resolved. "
                            "Thresholds are project-defined, not NASA standards."},
            {"id": "hillshade", "name": "Hillshade (DEM relief)", "url": f"{base}/api/overlays/hillshade.png", "default_on": False, "opacity": 0.6,
             "units": "relative illumination", **common,
             "description": "Shaded relief from the elevation model (sun from the north-west, 45° high).",
             "limitations": "Visual context only; read values from the elevation readout."},
        ],
    }


@app.get("/api/overlays/{name}.png")
async def overlay(name: str):
    f = (S.overlays.get("files") or {}).get(name)
    if not f:
        raise HTTPException(404, detail="overlay not available")
    return FileResponse(S.overlay_dir / f, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/stops")
async def stops():
    p = MARSWALK_ROOT / "data" / "stops.json"
    if not p.exists():
        raise HTTPException(404, detail="data/stops.json not found")
    text = p.read_text(encoding="utf-8").replace("Â·", "·")            # the file stores '·' double-encoded
    return JSONResponse(json.loads(text))


@app.get("/api/elevation")
async def elevation(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)):
    _need_terrain()

    def work():
        with S.lock:
            return S.terrain.analyze_point(lat, lon, radius_m=15.0)
    r = await run_in_threadpool(work)
    if not r["data_available"]:
        return {"elevation_m": None, "slope_deg": None, "data_available": False,
                "source_dataset": "none", "no_data_reason": f"No configured DEM covers {lat:.5f}°N {lon:.5f}°E."}
    ds = r["dataset"]
    return {"elevation_m": r["elevation_m"], "slope_deg": r["slope_at_point_deg"], "data_available": r["elevation_m"] is not None,
            "source_dataset": _dataset_label(ds),
            "no_data_reason": None if r["elevation_m"] is not None else "The DEM has no value at this exact point."}


@app.get("/api/terrain")
async def terrain(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                  radius: float = Query(150, ge=10, le=2000)):
    """Terrain statistics in a circle around a point (DEM elevation, Horn slope statistics, relief). Read-only; used by the AI assistant."""
    _need_terrain()

    def work():
        with S.lock:
            return S.terrain.analyze_point(lat, lon, radius_m=radius, max_slope_deg=S.settings.max_slope_deg)
    r = await run_in_threadpool(work)
    if r.get("dataset"):
        r["source_dataset"] = _dataset_label(r["dataset"])
    r["caution_slope_deg"] = S.settings.caution_slope_deg
    r["max_slope_deg_limit"] = S.settings.max_slope_deg
    return r


# ------------------------------------------------------------------ routes
@app.post("/api/route/options")
async def route_options(body: OptionsRequest):
    """Up to three alternative routes between two points (balanced / gentlest / most direct), same planner as /api/route/auto."""
    _need_terrain()
    bad = [o for o in body.objectives if o not in ("balanced", "min_slope", "shortest")]
    if bad:
        raise HTTPException(422, detail=f"Unknown objective(s): {bad}. Use balanced, min_slope, shortest.")

    def work():
        with S.lock:
            return S.planner.plan((body.start.lat, body.start.lon), (body.end.lat, body.end.lon), body.max_slope,
                                  body.objectives, S.settings.caution_slope_deg)
    try:
        routes, info = await run_in_threadpool(work)
    except RouteError as e:
        raise HTTPException(422, detail=e.message)
    out = []
    for r in routes:
        a = _analysis(r, info["dataset"], body.max_slope)
        a.update({"objective": r["objective"], "label": r["label"], "waypoints": [{"lat": p[0], "lon": p[1]} for p in r["geometry"]],
                  "walk_time_out_min": r["metrics"].get("walk_time_out_min"), "weighted_cost": r["metrics"].get("weighted_cost"),
                  "within_constraints": r["metrics"].get("within_constraints"), "steep_sections": r.get("steep_sections", [])[:4]})
        out.append(a)
    return {"routes": out, "dataset": _dataset_label(info["dataset"]), "limit_deg": body.max_slope}


@app.post("/api/route/analyze")
async def route_analyze(body: AnalyzeRequest):
    _need_terrain()
    limit = body.max_slope or S.settings.max_slope_deg
    pts = [(p.lat, p.lon) for p in body.waypoints]

    def work():
        with S.lock:
            return S.planner.evaluate_polyline(pts, limit, S.settings.caution_slope_deg)
    try:
        ev = await run_in_threadpool(work)
    except RouteError as e:
        raise HTTPException(422, detail=e.message)
    out = _analysis(ev, ev["dataset"], limit)
    out["route_id"] = body.route_id
    out["waypoints"] = [{"lat": a, "lon": b} for a, b in pts]
    return out


@app.post("/api/route/auto")
async def route_auto(body: AutoRequest):
    _need_terrain()
    stops = [(p.lat, p.lon, (p.model_extra or {}).get("name") or f"stop {i}") for i, p in enumerate(body.stops)]

    def work():
        legs = []
        with S.lock:
            for i, (a, b) in enumerate(zip(stops[:-1], stops[1:]), start=1):
                try:
                    routes, info = S.planner.plan((a[0], a[1]), (b[0], b[1]), body.max_slope, ["balanced"], S.settings.caution_slope_deg)
                except RouteError as e:
                    raise HTTPException(422, detail=f"Leg {i} ({a[2]} → {b[2]}): {e.message}")
                legs.append((routes[0], info["dataset"]))
        return legs
    legs = await run_in_threadpool(work)

    profile, geometry, off = [], [], 0.0
    dist = gain = loss = slope_len = cover_len = 0.0
    mx, ok_all, datasets = None, True, []
    for r, ds in legs:
        m = r["metrics"]
        for j, p in enumerate(r["profile"]):
            if profile and j == 0:
                continue                                      # shared joint between legs
            profile.append({"lat": p["lat"], "lon": p["lon"], "dist_m": round(off + p["d_m"], 1),
                            "elevation_m": p["elev_m"], "slope_deg": p["slope_deg"]})
        geometry += r["geometry"][1:] if geometry else r["geometry"]
        off += m["distance_m"]
        dist += m["distance_m"]; gain += m["elevation_gain_m"]; loss += m["elevation_loss_m"]
        slope_len += (m["avg_slope_deg"] or 0) * m["distance_m"]
        cover_len += (m["data_coverage_pct"] or 0) * m["distance_m"]
        if m["max_slope_deg"] is not None:
            mx = m["max_slope_deg"] if mx is None else max(mx, m["max_slope_deg"])
        ok_all &= bool(m["within_constraints"])
        datasets.append(_dataset_label(ds))
    return {
        "profile": profile,
        "waypoints": [{"lat": a, "lon": b} for a, b in geometry],
        "distance_m": round(dist, 0),
        "elevation_gain_m": round(gain, 1),
        "elevation_loss_m": round(loss, 1),
        "max_slope_deg": mx,
        "avg_slope_deg": round(slope_len / dist, 1) if dist else None,
        "over_limit": not ok_all,
        "data_coverage_pct": round(cover_len / dist, 1) if dist else 0,
        "legs": len(legs),
        "limit_deg": body.max_slope,
        "method": f"{ALGORITHM}; limit {body.max_slope:g}° (project-defined); start/stop cells excluded from the limit check",
        "source_dataset": "; ".join(dict.fromkeys(datasets)),
    }


@app.post("/api/eva")
async def eva(body: EvaRequest):
    _need_terrain()
    s = S.settings
    out_pts = [(p.lat, p.lon) for p in body.out_waypoints]
    back_pts = [(p.lat, p.lon) for p in body.back_waypoints]

    def work():
        with S.lock:
            return (S.planner.evaluate_polyline(out_pts, s.max_slope_deg, s.caution_slope_deg),
                    S.planner.evaluate_polyline(back_pts, s.max_slope_deg, s.caution_slope_deg))
    try:
        ev_out, ev_back = await run_in_threadpool(work)
    except RouteError as e:
        raise HTTPException(422, detail=e.message)
    walk_out = ev_out["metrics"]["walk_time_out_min"]
    walk_back = ev_back["metrics"]["walk_time_out_min"]          # the back route evaluated in its own direction
    science = body.n_stops * s.stop_minutes
    limit = s.suit_budget_hours * 60
    total = walk_out + walk_back + science
    reserve = limit - total
    target = limit * s.reserve_percent / 100
    status = "ok" if reserve >= target else "low" if reserve >= 0 else "over"
    dose = s.rad_reference_mgy_day * (total / 60) / 24
    return {
        "walk_out_min": round(walk_out, 1), "science_min": round(science, 1), "walk_back_min": round(walk_back, 1),
        "total_min": round(total, 1), "reserve_min": round(reserve, 1), "reserve_target_min": round(target, 1),
        "suit_limit_min": round(limit, 1), "status": status,
        "label": f"modified Tobler walking model, k_suit {s.k_suit}, {s.stop_minutes:g} min per stop, {s.suit_budget_hours:g} h suit, "
                 f"{s.reserve_percent:g}% reserve target (project-defined)",
        "dose_estimate_mgy": round(dose, 4),
        "dose_note": f"Curiosity RAD reference {s.rad_reference_mgy_day} mGy/day measured at Gale crater, scaled to the EVA duration; "
                     "not a Jezero measurement and not an astronaut dose",
    }


# ------------------------------------------------------------------ space weather, environment, records, chat
@app.get("/api/spaceweather")
async def spaceweather():
    return await S.donki.status(S.http)


@app.get("/api/meda")
async def meda():
    # No calibrated MEDA dataset is installed. Returning an error keeps the placeholder in the Environment tab
    # instead of showing sample values as Perseverance measurements.
    raise HTTPException(503, detail="MEDA data is not installed on this server (calibrated PDS MEDA tables are required).")


@app.post("/api/records")
async def add_record(body: RecordIn):
    created = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    cur = S.db.execute("INSERT INTO records (data, created_at) VALUES (?, ?)", (body.model_dump_json(), created))
    S.db.commit()
    return {"id": cur.lastrowid, "created_at": created, **body.model_dump()}


@app.get("/api/records")
async def list_records(q: str = Query("", max_length=200)):
    rows = S.db.execute("SELECT id, data, created_at FROM records ORDER BY id DESC LIMIT 500").fetchall()
    out = [{"id": i, "created_at": c, **json.loads(d)} for i, d, c in rows]
    if q.strip():
        ql = q.lower()
        out = [r for r in out if ql in json.dumps(r).lower()]
    return out[:100]


@app.post("/api/chat")
async def chat(body: ChatIn):
    """Forwards the built-in drawer to the Marswalk AI assistant (its legacy /api/chat contract)."""
    try:
        r = await S.http.post(f"{S.assistant_url}/api/chat", json=body.model_dump(), timeout=120)
    except httpx.HTTPError:
        raise HTTPException(501, detail="The Marswalk AI assistant server is not running.")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, detail=r.text[:300])
    return r.json()


@app.get("/api/health")
async def health():
    return {"status": "ok", "terrain": S.terrain.describe() if S.terrain else [], "overlays": {k: v for k, v in S.overlays.items() if k != "stamp"},
            "assistant_url": S.assistant_url}


# ------------------------------------------------------------------ the Marswalk page (same origin as the API)
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(MARSWALK_ROOT / "index.html")


for _url, _dir in (("/css", "css"), ("/js", "js"), ("/JS", "js"), ("/assets", "assets"), ("/data", "data")):
    if (MARSWALK_ROOT / _dir).is_dir():
        app.mount(_url, StaticFiles(directory=str(MARSWALK_ROOT / _dir)), name=f"static{_url}")
