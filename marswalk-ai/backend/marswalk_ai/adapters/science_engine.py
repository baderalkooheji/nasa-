"""Adapter for the Marswalk Scientific Intelligence Engine (separate FastAPI service).

Registered only when SCIENCE_ENGINE_URL is set and GET /api/health answers.

Used endpoints (verified in the engine source, app/api/intelligence.py):
    POST /api/intelligence/eva-estimation
    POST /api/intelligence/radiation-estimation
    POST /api/intelligence/environmental-analysis
    POST /api/intelligence/space-weather-analysis

Deliberately NOT used:
    /terrain-analysis   - v1 builds a synthetic linear elevation ramp instead of reading a DEM
    /route-evaluation   - v1 assigns slopes with a fixed pattern (4 + (i % 3) * 5.5)
    /mission-analysis   - v1 uses fixed segment slopes [5, 8]
The assistant's own DEM-based terrain service is used for those instead.
"""
from __future__ import annotations

import httpx

from ..registry import Capability, CapabilityError, CapabilityRegistry, CapResult
from ..schema import obj

PROVIDER = "science-engine"


async def probe(services) -> dict:
    url = services.settings.science_engine_url
    if not url:
        return {"configured": False, "reachable": False}
    try:
        r = await services.http.get(f"{url}/api/health", timeout=4.0)
        r.raise_for_status()
        return {"configured": True, "reachable": True, "url": url, "health": r.json()}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "reachable": False, "url": url, "error": f"{type(e).__name__}: {e}"}


def _evidence(resp: dict) -> list[dict]:
    return [{"citation_key": e.get("citation_key"), "title": e.get("title"), "source_url": e.get("source_url"),
             "organization": e.get("organization"), "relevant_text": e.get("relevant_text", "")[:400]} for e in resp.get("evidence", [])]


async def _post(services, path: str, body: dict) -> dict:
    url = f"{services.settings.science_engine_url}/api/intelligence/{path}"
    try:
        r = await services.http.post(url, json=body, timeout=30.0)
    except httpx.HTTPError as e:
        raise CapabilityError("upstream_unreachable", f"Science Engine not reachable ({type(e).__name__}).")
    if r.status_code >= 400:
        detail = r.text[:300]
        try:
            detail = r.json().get("detail", detail)
        except Exception:  # noqa: BLE001
            pass
        raise CapabilityError("upstream_error", f"Science Engine returned {r.status_code}: {detail}")
    return r.json()


def _result(resp: dict, extra_limits: list[str] | None = None, data: dict | None = None) -> CapResult:
    body = data or {k: resp.get(k) for k in ("analysis_id", "analysis_type", "measurements", "calculations", "classification", "interpretation")}
    body["engine_status"] = resp.get("status")
    body["data_provenance"] = resp.get("data_provenance")
    return CapResult(body, provenance={"engine_analysis_id": resp.get("analysis_id")}, evidence=_evidence(resp),
                     limitations=(resp.get("limitations") or []) + (resp.get("uncertainty") or []) + (extra_limits or []))


