"""Frontend capabilities: actions the connected UI executes (map + navigation).

The backend never touches the map. A handler validates the request, resolves
route ids / places to concrete geometry from stored tool results, and emits a
typed frontend action with status "pending". The UI validates it again,
executes it and reports the outcome to POST /api/assistant/actions/result.
The assistant only treats an action as done after that acknowledgement.

The set of sections and layers (and therefore the schema enums) comes from what
the UI advertised in its session handshake, so the same assistant works with the
standalone demo map and with the real Marswalk page.
"""
from __future__ import annotations

from ..registry import Capability, CapabilityError, CapabilityRegistry, CapResult
from ..schema import LAT, LON, obj
from .common import POINT_TARGETS, resolve_point

PROVIDER = "frontend"
ACTION_TYPES = {
    "map.display_route": "display_route",
    "map.highlight_location": "highlight_location",
    "map.zoom_to_location": "zoom_to_location",
    "map.change_layer": "change_layer",
    "map.clear_route": "clear_route",
    "navigation.open_section": "navigate_to_section",
}

TARGET_ARGS = {
    "target": {"type": "string", "enum": POINT_TARGETS + ["route"],
               "description": "current_position | poi (poi_id) | point (lat/lon from a tool result) | selected | map_center | "
                              "active_route_end | route_steepest | route (whole route extent, zoom only)"},
    "poi_id": {"type": "string"}, "route_id": {"type": "string"},
    "lat": LAT, "lon": LON, "label": {"type": "string", "maxLength": 120},
}


def _pending(action: dict, extra: dict | None = None) -> CapResult:
    return CapResult({"action_id": action["action_id"], "type": action["type"], "status": "pending_ack",
                      "note": "Sent to the frontend. It is NOT confirmed until the frontend acknowledges it; do not say it is done.",
                      **(extra or {})},
                     status="pending_ack")


def _ids(session, key) -> list[str]:
    return [x["id"] for x in (session.frontend.get(key) or [])] if session else []


def _layer_schema(session) -> dict:
    layers = session.frontend.get("layers") or []
    desc = "; ".join(f"{l['id']} = {l.get('label', l['id'])}" + (f" ({', '.join(l.get('keywords', []))})" if l.get("keywords") else "")
                     for l in layers)
    return obj({"layer_id": {"type": "string", "enum": [l["id"] for l in layers] or ["none"], "description": desc},
                "visible": {"type": "boolean", "default": True},
                "opacity": {"type": "number", "minimum": 0, "maximum": 1}}, ["layer_id"])


def _section_schema(session) -> dict:
    secs = session.frontend.get("sections") or []
    desc = "; ".join(f"{x['id']} = {x.get('label', x['id'])}" + (f" ({', '.join(x.get('keywords', []))})" if x.get("keywords") else "")
                     for x in secs)
    return obj({"section_id": {"type": "string", "enum": [x["id"] for x in secs] or ["none"], "description": desc}}, ["section_id"])


