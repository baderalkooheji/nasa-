"""Chat endpoint: the agent loop (model â†’ tools â†’ model), mission memory, actions, and failure handling (scripted model)."""
import pytest
from fastapi.testclient import TestClient

import main
from llm import LLMResponse, LLMUnavailable, ToolCall


class ScriptedLLM:
    """Replays prepared model turns; records what the model was sent."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.sent = []
        self.model = "scripted"

    async def chat(self, messages, tools=None, **kw):
        self.sent.append({"messages": list(messages), "tools": tools})
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        if isinstance(t, ToolCall):
            return LLMResponse(content="", tool_calls=[t], model="scripted",
                               raw_message={"role": "assistant", "content": "", "tool_calls": [{"function": {"name": t.name, "arguments": t.arguments}}]})
        return LLMResponse(content=t, model="scripted", raw_message={"role": "assistant", "content": t})

    async def list_models(self):
        return ["scripted"]


@pytest.fixture
def client(fake_marswalk, monkeypatch):
    with TestClient(main.app) as c:
        yield c


PAGE = {"view": "2d", "selected": {"lat": 18.4589, "lon": 77.4062, "name": "Delta front Â· Hawksbill Gap"}, "layers": []}


def test_tool_results_go_back_to_the_model_and_actions_to_the_page(client, monkeypatch):
    llm = ScriptedLLM([ToolCall(name="generate_route", arguments={"destination": "delta front"}),
                       "R1 is 1234 m long with a maximum slope of 14.8Â° (calculated from the DEM). Sending it to the map."])
    monkeypatch.setattr(main, "llm_client", llm)
    r = client.post("/chat", json={"message": "Delta front, please, with the planner", "client_context": PAGE}).json()
    assert r["message"].startswith("R1 is 1234 m")
    gi = [c["name"] for c in r["tool_calls"]].index("generate_route")
    assert r["tool_results"][gi]["success"]
    assert r["actions"][0]["type"] == "display_route" and r["actions"][0]["analysis"]["distance_m"] == 1234.0
    tool_msg = [m for m in llm.sent[1]["messages"] if m["role"] == "tool"][0]          # the model saw the real result
    assert '"distance_m": 1234.0' in tool_msg["content"]
    assert "Delta front" in llm.sent[0]["messages"][0]["content"]                      # page state in the system prompt

    # follow-up in the same conversation: mission memory keeps R1
    llm2 = ScriptedLLM([])                                     # EVA budgets are answered from the tool data directly
    monkeypatch.setattr(main, "llm_client", llm2)
    r2 = client.post("/chat", json={"message": "How long would that EVA take?", "conversation_id": r["conversation_id"],
                                    "client_context": PAGE}).json()
    assert "total 65.0 min of a 240.0-min suit budget" in r2["message"] and "Gale crater" in r2["message"]
    ei = [c["name"] for c in r2["tool_calls"]].index("estimate_eva")
    assert r2["tool_results"][ei]["success"] and r2["tool_results"][ei]["data"]["route_id"] == "R1"
    ctx = client.get(f"/conversations/{r['conversation_id']}/context").json()
    assert ctx["current_route"]["route_id"] == "R1" and ctx["route_comparisons"][0]["distance_m"] == 1234.0


def test_empty_model_text_falls_back_to_tool_numbers(client, monkeypatch):
    monkeypatch.setattr(main, "llm_client", ScriptedLLM([ToolCall(name="analyse_terrain", arguments={"location": "selected"}), ""]))
    r = client.post("/chat", json={"message": "analyse this", "client_context": PAGE}).json()
    assert "-2525.3 m (DEM)" in r["message"]


def test_ollama_down_is_reported_plainly(client, monkeypatch):
    monkeypatch.setattr(main, "llm_client", ScriptedLLM([LLMUnavailable("Cannot reach Ollama at http://127.0.0.1:11434")]))
    r = client.post("/chat", json={"message": "hello"}).json()
    assert r["metadata"]["error"] == "llm_unavailable" and "not available" in r["message"]


def test_action_results_are_recorded(client, monkeypatch):
    monkeypatch.setattr(main, "llm_client", ScriptedLLM([ToolCall(name="zoom_map", arguments={"location": "habitat"}), "Zooming to the habitat."]))
    r = client.post("/chat", json={"message": "zoom to the habitat", "client_context": PAGE}).json()
    a = r["actions"][0]
    assert a["type"] == "zoom_to" and a["lat"] == pytest.approx(18.451331)
    ack = client.post("/actions/result", json={"conversation_id": r["conversation_id"], "action_id": a["action_id"], "ok": True,
                                               "detail": "map moved"}).json()
    assert ack["stored"] is True


def test_health_and_tools_endpoints(client, monkeypatch):
    monkeypatch.setattr(main, "llm_client", ScriptedLLM([]))
    tools = client.get("/tools").json()
    assert len(tools) >= 17
    h = client.get("/health").json()
    assert h["knowledge_documents"] >= 15 and h["marswalk_backend"] is True


def test_knowledge_is_retrieved_before_the_model_answers(client, monkeypatch):
    llm = ScriptedLLM(["Yes: MOXIE produced 122 g of oxygen on Mars, a small demonstration [DOC-1]."])
    monkeypatch.setattr(main, "llm_client", llm)
    r = client.post("/chat", json={"message": "Can astronauts make oxygen on Mars?"}).json()
    sent = llm.sent[0]["messages"][-1]["content"]                          # passages travel with this turn's message
    assert "Knowledge base passages" in sent and "MOXIE" in sent            # passages were in front of the model
    assert r["tool_calls"][0]["name"] == "search_knowledge_base" and r["tool_calls"][0]["arguments"]["auto"] is True
    assert "[DOC-1]" in r["message"]                                        # a real citation is kept


def test_invented_citations_are_removed(client, monkeypatch):
    monkeypatch.setattr(main, "llm_client", ScriptedLLM(["The capital of France is Paris [DOC-1]."]))
    r = client.post("/chat", json={"message": "What is the capital of France?"}).json()
    assert "[DOC-1]" not in r["message"] and "no knowledge-base document supports" in r["message"]


def test_mission_requests_use_the_real_tool_even_if_the_model_would_not(client, monkeypatch):
    llm = ScriptedLLM([])                                      # answered from the NASA data directly, no model call
    monkeypatch.setattr(main, "llm_client", llm)
    r = client.post("/chat", json={"message": "Where is Perseverance now?", "client_context": PAGE}).json()
    assert r["tool_calls"][0]["name"] == "retrieve_mission_information" and r["tool_results"][0]["data"]["sol"] == 2022
    assert "sol 2022: 18.43560°N 77.23320°E" in r["message"] and "not live" in r["message"] and not llm.sent
    assert r["actions"][0]["type"] == "highlight_location"


def test_routing_keeps_general_questions_on_the_knowledge_base():
    import asyncio
    pr = lambda q: [t for t, _ in asyncio.run(main._preroute(q))]
    assert pr("How long would that EVA take?") == ["estimate_eva"]
    assert pr("How long is a day on Mars?") == []
    assert pr("Where is the rover?") == ["retrieve_mission_information"]
    assert pr("Plan a route to the rover") == ["generate_route"]
    assert pr("Compare the routes") == ["compare_routes"]
    assert pr("Give me three route options to stop 3 and compare them") == []
    assert pr("Save this location to the log") == ["save_to_mission_log"]


def test_commands_are_routed_with_arguments():
    import asyncio
    pr = lambda q: asyncio.run(main._preroute(q))
    assert pr("Show the HiRISE layer") == [("change_map_layer", {"layer": "hirise", "visible": True})]
    assert pr("hide the mola layer") == [("change_map_layer", {"layer": "mola", "visible": False})]
    assert pr("Save this location to the log") == [("save_to_mission_log", {"location": "selected"})]
    assert pr("Zoom to Jezero crater") == [("zoom_map", {"location": "jezero crater"})]
    assert pr("Open the 3D globe") == [("navigate_application", {"target": "3d"})]
    assert pr("Start the journey") == [("start_route_journey", {"route": "B"})]
    assert pr("Plan a route from the habitat to the delta front") == [("generate_route", {"start": "habitat", "destination": "delta front"})]


def test_pretended_actions_are_flagged(client, monkeypatch):
    monkeypatch.setattr(main, "llm_client", ScriptedLLM(["Sending the THEMIS layer to the map."]))
    r = client.post("/chat", json={"message": "thermal stuff please", "client_context": PAGE}).json()
    assert "no map or app action was carried out" in r["message"]
    monkeypatch.setattr(main, "llm_client", ScriptedLLM(["MOXIE showed that oxygen can be produced [DOC-1]."]))
    r = client.post("/chat", json={"message": "Can astronauts make oxygen on Mars?"}).json()
    assert "no map or app action" not in r["message"]


def test_ui_commands_and_comparisons_answer_without_the_model(client, monkeypatch):
    llm = ScriptedLLM([ToolCall(name="generate_route", arguments={"destination": "stop 3", "alternatives": True}), "R2 is recommended."])
    monkeypatch.setattr(main, "llm_client", llm)
    r = client.post("/chat", json={"message": "Give me three route options to stop 3", "client_context": PAGE}).json()
    conv = r["conversation_id"]
    silent = ScriptedLLM([])                                     # any model call would fail: there are no scripted turns
    monkeypatch.setattr(main, "llm_client", silent)
    r = client.post("/chat", json={"message": "Compare them", "conversation_id": conv, "client_context": PAGE}).json()
    assert r["message"].startswith("Best per criterion: shortest: R3") and "lowest max slope: R2" in r["message"]
    assert r["metadata"]["model"] == "direct (no model call)" and not silent.sent
    r = client.post("/chat", json={"message": "Zoom into the delta region", "conversation_id": conv, "client_context": PAGE}).json()
    assert r["actions"][0]["summary"].startswith("Zoom to Delta front") and not silent.sent


def test_routes_are_never_called_safe(client, monkeypatch):
    monkeypatch.setattr(main, "llm_client", ScriptedLLM([ToolCall(name="generate_route", arguments={"destination": "stop 1"}),
                                                         "R1 is 1234 m and suitable for safe walking."]))
    r = client.post("/chat", json={"message": "Plan a route to stop 1", "client_context": PAGE}).json()
    assert "does not make a route safe" in r["message"]


def test_no_second_search_when_passages_were_retrieved(client, monkeypatch):
    llm = ScriptedLLM(["MOXIE made 122 g of oxygen; a crew-scale plant is still needed [DOC-1]."])
    monkeypatch.setattr(main, "llm_client", llm)
    client.post("/chat", json={"message": "Can astronauts make oxygen on Mars?"})
    offered = {t["function"]["name"] for t in llm.sent[0]["tools"]}
    assert "search_knowledge_base" not in offered and "generate_route" in offered


async def _two_searches():
    import tools
    ctx = tools.RunContext()
    tok = tools.set_run_context(ctx)
    try:
        a = await tools.tool_registry.execute("search_knowledge_base", {"query": "MOXIE oxygen"})
        b = await tools.tool_registry.execute("search_knowledge_base", {"query": "water ice SWIM"})
    finally:
        tools.reset_run_context(tok)
    return [x["cite"] for x in a.data["results"]], [x["cite"] for x in b.data["results"]]


def test_citation_numbers_are_unique_within_a_turn(client):
    import asyncio
    a, b = asyncio.run(_two_searches())
    assert a and b and not set(a) & set(b)


def test_route_requests_run_the_planner_directly(client, monkeypatch):
    silent = ScriptedLLM([])
    monkeypatch.setattr(main, "llm_client", silent)
    r = client.post("/chat", json={"message": "generate a route to delta front", "client_context": PAGE}).json()
    assert r["actions"][0]["type"] == "display_route" and "1234.0 m" in r["message"] and not silent.sent
    r = client.post("/chat", json={"message": "Give me three route options from the habitat to stop 3", "client_context": PAGE}).json()
    assert "Planned 3 route options" in r["message"] and "Recommended: R2" in r["message"]
    r = client.post("/chat", json={"message": "generate me a new route exploration site", "client_context": PAGE}).json()
    assert "Where should the route go?" in r["message"] and "Delta front" in r["message"] and not r["actions"]
