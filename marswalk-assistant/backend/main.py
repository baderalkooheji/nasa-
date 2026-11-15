"""
Marswalk AI Assistant - Main FastAPI Application

Local, JARVIS-style mission assistant for the Marswalk Mission Planner:
    Ollama (qwen3) → tool calls → real Marswalk data (NASA DEMs, routes, EVA, mission log) + local knowledge base (RAG)
    → explanation with labelled numbers and citations, plus map actions the Marswalk page executes and confirms.

Run:   cd marswalk-assistant/backend  &&  python main.py        (http://127.0.0.1:8095)
Needs: Ollama running with the model pulled (ollama pull qwen3:1.7b) and the Marswalk backend on port 8085.
"""
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import BACKEND_DIR, settings
from llm import LLMUnavailable, llm_client
from marswalk_client import MarswalkUnavailable, marswalk
from models import Conversation, Message, MissionContext, SessionLocal, get_db, init_db
from prompts import build_state_message, build_system_prompt
from rag import knowledge_base
from schemas import (ActionResult, ChatRequest, ChatResponse, ConversationCreate, ConversationResponse, DocumentIngestRequest,
                     DocumentResponse, HealthResponse, MessageResponse, MissionContextResponse, MissionContextUpdate, ModelInfo,
                     ModelSwitchRequest, SearchRequest, SearchResponse, SearchResult, ToolCallRequest, ToolDefinitionResponse,
                     ToolExecuteRequest, ToolExecuteResponse, ToolResultResponse)
from tools import RunContext, _places, init_tools, reset_run_context, set_run_context, tool_registry

