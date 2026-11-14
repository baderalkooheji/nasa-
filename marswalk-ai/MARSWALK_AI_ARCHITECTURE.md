# Marswalk AI: Architecture

> Status: Phases 1–4 are built and tested, and Phase 5 is prepared: the real-backend adapter and the real-page bridge exist. The bridge was exercised against the original Marswalk page in a browser; the backend adapter only against a fake server, because the Marswalk backend does not exist yet. Labels used below: **VERIFIED** = checked in code or by running it; **UNVERIFIED** = written to a documented contract that could not be run; **MOCK** = a stand-in value.

## 1. What this system is

Marswalk AI is the conversational **operating interface** of the Marswalk Mission Planner. An astronaut or planner talks to it in natural language. It decides which approved mission capabilities to run, runs them, validates the results, and replies with a short message plus structured cards and map actions.

It sits on top of two existing pieces:

| Piece | Role | Relationship |
| --- | --- | --- |
| Marswalk Mission Planner (`marswalk-combined/`) | 2D Leaflet planner + 3D ArcGIS globe, static frontend | The assistant **controls** it through a frontend bridge and (later) **calls** its backend |
| Scientific Intelligence Engine (`Marswalk-Mission-Planner-Ai-model-main/`) | Headless FastAPI service: EVA, radiation, MEDA, DONKI, RAG corpus | The assistant **calls** it as a tool provider and reuses its document corpus |

The assistant does not re-implement Marswalk. Where Marswalk functionality does not exist yet (routing, elevation), the assistant has a clearly labelled **standalone** implementation behind the same capability ids. A real implementation replaces it by registering with a higher priority.

## 2. What was known and unknown when this was built

From `MARSWALK_TECHNICAL_REFERENCE.md` (code inspection) and the engine source:

| Item | Finding | Consequence |
| --- | --- | --- |
| Marswalk backend (`/api/*`, 10 routes) | **Not found**. The frontend calls routes that do not exist | `adapters/marswalk_http.py` targets the documented client contracts, is **UNVERIFIED**, and registers only when configured and reachable |
| Marswalk frontend globals | `window.map`, `select`, `setView`, `Mars3D`, plus top-level `AppState`, `nasaLayers`, `chatContext()` | `frontend/integration/marswalk-bridge.js` uses exactly these; **VERIFIED** by injecting it into the original page |
| Existing chat contract | `POST /api/chat {question, context} → {answer, sources}` | Implemented as a compatibility shim (`main.py: legacy_chat`) |
| Mission status, astronaut position, user preferences | Not modelled in Marswalk | **MOCK** values, labelled in every response |
| Engine `/eva-estimation`, `/radiation-estimation` | Deterministic formulas (**VERIFIED** in source) | Used when the engine is reachable |
| Engine `/environmental-analysis`, `/space-weather-analysis` | Read local `*_sample_cache.json` files; provenance not documented | Used, with an explicit "sample cache" limitation |
| Engine `/terrain-analysis`, `/route-evaluation`, `/mission-analysis` | Use a synthetic elevation ramp / fixed slope patterns, not a DEM (**VERIFIED** in `app/api/intelligence.py`) | **Not used**. The assistant's own DEM-based terrain service replaces them |
| Engine RAG | 7 curated Markdown summaries; no retrieval endpoint | Retrieval re-implemented over the same files (`services/knowledge.py`) |
| Real DEMs | `data/raw/ctx/…CTXDEM_20m.tif`, `data/raw/hirise/…DTM…1m….tif` present locally | Terrain and routing run on real Mars 2020 data when available |

## 3. Architecture

```text
USER
 │  natural language
 ▼
ASSISTANT PANEL (frontend/js/assistant.js) ───────── ACTION BRIDGE (frontend/js/action-bridge.js)
 │  POST /api/assistant/chat {message, client_context}       ▲ validates + executes typed actions,
 ▼                                                           │ POST /api/assistant/actions/result (ack)
FASTAPI (main.py)                                            │
 ▼                                                           │
ORCHESTRATOR (orchestrator.py)                               │
 ├─ context manager: session memory + UI state (memory.py)   │
 ├─ LLM tool loop (providers/*)  ── fails ──► RULE PLANNER (planner.py)
 ▼                                                           │
CAPABILITY REGISTRY (registry.py): allowlist · JSON-schema validation · permissions · timeouts · priority
 ▼                                                           │
ADAPTERS                                                     │
 ├─ standalone.py     mission data · DEM terrain · route service · document retrieval · fallbacks
 ├─ science_engine.py Scientific Intelligence Engine (HTTP)
 ├─ marswalk_http.py  original Marswalk backend (HTTP, when it exists)
 └─ frontend.py       map/navigation actions → typed frontend_actions ────────┘
 ▼
VALIDATED RESULTS → cards.py (deterministic cards) · evidence · limitations · grounding check
 ▼
AssistantResponse {message, intent, status, tool_calls, tool_results, frontend_actions, cards, evidence, limitations, errors, …}
```

