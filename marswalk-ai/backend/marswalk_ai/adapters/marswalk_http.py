"""Adapter for the ORIGINAL Marswalk backend (/api/*).

The contracts below are the client-side contracts documented in
MARSWALK_TECHNICAL_REFERENCE.md §3. That backend was NOT FOUND in the inspected
repository, so every mapping here is UNVERIFIED until it runs against the real
server. Capabilities are registered only when:
  * MARSWALK_API_URL is set, and
  * the endpoint is listed in MARSWALK_ENABLED_ENDPOINTS (POST endpoints) or a
    GET probe of it succeeds (GET endpoints).

Registered with priority 30, so they replace the standalone / engine versions.

    key            method  path                 capability
    stops          GET     /api/stops           (refreshes mission places)
    elevation      GET     /api/elevation       terrain.analyze (point only)
    route_auto     POST    /api/route/auto      route.generate (single route)
    route_analyze  POST    /api/route/analyze   route.evaluate
    eva            POST    /api/eva             eva.estimate
    spaceweather   GET     /api/spaceweather    space_weather.analyze
    meda           GET     /api/meda            environment.analyze
    records        GET/POST /api/records        mission.get_history / mission.save_record
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from ..registry import Capability, CapabilityError, CapabilityRegistry, CapResult
from ..schema import obj
from .common import resolve_place, resolve_point, route_brief, route_record
from .standalone import POINT_ARGS

PROVIDER = "marswalk-backend"
GET_PROBES = {"stops": "/api/stops", "spaceweather": "/api/spaceweather", "meda": "/api/meda", "records": "/api/records?q="}
UNVERIFIED = "Mapped from the documented client contract (MARSWALK_TECHNICAL_REFERENCE.md §3); not yet verified against the real server."


class MarswalkClient:
    def __init__(self, services):
        self.base = services.settings.marswalk_api_url
        self.http = services.http

    async def call(self, method: str, path: str, body: dict | None = None, timeout: float = 60.0):
        try:
            r = await self.http.request(method, f"{self.base}{path}", json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise CapabilityError("upstream_unreachable", f"Marswalk backend not reachable ({type(e).__name__}).")
        if r.status_code >= 400:
            msg = r.reason_phrase
            try:  # same error parsing as js/map.js api()
                j = r.json()
                d = j.get("detail")
                msg = (d.get("error", {}).get("message") if isinstance(d, dict) else d) or msg
            except Exception:  # noqa: BLE001
                pass
            raise CapabilityError("upstream_error", f"Marswalk backend {method} {path} returned {r.status_code}: {msg}")
        return r.json()


async def probe(services) -> dict:
    s = services.settings
    if not s.marswalk_api_url:
        return {"configured": False, "reachable": False, "endpoints": []}
    client = MarswalkClient(services)
    ok = [e for e in s.marswalk_enabled_endpoints]
    for key, path in GET_PROBES.items():
        if key in ok:
            continue
        try:
            await client.call("GET", path, timeout=4.0)
            ok.append(key)
        except CapabilityError:
            pass
    return {"configured": True, "reachable": bool(ok), "url": s.marswalk_api_url, "endpoints": sorted(set(ok))}


def _normalize_route(analysis: dict) -> dict:
    """Marswalk RouteAnalysis → the assistant's route fields (see technical reference §3.4)."""
    prof = analysis.get("profile") or []
    geom = [[p["lat"], p["lon"]] for p in prof if "lat" in p and "lon" in p] or [[w["lat"], w["lon"]] for w in analysis.get("waypoints", [])]
    slopes = [p.get("slope_deg") for p in prof]
    return {
        "label": "Marswalk auto-route", "objective": "marswalk_auto", "geometry": geom,
        "slope_classes": [], "profile": [{"d_m": p.get("dist_m"), "elev_m": p.get("elevation_m"), "slope_deg": p.get("slope_deg")} for p in prof],
        "metrics": {"distance_m": analysis.get("distance_m"), "elevation_gain_m": analysis.get("elevation_gain_m"),
                    "elevation_loss_m": analysis.get("elevation_loss_m"), "max_slope_deg": analysis.get("max_slope_deg"),
                    "avg_slope_deg": analysis.get("avg_slope_deg"), "data_coverage_pct": analysis.get("data_coverage_pct"),
                    "within_constraints": None if analysis.get("over_limit") is None else not analysis.get("over_limit"),
                    "weighted_cost": None, "walk_time_out_min": None, "walk_time_back_min": None, "length_above_caution_m": None},
        "steepest_point": _steepest(prof, slopes), "steep_sections": [],
        "_waypoints": analysis.get("waypoints") or [{"lat": g[0], "lon": g[1]} for g in geom],
        "_method": analysis.get("method"), "_source_dataset": analysis.get("source_dataset"),
    }


