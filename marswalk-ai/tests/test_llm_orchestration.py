"""LLM tool-calling loop with a scripted provider, provider wire formats, and LLM failure fallback."""
import json

import httpx

from conftest import Env, run

from marswalk_ai.providers.anthropic import AnthropicProvider
from marswalk_ai.providers.base import LLMProvider, LLMResponse, ProviderError, ToolCallRequest, ToolSpec, extract_json_object
from marswalk_ai.providers.gemini import GeminiProvider, sanitize_schema
from marswalk_ai.providers.openai_compat import OpenAICompatProvider


class Scripted(LLMProvider):
    """Returns pre-written turns; records what it was given."""
    name, model = "scripted", "script-1"

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    async def complete(self, system, transcript, tools):
        self.seen.append({"system": system, "transcript": [dict(t) for t in transcript], "tools": [t.name for t in tools]})
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


def call(name, args, i="c1"):
    return ToolCallRequest(id=i, name=name, arguments=args)


def test_llm_multi_tool_request_and_pending_actions():
    prov = Scripted([
        LLMResponse(tool_calls=[call("mission__get_context", {}, "a")]),
        LLMResponse(tool_calls=[call("route__generate", {"destination": "stop-1"}, "b")]),
        LLMResponse(tool_calls=[call("map__display_route", {"route_id": "R2"}, "c")]),
        LLMResponse(text="I generated three candidates; R2 has the lowest estimated terrain difficulty. Sending it to the map."),
    ])

    async def go():
        async with Env(provider=prov) as env:
            s = env.session()
            r = await env.ask(s, "Find me the best route to the delta and show it on the map")
            assert r["provider"]["mode"] == "llm"
            assert [c["capability_id"] for c in r["tool_calls"]] == ["mission.get_context", "route.generate", "map.display_route"]
            assert r["frontend_actions"][0]["type"] == "display_route" and r["frontend_actions"][0]["status"] == "pending"
            assert r["intent"]["name"] == "route_planning"
            # tool results fed back to the model are trimmed (no geometry) and say pending_ack for map actions
            last = prov.seen[-1]["transcript"]
            tool_msgs = [t for t in last if t["role"] == "tool"]
            assert all("geometry" not in t["content"] for t in tool_msgs)
            assert json.loads(tool_msgs[-1]["content"])["status"] == "pending_ack"
            # mission state and the tool list reach the model
            assert "MISSION STATE" in prov.seen[0]["system"] and "route__generate" in prov.seen[0]["tools"]
    run(go())


def test_llm_invalid_args_and_unknown_tools_are_rejected_and_reported_back():
    prov = Scripted([
        LLMResponse(tool_calls=[call("route__generate", {"destination": "stop-1", "max_slope_deg": 90}, "a"),
                                call("shell__exec", {"cmd": "ls"}, "b")]),
        LLMResponse(text="The route request was rejected because 90° is outside the allowed range."),
    ])

    async def go():
        async with Env(provider=prov) as env:
            s = env.session()
            r = await env.ask(s, "route to the delta with max slope 90")
            st = {x["capability_id"]: x["status"] for x in r["tool_results"]}
            assert st == {"route.generate": "rejected", "shell.exec": "rejected"}
            assert not s.routes and r["status"] == "partial"
            fed_back = [json.loads(t["content"]) for t in prov.seen[-1]["transcript"] if t["role"] == "tool"]
            assert {f["error"]["code"] for f in fed_back} == {"invalid_arguments", "unknown_capability"}
    run(go())


def test_grounding_check_flags_invented_numbers_and_safety_claims():
    prov = Scripted([
        LLMResponse(tool_calls=[call("rover__get_location", {}, "a")]),
        LLMResponse(text="The rover is at sol 2022 and the path is completely safe; it is 4321.7 m away."),
    ])

    async def go():
        async with Env(provider=prov) as env:
            s = env.session()
            r = await env.ask(s, "where is the rover")
            grounding = next(x for x in r["limitations"] if x.startswith("Grounding check"))
            assert "4321.7" in grounding and "2022" not in grounding       # sol 2022 is in the tool result
            assert any("never establish safety" in x for x in r["limitations"])
    run(go())


def test_llm_failure_falls_back_to_rules():
    prov = Scripted([ProviderError("gemini HTTP 503: overloaded")])

    async def go():
        async with Env(provider=prov) as env:
            s = env.session()
            r = await env.ask(s, "Show me the rover location")
            assert r["provider"]["mode"] == "rules-fallback"
            assert r["message"].startswith("(Language model unavailable")
            assert any(e["code"] == "llm_unavailable" for e in r["errors"])
            assert "rover.get_location" in [c["capability_id"] for c in r["tool_calls"]]
    run(go())


def test_llm_failure_mid_turn_reports_completed_operations():
    prov = Scripted([LLMResponse(tool_calls=[call("rover__get_location", {}, "a")]), ProviderError("timeout")])

    async def go():
        async with Env(provider=prov) as env:
            s = env.session()
            r = await env.ask(s, "Where is the rover?")
            assert r["status"] == "partial" and "failed part-way" in r["message"] and "rover.get_location: ok" in r["message"]
    run(go())