### Components

| Component | File(s) | Responsibility |
| --- | --- | --- |
| HTTP API | `main.py`, `contracts.py` | Sessions, chat, button commands, confirmations, action acks, capability listing, legacy `/api/chat`, static demo |
| Orchestrator | `orchestrator.py` | Runs one user turn: LLM tool loop (max `MAX_TOOL_ROUNDS`), rule fallback, grounding check, response assembly |
| Rule planner | `planner.py` | Offline intent recognition (about 30 intents), slot extraction (places, slopes, ordinals, route ids), multi-step plans, template replies |
| Capability Registry | `registry.py`, `schema.py` | The only way to execute anything. See [MARSWALK_AI_CAPABILITY_REGISTRY.md](MARSWALK_AI_CAPABILITY_REGISTRY.md) |
| Memory | `memory.py` | Conversation (recent messages, topic) kept **separate** from mission state (routes, route sets, active/previous route, destination, analyses, preferences, action outcomes, pending confirmations). SQLite persistence (stdlib) |
| Turn context | `context.py` | Mission places, rover snapshot, per-turn recorder (actions, evidence, limitations, known coordinates and numbers) |
| Providers | `providers/` | Gemini, Anthropic, OpenAI-compatible (also Ollama/vLLM), each over plain HTTP. JSON tool protocol for models without native tool calling |
| Terrain | `services/terrain.py` | Windowed DEM reads (rasterio), equirectangular → lat/lon, true ground spacing, Horn slope, point statistics |
| Routing | `services/routing.py` | Least-cost paths (Dijkstra, scipy) on the slope grid, alternatives, route metrics, Tobler walking time, deterministic recommendation |
| Retrieval | `services/knowledge.py` | Section chunking with overlap, TF-IDF cosine ranking, relevance threshold, chunk-level citations |
| Cards | `cards.py` | Result cards and their buttons, built from tool results only |
| Frontend | `frontend/js/*` | Assistant panel, action bridge, demo environment; `frontend/integration/marswalk-bridge.js` for the original page |

## 4. One request, end to end

*"Find me the best route to the delta and show it on the map"*

1. The panel sends `{session_id, message, client_context}`. `client_context` is the live UI state from the bridge: section, visible layers, map centre/zoom, selected point, displayed routes.
2. The orchestrator merges the UI state into the session and builds the LLM input:
   * the system prompt (rules + **compact mission state** + available sections/layers + integration status)
   * the last 8 messages
   * the tool list: only capabilities that are available, and only frontend actions that **this** UI advertised
3. The LLM (or the rule planner) calls the tools in turn: `mission.get_context` → `route.generate {destination: "stop-1"}` → `map.display_route {route_id: "R2"}`.
4. For each call, the registry checks the allowlist, validates the arguments against the JSON schema (rejecting invalid ones *before* running), checks the permission, and runs the handler with a timeout.
   * `route.generate` reads the HiRISE DTM window, computes the slope grid, finds three least-cost candidates, evaluates them, and picks one deterministically.
   * Routes are stored in session memory as `R1`–`R3`.
   * `map.display_route` resolves `R2` to its stored geometry and emits a typed `display_route` frontend action with `status: "pending"`. The LLM is told *pending_ack* and is instructed not to claim it is displayed.
5. Results are trimmed (geometries removed) before going back to the LLM, which writes the final message.
6. The grounding check compares every number in that message with the numbers in the tool results, the mission state and the user's text. Unmatched numbers, and any claim that something is "safe", are added to `limitations`.
7. The response returns. The panel renders the message, the tool timeline, cards (route table with Show buttons), evidence and limitations. It then executes the frontend action through the bridge and acknowledges it (`/actions/result`). The chip turns ✓ or ✕ **only after the page ran it**, and the outcome is stored in session memory, where the next turn can see it.

