# Marswalk Mission Planner — Technical Reference for AI Assistant Integration

> **Scope.** This document describes the code in `marswalk-combined/` exactly as found on 2026-11-14. Nothing was modified.
> It is meant for a developer building a separate **AI Mission Assistant** that will plug into Marswalk later.
>
> **How to read status labels**
>
> | Label | Meaning |
> | --- | --- |
> | **IMPLEMENTED** | Code exists in this repository and is wired into the UI |
> | **FRONTEND-ONLY / BACKEND NOT FOUND** | The frontend calls a backend route, but no backend code exists in this repository |
> | **PLACEHOLDER** | The UI exists and shows placeholder text until a backend route answers |
> | **BROKEN** | Code exists but static inspection shows it cannot work as written |
> | **NOT FOUND** | No code for this in the repository |
> | **UNKNOWN** | Cannot be determined from the code |
> | **INFERRED** | Deduced from comments, naming or client-side usage, not from an implementation |
>
> All statuses come from **reading the code**. The app was not run during this review.

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Feature inventory](#2-complete-feature-inventory)
3. [Backend and API inventory](#3-backend-and-api-inventory)
4. [Map and geospatial functionality](#4-map-and-geospatial-functionality)
5. [Route generation and evaluation](#5-route-generation-and-evaluation)
6. [Scientific intelligence engine](#6-scientific-intelligence-engine)
7. [Data inventory](#7-data-inventory)
8. [Frontend architecture](#8-frontend-architecture)
9. [Backend architecture](#9-backend-architecture)
10. [Mission context and state](#10-mission-context-and-state)
11. [AI integration opportunities](#11-ai-integration-opportunities)
12. [Integration requirements](#12-integration-requirements)
13. [Known limitations and incomplete features](#13-known-limitations-and-incomplete-features)
14. [Testing and example workflows](#14-testing-and-example-workflows)
15. [Final integration summary](#15-final-integration-summary)

---

## 0. Key finding (read this first)

**This repository has no backend.** It is a static frontend: HTML, CSS and ES modules, served by any static server, plus one offline Python data-preparation script.

* The 2D Mission Planner (`js/map.js`) calls **10 backend routes under `/api/*`**: layers, stops, elevation, route analysis, auto-route, EVA, space weather, chat, MEDA and records. **None of them is implemented here.** No server code, no FastAPI or Flask app, no database and no OpenAPI spec exists in the repository.
* Every elevation, slope, route, EVA, dose and chat result in the 2D planner depends on that missing backend. Without it, the 2D app shows NASA imagery, the Perseverance traverse, the mission stops (from `data/stops.json`) and rover-measured elevations near rover waypoints. Nothing else works.
* The 3D globe (`js/mars3d.js` and its modules) is **self-contained and functional**. It needs internet access to Esri and an ArcGIS CDN. Its elevation readouts come from DEM tiles in the browser. **But** the local Jezero terrain tiles (`data/processed/terrain/`) are **not present** in this checkout (they are git-ignored), so CTX and HiRISE 3D terrain will fail to load until `prepare_terrain.py` is run again (see §13).
* The **AI assistant UI already exists**: the chat drawer in `index.html` and `askAssistant()` in `js/map.js`. It posts to `POST /api/chat` with a `context` object built by `chatContext()`. That gives you the **integration contract the frontend already expects** (§3.8, §12).

The endpoint "schemas" in §3 are therefore **client-side contracts**: what the frontend sends and which response fields it reads. They are the schemas a backend must satisfy. They are not documentation of an existing server.

---

## 1. Project overview

### 1.1 What Marswalk does

Marswalk is a NASA Space Apps project, a **Mars EVA (astronaut surface walk) mission planner** for **Jezero Crater**, the Perseverance landing site. It combines:

1. A **2D mission planner** (Leaflet). It shows NASA imagery and the rover traverse, places a habitat and science stops, lets the user draw a walking route by hand (Route A) or request an automatic hazard-avoiding route (Route B), and compares the two. It also shows elevation profiles, slope colouring, an EVA time budget, a radiation dose estimate and space-weather status. All analysis numbers are meant to come from a backend.
2. A **3D globe** (ArcGIS Maps SDK for JavaScript 5.1). It shows global Mars plus true-relief Jezero terrain from the CTX DEM (20 m) and the HiRISE DTM (1 m source), with orthoimagery. You can click to inspect coordinates and elevation.

Header text in `index.html:6`: *"Marswalk — Mission Planner & 3D Globe"*. Mission label (static): `EVA-01 · Jezero delta` (`index.html:31`).

### 1.2 Main objectives (from code comments and docs)

* Plan a walking EVA from a habitat to science stops and back, under a project-defined maximum walkable slope (default 15°, slider range 5–25°).
* Assess routes using **real elevation data only**. The code repeatedly says "never measure in pixels", "no synthetic data" and "never fake data" (`js/map.js:23-24`, `README.md:12`).
* Be honest about uncertainty. The UI says: *"Results are preliminary terrain assessments … They do not establish astronaut safety."* (`index.html:86`).
* Keep the 3D module independent so it can be embedded elsewhere (`docs/INTEGRATION_GUIDE.md`).

### 1.3 Main user workflows

| # | Workflow | Status |
| --- | --- | --- |
| W1 | Open app → 2D map of the Jezero delta with NASA imagery, stops and rover traverse | IMPLEMENTED (works without backend) |
| W2 | Click map or stop → right panel shows lat/lon, elevation, slope, source | Elevation and slope: BACKEND NOT FOUND. Offline fallback: rover-measured elevation within 40 m of a rover waypoint |
| W3 | Route tab → "Draw route by hand" → click points → "Finish route" → Route A analysed | Drawing IMPLEMENTED. Analysis BACKEND NOT FOUND |
| W4 | Route tab → "Auto-route around hazards" → Route B + EVA budget | BACKEND NOT FOUND |
| W5 | Compare A vs B (table, profile chart, note) | IMPLEMENTED in frontend. Needs backend data |
| W6 | Toggle 2D imagery layers and opacity | IMPLEMENTED |
| W7 | Switch to "3D Globe" → fly to Jezero, toggle layers, click to inspect elevation | IMPLEMENTED (local terrain tiles missing, see §13) |
| W8 | Environment tab → MEDA weather table | PLACEHOLDER (`/api/meda`) |
| W9 | Mission log tab → save and search records | PLACEHOLDER (`/api/records`) |
| W10 | AI assistant drawer → ask question | PLACEHOLDER (`/api/chat`) |
| W11 | Space-weather badge in top bar | BACKEND NOT FOUND (`/api/spaceweather`) |
| W12 | Notifications bell (warnings, completion, data unavailable) | IMPLEMENTED |

### 1.4 Technologies

| Layer | Technology | Where |
| --- | --- | --- |
| Page | Plain HTML5, no framework, no build step | `index.html` |
| 2D map | **Leaflet 1.9.4** (unpkg CDN), `L.CRS.EPSG4326` | `index.html:10,310`, `js/map.js` |
| 3D globe | **ArcGIS Maps SDK for JavaScript 5.1** (CDN `https://js.arcgis.com/5.1/`), loaded via `$arcgis.import` | `index.html:12-13`, `js/sdk.js` |
| Charts | **Chart.js 4.4.1** (jsDelivr CDN) | `index.html:16`, `js/map.js:494-509` |
| Scripts | `js/nasa-config.js`, `js/map.js` are **classic scripts**. `js/app.js`, `js/integration.js` and the 3D modules are **ES modules** | `index.html:310-314` |
| Styles | Plain CSS | `css/*.css` |
| Data prep (offline) | Python 3: rasterio (GDAL), numpy, scipy, shapely, pillow, lerc | `scripts/prepare-terrain/` |
| Rover data fetch (offline) | Python stdlib | `nasa/tools/fetch_nasa_data.py` |
| Serving | `python -m http.server 8080 --bind 127.0.0.1` (`package.json` `start`) | `package.json` |
| Package manager | None. `package.json` has no dependencies | `package.json:13` |
| Backend | **NOT FOUND** (the client code suggests FastAPI-style errors and SQLite; see §9) | — |

---

## 2. Complete feature inventory

Legend for the Location column: **TB** = top bar, **LS** = left sidebar tabs, **RP** = right panel ("Selected location"), **BP** = bottom analysis panel under the 2D map, **3D** = 3D globe view.

| # | Feature | What it does | Location | Frontend file / function | Backend | Inputs → outputs | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F1 | 2D map | Leaflet map in Mars lon/lat (EPSG4326 grid) | Centre | `js/map.js:110-121` (`map`) | — | → map | IMPLEMENTED |
| F2 | NASA base imagery | 4 Mars Trek WMTS basemaps (Viking, CTX regional, CTX TRN, HiRISE) | LS › Layers › "NASA base imagery" | `addNasaLayers()`, `nasaTileLayer()`, `layerRow()` `js/map.js:125-169`. Config `NASA.basemaps` in `js/nasa-config.js:35-88` | Mars Trek (external) | checkbox → tile layer | IMPLEMENTED |
| F3 | NASA science overlays | MOLA shaded relief and THEMIS day IR, with opacity slider | LS › Layers › "Data layers" | same as F2. Config `NASA.overlays` `js/nasa-config.js:91-118` | Mars Trek | checkbox / slider → layer | IMPLEMENTED |
| F4 | Backend raster overlays | DEM-derived image overlays (e.g. slope, hillshade) from `/api/layers` | LS › Layers › "Data layers" | `loadLayers()` `js/map.js:200-254` | `GET /api/layers` | → `L.imageOverlay`s | BACKEND NOT FOUND |
| F5 | Data-unavailable banner | Shows a banner when the backend is offline, bounds are wrong, or the DEM is TEST data | Map top-left | `showBanner()` `js/map.js:255` | — | text | IMPLEMENTED |
| F6 | Landing-site marker | Circle at Octavia E. Butler Landing (sol 13) | Map | `addNasaLayers()` `js/map.js:167-168` | — | — | IMPLEMENTED |
| F7 | Mission stops | Habitat (H) + numbered stops, list in sidebar, click to select | Map + LS › Layers › "Mission stops" | `loadStops()` `js/map.js:260-281` | `GET /api/stops`, falls back to `data/stops.json` | → markers, `AppState.stops` | IMPLEMENTED (via fallback) |
| F8 | Perseverance traverse | Rover drive lines + end-of-drive waypoints (708 points, 559 drives) | Map + Layers list | `loadTraverse()` `js/map.js:284-320` | Local JSON, then mars.nasa.gov | → GeoJSON layers, `roverWaypoints[]` | IMPLEMENTED |
| F9 | Cursor coordinates | Live lat/lon readout | Map bottom-right | `map.on("mousemove")` `js/map.js:123` | — | mouse → text | IMPLEMENTED |
| F10 | Mars scale bar | "100 px ≈ X (Mars)" via haversine on the Mars sphere | Map bottom-right | `updateScale()`, `marsDistance()` `js/map.js:65-69,182-187` | — | — | IMPLEMENTED |
| F11 | Reset map view | Fits habitat + stops, clears selection | LS › Layers | `$("btnReset").onclick` `js/map.js:189-194` | — | — | IMPLEMENTED |
| F12 | Point selection / location info | Click map or stop → elevation, slope, source, reason | RP | `select(pt)` `js/map.js:333-353`. Renderer `onState(["selected"])` `js/map.js:357-381` | `GET /api/elevation` | `{lat,lon}` → `AppState.selected` | BACKEND NOT FOUND. Offline fallback IMPLEMENTED |
| F13 | Offline rover-elevation fallback | If the backend fails and the click is ≤ 40 m from a rover waypoint, shows the rover's `elev_geoid` | RP | `nearestWaypoint()` `js/map.js:323-330`, `select()` catch block | — | → elevation (no slope) | IMPLEMENTED |
| F14 | Rover photos at stops | Shows `photos[]` for a stop, or 3 "not added yet" placeholders | RP | `js/map.js:374-380` | stops data | — | PLACEHOLDER (`photos: []` in `data/stops.json`) |
| F15 | Route A (hand-drawn) | Click to add, drag to move, right-click or list button to delete, double-click or button to finish | LS › Route + map | `startDrawing`, `finishDrawing`, `addWaypoint`, `removeWaypoint`, `redrawDraft`, `draftPoints` `js/map.js:387-442` | — | clicks → waypoints | IMPLEMENTED |
| F16 | Route A analysis | Sends waypoints for terrain analysis, draws slope-coloured route | Map + BP | `analyzeA()` `js/map.js:431-439`, `drawRoute("A")` | `POST /api/route/analyze` | waypoints → RouteAnalysis | BACKEND NOT FOUND |
| F17 | Route B (auto-route) | Hazard-avoiding route habitat → stops, plus return leg | LS › Route | `$("btnAuto").onclick` `js/map.js:464-480` | `POST /api/route/auto` (×2) | stops + max_slope → RouteAnalysis + waypoints | BACKEND NOT FOUND |
| F18 | Max walkable slope slider | 5–25°, default 15. Recolours routes; sent to auto-route | LS › Route | `$("slope").oninput` + `onState(["maxSlope"])` `js/map.js:488-492` | Passed to `/api/route/auto` only | number → `AppState.maxSlope` | IMPLEMENTED (client side) |
| F19 | Slope-coloured route rendering | Segment colours: grey = no data, orange = over limit, yellow = over 8°, else blue (B) / light grey (A) | Map | `drawRoute(id, analysis)` `js/map.js:445-455` | — | profile → polylines | IMPLEMENTED |
| F20 | Elevation profile chart | Chart.js line chart of elevation vs distance for A and B | BP › "Elevation profile" | `onState(["routeA","routeB"])` `js/map.js:496-509` | — | profiles → chart | IMPLEMENTED (needs data) |
| F21 | Route comparison table | Distance, gain/loss, max/avg slope, data coverage for A vs B + text note | BP › "Compare routes" | `js/map.js:511-530` | — | — | IMPLEMENTED (needs data) |
| F22 | EVA time budget bar | Out / Science / Back / Reserve segments, suit-limit check | BP › "EVA budget" | `onState(["eva"])` `js/map.js:532-541` | `POST /api/eva` | → `AppState.eva` | BACKEND NOT FOUND |
| F23 | Radiation dose estimate | "Dose estimate ≈ X mGy (model estimate)" | BP under EVA bar | `js/map.js:540` | `/api/eva` (`dose_estimate_mgy`, `dose_note`) | — | BACKEND NOT FOUND |
| F24 | Radiation reference card | Static text: 0.21 mGy/day GCR measured at Gale crater (Curiosity RAD) | LS › Environment | `index.html:96-99` | — | — | IMPLEMENTED (static text) |
| F25 | Space-weather badge | NASA DONKI flares + SEP events over 7 days; status class | TB `#sw` | `loadSpaceWeather()` `js/map.js:544-554` | `GET /api/spaceweather` | → badge | BACKEND NOT FOUND |
| F26 | Notifications | Bell with count; severities `info`, `warning`, `unavailable`, `done`; dismiss / clear all | TB bell | `notify()`, `renderNotifications()` `js/map.js:78-104` | — | — | IMPLEMENTED |
| F27 | AI assistant drawer | Chat log, 3 suggestion chips, sends question + map context | TB "AI assistant" → drawer `#chat` | `askAssistant()`, `chatContext()`, `addMsg()` `js/map.js:559-587` | `POST /api/chat` | question + context → answer + sources | PLACEHOLDER |
| F28 | Environment (MEDA) dashboard | Table of sol, LMST, air temp, pressure, wind | LS › Environment | `loadEnvironment()` `js/map.js:590-599` | `GET /api/meda` | — | PLACEHOLDER |
| F29 | Mission log: save | Saves the selected point (and route summary) as a record | RP "Save to log" | `$("btnSaveRecord").onclick` `js/map.js:602-614` | `POST /api/records` | record → — | PLACEHOLDER |
| F30 | Mission log: list / search / open | Search box; clicking a record pans the map and selects the point | LS › Mission log | `loadRecords(q)` `js/map.js:615-631` | `GET /api/records?q=` | — | PLACEHOLDER |
| F31 | Sidebar tabs | Layers / Route / Environment / Mission log | LS | `js/map.js:645-651` | — | — | IMPLEMENTED |
| F32 | 2D ↔ 3D mode switch | Shows and hides the views; lazily inits 3D; syncs selection | TB "2D Map / 3D Globe" | `setView()` in `js/integration.js:136-162` (global `window.setView`) | — | — | IMPLEMENTED (sync partly BROKEN, §13) |
| F33 | 3D globe scene | Global Mars + Jezero terrain + imagery, stars, no atmosphere | 3D | `createMarsScene()` `js/mars-scene.js` | Esri astro services + local tiles | — | IMPLEMENTED |
| F34 | 3D flights | Global → Jezero animated flight, back to global, go-to-location | 3D top bar / CTA | `createCameraControls()` `js/camera-controls.js` | — | — | IMPLEMENTED |
| F35 | 3D zoom / reset / auto-spin | +, −, ⟲ buttons; slow spin until interaction | 3D | `zoomIn/zoomOut/reset/startSpin` | — | — | IMPLEMENTED |
| F36 | 3D layer toggles | Basemap radio (MDIM / colour relief), elevation, CTX, HiRISE, marker, footprints | 3D left panel | `wireControls()` `js/app.js:134-184`; `Mars3D.setVisibleLayer`, `setBasemap` | — | — | IMPLEMENTED |
| F37 | 3D click-to-inspect | Lat/lon + elevation from every covering DEM (HiRISE, CTX, global) | 3D right panel | `createLocationSelection().inspect()` `js/location-selection.js:32-54` | — (in-browser `queryElevation`) | click → `{latitude, longitude, elevations[]}` | IMPLEMENTED |
| F38 | 3D active-sources list | Datasets that contribute at the current camera position | 3D right panel | `renderSources()` `js/app.js:68-90` | — | — | IMPLEMENTED |
| F39 | 3D Jezero overlays | IAU centre marker, nominal rim circle (47.52 km), data-coverage outlines | 3D | `createJezeroOverlays()` `js/jezero-region.js` | — | — | IMPLEMENTED |
| F40 | Route display on 3D globe | Draw routes A and B as polylines on the globe | 3D | `drawRoute3D()` `js/integration.js:95-124` | — | — | **BROKEN** (§13) |
| F41 | Terrain/imagery tile preparation | Converts USGS/JPL GeoTIFFs to Terrain-RGB and imagery tiles + manifest | Offline CLI | `scripts/prepare-terrain/prepare_terrain.py` (`main()`) | — | GeoTIFF → tiles | IMPLEMENTED (offline tool) |
| F42 | Rover data download | Saves M20 waypoints and traverse JSON locally | Offline CLI | `nasa/tools/fetch_nasa_data.py` | mars.nasa.gov | → `nasa/data/*.json` | IMPLEMENTED (writes to `nasa/data/`, not `data/`) |
| F43 | Legacy standalone 2D planner | Earlier copy of the 2D app with a 3D placeholder | `nasa/index.html` | `nasa/JS/map.js` | same `/api/*` | — | Legacy duplicate (not used by root `index.html`) |

---

## 3. Backend and API inventory

### 3.0 Summary

| Item | Finding |
| --- | --- |
| Backend server code | **NOT FOUND** |
| OpenAPI / Swagger | **NOT FOUND** |
| API base URL | `const API = ""` (`js/map.js:28`), i.e. same origin as the page. Comment: use `"http://localhost:8000"` if the page is served elsewhere |
| HTTP helper | `api(path, body)` `js/map.js:54-63`. GET when `body` is absent; POST with `Content-Type: application/json` otherwise. Throws `ApiError(status, message)` |
| Error body parsing | `msg = j.detail?.error?.message \|\| j.detail \|\| r.statusText`. This matches FastAPI's `HTTPException(detail=…)` convention (**INFERRED**, not confirmed) |
| Auth | None. No headers, no tokens (§12.4) |
| Framework | **UNKNOWN**. Hints: FastAPI-style `detail`, port 8000 in the comment, "SQLite" in the `/api/records` comment (`js/map.js:601`), ".env on the server" for the chat key (`js/map.js:580`) |

Every endpoint below is in **FRONTEND-ONLY / BACKEND NOT FOUND** status. Request bodies are what the frontend sends. Response schemas list **the fields the frontend reads**. A backend may return more.

The schemas use TypeScript-style notation. `?` means optional or nullable as far as the frontend handles it.

---

### 3.1 `GET /api/layers` — backend raster overlays + DEM metadata

* **Caller:** `loadLayers()` `js/map.js:200-254`. Runs once at start-up.
* **Parameters:** none.
* **Response (expected):**

```ts
{
  bounds: [[south: number, west: number], [north: number, east: number]], // Leaflet LatLngBounds, planetocentric, E-positive
  dem: string,                    // DEM description; shown in attribution. If it starts with "TEST", a warning banner appears
  layers: Array<{
    id: string,                   // e.g. "slope" (gets default opacity 0.8)
    name: string,
    url?: string,                 // image URL for L.imageOverlay (required when available !== false)
    available?: boolean,          // false → greyed "unavailable" row with `reason`
    reason?: string,
    opacity?: number,             // 0..1
    default_on?: boolean,
    source: string,               // starts with "TEST" → "test data" tag
    description?: string, mission?: string, resolution_m?: number, units?: string,
    observation_date?: string, limitations?: string,
    legend?: string               // legend image URL
  }>
}
```

* **Client-side validation:** `bounds` must intersect `NASA.jezeroBounds` = `[[18.2110274, 77.1604655], [18.7211945, 77.6991776]]`. Otherwise the overlays are hidden and a banner explains the coordinate problem (`js/map.js:208-213`).
* **On error:** banner "Backend offline: showing NASA imagery and rover data only…". The app continues.
* **Example response (illustrative, not from a real server):**

```json
{"bounds":[[18.30,77.22],[18.67,77.58]],"dem":"Mars 2020 TRN HiRISE DTM 1 m",
 "layers":[{"id":"slope","name":"Slope (°)","url":"/static/slope.png","default_on":true,"source":"derived from HiRISE DTM","units":"degrees"}]}
```

### 3.2 `GET /api/stops` — habitat and mission stops

* **Caller:** `loadStops()` `js/map.js:260-281`. Runs after `loadLayers()`.
* **Fallback:** on any error, it loads `data/stops.json` via `fetchFirst([NASA.stopsFile])`. **This is what runs today.**
* **Response schema** (same as `data/stops.json`):

```ts
{
  _note?: string,                  // when present → "illustrative placeholder" label + info notification
  coordinate_system?: string,
  habitat: Stop,
  stops: Stop[]
}
type Stop = {
  id: string,                      // "H", "1", "2", …  (used as the marker label)
  name: string, lat: number, lon: number,
  reason?: string, source?: string,           // source = URL
  rover_sol?: number, rover_elev_geoid_m?: number,
  photos?: Array<{ url: string, sol: number, camera: string, dist_m?: number, credit?: string }>
}
```

* **Client-side validation:** stops outside `NASA.jezeroBounds` trigger a warning notification (lat/lon order or longitude sign check).
* **Example:** see `data/stops.json` (habitat at 18.451331 N, 77.401257 E plus 3 stops).

### 3.3 `GET /api/elevation?lat={lat}&lon={lon}` — point elevation and slope

* **Caller:** `select(pt)` `js/map.js:333-353`. Triggered by a map click (when not drawing), a stop click, a mission-log record click, or `window.selectPoint2D()`.
* **Query parameters:** `lat` (number, planetocentric °N), `lon` (number, °E, −180…180). Both required. They are not URL-encoded, just interpolated.
* **Response (expected):**

```ts
{
  elevation_m: number | null,      // metres relative to the areoid (by convention elsewhere in the app)
  slope_deg: number | null,
  source_dataset: string,          // if it starts with "Perseverance" the UI labels the value "rover-measured", else "DEM"
  data_available: boolean,         // false → "unavailable" notification with no_data_reason
  no_data_reason?: string
}
```

The response is merged as `{...response, ...pt}`, so `pt.name`, `reason`, `kind` and `photos` win over response fields.
* **Offline fallback:** finds the nearest rover waypoint within `NASA.rover.nearWaypoint_m` = 40 m. If one exists, it shows `elev_geoid` from `M20_waypoints.json` with `slope_deg: null`.
* **Example:** `GET /api/elevation?lat=18.4589&lon=77.4062` → `{"elevation_m":-2520.9,"slope_deg":4.2,"source_dataset":"HiRISE DTM","data_available":true}` (illustrative).

### 3.4 `POST /api/route/analyze` — analyse a given polyline (Route A)

* **Caller:** `analyzeA()` `js/map.js:431-439`. Runs after "Finish route" or after dragging or deleting a waypoint.
* **Request body:**

```json
{ "waypoints": [{"lat": 18.4513, "lon": 77.4013}, {"lat": 18.4589, "lon": 77.4062}], "route_id": "A" }
```

  Waypoints must have ≥ 2 points; the client enforces this in `finishDrawing()`. **The `maxSlope` slider is NOT sent.** `over_limit` must therefore come from a backend-side limit (**UNKNOWN** which).
* **Response — `RouteAnalysis` (shared with 3.5):**

```ts
type RouteAnalysis = {
  profile: Array<{ lat: number, lon: number, dist_m: number, elevation_m: number | null, slope_deg: number | null }>,
  distance_m: number,
  elevation_gain_m: number,
  elevation_loss_m: number,
  max_slope_deg: number | null,
  avg_slope_deg: number | null,
  over_limit: boolean,
  data_coverage_pct: number,       // < 100 → "unavailable" notification
  method: string,                  // shown under the profile chart
  source_dataset: string,
  waypoints?: Array<{lat, lon}>    // NOT read for route A in map.js; integration.js reads routeA.waypoints for 3D (see §13)
}
```

* **Errors:** any failure → notification "Route A analysis failed: …".

### 3.5 `POST /api/route/auto` — automatic hazard-avoiding route (Route B)

* **Caller:** `$("btnAuto").onclick` `js/map.js:464-480`. **Called twice:**
  1. Outbound: `{ stops: [habitat, ...stops], max_slope }`
  2. Return: `{ stops: [lastStop, habitat], max_slope }`
* **Request body:**

```ts
{ stops: Stop[] /* full stop objects incl. id, name, lat, lon, reason… */, max_slope: number /* degrees, 5..25 */ }
```

* **Response:** `RouteAnalysis` **plus** `waypoints: Array<{lat, lon}>` (required: it is passed to `/api/eva`). Only the outbound route is drawn and stored as `AppState.routeB`. The return route is used only for EVA.
* **Errors:** notification "Auto-route failed: …". The button is re-enabled in `finally`.

### 3.6 `POST /api/eva` — EVA time budget and dose

* **Caller:** same handler, after both auto-routes.
* **Request body:**

```ts
{ out_waypoints: Array<{lat,lon}>, back_waypoints: Array<{lat,lon}>, n_stops: number }
```

* **Response (expected):**

```ts
{
  walk_out_min: number, science_min: number, walk_back_min: number,
  reserve_min: number,             // negative → "Over suit limit by …"
  suit_limit_min: number,
  status: "ok" | string,           // anything other than "ok" → warning + red reserve segment
  label: string,                   // shown next to "EVA budget"
  dose_estimate_mgy: number,
  dose_note: string
}
```

* **Example (illustrative):** `{"walk_out_min":95,"science_min":120,"walk_back_min":60,"reserve_min":85,"suit_limit_min":480,"status":"ok","label":"walking speed model X","dose_estimate_mgy":0.07,"dose_note":"scaled from Curiosity RAD 0.21 mGy/day"}`

### 3.7 `GET /api/spaceweather` — NASA DONKI status

* **Caller:** `loadSpaceWeather()` `js/map.js:544-554`. Runs once at start-up.
* **Response (expected):**

```ts
{
  status: "unknown" | "caution" | string,   // used as a CSS class on #sw; other values UNKNOWN (CSS: see css/nasa-style.css)
  flares: any[],                            // only .length is used
  sep_events: any[],                        // only .length is used
  cached?: boolean,                         // → "· saved copy"
  rule: string,                             // tooltip
  source?: string                           // default "NASA DONKI"
}
```

* `status === "caution"` raises a warning notification. `"unknown"` shows "unavailable".

### 3.8 `POST /api/chat` — AI assistant (**the main integration point**)

* **Caller:** `askAssistant(q)` `js/map.js:570-582`, from the chat form or the suggestion chips.
* **Request body:**

```ts
{
  question: string,
  context: {                                 // built by chatContext() js/map.js:559-568
    selected: null | { lat, lon, name?, elevation_m, slope_deg, source },
    active_layers: string[],                 // ids of BACKEND overlays (/api/layers) currently on the map; NOT NASA layers
    route_a: null | { distance_m, max_slope_deg, gain_m },
    route_b: null | { distance_m, max_slope_deg, gain_m },
    eva: null | <the /api/eva response>,
    max_slope_deg: number,
    dem: string | undefined                  // AppState.layers?.dem
  }
}
```

* **Response (expected):** `{ answer: string, sources?: Array<{ title: string, url: string }> }`. `answer` is HTML-escaped and shown as plain text; there is no Markdown rendering.
* **Error handling already in the UI:**

| HTTP status | UI message |
| --- | --- |
| 404, 405, 501 | "The AI assistant isn't connected yet (backend route /api/chat not built)…" |
| 503 | "The AI assistant needs an API key. Add it to .env on the server." |
| other | "Assistant error: {message}" |

* **Not supported today:** streaming, conversation history (each request is stateless), tool/action calls back into the UI, rich content.

**Example request:**

```json
{"question":"Explain the slope on this route",
 "context":{"selected":{"lat":18.4589,"lon":77.4062,"name":"Delta front · Hawksbill Gap","elevation_m":-2520.9,"slope_deg":4.2,"source":"HiRISE DTM"},
  "active_layers":["slope"],"route_a":null,"route_b":{"distance_m":1850,"max_slope_deg":12.4,"gain_m":31},
  "eva":null,"max_slope_deg":15,"dem":"HiRISE DTM"}}
```

### 3.9 `GET /api/meda` — Perseverance MEDA weather

* **Caller:** `loadEnvironment()` `js/map.js:590-599`. Runs each time the Environment tab opens.
* **Response (expected):**

```ts
{ sol_range?: any /* not read */, rows: Array<{ sol: number, lmst: string, air_temp_c?: number, pressure_pa?: number, wind_ms?: number }>, source: string }
```

  Only the first 12 rows are shown. Errors are silently ignored and the placeholder stays.

### 3.10 `POST /api/records` and `GET /api/records?q=` — mission log

* **Save caller:** `$("btnSaveRecord").onclick` `js/map.js:602-614`.
* **Save body:**

```ts
{
  label: string,                    // selected.name || "Map point"
  lat: number, lon: number,
  analysis_type: "route" | "location",   // "route" if routeA or routeB exists
  result: string,                   // human-readable summary
  elevation_m: number | null, slope_deg: number | null,
  dataset: string,                  // selected.source_dataset
  status: "complete" | "data unavailable"
}
```

  The response body is ignored. 404/405 → "Mission log not connected yet".
* **List caller:** `loadRecords(q)` `js/map.js:615-623`.
* **List response:** `Array<{ label, lat, lon, analysis_type, result, created_at?, status }>`.
* Clicking a record calls `map.setView([lat, lon], max(zoom, 14))` and then `select({lat, lon, kind:"point"})`.
* Storage: the comment says "Integration role, SQLite" (**INFERRED**; nothing implemented).

### 3.11 Static "endpoints" actually used today (served by the static server)

| Path | Used by | Purpose |
| --- | --- | --- |
| `data/stops.json` | `loadStops()` fallback | Habitat and stops |
| `data/M20_waypoints.json`, `data/M20_traverse.json` | `loadTraverse()` via `NASA.rover.*` | Rover data (local first) |
| `data/processed/manifest.json` | `loadManifest()` `js/terrain-loader.js:27` | 3D tile index |
| `data/processed/terrain/{ctx,hirise}/{L}/{row}/{col}.png` | `createLocalElevationLayer()` | 3D terrain tiles (**missing on disk**) |
| `data/processed/imagery/{ctx,hirise}/{L}/{row}/{col}.{jpg,png}` | `createLocalImageryLayer()` | 3D imagery tiles (present) |

### 3.12 In-browser JavaScript API (the only real, callable "API" today)

`Mars3D` (`js/mars3d.js`, also `window.Mars3D`). Fully documented in `docs/INTEGRATION_GUIDE.md` §3:

| Method | Signature | Returns |
| --- | --- | --- |
| `init` | `(container: HTMLElement\|string, {dataUrl?, sdkVersion?, autoSpin?=true, camera?, quality?="high"})` | `Promise<Mars3D>`. Throws if already initialised |
| `destroy` | `()` | void |
| `isInitialized` | `()` | boolean |
| `goToJezero` | `({duration?=6500})` | Promise |
| `goToGlobal` | `({duration?=4000})` | Promise |
| `goToLocation` | `(lat, lon, {altitude?=25000, tilt?=50, heading?=0, duration?=4000, select?=true})` | Promise. Also emits `select` unless `select:false` |
| `setVisibleLayer` | `(layerId, visible)` | boolean (false = unknown id) |
| `setBasemap` | `("mdim"\|"colordem")` | void |
| `getLayers` | `()` | `[{id, kind, title, detail, scope, bounds, footprint, visible, failed, controlled}]` |
| `zoomIn` / `zoomOut` / `resetCamera` | `()` | void |
| `getRegion` | `()` | `"global"\|"jezero"` |
| `getView` / `getManifest` | `()` | SceneView / manifest object or null |
| `on` | `(event, cb)` | unsubscribe function. Events: `status`, `region`, `camera`, `select`, `layers` |
| `JEZERO` | constant | `{name, center:{lon,lat}, diameterKm, studyArea:[w,s,e,n], source}` |

---

## 4. Map and geospatial functionality

### 4.1 Map libraries

| View | Library | Instance | File |
| --- | --- | --- | --- |
| 2D | Leaflet 1.9.4 | `map` (top-level `const`, also `window.map`) | `js/map.js:110-116` |
| 3D | ArcGIS Maps SDK 5.1 `SceneView` (`viewingMode: "global"`) | `Mars3D.getView()` | `js/mars-scene.js:101-116` |

### 4.2 Coordinate reference systems

| Context | CRS | Notes |
| --- | --- | --- |
| All app coordinates | **Planetocentric latitude, east-positive longitude, −180…180** | Stated in `js/nasa-config.js:8-16`, `docs/DATA_SOURCES.md` |
| 2D Leaflet | `L.CRS.EPSG4326` (plain lon/lat degrees) | Mars Trek "EQ" tiles use the same grid (origin −180/90, 2×1 tiles at z0). Body: IAU2000:49900 sphere, R = 3 396 190 m |
| 3D | **GCS_Mars_2000_Sphere, WKID 104971**, R = 3 396 190 m | `MARS_WKID` in `js/map-layers.js:11` |
| 3D tiling | 512 px tiles, origin (−180, 90), L0 = 0.3515625°/px, halves per level | `createMarsTileInfo()` `js/map-layers.js:66-84` |
| Vertical | Metres relative to the Mars areoid (MOLA geoid), no exaggeration | `manifest.json › tilingScheme.verticalDatum` |
| Raw rasters | Equirectangular on the Mars sphere (CTX: std parallel 18.4663°; HiRISE: 0°) | `docs/DATA_SOURCES.md` |
| Rover JSON | lon, lat, plus `elev_geoid` (m) and `easting/northing` | `data/M20_waypoints.json` |
| Leaflet bounds format | `[[south, west], [north, east]]` | backend `/api/layers` must follow this |
| 3D bounds format | `[west, south, east, north]` | `manifest.json`, `inBounds()` |

> ⚠ The two bounds formats differ in order. Any assistant tool that passes bounds must state which one it uses.

**Distance on Mars:** `marsDistance(a, b)` (`js/map.js:65-69`) is a haversine with R = 3 396 190 m. It takes `{lat, lng}` (Leaflet naming). The code comments say it is "display only" (scale bar and nearest-waypoint lookup). Route distances must come from the backend.

### 4.3 Supported layers

**2D (Leaflet)** — config in `js/nasa-config.js`, objects in `nasaLayers[id]` (top-level `const`):

| id | Name | Pane | Default | maxNativeZoom | Bounds |
| --- | --- | --- | --- | --- | --- |
| `viking` | Viking colour mosaic, 232 m/px | `nasaBase` (z150) | on | 7 | global |
| `ctx_regional` | CTX Jezero region, ~6 m/px | `nasaBase` | on | 12 | 15.91–19.45°N, 75.15–78.87°E |
| `ctx_trn` | CTX orthomosaic, Mars 2020 TRN, 6 m/px | `nasaBase` | on | 13 | 18.21–18.72°N, 77.16–77.70°E |
| `hirise` | HiRISE orthomosaic, 25 cm/px | `nasaBase` | on | 17 | 18.31–18.67°N, 77.22–77.58°E |
| `mola` | MOLA shaded elevation, 463 m/px | `nasaOver` (z250) | off, opacity 0.5 | 7 | global |
| `themis` | THEMIS day IR, 100 m/px | `nasaOver` | off, opacity 0.6 | 9 | 65°S–65°N |
| *(backend)* `overlays[id]` | from `/api/layers` | `overlayPane` (z400) | per `default_on` | — | `meta.bounds` |
| Perseverance traverse | GeoJSON lines + circle markers | default | on | — | — (layer group is a **local variable**; not addressable) |
| Stops / habitat markers | `L.marker` with `divIcon` | default | on | — | not stored in a variable |
| Route A / B | `routeLayers.A`, `routeLayers.B` (`L.layerGroup`) | default | — | — | — |
| Landing marker | `L.circleMarker` | default | on | — | not stored |

**3D (ArcGIS)** — registry ids (`js/mars-scene.js`), toggled with `Mars3D.setVisibleLayer(id, bool)`:

| id | kind | Source | Notes |
| --- | --- | --- | --- |
| `global-imagery` | imagery (basemap) | Esri `OnMars/MDIM` | capped at cached L7, deeper tiles cut in the browser |
| `global-colordem` | imagery (basemap) | Esri `OnMars/MColorDEM` | hidden by default; `setBasemap("colordem")` |
| `global-elevation` | elevation (ground) | Esri `OnMars/MDEM200M` ImageServer | |
| `ctx-dem` | elevation | local `terrain/ctx` | only if in manifest. **Tiles missing on disk** |
| `hirise-dtm` | elevation | local `terrain/hirise` | only if in manifest. **Tiles missing on disk** |
| `ctx-ortho` | imagery (draped) | local `imagery/ctx` L7–12 | present (1 961 tiles) |
| `hirise-imagery` | imagery (draped) | local `imagery/hirise` L12–15 (controlled), or the Esri HiRISE fallback | present (4 092 tiles) |
| `jezero-marker`, `jezero-rim` | overlay | graphics | the UI checkbox toggles both |
| `footprints` | overlay | graphics from the manifest footprints | |
| `selection` | graphics layer | pin | not in the registry |

### 4.4 How layers are loaded

* **2D NASA tiles:** `addNasaLayers()` creates `L.tileLayer(cfg.url, {bounds, maxNativeZoom, noWrap, crossOrigin, errorTileUrl: 1×1 gif})`. Tile errors are ignored ("missing tile = no coverage").
* **2D backend overlays:** `loadLayers()` → `L.imageOverlay(l.url, meta.bounds)`. These are single images, not tiles.
* **2D rover data:** `fetchFirst([local, live])` tries the local file first, then mars.nasa.gov.
* **3D:** `Mars3D.init()` → `loadSdk()` → `loadManifest(dataUrl)` → `createMarsScene()`. Local layers are custom `BaseElevationLayer` / `BaseTileLayer` subclasses that consult the manifest tile index before fetching. Elevation tiles are Terrain-RGB PNGs decoded in `decodeTerrainRgb()`: `h = (R·65536 + G·256 + B)·0.1 − 10000`, alpha 0 = no-data. Missing elevation tiles fall back HiRISE → CTX → global (`fetchFromFallback`).

### 4.5 Markers and points of interest

| Item | Function | Code |
| --- | --- | --- |
| Pin icon factory | `pin(label, cls)` → `L.divIcon` with `<div class="pin {cls}">` | `js/map.js:258` |
| Habitat | `L.marker(..., pin("H","square"))` → click → `select({...habitat, kind:"habitat"})` | `js/map.js:266-267` |
| Stops | `L.marker(..., pin(st.id))` → click → `select({...st, kind:"stop"})` | `js/map.js:271` |
| Selected point | `selMarker = L.circleMarker` (only for `kind === "point"`) | `js/map.js:362` |
| Route A waypoints | draggable `L.marker` with `pin(i+1,"wp")` | `addWaypoint()` `js/map.js:398-405` |
| 3D pin | `GraphicsLayer "selection"` point-3d symbol | `js/location-selection.js:16-35` |
| 3D Jezero marker | point-3d icon + text | `js/jezero-region.js:54-88` |

There is **no generic "add POI" function**. Stops come only from `/api/stops` or `data/stops.json`.

### 4.6 How routes are drawn

`drawRoute(id: "A"|"B", analysis: RouteAnalysis)` (`js/map.js:445-455`):
* Clears `routeLayers[id]`, then draws one `L.polyline` **per profile segment** `profile[i-1] → profile[i]`.
* Colour by `profile[i].slope_deg`: `null` → `#9AA6B6`; `> AppState.maxSlope` → `#F97316`; `> SLOPE_CAUTION (8)` → `#F2C14E`; else `#5EC8F2` (B) or `#C9D2DD` (A).
* Weight 5 (B) or 3 (A). A is dashed `"8 6"`.
* Re-run automatically when `maxSlope` changes (`onState(["maxSlope"])`).
* The draft (unanalysed) Route A is a separate dashed yellow polyline `draftLine`.

**The frontend gets route geometry only from the backend `profile` array.** No client-side routing exists.

### 4.7 Zoom and navigation

| View | Mechanism |
| --- | --- |
| 2D | Leaflet defaults (wheel, drag, double-click zoom, which is disabled while drawing). `minZoom 3`, `maxZoom 19`, `zoomSnap 0.25`, `maxBounds` = whole planet. Start view: `NASA.startView = {lat:18.4545, lon:77.4040, zoom:15}`. `btnReset` fits the habitat + stops with `pad(0.6)`. Programmatic: `map.setView`, `map.flyTo`, `map.fitBounds`, `map.panTo`, `map.zoomIn/Out` (standard Leaflet) |
| 3D | `Mars3D.goToJezero/goToGlobal/goToLocation/zoomIn/zoomOut/resetCamera`. Region auto-detected: altitude < 800 km and inside the study area ±1.5° → `"jezero"` |

### 4.8 How map state is stored

* 2D: Leaflet holds view state (`map.getCenter()`, `map.getZoom()`, `map.getBounds()`, `map.hasLayer(l)`). App state is in `AppState` (§10). **Nothing is persisted**: no URL hash, no localStorage.
* 3D: inside the module-private `state` object of `js/mars3d.js`. Readable via `getView().camera`, `getRegion()`, `getLayers()` and the `camera` event.
* Cross-view: `SharedState` in `js/integration.js` (module-private, **not exported**).

### 4.9 Can the map be controlled through a frontend action interface?

**Partially.** There is no formal action or command interface, but these handles are reachable from any script on the page:

| Handle | How reachable | What it allows |
| --- | --- | --- |
| `window.map` | explicit global (`js/map.js:116`) | any Leaflet operation |
| `window.select(pt)` | explicit global (`js/map.js:384`) | select a point → elevation lookup → right panel |
| `window.selectPoint2D(lat, lon)` | explicit global (`js/integration.js:168`) | pan to zoom ≥ 14 + select |
| `window.setView("2d"\|"3d")` | explicit global (`js/integration.js:165`) | switch view |
| `window.Mars3D` | explicit global (`js/mars3d.js:233`) | full 3D API |
| `AppState`, `setState`, `onState`, `nasaLayers`, `overlays`, `routeLayers`, `notifications` | top-level `const` / `function` in the **classic script** `js/map.js`. Reachable **by bare name** from any other script or module on the page. `const`s are **not** `window.*` properties; `function` declarations are (`window.setState`, `window.onState`, `window.notify`, `window.drawRoute`, `window.analyzeA`, `window.askAssistant`, `window.chatContext`, `window.loadRecords`, `window.startDrawing`, `window.finishDrawing`, `window.addWaypoint`, …) | read and modify state; reuse internal functions |

**Functions that could be exposed to an AI assistant** (exact names):
`select`, `setState`, `onState`, `notify`, `drawRoute`, `analyzeA`, `addWaypoint`, `removeWaypoint`, `startDrawing`, `finishDrawing`, `draftPoints`, `chatContext`, `askAssistant`, `loadRecords`, `loadEnvironment`, `loadSpaceWeather`, `fmtKm`, `marsDistance`, `nearestWaypoint`, the `btnAuto`/`btnClear`/`btnReset`/`btnSaveRecord` click handlers (`$("btnAuto").onclick()`), `window.setView`, `window.selectPoint2D`, and every `Mars3D.*` method.

Not exposed (closure-local or anonymous): the Perseverance traverse layer group, stop markers, the landing marker, `chart`, `selMarker`, `wpMarkers` (a `let`, but still script-global and reachable by name), `drawing` (`let`, same), and `SharedState` / `drawRoute3D` / `clearRoutes3D` in `integration.js`.

---

## 5. Route generation and evaluation

### 5.1 What is implemented vs proposed

| Aspect | Implemented in this repo? | Where / notes |
| --- | --- | --- |
| Hand-drawn route capture | **Yes** | `js/map.js:387-442` |
| Route-generation (pathfinding) algorithm | **NOT FOUND** | expected behind `POST /api/route/auto`. The UI label "Auto-route around hazards" implies slope-constrained pathfinding (**INFERRED**). Algorithm: **UNKNOWN** |
| Route profile sampling, distance, slope, gain/loss | **NOT FOUND** (backend) | the client only renders `profile`, `distance_m`, … |
| Travel-time model | **NOT FOUND** (backend `/api/eva`) | fields `walk_out_min`, `walk_back_min`, `science_min`, `suit_limit_min`, `reserve_min` |
| Slope classification for display | **Yes** | thresholds 8° (`SLOPE_CAUTION`, comment "match backend config") and `AppState.maxSlope` |
| Route comparison | **Yes** (display logic) | `js/map.js:511-530` |
| Alternative routes | **Partial**: exactly two, A (manual) vs B (auto). No k-alternatives, no saved variants | — |
| Return-leg routing | Requested (`[lastStop, habitat]`) but **not drawn** | `js/map.js:471` |
| 3D route visualisation | **BROKEN** | `drawRoute3D()` `js/integration.js:95-124` |

### 5.2 Workflow (as coded)

```
Route A (manual)
  btnDraw → startDrawing()  (clears A, disables dblclick zoom)
  map click → addWaypoint(latlng)  (draggable, right-click deletes)
  btnDraw / dblclick → finishDrawing() → needs ≥ 2 points
     → analyzeA() → POST /api/route/analyze {waypoints, route_id:"A"}
     → drawRoute("A", a) → setState({routeA:a}) → routeNotifications(a,"Route A")
  later drag/delete → analyzeA() again

Route B (auto)
  btnAuto → requires AppState.stops
     → POST /api/route/auto {stops:[habitat,...stops], max_slope}     = out
     → POST /api/route/auto {stops:[lastStop, habitat], max_slope}    = back
     → drawRoute("B", out)
     → POST /api/eva {out_waypoints: out.waypoints, back_waypoints: back.waypoints, n_stops}
     → setState({routeB: out, eva}) → routeNotifications(out,"Route B") → EVA warning if status ≠ "ok"

btnClear → clears both route layers and waypoints → setState({routeA:null, routeB:null, eva:null})
```

### 5.3 Inputs and constraints

| Input | Source | Range / default |
| --- | --- | --- |
| Waypoints | user clicks | ≥ 2, lat/lon degrees |
| Stops | `AppState.stops` | habitat + N stops (3 in `data/stops.json`) |
| `max_slope` | slider `#slope` | 5–25°, default 15 (project-defined, not a NASA standard per the UI label) |
| Caution band | constant `SLOPE_CAUTION = 8` | display only |
| Terrain dataset | backend `/api/layers.dem` | **UNKNOWN**. `nasa/DATA_SOURCES.md` says the backend should use the Mars 2020 TRN CTX DTM (20 m) and HiRISE DTM (1 m) |

### 5.4 Evaluation criteria shown to the user

Distance, elevation gain and loss, max slope (with an over-limit flag), average slope, data coverage %. The comparison note says which route has the lower max slope and which is shorter, and always adds "Preliminary terrain assessment from elevation data only". An `info` notification fires if the distance differs by > 25% or the max slope by > 5°.

### 5.5 Terrain calculations available in-browser (3D only)

* Elevation at a point from each covering DEM: `inspect(lon, lat)` → `layer.queryElevation(point, {demResolution:"finest-contiguous"})` (`js/location-selection.js:41`).
* **No slope computation in the browser** (`integration.js:40` comment: "3D doesn't compute slope yet").
* The 3D DEM layers could support profile sampling with `ElevationLayer.queryElevation(polyline)` (an SDK capability). **Not implemented.**

### 5.6 Limitations

* No routing without the backend.
* The slider value does not reach `/api/route/analyze`, so Route A's `over_limit` may disagree with its colouring.
* The return leg is not visualised.
* Routes are not persisted (except as a text summary through `/api/records`).
* Only one auto route; science-stop order is fixed (habitat → stops in list order → back).

---

## 6. Scientific intelligence engine

| Module | Implementation in repo | Input | Output | Endpoint | Status |
| --- | --- | --- | --- | --- | --- |
| Terrain: point elevation/slope (2D) | none (client calls the backend) | `lat, lon` | `elevation_m, slope_deg, source_dataset, data_available, no_data_reason` | `GET /api/elevation` | BACKEND NOT FOUND |
| Terrain: point elevation (3D, in-browser) | `createLocationSelection().inspect()` `js/location-selection.js` | lon, lat (click or `Mars3D.goToLocation`) | `{latitude, longitude, elevations:[{id,label,value,resolution?,error?}]}` via the `select` event | none (JS) | IMPLEMENTED (CTX/HiRISE values need the missing terrain tiles; global 200 m works) |
| Terrain: rover-measured elevation | `nearestWaypoint()` + `select()` fallback | lat, lon | `elev_geoid` of the nearest waypoint ≤ 40 m | none | IMPLEMENTED |
| Terrain: route analysis | none | waypoints | `RouteAnalysis` | `POST /api/route/analyze` | BACKEND NOT FOUND |
| Terrain: raster derivatives (slope/hillshade maps) | none | — | image overlays | `GET /api/layers` | BACKEND NOT FOUND |
| EVA estimation | none | out/back waypoints, n_stops | time budget | `POST /api/eva` | BACKEND NOT FOUND |
| Environmental analysis (MEDA) | none | — | MEDA rows | `GET /api/meda` | PLACEHOLDER |
| Radiation estimation | static reference text + backend field | — | `dose_estimate_mgy`, `dose_note` | `POST /api/eva` | static text IMPLEMENTED; model BACKEND NOT FOUND |
| Space-weather analysis | none (client renders) | — | status, flares, SEP events, rule | `GET /api/spaceweather` | BACKEND NOT FOUND. Source named as NASA DONKI |
| Scientific interpretation | stop `reason` strings in `data/stops.json` (placeholder text flagged by `_note`) | — | text | — | starter data only |
| AI assistant / LLM | UI only | question + context | answer + sources | `POST /api/chat` | PLACEHOLDER |
| RAG retrieval / scientific documents | **NOT FOUND** | — | — | — | NOT FOUND. Only hint: `sources:[{title,url}]` in the chat response |

---

## 7. Data inventory

### 7.1 Local datasets

| Name | Purpose | Format | Coverage | Resolution | Update freq. | Stored at | Access | Limitations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Perseverance waypoints (MMGIS) | Rover positions, sol, `elev_geoid`, orientation, cumulative distance, panorama refs | GeoJSON FeatureCollection, 708 Point features (sol 13 → sol 2022) | Rover path, Jezero | per drive | snapshot; refresh with `fetch_nasa_data.py` | `data/M20_waypoints.json` (copy in `nasa/data/`) | `fetchFirst(NASA.rover.waypoints)` | `elev_radii`/`radius` can be −9999 (no data); panorama URLs are relative to MMGIS |
| Perseverance traverse | Drive lines | GeoJSON, 559 LineString features with `sol, fromRMC, toRMC, length, SCLK_START/END` | Rover path | per drive | snapshot | `data/M20_traverse.json` | `fetchFirst(NASA.rover.traverse)` | the code drops the third coordinate (elevation) |
| Mission stops | Habitat + 3 stops | JSON (schema §3.2) | Three Forks / delta front (~18.45°N, 77.40°E) | — | manual | `data/stops.json` | fallback for `/api/stops` | `_note`: names/reasons are a **starter set** to confirm; `photos: []`. File contains mis-encoded `Â·` characters |
| 3D manifest | Tile index, bounds, footprints, feathering stats, provenance | JSON v2 (`generated 2026-11-14T14:29:31Z`) | CTX + HiRISE footprints | — | regenerated by script | `data/processed/manifest.json` | `loadManifest()` | — |
| Footprints | Valid-data polygons of the 4 rasters | GeoJSON (MultiPolygon ×4) | — | simplified 0.0015° | regenerated | `data/processed/footprints.geojson` | GIS use only (the app uses the manifest copy) | — |
| CTX ortho tiles | 3D draped imagery | 512 px JPG/PNG, L7–12 | 77.00–78.58°E, 17.58–19.29°N | L12 ≈ 5 m | regenerated | `data/processed/imagery/ctx/` (1 961 tiles) | `createLocalImageryLayer` | greyscale |
| HiRISE ortho tiles | 3D draped imagery | 512 px, L12–15 | 77.22–77.58°E, 18.31–18.67°N | L15 ≈ 0.636 m | regenerated | `data/processed/imagery/hirise/` (4 092 tiles) | same | greyscale; covers ~66% of its bounding box |
| CTX DEM terrain tiles | 3D terrain | Terrain-RGB PNG 257², L6–11 | CTX bounds | L11 ≈ 20 m | regenerated | `data/processed/terrain/ctx/` | `createLocalElevationLayer` | **NOT PRESENT** (git-ignored; 519 tiles listed in the manifest) |
| HiRISE DTM terrain tiles | 3D terrain | Terrain-RGB PNG, L11–14 | HiRISE bounds | L14 ≈ 2.5 m (2 m averaged) | regenerated | `data/processed/terrain/hirise/` | same | **NOT PRESENT** (1 517 tiles listed) |
| Intermediate rasters | Feathered DEMs, global DEM clip | GeoTIFF | region | 20 m / 2 m / ~163 m | — | `data/processed/_work/*.tif` (+ settings JSON) | `prepare_terrain.py` only | git-ignored; **could serve a backend elevation/slope service** |
| Raw CTX DEM | Source DEM | GeoTIFF float32, 4456×5067 | 76.997–78.583°E, 17.581–19.290°N | 20 m | static product | `data/raw/ctx/M20_JezeroCrater_CTXDEM_20m.tif` (90 MB) + PDS XML label | offline only | git-ignored |
| Raw CTX ortho | Source imagery | GeoTIFF uint8 | ≈ same | 5 m | static | `data/raw/ctx/…CTXortho_mosaic_5m.tif` (360 MB) | offline | zeros are gaps |
| Raw HiRISE DTM | Source DEM | GeoTIFF float32, 21400×21488 | 77.223–77.584°E, 18.307–18.669°N | 1 m | static | `data/raw/hirise/…DTM…1m….tif` (1.8 GB) | offline | 66% valid |
| Raw HiRISE ortho | Source imagery | GeoTIFF uint8 | same as DTM | 0.25 m | static | `data/raw/hirise/…orthoMosaic_25cm….tif` (7.4 GB) | offline | — |

### 7.2 External services and APIs

| Name | Purpose | Format | Coverage / resolution | Accessed by | Limitations |
| --- | --- | --- | --- | --- | --- |
| NASA Mars Trek WMTS (`https://trek.nasa.gov/tiles/Mars/EQ/{ID}/1.0.0/default/default028mm/{z}/{y}/{x}.{ext}`) | 2D imagery (6 products, §4.3) | PNG/JPG tiles | per product | Leaflet tile layers | needs internet; the HiRISE mosaic predates landing (no rover tracks) |
| NASA MMGIS (`https://mars.nasa.gov/mmgis-maps/M20/Layers/json/…`) | Live rover data fallback | GeoJSON | rover path | `fetchFirst` second URL | CORS behaviour **UNKNOWN**; the app notes a live load as info |
| Esri `astro.arcgis.com` OnMars services: `MDIM` (MapServer), `MColorDEM` (MapServer), `MDEM200M` (ImageServer), `HiRISE` (MapServer) | 3D global imagery, relief, elevation; HiRISE fallback | Esri tiles (LERC for elevation) | global; MDIM/MColorDEM cached to L7 ≈ 163 m/px; DEM 200 m | ArcGIS SDK | needs internet. The Esri HiRISE mosaic is uncontrolled/beta |
| ArcGIS JS SDK CDN `https://js.arcgis.com/5.1/` | 3D engine | JS | — | `<script type="module">` + `sdk.js` | — |
| unpkg (Leaflet 1.9.4), jsDelivr (Chart.js 4.4.1) | libraries | JS/CSS | — | `<script>` | — |
| USGS Astrogeology S3 (`asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/…`) | Raw raster download | GeoTIFF | — | `download_data.ps1` | ~9 GB |
| NASA DONKI | Space weather | **UNKNOWN** (backend) | — | only via `/api/spaceweather` | not called from the frontend |
| Perseverance MEDA | Weather | **UNKNOWN** (backend) | — | only via `/api/meda` | — |
| Curiosity RAD (Gale crater) | Radiation context: 0.21 mGy/day | static text | Gale crater, not Jezero | `index.html:98` | historical value from another site |

---

## 8. Frontend architecture

### 8.1 Framework and loading

No framework. `index.html` loads, in order:
1. Leaflet CSS, ArcGIS theme CSS, ArcGIS SDK (`type="module"`), Chart.js.
2. `JS/nasa-config.js` (classic) → defines global `const NASA`, `TREK`, `trekUrl`.
3. `JS/map.js` (classic) → the 2D planner. Runs immediately.
4. `js/app.js` (module) → **initialises the 3D scene immediately** in the hidden `#viewDiv` (`main()` at `js/app.js:187-200`).
5. `JS/integration.js` (module) → bridges 2D and 3D, defines `window.setView`, and calls `setView("2d")`.

> ⚠ `index.html` references `JS/…` (uppercase) while the folder is `js/` (lowercase). This works on Windows and macOS (case-insensitive file systems) but **will 404 on Linux servers**. `JS/integration.js` imports `../js/mars3d.js`.

### 8.2 Page structure (main DOM ids)

| Region | Ids |
| --- | --- |
| Top bar | `#missionName`, `#mode2d`, `#mode3d`, `#sw`, `#btnChat`, `#btnBell`, `#bellCount`, `#notifPanel`, `#notifList`, `#btnClearNotifs` |
| Left tabs | `.tab[data-tab=layers\|route\|env\|records]`, panes `#tab-layers`, `#tab-route`, `#tab-env`, `#tab-records` |
| Layers tab | `#basemaps`, `#layers`, `#stops`, `#btnReset` |
| Route tab | `#btnAuto`, `#slope`, `#slopeVal`, `#btnDraw`, `#drawHint`, `#waypointList`, `#btnClear` |
| Env tab | `#envPanel` |
| Records tab | `#recordSearch`, `#recordList` |
| 2D view | `#view2d`, `#map`, `#dataBanner`, `#coords`, `#scale`, `#routeLegend`, `#profile` (canvas), `#method`, `#compare`, `#compareNote`, `#evaBar`, `#evaLabel`, `#dose` |
| 3D view | `#view3d`, `#app`, `#nav-global`, `#nav-jezero`, `#status`, `#viewDiv`, `#btn-zoom-in/out`, `#btn-reset`, `#btn-explore`, `#btn-back`, `#loading-veil`, `#panel-left`, `#panel-right`, `#info-region`, `#info-lat`, `#info-lon`, `#info-elev`, `#info-sources`, `#info-alt`, `#info-ht`, `input[name=basemap]`, `input[data-layer=…]` |
| Right panel | `#info`, `#photos`, `#btnSaveRecord` |
| AI drawer | `#chat`, `#chatLog`, `.chip` ×3, `#chatForm`, `#chatInput`, `#btnChatClear`, `#btnChatClose` |

### 8.3 Routing / navigation

No URL router. "Navigation" means:
* **Tabs:** click handlers on `.tab` toggle `hidden` on `.tabpane`. Opening `env` calls `loadEnvironment()`; opening `records` calls `loadRecords()`. Programmatic: `document.querySelector('.tab[data-tab="route"]').click()`.
* **View mode:** `window.setView("2d"|"3d")`.
* **3D region:** `Mars3D.goToJezero()` / `goToGlobal()`.
* **AI drawer:** toggle `#chat.hidden` (`#btnChat`).

### 8.4 State management

* **`AppState` + `setState(patch)` + `onState(keys, fn)`** (`js/map.js:32-49`). `setState` does `Object.assign` and dispatches `window` `CustomEvent("appstate", {detail:{state, changed}})`. All 2D renderers subscribe with `onState`.
* **`SharedState`** in `integration.js` mirrors `selected`, `routeA`, `routeB`, `stops` and `maxSlope` from `appstate` events and tracks `view`.
* **Mars3D** has its own private `state` plus an event bus (`Mars3D.on`).
* `notifications[]` is a plain array in `map.js`.

### 8.5 Component communication

```
map.js ──setState──► window "appstate" event ──► map.js onState renderers
                                              └─► integration.js (SharedState, syncTo3D)
Mars3D ──on("select")──► integration.js ──(view==="2d")──► window.selectPoint2D → map.js select()
Mars3D ──on("status"/"region"/"camera"/"layers"/"select")──► app.js (3D panels)
integration.js ──window.setView──► show/hide views, Mars3D.init on demand
```

### 8.6 API requests

Only through `api(path, body)` in `map.js` (fetch, JSON). `fetchFirst(urls)` handles static JSON with a fallback list. The 3D module uses `fetch` (terrain) and `esriRequest` (imagery).

### 8.7 Map control

2D via `window.map` (Leaflet). 3D via `window.Mars3D`. See §4.9.

### 8.8 Where an AI assistant panel can be integrated

1. **Existing drawer `#chat`** (`index.html:291-307`). The simplest path is to keep the UI and implement `POST /api/chat` (§3.8). Limitation: plain-text answers, no actions.
2. **Replace `askAssistant()`.** Because it is a global function declaration in a classic script, a later script can override it (`window.askAssistant = myImpl` — note the form submit handler calls the identifier `askAssistant`, which resolves to the global binding, so the override takes effect). That lets an external assistant (iframe, web component or remote API) receive `chatContext()` and return actions.
3. **New panel:** add a `<script type="module">` after `JS/integration.js` that mounts its own UI and calls the handles in §4.9.

---

## 9. Backend architecture

| Item | Finding |
| --- | --- |
| Framework | **NOT FOUND** (hints point to FastAPI; **INFERRED** only) |
| Modules / services | **NOT FOUND** |
| Database | **NOT FOUND**. The comment on `/api/records` mentions SQLite |
| Data processing | Offline only: `scripts/prepare-terrain/prepare_terrain.py` |
| API structure | the 10 client-expected routes in §3 |
| External integrations | NASA DONKI and MEDA are named in UI and comments; no code |
| Existing AI integrations | **NONE**. Only the client placeholder for `/api/chat`; the 503 message implies an API key in `.env` |

### 9.1 `prepare_terrain.py` (the only server-side-capable code)

CLI (`main()`, `scripts/prepare-terrain/prepare_terrain.py:588`). Arguments: `--ctx-dem` (req), `--ctx-ortho` (req), `--hirise-dtm`, `--hirise-ortho`, `--out` (req), `--ctx-dem-levels 6-11`, `--ctx-ortho-levels 7-12`, `--hirise-dtm-levels 11-14`, `--hirise-ortho-levels 12-15`, `--ctx-feather-m 2000`, `--hirise-feather-m 300`, `--hirise-decimate 2`, `--only`.

Pipeline functions: `inspect()` (verifies bounds analytically), `fetch_global_dem()` (downloads Esri MDEM200M LERC tiles at L7), `feather_dem()` (smoothstep edge blend), `footprint()` (valid-data polygons), `encode_terrain_rgb()`, `build_dem_tiles()`, `build_image_tiles()` / `build_image_pyramid()`, `alignment_check()`. Outputs: `terrain/`, `imagery/`, `manifest.json`, `footprints.geojson`, `_work/`.

Its rasterio/numpy helpers (`tile_bounds`, `valid_elev`, the feathered DEMs in `_work/`) are the natural basis for a future `/api/elevation` and `/api/route/*` service. That would be **new work**.

---

## 10. Mission context and state

| Item | Where it lives | Shape | How an external assistant can read it |
| --- | --- | --- | --- |
| Current mission name | static HTML `#missionName` = "EVA-01 · Jezero delta" | string | `document.getElementById("missionName").textContent` |
| Mission location / region | constants `NASA.jezeroBounds`, `NASA.startView`, `NASA.landing`; 3D `Mars3D.JEZERO` | objects | global `NASA`; `Mars3D.JEZERO` |
| Habitat + stops (destinations) | `AppState.stops` | §3.2 schema | `AppState.stops` (bare global) |
| Selected location | `AppState.selected` (2D); `SharedState.selectedPoint` (private) | `{lat, lon, kind, name?, reason?, elevation_m, slope_deg, source_dataset, data_available, no_data_reason?, photos?}` | `AppState.selected`; or listen to `appstate` events where `changed` includes `"selected"` |
| Active route(s) | `AppState.routeA`, `AppState.routeB` | `RouteAnalysis` | same |
| Draft (unanalysed) route | `wpMarkers` (`let`) → `draftPoints()` | `[{lat, lon}]` | `draftPoints()` |
| Drawing mode | `drawing` (`let`) | boolean | bare `drawing` |
| Route results / EVA | `AppState.eva` | §3.6 | same |
| Max slope | `AppState.maxSlope` | number | same |
| Backend layer metadata | `AppState.layers` | §3.1 | same |
| Selected map layers (2D) | Leaflet: `map.hasLayer(nasaLayers[id])`, `map.hasLayer(overlays[id])` | — | iterate `nasaLayers` / `overlays` |
| Selected map layers (3D) | `Mars3D.getLayers()` → `visible` | — | same |
| Current view (2D/3D) | `SharedState.view` (private); `AppState.view` exists but is **never updated** in the combined app | — | `document.getElementById("view3d").hidden` |
| Map viewport | `map.getCenter()/getZoom()/getBounds()`; 3D `Mars3D.getView().camera` or the `camera` event | — | same |
| Mission status | **NOT FOUND** (only `eva.status` and space-weather status) | — | — |
| Space weather | rendered only into `#sw` (not stored in `AppState`) | — | read `#sw` text/class, or call `/api/spaceweather` |
| Previous mission records | backend `/api/records` (**NOT FOUND**) | §3.10 | `GET /api/records?q=` once built |
| Notifications | `notifications[]` (`const`) | `{id, severity, text, time}` | bare `notifications` |
| User preferences | **NOT FOUND** (no persistence of slope, layers or view) | — | — |
| Assistant context snapshot | `chatContext()` | §3.8 | call `chatContext()` |

**Retrieving context from a separate assistant:**
* **Same page (recommended):** call `chatContext()`, or read `AppState` directly. Subscribe to `window.addEventListener("appstate", e => …)` for live updates.
* **Different origin / process:** nothing exists. You would need either (a) a `postMessage` bridge if the assistant runs in an iframe, or (b) the frontend pushing `chatContext()` with every `/api/chat` call (already done). The backend has no session store, so it cannot know the UI state independently.

---

## 11. AI integration opportunities

Conventions: **RO** = read-only, **SC** = changes application state. "Adapter" = new code needed.

### 11.1 Backend tools (HTTP; all require the backend to be built first)

| ID | Name | Description | Input schema | Output schema | Existing reference | Adapter? | RO/SC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `be.get_layers` | Get data layers | DEM name, bounds, available raster layers | `{}` | §3.1 | `GET /api/layers` | backend route must be built | RO |
| `be.get_stops` | Get mission stops | Habitat + stops | `{}` | §3.2 | `GET /api/stops` (fallback `data/stops.json` already works) | static file works today | RO |
| `be.get_elevation` | Point terrain | Elevation and slope at a point | `{"lat":"number","lon":"number"}` | §3.3 | `GET /api/elevation` | backend | RO |
| `be.analyze_route` | Analyse route | Profile, distance, slopes for a polyline | `{"waypoints":[{"lat":"number","lon":"number"}],"route_id":"string"}` | `RouteAnalysis` | `POST /api/route/analyze` | backend | RO (server) |
| `be.auto_route` | Generate route | Slope-constrained route through stops | `{"stops":[Stop],"max_slope":"number"}` | `RouteAnalysis & {waypoints}` | `POST /api/route/auto` | backend | RO (server) |
| `be.eva_budget` | EVA budget | Time budget + dose | `{"out_waypoints":[…],"back_waypoints":[…],"n_stops":"int"}` | §3.6 | `POST /api/eva` | backend | RO |
| `be.space_weather` | Space weather | DONKI 7-day summary | `{}` | §3.7 | `GET /api/spaceweather` | backend | RO |
| `be.meda` | Weather data | MEDA rows | `{}` | §3.9 | `GET /api/meda` | backend | RO |
| `be.records_list` | Search mission log | — | `{"q":"string"}` | `[Record]` | `GET /api/records` | backend | RO |
| `be.records_save` | Save to log | — | Record (§3.10) | any | `POST /api/records` | backend | SC (persistent) |
| `be.rover_nearest` | Nearest rover position | Nearest Perseverance localisation, sol, elev_geoid, distance | `{"lat":"number","lon":"number"}` | `{"lat","lon","sol","elev","d"}` | `nearestWaypoint()` (client) + `data/M20_waypoints.json` | yes: port to backend, or call the client fn | RO |
| `be.dataset_coverage` | Which datasets cover a point | — | `{"lat","lon"}` | `[{"id","covers":bool}]` | `covers()` `js/terrain-loader.js:344` + manifest footprints | yes | RO |
| `be.science_docs_search` | RAG search | — | `{"query":"string"}` | `[{"title","url","snippet"}]` | **NOT FOUND** | new | RO |

### 11.2 Frontend actions (in-page; work today unless noted)

| ID | Name | Description | Input schema | Output | Existing implementation | Adapter? | RO/SC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fe.get_context` | Get mission context | UI snapshot for the assistant | `{}` | §3.8 `context` | `chatContext()` | no (extend if more fields wanted: view, 2D viewport, NASA layers, draft waypoints) | RO |
| `fe.get_state` | Raw app state | full `AppState` | `{}` | `AppState` | bare `AppState` | small (serialise; strip Leaflet objects — none stored) | RO |
| `fe.select_point` | Select location | Pan and select a point; triggers elevation lookup | `{"lat":"number","lon":"number"}` | updates `AppState.selected` | `window.selectPoint2D(lat, lon)` | no | SC |
| `fe.select_stop` | Select stop | Select by stop id | `{"stop_id":"string"}` | — | `select({...stop, kind:"stop"})` with `AppState.stops` | thin wrapper | SC |
| `fe.pan_zoom_2d` | Navigate 2D map | — | `{"lat","lon","zoom?":"number"}` or `{"bounds":[[s,w],[n,e]]}` | — | `map.setView` / `map.flyTo` / `map.fitBounds` | thin wrapper | SC (view) |
| `fe.reset_view` | Reset map | Frame habitat + stops | `{}` | — | `$("btnReset").onclick()` | no | SC |
| `fe.set_layer_2d` | Toggle 2D layer | NASA or backend layer on/off/opacity | `{"layer_id":"viking\|ctx_regional\|ctx_trn\|hirise\|mola\|themis\|<backend id>","visible":"bool","opacity?":"0..1"}` | `{"ok":bool}` | `map.addLayer/removeLayer`, `layer.setOpacity` on `nasaLayers[id]` / `overlays[id]` | **yes**: the sidebar checkboxes do not update when toggled programmatically; the traverse layer is not addressable | SC |
| `fe.set_layer_3d` | Toggle 3D layer | — | `{"layer_id":"global-imagery\|…\|footprints","visible":"bool"}` | `bool` | `Mars3D.setVisibleLayer` | yes, for checkbox sync (`input[data-layer]`) | SC |
| `fe.set_basemap_3d` | 3D basemap | — | `{"name":"mdim\|colordem"}` | — | `Mars3D.setBasemap` | radio sync | SC |
| `fe.set_view_mode` | 2D/3D | — | `{"mode":"2d\|3d"}` | — | `window.setView` | no | SC |
| `fe.fly_3d` | 3D camera | go to Jezero / global / location | `{"target":"jezero\|global"}` or `{"lat","lon","altitude?","tilt?","heading?"}` | `select` event payload | `Mars3D.goToJezero/goToGlobal/goToLocation` | no | SC |
| `fe.inspect_3d` | 3D elevation | elevations from every covering DEM | `{"lat","lon"}` | `{latitude, longitude, elevations[]}` | `Mars3D.goToLocation(lat, lon)` + `Mars3D.on("select")` | small (promise wrapper) | SC (camera) / RO (data) |
| `fe.set_max_slope` | Set slope limit | — | `{"deg":"5..25"}` | — | `setState({maxSlope})` | **yes**: also set `#slope.value` and `#slopeVal` text | SC |
| `fe.run_auto_route` | Run Route B | — | `{"max_slope?":"number"}` | `AppState.routeB`, `AppState.eva` | `$("btnAuto").onclick()` (async) | small (await + return state) | SC (needs backend) |
| `fe.set_route_a` | Set hand route | Replace Route A with given waypoints and analyse | `{"waypoints":[{"lat","lon"}]}` | `AppState.routeA` | `startDrawing()` + `addWaypoint(L.latLng(...))` ×N + `finishDrawing()` | wrapper | SC (needs backend) |
| `fe.clear_routes` | Clear routes | — | `{}` | — | `$("btnClear").onclick()` | no | SC |
| `fe.compare_routes` | Compare A vs B | Structured comparison | `{}` | `{a:{…}, b:{…}, lower_max_slope, shorter}` | logic in `js/map.js:511-530` | yes (extract logic; today it renders HTML) | RO |
| `fe.open_tab` | Navigate dashboard | — | `{"tab":"layers\|route\|env\|records"}` | — | `.tab[data-tab=…].click()` | thin wrapper | SC (UI) |
| `fe.toggle_chat` | Open/close assistant | — | `{"open":bool}` | — | `#chat.hidden` | thin | SC (UI) |
| `fe.notify` | Post notification | — | `{"severity":"info\|warning\|unavailable\|done","text":"string"}` | — | `notify(severity, text)` | no | SC (UI) |
| `fe.save_record` | Save selection to log | — | `{}` | — | `$("btnSaveRecord").onclick()` | no (needs backend) | SC |
| `fe.draw_route_3d` | Show route on globe | — | `{"route":"A\|B"}` | — | `drawRoute3D()` | **yes, must be fixed** (§13) | SC |

---

## 12. Integration requirements

### 12.1 Minimum API endpoints

| Priority | Endpoint | Why |
| --- | --- | --- |
| **Required** | `POST /api/chat` (same origin, or set `API` in `js/map.js:28`) | The existing UI calls it. Contract in §3.8 |
| Strongly recommended | `GET /api/elevation`, `POST /api/route/analyze`, `POST /api/route/auto`, `POST /api/eva` | Without them the assistant has no terrain or route facts to reason about |
| Optional | `/api/layers`, `/api/spaceweather`, `/api/meda`, `/api/records` | Richer context |

### 12.2 Required frontend callbacks / adapter

The current chat contract is **question → text answer only**. For the assistant to *operate* the app you need:
1. **An action channel.** Extend the `/api/chat` response, e.g. `{answer, sources, actions:[{type:"fe.select_point", args:{…}}]}`, and add a dispatcher in the page that maps `type` to the handles in §11.2. Alternatively, expose `window.MarswalkActions = {…}` for an in-page agent.
2. **UI sync helpers** for slider, layer checkboxes and 3D checkboxes (programmatic changes do not update the controls today).
3. **Awaitable wrappers** for async flows (auto-route, analyse, 3D inspect) that resolve with resulting state.
4. **Fix the 3D bridge** (§13) if the assistant should show routes or locations in 3D.

### 12.3 Shared state

Use `AppState` (read) and `setState` / `onState` (write / subscribe). Extend `chatContext()` with fields it currently omits: `view` (2D/3D), 2D viewport (center/zoom/bounds), visible NASA layers (today `active_layers` lists only backend overlays), the draft waypoints, `AppState.stops`, space-weather status, and the 3D camera/region.

### 12.4 Authentication

None exists in the frontend or the API helper. No user identity. If the assistant backend needs auth, add headers in `api()` (`js/map.js:54-63`). Keep LLM API keys on the server: the existing 503 message assumes a server `.env`.

### 12.5 CORS

* With `API = ""` the backend must be **served from the same origin** as the static files (or proxy them). Otherwise set `API = "http://localhost:8000"` and enable CORS for the page origin on the backend (POST with `Content-Type: application/json` triggers a pre-flight `OPTIONS`).
* External tile and data hosts (`trek.nasa.gov`, `mars.nasa.gov`, `astro.arcgis.com`, `js.arcgis.com`) are fetched cross-origin by the browser. Their CORS behaviour is **UNKNOWN** from code beyond `crossOrigin: true` on Leaflet tiles.

### 12.6 Environment variables

None are read by any code in this repository. The UI text mentions `.env` for the chat API key (server side, **INFERRED**).

### 12.7 Dependencies

* Frontend: CDN-only (Leaflet 1.9.4, Chart.js 4.4.1, ArcGIS 5.1). No npm.
* Data prep: `scripts/prepare-terrain/requirements.txt` (rasterio ≥ 1.4, numpy ≥ 1.26, scipy ≥ 1.11, shapely ≥ 2.0, pillow ≥ 10.0, lerc ≥ 4.0).
* Backend: **none defined**.

### 12.8 Integration blockers

1. **No backend at all.** Every analytic tool must be built first, or mocked for assistant development.
2. Chat response has **no action schema**. Plain-text only.
3. `active_layers` in `chatContext()` omits the NASA layers, view mode and viewport.
4. 3D sync bugs in `integration.js` (§13).
5. Missing 3D terrain tiles (`data/processed/terrain/`).
6. Upper/lower-case `JS/` vs `js/` paths break on Linux hosting.
7. `.claude/launch.json` points at another user's folder (`C:\Users\ABDO\Desktop\marswalk-combined`), so the preview config does not work on this machine.

---

## 13. Known limitations and incomplete features

### 13.1 Not implemented (backend routes called by the UI)
`/api/layers`, `/api/stops`, `/api/elevation`, `/api/route/analyze`, `/api/route/auto`, `/api/eva`, `/api/spaceweather`, `/api/chat`, `/api/meda`, `/api/records`. Also not implemented: RAG / document retrieval, user preferences or persistence, mission status, user accounts, OpenAPI docs.

### 13.2 Placeholders and starter data
* `data/stops.json`: real rover positions, but names and reasons are a **starter set** (`_note`); `photos: []` everywhere → placeholder tiles "Rover photo not added yet".
* Environment tab: placeholder until `/api/meda` exists.
* Mission log: placeholder until `/api/records` exists.
* Radiation card: static value from Gale crater (Curiosity RAD), not Jezero.
* No mock or synthetic data generators exist. The UI explicitly flags backend data whose `source` or `dem` starts with `"TEST"`.

### 13.3 Broken or inconsistent code (static inspection)

| # | Issue | Location | Effect |
| --- | --- | --- | --- |
| B1 | `Mars3D.goTo(...)` is called but **Mars3D has no `goTo`** (it has `goToLocation`) | `js/integration.js:60` | `TypeError` when switching to 3D with a selected point, or selecting while in 3D. It aborts the rest of `syncTo3D` (routes not drawn) |
| B2 | `await import("esri/geometry/Polyline", "esri/Graphic")` and `import("esri/symbols/SimpleLineSymbol")`: bare specifiers cannot be resolved by native `import()`, the second argument is not a module list, and named exports are destructured from modules that use default exports | `js/integration.js:109,115` | `drawRoute3D` always rejects → **no routes in 3D** |
| B3 | `drawRoute3D` uses `spatialReference: {wkid: 4326}` (Earth WGS84) while the scene uses Mars WKID 104971 | `js/integration.js:112` | even if B2 is fixed, the geometry may be projected or rejected |
| B4 | Route A analysis is stored as the raw `/api/route/analyze` response; `integration.js` expects `routeA.waypoints`, which map.js does not guarantee | `js/integration.js:64` | Route A likely never drawn in 3D |
| B5 | `syncTo2D` only runs when `SharedState.view === "2d"`, but 3D clicks happen in 3D mode | `js/integration.js:78` | 3D selection reaches 2D only when the user switches back (via `setView("2d")`) |
| B6 | `js/app.js` initialises the 3D scene at page load inside a **hidden** container, and `integration.js` also calls `Mars3D.init` if `isInitialized()` is false. `state` is only set after async SDK and manifest loads | `js/app.js:187-200`, `js/integration.js:154-158` | possible double-init race ("already initialised") if 3D is opened quickly. Hidden-container sizing at init time not verified |
| B7 | `AppState.view` is never updated in the combined app (the old `setView` in `nasa/JS/map.js` did it) | `js/map.js:40,636-638` | stale `view` in `AppState` |
| B8 | `index.html` loads `JS/*.js`; the folder is `js/` | `index.html:311-314` | 404 on case-sensitive hosts |
| B9 | `mode2d`/`mode3d` click handlers are assigned in both `map.js` and `integration.js` | — | harmless (integration wins), but duplicated |
| B10 | `data/stops.json` contains mis-encoded `Â·` instead of `·` | `data/stops.json` | cosmetic |
| B11 | `fetch_nasa_data.py` writes into `nasa/data/`, but the root app reads `data/` | `nasa/tools/fetch_nasa_data.py:13` | refreshed data is not picked up by the combined app |
| B12 | `.claude/launch.json` `--directory` points to `C:\Users\ABDO\…` | `.claude/launch.json` | preview config invalid on this machine |

### 13.4 Dependent on external services
NASA Mars Trek tiles (2D imagery), mars.nasa.gov (live rover fallback), Esri astro.arcgis.com (all 3D global layers), js.arcgis.com, unpkg and jsDelivr CDNs. NASA DONKI and MEDA (via the future backend).

### 13.5 Missing datasets
* `data/processed/terrain/ctx/` and `terrain/hirise/` (Terrain-RGB tiles): listed in `manifest.json` but absent → 3D local terrain fetches 404 (rejected tiles; the app logs them). Regenerate with `npm run prepare-terrain` or `prepare_terrain.py`; raw inputs are present in `data/raw/`.
* Backend DEM for 2D analysis: none configured.
* MEDA, DONKI, RAG corpus: none.

### 13.6 Not tested
No test files, test framework or CI config exist in the repository. **NOT FOUND.**

---

## 14. Testing and example workflows

All workflows assume an in-page assistant (a script loaded after `JS/integration.js`) or an action dispatcher (§12.2). Steps marked ⛔ need the missing backend.

### 14.1 Generate a route and display it
1. `fe.get_context` → `chatContext()`; check `AppState.stops` is loaded.
2. Optional: `setState({maxSlope: 12}); slope.value = 12; slopeVal.textContent = 12;`
3. ⛔ `await $("btnAuto").onclick()`. Internally: `POST /api/route/auto` (out), `POST /api/route/auto` (back), `drawRoute("B", out)`, `POST /api/eva`, `setState({routeB, eva})`.
4. Read `AppState.routeB` (`distance_m`, `max_slope_deg`, `over_limit`, `data_coverage_pct`) and `AppState.eva`.
5. Optional: `map.fitBounds(L.latLngBounds(AppState.routeB.profile.map(p => [p.lat, p.lon])).pad(0.2))`.

### 14.2 Compare two routes
1. Create Route A: `startDrawing(); for (const w of wps) addWaypoint(L.latLng(w.lat, w.lon)); await finishDrawing();` ⛔ (`POST /api/route/analyze`).
2. Create Route B as in 14.1 ⛔.
3. Read `AppState.routeA` and `AppState.routeB`. Reproduce the comparison logic from `js/map.js:521-528` (lower max slope, shorter route, >25% distance or >5° slope difference). The table `#compare` and chart `#profile` update automatically through `onState`.

### 14.3 Analyse a selected location
1. `window.selectPoint2D(18.4589, 77.4062)` → `select()` → ⛔ `GET /api/elevation?lat=…&lon=…`.
2. Without the backend: if within 40 m of a rover waypoint, `AppState.selected.elevation_m` is rover-measured and `source_dataset` starts with "Perseverance…".
3. For DEM values today, use 3D: `setView("3d")`, then `const off = Mars3D.on("select", r => {…; off();}); await Mars3D.goToLocation(18.4589, 77.4062, {altitude: 3000});`. You get `r.elevations` for `hirise-dtm`, `ctx-dem` and `global-elevation` (CTX/HiRISE require the terrain tiles).
4. Add context: `nearestWaypoint(lat, lon)` (sol, rover elevation), `covers()` with `Mars3D.getManifest().datasets[id]` for dataset coverage, and the stop `reason` if the point is a stop.

### 14.4 Retrieve current mission context
```js
const ctx = {
  ...chatContext(),                                   // selected, active backend layers, routes, eva, max slope, dem
  mission: document.getElementById("missionName").textContent,
  stops: AppState.stops,
  view: document.getElementById("view3d").hidden ? "2d" : "3d",
  viewport2d: { center: map.getCenter(), zoom: map.getZoom() },
  nasa_layers_on: Object.entries(nasaLayers).filter(([, l]) => map.hasLayer(l)).map(([id]) => id),
  layers3d: Mars3D.isInitialized() ? Mars3D.getLayers() : null,
  region3d: Mars3D.isInitialized() ? Mars3D.getRegion() : null,
  space_weather: document.getElementById("sw").textContent,
  notifications,
};
```

### 14.5 Change a map layer
* 2D: `map.addLayer(nasaLayers.mola); nasaLayers.mola.setOpacity(0.7);` (the sidebar checkbox will **not** reflect this; adapter needed).
* 3D: `Mars3D.setVisibleLayer("hirise-imagery", false); Mars3D.setBasemap("colordem");`, then sync `input[data-layer="hirise-imagery"]` and `input[name=basemap]`.

### 14.6 Navigate to a dashboard section
* Tabs: `document.querySelector('.tab[data-tab="env"]').click()` (also triggers ⛔ `loadEnvironment()`).
* View: `window.setView("3d")`, then `Mars3D.goToJezero()`.
* Assistant drawer: `document.getElementById("chat").hidden = false`.

### 14.7 Suggested acceptance tests for the integration (none exist today)
1. `/api/chat` contract: 200 with `{answer}`, 503 when no key, 404/501 handled.
2. `chatContext()` is JSON-serialisable in every state (no selection, A only, B only, both).
3. Each `fe.*` action updates both state and visible controls.
4. Coordinates: tools reject lat/lon outside ±90/±180 and warn outside `NASA.jezeroBounds`.

---

## 15. Final integration summary

### 15.1 Five most important backend capabilities to expose
1. **`POST /api/chat`**: the assistant entry point the UI already calls. Extend it with an `actions[]` field.
2. **`GET /api/elevation`**: point elevation and slope from the CTX/HiRISE DTM (the feathered rasters in `data/processed/_work/` or the raw ones in `data/raw/` can back it).
3. **`POST /api/route/auto`**: slope-constrained route generation returning `RouteAnalysis + waypoints`.
4. **`POST /api/route/analyze`**: evaluation of any polyline (also used to check assistant-proposed routes).
5. **`POST /api/eva`**: EVA time budget and dose estimate (feeds the safety/feasibility narrative).

### 15.2 Five most important frontend actions to expose
1. **`fe.get_context`**: `chatContext()` extended with view, viewport, NASA layers and stops.
2. **`fe.select_point`**: `window.selectPoint2D(lat, lon)` (and `Mars3D.goToLocation` for 3D).
3. **`fe.run_auto_route` / `fe.set_route_a`**: `$("btnAuto").onclick()` and `startDrawing` / `addWaypoint` / `finishDrawing`.
4. **`fe.set_layer_2d` / `fe.set_layer_3d`**: `nasaLayers[id]` with `map.addLayer/removeLayer`, `Mars3D.setVisibleLayer`, plus checkbox sync.
5. **`fe.set_view_mode` + `fe.open_tab`**: `window.setView("2d"|"3d")` and `.tab[data-tab]` clicks.

### 15.3 Files another developer should inspect

| File | Why |
| --- | --- |
| `js/map.js` | All 2D logic, `AppState`, every `/api/*` contract, `chatContext()`, `askAssistant()` |
| `js/nasa-config.js` | Coordinates, bounds, layer catalogue, rover data URLs |
| `js/integration.js` | 2D ↔ 3D bridge, `window.setView`, `selectPoint2D` (contains bugs B1–B5) |
| `js/mars3d.js` | Public 3D API and events |
| `docs/INTEGRATION_GUIDE.md` | 3D API reference |
| `index.html` | DOM ids, chat drawer, script load order |
| `data/stops.json` | Stop schema |
| `data/processed/manifest.json` | Dataset bounds, footprints, tile index |
| `docs/DATA_SOURCES.md`, `nasa/DATA_SOURCES.md` | Dataset provenance and coordinate conventions |
| `scripts/prepare-terrain/prepare_terrain.py` | The only raster-processing code; reusable for a terrain backend |

### 15.4 Minimum integration requirements
1. A backend serving `POST /api/chat` on the same origin (or set `API` + CORS) and returning `{answer, sources?}`.
2. An action schema plus an in-page dispatcher if the assistant must operate the map.
3. Terrain endpoints (`/api/elevation`, `/api/route/*`, `/api/eva`) if the assistant should reason about routes. Otherwise it only has stops, rover waypoints and 3D DEM readings.
4. Regenerated `data/processed/terrain/` if 3D DEM answers are needed.
5. Fixes for B1–B3 if routes or locations must be shown in 3D.

### 15.5 Unanswered technical questions
1. Is there a backend repository elsewhere (FastAPI?) that implements the 10 `/api/*` routes? Which framework, port and host?
2. Which DEM, resolution and slope algorithm should `/api/elevation` and `/api/route/*` use? What is the pathfinding algorithm (A*, Dijkstra on a slope-cost grid…)?
3. Which walking-speed / Tobler-type model and suit limit drive `/api/eva`? Which dose model produces `dose_estimate_mgy`?
4. What values can `spaceweather.status` take besides `"unknown"` and `"caution"` (CSS classes in `css/nasa-style.css` may define them)?
5. Should the backend's route-A `over_limit` use the client's `maxSlope`? (It is not sent today.)
6. Which LLM provider and key does `/api/chat` expect (only "API key in .env" is mentioned)? Is conversation history required?
7. What document corpus should the RAG layer use (none exists)?
8. Should the assistant act on the 2D map, the 3D globe, or both? (The 3D bridge needs fixing for both.)
9. Where will records live (SQLite per comment) and is there a user or mission identity?
10. Final hosting: same-origin static + API, or separate origins (affects CORS and `API`)?
