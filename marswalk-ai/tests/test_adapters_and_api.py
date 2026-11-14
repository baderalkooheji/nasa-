"""Science Engine and Marswalk backend adapters (against fake servers), the HTTP API and the legacy /api/chat shim."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FULL_FRONTEND, REAL_DEM, Env, make_settings, run


# ------------------------------------------------------------------ fake upstream servers
def fake_engine(fail_eva=False):
    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/api/health":
            return httpx.Response(200, json={"status": "healthy"})
        body = json.loads(req.content or b"{}")
        if p.endswith("/eva-estimation"):
            if fail_eva:
                return httpx.Response(500, json={"detail": "engine crashed"})
            return httpx.Response(200, json={"analysis_id": "eva-1", "status": "success", "calculations": {
                "outbound_walking_hours": 0.2, "return_walking_hours": 0.2, "scientific_activity_hours": 0.5, "total_eva_duration_hours": 0.9,
                "configured_budget_hours": 4.0, "required_reserve_hours": 0.8, "remaining_margin_hours": 2.3},
                "classification": {"severity": "Nominal"}, "evidence": [{"citation_key": "REF-1", "title": "Engine doc", "source_url": "https://x",
                                                                        "relevant_text": "t"}], "limitations": ["engine limitation"], "uncertainty": [],
                "inputs_seen": body})
        if p.endswith("/space-weather-analysis"):
            return httpx.Response(200, json={"analysis_id": "sw-1", "status": "success",
                                             "measurements": {"inspected_sep_count": 1, "inspected_flare_count": 2, "inspected_cme_count": 1,
                                                              "data_source": "local_resilience_cache"},
                                             "calculations": {"active_hazards_count": 0},
                                             "classification": {"space_weather_status": "Nominal"}, "limitations": [], "uncertainty": []})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def fake_marswalk():
    stops = {"habitat": {"id": "H", "name": "Real habitat", "lat": 18.4513, "lon": 77.4012},
             "stops": [{"id": "1", "name": "Real delta stop", "lat": 18.4589, "lon": 77.4062}]}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/api/stops":
            return httpx.Response(200, json=stops)
        if p == "/api/route/auto":
            b = json.loads(req.content)
            assert set(b) == {"stops", "max_slope"}
            a, z = b["stops"][0], b["stops"][-1]
            prof = [{"lat": a["lat"], "lon": a["lon"], "dist_m": 0, "elevation_m": -2525.0, "slope_deg": None},
                    {"lat": (a["lat"] + z["lat"]) / 2, "lon": (a["lon"] + z["lon"]) / 2, "dist_m": 300, "elevation_m": -2522.0, "slope_deg": 6.0},
                    {"lat": z["lat"], "lon": z["lon"], "dist_m": 610, "elevation_m": -2521.0, "slope_deg": 4.0}]
            return httpx.Response(200, json={"profile": prof, "distance_m": 610, "elevation_gain_m": 4, "elevation_loss_m": 0,
                                             "max_slope_deg": 6.0, "avg_slope_deg": 5.0, "over_limit": False, "data_coverage_pct": 100,
                                             "method": "A* on slope cost", "source_dataset": "HiRISE DTM",
                                             "waypoints": [{"lat": x["lat"], "lon": x["lon"]} for x in prof]})
        if p == "/api/spaceweather":
            return httpx.Response(200, json={"status": "caution", "flares": [1], "sep_events": [1, 2], "rule": "SEP in 7 d", "cached": False})
        return httpx.Response(404, json={"detail": "not built"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_science_engine_adapter_replaces_local_fallbacks():
    async def go():
        async with Env(make_settings(science_engine_url="http://engine"), http=fake_engine()) as env:
            assert env.registry.get("eva.estimate").provider == "science-engine"
            assert env.registry.get("space_weather.analyze").availability == "available"
            assert env.services.status["science_engine"]["reachable"]
            s = env.session()
            await env.ask(s, "Find me a route to the delta")
            r = await env.ask(s, "How long will this journey take?")
            eva = next(x for x in r["tool_results"] if x["capability_id"] == "eva.estimate")
            assert eva["provenance"]["provider"] == "science-engine" and eva["data"]["total_min"] == 54.0
            assert "engine limitation" in r["limitations"]
            assert any(e["title"] == "Engine doc" for e in r["evidence"])
            r = await env.ask(s, "What is the space weather?")
            assert "Nominal" in r["message"] and "local_resilience_cache" in r["message"]
    run(go())


def test_upstream_failure_is_reported_not_hidden():
    async def go():
        async with Env(make_settings(science_engine_url="http://engine"), http=fake_engine(fail_eva=True)) as env:
            s = env.session()
            await env.ask(s, "Find me a route to the delta")
            r = await env.ask(s, "How long will this journey take?")
            assert r["status"] == "error" and r["errors"][0]["code"] == "upstream_error"
            assert "engine crashed" in r["message"]
    run(go())


def test_unreachable_engine_keeps_local_fallbacks():
    def down(req):
        raise httpx.ConnectError("refused")

    async def go():
        async with Env(make_settings(science_engine_url="http://engine"), http=httpx.AsyncClient(transport=httpx.MockTransport(down))) as env:
            assert env.registry.get("eva.estimate").provider == "standalone"
            assert env.services.status["science_engine"]["reachable"] is False
    run(go())


def test_marswalk_backend_adapter():
    async def go():
        st = make_settings(marswalk_api_url="http://mw", marswalk_enabled_endpoints=["route_auto"])
        async with Env(st, http=fake_marswalk()) as env:
            assert set(env.services.status["marswalk_backend"]["endpoints"]) == {"route_auto", "stops", "spaceweather"}
            assert env.registry.get("route.generate").provider == "marswalk-backend"
            assert env.services.mission.get("stop-1")["name"] == "Real delta stop"       # /api/stops replaced the mock places
            assert "delta" in env.services.mission.get("stop-1")["aliases"]
            s = env.session()
            r = await env.ask(s, "Show me a route to the delta")
            g = next(x for x in r["tool_results"] if x["capability_id"] == "route.generate")
            assert g["provenance"]["provider"] == "marswalk-backend" and g["data"]["routes"][0]["distance_m"] == 610
            assert r["frontend_actions"][0]["payload"]["geometry"][0] == [18.4513, 77.4012]
            r = await env.ask(s, "space weather status?")
            assert "caution" in r["message"]
    run(go())


@pytest.mark.skipif(not REAL_DEM, reason="Mars 2020 DEMs not present in ../data/raw")
def test_real_dem_terrain_matches_rover_elevation():
    async def go():
        st = make_settings()
        from marswalk_ai.config import Settings
        d = Settings.from_env()
        st.ctx_dem_path, st.hirise_dtm_path = d.ctx_dem_path, d.hirise_dtm_path
        async with Env(st) as env:
            assert env.services.terrain.data_mode == "real"
            s = env.session()
            r = await env.ask(s, "Analyze the terrain around the habitat")
            t = r["tool_results"][0]["data"]
            hab = env.services.mission.get("habitat")
            assert t["dataset"]["id"] == "hirise-dtm"
            assert abs(t["elevation_m"] - hab["rover_elev_geoid_m"]) < 2.0          # DTM vs rover-measured, same areoid
            assert r["simulated"] is False
    run(go())


# ------------------------------------------------------------------ HTTP API
@pytest.fixture()
def client(monkeypatch):
    for k, v in {"LLM_PROVIDER": "offline", "SESSION_DB": ":memory:", "CTX_DEM_PATH": "none", "HIRISE_DTM_PATH": "none",
                 "KNOWLEDGE_DIR": "none", "SCIENCE_ENGINE_URL": "", "MARSWALK_API_URL": ""}.items():
        monkeypatch.setenv(k, v)
    from marswalk_ai.main import app
    with TestClient(app) as c:
        yield c


def test_http_api_flow(client):
    assert client.get("/api/assistant/health").json()["status"] == "ok"
    sid = client.post("/api/assistant/sessions", json={"frontend": FULL_FRONTEND}).json()["session_id"]
    caps = client.get(f"/api/assistant/capabilities?session_id={sid}").json()["capabilities"]
    assert any(c["id"] == "map.change_layer" and c["input_schema"]["properties"]["layer_id"]["enum"] == ["mola", "hirise"] for c in caps)
    r = client.post("/api/assistant/chat", json={"session_id": sid, "message": "Show me a route to the delta"}).json()
    assert r["status"] == "success" and r["frontend_actions"][0]["type"] == "display_route"
    act = r["frontend_actions"][0]["action_id"]
    assert client.post("/api/assistant/actions/result", json={"session_id": sid, "action_id": act, "ok": True}).json()["status"] == "succeeded"
    rid = r["cards"][0]["data"]["routes"][0]["route_id"]
    c = client.post("/api/assistant/command", json={"session_id": sid, "capability_id": "map.display_route", "arguments": {"route_id": rid}}).json()
    assert c["frontend_actions"][0]["payload"]["route_id"] == rid
    bad = client.post("/api/assistant/command", json={"session_id": sid, "capability_id": "map.display_route", "arguments": {"route_id": 5}}).json()
    assert bad["status"] == "error" and bad["errors"][0]["code"] == "invalid_arguments"
    assert client.post("/api/assistant/chat", json={"session_id": "nope", "message": "hi"}).status_code == 404
    assert client.post("/api/assistant/chat", json={"session_id": sid, "message": ""}).status_code == 422
    state = client.get(f"/api/assistant/sessions/{sid}").json()
    assert state["actions"][act]["status"] == "succeeded"


def test_legacy_marswalk_chat_contract(client):
    r = client.post("/api/chat", json={"question": "Tell me about this location",
                                       "context": {"selected": {"lat": 18.4555, "lon": 77.4035, "name": None, "elevation_m": -2524,
                                                                "slope_deg": 3.1, "source": "DEM"}, "active_layers": [], "route_a": None,
                                                   "route_b": None, "eva": None, "max_slope_deg": 15, "dem": "TEST"}})
    j = r.json()
    assert r.status_code == 200 and set(j) == {"answer", "sources"} and "Terrain within" in j["answer"]
    r = client.post("/api/chat", json={"question": "Change to the terrain map"}).json()
    assert "no map connected" in r["answer"]                    # the legacy drawer cannot execute map actions