## 5. Who does what: Python, retrieval, LLM

| Function | Deterministic Python | Retrieval (RAG) | LLM |
| --- | --- | --- | --- |
| Elevation, slope, relief, coverage | ✔ `terrain.py` (DEM) | | |
| Route generation, alternatives, metrics, recommendation | ✔ `routing.py` | | explains |
| Route comparison | ✔ `route.compare` | | summarises |
| EVA time budget, radiation dose | ✔ engine / fallback formulas | | explains |
| Space-weather caution status | ✔ engine rule | | explains |
| Place names → coordinates | ✔ `MissionData.resolve` (gazetteer) | | |
| Scientific background, method notes | | ✔ `science.search_documents` | cites `[DOC-n]` |
| Understanding the request, choosing tools, follow-ups | rules (offline / fallback) | | ✔ |
| Final wording | templates (offline) | | ✔ (checked by the grounding check) |
| Map control, navigation | validation + typed actions | | chooses when |

The LLM never produces a measurement, a coordinate, a route or a classification. Map tools only accept place ids, route ids, or coordinates that appear in a tool result, the UI state or the user's own message (`TurnRecorder.coordinate_is_grounded`).

## 6. Scientific reliability: rule → mechanism

| Rule | Mechanism |
| --- | --- |
| Never invent measurements | Numbers come only from tool results. A grounding check flags any other number. Missing values stay `null` |
| Never fabricate coordinates | Coordinate grounding in `resolve_point`; the LLM refers to `poi_id` / `route_id` |
| Never claim an action or tool succeeded when it didn't | Frontend actions are `pending` until the page acknowledges them. Tool errors are listed in `errors`; status becomes `partial` / `error` |
| Never claim a route is safe | Prompt rule, a fixed sentence in route replies, and a grounding check for "safe" claims |
| Measured vs calculated vs estimated | Every capability has a `data_mode` (`real`, `simulated`, `calculated`, `reference`, `cached-sample`). Cards show it. `simulated: true` and `mock_components` are added to every affected response |
| Gale ≠ Jezero radiation | The radiation result, card and prompt all state it |
| MEDA is local and historical | Stated in the limitations and the card. No MEDA source in standalone mode means "unavailable", never invented values |
| Project-defined thresholds | Every threshold is labelled "project-defined" (limit 15°, caution 10°, suit budget 4 h, reserve 20 %, k_suit 0.6) |
| Dataset resolution, CRS, nodata | Per-raster standard parallel and true ground spacing; nodata → NaN → blocked cells; dataset and effective resolution reported with every result |
| Coverage limits | Routing and terrain use only datasets that cover the points (HiRISE → CTX). Outside coverage: "no data", never extrapolation |
| LLM failure | Rule-planner fallback (labelled), or a summary of the completed operations if the LLM failed mid-turn |

## 7. Capability Registry (summary)

Each capability declares an `id`, `name`, `description`, `input_schema` (closed JSON schema), `output_schema` (soft-checked), `handler`, `side` (backend/frontend), `category`, `changes_state`, `permission` (`read` / `write` / `confirm`), `availability` (+ reason), `provider`, `data_mode`, `priority`, `timeout_s` and `error_policy`.

* **Adapters register what they actually support.** A higher-priority provider replaces a lower one for the same id (Marswalk backend 30 > Science Engine 20 > standalone 10 > local fallbacks 5 > unavailable stubs 1).
* **Frontend capabilities are per-session.** A UI offers only the actions, sections and layers it advertised in its handshake. Section and layer enums in the schemas come from that handshake.
* **`confirm` permissions** (e.g. `mission.save_record`) return `pending_confirmation`. They run only through `POST /api/assistant/confirm`, once.
* Nothing is discovered automatically from source code or OpenAPI. Backend OpenAPI specs could seed adapters, but each capability is registered explicitly.

## 8. Model choice