logging.basicConfig(level=logging.DEBUG if settings.DEBUG else logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("marswalk.assistant")
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_tools()
    await knowledge_base.initialize()
    docs = await knowledge_base.ingest_directory()           # new or changed documents only
    logger.info("Knowledge base: %d documents (%s)", len(docs), knowledge_base.mode)
    models = await llm_client.list_models()
    if settings.DEFAULT_MODEL not in models:
        logger.warning("Model %s not installed in Ollama (found: %s). Run: ollama pull %s", settings.DEFAULT_MODEL, models,
                       settings.DEFAULT_MODEL)
    yield
    await marswalk.close()


app = FastAPI(title="Marswalk AI Assistant API", description="Local mission assistant for Marswalk (Ollama + tools + RAG)",
              version=settings.APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


# ============================================================================
# Health & models
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    models = await llm_client.list_models()
    db_ok = False
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass
    mw_ok = False
    try:
        await marswalk.health()
        mw_ok = True
    except MarswalkUnavailable:
        pass
    installed = llm_client.model in models
    return HealthResponse(
        status="healthy" if (installed and db_ok and mw_ok) else "degraded", version=settings.APP_VERSION,
        ollama_connected=bool(models) or installed, model=llm_client.model, model_installed=installed,
        knowledge_base_ready=knowledge_base._initialized, knowledge_documents=len(await knowledge_base.list_documents(500)),
        knowledge_mode=knowledge_base.mode, database_connected=db_ok, marswalk_backend=mw_ok, tools=len(tool_registry.list_tools()))


@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    return [ModelInfo(name=m) for m in await llm_client.list_models()]


@app.post("/models/switch", response_model=ModelInfo)
async def switch_model(request: ModelSwitchRequest):
    if not await llm_client.switch_model(request.model):
        raise HTTPException(status_code=400, detail=f"Model not installed in Ollama: {request.model} (ollama pull {request.model})")
    return ModelInfo(name=llm_client.model)


@app.post("/models/pull", response_model=ModelInfo)
async def pull_model(request: ModelSwitchRequest):
    if not await llm_client.pull_model(request.model):
        raise HTTPException(status_code=400, detail=f"Failed to pull model: {request.model}")
    return ModelInfo(name=request.model)


# ============================================================================
# Conversations
# ============================================================================

def _conv_response(c: Conversation) -> ConversationResponse:
    return ConversationResponse(id=c.id, session_id=c.session_id, title=c.title, created_at=c.created_at, updated_at=c.updated_at,
                                is_active=c.is_active, message_count=len(c.messages))


def _new_conversation(db: Session, session_id: Optional[str] = None, title: str = "New Mission Session") -> Conversation:
    conv = Conversation(session_id=session_id or uuid.uuid4().hex[:12], title=title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    db.add(MissionContext(conversation_id=conv.id))
    db.commit()
    return conv


@app.post("/conversations", response_model=ConversationResponse)
async def create_conversation(request: ConversationCreate, db: Session = Depends(get_db)):
    return _conv_response(_new_conversation(db, request.session_id, request.title or "New Mission Session"))


@app.get("/conversations", response_model=List[ConversationResponse])
async def list_conversations(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.query(Conversation).filter(Conversation.is_active.is_(True)).order_by(Conversation.updated_at.desc()).limit(limit).all()
    return [_conv_response(c) for c in rows]


@app.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _conv_response(conv)


@app.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_messages(conversation_id: int, limit: int = 50, db: Session = Depends(get_db)):
    if not db.get(Conversation, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at).limit(limit).all()
    return [MessageResponse(id=m.id, conversation_id=m.conversation_id, role=m.role, content=m.content, tool_calls=m.tool_calls,
                            tool_results=m.tool_results, metadata=m.meta, created_at=m.created_at) for m in rows]


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv.is_active = False
    db.commit()
    return {"success": True, "message": "Conversation archived"}


# ============================================================================
# Chat: model → tools → model (agent loop)
# ============================================================================

def _memory(ctx: Optional[MissionContext]) -> Dict[str, Any]:
    """Mission memory restored for this turn: routes generated earlier (full data), current route, last place."""
    if not ctx:
        return {"routes": {}, "current_route_id": None, "selected_area": None}
    routes = {r["route_id"]: r["data"] for r in (ctx.route_comparisons or []) if isinstance(r, dict) and "route_id" in r}
    return {"routes": routes, "current_route_id": (ctx.current_route or {}).get("route_id"), "selected_area": ctx.selected_area}


def _compact(data: Any, limit: int = 5000) -> str:
    s = json.dumps(data, default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…(truncated)"


def _fallback_answer(results: List[Dict[str, Any]]) -> str:
    """Used when the model returns no text after tools: report what the tools did, with their own numbers."""
    lines = []
    for r in results:
        if not r["success"]:
            lines.append(f"• {r['name']} failed: {r['error']}")
            continue
        d = r.get("data") or {}
        if r["name"] == "generate_route":
            for x in d.get("routes", []):
                lines.append(f"• {x['route_id']} {x['label']}: {x['distance_m']} m, max slope {x['max_slope_deg']}°, "
                             f"+{x['elevation_gain_m']} m (calculated from the DEM)")
            if d.get("recommended_route_id"):
                lines.append(f"• Recommended {d['recommended_route_id']}: {d.get('why_recommended')}. Sending it to the map.")
        elif r["name"] == "analyse_terrain" and d.get("data_available", True):
            lines.append(f"• {d.get('place')}: elevation {d.get('elevation_m')} m (DEM), slope P90 {d.get('slope_p90_deg')}°, "
                         f"difficulty {d.get('difficulty')}")
        elif r["name"] in ("search_knowledge_base", "retrieve_mission_information") and d.get("results"):
            for x in d["results"][:2]:
                lines.append(f"• [{x['cite']}] {x['text'][:240]}…")
        elif r["name"] == "estimate_eva":
            lines.append(f"• EVA {d.get('route_id')}: total {d.get('total_min')} min, status {d.get('status')} (estimate)")
        else:
            lines.append(f"• {r['name']}: done" + (f" ({d['map']['summary']}, sent to the map)" if isinstance(d.get("map"), dict) else ""))
    return "\n".join(lines) or "No result."


_ROUTES = [
    # (pattern, unless, tool, arguments) - unambiguous mission requests where a small model tends to answer from memory instead of
    # calling the tool. The tool runs first; the model then explains its real result (it can still call further tools).
    (r"\b(how long|how much time|duration|time budget|eva (time|budget|estimate)|suit time)\b",
     r"\b(day|year|sol|delay|signal|message|light|mission (last|lasted))\b", "estimate_eva", {}),
    (r"\b(where|location|position|locate|find|last seen)\b.*\b(perseverance|rover)\b|\b(perseverance|rover)\b.*\b(where|location|position|now|today)\b",
     r"\b(route|path) to\b", "retrieve_mission_information", {"topic": "perseverance_position"}),
    (r"\b(space weather|solar flare|flares|cme|solar particle|weather|environmental conditions|environment)\b",
     r"\b(why|explain|what is meda|instrument)\b", "analyse_environment", {}),
    (r"\bcompare\b.*\b(routes?|options?|them|those)\b|\b(which|what) (route|option) is (best|better|shorter|safer|faster)\b",
     r"\bgenerate|plan|give me|three|options to\b", "compare_routes", {}),
    (r"\b(mission log|log entries|what (have|did) (we|i) (save|saved|log)|saved (places|locations))\b",
     r"\b(save|store|add|record) (this|it|that|the)\b", "get_mission_log", {}),
]


def tools_allowed(request: ChatRequest) -> bool:
    return bool(request.use_tools and settings.ENABLE_TOOLS)


_LAYER_WORDS = r"(hirise|mola|themis|viking|ctx|hillshade|slope classes|slope layer|infrared|colour mosaic|color mosaic|elevation layer)"


def _command_routes(t: str) -> List[tuple]:
    """Clear UI / log commands with arguments taken from the request (the actions are confirmed by the page)."""
    import re
    out = []
    m = re.search(r"\b(show|hide|turn on|turn off|enable|disable|display|remove|add)\b.*?\b" + _LAYER_WORDS, t)
    if m:
        out.append(("change_map_layer", {"layer": m.group(2), "visible": m.group(1) not in ("hide", "turn off", "disable", "remove")}))
    if re.search(r"\b(save|store|bookmark|record|log)\b.*\b(this|location|point|place|spot|here|it|selection)\b", t) and \
            not re.search(r"\b(what|show|open|read|list)\b.*\blog\b", t):
        out.append(("save_to_mission_log", {"location": "selected"}))
    m = re.search(r"\b(zoom|fly|go|take me|move the map|centre|center)\b(?: in)?(?: the map)? (?:to|on|into|over)\s+(?:the\s+)?(.+?)[.?!]*$", t)
    if m and not re.search(r"\b(route|path) (from|to)\b|\bplan\b", t):
        out.append(("zoom_map", {"location": m.group(2).strip()}))
    if re.search(r"\b(3d|globe)\b", t) and re.search(r"\b(open|switch|show|go|change|view)\b", t):
        out.append(("navigate_application", {"target": "3d"}))
    elif re.search(r"\b(2d|flat map)\b", t) and re.search(r"\b(open|switch|show|go|back|change|view)\b", t):
        out.append(("navigate_application", {"target": "2d"}))
    if re.search(r"\b(exit|leave|stop|close)\b.*\bpresentation\b", t):
        out.append(("navigate_application", {"target": "exit_presentation"}))
    elif re.search(r"\bpresentation\b|\bfull ?screen\b", t):
        out.append(("navigate_application", {"target": "presentation"}))
    if re.search(r"\b(open|show)\b.*\b(mission log|log)\b", t):
        out.append(("navigate_application", {"target": "mission_log"}))
    m = re.search(r"\b(start|begin|play|run)\b.*\bjourney\b", t)
    if m:
        out.append(("start_route_journey", {"route": "A" if re.search(r"\broute a\b|hand", t) else "B"}))
    return out


async def _preroute(message: str) -> List[tuple]:
    import re
    t = message.lower().strip()
    found = [(tool, dict(args)) for pat, unless, tool, args in _ROUTES if re.search(pat, t) and not re.search(unless, t)]
    found += _command_routes(t)
    if any(n == "save_to_mission_log" for n, _ in found):          # "save this … to the log" is not a request to read the log
        found = [f for f in found if f[0] != "get_mission_log"]
    return found


# an action verb close to an app object ("sending … to the map", "saved to the log", "switched to the 3D view")
# Requests answered directly from the tool result, without the model: their results need no interpretation, and a small model
# only risks garbling them (comparisons) while adding 20-80 s on a CPU.
DIRECT = {"change_map_layer", "save_to_mission_log", "zoom_map", "navigate_application", "start_route_journey", "compare_routes",
          "get_mission_log", "retrieve_mission_information", "estimate_eva", "analyse_environment"}


def _direct_answer(executed: List[Dict[str, Any]]) -> str:
    lines = []
    for e in executed:
        d = e.get("data") or {}
        if not e["success"]:
            lines.append(f"Could not do that: {e['error']}")
        elif e["name"] == "retrieve_mission_information" and d.get("sol") is not None:
            lines.append(f"Perseverance's latest localisation in the NASA file is sol {d['sol']}: {d['lat']:.5f}°N {d['lon']:.5f}°E, "
                         f"elevation {round(d['elev_geoid_m'], 1) if d.get('elev_geoid_m') is not None else 'n/a'} m (areoid), after "
                         f"{d.get('dist_total_km')} km of driving. This is a historical position (NASA/JPL MMGIS), not live. "
                         "Sending a marker to the map.")
        elif e["name"] == "estimate_eva":
            n = d.get("science_stops")
            lines.append(f"EVA estimate for route {d.get('route_id')}: walk out {d.get('walk_out_min')} min, science {d.get('science_min')} min "
                         f"({n} stop{'s' if n != 1 else ''}), walk back {d.get('walk_back_min')} min, total {d.get('total_min')} min of a "
                         f"{d.get('suit_limit_min')}-min suit budget. Reserve {d.get('reserve_min')} min (target {d.get('reserve_target_min')} "
                         f"min): status {str(d.get('status')).upper()}.")
            lines.append(f"Reference radiation dose for this EVA: {d.get('radiation_dose_for_this_eva_mgy')} mGy (Curiosity RAD value "
                         "measured at Gale crater, scaled to the EVA time; not a Jezero measurement).")
            lines.append(f"Estimates from the {d.get('model')}.")
        elif e["name"] == "analyse_environment":
            sw = d.get("space_weather") or {}
            if sw.get("status") and sw.get("status") != "unknown":
                lines.append(f"Space weather (NASA DONKI): {sw['status']}; flares {len(sw.get('flares') or [])}, SEP events "
                             f"{len(sw.get('sep_events') or [])} in the last 7 days. {sw.get('rule', '')}")
            else:
                lines.append(f"Space weather: not available ({sw.get('source') or sw.get('reason') or 'NASA DONKI not reachable'}).")
            mw = d.get("meda_weather") or {}
            lines.append("MEDA weather: " + ("not available (" + str(mw.get("reason")) + ")" if mw.get("available") is False else json.dumps(mw)[:300]))
            rr = d.get("radiation_reference") or {}
            lines.append(f"Radiation reference: {rr.get('value')} measured at {rr.get('measured_at')}; {rr.get('data_type')}.")
        elif e["name"] == "compare_routes":
            lines.append(d["summary"].replace("Best per criterion (computed, use as written) - ", "Best per criterion: "))
            lines += [f"• {row}" for row in d.get("table", [])]
            lines.append("Distances and slopes are calculated from the DEM; walking times are model estimates.")
        elif e["name"] == "get_mission_log":
            if not d.get("entries"):
                lines.append("The mission log is empty.")
            else:
                lines.append(f"The mission log has {d['count']} entr{'y' if d['count'] == 1 else 'ies'}; latest:")
                lines += [f"• {x['label']} ({x['analysis_type']}): {x['result']} · {x['created_at']}" for x in d["entries"][:6]]
        elif e["name"] == "save_to_mission_log":
            s = d.get("saved") or {}
            lines.append(f"Saved “{s.get('label')}” to the mission log: {s.get('result')} (elevation measured on the DEM, slope calculated).")
        else:
            m = d.get("map") or {}
            lines.append(f"Sending to the page: {m.get('summary', e['name'])}. The panel confirms when it is done.")
    return "\n".join(lines)


_CLAIMS = (r"\b(send|sent|zoom|switch|open|sav|show|display|enabl|disabl|highlight|start|mov|turn)\w*\b[^.]{0,50}"
           r"\b(map|layer|globe|3d|2d|view|log|panel|tab|journey|marker|presentation)\b")


def _check_safety_language(text_: str, executed: List[Dict[str, Any]]) -> str:
    """Routes are never 'safe' or 'guaranteed' (scientific rule 3); small models sometimes say so anyway."""
    import re
    route_turn = any(e["name"] in ("generate_route", "compare_routes", "estimate_eva", "analyse_terrain") for e in executed)
    if route_turn and re.search(r"\b(safe|safely|guarantee[sd]?|risk-free|no risk)\b", text_, re.I) and \
            not re.search(r"\bnot (guaranteed )?safe\b", text_, re.I):
        return (text_ + "\n(Note: meeting the slope limit does not make a route safe or guaranteed; the elevation model does not "
                "resolve boulders or small ledges, and the limits are project-defined.)")
    return text_


def _check_action_claims(text_: str, executed: List[Dict[str, Any]], actions: List[Dict[str, Any]]) -> str:
    """If the reply talks as if it acted on the map/app/log but no such tool ran, say so (no pretended actions)."""
    import re
    acted = actions or any(e["name"] in ("save_to_mission_log",) and e.get("success") for e in executed)
    if not acted and re.search(_CLAIMS, text_.lower()):
        return text_ + "\n(Note: no map or app action was carried out for this request.)"
    return text_


def _check_citations(text_: str, executed: List[Dict[str, Any]]) -> str:
    """Remove [DOC-n] markers that do not correspond to a passage actually retrieved this turn (no invented citations)."""
    import re
    valid = {x["cite"] for r in executed if r["name"] in ("search_knowledge_base", "retrieve_mission_information") and r.get("success")
             for x in ((r.get("data") or {}).get("results") or [])}
    cleaned = re.sub(r"\s*\[(DOC-\d+)\]", lambda m: m.group(0) if m.group(1) in valid else "", text_)
    if cleaned != text_ and not valid:
        cleaned += "\n(Note: no knowledge-base document supports this answer; it is not from a cited source.)"
    return cleaned


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    t0 = time.perf_counter()
    if request.conversation_id:
        conv = db.get(Conversation, request.conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    elif request.session_id:
        conv = db.query(Conversation).filter(Conversation.session_id == request.session_id).first() or _new_conversation(db, request.session_id)
    else:
        conv = _new_conversation(db)
    mctx = db.query(MissionContext).filter(MissionContext.conversation_id == conv.id).first()
    mem = _memory(mctx)
    page = request.client_context or {}

    history = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at.desc()) \
        .limit(settings.MAX_HISTORY_MESSAGES).all()
    db.add(Message(conversation_id=conv.id, role="user", content=request.message))
    db.commit()

    run = RunContext(page=page, routes=dict(mem["routes"]), current_route_id=mem["current_route_id"], selected_area=mem["selected_area"])
    token = set_run_context(run)
    executed: List[Dict[str, Any]] = []
    calls: List[ToolCallRequest] = []
    final, model_name, usage, error, rounds = "", llm_client.model, {"prompt_tokens": 0, "completion_tokens": 0}, None, 0
    direct = False
    try:
        try:
            places = await _places()
        except MarswalkUnavailable:
            places = []
        mem_summary = {"routes": {k: {kk: v.get(kk) for kk in ("label", "distance_m", "max_slope_deg")} for k, v in run.routes.items()},
                       "current_route_id": run.current_route_id, "selected_area": run.selected_area}
        # Prompt layout for speed on a CPU: [fixed system prompt + tool definitions] [history] [this turn]. The fixed prefix is
        # identical every turn, so Ollama reuses its cached computation; everything that changes (page state, retrieved
        # passages) goes into THIS turn's user message. (Qwen's template merges all system messages into the top block,
        # so they must not be sent as extra system messages.)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": build_system_prompt(places)}]
        # history without the server's own "(Note: …)" corrections, so the model does not learn to copy them
        messages += [{"role": m.role, "content": re.sub(r"\n?\(Note: [^)]*\)", "", m.content).strip()}
                     for m in reversed(history) if m.role in ("user", "assistant") and m.content]
        context_parts = [build_state_message(page, mem_summary)]

        # Mission requests with an unambiguous tool: run it first and give the model the real result to explain.
        pre = await _preroute(request.message) if tools_allowed(request) else []
        for name, args in pre:
            res = await tool_registry.execute(name, args)
            calls.append(ToolCallRequest(name=name, arguments=args, id=f"auto_{name}"))
            executed.append({"name": name, "arguments": args, **res.to_dict()})
        if pre:
            context_parts.append("Tool results already obtained for this request (real data; explain them, keep their labels, and do not "
                                 "change or add numbers; a 'map' entry means the action was SENT to the map, not yet confirmed):\n"
                                 + "\n".join(f"{e['name']}: " + _compact({"success": e["success"], "data": e["data"], "error": e["error"]}, 3000)
                                             for e in executed))

        # Retrieval BEFORE the model answers (classic RAG): small local models do not reliably decide to search, and then
        # answer from memory. Relevant passages (the retriever's relevance gate decides) are put in front of the question.
        # Not for pre-routed mission requests: documents there only distract from the tool result.
        kb = await tool_registry.execute("search_knowledge_base", {"query": request.message[:300], "top_k": 3}) if not pre else None
        passages = (kb.data or {}).get("results") if kb and kb.success else None
        if passages:
            calls.append(ToolCallRequest(name="search_knowledge_base", arguments={"query": request.message[:300], "auto": True}, id="auto_rag"))
            executed.append({"name": "search_knowledge_base", "arguments": {"query": request.message[:300], "auto": True}, **kb.to_dict()})
            context_parts.append("Knowledge base passages retrieved for this question. If I ask you to DO something (plan or compare "
                                 "routes, analyse terrain, estimate an EVA, show, zoom, change layers, save, start), call the tools instead. "
                                 "If I ask for information and these passages answer it, answer ONLY from them in simple language, cite them "
                                 "as [DOC-n] and keep their uncertainty; add no facts that are not in them.\n"
                                 + "\n".join(f"[{p['cite']}] {p['title']} — {p.get('section') or ''}: {p['text']}" for p in passages))
        if run.actions:      # only when an action is actually queued: as a standing line it primes small models to talk about the map
            context_parts.append("Map/app actions above were only SENT to the page; describe them as being sent, not as done.")
        messages.append({"role": "user", "content": "\n\n".join(context_parts) + "\n\nMy request: " + request.message})
        tools = tool_registry.get_tools_for_ollama() if tools_allowed(request) else None
        if tools and passages:
            # the passages are already in front of the model: a second search only replaces good evidence with worse
            tools = [t for t in tools if t["function"]["name"] not in ("search_knowledge_base", "retrieve_mission_information")]
        temperature = request.temperature if request.temperature is not None else 0.3
        max_tokens = min(request.max_tokens or 700, 1500)

        direct = bool(pre) and all(n in DIRECT for n, _ in pre)
        if direct:
            final = _direct_answer(executed)
        while not direct:
            resp = await llm_client.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
            model_name = resp.model
            usage = {k: usage[k] + (resp.usage or {}).get(k, 0) for k in usage}
            if not resp.tool_calls:
                final = resp.content
                break
            rounds += 1
            messages.append(resp.raw_message)
            for tc in resp.tool_calls:
                calls.append(ToolCallRequest(name=tc.name, arguments=tc.arguments, id=tc.id))
                res = await tool_registry.execute(tc.name, tc.arguments)
                executed.append({"name": tc.name, "arguments": tc.arguments, **res.to_dict()})
                messages.append({"role": "tool", "tool_name": tc.name,
                                 "content": _compact({"success": res.success, "data": res.data, "error": res.error})})
            if rounds >= settings.MAX_TOOL_ROUNDS:              # stop calling tools: explain what we have
                resp = await llm_client.chat(messages, tools=None, temperature=temperature, max_tokens=max_tokens)
                final = resp.content
                break
        if not final.strip():
            final = _fallback_answer(executed) if executed else "I have no answer for that."
        final = _check_citations(final, executed)
        final = _check_action_claims(final, executed, run.actions)
        final = _check_safety_language(final, executed)
    except LLMUnavailable as e:
        error = "llm_unavailable"
        final = (f"The local AI model is not available: {e}\n"
                 + ("Tool results so far:\n" + _fallback_answer(executed) if executed else
                    "Start Ollama (it runs in the Windows tray) and make sure the model is installed."))
    finally:
        reset_run_context(token)

    tool_results = [ToolResultResponse(success=r["success"], data=r["data"], error=r["error"], metadata=r["metadata"]) for r in executed]
    # history keeps a light record of each tool call (full route data lives in mission memory)
    db.add(Message(conversation_id=conv.id, role="assistant", content=final, tool_calls=[c.model_dump() for c in calls],
                   tool_results=[{"name": r["name"], "success": r["success"], "error": r["error"]} for r in executed],
                   meta={"model": model_name, "actions": [a["action_id"] for a in run.actions], "error": error}))

    # ---- mission memory
    if mctx:
        if run.routes:
            mctx.route_comparisons = [{"route_id": k, "data": v} for k, v in list(run.routes.items())[-6:]]
        if run.current_route_id and run.current_route_id in run.routes:
            r = run.routes[run.current_route_id]
            mctx.current_route = {"route_id": run.current_route_id, **{k: r.get(k) for k in (
                "label", "distance_m", "max_slope_deg", "elevation_gain_m", "data_coverage_pct", "over_limit", "_from", "_to")}}
        if run.selected_area:
            mctx.selected_area = run.selected_area
        if page.get("selected"):
            mctx.selected_point = page["selected"]
        if page.get("view"):
            mctx.map_view = page["view"]
        if page.get("visible_layers") is not None:
            mctx.selected_layers = page["visible_layers"]
        if run.analyses:
            mctx.scientific_analyses = ((mctx.scientific_analyses or []) + [{**a, "at": datetime.utcnow().isoformat(timespec="seconds")}
                                                                            for a in run.analyses])[-20:]
    conv.updated_at = datetime.utcnow()
    if conv.title in ("New Conversation", "New Mission Session"):
        conv.title = request.message[:60]
    db.commit()

    return ChatResponse(message=final, conversation_id=conv.id, session_id=conv.session_id, tool_calls=calls, tool_results=tool_results,
                        actions=run.actions, metadata={"model": "direct (no model call)" if direct and not error else model_name,
                                                       "usage": usage, "tool_rounds": rounds,
                                                       "timing_ms": round((time.perf_counter() - t0) * 1000), "error": error})


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, db: Session = Depends(get_db)):
    """Kept for compatibility with the first version: returns the full response."""
    return await chat(request, db)


@app.post("/actions/result")
async def action_result(body: ActionResult, db: Session = Depends(get_db)):
    """The page reports the outcome of a frontend action; stored with the assistant message that requested it."""
    msg = db.query(Message).filter(Message.conversation_id == body.conversation_id, Message.role == "assistant") \
        .order_by(Message.created_at.desc()).limit(5).all()
    for m in msg:
        meta = dict(m.meta or {})
        if body.action_id in (meta.get("actions") or []):
            meta.setdefault("action_results", {})[body.action_id] = {"ok": body.ok, "detail": body.detail, "type": body.type}
            m.meta = meta
            db.commit()
            return {"stored": True}
    return {"stored": False}


# ============================================================================
# Mission context
# ============================================================================

def _ctx_response(ctx: MissionContext) -> MissionContextResponse:
    return MissionContextResponse(
        current_mission=ctx.current_mission or "EVA-01 · Jezero delta", selected_area=ctx.selected_area, current_route=ctx.current_route,
        route_comparisons=[{"route_id": r.get("route_id"), **{k: (r.get("data") or {}).get(k) for k in (
            "label", "distance_m", "max_slope_deg", "elevation_gain_m", "over_limit")}} for r in (ctx.route_comparisons or [])],
        selected_layers=ctx.selected_layers, scientific_analyses=ctx.scientific_analyses, mission_objectives=ctx.mission_objectives,
        map_view=ctx.map_view or "2d", map_center=ctx.map_center, selected_point=ctx.selected_point)


@app.get("/conversations/{conversation_id}/context", response_model=MissionContextResponse)
async def get_mission_context(conversation_id: int, db: Session = Depends(get_db)):
    ctx = db.query(MissionContext).filter(MissionContext.conversation_id == conversation_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Mission context not found")
    return _ctx_response(ctx)


@app.patch("/conversations/{conversation_id}/context", response_model=MissionContextResponse)
async def update_mission_context(conversation_id: int, updates: MissionContextUpdate, db: Session = Depends(get_db)):
    ctx = db.query(MissionContext).filter(MissionContext.conversation_id == conversation_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Mission context not found")
    for k, v in updates.model_dump(exclude_unset=True).items():
        setattr(ctx, k, v)
    db.commit()
    db.refresh(ctx)
    return _ctx_response(ctx)


# ============================================================================
# Knowledge base
# ============================================================================

def _doc_response(d) -> DocumentResponse:
    return DocumentResponse(id=d.id, filename=d.filename, filepath=d.filepath, title=d.title, source_type=d.source_type,
                            chunk_count=d.chunk_count, is_processed=d.is_processed, created_at=d.created_at)


@app.post("/knowledge/ingest", response_model=DocumentResponse)
async def ingest_document(request: DocumentIngestRequest):
    try:
        return _doc_response(await knowledge_base.ingest_document(request.filepath, request.title, request.source_type, request.metadata))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/knowledge/ingest-directory", response_model=List[DocumentResponse])
async def ingest_directory(directory: Optional[str] = None):
    return [_doc_response(d) for d in await knowledge_base.ingest_directory(directory)]


@app.get("/knowledge/documents", response_model=List[DocumentResponse])
async def list_documents(limit: int = 100):
    return [_doc_response(d) for d in await knowledge_base.list_documents(limit)]


@app.delete("/knowledge/documents/{document_id}")
async def delete_document(document_id: int):
    if not await knowledge_base.delete_document(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True, "message": "Document deleted"}


@app.post("/knowledge/search", response_model=SearchResponse)
async def search_knowledge(request: SearchRequest):
    results = await knowledge_base.search(request.query, request.top_k, request.filter)
    return SearchResponse(query=request.query, results=[SearchResult(**r) for r in results])


# ============================================================================
# Tools
# ============================================================================

@app.get("/tools", response_model=List[ToolDefinitionResponse])
async def list_tools():
    return [ToolDefinitionResponse(name=t.name, description=t.description, parameters=t.to_json_schema()["parameters"], returns=t.returns,
                                   requires_permission=t.requires_permission, category=t.category) for t in tool_registry.list_tools()]


@app.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(request: ToolExecuteRequest):
    """Run one tool directly (testing). Frontend actions it queues are returned in metadata.actions."""
    run = RunContext()
    token = set_run_context(run)
    try:
        result = await tool_registry.execute(request.name, request.arguments)
    finally:
        reset_run_context(token)
    return ToolExecuteResponse(success=result.success, data=result.data, error=result.error,
                               metadata={**result.metadata, "actions": run.actions})


# ============================================================================
# Frontends: standalone mission-control page (/app) and the embeddable panel for the Marswalk page (/embed)
# ============================================================================

app.mount("/embed", StaticFiles(directory=str(FRONTEND_DIR / "embed")), name="embed")
app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="app")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/app/")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=False)
