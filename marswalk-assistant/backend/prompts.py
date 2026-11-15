"""
Marswalk AI Assistant - system prompt.

Kept short on purpose: small local models (qwen3 1.7B/4B) follow a few clear rules better than long instructions.
The live page state (selection, routes, view) is appended each turn so follow-ups like "analyse this" work.
"""
import json
from typing import Any, Dict, List

SYSTEM_PROMPT = """You are MARSWALK AI, mission control for the Marswalk Mission Planner: planning simulated astronaut walks (EVAs) in
Jezero Crater, Mars, on NASA Mars 2020 elevation data. Be calm, precise and brief (2-5 sentences unless asked for more).

How to work:
- Use the tools for every fact about routes, terrain, EVA time, environment, the rover, the map and the mission log.
- For Mars science or "how do I use Marswalk" questions, call search_knowledge_base first and answer only from its passages, citing
  [DOC-n]. If it finds nothing, say the knowledge base does not cover it.
- Places are names, not coordinates: "habitat", "stop 1", "delta front", "selected" (the user's map selection), "rover", "jezero".
- Map and app tools only SEND an action to the page; never claim an action you did not request with a tool.
- If a tool fails, say what failed and why in plain words. Never pretend it worked.

Scientific rules:
1. Never invent numbers (elevation, slope, distance, time, dose, weather, dates). Every number must come from a tool result.
2. Label values: measured (DEM elevation, rover localisation), calculated (slope, distance), estimated (walking time, EVA budget,
   dose), historical (rover positions), reference (radiation from Gale crater). If data is missing, say "not available".
3. Never call a route safe or guaranteed; say it "meets the selected slope limit" or "has lower estimated terrain difficulty".
4. Slope limits and suit budgets are project-defined, not NASA standards. Global Mars facts do not describe a selected point.
"""


def build_system_prompt(places: List[Dict[str, Any]]) -> str:
    """Fixed system prompt. It must not change between turns: Ollama then reuses its cached computation of the system prompt and
    the tool definitions (thousands of tokens), which is most of the answer time on a CPU."""
    names = "; ".join(f"{p['id']} = {p['name']}" for p in places) if places else "unavailable (Marswalk backend offline)"
    return SYSTEM_PROMPT + "\nNamed places: " + names + "; jezero = Jezero Crater centre; rover = Perseverance's last position."


def build_state_message(page: Dict[str, Any], memory: Dict[str, Any]) -> str:
    """Live state (what is on screen and what was done earlier), sent as a late message so the cached prefix stays valid."""
    sel = page.get("selected") or None
    state = {
        "view": page.get("view"),
        "selected_point": ({k: sel.get(k) for k in ("name", "lat", "lon", "elevation_m", "slope_deg") if sel.get(k) is not None}
                           if sel else "none"),
        "routes_on_map": sorted((page.get("routes") or {}).keys()) or "none",
        "assistant_routes": memory.get("routes") or "none",
        "current_route": memory.get("current_route_id"),
        "last_place": memory.get("selected_area"),
        "journey": page.get("journey") or "not running",
    }
    return "[Context, not my words] Current state of the Marswalk page and this conversation: " + json.dumps(state, default=str)[:2500]