| Need | Choice | Why |
| --- | --- | --- |
| Default LLM | **Gemini 2.5 Flash** (`GEMINI_MODEL`) | The Science Engine already uses it, so the team has keys and experience. Native function calling, fast, low cost; suits short tool-heavy turns |
| Alternatives | Anthropic (`ANTHROPIC_MODEL`, default `claude-sonnet-5-5`), any OpenAI-compatible endpoint (OpenAI, Ollama, vLLM, LM Studio, OpenRouter) | Same orchestrator; switch with `LLM_PROVIDER`. Local models without tool calling use `OPENAI_TOOL_MODE=json` |
| No key / outage | Offline rule planner | Demos never depend on network access to an LLM. Every required request works offline |
| Retrieval | TF-IDF over the engine's Markdown corpus | 7 documents, offline, deterministic, no model download. When the engine exposes an embedding-based search endpoint, register it as `science.search_documents` with a higher priority |

No model is trained. Providers are about 100 lines each over HTTP, so changing vendor never touches the orchestrator.

## 9. Frontend

* **Panel** (`assistant.js`, styles scoped under `.mwai-`): a floating orb opens a mission-control panel with provider status, a data-source strip, a message log, tool timeline, result cards with real action buttons, evidence and limitation folds, confirmation prompts, suggestions and an input. It is embeddable in any page.
* **Bridge** (`action-bridge.js`): the only way actions reach the page. It has a fixed set of six action types, each payload is validated (coordinate ranges, geometry size, types), and only host-registered handlers run. No code execution.
* **Demo environment** (`index.html`, `demo-env.js`): a stand-in Marswalk page with the same NASA Mars Trek tiles and CRS (EPSG4326 = planetocentric, east-positive), mission places, the rover track, layer toggles kept in sync with assistant actions, and five sections (map, dashboard, terrain, environment, history).
* **Original page bridge** (`integration/marswalk-bridge.js`): the same handlers implemented with the original code's globals. Assistant routes go in their own Leaflet layer group, so Route A/B are untouched.

## 10. Implementation status by phase

| Phase | Status |
| --- | --- |
| 1 Standalone foundation (UI, conversation, LLM connection, intents, structured responses, context, errors) | Done |
| 2 Capability Registry (definitions, registration, discovery, validation, execution, mock handlers, mock context) | Done |
| 3 Mission functionality (routes, comparison, terrain, EVA, context, science Q&A) | Done. Terrain and routes run on **real** DEMs when available; mock items are labelled |
| 4 Interactive assistant (map actions, route display, highlights, navigation, cards, buttons) | Done |
| 5 Real Marswalk integration | Prepared. The frontend bridge was tested on the original page (route display, layer change with checkbox sync, tab navigation, highlight/zoom, selection from `AppState`). The backend adapter is written to the documented contract and tested against a fake server; it is **UNVERIFIED** against a real one because none exists |

## 11. Risks, limitations and descoped items

| Item | Notes |
| --- | --- |
| Marswalk backend does not exist | Until it does, routes and terrain come from the standalone services. If the team builds it, keep `/api/route/auto` and `/api/elevation` compatible with `MARSWALK_TECHNICAL_REFERENCE.md` §3 or update `marswalk_http.py` |
| Standalone route service vs a future Marswalk engine | Results will differ. The provenance on every route says which engine produced it |
| HiRISE DEM reads | The raw 1.8 GB GeoTIFF is stored in full-width strips. Routes over a few km read tens to hundreds of MB from disk (about 0.5–1 s). Pre-tiled or overview rasters would be faster |
| Engine sample caches | MEDA and DONKI values from the engine come from local sample files; they are labelled as such |
| Streaming responses | Not implemented. The panel shows a working indicator and then the full tool timeline. True streaming (SSE) is a later improvement |
| Voice input/output | Not implemented (out of scope for 48 h) |
| Live LLM providers | The wire formats are unit-tested against recorded shapes. A live call with a real key was **not** run in this environment |
| 3D globe actions | The bridge targets the 2D map. `zoom_to_location` uses `Mars3D.goToLocation` when the 3D view is open; route drawing on the globe is not implemented (the original `drawRoute3D` is broken, see the technical reference §13) |
| Authentication | None, like Marswalk. Put it behind the same reverse proxy or add a token check before any public deployment |

## 12. Dependencies

Python 3.11+ (tested on 3.13): `fastapi`, `uvicorn`, `httpx`, `numpy`, `scipy`, optional `rasterio`, and `pytest` for tests. The frontend has no build step; Leaflet 1.9.4 comes from a CDN (same as Marswalk). No LLM SDKs.