def test_tool_round_limit():
    prov = Scripted([LLMResponse(tool_calls=[call("rover__get_location", {}, f"x{i}")]) for i in range(10)])

    async def go():
        async with Env(provider=prov) as env:
            env.settings.max_tool_rounds = 3
            s = env.session()
            r = await env.ask(s, "loop forever")
            assert r["status"] == "partial" and len(r["tool_calls"]) == 3
    run(go())


# ------------------------------------------------------------------ provider wire formats
def _capture(response_json):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=response_json)
    return seen, httpx.AsyncClient(transport=httpx.MockTransport(handler))


TOOLS = [ToolSpec("route__generate", "d", {"type": "object", "properties": {"destination": {"type": "string", "default": "x"},
                                                                            "n": {"type": ["number", "null"]}},
                                           "required": ["destination"], "additionalProperties": False}),
         ToolSpec("mission__get_context", "d", {"type": "object", "properties": {}, "required": [], "additionalProperties": False})]
TRANSCRIPT = [{"role": "user", "content": "go"},
              {"role": "assistant", "content": "", "tool_calls": [ToolCallRequest("t1", "route__generate", {"destination": "stop-1"}),
                                                                  ToolCallRequest("t2", "mission__get_context", {})], "raw": None},
              {"role": "tool", "tool_call_id": "t1", "name": "route__generate", "content": json.dumps({"ok": True})},
              {"role": "tool", "tool_call_id": "t2", "name": "mission__get_context", "content": json.dumps({"ok": True})}]


def test_openai_wire_format():
    seen, http = _capture({"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "route__generate", "arguments": "{\"destination\": \"stop-2\"}"}}]}}]})

    async def go():
        p = OpenAICompatProvider("k", "m", "https://api.example/v1", http, 5, 0.2)
        r = await p.complete("sys", TRANSCRIPT, TOOLS)
        assert seen["url"].endswith("/chat/completions") and seen["headers"]["authorization"] == "Bearer k"
        roles = [m["role"] for m in seen["body"]["messages"]]
        assert roles == ["system", "user", "assistant", "tool", "tool"]
        assert r.tool_calls[0].arguments == {"destination": "stop-2"}
    run(go())


def test_openai_json_mode_for_models_without_tools():
    seen, http = _capture({"choices": [{"message": {"content": "Sure.\n```json\n{\"tool_calls\": [{\"name\": \"mission__get_context\", \"arguments\": {}}]}\n```"}}]})

    async def go():
        p = OpenAICompatProvider(None, "local", "http://localhost:11434/v1", http, 5, 0.2, tool_mode="json")
        r = await p.complete("sys", [{"role": "user", "content": "hi"}], TOOLS)
        assert "tools" not in seen["body"] and "Available tools" in seen["body"]["messages"][0]["content"]
        assert r.tool_calls[0].name == "mission__get_context"
    run(go())
    assert extract_json_object('noise {"final": "a {b}"} tail') == {"final": "a {b}"}


def test_anthropic_wire_format_groups_tool_results():
    seen, http = _capture({"content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "tu", "name": "route__generate",
                                                                     "input": {"destination": "stop-1"}}], "usage": {}})

    async def go():
        p = AnthropicProvider("k", "claude-x", http, 5, 0.2)
        r = await p.complete("sys", TRANSCRIPT, TOOLS)
        b = seen["body"]
        assert seen["headers"]["x-api-key"] == "k" and seen["headers"]["anthropic-version"]
        assert [m["role"] for m in b["messages"]] == ["user", "assistant", "user"]
        assert [x["type"] for x in b["messages"][2]["content"]] == ["tool_result", "tool_result"]
        assert b["tools"][0]["input_schema"]["required"] == ["destination"]
        assert r.text == "ok" and r.tool_calls[0].arguments == {"destination": "stop-1"}
    run(go())


def test_gemini_wire_format_and_schema_sanitising():
    seen, http = _capture({"candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"name": "route__generate", "args": {"destination": "stop-3"}}}]}}]})

    async def go():
        p = GeminiProvider("k", "gemini-x", http, 5, 0.2)
        r = await p.complete("sys", TRANSCRIPT, TOOLS)
        b = seen["body"]
        assert "gemini-x:generateContent" in seen["url"] and seen["headers"]["x-goog-api-key"] == "k"
        decl = b["tools"][0]["functionDeclarations"]
        assert "parameters" not in decl[1]                         # no-argument function
        params = decl[0]["parameters"]
        assert "additionalProperties" not in params and "default" not in params["properties"]["destination"]
        assert params["properties"]["n"] == {"type": "number", "nullable": True}
        parts = b["contents"][-1]["parts"]
        assert [list(x) for x in parts] == [["functionResponse"], ["functionResponse"]]
        assert r.tool_calls[0].arguments == {"destination": "stop-3"}
    run(go())
    assert sanitize_schema({"type": "object", "title": "x", "properties": {"a": {"type": "string", "maxLength": 3}}}) == \
        {"type": "object", "properties": {"a": {"type": "string"}}}


def test_provider_http_errors_never_leak_keys():
    async def go():
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}})))
        p = GeminiProvider("SECRET-KEY-123", "g", http, 5, 0.2)
        try:
            await p.complete("s", [{"role": "user", "content": "x"}], [])
        except ProviderError as e:
            assert "SECRET" not in str(e) and "401" in str(e)
        else:
            raise AssertionError("expected ProviderError")
    run(go())
