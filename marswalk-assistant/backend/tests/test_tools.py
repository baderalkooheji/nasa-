"""Tools: registry, argument handling, real-data wiring (through the fake Marswalk client), honesty rules."""
import pytest

import tools
from tools import calculate_slope, resolve_place, tool_registry

pytestmark = pytest.mark.asyncio

EXPECTED = {"get_current_mission_state", "generate_route", "compare_routes", "display_route_on_map", "analyse_terrain", "calculate_slope",
            "estimate_eva", "analyse_environment", "search_knowledge_base", "retrieve_mission_information", "highlight_map_location",
            "zoom_map", "change_map_layer", "navigate_application", "start_route_journey", "save_to_mission_log", "get_mission_log"}


def test_registry_has_all_tools_with_schemas():
    names = {t.name for t in tool_registry.list_tools()}
    assert EXPECTED <= names
    for t in tool_registry.list_tools():
        s = t.to_ollama()
        assert s["type"] == "function" and s["function"]["parameters"]["type"] == "object"
        assert t.description


async def test_arguments_are_coerced_and_validated(fake_marswalk, run_ctx):
    r = await tool_registry.execute("calculate_slope", {"elevation1": "-2525", "elevation2": -2520, "distance_m": "100"})
    assert r.success and r.data["slope_deg"] == pytest.approx(2.86, abs=0.01)
    r = await tool_registry.execute("calculate_slope", {"elevation1": 1})
    assert not r.success and "Missing required parameter" in r.error
    r = await tool_registry.execute("no_such_tool", {})
    assert not r.success and "Tool not found" in r.error


async def test_places_resolve_from_stops_selection_and_coordinates(fake_marswalk, run_ctx):
    assert (await resolve_place("habitat"))["lat"] == pytest.approx(18.451331)
    assert (await resolve_place("stop 3"))["name"].startswith("Crater floor")
    assert (await resolve_place("delta front"))["name"].startswith("Delta front")
    assert (await resolve_place("selected"))["lat"] == pytest.approx(18.4589)
    assert (await resolve_place("18.45, 77.40"))["lon"] == pytest.approx(77.40)
    assert (await resolve_place("rover"))["name"] == "Perseverance (sol 2022)"
    assert (await resolve_place("jezero"))["lat"] == pytest.approx(18.4082)
    assert await resolve_place("olympus mons") is None


async def test_generate_route_uses_the_planner_and_queues_display(fake_marswalk, run_ctx):
    r = await tool_registry.execute("generate_route", {"destination": "delta front"})
    assert r.success
    assert fake_marswalk.calls[0][0] == "route_auto"
    route = r.data["routes"][0]
    assert route["route_id"] == "R1" and route["distance_m"] == 1234.0 and "calculated" in route["data_type"]
    assert run_ctx.actions and run_ctx.actions[0]["type"] == "display_route"
    assert "confirms" in r.data["map"]["status"]                 # never claims the map already shows it


async def test_alternatives_recommend_lowest_cost_within_limit(fake_marswalk, run_ctx):
    r = await tool_registry.execute("generate_route", {"destination": "stop 3", "alternatives": "true"})
    assert r.success and len(r.data["routes"]) == 3
    assert r.data["recommended_route_id"] == "R2"               # gentlest: lowest cost among routes within the limit
    c = await tool_registry.execute("compare_routes", {})
    assert c.success and c.data["best_by"]["shortest"] == "R3" and c.data["best_by"]["lowest_max_slope"] == "R2"


async def test_terrain_values_are_labelled(fake_marswalk, run_ctx):
    r = await tool_registry.execute("analyse_terrain", {"location": "selected", "radius_m": 200})
    assert r.success and r.data["elevation_m"] == -2525.3 and r.data["difficulty"] == "moderate"
    assert "measured" in r.data["data_type"]["elevation"] and "calculated" in r.data["data_type"]["slope"]
    assert fake_marswalk.calls[-1] == ("terrain", 18.4589, 77.4062, 200.0)


async def test_no_selection_is_reported_not_invented(fake_marswalk, run_ctx):
    run_ctx.page["selected"] = None
    r = await tool_registry.execute("analyse_terrain", {})
    assert not r.success and "No point is selected" in r.error


async def test_eva_needs_a_route_then_estimates(fake_marswalk, run_ctx):
    r = await tool_registry.execute("estimate_eva", {})
    assert not r.success and "No route" in r.error
    await tool_registry.execute("generate_route", {"destination": "stop 1"})
    r = await tool_registry.execute("estimate_eva", {"science_stops": 2})
    assert r.success and r.data["total_min"] == 95.0 and "estimated" in r.data["data_type"]


async def test_environment_reports_missing_meda_honestly(fake_marswalk, run_ctx):
    r = await tool_registry.execute("analyse_environment", {})
    assert r.success and r.data["meda_weather"]["available"] is False
    assert "Gale" in r.data["radiation_reference"]["measured_at"]


async def test_layers_are_validated_against_the_page(fake_marswalk, run_ctx):
    r = await tool_registry.execute("change_map_layer", {"layer": "HiRISE", "visible": True})
    assert r.success and run_ctx.actions[-1]["layer_id"] == "hirise"
    r = await tool_registry.execute("change_map_layer", {"layer": "geology unicorns"})
    assert not r.success and "Available layers" in r.error


async def test_mission_log_save_and_read(fake_marswalk, run_ctx):
    r = await tool_registry.execute("save_to_mission_log", {"note": "sample site"})
    assert r.success and r.data["saved"]["label"].startswith("Delta front")
    assert "sample site" in fake_marswalk.records_store[0]["result"]
    r = await tool_registry.execute("get_mission_log", {"query": "delta"})
    assert r.success and r.data["count"] == 1


async def test_rover_position_is_historical(fake_marswalk, run_ctx):
    r = await tool_registry.execute("retrieve_mission_information", {"topic": "perseverance_position"})
    assert r.success and r.data["sol"] == 2022 and "historical" in r.data["data_type"]
    assert run_ctx.actions[-1]["type"] == "highlight_location"


async def test_calculate_slope_direct():
    r = await calculate_slope(0, 10, 100)
    assert r.success and r.data["classification"] == "moderate"
