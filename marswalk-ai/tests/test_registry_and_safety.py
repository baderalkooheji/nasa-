"""Capability Registry, schema validation, permissions and failure handling."""
import asyncio

from conftest import Env, make_settings, run

from marswalk_ai.registry import Capability, CapabilityRegistry, CapResult
from marswalk_ai.schema import obj, validate


def test_schema_validator():
    s = obj({"a": {"type": "number", "minimum": 0, "maximum": 5}, "b": {"type": "string", "enum": ["x", "y"]},
             "c": {"type": "array", "items": {"type": "integer"}, "maxItems": 2}}, ["a"])
    assert validate({"a": 1}, s) == []
    assert validate({}, s)                                        # missing required
    assert validate({"a": True}, s)                               # bool is not a number
    assert validate({"a": 9}, s)                                  # above maximum
    assert validate({"a": 1, "b": "z"}, s)                        # enum
    assert validate({"a": 1, "zzz": 1}, s)                        # additionalProperties false
    assert validate({"a": 1, "c": [1, 2, 3]}, s)                  # maxItems
    assert validate({"a": 1, "c": [1.5]}, s)                      # integer items


def test_registry_execution_rules():
    async def go():
        async with Env() as env:
            s = env.session()
            from marswalk_ai.orchestrator import TurnRun
            tr = TurnRun(env.orch, s, "")
            # unknown capability
            r = await tr.call("system.exec", {"cmd": "rm -rf /"})
            assert r.status == "rejected" and r.error["code"] == "unknown_capability"
            # invalid arguments are rejected before execution
            r = await tr.call("route.generate", {"destination": "stop-1", "max_slope_deg": 80})
            assert r.status == "rejected" and r.error["code"] == "invalid_arguments"
            r = await tr.call("route.generate", {"destination": "stop-1", "evil": "x"})
            assert r.status == "rejected"
            assert not s.routes                                    # nothing ran
            # arguments that are not an object
            r = await tr.call("terrain.analyze", "not-json")
            assert r.error["code"] == "invalid_arguments"
            # unknown place
            r = await tr.call("route.generate", {"destination": "Olympus Mons"})
            assert r.status == "error" and r.error["code"] == "unknown_place"
    run(go())


def test_ungrounded_coordinates_are_refused():
    async def go():
        async with Env() as env:
            s = env.session()
            from marswalk_ai.orchestrator import TurnRun
            tr = TurnRun(env.orch, s, "highlight something")
            r = await tr.call("map.highlight_location", {"target": "point", "lat": 18.4321, "lon": 77.4321}, origin="llm")
            assert r.status == "error" and r.error["code"] == "ungrounded_coordinates"
            assert not tr.turn.frontend_actions
            # the same coordinates are fine when the user typed them
            tr2 = TurnRun(env.orch, s, "highlight 18.4321, 77.4321")
            r = await tr2.call("map.highlight_location", {"target": "point", "lat": 18.4321, "lon": 77.4321}, origin="llm")
            assert r.ok and r.status == "pending_ack"
    run(go())


def test_invalid_and_out_of_coverage_coordinates():
    async def go():
        async with Env() as env:
            s = env.session()
            # outside the planet's coordinate range -> schema rejection
            from marswalk_ai.orchestrator import TurnRun
            tr = TurnRun(env.orch, s, "analyze 95, 400")
            r = await tr.call("terrain.analyze", {"target": "point", "lat": 95, "lon": 400})
            assert r.status == "rejected"
            # valid coordinates outside any dataset -> honest "no data"
            r = await env.ask(s, "Analyze the terrain at -14.5, 175.4", {"selected": {"lat": -14.5, "lon": 175.4}})
            res = r["tool_results"][0]
            assert res["data"]["data_available"] is False
            assert "No elevation data" in r["message"]
    run(go())


def test_frontend_capabilities_require_a_connected_ui():
    async def go():
        async with Env() as env:
            s = env.session(frontend={"client": "no-map", "capabilities": []})
            names = {c.id for c in env.registry.usable(s)}
            assert "map.display_route" not in names and "route.generate" in names
            await env.ask(s, "Find me a route to the delta")
            r = await env.ask(s, "Change to the terrain map")
            assert not r["frontend_actions"]
    run(go())


def test_handler_failures_timeouts_and_priority():
    async def boom(args, ctx):
        raise RuntimeError("upstream exploded")

    async def slow(args, ctx):
        await asyncio.sleep(2)
        return CapResult({})

    async def ok(args, ctx):
        return CapResult({"v": 1})

    async def go():
        reg = CapabilityRegistry()
        reg.register(Capability(id="t.boom", name="boom", description="", input_schema=obj({}), output_schema={}, handler=boom))
        reg.register(Capability(id="t.slow", name="slow", description="", input_schema=obj({}), output_schema={}, handler=slow, timeout_s=0.05))
        reg.register(Capability(id="t.x", name="x", description="", input_schema=obj({}), output_schema={}, handler=ok, priority=20, provider="hi"))
        assert not reg.register(Capability(id="t.x", name="x", description="", input_schema=obj({}), output_schema={}, handler=ok, priority=5, provider="lo"))
        assert reg.get("t.x").provider == "hi"

        class Ctx:
            session = None
        r = await reg.execute("t.boom", {}, Ctx())
        assert r.status == "error" and r.error["code"] == "internal_error" and "exploded" in r.error["message"]
        r = await reg.execute("t.slow", {}, Ctx())
        assert r.error["code"] == "timeout"
    run(go())


def test_frontend_action_failure_is_recorded_and_visible_to_next_turn():
    async def go():
        async with Env() as env:
            s = env.session()
            r = await env.ask(s, "Show me a route to the delta")
            act = r["frontend_actions"][0]
            assert act["status"] == "pending" and act["requires_ack"]
            env.orch.action_result(s, act["action_id"], False, "map not ready")
            summary = s.mission_summary(env.services.mission.mission)
            rec = [a for a in summary["recent_frontend_actions"] if a["action_id"] == act["action_id"]][0]
            assert rec["status"] == "failed" and rec["detail"] == "map not ready"
    run(go())


def test_knowledge_unavailable_is_reported():
    async def go():
        async with Env(make_settings(knowledge_dir=None)) as env:
            assert env.registry.get("science.search_documents").availability == "unavailable"
            s = env.session()
            r = await env.ask(s, "What is the Jezero delta made of?")
            assert r["status"] == "partial" and "can't answer" in r["message"]
    run(go())


def test_knowledge_retrieval_returns_citations():
    async def go():
        async with Env() as env:
            s = env.session()
            r = await env.ask(s, "How is slope derived from an elevation model?")
            assert r["evidence"] and r["evidence"][0]["title"].startswith("TEST FIXTURE")
            assert "[DOC-1]" in r["message"]
            r = await env.ask(s, "What is the capital of France?")
            assert not r["evidence"] and "won't answer without a source" in r["message"]
    run(go())


def test_no_terrain_means_route_capability_unavailable():
    async def go():
        async with Env(make_settings(allow_synthetic_terrain=False)) as env:
            assert env.registry.get("route.generate").availability == "unavailable"
            s = env.session()
            r = await env.ask(s, "Find me a route to the delta")
            assert r["status"] == "error" and r["errors"][0]["code"] == "capability_unavailable"
    run(go())