def _steepest(prof, slopes):
    vals = [(s, i) for i, s in enumerate(slopes) if s is not None]
    if not vals:
        return None
    s, i = max(vals)
    return {"lat": prof[i]["lat"], "lon": prof[i]["lon"], "slope_deg": s, "at_m": prof[i].get("dist_m")}


def register(registry: CapabilityRegistry, services, endpoints: list[str]) -> None:
    client = MarswalkClient(services)
    s = services.settings

    if "elevation" in endpoints:
        async def elevation(args, ctx):
            pt = resolve_point(args, ctx)
            e = await client.call("GET", f"/api/elevation?lat={pt['lat']}&lon={pt['lon']}")
            data = {"data_available": bool(e.get("data_available")), "location": pt, "elevation_m": e.get("elevation_m"),
                    "slope_at_point_deg": e.get("slope_deg"), "dataset": {"name": e.get("source_dataset"), "data_mode": "real"},
                    "no_data_reason": e.get("no_data_reason")}
            return CapResult(data, limitations=["Point value from the Marswalk backend; no neighbourhood statistics.", UNVERIFIED])
        registry.register(Capability(
            id="terrain.analyze", name="Analyze terrain", category="terrain", provider=PROVIDER, data_mode="real", priority=30,
            description="Elevation and slope at a point from the Marswalk backend DEM.", input_schema=obj(dict(POINT_ARGS)),
            output_schema=obj({"data_available": {"type": "boolean"}}, ["data_available"]), handler=elevation, notes=UNVERIFIED))

    if "route_auto" in endpoints:
        async def auto(args, ctx):
            sess = ctx.session
            start, dest = resolve_place(args["start"], ctx), resolve_place(args["destination"], ctx)
            max_slope = args.get("max_slope_deg") or sess.preferences.get("max_slope_deg") or s.max_slope_deg
            out = await client.call("POST", "/api/route/auto", {"stops": [start, dest], "max_slope": max_slope}, timeout=120)
            n = _normalize_route(out)
            rec = route_record(sess, n, start, dest, {"name": n["_source_dataset"]}, max_slope, s.caution_slope_deg, PROVIDER, "real",
                               n["_method"] or "Marswalk /api/route/auto")
            rec["waypoints"] = n["_waypoints"]
            rec["recommended"] = True
            rs = sess.add_route_set([rec], rec["route_id"], {"start": start, "destination": dest, "max_slope_deg": max_slope})
            sess.destination = dest
            return CapResult({"route_set_id": rs["set_id"], "routes": [route_brief(rec)], "recommended_route_id": rec["route_id"],
                              "recommendation_rationale": ["Single route returned by the Marswalk route engine."],
                              "start": start, "destination": dest, "max_slope_deg": max_slope},
                             limitations=["The Marswalk auto-route returns one route; alternatives are not available from it.", UNVERIFIED])
        registry.register(Capability(
            id="route.generate", name="Generate route", category="route", provider=PROVIDER, data_mode="real", priority=30,
            description="Hazard-avoiding route from the Marswalk route engine (POST /api/route/auto).",
            input_schema=obj({"destination": {"type": "string", "minLength": 1}, "start": {"type": "string", "default": "current_position"},
                              "mode": {"type": "string", "enum": ["candidates", "balanced", "min_slope", "shortest"], "default": "candidates"},
                              "max_slope_deg": {"type": "number", "minimum": 5, "maximum": 25},
                              "avoid_route_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                                                  "description": "Not supported by /api/route/auto; ignored"}}, ["destination"]),
            output_schema=obj({"routes": {"type": "array"}}, ["routes"]), handler=auto, timeout_s=150, notes=UNVERIFIED))

    if "route_analyze" in endpoints:
        async def analyze(args, ctx):
            rid = args.get("route_id") or ctx.session.active_route_id
            r = ctx.session.routes.get(rid or "")
            if not r:
                raise CapabilityError("unknown_route", f"No route '{rid}'.")
            wps = r.get("waypoints") or [{"lat": g[0], "lon": g[1]} for g in r["geometry"]]
            a = await client.call("POST", "/api/route/analyze", {"waypoints": wps, "route_id": rid})
            n = _normalize_route(a)
            return CapResult({"route": route_brief(r), "metrics": n["metrics"], "steepest_point": n["steepest_point"],
                              "method": n["_method"], "dataset": n["_source_dataset"]}, limitations=[UNVERIFIED])
        registry.register(Capability(
            id="route.evaluate", name="Evaluate route", category="route", provider=PROVIDER, data_mode="real", priority=30,
            description="Terrain analysis of a stored route by the Marswalk backend (POST /api/route/analyze).",
            input_schema=obj({"route_id": {"type": "string"}}), output_schema=obj({"metrics": {"type": "object"}}, ["metrics"]),
            handler=analyze, notes=UNVERIFIED))

    if "eva" in endpoints:
        async def eva(args, ctx):
            rid = args.get("route_id") or ctx.session.active_route_id
            r = ctx.session.routes.get(rid or "")
            if not r:
                raise CapabilityError("unknown_route", "EVA estimation needs a stored route.")
            wps = r.get("waypoints") or [{"lat": g[0], "lon": g[1]} for g in r["geometry"]]
            e = await client.call("POST", "/api/eva", {"out_waypoints": wps, "back_waypoints": list(reversed(wps)), "n_stops": args["stops"]})
            total = sum(e.get(k) or 0 for k in ("walk_out_min", "science_min", "walk_back_min"))
            ctx.session.add_analysis("eva_estimation", f"{rid}: {total} min (Marswalk)", {"route_id": rid, "total_min": total}, PROVIDER, "calculated")
            return CapResult({**e, "route_id": rid, "total_min": total},
                             limitations=["Return leg approximated by reversing the outbound route.", UNVERIFIED])
        registry.register(Capability(
            id="eva.estimate", name="Estimate EVA duration", category="science", provider=PROVIDER, data_mode="calculated", priority=30,
            description="EVA time budget and dose estimate from the Marswalk backend (POST /api/eva).",
            input_schema=obj({"route_id": {"type": "string"}, "stops": {"type": "integer", "minimum": 0, "maximum": 20, "default": 1}}),
            output_schema=obj({}), handler=eva, notes=UNVERIFIED))

    if "spaceweather" in endpoints:
        async def sw(args, ctx):
            w = await client.call("GET", "/api/spaceweather")
            return CapResult({"status": w.get("status"), "flare_count": len(w.get("flares") or []), "sep_count": len(w.get("sep_events") or []),
                              "rule": w.get("rule"), "cached": w.get("cached"), "source": w.get("source", "NASA DONKI")},
                             limitations=["Caution status is a project-defined rule.", UNVERIFIED])
        registry.register(Capability(
            id="space_weather.analyze", name="Space weather (DONKI)", category="science", provider=PROVIDER, data_mode="real", priority=30,
            description="NASA DONKI 7-day space-weather status from the Marswalk backend.", input_schema=obj({}), output_schema=obj({}),
            handler=sw, notes=UNVERIFIED))

    if "meda" in endpoints:
        async def meda(args, ctx):
            m = await client.call("GET", "/api/meda")
            return CapResult({"rows": (m.get("rows") or [])[:24], "source": m.get("source"), "sol_range": m.get("sol_range")},
                             limitations=["Historical measurements at the rover's location and times, not live crater-wide weather.", UNVERIFIED])
        registry.register(Capability(
            id="environment.analyze", name="Environmental conditions (MEDA)", category="science", provider=PROVIDER, data_mode="real",
            priority=30, description="Perseverance MEDA rows (sol, LMST, air temperature, pressure, wind) from the Marswalk backend.",
            input_schema=obj({"sol": {"type": "integer", "minimum": 0}}), output_schema=obj({}), handler=meda, notes=UNVERIFIED))

    if "records" in endpoints:
        async def history(args, ctx):
            rows = await client.call("GET", f"/api/records?q={quote(args.get('query', ''))}")
            return CapResult({"analyses": ctx.session.analyses[-args["limit"]:], "saved_records": rows[: args["limit"]]},
                             limitations=[UNVERIFIED])
        registry.register(Capability(
            id="mission.get_history", name="Get mission history", category="mission", provider=PROVIDER, priority=30,
            description="Mission-log records from the Marswalk backend plus this session's analyses.",
            input_schema=obj({"query": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}),
            output_schema=obj({}), handler=history, notes=UNVERIFIED))

        async def save(args, ctx):
            sel = ctx.session.client_context.get("selected") or {}
            body = {"label": args["label"], "lat": sel.get("lat"), "lon": sel.get("lon"),
                    "analysis_type": "route" if args.get("route_id") else "location", "result": args.get("note", ""),
                    "elevation_m": sel.get("elevation_m"), "slope_deg": sel.get("slope_deg"), "dataset": sel.get("source_dataset"),
                    "status": "complete"}
            await client.call("POST", "/api/records", body)
            return CapResult({"saved": True, "record": body}, limitations=[UNVERIFIED])
        registry.register(Capability(
            id="mission.save_record", name="Save to mission log", category="mission", provider=PROVIDER, priority=30,
            description="Save a record to the Marswalk mission log (POST /api/records). Requires confirmation.",
            input_schema=obj({"label": {"type": "string", "minLength": 1, "maxLength": 120}, "note": {"type": "string", "maxLength": 1000},
                              "route_id": {"type": "string"}, "analysis_id": {"type": "string"}}, ["label"]),
            output_schema=obj({"saved": {"type": "boolean"}}, ["saved"]), handler=save, changes_state=True, permission="confirm", notes=UNVERIFIED))


async def refresh_stops(services) -> bool:
    """Replace mock places with the real /api/stops response (same schema as data/stops.json)."""
    try:
        st = await MarswalkClient(services).call("GET", "/api/stops", timeout=6.0)
    except CapabilityError:
        return False
    m = services.mission
    keep = [p for p in m.pois if p["kind"] not in ("habitat", "science_stop")]
    new = []
    for src, kind in [(st["habitat"], "habitat")] + [(x, "science_stop") for x in st.get("stops", [])]:
        pid = "habitat" if kind == "habitat" else f"stop-{src['id']}"
        old = m.get(pid) or {}
        new.append({"id": pid, "name": src["name"], "kind": kind, "lat": src["lat"], "lon": src["lon"],
                    "aliases": old.get("aliases", []), "reason": src.get("reason"), "source": src.get("source"),
                    "provenance": "Marswalk backend /api/stops"})
    m.mission["pois"] = new + keep
    m.mission["planned_stops"] = [p["id"] for p in new if p["kind"] == "science_stop"]
    m.source = "marswalk"
    return True