def register(registry: CapabilityRegistry, services) -> None:
    s = services.settings

    async def eva(args, ctx):
        rid = args.get("route_id") or ctx.session.active_route_id
        r = ctx.session.routes.get(rid or "")
        if not r:
            raise CapabilityError("unknown_route", "EVA estimation needs a stored route; generate one first.")
        m = r["metrics"]
        prefs = ctx.session.preferences
        body = {"route_distance_km": round(m["distance_m"] / 1000, 4), "cumulative_elevation_gain_m": m["elevation_gain_m"],
                "cumulative_elevation_loss_m": m["elevation_loss_m"], "average_slope_deg": m["avg_slope_deg"],
                "stop_count": args["stops"], "stop_duration_minutes": args.get("stop_minutes", prefs.get("stop_minutes", s.stop_minutes)),
                "suit_time_budget_hours": args.get("suit_budget_hours", prefs.get("suit_budget_hours", s.suit_budget_hours)),
                "reserve_margin_percent": args.get("reserve_percent", s.reserve_percent), "suit_factor_k": s.k_suit}
        resp = await _post(services, "eva-estimation", body)
        c = resp.get("calculations", {})
        total_min = round(c.get("total_eva_duration_hours", 0) * 60, 1) if c.get("total_eva_duration_hours") is not None else None
        ctx.session.add_analysis("eva_estimation", f"{rid}: {total_min} min total (Science Engine)",
                                 {"route_id": rid, "total_min": total_min}, PROVIDER, "calculated")
        res = _result(resp, ["The engine models the route with its AVERAGE slope (out and back), so steep sections are smoothed out."])
        res.data.update({"route_id": rid, "total_min": total_min, "inputs_sent": body})
        return res

    registry.register(Capability(
        id="eva.estimate", name="Estimate EVA duration", category="science", provider=PROVIDER, data_mode="calculated", priority=20,
        description="EVA time budget for a stored route from the Scientific Intelligence Engine (modified Tobler model, suit budget, reserve).",
        input_schema=obj({"route_id": {"type": "string"}, "stops": {"type": "integer", "minimum": 0, "maximum": 20, "default": 1},
                          "stop_minutes": {"type": "number", "minimum": 0, "maximum": 240},
                          "suit_budget_hours": {"type": "number", "minimum": 0.5, "maximum": 12},
                          "reserve_percent": {"type": "number", "minimum": 0, "maximum": 80}}),
        output_schema=obj({"calculations": {"type": "object"}}, ["calculations"]), handler=eva, timeout_s=40))

    async def rad(args, ctx):
        hours = args.get("eva_hours")
        if hours is None:
            ev = next((a for a in reversed(ctx.session.analyses) if a["type"] == "eva_estimation"), None)
            if not ev or ev["key_values"].get("total_min") is None:
                raise CapabilityError("missing_duration", "No EVA duration is known; estimate the EVA first or give a duration in hours.")
            hours = ev["key_values"]["total_min"] / 60
        resp = await _post(services, "radiation-estimation", {"eva_duration_hours": round(hours, 3),
                                                              "reference_dose_mgy_day": s.rad_reference_mgy_day})
        return _result(resp, ["Reference rate measured at Gale crater by Curiosity RAD; it is not a Jezero measurement."])

    registry.register(Capability(
        id="radiation.estimate", name="Estimate radiation dose", category="science", provider=PROVIDER, data_mode="reference", priority=20,
        description="Absorbed (and engine-estimated equivalent) dose for an EVA duration from the Curiosity RAD Gale-crater reference rate. "
                    "Uses the latest EVA estimate when eva_hours is omitted.",
        input_schema=obj({"eva_hours": {"type": "number", "minimum": 0, "maximum": 48}}),
        output_schema=obj({"calculations": {"type": "object"}}, ["calculations"]), handler=rad, timeout_s=40))

    async def env(args, ctx):
        body = {"sol": args["sol"]} if args.get("sol") is not None else {}
        resp = await _post(services, "environmental-analysis", body)
        return _result(resp, ["Values come from the engine's local MEDA cache file (meda_sample_cache.json); whether they are calibrated NASA "
                              "MEDA records is not documented in the engine. Treat them as sample data until verified.",
                              "MEDA measures conditions at Perseverance's position and time only; it is not crater-wide or live weather."])

    registry.register(Capability(
        id="environment.analyze", name="Environmental conditions (MEDA)", category="science", provider=PROVIDER, data_mode="cached-sample",
        priority=20, description="Perseverance MEDA observations for a sol from the Science Engine (temperature, pressure, wind, flags). "
                                 "Historical in-situ values, not a forecast.",
        input_schema=obj({"sol": {"type": "integer", "minimum": 0, "maximum": 10000}}),
        output_schema=obj({"classification": {"type": "object"}}, ["classification"]), handler=env, timeout_s=40))

    async def sw(args, ctx):
        resp = await _post(services, "space-weather-analysis", {"lookback_days": args["lookback_days"]})
        src = (resp.get("measurements") or {}).get("data_source")
        extra = ["Caution status is a project-defined rule, not an official NASA protocol."]
        if src and "cache" in str(src):
            extra.insert(0, f"Engine data source: {src} (donki_sample_cache.json), not a live DONKI query.")
        return _result(resp, extra)

    registry.register(Capability(
        id="space_weather.analyze", name="Space weather (DONKI)", category="science", provider=PROVIDER, data_mode="cached-sample", priority=20,
        description="Recent solar flares, SEP and CME events and the project-defined caution status ('Caution: shelter-ready' / 'Nominal') "
                    "from the Science Engine.",
        input_schema=obj({"lookback_days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7}}),
        output_schema=obj({"classification": {"type": "object"}}, ["classification"]), handler=sw, timeout_s=40))
