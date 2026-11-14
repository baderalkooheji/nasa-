"""Standalone adapter: capabilities that work without the original Marswalk backend.

Data honesty:
  * places      - real Perseverance positions (names are Marswalk's starter set)
  * terrain     - real CTX / HiRISE DEMs when configured, otherwise SIMULATED (labelled)
  * routes      - computed by this project's standalone route service (not the Marswalk engine)
  * EVA / dose  - project-defined models, used only when the Science Engine is not reachable
  * environment / space weather - NOT available here (no data source); reported as unavailable
"""
from __future__ import annotations

import time

from ..registry import Capability, CapabilityError, CapabilityRegistry, CapResult
from ..schema import LAT, LON, obj
from ..services.routing import RouteError, recommend
from .common import (POINT_TARGETS, check_in_mission_area, poi_brief, resolve_place, resolve_point, route_brief,
                     route_record)

PROVIDER = "standalone"
ROUTE_ALGO = "Dijkstra least-cost path on an 8-connected slope grid (scipy); cost = 1 + (slope/max_slope)^2, cells above the limit blocked"

POINT_ARGS = {
    "target": {"type": "string", "enum": POINT_TARGETS,
               "description": "Which point: current_position, poi (needs poi_id), point (needs lat/lon from a tool result or the user), "
                              "selected (map selection), map_center, active_route_end, route_steepest"},
    "poi_id": {"type": "string", "description": "Place id or name, e.g. 'stop-1' or 'delta'"},
    "lat": LAT, "lon": LON,
    "route_id": {"type": "string", "description": "Route id for route_steepest (default: active route)"},
}


def _route_limits(ctx, dataset: dict, data_mode: str, max_slope: float) -> list[str]:
    s = ctx.services.settings
    lim = [
        f"Routes come from the standalone route service on {dataset.get('name')} resampled to {dataset.get('resolution_m')} m; "
        "obstacles smaller than one grid cell (boulders, small ledges) are not resolved.",
        f"Slope thresholds are project-defined (limit {max_slope:g}°, caution {s.caution_slope_deg:g}°), not NASA standards.",
        f"Walking times use a modified terrestrial hiking model (Tobler, k_suit = {s.k_suit}); they are estimates, not a validated Mars EVA model.",
        "Meeting the slope limit does not make a route safe. This is a preliminary terrain assessment only.",
        "Standalone service: re-verify routes with the Marswalk route engine once the assistant is connected to it.",
    ]
    if data_mode != "real":
        lim.insert(0, "SIMULATED TERRAIN: no DEM is configured, so these numbers are not Mars measurements.")
    return lim