def register(registry: CapabilityRegistry, services) -> None:
    async def display_route(args, ctx):
        rid = args.get("route_id") or ctx.session.active_route_id
        r = ctx.session.routes.get(rid or "")
        if not r:
            raise CapabilityError("unknown_route", f"No route '{rid}' to display.")
        payload = {"route_id": r["route_id"], "label": r["label"], "geometry": r["geometry"], "slope_classes": r.get("slope_classes", []),
                   "emphasis": args["emphasis"], "fit": args["fit"], "recommended": r.get("recommended", False),
                   "data_mode": r["provenance"].get("data_mode"),
                   "summary": {k: r["metrics"].get(k) for k in ("distance_m", "max_slope_deg", "walk_time_out_min")}}
        ctx.session.set_active_route(r["route_id"])
        from .common import route_brief
        return _pending(ctx.turn.add_action("display_route", "map.display_route", payload, f"Display {r['route_id']} ({r['label']})"),
                        {"route": route_brief(r)})

    registry.register(Capability(
        id="map.display_route", name="Display route on map", side="frontend", category="map", provider=PROVIDER, changes_state=True,
        permission="write", description="Draw a stored route (by route_id) on the map, coloured by slope class. Geometry comes from the stored route.",
        input_schema=obj({"route_id": {"type": "string", "description": "Stored route id (default: active route)"},
                          "emphasis": {"type": "string", "enum": ["primary", "alternative"], "default": "primary"},
                          "fit": {"type": "boolean", "default": True, "description": "Zoom the map to the route"}}),
        output_schema=obj({"action_id": {"type": "string"}}, ["action_id"]), handler=display_route))

    async def highlight(args, ctx):
        pt = resolve_point(args, ctx)
        payload = {"lat": pt["lat"], "lon": pt["lon"], "label": args.get("label") or pt["name"], "kind": pt.get("kind"),
                   "style": args["style"]}
        return _pending(ctx.turn.add_action("highlight_location", "map.highlight_location", payload, f"Highlight {payload['label']}"))

    registry.register(Capability(
        id="map.highlight_location", name="Highlight location", side="frontend", category="map", provider=PROVIDER, changes_state=True,
        permission="write", description="Place a highlight marker on a place, the steepest point of a route, the selection or a point from a tool result.",
        input_schema=obj({**{k: v for k, v in TARGET_ARGS.items()},
                          "style": {"type": "string", "enum": ["info", "caution", "destination", "rover"], "default": "info"}}),
        output_schema=obj({"action_id": {"type": "string"}}, ["action_id"]), handler=highlight))

    async def zoom(args, ctx):
        if args.get("target") == "route":
            rid = args.get("route_id") or ctx.session.active_route_id
            r = ctx.session.routes.get(rid or "")
            if not r:
                raise CapabilityError("unknown_route", f"No route '{rid}' to zoom to.")
            lats, lons = [p[0] for p in r["geometry"]], [p[1] for p in r["geometry"]]
            payload = {"bounds": [[min(lats), min(lons)], [max(lats), max(lons)]], "label": r["label"]}
        else:
            pt = resolve_point(args, ctx)
            payload = {"lat": pt["lat"], "lon": pt["lon"], "zoom": args.get("zoom", 16), "label": pt["name"]}
        return _pending(ctx.turn.add_action("zoom_to_location", "map.zoom_to_location", payload, f"Zoom to {payload['label']}"))

    registry.register(Capability(
        id="map.zoom_to_location", name="Zoom map", side="frontend", category="map", provider=PROVIDER, changes_state=True, permission="write",
        description="Move/zoom the map to a place, a point from a tool result, the steepest point of a route, or a whole route (target='route').",
        input_schema=obj({**TARGET_ARGS, "zoom": {"type": "number", "minimum": 3, "maximum": 19}}),
        output_schema=obj({"action_id": {"type": "string"}}, ["action_id"]), handler=zoom))

    async def change_layer(args, ctx):
        layers = {l["id"]: l for l in ctx.session.frontend.get("layers") or []}
        if args["layer_id"] not in layers:
            raise CapabilityError("unknown_layer", f"Layer '{args['layer_id']}' is not offered by this frontend.")
        payload = {k: args[k] for k in ("layer_id", "visible", "opacity") if k in args}
        label = layers[args["layer_id"]].get("label", args["layer_id"])
        return _pending(ctx.turn.add_action("change_layer", "map.change_layer", payload,
                                            f"{'Show' if args['visible'] else 'Hide'} layer {label}"))

    registry.register(Capability(
        id="map.change_layer", name="Change map layer", side="frontend", category="map", provider=PROVIDER, changes_state=True, permission="write",
        description="Show or hide a map layer offered by the connected frontend (ids and meanings are listed in the schema).",
        input_schema=obj({"layer_id": {"type": "string"}, "visible": {"type": "boolean", "default": True},
                          "opacity": {"type": "number", "minimum": 0, "maximum": 1}}, ["layer_id"]),
        schema_builder=_layer_schema, output_schema=obj({"action_id": {"type": "string"}}, ["action_id"]), handler=change_layer))

    async def clear_route(args, ctx):
        rid = args.get("route_id")
        if rid and rid not in ctx.session.routes:
            raise CapabilityError("unknown_route", f"No route '{rid}'.")
        return _pending(ctx.turn.add_action("clear_route", "map.clear_route", {"route_id": rid},
                                            f"Clear {'route ' + rid if rid else 'all assistant routes'} from the map"))

    registry.register(Capability(
        id="map.clear_route", name="Clear route overlay", side="frontend", category="map", provider=PROVIDER, changes_state=True, permission="write",
        description="Remove one displayed route (route_id) or all assistant route overlays (no route_id) from the map. Stored routes are kept.",
        input_schema=obj({"route_id": {"type": "string"}}), output_schema=obj({"action_id": {"type": "string"}}, ["action_id"]),
        handler=clear_route))

    async def open_section(args, ctx):
        secs = {x["id"]: x for x in ctx.session.frontend.get("sections") or []}
        if args["section_id"] not in secs:
            raise CapabilityError("unknown_section", f"Section '{args['section_id']}' is not offered by this frontend.")
        label = secs[args["section_id"]].get("label", args["section_id"])
        return _pending(ctx.turn.add_action("navigate_to_section", "navigation.open_section", {"section_id": args["section_id"]},
                                            f"Open {label}"))

    registry.register(Capability(
        id="navigation.open_section", name="Open application section", side="frontend", category="navigation", provider=PROVIDER,
        changes_state=True, permission="write",
        description="Open a section/panel of the application (ids and meanings are listed in the schema).",
        input_schema=obj({"section_id": {"type": "string"}}, ["section_id"]), schema_builder=_section_schema,
        output_schema=obj({"action_id": {"type": "string"}}, ["action_id"]), handler=open_section))
