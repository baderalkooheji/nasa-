"""The 14 required astronaut requests (plus follow-ups), offline rule-based mode, synthetic terrain.

Checks intent, tools used, status, frontend actions, and that no number in a reply is invented
(every number must be derivable from tool results or configuration)."""
import itertools
import re

from conftest import Env, run

NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def collect_numbers(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_numbers(v, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.add(float(obj))
    elif isinstance(obj, str):
        out.update(float(x) for x in NUM.findall(obj))


def ungrounded(resp, settings, extra=()):
    vals = set(extra)
    collect_numbers([r["data"] for r in resp["tool_results"]], vals)
    collect_numbers(resp["cards"], vals)
    vals |= {settings.max_slope_deg, settings.caution_slope_deg, settings.k_suit, settings.rad_reference_mgy_day, 24.0, 2.0}
    base = [v for v in vals if abs(v) < 1e6]
    derived = set(base)
    for v in base:
        derived |= {v / 1000, v / 60, v % 60, v // 60, round(v)}
    small = [v for v in base if abs(v) < 20000][:300]
    derived |= {abs(a - b) for a, b in itertools.combinations(small, 2)}
    bad = []
    for raw in NUM.findall(resp["message"]):
        n = float(raw)
        if abs(n) <= 10 and n.is_integer():
            continue
        if not any(abs(n - v) <= max(0.051, 0.006 * abs(v)) or (n.is_integer() and abs(n - v) <= 0.5) for v in derived):
            bad.append(raw)
    return bad


def tools(resp):
    return [c["capability_id"] for c in resp["tool_calls"]]


def action_types(resp):
    return [a["type"] for a in resp["frontend_actions"]]


def test_required_conversation_flow():
    async def go():
        async with Env() as env:
            s = env.session()
            S = env.settings
            seq = []

            async def ask(text, ctx=None):
                r = await env.ask(s, text, ctx)
                assert not ungrounded(r, S, NUM_IN(text)), (text, ungrounded(r, S, NUM_IN(text)), r["message"])
                seq.append((text, r))
                return r

            # 1 Find me a route to the delta
            r = await ask("Find me a route to the delta.")
            assert r["intent"]["name"] == "route_find" and r["status"] == "success"
            assert "route.generate" in tools(r)
            gen = next(x for x in r["tool_results"] if x["capability_id"] == "route.generate")
            assert gen["ok"] and len(gen["data"]["routes"]) >= 2
            assert gen["data"]["destination"]["poi_id"] == "stop-1"
            assert any(c["type"] == "route_set" for c in r["cards"])
            assert r["simulated"] is True                       # synthetic terrain must be flagged
            assert "safe" not in r["message"].lower().replace("does not make a route safe", "")

            # 2 Alternative with less steep terrain (follow-up: no destination repeated)
            r = await ask("Find an alternative with less steep terrain.")
            assert r["intent"]["name"] == "route_alternative"
            assert tools(r).count("route.generate") >= 1 and "route.compare" in tools(r)

            # 3 Compare the two routes
            r = await ask("Compare the two routes.")
            assert r["intent"]["name"] == "route_compare" and tools(r) == ["route.compare"]
            assert len(r["tool_results"][0]["data"]["routes"]) == 2

            # 4 Which route takes less time?
            r = await ask("Which route takes less time?")
            assert r["intent"]["name"] == "route_time_compare" and "takes the least walking time" in r["message"]

            # 5 Show me the steepest section -> evaluation + map highlight + zoom
            r = await ask("Show me the steepest section.")
            assert r["intent"]["name"] == "route_steepest"
            assert action_types(r) == ["highlight_location", "zoom_to_location"]

            # 6 Explain why you recommended this route
            r = await ask("Explain why you recommended this route.")
            assert r["intent"]["name"] == "explain" and "lowest" in r["message"]

            # 7 Analyze this location (uses the map selection sent by the UI)
            sel = {"selected": {"lat": 18.4555, "lon": 77.4035, "name": "Selected point"}}
            r = await ask("Analyze this location.", sel)
            assert r["intent"]["name"] == "terrain_analyze"
            ta = r["tool_results"][0]
            assert ta["arguments"]["target"] == "selected" and ta["data"]["location"]["lat"] == 18.4555

            # 8 Environment: no source connected -> honest partial answer, no invented readings
            r = await ask("What environmental information is available?")
            assert r["status"] == "partial" and r["errors"][0]["code"] == "capability_unavailable"
            assert "will not invent" in r["message"]

            # 9 Radiation: Gale reference, never a Jezero measurement
            r = await ask("What radiation information do we have?")
            assert "Gale" in r["message"] and "not at Jezero" in r["message"]
            r = await ask("What radiation dose would this EVA involve?")      # EVA card button text
            assert r["intent"]["name"] == "radiation"

            # 10 Rover location -> historical, highlighted on the map
            r = await ask("Show me the rover location.")
            assert "historical" in r["message"] and action_types(r) == ["highlight_location", "zoom_to_location"]

            # 11 Open the mission dashboard
            r = await ask("Open the mission dashboard.")
            assert action_types(r) == ["navigate_to_section"] and r["frontend_actions"][0]["payload"]["section_id"] == "dashboard"

            # 12 Change to the terrain map
            r = await ask("Change to the terrain map.")
            assert action_types(r) == ["change_layer"] and r["frontend_actions"][0]["payload"] == {"layer_id": "mola", "visible": True}

            # 13 Current mission location
            r = await ask("What is my current mission location?")
            assert r["intent"]["name"] == "mission_location" and "Habitat" in r["message"] and "mock" in r["message"]

            # 14 Make the previous route shorter
            r = await ask("Make the previous route shorter.")
            assert r["intent"]["name"] == "route_shorter"
            g = [x for x in r["tool_results"] if x["capability_id"] == "route.generate"][0]
            assert g["arguments"]["mode"] == "shortest"

            # extra follow-ups
            r = await ask("How long will this journey take?")
            assert r["intent"]["name"] == "eva_estimate" and any(c["type"] == "eva" for c in r["cards"])
            r = await ask("Show the second option")
            assert action_types(r) == ["display_route"]
            first_set = s.route_sets[0]["route_ids"]
            assert r["frontend_actions"][0]["payload"]["route_id"] == first_set[1]
            r = await ask("What should I investigate next?")
            assert r["intent"]["name"] == "next_target"
            r = await ask("What are the biggest risks ahead?")
            assert r["intent"]["name"] == "risks" and "does not establish safety" in r["message"]
            return seq
    run(go())


def NUM_IN(text):
    return {float(x) for x in NUM.findall(text)}


def test_follow_up_uses_memory_not_repetition():
    async def go():
        async with Env() as env:
            s = env.session()
            await env.ask(s, "Find me a route to the upper delta-front ridge")
            assert s.destination["poi_id"] == "stop-2"
            r = await env.ask(s, "Can you find a shorter one?")
            g = next(x for x in r["tool_results"] if x["capability_id"] == "route.generate")
            assert g["arguments"]["destination"] == "stop-2"         # resolved from memory
    run(go())


def test_ambiguous_follow_ups_ask_for_clarification():
    async def go():
        async with Env() as env:
            s = env.session()
            r = await env.ask(s, "Compare them")
            assert r["status"] == "needs_clarification" and not r["tool_calls"]
            r = await env.ask(s, "Find me a route")
            assert r["status"] == "needs_clarification" and "Known places" in r["message"]
            r = await env.ask(s, "Make it shorter")
            assert r["status"] == "needs_clarification"
            r = await env.ask(s, "flibbertigibbet")
            assert r["status"] == "needs_clarification"
    run(go())


def test_slope_constraint_and_display_request():
    async def go():
        async with Env() as env:
            s = env.session()
            r = await env.ask(s, "Show me a route to the landing site avoiding slopes above 12 degrees")
            g = next(x for x in r["tool_results"] if x["capability_id"] == "route.generate")
            assert g["arguments"]["max_slope_deg"] == 12.0
            assert s.preferences["max_slope_deg"] == 12.0
            assert "display_route" in action_types(r)
            for rt in g["data"]["routes"]:
                if rt["within_constraints"]:
                    assert rt["max_slope_deg"] <= 12.0
    run(go())


def test_real_marswalk_sections_do_not_hijack_route_requests():
    """Regression: the real Marswalk page has a section with id 'route'; 'show me a route' must still plan a route."""
    real_ui = {"client": "marswalk-2d", "capabilities": ["map.display_route", "navigation.open_section"],
               "sections": [{"id": "route", "label": "Route planner tab", "keywords": ["route planner", "route tab"]},
                            {"id": "records", "label": "Mission log", "keywords": ["mission log"]}], "layers": []}

    async def go():
        async with Env() as env:
            s = env.session(real_ui)
            r = await env.ask(s, "Show me a route to the delta")
            assert r["intent"]["name"] == "route_find" and "display_route" in action_types(r)
            r = await env.ask(s, "Open the route planner")
            assert r["frontend_actions"][0]["payload"] == {"section_id": "route"}
            r = await env.ask(s, "Open the mission dashboard")
            assert r["status"] == "needs_clarification" and "Route planner tab" in r["message"] and not r["frontend_actions"]
    run(go())


def test_save_requires_confirmation():
    async def go():
        async with Env() as env:
            s = env.session()
            await env.ask(s, "Find me a route to the delta")
            r = await env.ask(s, "Save this route to the mission log")
            assert r["status"] == "needs_confirmation" and r["pending_confirmations"]
            assert env.services.store.list_records() == []              # nothing written yet
            cid = r["pending_confirmations"][0]["confirmation_id"]
            done = await env.orch.confirm(s, cid, True)
            assert done["status"] == "success" and len(env.services.store.list_records()) == 1
            again = await env.orch.confirm(s, cid, True)
            assert again["status"] == "error"                            # one-shot
    run(go())
