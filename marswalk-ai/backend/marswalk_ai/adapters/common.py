"""Helpers shared by adapters: resolving places and points, building route records."""
from __future__ import annotations

import time

from ..registry import CapabilityError

POINT_TARGETS = ["current_position", "poi", "point", "selected", "map_center", "active_route_end", "route_steepest"]


def poi_brief(p: dict) -> dict:
    return {"poi_id": p["id"], "name": p["name"], "kind": p.get("kind"), "lat": p["lat"], "lon": p["lon"]}


def resolve_place(name_or_id: str, ctx) -> dict:
    """A place given by POI id, name/alias, or a keyword (current_position, selected, map_center, active_route_end)."""
    m = ctx.services.mission
    key = (name_or_id or "").strip()
    low = key.lower().replace(" ", "_")
    if low in ("current_position", "here", "my_position", "current_location"):
        return {**m.current_position(), "kind": "current_position"}
    if low in ("selected", "selected_point", "this_location"):
        sel = ctx.session.client_context.get("selected") or {}
        if "lat" not in sel:
            raise CapabilityError("no_selection", "No location is selected on the map.")
        return {"poi_id": None, "name": sel.get("name") or "Selected point", "lat": sel["lat"], "lon": sel["lon"], "kind": "selected"}
    if low in ("map_center", "map_centre"):
        c = (ctx.session.client_context.get("map") or {}).get("center") or {}
        if "lat" not in c:
            raise CapabilityError("no_map_state", "The map centre is not known (the frontend did not report it).")
        return {"poi_id": None, "name": "Map centre", "lat": c["lat"], "lon": c["lon"], "kind": "map_center"}
    if low in ("active_route_end", "route_end"):
        r = ctx.session.routes.get(ctx.session.active_route_id or "")
        if not r:
            raise CapabilityError("no_active_route", "There is no active route.")
        return {**r["destination"], "kind": "route_end"}
    p = m.get(key) or next(iter(m.resolve(key)), None)
    if not p:
        known = ", ".join(f"{q['id']} ({q['name']})" for q in m.pois)
        raise CapabilityError("unknown_place", f"'{name_or_id}' does not match a known place. Known places: {known}.")
    return {**poi_brief(p), "kind": p.get("kind")}


def resolve_point(args: dict, ctx) -> dict:
    """Resolve a {target, poi_id, lat, lon, route_id} argument set to a concrete, grounded point."""
    target = args.get("target") or ("poi" if args.get("poi_id") else "point" if "lat" in args else "current_position")
    m = ctx.services.mission
    if target == "poi":
        if not args.get("poi_id"):
            raise CapabilityError("invalid_arguments", "target 'poi' needs poi_id.")
        return resolve_place(args["poi_id"], ctx)
    if target == "point":
        lat, lon = args.get("lat"), args.get("lon")
        if lat is None or lon is None:
            raise CapabilityError("invalid_arguments", "target 'point' needs lat and lon.")
        if not ctx.turn.coordinate_is_grounded(lat, lon, m):
            raise CapabilityError("ungrounded_coordinates",
                                  f"Coordinates ({lat}, {lon}) do not come from a tool result, the map state or the user's message; "
                                  "they will not be used.")
        return {"poi_id": None, "name": args.get("label") or "Point", "lat": lat, "lon": lon, "kind": "point"}
    if target == "route_steepest":
        rid = args.get("route_id") or ctx.session.active_route_id
        r = ctx.session.routes.get(rid or "")
        if not r:
            raise CapabilityError("no_active_route", "There is no route to take the steepest point from.")
        sp = r["steepest_point"]
        return {"poi_id": None, "name": f"Steepest point on {rid} ({sp['slope_deg']}°)", "lat": sp["lat"], "lon": sp["lon"], "kind": "route_steepest"}
    return resolve_place(target, ctx)


def check_in_mission_area(pt: dict, ctx) -> list[str]:
    if ctx.services.mission.in_bounds(pt["lat"], pt["lon"]):
        return []
    return [f"{pt['name']} ({pt['lat']:.5f}°N, {pt['lon']:.5f}°E) is outside the Jezero planning area used by Marswalk."]


def route_record(session, r: dict, start: dict, dest: dict, dataset: dict, max_slope: float, caution: float,
                 provider: str, data_mode: str, algorithm: str) -> dict:
    return {
        "route_id": session.next_route_id(), "label": r["label"], "objective": r.get("objective"),
        "start": start, "destination": dest,
        "geometry": r["geometry"], "slope_classes": r.get("slope_classes", []), "profile": r.get("profile", []),
        "metrics": r["metrics"], "steepest_point": r.get("steepest_point"), "steep_sections": r.get("steep_sections", []),
        "constraints": {"max_slope_deg": max_slope, "caution_slope_deg": caution, "source": "project-defined"},
        "provenance": {"provider": provider, "dataset": dataset, "data_mode": data_mode, "algorithm": algorithm},
        "recommended": False, "created_at": time.time(),
    }


def route_brief(r: dict) -> dict:
    m = r["metrics"]
    return {"route_id": r["route_id"], "label": r["label"], "objective": r.get("objective"),
            "from": r["start"].get("name"), "to": r["destination"].get("name"),
            "distance_m": m.get("distance_m"), "elevation_gain_m": m.get("elevation_gain_m"),
            "elevation_loss_m": m.get("elevation_loss_m"), "max_slope_deg": m.get("max_slope_deg"),
            "avg_slope_deg": m.get("avg_slope_deg"), "length_above_caution_m": m.get("length_above_caution_m"),
            "weighted_cost": m.get("weighted_cost"), "walk_time_out_min": m.get("walk_time_out_min"),
            "within_constraints": m.get("within_constraints"), "recommended": r.get("recommended", False),
            "steepest_point": r.get("steepest_point"), "data_mode": r["provenance"].get("data_mode")}
