"""The Marswalk backend (/api/* contract of js/map.js), on SIMULATED terrain so it runs anywhere."""
import httpx
import pytest
from fastapi.testclient import TestClient

from marswalk_api.donki import Donki, classify


@pytest.fixture()
def api(monkeypatch, tmp_path):
    for k, v in {"CTX_DEM_PATH": "none", "HIRISE_DTM_PATH": "none", "DONKI_ENABLED": "false",
                 "MARSWALK_RECORDS_DB": ":memory:", "MARSWALK_OVERLAY_DIR": str(tmp_path / "overlays"),
                 "ASSISTANT_URL": "http://127.0.0.1:9"}.items():
        monkeypatch.setenv(k, v)
    from marswalk_api.main import app
    with TestClient(app) as c:
        yield c


HAB = {"id": "H", "name": "Habitat", "lat": 18.451331, "lon": 77.401257}
S1 = {"id": "1", "name": "Delta front", "lat": 18.458895, "lon": 77.406168}
S2 = {"id": "2", "name": "Ridge", "lat": 18.458109, "lon": 77.408763}


def test_layers_contract(api):
    j = api.get("/api/layers").json()
    (s, w), (n, e) = j["bounds"]
    assert s < 18.72 and n > 18.21 and w < 77.70 and e > 77.16          # overlaps NASA.jezeroBounds (map.js check)
    assert j["dem"].startswith("TEST")                                   # simulated terrain is flagged to the UI
    ids = {l["id"]: l for l in j["layers"]}
    assert set(ids) == {"slope", "hillshade"} and ids["slope"]["source"].startswith("TEST")
    png = api.get(ids["slope"]["url"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"


def test_stops_served_with_fixed_encoding(api):
    j = api.get("/api/stops").json()
    assert j["habitat"]["name"].startswith("Habitat") and "Â" not in j["habitat"]["name"]
    assert len(j["stops"]) == 3


def test_elevation(api):
    j = api.get("/api/elevation", params={"lat": 18.4555, "lon": 77.4035}).json()
    assert j["data_available"] and isinstance(j["elevation_m"], float) and isinstance(j["slope_deg"], float)
    assert "SIMULATED" in j["source_dataset"]
    out = api.get("/api/elevation", params={"lat": -10, "lon": 10}).json()
    assert out["data_available"] is False and out["elevation_m"] is None and out["no_data_reason"]
    assert api.get("/api/elevation", params={"lat": 95, "lon": 10}).status_code == 422


def test_terrain_statistics_for_the_assistant(api):
    j = api.get("/api/terrain", params={"lat": 18.4555, "lon": 77.4035, "radius": 200}).json()
    assert j["data_available"] and j["radius_m"] == 200
    for k in ("elevation_m", "relief_m", "slope_mean_deg", "slope_p90_deg", "slope_max_deg", "classification", "source_dataset"):
        assert k in j
    assert "SIMULATED" in j["source_dataset"]
    assert api.get("/api/terrain", params={"lat": -10, "lon": 10}).json()["data_available"] is False
    assert api.get("/api/terrain", params={"lat": 18.45, "lon": 77.40, "radius": 5000}).status_code == 422


def test_route_options_for_the_assistant(api):
    j = api.post("/api/route/options", json={"start": {"lat": HAB["lat"], "lon": HAB["lon"]}, "end": {"lat": S1["lat"], "lon": S1["lon"]}}).json()
    assert 1 <= len(j["routes"]) <= 3
    r = j["routes"][0]
    for k in ("objective", "label", "profile", "waypoints", "distance_m", "max_slope_deg", "walk_time_out_min", "within_constraints"):
        assert k in r
    assert api.post("/api/route/options", json={"start": {"lat": HAB["lat"], "lon": HAB["lon"]}, "end": {"lat": S1["lat"], "lon": S1["lon"]},
                                                "objectives": ["fastest"]}).status_code == 422


def test_route_analyze_contract(api):
    j = api.post("/api/route/analyze", json={"waypoints": [{"lat": HAB["lat"], "lon": HAB["lon"]}, {"lat": S1["lat"], "lon": S1["lon"]}],
                                             "route_id": "A"}).json()
    for k in ("profile", "distance_m", "elevation_gain_m", "elevation_loss_m", "max_slope_deg", "avg_slope_deg", "over_limit",
              "data_coverage_pct", "method", "source_dataset"):
        assert k in j
    p = j["profile"]
    assert len(p) >= 2 and {"lat", "lon", "dist_m", "elevation_m", "slope_deg"} <= set(p[0])
    assert p[-1]["dist_m"] == pytest.approx(j["distance_m"], abs=1)
    assert api.post("/api/route/analyze", json={"waypoints": [{"lat": 1, "lon": 2}]}).status_code == 422
    r = api.post("/api/route/analyze", json={"waypoints": [{"lat": -10, "lon": 10}, {"lat": -10.01, "lon": 10.01}]})
    assert r.status_code == 422 and "covers" in r.json()["detail"]


def test_auto_route_and_eva(api):
    out = api.post("/api/route/auto", json={"stops": [HAB, S1, S2], "max_slope": 15}).json()
    assert out["legs"] == 2 and len(out["waypoints"]) >= 3 and out["distance_m"] > 0
    dists = [p["dist_m"] for p in out["profile"]]
    assert dists == sorted(dists)                                    # legs are joined into one profile
    back = api.post("/api/route/auto", json={"stops": [S2, HAB], "max_slope": 15}).json()
    e = api.post("/api/eva", json={"out_waypoints": out["waypoints"], "back_waypoints": back["waypoints"], "n_stops": 2}).json()
    assert e["science_min"] == 60 and e["suit_limit_min"] == 240
    assert e["reserve_min"] == pytest.approx(240 - e["walk_out_min"] - e["walk_back_min"] - 60, abs=0.2)
    assert e["status"] in ("ok", "low", "over") and "Gale" in e["dose_note"]
    assert e["dose_estimate_mgy"] == pytest.approx(0.21 * (e["total_min"] / 60) / 24, abs=1e-4)


def test_meda_is_honestly_unavailable(api):
    r = api.get("/api/meda")
    assert r.status_code == 503 and "not installed" in r.json()["detail"]


def test_records_roundtrip(api):
    rec = {"label": "Test point", "lat": 18.45, "lon": 77.40, "analysis_type": "location", "result": "Elevation -2525 m",
           "elevation_m": -2525.0, "slope_deg": 3.1, "dataset": "HiRISE", "status": "complete"}
    assert api.post("/api/records", json=rec).status_code == 200
    rows = api.get("/api/records", params={"q": "test point"}).json()
    assert rows and rows[0]["label"] == "Test point" and rows[0]["created_at"]
    assert api.get("/api/records", params={"q": "zzz-nothing"}).json() == []


def test_chat_proxy_reports_missing_assistant(api):
    r = api.post("/api/chat", json={"question": "hi", "context": {}})
    assert r.status_code == 501                                       # map.js shows "assistant isn't connected yet"


def test_spaceweather_unknown_without_data(api):
    j = api.get("/api/spaceweather").json()
    assert j["status"] == "unknown" and j["flares"] == [] and j["sep_events"] == []


def test_donki_rule_and_saved_copy(tmp_path):
    assert classify([{"classType": "C3.1"}, {"classType": "M4.9"}], []) == "nominal"
    assert classify([{"classType": "M5.0"}], []) == "caution"
    assert classify([{"classType": "X1.2"}], []) == "caution"
    assert classify([], [{"sepID": "x"}]) == "caution"
    calls = {"n": 0}

    def ok(req):
        calls["n"] += 1
        return httpx.Response(200, json=[{"flrID": "F1", "classType": "M6.1"}] if req.url.path.endswith("FLR") else [])

    import asyncio

    async def go():
        d = Donki(tmp_path / "donki.json")
        live = await d.status(httpx.AsyncClient(transport=httpx.MockTransport(ok)))
        assert live["status"] == "caution" and live["cached"] is False and len(live["flares"]) == 1
        d2 = Donki(tmp_path / "donki.json")                            # new process, API down → saved copy
        d2_saved = d2._saved()
        d2_saved["fetched_at"] -= 7200
        d2.cache_file.write_text(__import__("json").dumps(d2_saved), encoding="utf-8")

        def down(req):
            raise httpx.ConnectError("offline")
        saved = await Donki(tmp_path / "donki.json").status(httpx.AsyncClient(transport=httpx.MockTransport(down)))
        assert saved["cached"] is True and saved["status"] == "caution" and "saved copy" in saved["source"]
    asyncio.run(go())
