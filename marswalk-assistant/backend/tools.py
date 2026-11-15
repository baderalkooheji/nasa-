"""
Marswalk AI Assistant - Tool Calling System
Registry and execution of the tools the language model may call.

Rules enforced here (not left to the model):
  * Every measurement / calculation comes from the real Marswalk backend (NASA DEMs) or from Python code in this file.
    No tool returns invented or random values. When data is missing the tool says so.
  * Places are resolved on the server from named places (habitat, stops, Jezero, the rover, the user's selected point)
    or explicit "lat, lon" text, so the model does not have to (and cannot usefully) invent coordinates.
  * Map / application tools only QUEUE a frontend action. The Marswalk page executes it and reports back; the
    assistant must not claim an action happened until the page confirms it.
  * Results returned to the model are compact summaries with data labels (measured / calculated / estimated /
    historical / reference / missing). Full route geometry goes to the page, not into the model's context.
"""
from __future__ import annotations

import contextvars
import json
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from marswalk_client import MarswalkUnavailable, marswalk

logger = logging.getLogger(__name__)

MARS_R = 3396190.0                                       # m, Mars 2000 sphere (IAU)
JEZERO_CENTER = {"lat": 18.4082, "lon": 77.6873, "name": "Jezero Crater (IAU centre)",
                 "source": "IAU Gazetteer of Planetary Nomenclature, feature 14300 (js/jezero-region.js)"}


# ============================================================================
# Registry (structure from the original assistant, with argument coercion added)
# ============================================================================

@dataclass
class ToolParameter:
    name: str
    type: str  # string, number, integer, boolean, array, object
    description: str
    required: bool = False
    default: Any = None
    enum: List[Any] = field(default_factory=list)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)
    returns: str = "object"
    requires_permission: bool = False
    category: str = "general"

    def to_json_schema(self) -> Dict[str, Any]:
        properties, required = {}, []
        for p in self.parameters:
            prop = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            if p.default is not None:
                prop["default"] = p.default
            if p.type == "array":
                prop["items"] = {"type": "string"}
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        return {"name": self.name, "description": self.description,
                "parameters": {"type": "object", "properties": properties, "required": required}}

    def to_ollama(self) -> Dict[str, Any]:
        """Ollama / OpenAI-style function tool definition."""
        return {"type": "function", "function": self.to_json_schema()}