def register(registry: CapabilityRegistry, services) -> None:
    s = services.settings
    terrain, planner, kb = services.terrain, services.planner, services.knowledge
    terrain_mode = terrain.data_mode if terrain else "none"

    # ------------------------------------------------------------------ mission
    async def get_context(args, ctx):
        sess, m = ctx.session, ctx.services.mission
        active = sess.routes.get(sess.active_route_id or "")
        data = {
            "mission": {"name": m.mission["name"], "status": m.mission["status"], "status_source": m.mission["status_source"]},
            "current_position": m.current_position(),
            "destination": sess.destination,
            "planned_stops": [poi_brief(m.get(pid)) for pid in m.mission.get("planned_stops", []) if m.get(pid)],
            "active_route": route_brief(active) if active else None,
            "previous_route_id": sess.previous_route_id,
            "route_count": len(sess.routes),
            "ui": {k: sess.client_context.get(k) for k in ("section", "visible_layers", "selected", "map", "displayed_routes")
                   if k in sess.client_context},
            "preferences": {"max_slope_deg": sess.preferences.get("max_slope_deg", s.max_slope_deg),
                            "stop_minutes": sess.preferences.get("stop_minutes", s.stop_minutes),
                            "suit_budget_hours": sess.preferences.get("suit_budget_hours", s.suit_budget_hours)},
            "terrain_datasets": [{"id": d["id"], "name": d["name"], "data_mode": d["data_mode"]} for d in (terrain.describe() if terrain else [])],
            "integrations": ctx.services.status,
        }
        return CapResult(data, limitations=["Mission status and astronaut position are MOCK values in the standalone environment."])

    registry.register(Capability(
        id="mission.get_context", name="Get mission context", category="mission", provider=PROVIDER, data_mode="mixed",
        description="Current mission state: name, status, current position, destination, active route, planned stops, UI state "
                    "(section, visible layers, selected point) and data sources. Call this before answering questions about 'my route', "
                    "'my location' or 'this location'.",
        input_schema=obj({}), output_schema=obj({"mission": {"type": "object"}, "current_position": {"type": "object"}}, ["mission"]),
        handler=get_context, notes="Astronaut position and mission status are mock values (Marswalk has no such model)."))

    async def get_history(args, ctx):
        sess = ctx.session
        recs = ctx.services.store.list_records(args.get("query", ""), args["limit"])
        return CapResult({"analyses": sess.analyses[-args["limit"]:],
                          "routes": [route_brief(r) for r in list(sess.routes.values())[-args["limit"]:]],
                          "saved_records": recs})

    registry.register(Capability(
        id="mission.get_history", name="Get mission history", category="mission", provider=PROVIDER, data_mode="n/a",
        description="Previous analyses and routes in this session, plus saved mission-log records.",
        input_schema=obj({"query": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}),
        output_schema=obj({"analyses": {"type": "array"}}, ["analyses"]), handler=get_history))

    async def save_record(args, ctx):
        rec = ctx.services.store.add_record(ctx.session.id, args)
        return CapResult({"saved": True, "record": rec})

    registry.register(Capability(
        id="mission.save_record", name="Save to mission log", category="mission", provider=PROVIDER, data_mode="n/a",
        description="Save a note, route or analysis to the persistent mission log. Requires the user's confirmation.",
        input_schema=obj({"label": {"type": "string", "minLength": 1, "maxLength": 120}, "note": {"type": "string", "maxLength": 1000},
                          "route_id": {"type": "string"}, "analysis_id": {"type": "string"}}, ["label"]),
        output_schema=obj({"saved": {"type": "boolean"}}, ["saved"]), handler=save_record,
        changes_state=True, permission="confirm"))

    async def set_prefs(args, ctx):
        ctx.session.preferences.update({k: v for k, v in args.items() if v is not None})
        return CapResult({"preferences": ctx.session.preferences})

    registry.register(Capability(
        id="mission.set_preferences", name="Set planning preferences", category="mission", provider=PROVIDER,
        description="Store the user's planning preferences for this session (maximum slope, minutes per science stop, suit time budget).",
        input_schema=obj({"max_slope_deg": {"type": "number", "minimum": 5, "maximum": 25},
                          "stop_minutes": {"type": "number", "minimum": 0, "maximum": 240},
                          "suit_budget_hours": {"type": "number", "minimum": 0.5, "maximum": 12}}),
        output_schema=obj({"preferences": {"type": "object"}}, ["preferences"]), handler=set_prefs, changes_state=True, permission="write"))

    # ------------------------------------------------------------------ places / rover
    async def poi_search(args, ctx):
        m = ctx.services.mission
        q = args["query"]
        hits = m.resolve(q) or ([m.get(q)] if m.get(q) else [])
        return CapResult({"matches": [poi_brief(p) | {"aliases": p.get("aliases", []), "reason": p.get("reason"),
                                                      "provenance": p.get("provenance")} for p in hits],
                          "known_places": [poi_brief(p) for p in m.pois]})

    registry.register(Capability(
        id="poi.search", name="Find a named place", category="mission", provider=PROVIDER, data_mode="real",
        description="Resolve a place name (e.g. 'delta', 'habitat', 'landing site', 'stop 2') to a known mission location.",
        input_schema=obj({"query": {"type": "string", "minLength": 1, "maxLength": 120}}, ["query"]),
        output_schema=obj({"matches": {"type": "array"}}, ["matches"]), handler=poi_search,
        notes="Positions are real Perseverance localisations; names/reasons are Marswalk's starter set."))

    async def nearby(args, ctx):
        from ..services.terrain import ground_distance_m
        pt = resolve_point(args, ctx)
        m, radius = ctx.services.mission, args["radius_m"]
        pois = []
        for p in m.pois:
            d = ground_distance_m(pt["lat"], pt["lon"], p["lat"], p["lon"])
            if d <= radius and not (pt.get("poi_id") == p["id"]):
                pois.append(poi_brief(p) | {"distance_m": round(d, 0), "reason": p.get("reason")})
        pois.sort(key=lambda x: x["distance_m"])
        visited = {r["destination"].get("poi_id") for r in ctx.session.routes.values()}
        rover = [w for w in m.nearest_rover(pt["lat"], pt["lon"], 5) if w["distance_m"] <= radius]
        return CapResult({"origin": pt, "radius_m": radius, "places": pois,
                          "unvisited_planned_stops": [x for x in pois if x["poi_id"] in m.mission.get("planned_stops", []) and x["poi_id"] not in visited],
                          "rover_localisations_nearby": rover},
                         limitations=["Rover localisations are historical (NASA/JPL-Caltech MMGIS snapshot), not live positions."])

    registry.register(Capability(
        id="poi.get_nearby", name="Places near a point", category="mission", provider=PROVIDER, data_mode="real",
        description="Known mission places and historical Perseverance localisations within a radius of a point. Useful for 'what should I investigate next'.",
        input_schema=obj({**POINT_ARGS, "radius_m": {"type": "number", "minimum": 50, "maximum": 20000, "default": 1500}}),
        output_schema=obj({"places": {"type": "array"}}, ["places"]), handler=nearby))

    async def rover_loc(args, ctx):
        m = ctx.services.mission
        latest = m.latest_rover()
        data = {"latest_localisation": {"sol": latest["sol"], "lat": latest["lat"], "lon": latest["lon"],
                                        "elev_geoid_m": latest["elev_geoid_m"], "dist_total_m": latest.get("dist_total_m")},
                "source": latest["source"], "is_live": False,
                "snapshot_note": latest["note"]}
        return CapResult(data, provenance={"dataset": "M20_waypoints.json snapshot"},
                         limitations=[f"This is the last localisation in the local snapshot (sol {latest['sol']}), not a live rover position."])

    registry.register(Capability(
        id="rover.get_location", name="Perseverance rover location", category="mission", provider=PROVIDER, data_mode="real",
        description="Latest Perseverance localisation in the local NASA waypoint snapshot (historical, with sol number).",
        input_schema=obj({}), output_schema=obj({"latest_localisation": {"type": "object"}}, ["latest_localisation"]), handler=rover_loc))

    # ------------------------------------------------------------------ routes
    async def route_generate(args, ctx):
        sess = ctx.session
        if planner is None:
            raise CapabilityError("terrain_unavailable", "No terrain data is configured, so routes cannot be computed.")
        start = resolve_place(args["start"], ctx)
        dest = resolve_place(args["destination"], ctx)
        warn = check_in_mission_area(start, ctx) + check_in_mission_area(dest, ctx)
        max_slope = args.get("max_slope_deg") or sess.preferences.get("max_slope_deg") or s.max_slope_deg
        objectives = ["balanced", "min_slope", "shortest"] if args["mode"] == "candidates" else [args["mode"]]
        avoid_ids = args.get("avoid_route_ids") or []
        unknown = [i for i in avoid_ids if i not in sess.routes]
        if unknown:
            raise CapabilityError("unknown_route", f"Cannot avoid unknown route(s): {', '.join(unknown)}.")
        try:
            routes, info = planner.plan((start["lat"], start["lon"]), (dest["lat"], dest["lon"]), max_slope, objectives, s.caution_slope_deg,
                                        avoid=[sess.routes[i]["geometry"] for i in avoid_ids])
        except RouteError as e:
            raise CapabilityError(e.code, e.message)
        mode = info["dataset"]["data_mode"]
        records = [route_record(sess, r, start, dest, info["dataset"], max_slope, s.caution_slope_deg, PROVIDER, mode, ROUTE_ALGO) for r in routes]
        if avoid_ids:
            for r in records:
                r["label"] += f" (avoiding {', '.join(avoid_ids)})"
        if len(records) > 1:
            best, why = recommend(records)
            if best is not None:
                records[best]["recommended"] = True
                records[best]["rationale"] = why
            rec_id = records[best]["route_id"] if best is not None else None
        else:                                    # a single requested route is not a recommendation
            best, why, rec_id = None, [], None
        rs = sess.add_route_set(records, rec_id, {"start": start, "destination": dest, "max_slope_deg": max_slope, "mode": args["mode"],
                                                  "avoid_route_ids": avoid_ids})
        sess.destination = dest
        notes = []
        if info.get("end_on_steep_cell"):
            notes.append(f"The destination cell itself is steeper than {max_slope:g}° (reported separately; the route statistics exclude the endpoints).")
        if info.get("start_on_steep_cell"):
            notes.append(f"The start cell itself is steeper than {max_slope:g}°.")
        sess.add_analysis("route_generation", f"{len(records)} route(s) {start['name']} → {dest['name']}",
                          {"route_ids": rs["route_ids"], "recommended": rs["recommended_id"]}, PROVIDER, mode)
        return CapResult(
            {"route_set_id": rs["set_id"], "routes": [route_brief(r) for r in records],
             "recommended_route_id": rs["recommended_id"], "recommendation_rationale": why if best is not None else
             (["No candidate meets the slope limit; none is recommended."] if len(records) > 1 else []),
             "start": start, "destination": dest, "max_slope_deg": max_slope, "dataset": info["dataset"],
             "notes": notes + warn},
            provenance={"dataset": info["dataset"]["name"], "algorithm": ROUTE_ALGO},
            limitations=_route_limits(ctx, info["dataset"], mode, max_slope))

    registry.register(Capability(
        id="route.generate", name="Generate route", category="route", provider=PROVIDER, data_mode=terrain_mode,
        availability="available" if planner else "unavailable", availability_reason="" if planner else "No terrain data configured.",
        description="Compute walking routes from a start to a destination on the elevation model, keeping every traversed cell at or below the "
                    "maximum slope. mode='candidates' returns up to three alternatives (balanced / gentlest / most direct) and a deterministic "
                    "recommendation; 'shortest', 'min_slope' or 'balanced' return one route. Routes are stored and referenced by route_id.",
        input_schema=obj({
            "destination": {"type": "string", "minLength": 1, "description": "Place id or name (e.g. 'stop-1', 'delta', 'landing site'), or 'selected' / 'map_center'"},
            "start": {"type": "string", "default": "current_position", "description": "Place id/name, 'current_position' (default) or 'active_route_end'"},
            "mode": {"type": "string", "enum": ["candidates", "balanced", "min_slope", "shortest"], "default": "candidates"},
            "max_slope_deg": {"type": "number", "minimum": 5, "maximum": 25, "description": "Project-defined slope limit (default 15°)"},
            "avoid_route_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                                "description": "Stored routes to steer away from (for a genuinely different alternative)"},
        }, ["destination"]),
        output_schema=obj({"routes": {"type": "array"}, "recommended_route_id": {"type": ["string", "null"]}}, ["routes"]),
        handler=route_generate, changes_state=False, timeout_s=90,
        notes="Standalone route service. Replace with the Marswalk route engine (marswalk_http adapter) when available."))

    def _route(ctx, rid):
        rid = rid or ctx.session.active_route_id
        r = ctx.session.routes.get(rid or "")
        if not r:
            raise CapabilityError("unknown_route", f"No route '{rid}'. Known routes: {', '.join(ctx.session.routes) or 'none'}.")
        return r

    async def route_evaluate(args, ctx):
        r = _route(ctx, args.get("route_id"))
        m = r["metrics"]
        cls = terrain.classify(m.get("max_slope_deg"), m.get("avg_slope_deg"), r["constraints"]["max_slope_deg"]) if terrain else {}
        ctx.session.set_active_route(r["route_id"])
        ctx.session.add_analysis("route_evaluation", f"{r['route_id']}: max {m.get('max_slope_deg')}°, {m.get('distance_m')} m",
                                 {"route_id": r["route_id"]}, r["provenance"]["provider"], r["provenance"]["data_mode"])
        return CapResult({"route": route_brief(r), "metrics": m, "steepest_point": r.get("steepest_point"),
                          "steep_sections": r.get("steep_sections", []), "classification": cls, "constraints": r["constraints"],
                          "rationale": r.get("rationale"), "dataset": r["provenance"].get("dataset")},
                         limitations=_route_limits(ctx, r["provenance"].get("dataset", {}), r["provenance"]["data_mode"], r["constraints"]["max_slope_deg"])[:2])

    registry.register(Capability(
        id="route.evaluate", name="Evaluate route", category="route", provider=PROVIDER, data_mode=terrain_mode,
        description="Detailed terrain evaluation of a stored route: distance, gain/loss, slopes, steep sections (location and length), steepest point, "
                    "terrain cost, walking time and the reason it was recommended. Defaults to the active route.",
        input_schema=obj({"route_id": {"type": "string"}}), output_schema=obj({"metrics": {"type": "object"}}, ["metrics"]),
        handler=route_evaluate))

    async def route_compare(args, ctx):
        sess = ctx.session
        ids = args.get("route_ids")
        if not ids:
            latest = sess.latest_set()
            ids = latest["route_ids"] if latest and len(latest["route_ids"]) > 1 else [x for x in (sess.previous_route_id, sess.active_route_id) if x]
        routes = [_route(ctx, i) for i in dict.fromkeys(ids)]
        if len(routes) < 2:
            raise CapabilityError("not_enough_routes", "At least two routes are needed for a comparison.")
        keys = ["distance_m", "elevation_gain_m", "max_slope_deg", "avg_slope_deg", "length_above_caution_m", "weighted_cost", "walk_time_out_min"]
        rows = [{"route_id": r["route_id"], "label": r["label"], **{k: r["metrics"].get(k) for k in keys},
                 "within_constraints": r["metrics"].get("within_constraints"), "recommended": r.get("recommended", False)} for r in routes]

        def best(k):
            vals = [x for x in rows if x[k] is not None]
            return min(vals, key=lambda x: x[k])["route_id"] if vals else None
        summary = {"shortest": best("distance_m"), "fastest": best("walk_time_out_min"), "lowest_max_slope": best("max_slope_deg"),
                   "lowest_terrain_cost": best("weighted_cost"), "least_climbing": best("elevation_gain_m")}
        a, b = rows[0], rows[1]
        diffs = {k: None if a[k] is None or b[k] is None else round(b[k] - a[k], 3) for k in keys}
        sess.add_analysis("route_comparison", f"compared {', '.join(r['route_id'] for r in rows)}", summary,
                          routes[0]["provenance"]["provider"], routes[0]["provenance"]["data_mode"])
        return CapResult({"routes": rows, "best_by": summary, "difference_second_minus_first": diffs})

    registry.register(Capability(
        id="route.compare", name="Compare routes", category="route", provider=PROVIDER, data_mode=terrain_mode,
        description="Side-by-side comparison of stored routes (default: the latest candidate set, or the previous vs the active route): "
                    "which is shortest, fastest, has the lowest maximum slope and the lowest terrain cost.",
        input_schema=obj({"route_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4}}),
        output_schema=obj({"routes": {"type": "array"}, "best_by": {"type": "object"}}, ["routes", "best_by"]), handler=route_compare))

    # ------------------------------------------------------------------ terrain
    async def terrain_analyze(args, ctx):
        if terrain is None or not terrain.sources:
            raise CapabilityError("terrain_unavailable", "No terrain data is configured.")
        pt = resolve_point(args, ctx)
        res = terrain.analyze_point(pt["lat"], pt["lon"], args["radius_m"],
                                    ctx.session.preferences.get("max_slope_deg") or s.max_slope_deg)
        res["location"] = pt
        lim = check_in_mission_area(pt, ctx)
        if res["data_available"]:
            near = ctx.services.mission.nearest_rover(pt["lat"], pt["lon"], 1)[0]
            res["nearest_rover_localisation"] = near
            ds = res["dataset"]
            lim += [f"Values are sampled from {ds['name']} at {ds['resolution_m']} m; elevations are metres relative to the Mars areoid.",
                    "Slope classes are project-defined, not NASA standards."]
            if ds["data_mode"] != "real":
                lim.insert(0, "SIMULATED TERRAIN: not Mars data.")
            ctx.session.add_analysis("terrain_analysis", f"{pt['name']}: elevation {res['elevation_m']} m, mean slope {res['slope_mean_deg']}°",
                                     {"lat": pt["lat"], "lon": pt["lon"]}, PROVIDER, ds["data_mode"])
        return CapResult(res, limitations=lim)

    registry.register(Capability(
        id="terrain.analyze", name="Analyze terrain", category="terrain", provider=PROVIDER, data_mode=terrain_mode,
        availability="available" if terrain and terrain.sources else "unavailable",
        description="Terrain statistics around a point from the finest covering DEM: elevation, relief, mean / 90th-percentile / maximum slope, "
                    "share of area above the caution and limit slopes, project-defined difficulty class, nearest rover localisation.",
        input_schema=obj({**POINT_ARGS, "radius_m": {"type": "number", "minimum": 20, "maximum": 1000, "default": 150}}),
        output_schema=obj({"data_available": {"type": "boolean"}}, ["data_available"]), handler=terrain_analyze))

    # ------------------------------------------------------------------ EVA + radiation (local fallbacks)
    async def eva_local(args, ctx):
        r = _route(ctx, args.get("route_id"))
        prefs = ctx.session.preferences
        stops = args["stops"]
        stop_min = args.get("stop_minutes", prefs.get("stop_minutes", s.stop_minutes))
        budget_h = args.get("suit_budget_hours", prefs.get("suit_budget_hours", s.suit_budget_hours))
        reserve_pct = args.get("reserve_percent", s.reserve_percent)
        out_min = r["metrics"]["walk_time_out_min"]
        back_min = r["metrics"]["walk_time_back_min"] if args["round_trip"] else 0.0
        sci = stops * stop_min
        total = out_min + back_min + sci
        reserve = budget_h * 60 * reserve_pct / 100
        effective = budget_h * 60 - reserve
        remaining = effective - total
        status = "critical" if total > budget_h * 60 else "warning" if total > effective else "nominal"
        data = {"route_id": r["route_id"], "walk_out_min": round(out_min, 1), "walk_back_min": round(back_min, 1),
                "science_min": round(sci, 1), "total_min": round(total, 1), "suit_budget_min": round(budget_h * 60, 1),
                "required_reserve_min": round(reserve, 1), "effective_budget_min": round(effective, 1),
                "remaining_margin_min": round(remaining, 1), "status": status,
                "assumptions": {"stops": stops, "stop_minutes": stop_min, "suit_budget_hours": budget_h, "reserve_percent": reserve_pct,
                                "k_suit": s.k_suit, "model": "modified Tobler per route segment, clamped to 0.2-5 km/h"}}
        ctx.session.add_analysis("eva_estimation", f"{r['route_id']}: {data['total_min']} min total, status {status}",
                                 {"route_id": r["route_id"], "total_min": data["total_min"]}, PROVIDER, "calculated")
        return CapResult(data, limitations=[
            "Project-defined model (terrestrial hiking function with a suit factor); not a validated Martian EVA timeline.",
            "Suit budget and reserve are project configuration values, not NASA requirements.",
            "Local fallback calculation: the Scientific Intelligence Engine was not reachable."])

    registry.register(Capability(
        id="eva.estimate", name="Estimate EVA duration", category="science", provider=PROVIDER, data_mode="calculated", priority=5,
        description="EVA time budget for a stored route: walking out and back, science-stop time, total, suit budget, reserve and remaining margin.",
        input_schema=obj({"route_id": {"type": "string"}, "stops": {"type": "integer", "minimum": 0, "maximum": 20, "default": 1},
                          "stop_minutes": {"type": "number", "minimum": 0, "maximum": 240},
                          "suit_budget_hours": {"type": "number", "minimum": 0.5, "maximum": 12},
                          "reserve_percent": {"type": "number", "minimum": 0, "maximum": 80},
                          "round_trip": {"type": "boolean", "default": True}}),
        output_schema=obj({"total_min": {"type": "number"}}, ["total_min"]), handler=eva_local))

    async def rad_local(args, ctx):
        hours = args.get("eva_hours")
        basis = "eva_hours argument"
        if hours is None:
            eva = next((a for a in reversed(ctx.session.analyses) if a["type"] == "eva_estimation"), None)
            if eva and eva["key_values"].get("total_min") is not None:
                hours, basis = eva["key_values"]["total_min"] / 60, f"EVA estimate {eva['analysis_id']}"
        rate = s.rad_reference_mgy_day
        data = {"reference_dose_rate_mgy_per_day": rate,
                "reference_origin": "Curiosity RAD, Gale crater (published surface GCR dose rate) - used as a reference for Jezero",
                "eva_hours": None if hours is None else round(hours, 2), "duration_basis": basis if hours is not None else None,
                "estimated_absorbed_dose_mgy": None if hours is None else round(rate * hours / 24, 5),
                "formula": "dose = reference_rate × hours / 24"}
        return CapResult(data, limitations=[
            "The reference rate was MEASURED AT GALE CRATER by Curiosity; it is not a Jezero measurement.",
            "Absorbed dose from galactic cosmic rays only; not an astronaut-specific dose, and solar particle events are not included.",
            "Local fallback calculation: the Scientific Intelligence Engine was not reachable."] +
            ([] if hours is not None else ["No EVA duration is known yet: estimate the EVA first or give a duration."]))

    registry.register(Capability(
        id="radiation.estimate", name="Estimate radiation dose", category="science", provider=PROVIDER, data_mode="reference", priority=5,
        description="Estimated absorbed dose for an EVA duration from the Curiosity RAD Gale-crater reference rate (0.21 mGy/day). "
                    "Uses the latest EVA estimate when eva_hours is omitted.",
        input_schema=obj({"eva_hours": {"type": "number", "minimum": 0, "maximum": 48}}),
        output_schema=obj({"reference_dose_rate_mgy_per_day": {"type": "number"}}, ["reference_dose_rate_mgy_per_day"]), handler=rad_local))

    for cid, name, desc in (
        ("environment.analyze", "Environmental conditions (MEDA)", "Perseverance MEDA environmental observations (temperature, pressure, wind)."),
        ("space_weather.analyze", "Space weather (DONKI)", "Recent solar flares / SEP / CME events and the project-defined caution status."),
    ):
        registry.register(Capability(
            id=cid, name=name, category="science", provider=PROVIDER, description=desc, priority=1,
            input_schema=obj({}), output_schema={}, handler=None, availability="unavailable",
            availability_reason="No data source: needs the Scientific Intelligence Engine (SCIENCE_ENGINE_URL) or the Marswalk backend."))

    # ------------------------------------------------------------------ knowledge (RAG retrieval)
    async def search_docs(args, ctx):
        hits = kb.search(args["query"], args["top_k"], args.get("topic"))
        evidence = [{"citation_key": h["citation_key"], "title": h["title"], "section": h["section"], "source_url": h["source_url"],
                     "organization": h["organization"], "relevant_text": h["text"][:400], "reliability": h["reliability_category"]} for h in hits]
        return CapResult({"query": args["query"], "results": hits, "result_count": len(hits),
                          "corpus": {"directory": kb.directory, "documents": len(kb.documents())}},
                         evidence=evidence,
                         limitations=(["No document passed the relevance threshold; there is no documentary evidence for this question in the corpus."]
                                      if not hits else []) +
                         ["Documents are curated summaries in the Science Engine corpus; check the original source before citing them."])

    registry.register(Capability(
        id="science.search_documents", name="Search scientific documents", category="knowledge", provider=PROVIDER, data_mode="reference",
        availability="available" if kb and kb.available else "unavailable", availability_reason=kb.error if kb else "Not configured.",
        description="Retrieve passages from the scientific document corpus (Jezero geology, CTX/HiRISE topography, MEDA, RAD, DONKI, EVA mobility) "
                    "with citations. Use for scientific background; it never provides measurements.",
        input_schema=obj({"query": {"type": "string", "minLength": 2, "maxLength": 300},
                          "top_k": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
                          "topic": {"type": "string", "description": "Optional scientific_topic filter"}}, ["query"]),
        output_schema=obj({"results": {"type": "array"}}, ["results"]), handler=search_docs))
