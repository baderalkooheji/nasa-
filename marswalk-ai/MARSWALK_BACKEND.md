# Marswalk backend (`/api/*` for the 2D planner)

The original Marswalk 2D planner (`js/map.js`) calls ten `/api/*` routes that were never built, so it showed *"Backend offline: showing NASA imagery and rover data only…"*. `backend/marswalk_api/` implements them, using the same real-DEM terrain and routing code as the Marswalk AI assistant. It also serves the Marswalk page itself, so `map.js`'s same-origin `const API = ""` works unchanged.

## Run

```bash
cd marswalk-ai
```

```bash
.venv\Scripts\python -m uvicorn marswalk_api.main:app --app-dir backend --port 8085
```

Open **<http://127.0.0.1:8085/>**. The first start renders the slope and hillshade overlays from the DEM (about 20 s) and caches them in `var/overlays/`; later starts are instant.

For the AI assistant panel, also run the assistant on port 8090 (see [README.md](README.md)). `index.html` loads it automatically when it is running.

## Endpoints

Contracts follow `MARSWALK_TECHNICAL_REFERENCE.md` §3 (what `map.js` sends and reads).

| Route | What it returns | Data |
| --- | --- | --- |
| `GET /api/layers` | Overlay bounds, DEM name, two layers: **Slope classes** (amber 10–15°, orange > 15°) and **Hillshade** | Rendered from the HiRISE DTM at about 10 m/px |
| `GET /api/overlays/{slope,hillshade}.png` | The overlay images | – |
| `GET /api/stops` | `data/stops.json` (with the mis-encoded `·` fixed) | Real rover positions; names are Marswalk's starter set |
| `GET /api/elevation?lat&lon` | `elevation_m`, `slope_deg` (Horn 3×3), `source_dataset`, `data_available`, `no_data_reason` | Finest DEM covering the point (HiRISE 1 m, else CTX 20 m) |
| `POST /api/route/analyze` | Route A analysis: profile (lat, lon, dist, elevation, slope), distance, gain/loss, max/avg slope, `over_limit`, coverage | Terrain sampled along the drawn line |
| `POST /api/route/auto` | Route B: least-cost route through all stops (one leg per pair), joined profile, `waypoints` | Dijkstra on the slope grid; cells above the limit blocked; a leg falls back from HiRISE to CTX when no path exists at HiRISE resolution (shown in `source_dataset`) |
| `POST /api/eva` | Walk out / science / walk back / reserve (minutes), suit limit, status `ok` / `low` / `over`, dose estimate | Modified Tobler walking model (k_suit 0.6), 30 min per stop, 4 h suit, 20 % reserve target, Gale RAD 0.21 mGy/day reference. All project-defined |
| `GET /api/spaceweather` | `nominal` / `caution` / `unknown`, flares and SEP events of the last 7 days | NASA DONKI (api.nasa.gov, then CCMC), refreshed hourly, saved copy used when offline. `unknown` if no data was ever fetched |
| `GET /api/meda` | **503 (not installed)** | No calibrated MEDA tables are installed, so the Environment tab keeps its placeholder rather than showing sample values as measurements |
| `POST /api/records`, `GET /api/records?q=` | Mission log | SQLite: `var/marswalk_records.sqlite3` |
| `POST /api/chat` | Forwards the built-in drawer to the AI assistant (`ASSISTANT_URL`) | 501 when the assistant is not running |
| `GET /api/health` | Terrain datasets, overlay metadata | – |

Errors use FastAPI's `{"detail": "…"}`, which `map.js` shows in its notifications. Examples: a route outside DEM coverage returns 422 *"No configured elevation dataset covers this route."*; an auto-route leg with no path returns 422 *"Leg 2 (A → B): No route was found…"*.

## Settings

The backend reads `marswalk-ai/.env` (see [.env.example](.env.example)): `CTX_DEM_PATH`, `HIRISE_DTM_PATH`, `MAX_SLOPE_DEG`, `CAUTION_SLOPE_DEG`, `K_SUIT`, `STOP_MINUTES`, `SUIT_BUDGET_HOURS`, `RESERVE_PERCENT`, `RAD_REFERENCE_MGY_DAY`. It also reads `NASA_API_KEY` (default `DEMO_KEY`), `DONKI_ENABLED`, `ASSISTANT_URL`, `MARSWALK_RECORDS_DB`, `MARSWALK_OVERLAY_DIR` and `MARSWALK_ROOT` (the Marswalk folder; default is the parent of `marswalk-ai/`).

## Known limits

* `map.js` does not send the slope slider value to `/api/route/analyze`, so Route A's over-limit flag uses the server limit (15°). Route B receives the slider value.
* Space weather depends on reaching NASA DONKI. During development neither DONKI host was reachable from this machine, so the badge showed "unavailable". The live path is covered by tests with a mocked server.
* MEDA is intentionally not served (see above).
* Only static folders the page needs are served (`css`, `js`, `assets`, `data`); `.git`, `.env` and the AI project are not exposed.