class ToolResult:
    def __init__(self, success: bool, data: Any = None, error: str = None, metadata: Dict = None):
        self.success = success
        self.data = data
        self.error = error
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"success": self.success, "data": self.data, "error": self.error, "metadata": self.metadata}

    @classmethod
    def success_result(cls, data: Any, metadata: Dict = None) -> "ToolResult":
        return cls(success=True, data=data, metadata=metadata)

    @classmethod
    def error_result(cls, error: str, metadata: Dict = None) -> "ToolResult":
        return cls(success=False, error=error, metadata=metadata)


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._handlers: Dict[str, Callable[..., Awaitable[ToolResult]]] = {}

    def register(self, definition: ToolDefinition, handler: Callable[..., Awaitable[ToolResult]]):
        self._tools[definition.name] = definition
        self._handlers[definition.name] = handler

    def unregister(self, name: str):
        self._tools.pop(name, None)
        self._handlers.pop(name, None)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def get_tools_for_llm(self) -> List[Dict[str, Any]]:
        return [t.to_json_schema() for t in self._tools.values()]

    def get_tools_for_ollama(self) -> List[Dict[str, Any]]:
        return [t.to_ollama() for t in self._tools.values()]

    async def execute(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        handler = self._handlers.get(name)
        if not handler:
            return ToolResult.error_result(f"Tool not found: {name}. Available: {', '.join(self._tools)}")
        tool_def = self._tools[name]
        try:
            args = self._coerce(tool_def, dict(arguments or {}))
            err = self._validate_arguments(tool_def, args)
            if err:
                return ToolResult.error_result(err)
            known = {p.name for p in tool_def.parameters}
            return await handler(**{k: v for k, v in args.items() if k in known})
        except MarswalkUnavailable as e:
            return ToolResult.error_result(str(e), {"data_type": "missing"})
        except Exception as e:                           # never let one tool crash the conversation
            logger.exception("Tool execution error for %s", name)
            return ToolResult.error_result(f"Execution failed: {e}")

    @staticmethod
    def _coerce(tool_def: ToolDefinition, args: Dict[str, Any]) -> Dict[str, Any]:
        """Small models often send numbers as strings or arrays as comma lists; convert where unambiguous."""
        for p in tool_def.parameters:
            if p.name not in args or args[p.name] is None:
                args.pop(p.name, None)
                continue
            v = args[p.name]
            try:
                if p.type in ("number", "integer") and isinstance(v, str) and v.strip():
                    v = float(v) if p.type == "number" else int(float(v))
                elif p.type == "integer" and isinstance(v, float) and v.is_integer():
                    v = int(v)
                elif p.type == "boolean" and isinstance(v, str):
                    v = v.strip().lower() in ("true", "yes", "1", "on")
                elif p.type == "array" and isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except json.JSONDecodeError:
                        v = [x.strip() for x in v.split(",") if x.strip()]
                elif p.type == "string" and isinstance(v, (int, float)) and not isinstance(v, bool):
                    v = str(v)
            except (TypeError, ValueError):
                pass
            args[p.name] = v
        return args

    @staticmethod
    def _validate_arguments(tool_def: ToolDefinition, arguments: Dict[str, Any]) -> Optional[str]:
        for p in tool_def.parameters:
            if p.required and p.name not in arguments:
                return f"Missing required parameter: {p.name}"
            if p.name not in arguments:
                continue
            v = arguments[p.name]
            ok = {"number": isinstance(v, (int, float)) and not isinstance(v, bool),
                  "integer": isinstance(v, int) and not isinstance(v, bool),
                  "string": isinstance(v, str), "boolean": isinstance(v, bool),
                  "array": isinstance(v, list), "object": isinstance(v, dict)}.get(p.type, True)
            if not ok:
                return f"Parameter {p.name} must be a {p.type}"
            if p.enum and v not in p.enum:
                return f"Parameter {p.name} must be one of: {p.enum}"
        return None


tool_registry = ToolRegistry()


def tool(name: str, description: str, parameters: List[ToolParameter] = None, returns: str = "object",
         requires_permission: bool = False, category: str = "general"):
    """Decorator to register a tool."""
    def decorator(func):
        tool_registry.register(ToolDefinition(name, description, parameters or [], returns, requires_permission, category), func)
        return func
    return decorator


# ============================================================================
# Per-request context (set by main.py around each chat turn)
# ============================================================================

@dataclass
class RunContext:
    page: Dict[str, Any] = field(default_factory=dict)          # state reported by the Marswalk page (selection, routes, view, layers)
    routes: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # routes generated in this conversation: id → full analysis
    actions: List[Dict[str, Any]] = field(default_factory=list)       # frontend actions queued this turn
    analyses: List[Dict[str, Any]] = field(default_factory=list)      # analyses run this turn (for mission memory)
    current_route_id: Optional[str] = None
    selected_area: Optional[Dict[str, Any]] = None
    citations: int = 0                                                 # [DOC-n] numbers already used this turn (unique per turn)


_RUN: contextvars.ContextVar[RunContext] = contextvars.ContextVar("marswalk_run", default=None)


def set_run_context(ctx: RunContext):
    return _RUN.set(ctx)


def reset_run_context(token):
    _RUN.reset(token)


def run_ctx() -> RunContext:
    ctx = _RUN.get()
    if ctx is None:                                  # direct tool execution (e.g. /tools/execute, tests)
        ctx = RunContext()
        _RUN.set(ctx)
    return ctx


def _queue_action(kind: str, summary: str, **payload) -> Dict[str, Any]:
    a = {"action_id": uuid.uuid4().hex[:10], "type": kind, "summary": summary, **payload}
    run_ctx().actions.append(a)
    return a


def _pending(a: Dict[str, Any]) -> Dict[str, Any]:
    return {"action_id": a["action_id"], "status": "sent to the Marswalk page; it confirms or reports failure itself", "summary": a["summary"]}


# ============================================================================
# Places
# ============================================================================

_LATLON = re.compile(r"(-?\d{1,2}(?:\.\d+)?)\s*°?\s*([NS])?\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*°?\s*([EW])?", re.I)


async def _places() -> List[Dict[str, Any]]:
    s = await marswalk.stops()
    out = [{"id": "habitat", "name": s["habitat"]["name"], "lat": s["habitat"]["lat"], "lon": s["habitat"]["lon"],
            "reason": s["habitat"].get("reason")}]
    for st in s.get("stops", []):
        out.append({"id": f"stop {st['id']}", "name": st["name"], "lat": st["lat"], "lon": st["lon"], "reason": st.get("reason")})
    return out


async def resolve_place(text: Any) -> Optional[Dict[str, Any]]:
    """'habitat', 'stop 2', 'delta front', 'selected', 'jezero', 'rover', '18.45, 77.40' → {lat, lon, name, source}."""
    if text is None:
        return None
    if isinstance(text, dict) and "lat" in text and "lon" in text:
        return {"lat": float(text["lat"]), "lon": float(text["lon"]), "name": text.get("name") or "given point", "source": "user"}
    t = str(text).strip()
    tl = t.lower()
    m = _LATLON.search(t)
    if m and re.fullmatch(r"[\s\d.,°nsewNSEW\-]+", t):
        lat, lon = float(m.group(1)), float(m.group(3))
        if (m.group(2) or "").upper() == "S":
            lat = -abs(lat)
        if (m.group(4) or "").upper() == "W":
            lon = -abs(lon)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"lat": lat, "lon": lon, "name": f"{lat:.5f}°N {lon:.5f}°E", "source": "coordinates given by the user"}
    if re.search(r"\b(selected|this (location|point|place|spot)|here|current (location|point)|my (location|point))\b", tl) or tl in ("selection", "this"):
        sel = (run_ctx().page or {}).get("selected")
        if sel and sel.get("lat") is not None:
            return {"lat": sel["lat"], "lon": sel["lon"], "name": sel.get("name") or "selected point on the map",
                    "source": "the point selected on the Marswalk map"}
        return None
    if re.search(r"\b(rover|perseverance)\b", tl):
        rv = await _rover_latest()
        return {"lat": rv["lat"], "lon": rv["lon"], "name": f"Perseverance (sol {rv['sol']})", "source": rv["source"]}
    if re.search(r"\b(jezero|crater cent(er|re))\b", tl) and not re.search(r"floor|depot|east", tl):
        return dict(JEZERO_CENTER)
    places = await _places()
    for p in places:                                     # id: "habitat", "h", "stop 2", "2"
        if tl in (p["id"], p["id"].replace("stop ", ""), "h" if p["id"] == "habitat" else "\0") or tl == f"stop{p['id'][5:]}":
            return {**p, "source": "data/stops.json (Perseverance end-of-drive positions)"}
    if re.search(r"\b(habitat|base|home|depot|three forks)\b", tl):
        p = places[0]
        return {**p, "source": "data/stops.json"}
    words = [w for w in re.findall(r"[a-z]+", tl) if w not in ("the", "of", "to", "a", "at", "near", "stop", "area", "region")]
    best, score = None, 0
    for p in places:
        name = p["name"].lower()
        primary = re.split(r"\s+[·(]", name)[0]             # "Delta front" in "Delta front · Hawksbill Gap"
        sc = sum(2 if w in primary else 1 for w in words if w in name)
        if sc > score:
            best, score = p, sc
    if best and score:
        return {**best, "source": "data/stops.json (Perseverance end-of-drive positions)"}
    return None


