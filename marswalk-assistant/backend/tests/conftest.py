"""
Marswalk AI Assistant - test configuration.

Tests run without Ollama and without the Marswalk server:
  * `fake_marswalk` replaces the Marswalk HTTP client with canned responses shaped like the real endpoints;
  * the chat tests script the model's replies.
Each test session uses its own SQLite file; the knowledge base indexes the real documents in data/documents.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
_tmp = Path(tempfile.mkdtemp(prefix="mwx-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["CHROMA_PERSIST_DIR"] = str(_tmp / "chroma")
os.environ["DOCUMENTS_DIR"] = str(BACKEND / "data" / "documents")

STOPS = {
    "habitat": {"id": "H", "name": "Habitat · Three Forks area (crater floor at the delta base)", "lat": 18.451331, "lon": 77.401257,
                "reason": "Flat crater floor"},
    "stops": [
        {"id": "1", "name": "Delta front · Hawksbill Gap", "lat": 18.458895, "lon": 77.406168, "reason": "Layered rocks"},
        {"id": "2", "name": "Upper delta-front ridge", "lat": 18.458109, "lon": 77.408763, "reason": "Second look"},
        {"id": "3", "name": "Crater floor east of the depot", "lat": 18.45267, "lon": 77.408056, "reason": "Older unit"},
    ],
}


def _route(label, objective, dist, mx, ok, cost):
    return {"profile": [{"lat": 18.4513, "lon": 77.4012, "dist_m": 0, "elevation_m": -2525.0, "slope_deg": 3.0},
                        {"lat": 18.4588, "lon": 77.4061, "dist_m": dist, "elevation_m": -2520.8, "slope_deg": mx}],
            "waypoints": [{"lat": 18.4513, "lon": 77.4012}, {"lat": 18.4588, "lon": 77.4061}],
            "distance_m": dist, "elevation_gain_m": 9.0, "elevation_loss_m": 4.0, "max_slope_deg": mx, "avg_slope_deg": 4.0,
            "over_limit": not ok, "data_coverage_pct": 100.0, "limit_deg": 15, "source_dataset": "HiRISE DTM @ 1 m",
            "objective": objective, "label": label, "walk_time_out_min": dist / 50, "weighted_cost": cost, "within_constraints": ok}


class FakeMarswalk:
    """Canned responses with the shape of marswalk_api/main.py (values are fixtures, not Mars data)."""

    def __init__(self):
        self.records_store = []
        self.calls = []

    async def stops(self):
        return STOPS

    async def health(self):
        return {"status": "ok"}

    async def elevation(self, lat, lon):
        return {"elevation_m": -2523.5, "slope_deg": 2.0, "data_available": True, "source_dataset": "HiRISE DTM @ 1 m"}

    async def terrain(self, lat, lon, radius=150):
        self.calls.append(("terrain", lat, lon, radius))
        return {"data_available": True, "elevation_m": -2525.3, "elevation_min_m": -2530.0, "elevation_max_m": -2519.0, "relief_m": 11.0,
                "slope_at_point_deg": 2.1, "slope_mean_deg": 4.2, "slope_p90_deg": 8.9, "slope_max_deg": 31.0,
                "area_above_caution_pct": 6.0, "area_above_limit_pct": 1.5, "classification": {"terrain_difficulty": "moderate",
                "basis": "90th-percentile slope"}, "thresholds": {"max_slope_deg": 15, "caution_slope_deg": 10},
                "source_dataset": "HiRISE DTM @ 1 m"}

    async def route_auto(self, stops, max_slope=15):
        self.calls.append(("route_auto", stops, max_slope))
        r = _route("x", "balanced", 1234.0, 14.8, True, 1.2)
        for k in ("objective", "label", "walk_time_out_min", "weighted_cost", "within_constraints"):
            r.pop(k)
        return r

    async def route_options(self, start, end, max_slope=15, objectives=None):
        self.calls.append(("route_options", start, end, max_slope))
        return {"routes": [_route("Balanced: lowest terrain cost", "balanced", 900.0, 14.6, True, 1.3),
                           _route("Gentlest slopes", "min_slope", 1100.0, 9.8, True, 1.1),
                           _route("Most direct", "shortest", 700.0, 22.4, False, 2.4)], "dataset": "HiRISE DTM @ 1 m", "limit_deg": max_slope}

    async def eva(self, out_w, back_w, n_stops=0):
        return {"walk_out_min": 18.0, "science_min": 30.0 * n_stops, "walk_back_min": 17.0, "total_min": 35.0 + 30 * n_stops,
                "suit_limit_min": 240.0, "reserve_min": 175.0, "reserve_target_min": 48.0, "status": "ok", "dose_estimate_mgy": 0.0096,
                "label": "modified Tobler walking model (project-defined)", "dose_note": "Gale crater reference, scaled"}

    async def spaceweather(self):
        return {"status": "quiet", "flares": 0}

    async def meda(self):
        from marswalk_client import MarswalkUnavailable
        raise MarswalkUnavailable("/api/meda failed (503): MEDA data is not installed on this server")

    async def records(self, q=""):
        return [r for r in reversed(self.records_store) if q.lower() in str(r).lower()]

    async def add_record(self, rec):
        rec = {"id": len(self.records_store) + 1, "created_at": "2026-11-15 10:00", **rec}
        self.records_store.append(rec)
        return rec

    async def rover_waypoints(self):
        return {"features": [{"properties": {"sol": 13, "lat": 18.4446, "lon": 77.4509, "elev_geoid": -2569.9, "dist_total_m": 0}},
                             {"properties": {"sol": 2022, "lat": 18.4356, "lon": 77.2332, "elev_geoid": -1936.4, "dist_total_m": 45245.7,
                                             "dist_km": 45.25}}]}

    async def close(self):
        pass


@pytest.fixture
def fake_marswalk(monkeypatch):
    import marswalk_client
    import tools
    fake = FakeMarswalk()
    monkeypatch.setattr(tools, "marswalk", fake)
    monkeypatch.setattr(marswalk_client, "marswalk", fake)
    try:
        import main
        monkeypatch.setattr(main, "marswalk", fake)
    except Exception:
        pass
    return fake


@pytest.fixture
def run_ctx():
    """Fresh per-turn context (page state) for tool tests."""
    import tools
    ctx = tools.RunContext(page={
        "view": "2d",
        "selected": {"lat": 18.4589, "lon": 77.4062, "name": "Delta front · Hawksbill Gap", "elevation_m": -2520.9, "slope_deg": 6.1},
        "layers": [{"id": "hirise", "label": "HiRISE orthomosaic, walk zone", "keywords": ["hirise"]},
                   {"id": "mola", "label": "MOLA shaded elevation", "keywords": ["mola", "elevation"]}],
    })
    token = tools.set_run_context(ctx)
    yield ctx
    tools.reset_run_context(token)
