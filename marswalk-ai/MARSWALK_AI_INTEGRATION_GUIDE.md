# Marswalk AI: Integration Guide

How to run the assistant, call its API, embed the panel in a page, implement frontend actions, and connect it to the original Marswalk application and the Scientific Intelligence Engine.

Contents: [1 Run](#1-run) · [2 HTTP API](#2-http-api) · [3 Response contract](#3-response-contract) · [4 Frontend action contract](#4-frontend-action-contract) · [5 Embedding the panel](#5-embedding-the-panel) · [6 Connecting the original Marswalk app](#6-connecting-the-original-marswalk-app) · [7 Connecting the Science Engine](#7-connecting-the-scientific-intelligence-engine) · [8 Adding a capability](#8-adding-a-capability) · [9 Phase 5 checklist](#9-phase-5-checklist) · [10 Configuration reference](#10-configuration-reference)

---

## 1. Run

```bash
.venv\Scripts\python -m uvicorn marswalk_ai.main:app --app-dir backend --port 8090
```

* `GET /api/assistant/health` shows which integrations are live (terrain datasets, Science Engine, Marswalk backend, documents, LLM).
* `/docs` is the OpenAPI UI for the assistant's own API.
* The process serves the demo frontend at `/`. CORS is open (`CORS_ORIGINS=*`) so another origin, such as the original Marswalk page, can call it.

## 2. HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/assistant/health` | Status, integrations, capability counts |
| POST | `/api/assistant/sessions` | Create a session; the UI's **handshake** declares which frontend actions, sections and layers it supports |
| GET | `/api/assistant/sessions/{id}` | Mission state, recent messages, routes, analyses, action outcomes (for dashboards and debugging) |
| GET | `/api/assistant/capabilities?session_id=` | Registry listing with per-session schemas and `offered_to_llm` |
| POST | `/api/assistant/chat` | One user message → `AssistantResponse` |
| POST | `/api/assistant/command` | Run one capability directly (card buttons); still validated by the registry, no LLM involved |
| POST | `/api/assistant/confirm` | Approve or cancel a `pending_confirmation` (one-shot) |
| POST | `/api/assistant/actions/result` | Frontend acknowledgement of an executed action `{action_id, ok, detail}` |
| GET | `/api/assistant/mission` | Mission places and the latest rover position (demo map) |
| GET | `/api/assistant/rover/track` | Perseverance localisations for display |
| POST | `/api/chat` | **Legacy Marswalk contract** `{question, context}` → `{answer, sources}` (no map actions) |

Errors use FastAPI conventions: 422 for schema errors, 404 `{"detail": {"error": {"code": "unknown_session", ...}}}`. The original Marswalk `api()` helper already parses `detail.error.message`.

### 2.1 Session handshake

```http
POST /api/assistant/sessions
{"frontend": {
  "client": "marswalk-2d",
  "capabilities": ["map.display_route", "map.highlight_location", "map.zoom_to_location",
                   "map.change_layer", "map.clear_route", "navigation.open_section"],
  "sections": [{"id": "route", "label": "Route planner tab", "keywords": ["route planner"]}],
  "layers":   [{"id": "mola", "label": "MOLA shaded elevation (463 m/px)", "keywords": ["terrain map", "elevation map"]}]
}}
→ {"session_id": "ses-…", "provider": {"name": "offline-rules", "model": null, "note": "…"},
   "capabilities": ["mission.get_context", "route.generate", …, "map.display_route", …],
   "integrations": {…}, "mission": {"name": "EVA-01 · Jezero delta", "current_position": {…}}}
```

Only the frontend capabilities listed here are offered to the assistant for this session. The `layer_id` and `section_id` enums in their schemas are built from `layers` and `sections`. **Section ids are never matched as words.** Give each section a descriptive `label` and `keywords`.

### 2.2 Chat

```http
POST /api/assistant/chat
{"session_id": "ses-…", "message": "Show me a route to the delta",
 "client_context": {
   "section": "map",
   "visible_layers": ["viking", "ctx", "hirise"],
   "map": {"center": {"lat": 18.4545, "lon": 77.404}, "zoom": 15},
   "selected": {"lat": 18.4555, "lon": 77.4035, "name": "Selected point"},
   "displayed_routes": []
 }}
```

`client_context` is optional but recommended. Without `selected`, "analyze this location" falls back to the map centre or the current position, and the reply says so. Coordinates in `client_context` count as grounded, so map tools may use them.

## 3. Response contract

Abridged from a real response (offline mode, real HiRISE DTM):

```json
{
  "response_id": "rsp-334c86f36c",
  "session_id": "ses-561a40296929",
  "message": "Computed 3 candidate routes from Habitat · Three Forks area … to Delta front · Hawksbill Gap on Mars 2020 TRN HiRISE DTM mosaic (1 m) (slope limit 15°, project-defined).\n• R1 …\n• R2 \"Gentlest slopes\": 634 m, max slope 14.8°, mean 3.7°, about 13 min walking out  ← recommended\n…\nSending R2 to the map.",
  "intent": {"name": "route_find", "confidence": 0.85, "source": "rules"},
  "status": "success",
  "tool_calls": [
    {"call_id": "call-…", "capability_id": "mission.get_context", "arguments": {}, "origin": "rules"},
    {"call_id": "call-…", "capability_id": "route.generate", "arguments": {"destination": "stop-1", "start": "current_position", "mode": "candidates"}, "origin": "rules"},
    {"call_id": "call-…", "capability_id": "map.display_route", "arguments": {"route_id": "R2", "emphasis": "primary", "fit": true}, "origin": "rules"}
  ],
  "tool_results": [
    {"call_id": "call-…", "capability_id": "route.generate", "status": "success", "ok": true,
     "data": {"route_set_id": "S1",
              "routes": [{"route_id": "R1", "label": "Balanced: lowest terrain cost", "distance_m": 565.0, "max_slope_deg": 14.8,
                          "avg_slope_deg": 4.4, "weighted_cost": 1.132, "walk_time_out_min": 12.3, "within_constraints": true,
                          "steepest_point": {"lat": 18.458866, "lon": 77.406229, "slope_deg": 14.8, "at_m": 561.0}, "data_mode": "real"}],
              "recommended_route_id": "R2",
              "recommendation_rationale": ["lowest terrain cost among routes that meet the slope limit (weighted cost 1.087)", "…"],
              "dataset": {"id": "hirise-dtm", "resolution_m": 2.72, "native_resolution_m": 1.0, "data_mode": "real", "source": "USGS Astrogeology, JEZ_hirise_soc_006_DTM…"},
              "notes": ["The destination cell itself is steeper than 15° (reported separately; …)"]},
     "error": null,
     "provenance": {"provider": "standalone", "data_mode": "real", "algorithm": "Dijkstra least-cost path …"},
     "warnings": [], "duration_ms": 511, "origin": "rules"}
  ],
  "frontend_actions": [
    {"action_id": "act-4c4ba3340c", "type": "display_route", "capability_id": "map.display_route",
     "payload": {"route_id": "R2", "label": "Gentlest slopes", "geometry": [[18.45134, 77.401237], "…"], "slope_classes": ["ok", "…"],
                 "emphasis": "primary", "fit": true, "recommended": true, "data_mode": "real",
                 "summary": {"distance_m": 634.0, "max_slope_deg": 14.8, "walk_time_out_min": 13.4}},
     "summary": "Display R2 (Gentlest slopes)", "status": "pending", "requires_ack": true}
  ],
  "cards": [{"type": "route_set", "title": "Routes to Delta front · Hawksbill Gap", "data": {"…": "…"},
             "actions": [{"kind": "command", "label": "Show recommended route", "capability_id": "map.display_route", "arguments": {"route_id": "R2"}, "style": "primary"},
                         {"kind": "prompt", "label": "Compare alternatives", "text": "Compare the candidate routes"}],
             "provenance": {"provider": "standalone", "data_mode": "real"}}],
  "evidence": [],
  "limitations": ["Routes come from the standalone route service on Mars 2020 TRN HiRISE DTM mosaic (1 m) resampled to 2.72 m; …", "Slope thresholds are project-defined …", "…"],
  "errors": [],
  "pending_confirmations": [],
  "suggestions": ["Explain why you recommended this route", "Show me the steepest section", "…"],
  "provider": {"mode": "rules", "name": "offline-rules", "model": null, "note": "…"},
  "data_modes": ["mixed", "real"],
  "simulated": false,
  "mock_components": ["mission status and astronaut position are mock values", "routes from the standalone route service (not the Marswalk route engine)"],
  "timing_ms": 607
}
```

| Field | Meaning |
| --- | --- |
| `status` | `success`, `partial` (some tool failed or data missing), `needs_confirmation`, `needs_clarification`, `error` |
| `intent.source` | `llm`, `rules` (offline), `button` (command / confirm) |
| `provider.mode` | `llm`, `rules`, `rules-fallback` (LLM failed; reply prefixed accordingly), `command` |
| `tool_results[].status` | `success`, `pending_ack` (frontend action sent), `pending_confirmation`, `error`, `rejected` (unknown tool / invalid args, **not executed**), `unavailable` |
| `cards[].actions[].kind` | `command` → `POST /api/assistant/command` with `capability_id` + `arguments`; `prompt` → send `text` as a chat message |
| `simulated` | `true` if any result used SIMULATED terrain; the UI must show it |
| `limitations` | Always display them (collapsed is fine); they carry data provenance and model caveats |

## 4. Frontend action contract

Six action types. The page must validate the payload, execute it, then report the outcome. The assistant treats an action as done **only** after `ok: true` comes back.

| `type` | capability | payload |
| --- | --- | --- |
| `display_route` | `map.display_route` | `route_id`, `label`, `geometry: [[lat, lon], …]` (≥ 2), `slope_classes: ["ok"\|"caution"\|"over"\|"nodata", …]` (one per segment), `emphasis: "primary"\|"alternative"`, `fit: bool`, `recommended`, `data_mode`, `summary` |
| `highlight_location` | `map.highlight_location` | `lat`, `lon`, `label`, `kind`, `style: "info"\|"caution"\|"destination"\|"rover"` |
| `zoom_to_location` | `map.zoom_to_location` | `lat`, `lon`, `zoom`, `label` **or** `bounds: [[s, w], [n, e]]`, `label` |
| `change_layer` | `map.change_layer` | `layer_id` (from the handshake), `visible` (default true), `opacity?` 0–1 |
| `clear_route` | `map.clear_route` | `route_id?` (omitted = all assistant routes) |
| `navigate_to_section` | `navigation.open_section` | `section_id` (from the handshake) |

Coordinates are planetocentric latitude and **east-positive** longitude (−180…180), the same as Marswalk, Mars Trek and MMGIS. Leaflet `L.CRS.EPSG4326` uses them directly.

Acknowledge each action:

```http
POST /api/assistant/actions/result
{"session_id": "ses-…", "action_id": "act-4c4ba3340c", "ok": true, "detail": "R2 drawn (213 points)", "type": "display_route"}
```

`frontend/js/action-bridge.js` implements validation and dispatch, so a host only writes the handlers:

```js
const bridge = new MarswalkActionBridge({
  client: "my-page",
  sections: () => [{ id: "dashboard", label: "Mission dashboard", keywords: ["dashboard"] }],
  layers: () => [{ id: "mola", label: "MOLA shaded elevation", keywords: ["terrain map"] }],
  getContext: () => ({ section: "map", selected: null, map: { center: { lat: 18.45, lon: 77.40 }, zoom: 15 } }),
  handlers: {
    display_route: async (p) => { /* draw p.geometry */ return "drawn"; },   // throw an Error to report failure
    navigate_to_section: async (p) => { /* open p.section_id */ },
  },
});
```

Only the action types that have handlers are advertised.

## 5. Embedding the panel

```html
<link rel="stylesheet" href="http://127.0.0.1:8090/css/assistant.css">
<script src="http://127.0.0.1:8090/js/action-bridge.js"></script>
<script src="http://127.0.0.1:8090/js/assistant.js"></script>
<script>
  const assistant = MarswalkAssistant.mount({ apiBase: "http://127.0.0.1:8090", bridge });
  // assistant.toggle(true) opens it; assistant.ask("…") sends a message programmatically
</script>
```

All classes are prefixed `mwai-`, the panel is `position: fixed` bottom-right, and it adds no global styles.

## 6. Connecting the original Marswalk app

### 6.1 Frontend (works today)

`frontend/integration/marswalk-bridge.js` implements the six handlers with the original code:

| Action | Original code used |
| --- | --- |
| display / clear route | own `L.layerGroup` on `window.map` (Route A/B untouched) |
| highlight | `L.circleMarker` on `window.map` |
| zoom | `map.fitBounds` / `map.setView`; `Mars3D.goToLocation` when the 3D view is open |
| change_layer | `nasaLayers[id]` / `overlays[id]` + checkbox sync in `#basemaps` / `#layers` |
| navigate | `.tab[data-tab=layers\|route\|env\|records]` click; `window.setView("2d"\|"3d")` |
| context | `chatContext()`, `AppState.selected`, active tab, visible NASA layers, map centre/zoom |

Add after `<script type="module" src="JS/integration.js"></script>` in the original `index.html`:

```html
<link rel="stylesheet" href="http://127.0.0.1:8090/css/assistant.css">
<script src="http://127.0.0.1:8090/js/action-bridge.js"></script>
<script src="http://127.0.0.1:8090/js/assistant.js"></script>
<script src="http://127.0.0.1:8090/integration/marswalk-bridge.js" data-api="http://127.0.0.1:8090"></script>
```

The top-bar "AI assistant" button then opens the new panel. This was verified by injecting the same four files into the original page served locally:

* "Show me a route to the delta" drew the route; "Change to the terrain map" turned on `nasaLayers.mola` and ticked its checkbox.
* "Open the mission log" and "Open the route planner" switched tabs.
* "Show me the steepest section" highlighted and zoomed.
* "Analyze this location" used `AppState.selected` set by the original `select()`.
* Every action was acknowledged as succeeded.

**Zero-change option:** the existing drawer already posts to `/api/chat`. If the Marswalk backend proxies `/api/chat` to `http://127.0.0.1:8090/api/chat`, the old drawer gets text answers immediately. It cannot run map actions.

### 6.2 Backend (when it exists)

Set in `.env`:

```text
MARSWALK_API_URL=http://127.0.0.1:8000
MARSWALK_ENABLED_ENDPOINTS=route_auto,route_analyze,elevation,eva
```

At startup the adapter probes the GET endpoints (`/api/stops`, `/api/spaceweather`, `/api/meda`, `/api/records`) and enables those that answer. POST endpoints must be listed explicitly because they can't be probed safely. Enabled endpoints register with priority 30 and replace the standalone or engine capability of the same id. `/api/stops` replaces the mock mission places (aliases such as "delta" are kept).

The mappings follow `MARSWALK_TECHNICAL_REFERENCE.md` §3 and are **UNVERIFIED** until run against the real server. Check `_normalize_route()` in `adapters/marswalk_http.py` first: it converts the Marswalk `RouteAnalysis` into the assistant's route record.

## 7. Connecting the Scientific Intelligence Engine

```bash
cd C:\Users\HP\Downloads\Marswalk-Mission-Planner-Ai-model-main\Marswalk-Mission-Planner-Ai-model-main
```

```bash
python -m venv --system-site-packages .venv
```

```bash
.venv\Scripts\python -m pip install fastapi uvicorn pydantic-settings httpx scikit-image
```

```bash
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

chromadb and google-genai are optional for the engine (lazy imports with fallbacks). Without chromadb its `/api/health` reports `chromadb_loaded_chunks: 0` and its own evidence lists are empty. The assistant's `science.search_documents` still cites the same documents. Verified live on 2026-11-14: EVA, radiation, environment and space-weather requests ran through the engine.

Then set `SCIENCE_ENGINE_URL=http://127.0.0.1:8000` and `KNOWLEDGE_DIR=<engine>\data\documents`. On startup the assistant calls `/api/health` and registers `eva.estimate`, `radiation.estimate`, `environment.analyze` and `space_weather.analyze` from the engine (priority 20). The engine's terrain, route and mission endpoints are deliberately **not** used (synthetic profiles; see the architecture §2).

## 8. Adding a capability

```python
# backend/marswalk_ai/adapters/my_adapter.py
from ..registry import Capability, CapResult, CapabilityError
from ..schema import obj, LAT, LON

def register(registry, services):
    async def handler(args, ctx):                       # ctx.session, ctx.services, ctx.turn
        if not services.http:
            raise CapabilityError("upstream_unreachable", "…")   # honest, typed failure
        value = ...                                      # compute or fetch: never ask the LLM for numbers
        return CapResult({"value": value}, limitations=["…"], evidence=[])

    registry.register(Capability(
        id="crism.mineral_context", name="CRISM mineral context", category="science", provider="crism-service",
        description="What the model sees when choosing tools - say what it returns and what it never returns.",
        input_schema=obj({"lat": LAT, "lon": LON}, ["lat", "lon"]),
        output_schema=obj({"value": {"type": ["string", "null"]}}, ["value"]),
        handler=handler, data_mode="real", permission="read", priority=20, timeout_s=20))
```

Call `register()` in `main.build_app_state`, add a card in `cards.py` if useful, add a test, then run `python scripts/export_capabilities.py` to refresh the registry document. The LLM sees the new tool on the next session; the rule planner needs an intent only if offline support is wanted.

## 9. Phase 5 checklist

1. Add the four script and link tags to the original `index.html` (§6.1). Confirm `/api/assistant/health` is reachable from the page origin.
2. Run the 14 required requests on the real page and check every action chip turns ✓.
3. If the Marswalk backend now exists, set `MARSWALK_API_URL` / `MARSWALK_ENABLED_ENDPOINTS`, restart, and check `/api/assistant/health → integrations.marswalk_backend.endpoints`.
4. Compare one route from the standalone service with the Marswalk engine on the same endpoints. Fix `_normalize_route()` if fields differ.
5. Run the engine and set `SCIENCE_ENGINE_URL` and `KNOWLEDGE_DIR`.
6. Set an LLM key and test the same requests in `llm` mode. Watch `limitations` for grounding-check warnings.
7. Remove nothing: standalone capabilities stay registered at lower priority as automatic fallbacks.

## 10. Configuration reference

All variables are listed in [.env.example](.env.example). The important ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | `auto` | `auto` picks the first configured of gemini → anthropic → openai, else offline |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-2.5-flash` | |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | – / `claude-sonnet-5-5` | |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_TOOL_MODE` | OpenAI / – / `native` | Works with Ollama, vLLM, LM Studio; `json` for models without tool calling |
| `SCIENCE_ENGINE_URL` | – | Scientific Intelligence Engine |
| `MARSWALK_API_URL`, `MARSWALK_ENABLED_ENDPOINTS` | – | Original Marswalk backend |
| `KNOWLEDGE_DIR` | – | Markdown corpus for `science.search_documents` |
| `CTX_DEM_PATH`, `HIRISE_DTM_PATH` | `../data/raw/…` if present | `none` disables |
| `ALLOW_SYNTHETIC_TERRAIN` | `true` | Labelled SIMULATED surface when no DEM is available |
| `MAX_SLOPE_DEG`, `CAUTION_SLOPE_DEG`, `K_SUIT`, `STOP_MINUTES`, `SUIT_BUDGET_HOURS`, `RESERVE_PERCENT`, `RAD_REFERENCE_MGY_DAY` | 15, 10, 0.6, 30, 4, 20, 0.21 | Project-defined, not NASA standards |
| `SESSION_DB` | `var/sessions.sqlite3` | `:memory:` disables persistence |
