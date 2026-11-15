# Marswalk AI Assistant

Local, JARVIS-style mission assistant for the **Marswalk Mission Planner**. It runs entirely on your laptop:

* **Brain:** a local LLM through [Ollama](https://ollama.com) (default `qwen3:1.7b`, CPU only, no API key, no cloud).
* **Tools (17):** route planning and comparison, terrain analysis, EVA budget, environment, knowledge search, rover position,
  map control, app navigation, route journey, mission log. Numbers come from the **real Marswalk backend** (NASA Mars 2020 DEMs),
  never from the model.
* **Knowledge base (RAG):** PDF / TXT / Markdown / JSON documents in `backend/data/documents`, searched before answering, with citations.
* **Memory (SQLite):** conversation history and mission memory (routes generated, current route, analyses, map state).
* **Interface:** a mission-control panel embedded in the Marswalk page (orb, bottom right), plus a standalone page at `/app`.

Based on the original assistant by the team (github.com/ahmed-awad11/AI-mars-assistance); see "What changed" below.

## Architecture

```
Marswalk page (index.html, :8085) ──── embed/marswalk-embed.js ──── POST /chat {message, client_context}
      ▲   executes map actions, reports ✓/✕                              │
      │   (POST /actions/result)                                          ▼
      │                                         Assistant backend (FastAPI, :8095)
      │                                           1. system prompt + page state + memory
      │                                           2. Ollama (qwen3) chooses tools ──► tools.py
      │                                           3. tool results → model → answer   │
      └──────────── actions (display_route, zoom_to, …) ◄────────────────────────────┤
                                                   real data: marswalk_client.py ────► Marswalk backend (:8085)
                                                   knowledge:  rag.py (BM25 / ChromaDB) ─► data/documents
                                                   memory:     models.py (SQLite)
```

## Install (Windows)

1. **Ollama:** install from https://ollama.com/download (runs in the tray), then
   ```
   ollama pull qwen3:1.7b
   ```
   Optional, better tool choice but ~2.5x slower on a CPU: `ollama pull qwen3:4b` and set `DEFAULT_MODEL=qwen3:4b`.
2. **Python 3.10+:**
   ```
   cd marswalk-assistant
   python -m venv .venv
   .venv\Scripts\python -m pip install -r backend\requirements.txt
   ```
3. Optional: `copy backend\.env.example backend\.env` and edit (port, model, Marswalk URL).

## Run

1. Start the Marswalk backend (serves the page and the data on port 8085):
   `cd marswalk-ai && .venv\Scripts\python -m uvicorn marswalk_api.main:app --app-dir backend --port 8085`
2. Start the assistant: `marswalk-assistant\.venv\Scripts\python marswalk-assistant\backend\main.py` (port 8095)
3. Open http://127.0.0.1:8085 and click the orange orb (bottom right). Standalone page: http://127.0.0.1:8095/app/

`GET http://127.0.0.1:8095/health` shows whether Ollama, the model, the Marswalk backend and the knowledge base are ready.
On a laptop CPU an answer takes roughly 10-60 s (the panel shows a timer); the first question also loads the model.

## Try it

* "Plan a route from the habitat to the delta front" → planner on the DEM, route drawn on the map as Route B
* "Give me three route options to stop 3 and compare them" → balanced / gentlest / most direct, recommendation
* "How long would that EVA take?" → EVA budget for the current route (follow-up uses mission memory)
* "Analyse the terrain at the selected point" (click the map first) → elevation, relief, slope statistics, difficulty
* "Where is Perseverance?" → latest NASA localisation, marked on the map
* "Can astronauts make oxygen on Mars?" → knowledge base answer with [DOC-n] citations
* "Zoom to Jezero crater", "Show the HiRISE layer", "Open the 3D globe", "Start the journey", "Presentation mode"
* "Save this location to the log", "What's in the mission log?"

## Knowledge base

Every PDF/TXT/MD/JSON file in `backend/data/documents` is indexed at startup (only new or changed files). To add a document,
copy it there and restart the assistant, or `POST /knowledge/ingest {"filepath": "..."}`. Markdown files may start with front
matter (`title`, `source_url`, `organization`, …) so answers can cite the source; see `marswalk_app_guide.md` for an example.

Included: the Mars AI Agent Knowledge Base (6 documents from the team's PDF), the Science Engine's 7 Jezero documents
(geology, delta, CTX/HiRISE topography, MEDA, RAD, DONKI, EVA mobility), the Marswalk user guide and the technical reference.

Retrieval uses BM25 keyword ranking over SQLite (no model download). If `chromadb` and `sentence-transformers` are installed,
semantic search is used instead (heavy: installs PyTorch).

## Scientific rules (enforced in code and prompt)

* Measurements and calculations come only from tools: the Marswalk backend (DEM elevation, Horn slope, least-cost routes,
  modified Tobler walking model) or Python. No tool returns invented or random values; missing data is reported as missing.
* Values are labelled measured / calculated / estimated / historical / reference.
* Places are resolved on the server (habitat, stops, Jezero, rover, the map selection, explicit lat/lon), so the model never has to
  make up coordinates.
* Map actions are only *sent*; the page executes them and reports ✓/✕. The assistant never claims an action it cannot confirm.
* No route is called safe; slope limits and suit budgets are labelled project-defined.

## Connecting to the Marswalk application

* **Data:** `backend/marswalk_client.py` calls the Marswalk backend (`MARSWALK_API_URL`): `/api/stops`, `/api/elevation`,
  `/api/terrain`, `/api/route/auto`, `/api/route/options`, `/api/route/analyze`, `/api/eva`, `/api/spaceweather`, `/api/meda`,
  `/api/records`, `/data/M20_waypoints.json`.
* **Page:** `index.html` loads `/embed/marswalk-embed.css` and `/embed/marswalk-embed.js` from the assistant when `/health`
  answers. The panel sends the page state with each message and executes these actions with the page's own APIs:
  `display_route` (drawRoute + setState routeB), `highlight_location`, `zoom_to`, `fit_bounds` (Leaflet map / Mars3D),
  `change_layer` (nasaLayers / overlays), `navigate` (tabs, setView 2d/3d, mission log, MarswalkPresentation),
  `start_journey` (MarswalkJourney), `refresh_log`.
* **Adding a tool:** decorate an async function in `tools.py` with `@tool(...)`; return `ToolResult.success_result(...)` with
  labelled data, call `_queue_action(...)` for page actions and add the handler to `ACTIONS` in `marswalk-embed.js`.

## API

`POST /chat` · `POST /actions/result` · `GET/POST /conversations` · `GET /conversations/{id}/messages` ·
`GET/PATCH /conversations/{id}/context` · `POST /knowledge/ingest` · `POST /knowledge/ingest-directory` · `GET /knowledge/documents` ·
`POST /knowledge/search` · `GET /tools` · `POST /tools/execute` · `GET /models` · `POST /models/switch` · `POST /models/pull` · `GET /health`

## Tests

```
cd marswalk-assistant\backend
..\.venv\Scripts\python -m pytest
```
29 tests, no Ollama or Marswalk server needed (a fake Marswalk client and a scripted model): tool wiring and labels, place
resolution, alternatives and recommendation, honesty when data is missing, retrieval accuracy on known questions, off-topic
refusal, the chat loop (tools → model), mission memory across turns, action acknowledgement, Ollama-down handling.

## What changed from the first version

* Startup fixes: SQLAlchemy reserved `metadata` attribute, missing imports, missing `pydantic-settings`/`sqlalchemy` requirements,
  port 8000 conflict with the Science Engine (now 8095), paths independent of the working directory.
* Tools return real data instead of mock/random values; new tools: estimate_eva, start_route_journey, save_to_mission_log,
  get_mission_log.
* Native Ollama tool calling with a real agent loop (tool results go back to the model), qwen3 thinking off by default for speed.
* BM25 retrieval with citations and relevance gating instead of word counting; front matter kept; changed files re-indexed.
* Embedded Marswalk panel with action confirmation; mission log moved into the assistant.