async def _place_or_error(location: Any, lat: Optional[float] = None, lon: Optional[float] = None):
    if lat is not None and lon is not None:
        return {"lat": lat, "lon": lon, "name": f"{lat:.5f}°N {lon:.5f}°E", "source": "coordinates given"}, None
    p = await resolve_place(location if location is not None else "selected")
    if p:
        return p, None
    names = ", ".join(f"{x['id']} ({x['name']})" for x in await _places())
    if location in (None, "", "selected") or re.search(r"selected|this|here", str(location or "").lower()):
        return None, "No point is selected on the Marswalk map. Ask the user to click a point, or name a place: " + names
    return None, f"Unknown place '{location}'. Known places: {names}; also 'jezero', 'rover', 'selected', or 'lat, lon'."


async def _rover_latest() -> Dict[str, Any]:
    fc = await marswalk.rover_waypoints()
    feats = [f for f in fc.get("features", []) if f.get("properties", {}).get("sol") is not None]
    p = max(feats, key=lambda f: (f["properties"]["sol"], f["properties"].get("dist_total_m") or 0))["properties"]
    return {"sol": p["sol"], "lat": p["lat"], "lon": p["lon"], "elev_geoid_m": p.get("elev_geoid"),
            "dist_total_km": p.get("dist_km") or round((p.get("dist_total_m") or 0) / 1000, 2),
            "source": "NASA/JPL-Caltech MMGIS M20_waypoints.json (historical localisation, not live)"}


# ============================================================================
# Route helpers
# ============================================================================

def _route_summary(rid: str, r: Dict[str, Any]) -> Dict[str, Any]:
    return {"route_id": rid, "label": r.get("label") or r.get("objective") or "route",
            "from": r.get("_from"), "to": r.get("_to"),
            "distance_m": r.get("distance_m"), "max_slope_deg": r.get("max_slope_deg"), "avg_slope_deg": r.get("avg_slope_deg"),
            "elevation_gain_m": r.get("elevation_gain_m"), "elevation_loss_m": r.get("elevation_loss_m"),
            "walk_time_out_min": r.get("walk_time_out_min") or r.get("walk_time_min"),
            "over_slope_limit": r.get("over_limit"), "slope_limit_deg": r.get("limit_deg"),
            "data_coverage_pct": r.get("data_coverage_pct"), "source_dataset": r.get("source_dataset"),
            "data_type": "calculated from the DEM (distances, slopes, elevation change); walking time is a model estimate"}


def _new_route_id() -> str:
    ctx = run_ctx()
    n = 1
    while f"R{n}" in ctx.routes:
        n += 1
    return f"R{n}"


def _page_routes() -> Dict[str, Dict[str, Any]]:
    """Routes A / B currently on the Marswalk page (reported with each message)."""
    out = {}
    for key in ("A", "B"):
        r = ((run_ctx().page or {}).get("routes") or {}).get(key)
        if r:
            out[key] = r
    return out


