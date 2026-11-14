# Marswalk AI: Mission Assistant

A conversational mission companion for the **Marswalk Mission Planner** (Jezero Crater EVA planning). It understands requests such as *"Find me the best route to the delta"*, runs the approved mission capabilities (route planning, terrain analysis, EVA and radiation estimates, map control, navigation), and replies with structured, source-labelled results. Every number it states comes from a tool, never from the language model.

It runs on its own now and connects to the original Marswalk app later through adapters.

| Document | Contents |
| --- | --- |
| [MARSWALK_AI_ARCHITECTURE.md](MARSWALK_AI_ARCHITECTURE.md) | Design, components, data flow, safety rules, known/unknown parts of Marswalk |
| [MARSWALK_AI_INTEGRATION_GUIDE.md](MARSWALK_AI_INTEGRATION_GUIDE.md) | API contract, frontend action contract, connecting to the real Marswalk app |
| [MARSWALK_AI_CAPABILITY_REGISTRY.md](MARSWALK_AI_CAPABILITY_REGISTRY.md) | Every capability with schemas, permissions, providers (generated from code) |

## Quick start (Windows PowerShell)

```bash
cd marswalk-ai
```

```bash
python -m venv --system-site-packages .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
copy .env.example .env
```

```bash
.venv\Scripts\python -m uvicorn marswalk_ai.main:app --app-dir backend --port 8090
```

Open <http://127.0.0.1:8090/> for the demo environment (mock Marswalk map plus assistant panel), or <http://127.0.0.1:8090/docs> for the OpenAPI docs.

With no API key the assistant runs in **offline rule-based mode**: intent recognition and replies are deterministic and use the same tools. Add `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` or an OpenAI-compatible endpoint in `.env` to enable the LLM orchestrator.

### Run the full Marswalk app (2D planner + 3D globe + assistant)

The original 2D planner needs its `/api/*` backend, which lives in `backend/marswalk_api/` (see [MARSWALK_BACKEND.md](MARSWALK_BACKEND.md)):

```bash
.venv\Scripts\python -m uvicorn marswalk_api.main:app --app-dir backend --port 8085
```

Then open <http://127.0.0.1:8085/>. Keep the assistant (port 8090) running too for the AI panel.

## What is real and what is mock

| Part | Status in the standalone build |
| --- | --- |
| Terrain (elevation, slope) | **Real** Mars 2020 HiRISE DTM (1 m) and CTX DEM (20 m), read from `../data/raw/` when present. Otherwise a clearly labelled **SIMULATED** surface |
| Route planning | Standalone least-cost router on that DEM (not the Marswalk route engine, which does not exist yet) |
| Places, rover track | Real Perseverance localisations (NASA/JPL-Caltech MMGIS snapshot). Stop names and reasons are Marswalk's unconfirmed starter set |
| Mission status, astronaut position | **Mock** (Marswalk has no such model) |
| EVA time, radiation dose | Project-defined models. Uses the Scientific Intelligence Engine when `SCIENCE_ENGINE_URL` is set, otherwise a local fallback |
| Environment (MEDA), space weather (DONKI) | **Unavailable** unless the Science Engine or Marswalk backend is connected. The assistant says so and invents nothing |
| Scientific documents | Retrieval over a Markdown corpus (`KNOWLEDGE_DIR`, e.g. the engine's `data/documents`) |

## Tests

```bash
.venv\Scripts\python -m pytest
```

There are 45 tests. They cover the Marswalk backend's `/api/*` contract (10 tests), the mission-log planning request, and the 14 required astronaut requests and their follow-ups, invented-number detection, schema and permission enforcement, confirmations, LLM tool loops and provider wire formats (Gemini, Anthropic, OpenAI), LLM and backend failure handling, the Science Engine and Marswalk adapters against fake servers, the HTTP API, the legacy `/api/chat` contract, and a real-DEM check (DTM elevation within 2 m of the rover-measured value).

## Layout

```text
marswalk-ai/
├── backend/marswalk_ai/
│   ├── main.py            FastAPI app, startup wiring, legacy /api/chat shim
│   ├── orchestrator.py    LLM tool loop, rule fallback, grounding check, response builder
│   ├── planner.py         offline intent recognition + multi-step plans
│   ├── registry.py        Capability Registry (allowlist, validation, permissions, timeouts)
│   ├── memory.py          session memory (conversation + mission state, SQLite persistence)
│   ├── context.py         mission data, turn recorder, coordinate grounding
│   ├── cards.py, prompts.py, schema.py, contracts.py, config.py
│   ├── providers/         gemini.py, anthropic.py, openai_compat.py (HTTP, no SDKs), factory.py
│   ├── adapters/          standalone.py, frontend.py, science_engine.py, marswalk_http.py
│   ├── services/          terrain.py (DEM access), routing.py (least-cost paths), knowledge.py (retrieval)
│   └── data/              mission.json, rover_waypoints.json (built by scripts/build_mock_data.py)
├── frontend/
│   ├── index.html         standalone demo environment
│   ├── js/assistant.js    embeddable assistant panel
│   ├── js/action-bridge.js  typed, validated frontend action interface
│   ├── js/demo-env.js     demo map (NASA Mars Trek tiles, same CRS as Marswalk)
│   └── integration/marswalk-bridge.js   bridge for the ORIGINAL Marswalk page
├── scripts/               build_mock_data.py, export_capabilities.py
└── tests/
```
