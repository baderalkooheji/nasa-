"""Deterministic result cards built from tool results (never from LLM text).

Card actions are either
  {"kind": "command", "label", "capability_id", "arguments"}  -> POST /api/assistant/command (validated by the registry)
  {"kind": "prompt",  "label", "text"}                         -> sent as a chat message
"""
from __future__ import annotations

from .planner import normalize_eva


def _cmd(label, cap, args, style="secondary"):
    return {"kind": "command", "label": label, "capability_id": cap, "arguments": args, "style": style}


def _prompt(label, text):
    return {"kind": "prompt", "label": label, "text": text}


def build_cards(results: list, session) -> list[dict]:
    ui = set(session.frontend.get("capabilities", []))
    cards: list[dict] = []
    for r in results:
        if not r.ok or r.status not in ("success",):
            continue
        d, cid, prov = r.data, r.capability_id, r.provenance
        if cid == "route.generate":
            rec = d.get("recommended_route_id")
            actions = []
            if "map.display_route" in ui and rec:
                actions.append(_cmd("Show recommended route", "map.display_route", {"route_id": rec}, "primary"))
            if len(d["routes"]) > 1:
                actions.append(_prompt("Compare alternatives", "Compare the candidate routes"))
            if rec:
                actions.append(_prompt("Explain recommendation", "Explain why you recommended this route"))
            cards.append({"type": "route_set", "title": f"Routes to {d['destination']['name']}", "data": {
                "routes": d["routes"], "recommended_route_id": rec, "rationale": d.get("recommendation_rationale"),
                "max_slope_deg": d.get("max_slope_deg"), "dataset": d.get("dataset"), "notes": d.get("notes")},
                "route_actions": ({x["route_id"]: [_cmd("Show", "map.display_route", {"route_id": x["route_id"],
                                                                                       "emphasis": "primary" if x["route_id"] == rec else "alternative"})]
                                   for x in d["routes"]} if "map.display_route" in ui else {}),
                "actions": actions, "provenance": prov})
        elif cid == "route.compare":
            cards.append({"type": "comparison", "title": "Route comparison", "data": d,
                          "actions": [_cmd(f"Show {d['best_by']['lowest_terrain_cost']}", "map.display_route",
                                           {"route_id": d["best_by"]["lowest_terrain_cost"]}, "primary")] if "map.display_route" in ui else [],
                          "provenance": prov})
        elif cid == "route.evaluate":
            rid = (d.get("route") or {}).get("route_id")
            acts = []
            if "map.highlight_location" in ui and d.get("steepest_point"):
                acts.append(_cmd("Highlight steepest point", "map.highlight_location", {"target": "route_steepest", "route_id": rid, "style": "caution"}))
            cards.append({"type": "route_detail", "title": f"Route {rid} terrain", "data": d, "actions": acts, "provenance": prov})
        elif cid == "terrain.analyze" and d.get("data_available"):
            cards.append({"type": "terrain", "title": f"Terrain · {(d.get('location') or {}).get('name', 'point')}", "data": d,
                          "actions": [_cmd("Highlight on map", "map.highlight_location",
                                           {"target": "point", "lat": d["location"]["lat"], "lon": d["location"]["lon"],
                                            "label": d["location"].get("name")})] if "map.highlight_location" in ui else [],
                          "provenance": prov})
        elif cid == "eva.estimate":
            cards.append({"type": "eva", "title": f"EVA budget · {d.get('route_id', '')}", "data": normalize_eva(d) | {"route_id": d.get("route_id")},
                          "actions": [_prompt("Radiation for this EVA", "What radiation dose would this EVA involve?")], "provenance": prov})
        elif cid == "radiation.estimate":
            cards.append({"type": "radiation", "title": "Radiation estimate", "data": d, "actions": [], "provenance": prov})
        elif cid == "space_weather.analyze":
            cards.append({"type": "space_weather", "title": "Space weather", "data": d, "actions": [], "provenance": prov})
        elif cid == "environment.analyze":
            cards.append({"type": "environment", "title": "Environment (MEDA)", "data": d, "actions": [], "provenance": prov})
        elif cid == "rover.get_location":
            loc = d["latest_localisation"]
            cards.append({"type": "location", "title": f"Perseverance · sol {loc['sol']}", "data": d,
                          "actions": [_cmd("Show on map", "map.zoom_to_location", {"target": "point", "lat": loc["lat"], "lon": loc["lon"], "zoom": 15})]
                          if "map.zoom_to_location" in ui else [], "provenance": prov})
        elif cid == "mission.get_context" and r.origin != "internal":
            cards.append({"type": "mission", "title": d["mission"]["name"], "data": d, "actions": [], "provenance": prov})
        elif cid == "poi.get_nearby":
            cards.append({"type": "places", "title": "Nearby places", "data": d,
                          "actions": [_prompt(f"Route to {p['name'].split(' · ')[0]}", f"Find a route to {p['poi_id']}") for p in
                                      (d.get("unvisited_planned_stops") or d.get("places") or [])[:2]], "provenance": prov})
    # one mission card per response is enough
    seen, out = set(), []
    for c in cards:
        if c["type"] == "mission" and len(cards) > 1:
            continue
        key = (c["type"], c["title"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def suggestions_for(session, intent_name: str) -> list[str]:
    s: list[str] = []
    has_route = bool(session.routes)
    if not has_route:
        s += ["Find me the best route to the delta", "Show me the Perseverance rover location", "Analyze the terrain around the habitat"]
    else:
        s += ["Show me the steepest section", "How long will this journey take?", "Find an alternative with less steep terrain"]
        if len(session.routes) > 1:
            s.append("Compare the routes")
        s.append("What are the biggest risks ahead?")
    if intent_name in ("route_find", "route_alternative", "route_shorter"):
        s.insert(0, "Explain why you recommended this route")
    return list(dict.fromkeys(s))[:4]