def _find_route(route_id: Optional[str]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    ctx = run_ctx()
    rid = (route_id or "").strip().upper().replace("ROUTE ", "") or None
    if rid is None:
        rid = ctx.current_route_id
        if rid is None and _page_routes():
            rid = "B" if "B" in _page_routes() else "A"
    if rid in ctx.routes:
        return rid, ctx.routes[rid]
    if rid in _page_routes():
        return rid, _page_routes()[rid]
    return rid, None


def _display(rid: str, r: Dict[str, Any], fit: bool = True) -> Dict[str, Any]:
    analysis = {k: v for k, v in r.items() if not k.startswith("_")}
    return _queue_action("display_route", f"Show {rid} ({r.get('label') or 'route'}) on the map as Route B", route_id=rid,
                         label=r.get("label") or rid, analysis=analysis, fit=fit)


# ============================================================================
# Mission tools
# ============================================================================

@tool(
    name="get_current_mission_state",
    description="Current mission state: 2D/3D view, the point selected on the map, Routes A/B on the map, routes generated in this "
                "conversation, visible layers, journey status and the named places (habitat, stops). Call it when the user refers "
                "to 'this', 'here', 'the route' or asks what is on screen.",
    category="mission",
)
async def get_current_mission_state() -> ToolResult:
    ctx = run_ctx()
    page = ctx.page or {}
    try:
        places = [{"id": p["id"], "name": p["name"]} for p in await _places()]
    except MarswalkUnavailable as e:
        places = f"unavailable ({e})"
    return ToolResult.success_result({
        "mission": "EVA-01 · Jezero delta",
        "view": page.get("view", "unknown (page did not report)"),
        "selected_point": page.get("selected") or "none - nothing selected on the map",
        "routes_on_map": {k: {kk: v.get(kk) for kk in ("distance_m", "max_slope_deg", "elevation_gain_m", "over_limit")}
                          for k, v in _page_routes().items()} or "none",
        "routes_generated_in_this_conversation": [_route_summary(k, v) for k, v in ctx.routes.items()] or "none",
        "current_route_id": ctx.current_route_id,
        "visible_layers": page.get("visible_layers") or [],
        "journey": page.get("journey") or "not running",
        "places": places,
        "mission_objectives": "not set in this application",
    })


@tool(
    name="generate_route",
    description="Plan a walking route on the real Jezero DEM, avoiding slopes above the limit. Places are names: 'habitat', "
                "'stop 1', 'delta front', 'selected' (the map selection), 'rover', or 'lat, lon'. Set alternatives=true to get three "
                "options (balanced, gentlest, most direct) between two places. The recommended route is shown on the map.",
    parameters=[
        ToolParameter("destination", "string", "Where to go (place name, 'selected', or 'lat, lon')", required=True),
        ToolParameter("start", "string", "Start place (default: habitat)", default="habitat"),
        ToolParameter("via", "array", "Optional places to visit on the way, in order"),
        ToolParameter("max_slope", "number", "Maximum walkable slope in degrees, 5-25 (project default 15)", default=15),
        ToolParameter("alternatives", "boolean", "true = three alternative routes to compare", default=False),
    ],
    category="routing",
)
async def generate_route(destination: str, start: str = "habitat", via: List[str] = None, max_slope: float = 15,
                         alternatives: bool = False) -> ToolResult:
    if not 5 <= float(max_slope) <= 25:
        return ToolResult.error_result("max_slope must be between 5 and 25 degrees (project-defined range).")
    a, err = await _place_or_error(start or "habitat")
    if err:
        return ToolResult.error_result("Start: " + err)
    b, err = await _place_or_error(destination)
    if err:
        return ToolResult.error_result("Destination: " + err)
    stops = [a]
    for v in via or []:
        p, err = await _place_or_error(v)
        if err:
            return ToolResult.error_result("Via: " + err)
        stops.append(p)
    stops.append(b)
    ctx = run_ctx()
    made = []
    if alternatives and len(stops) == 2:
        res = await marswalk.route_options({"lat": a["lat"], "lon": a["lon"]}, {"lat": b["lat"], "lon": b["lon"]}, max_slope)
        for r in res["routes"]:
            rid = _new_route_id()
            r.update({"_from": a["name"], "_to": b["name"]})
            ctx.routes[rid] = r
            made.append(rid)
        ok = [rid for rid in made if ctx.routes[rid].get("within_constraints")]
        best = min(ok or made, key=lambda rid: ctx.routes[rid].get("weighted_cost") or 9e9)
        reason = ("lowest terrain cost among the options that stay within the slope limit" if ok
                  else "no option stays within the slope limit everywhere; showing the lowest terrain cost one")
    else:
        r = await marswalk.route_auto([{"lat": p["lat"], "lon": p["lon"], "name": p["name"]} for p in stops], max_slope)
        rid = _new_route_id()
        r.update({"label": "Least-cost route", "_from": a["name"], "_to": b["name"],
                  "_via": [p["name"] for p in stops[1:-1]]})
        ctx.routes[rid] = r
        made, best, reason = [rid], rid, "only route requested"
    ctx.current_route_id = best
    act = _display(best, ctx.routes[best])
    return ToolResult.success_result({
        "reply_hint": "The page shows these routes in a table. Reply in 2-3 sentences: the recommended route, why, and its distance "
                      "and maximum slope.",
        "routes": [_route_summary(rid, ctx.routes[rid]) for rid in made],
        "recommended_route_id": best, "why_recommended": reason,
        "map": _pending(act),
        "notes": ["Slope limit and caution thresholds are project-defined, not NASA standards.",
                  "A route that meets the slope limit is not guaranteed safe: the DEM does not resolve boulders or small ledges."],
    })


@tool(
    name="compare_routes",
    description="Compare routes by distance, steepest slope, climbing and walking time. Route ids: R1, R2, … (generated in this "
                "conversation) or A / B (the routes on the map). Without ids, compares every known route.",
    parameters=[ToolParameter("route_ids", "array", "Route ids to compare, e.g. ['R1','R2'] or ['A','B']")],
    category="routing",
)
async def compare_routes(route_ids: List[str] = None) -> ToolResult:
    ctx = run_ctx()
    pool = {**{k: v for k, v in _page_routes().items()}, **ctx.routes}
    ids = [str(i).strip().upper().replace("ROUTE ", "") for i in (route_ids or [])] or list(pool)
    missing = [i for i in ids if i not in pool]
    rows = [_route_summary(i, pool[i]) for i in ids if i in pool]
    if len(rows) < 2:
        return ToolResult.error_result(
            f"Need at least two routes to compare; known routes: {list(pool) or 'none'}"
            + (f"; not found: {missing}" if missing else "") + ". Generate routes first (generate_route with alternatives=true).")

    def best(key, fn=min):
        vals = [r for r in rows if r.get(key) is not None]
        return fn(vals, key=lambda r: r[key]) if vals else None
    picks = {"shortest": ("distance_m", "m"), "lowest_max_slope": ("max_slope_deg", "°"), "least_climbing": ("elevation_gain_m", "m"),
             "fastest_walk": ("walk_time_out_min", "min")}
    best_by, sentences = {}, []
    for label, (key, unit) in picks.items():
        b = best(key)
        if b:
            best_by[label] = b["route_id"]
            sentences.append(f"{label.replace('_', ' ')}: {b['route_id']} ({b.get('label')}, {b[key]} {unit})")
    table = [f"{r['route_id']} {r.get('label')}: {r.get('distance_m')} m, max slope {r.get('max_slope_deg')}°, "
             f"climb +{r.get('elevation_gain_m')} m, walk {r.get('walk_time_out_min')} min"
             + (", OVER the slope limit" if r.get("over_slope_limit") else ", within the slope limit") for r in rows]
    return ToolResult.success_result({
        "summary": "Best per criterion (computed, use as written) - " + "; ".join(sentences) + ".",
        "table": table,
        "routes": rows,
        "best_by": best_by,
        "not_found": missing,
        "data_type": "calculated from the DEM by the Marswalk planner; walking time is a model estimate",
    })


@tool(
    name="display_route_on_map",
    description="Show a route (R1, R2, … or A / B) on the Marswalk map (as Route B, with its elevation profile).",
    parameters=[ToolParameter("route_id", "string", "Route id, e.g. R2", required=True)],
    category="map",
)
async def display_route_on_map(route_id: str) -> ToolResult:
    rid, r = _find_route(route_id)
    if r is None:
        return ToolResult.error_result(f"Route {route_id} not found. Known: {list(run_ctx().routes) + list(_page_routes())}")
    if rid in ("A", "B"):
        return ToolResult.success_result({"note": f"Route {rid} is already on the map."})
    run_ctx().current_route_id = rid
    return ToolResult.success_result({"map": _pending(_display(rid, r))})


# ============================================================================
# Science tools
# ============================================================================

@tool(
    name="analyse_terrain",
    description="Terrain at a place from the NASA elevation model: elevation, relief, slope statistics and difficulty class "
                "within a radius. Place: name, 'selected', 'rover', or 'lat, lon'.",
    parameters=[
        ToolParameter("location", "string", "Place to analyse (default: the point selected on the map)", default="selected"),
        ToolParameter("radius_m", "number", "Radius in metres (10-2000, default 150)", default=150),
    ],
    category="science",
)
async def analyse_terrain(location: str = "selected", radius_m: float = 150) -> ToolResult:
    p, err = await _place_or_error(location)
    if err:
        return ToolResult.error_result(err)
    radius_m = max(10.0, min(2000.0, float(radius_m)))
    t = await marswalk.terrain(p["lat"], p["lon"], radius_m)
    ctx = run_ctx()
    ctx.selected_area = {"lat": p["lat"], "lon": p["lon"], "name": p["name"], "radius_m": radius_m}
    if not t.get("data_available"):
        return ToolResult.success_result({"place": p["name"], "data_available": False,
                                          "reason": t.get("reason") or "No elevation dataset covers this point.", "data_type": "missing"})
    cls = t.get("classification") or {}
    out = {
        "place": p["name"], "lat": round(p["lat"], 6), "lon": round(p["lon"], 6), "radius_m": radius_m,
        "elevation_m": t.get("elevation_m"), "elevation_range_m": [t.get("elevation_min_m"), t.get("elevation_max_m")],
        "relief_m": t.get("relief_m"), "slope_at_point_deg": t.get("slope_at_point_deg"),
        "slope_mean_deg": t.get("slope_mean_deg"), "slope_p90_deg": t.get("slope_p90_deg"), "slope_max_deg": t.get("slope_max_deg"),
        "area_above_caution_pct": t.get("area_above_caution_pct"), "area_above_limit_pct": t.get("area_above_limit_pct"),
        "difficulty": cls.get("terrain_difficulty"), "difficulty_basis": cls.get("basis"),
        "thresholds": t.get("thresholds"), "source_dataset": t.get("source_dataset"),
        "data_type": {"elevation": "measured (DEM, metres relative to the Mars areoid)", "slope": "calculated (Horn 3x3 on the DEM)",
                      "difficulty": "classification with project-defined thresholds"},
    }
    ctx.analyses.append({"type": "terrain", "place": p["name"], "elevation_m": out["elevation_m"], "slope_p90_deg": out["slope_p90_deg"]})
    return ToolResult.success_result(out)


@tool(
    name="calculate_slope",
    description="Slope between two points from their elevations and horizontal distance (pure calculation).",
    parameters=[
        ToolParameter("elevation1", "number", "Elevation of first point (m)", required=True),
        ToolParameter("elevation2", "number", "Elevation of second point (m)", required=True),
        ToolParameter("distance_m", "number", "Horizontal distance (m)", required=True),
    ],
    category="science",
)
async def calculate_slope(elevation1: float, elevation2: float, distance_m: float) -> ToolResult:
    if distance_m <= 0:
        return ToolResult.error_result("Distance must be positive")
    diff = elevation2 - elevation1
    deg = math.degrees(math.atan(diff / distance_m))
    return ToolResult.success_result({
        "slope_deg": round(deg, 2), "slope_percent": round(diff / distance_m * 100, 2), "elevation_difference_m": round(diff, 2),
        "horizontal_distance_m": distance_m,
        "classification": "gentle" if abs(deg) < 5 else "moderate" if abs(deg) < 15 else "steep",
        "data_type": "calculated from the given values; classes are generic, not project thresholds",
    })


@tool(
    name="estimate_eva",
    description="EVA time budget for a route (walk out, science stops, walk back, suit reserve) and the reference radiation dose. "
                "Route id: R1… or A / B; default is the current route.",
    parameters=[
        ToolParameter("route_id", "string", "Route id (default: current route)"),
        ToolParameter("science_stops", "integer", "Number of science stops (default: stops on the route, else 1)"),
    ],
    category="science",
)
async def estimate_eva(route_id: str = None, science_stops: int = None) -> ToolResult:
    rid, r = _find_route(route_id)
    if r is None:
        return ToolResult.error_result("No route to estimate. Generate a route first (generate_route) or draw one on the map.")
    wps = r.get("waypoints") or [{"lat": p["lat"], "lon": p["lon"]} for p in (r.get("profile") or [])]
    if len(wps) < 2:
        return ToolResult.error_result(f"Route {rid} has no geometry to evaluate.")
    if len(wps) > 4000:
        step = math.ceil(len(wps) / 4000)
        wps = wps[::step] + [wps[-1]]
    n = science_stops if science_stops is not None else max(1, len(r.get("_via") or []) + 1)
    e = await marswalk.eva(wps, list(reversed(wps)), int(n))
    out = {"route_id": rid, "science_stops": n, **{k: e.get(k) for k in (
        "walk_out_min", "science_min", "walk_back_min", "total_min", "suit_limit_min", "reserve_min", "reserve_target_min", "status")},
        "radiation_dose_for_this_eva_mgy": e.get("dose_estimate_mgy"),
        "model": e.get("label"), "dose_note": e.get("dose_note"),
        "data_type": "estimated (walking model with project-defined suit budget); dose is a reference scaled from Gale crater"}
    run_ctx().analyses.append({"type": "eva", "route_id": rid, "total_min": out["total_min"], "status": out["status"]})
    return ToolResult.success_result(out)


@tool(
    name="analyse_environment",
    description="Environmental conditions available to Marswalk: NASA DONKI space weather (flares, SEP, CME), MEDA weather status, "
                "and the radiation reference. For general Mars climate facts use search_knowledge_base.",
    category="science",
)
async def analyse_environment() -> ToolResult:
    out: Dict[str, Any] = {}
    try:
        out["space_weather"] = {**(await marswalk.spaceweather()), "data_type": "observed by NASA (DONKI), Earth-centred catalogue"}
    except MarswalkUnavailable as e:
        out["space_weather"] = {"available": False, "reason": str(e), "data_type": "missing"}
    try:
        out["meda_weather"] = await marswalk.meda()
    except MarswalkUnavailable as e:
        out["meda_weather"] = {"available": False, "reason": str(e).split(": ", 1)[-1], "data_type": "missing"}
    out["radiation_reference"] = {"value": "0.21 mGy/day", "measured_at": "Gale crater by Curiosity RAD",
                                  "data_type": "reference measurement from another site, not a Jezero measurement"}
    run_ctx().analyses.append({"type": "environment"})
    return ToolResult.success_result(out)


# ============================================================================
# Knowledge tools
# ============================================================================

@tool(
    name="search_knowledge_base",
    description="Search the Marswalk knowledge base (Mars facts, human exploration challenges, MOXIE, water, radiation, Jezero "
                "geology, Perseverance, MEDA, DEM methods, and how to use the Marswalk app). Call it BEFORE answering any science or "
                "'how do I' question and cite results as [DOC-n].",
    parameters=[
        ToolParameter("query", "string", "What to look for", required=True),
        ToolParameter("top_k", "integer", "Number of passages (1-6, default 4)", default=4),
    ],
    category="knowledge",
)
async def search_knowledge_base(query: str, top_k: int = 4) -> ToolResult:
    from rag import knowledge_base                       # late import: rag imports models/config
    hits = await knowledge_base.search(query, max(1, min(6, int(top_k))))
    if not hits:
        return ToolResult.success_result({"query": query, "results": [],
                                          "note": "Nothing relevant in the knowledge base. Say so; do not answer from memory."})
    res = []
    ctx = run_ctx()
    for h in hits:
        ctx.citations += 1
        m = h.get("metadata") or {}
        res.append({"cite": f"DOC-{ctx.citations}", "title": m.get("title"), "section": m.get("section"),
                    "source_url": m.get("source_url"), "organization": m.get("organization"),
                    "text": h["content"][:600], "relevance": round(h.get("relevance", 0), 3)})
    return ToolResult.success_result({"query": query, "results": res,
                                      "rule": "Answer only from these passages and cite [DOC-n]. Keep their uncertainty."})


@tool(
    name="retrieve_mission_information",
    description="Mission facts. topic 'perseverance_position' gives the rover's latest localisation (historical, NASA file). Other "
                "topics search the knowledge base: perseverance, jezero, geology, meda, radiation, water, oxygen, communication, health, "
                "mars_facts, marswalk_app.",
    parameters=[
        ToolParameter("topic", "string", "Topic", required=True,
                      enum=["perseverance_position", "perseverance", "jezero", "geology", "meda", "radiation", "water", "oxygen",
                            "communication", "health", "mars_facts", "marswalk_app"]),
        ToolParameter("query", "string", "Specific question (optional)"),
    ],
    category="knowledge",
)
async def retrieve_mission_information(topic: str, query: str = None) -> ToolResult:
    if topic == "perseverance_position":
        rv = await _rover_latest()
        act = _queue_action("highlight_location", f"Mark Perseverance (sol {rv['sol']}) on the map", lat=rv["lat"], lon=rv["lon"],
                            label=f"Perseverance · sol {rv['sol']}", style="rover", zoom=14)
        return ToolResult.success_result({**rv, "data_type": "historical localisation (NASA), not live", "map": _pending(act)})
    q = {"perseverance": "Perseverance rover Mars 2020 instruments", "jezero": "Jezero crater delta lake geology",
         "geology": "Jezero geology delta sedimentary crater floor", "meda": "MEDA weather instrument measurements",
         "radiation": "radiation shielding RAD dose", "water": "water ice subsurface recovery", "oxygen": "MOXIE oxygen production",
         "communication": "communication delay autonomy", "health": "gravity bone muscle health",
         "mars_facts": "Mars gravity day year atmosphere pressure temperature", "marswalk_app": "Marswalk application how to use"}[topic]
    return await search_knowledge_base((query + " " + q) if query else q, 4)


# ============================================================================
# Map / application tools (frontend actions, confirmed by the page)
# ============================================================================

@tool(
    name="highlight_map_location",
    description="Mark a place on the Marswalk map. Place: name, 'selected', 'rover', 'jezero', or 'lat, lon'.",
    parameters=[
        ToolParameter("location", "string", "Place to mark", required=True),
        ToolParameter("label", "string", "Label shown on the marker"),
    ],
    category="map",
)
async def highlight_map_location(location: str, label: str = None) -> ToolResult:
    p, err = await _place_or_error(location)
    if err:
        return ToolResult.error_result(err)
    act = _queue_action("highlight_location", f"Mark {label or p['name']}", lat=p["lat"], lon=p["lon"], label=label or p["name"], zoom=16)
    return ToolResult.success_result({"place": p["name"], "lat": p["lat"], "lon": p["lon"], "map": _pending(act)})


@tool(
    name="zoom_map",
    description="Move/zoom the map to a place: name ('habitat', 'delta front', 'stop 3'), 'jezero' (whole crater), 'rover', "
                "'selected', 'route' (fit the current route) or 'lat, lon'.",
    parameters=[
        ToolParameter("location", "string", "Where to go", required=True),
        ToolParameter("zoom", "integer", "Zoom level 3-19 (default 16; 'jezero' uses 11)"),
    ],
    category="map",
)
async def zoom_map(location: str, zoom: int = None) -> ToolResult:
    if str(location).strip().lower() in ("route", "the route", "current route", "routes"):
        rid, r = _find_route(None)
        pts = (r or {}).get("waypoints") or [{"lat": p["lat"], "lon": p["lon"]} for p in ((r or {}).get("profile") or [])]
        if not pts:
            return ToolResult.error_result("No route to zoom to.")
        lats, lons = [p["lat"] for p in pts], [p["lon"] for p in pts]
        act = _queue_action("fit_bounds", f"Fit the map to route {rid}", bounds=[[min(lats), min(lons)], [max(lats), max(lons)]])
        return ToolResult.success_result({"route_id": rid, "map": _pending(act)})
    p, err = await _place_or_error(location)
    if err:
        return ToolResult.error_result(err)
    z = int(zoom) if zoom else (11 if p.get("name", "").startswith("Jezero Crater") else 16)
    z = max(3, min(19, z))
    run_ctx().selected_area = {"lat": p["lat"], "lon": p["lon"], "name": p["name"]}
    act = _queue_action("zoom_to", f"Zoom to {p['name']}", lat=p["lat"], lon=p["lon"], zoom=z)
    return ToolResult.success_result({"place": p["name"], "map": _pending(act)})


@tool(
    name="change_map_layer",
    description="Show or hide a map layer by id or name (e.g. 'hirise', 'mola', 'themis', 'slope', 'hillshade', 'ctx').",
    parameters=[
        ToolParameter("layer", "string", "Layer id or name", required=True),
        ToolParameter("visible", "boolean", "true = show, false = hide", default=True),
    ],
    category="map",
)
async def change_map_layer(layer: str, visible: bool = True) -> ToolResult:
    catalogue = (run_ctx().page or {}).get("layers") or []
    q = str(layer).strip().lower()
    match = None
    for c in catalogue:
        if q == str(c.get("id", "")).lower():
            match = c
            break
    if not match:
        for c in catalogue:
            hay = " ".join([str(c.get("id", "")), str(c.get("label", ""))] + [str(k) for k in c.get("keywords", [])]).lower()
            if q in hay or all(w in hay for w in q.split()):
                match = c
                break
    if not match:
        ids = ", ".join(f"{c.get('id')} ({c.get('label')})" for c in catalogue) or "none reported by the page"
        return ToolResult.error_result(f"No layer matches '{layer}'. Available layers: {ids}")
    act = _queue_action("change_layer", f"{'Show' if visible else 'Hide'} layer {match.get('label') or match['id']}",
                        layer_id=match["id"], visible=bool(visible))
    return ToolResult.success_result({"layer": match.get("label") or match["id"], "visible": bool(visible), "map": _pending(act)})


@tool(
    name="navigate_application",
    description="Open part of the Marswalk app: 'layers', 'route' or 'environment' tab, '2d' map, '3d' globe, 'mission_log' "
                "(in this assistant), 'presentation' (full-screen view) or 'exit_presentation'.",
    parameters=[ToolParameter("target", "string", "Where to go", required=True,
                              enum=["layers", "route", "environment", "2d", "3d", "mission_log", "presentation", "exit_presentation"])],
    category="map",
)
async def navigate_application(target: str) -> ToolResult:
    act = _queue_action("navigate", f"Open {target.replace('_', ' ')}", target=target)
    return ToolResult.success_result({"target": target, "map": _pending(act)})


@tool(
    name="start_route_journey",
    description="Start the animated route journey (rover moving along the route, 2D or 3D). route: 'B' (auto / assistant routes) or 'A' "
                "(hand-drawn).",
    parameters=[ToolParameter("route", "string", "A or B (default B)", default="B", enum=["A", "B"])],
    category="map",
)
async def start_route_journey(route: str = "B") -> ToolResult:
    if route not in _page_routes() and not (route == "B" and run_ctx().current_route_id):
        return ToolResult.error_result(f"Route {route} is not on the map. Generate or draw a route first.")
    act = _queue_action("start_journey", f"Start the journey along Route {route}", route=route)
    return ToolResult.success_result({"route": route, "map": _pending(act)})


# ============================================================================
# Mission log (records stored by the Marswalk backend)
# ============================================================================

@tool(
    name="save_to_mission_log",
    description="Save a place to the mission log with its DEM elevation and slope (default: the point selected on the map).",
    parameters=[
        ToolParameter("location", "string", "Place to save (default: selected point)", default="selected"),
        ToolParameter("note", "string", "Optional note to store with it"),
    ],
    requires_permission=False,
    category="mission",
)
async def save_to_mission_log(location: str = "selected", note: str = None) -> ToolResult:
    p, err = await _place_or_error(location)
    if err:
        return ToolResult.error_result(err)
    try:
        e = await marswalk.elevation(p["lat"], p["lon"])
    except MarswalkUnavailable:
        e = {"elevation_m": None, "slope_deg": None, "data_available": False, "source_dataset": "unavailable"}
    rec = {"label": p["name"][:200], "lat": p["lat"], "lon": p["lon"], "analysis_type": "location",
           "result": (f"Elevation {e.get('elevation_m', 'n/a')} m, slope {e.get('slope_deg', 'n/a')}°" + (f" · {note}" if note else ""))[:1000],
           "elevation_m": e.get("elevation_m"), "slope_deg": e.get("slope_deg"), "dataset": (e.get("source_dataset") or "")[:300] or None,
           "status": "complete" if e.get("data_available") else "data unavailable"}
    saved = await marswalk.add_record(rec)
    _queue_action("refresh_log", "Refresh the mission log view")
    return ToolResult.success_result({"saved": {k: saved.get(k) for k in ("id", "label", "result", "created_at")},
                                      "data_type": "elevation measured (DEM), slope calculated"})


@tool(
    name="get_mission_log",
    description="Read the mission log (saved places, analyses and EVA plans), newest first; optional text filter.",
    parameters=[ToolParameter("query", "string", "Optional filter text", default="")],
    category="mission",
)
async def get_mission_log(query: str = "") -> ToolResult:
    rows = await marswalk.records(query or "")
    return ToolResult.success_result({"count": len(rows), "entries": [
        {k: r.get(k) for k in ("label", "analysis_type", "result", "created_at", "status")} for r in rows[:12]]})


def init_tools():
    """Tools are registered by the decorators; log what is available."""
    logger.info("Initialized %d tools: %s", len(tool_registry._tools), ", ".join(tool_registry._tools))
