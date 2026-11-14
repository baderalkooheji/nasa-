"""Marswalk AI backend (FastAPI).

Run from marswalk-ai/:
    .venv\\Scripts\\python -m uvicorn marswalk_ai.main:app --app-dir backend --port 8090
Open http://127.0.0.1:8090/ for the standalone demo environment, /docs for the API.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .adapters import frontend as frontend_adapter
from .adapters import marswalk_http, science_engine, standalone
from .config import FRONTEND_DIR, Settings
from .context import MissionData, Services
from .contracts import (ActionResult, AssistantResponse, ChatRequest, CommandRequest, ConfirmRequest, LegacyChatRequest,
                        SessionCreate)
from .memory import SessionStore
from .orchestrator import Orchestrator
from .providers import create_provider
from .registry import CapabilityRegistry
from .services.knowledge import KnowledgeBase
from .services.routing import RoutePlanner
from .services.terrain import TerrainService

LEGACY_SESSION = "legacy-marswalk-drawer"


async def build_app_state(settings: Settings, http: httpx.AsyncClient | None = None, provider=None, provider_note=None):
    """Wire services, adapters and the orchestrator. Used by the app and by tests."""
    http = http or httpx.AsyncClient()
    mission = MissionData()
    services = Services(settings=settings, mission=mission, store=SessionStore(settings.session_db), http=http)
    services.terrain = TerrainService.from_settings(settings, mission.bounds)
    services.planner = RoutePlanner(services.terrain, settings.k_suit, settings.max_slope_deg) if services.terrain.sources else None
    services.knowledge = KnowledgeBase(settings.knowledge_dir)

    registry = CapabilityRegistry()
    standalone.register(registry, services)
    frontend_adapter.register(registry, services)

    eng = await science_engine.probe(services)
    if eng.get("reachable"):
        science_engine.register(registry, services)
    mw = await marswalk_http.probe(services)
    if mw.get("reachable"):
        marswalk_http.register(registry, services, mw["endpoints"])
        if "stops" in mw["endpoints"]:
            await marswalk_http.refresh_stops(services)

    services.status = {
        "terrain": {"data_mode": services.terrain.data_mode, "datasets": [d["id"] for d in services.terrain.describe()],
                    "load_errors": services.terrain.load_errors},
        "science_engine": {k: v for k, v in eng.items() if k != "health"},
        "marswalk_backend": mw,
        "knowledge": {"available": services.knowledge.available, "documents": len(services.knowledge.documents()),
                      "error": services.knowledge.error},
        "mission_data": mission.source,
    }
    if provider is None and provider_note is None:
        provider, provider_note = create_provider(settings, http)
    services.status["llm"] = provider_note
    return services, registry, Orchestrator(services, registry, provider, provider_note)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    http = httpx.AsyncClient()
    app.state.services, app.state.registry, app.state.orch = await build_app_state(settings, http)
    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(title="Marswalk AI - Mission Assistant API", version=__version__, lifespan=lifespan,
              description="Conversational mission assistant for the Marswalk Mission Planner. See MARSWALK_AI_INTEGRATION_GUIDE.md.")
_cors = Settings.from_env().cors_origins
app.add_middleware(CORSMiddleware, allow_origins=_cors, allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])


def _session(session_id: str):
    s = app.state.services.store.get(session_id)
    if s is None:
        raise HTTPException(404, detail={"error": {"code": "unknown_session", "message": "Session not found; create one first."}})
    return s


@app.get("/api/assistant/health")
async def health():
    st = app.state.services.status
    return {"status": "ok", "version": __version__, "integrations": st,
            "capabilities": {"available": sum(c.availability != "unavailable" for c in app.state.registry.all()),
                             "total": len(app.state.registry.all())}}


@app.post("/api/assistant/sessions")
async def create_session(body: SessionCreate):
    s = app.state.services.store.create(body.frontend.model_dump())
    usable = app.state.registry.usable(s)
    orch = app.state.orch
    return {"session_id": s.id, "provider": {"name": orch.provider.name if orch.provider else "offline-rules",
                                             "model": getattr(orch.provider, "model", None), "note": orch.provider_note},
            "capabilities": [c.id for c in usable], "integrations": app.state.services.status,
            "mission": {"name": app.state.services.mission.mission["name"], "current_position": app.state.services.mission.current_position()}}


@app.get("/api/assistant/mission")
async def mission():
    """Mission places for map display (demo environment)."""
    m = app.state.services.mission
    return {"name": m.mission["name"], "status": m.mission["status"], "bounds": m.bounds, "source": m.source,
            "current_position": m.current_position(), "pois": [{k: p.get(k) for k in ("id", "name", "kind", "lat", "lon", "reason", "provenance")}
                                                              for p in m.pois],
            "rover_latest": m.latest_rover()}


@app.get("/api/assistant/rover/track")
async def rover_track():
    """Perseverance end-of-drive localisations (historical snapshot) for map display."""
    r = app.state.services.mission.rover
    return {"source": r["source"], "credit": r["credit"], "note": r["note"],
            "points": [[w["lat"], w["lon"], w["sol"]] for w in r["waypoints"]]}


@app.get("/api/assistant/sessions/{session_id}")
async def get_session(session_id: str):
    s = _session(session_id)
    return {"session_id": s.id, "mission_state": s.mission_summary(app.state.services.mission.mission),
            "messages": s.recent_messages(40), "routes": {k: {kk: vv for kk, vv in v.items() if kk not in ("profile",)} for k, v in s.routes.items()},
            "analyses": s.analyses, "actions": s.actions}


@app.get("/api/assistant/capabilities")
async def capabilities(session_id: str | None = None):
    s = app.state.services.store.get(session_id) if session_id else None
    reg = app.state.registry
    usable = {c.id for c in reg.usable(s)} if s else None
    return {"capabilities": [c.describe(s) | ({"offered_to_llm": c.id in usable} if usable is not None else {}) for c in reg.all()]}


@app.post("/api/assistant/chat", response_model=AssistantResponse)
async def chat(body: ChatRequest):
    s = _session(body.session_id)
    return await app.state.orch.chat(s, body.message, body.client_context)


@app.post("/api/assistant/command", response_model=AssistantResponse)
async def command(body: CommandRequest):
    s = _session(body.session_id)
    if body.client_context:
        s.client_context = {**s.client_context, **body.client_context}
    return await app.state.orch.command(s, body.capability_id, body.arguments)


@app.post("/api/assistant/confirm", response_model=AssistantResponse)
async def confirm(body: ConfirmRequest):
    s = _session(body.session_id)
    return await app.state.orch.confirm(s, body.confirmation_id, body.approve)


@app.post("/api/assistant/actions/result")
async def action_result(body: ActionResult):
    s = _session(body.session_id)
    return app.state.orch.action_result(s, body.action_id, body.ok, body.detail, body.type)


@app.post("/api/chat")
async def legacy_chat(body: LegacyChatRequest):
    """Drop-in for the existing Marswalk drawer: {question, context} -> {answer, sources}. No map actions (the drawer can't run them)."""
    store = app.state.services.store
    s = store.get_or_create(LEGACY_SESSION, {"client": "marswalk-legacy-drawer", "capabilities": []})
    ctx = body.context or {}
    cc = {}
    sel = ctx.get("selected")
    if isinstance(sel, dict) and sel.get("lat") is not None:
        cc["selected"] = {"lat": sel.get("lat"), "lon": sel.get("lon"), "name": sel.get("name"),
                          "elevation_m": sel.get("elevation_m"), "slope_deg": sel.get("slope_deg"), "source_dataset": sel.get("source")}
    cc["legacy_context"] = {k: ctx.get(k) for k in ("route_a", "route_b", "eva", "max_slope_deg", "dem", "active_layers")}
    r = await app.state.orch.chat(s, body.question, cc)
    sources = [{"title": e.get("title"), "url": e.get("source_url")} for e in r["evidence"] if e.get("source_url")]
    answer = r["message"] + (("\n\nLimitations: " + " ".join(r["limitations"][:2])) if r["limitations"] else "")
    return {"answer": answer, "sources": sources}


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
