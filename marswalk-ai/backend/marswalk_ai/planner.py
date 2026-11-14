"""Rule-based intent recognition and planning.

Used (a) when no LLM is configured, and (b) as the fallback when the LLM call fails.
It recognises the request, resolves references from mission memory ("it", "the previous
route", "the second option"), runs the same registered capabilities as the LLM path and
writes the reply from templates. Every number in a reply is read from a tool result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ORDINALS = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2, "three": 2}
DISPLAY = re.compile(r"\b(show|display|draw|visuali[sz]e|put|plot|highlight)\b|on the map")


@dataclass
class Intent:
    name: str
    confidence: float = 0.7
    slots: dict = field(default_factory=dict)


def _has(t: str, pattern: str) -> bool:
    return re.search(pattern, t) is not None


def _fmt_min(m) -> str:
    if m is None:
        return "unknown"
    m = float(m)
    return f"{m:.0f} min" if m < 90 else f"{int(m // 60)} h {m % 60:02.0f} min"


def _fmt_m(m) -> str:
    if m is None:
        return "unknown"
    return f"{m / 1000:.2f} km" if m >= 1000 else f"{m:.0f} m"


class RulePlanner:
    def __init__(self, services):
        self.services = services

    # ================================================================== parsing
    def _match_ui(self, text: str, items: list[dict], use_ids: bool = True) -> tuple[str | None, int]:
        best, score = None, 0
        for it in items or []:
            names = [it.get("label", "").lower(), *[k.lower() for k in it.get("keywords", [])]]
            if use_ids:
                names.append(it["id"].replace("_", " "))
            for kw in names:
                if kw and re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", text) and len(kw) > score:
                    best, score = it["id"], len(kw)
        return best, score

    def _slots(self, text: str, session) -> dict:
        m = self.services.mission
        slots: dict = {"display": bool(DISPLAY.search(text))}
        fr = re.search(r"\bfrom (?:the |my )?(.+?) to (?:the )?(.+?)(?:[?.!,]| with| avoiding| and |$)", text)
        if fr:
            s, d = m.resolve(fr.group(1)), m.resolve(fr.group(2))
            if s:
                slots["start"] = s[0]["id"]
            if d:
                slots["destination"] = d[0]["id"]
        if "destination" not in slots:
            to = re.search(r"\b(?:to|towards|toward|reach|visit) (?:the )?(.+?)(?:[?.!,]| with| avoiding| instead| and |$)", text)
            hits = m.resolve(to.group(1)) if to else []
            hits = hits or m.resolve(text)
            hits = [h for h in hits if h["id"] != slots.get("start")]
            if hits:
                slots["destination"] = hits[0]["id"]
        sl = re.search(r"(?:slope|slopes|steeper than|grade|incline)\D{0,20}?(\d{1,2}(?:\.\d+)?)\s*(?:°|deg|degree)", text) or \
            re.search(r"(\d{1,2}(?:\.\d+)?)\s*(?:°|deg|degrees?)\b", text)
        if sl:
            slots["max_slope_deg"] = float(sl.group(1))
        rid = re.findall(r"\br(\d+)\b", text)
        if rid:
            slots["route_ids"] = [f"R{x}" for x in rid]
        o = re.search(r"\b(first|second|third|1st|2nd|3rd)\b(?: (?:option|route|one|candidate|alternative))?|\b(?:option|route|candidate) (one|two|three|\d)\b", text)
        if o:
            w = o.group(1) or o.group(2)
            slots["ordinal"] = ORDINALS.get(w, int(w) - 1 if w and w.isdigit() else 0)
        rad = re.search(r"within (\d+(?:\.\d+)?)\s*(km|m)\b", text)
        if rad:
            slots["radius_m"] = float(rad.group(1)) * (1000 if rad.group(2) == "km" else 1)
        hrs = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)\b", text)
        if hrs:
            slots["hours"] = float(hrs.group(1))
        slots["previous"] = bool(re.search(r"\b(previous|last|earlier|old|original|former)\b", text))
        layer, ls = self._match_ui(text, session.frontend.get("layers"))
        # section ids are often generic words ("route", "map"), so sections match on labels/keywords only
        section, ss = self._match_ui(text, session.frontend.get("sections"), use_ids=False)
        slots.update(layer=layer, layer_score=ls, section=section, section_score=ss)
        return slots

    def parse(self, text: str, session) -> Intent:
        t = " ".join(text.lower().strip().split())
        s = self._slots(t, session)
        route_word = _has(t, r"\b(route|routes|path|paths|way|traverse|option|options|alternative|alternatives|it|one)\b")

        def I(name, conf=0.8):
            return Intent(name, conf, s)

        if not t:
            return I("empty", 1.0)
        if _has(t, r"^(hi|hello|hey|good (morning|evening|afternoon))\b|^help\b|what can you do|how do i use"):
            return I("help", 0.9)
        if _has(t, r"\b(clear|remove|erase|delete|hide)\b.*\b(route|routes|path|overlay|overlays)\b"):
            return I("clear_routes", 0.9)
        if _has(t, r"steepest|most challenging|hardest (part|section|segment|bit)|most difficult (part|section|segment)|steep (section|part|segment)s?\b"):
            return I("route_steepest", 0.9)
        if _has(t, r"\b(why|explain|reason|justify)\b") and _has(t, r"recommend|chose|choose|choice|pick|this route|that route|selected route|that result|this result|it\b"):
            return I("explain", 0.85)
        if _has(t, r"\bcompare|comparison|difference between|\bversus\b|\bvs\.?\b"):
            return I("route_compare", 0.9)
        if _has(t, r"\bwhich\b.*\b(faster|quicker|less time|least time|shortest time|takes less|quickest)\b"):
            return I("route_time_compare", 0.9)
        if _has(t, r"\b(rover|perseverance)\b") and not _has(t, r"\b(route|path) to\b"):
            return I("rover_location", 0.9)
        if s["layer"] and s["layer_score"] >= s["section_score"] and _has(t, r"\b(map|layer|layers|basemap|imagery|overlay|view|switch|change|turn on|show|enable)\b"):
            return I("change_layer", 0.85)
        if s["section"] and _has(t, r"\b(open|go to|go back|take me|navigate|switch|show|bring up|return|back to|display)\b"):
            return I("open_section", 0.85)
        if not s["section"] and _has(t, r"\b(dashboard|panel|section|tab|page|screen)\b") and \
                _has(t, r"\b(open|go to|take me|navigate|switch|show|bring up|is there|do you have|where is)\b"):
            return I("open_section", 0.6)          # no matching section: the handler lists the available ones
        if _has(t, r"\b(change|switch)\b.*\b(map|layer|basemap|imagery)\b|\b(turn on|turn off|enable|disable|hide|show)\b.*\b(layer|basemap)s?\b"):
            return I("change_layer", 0.6)          # no matching layer: the handler asks which one / reports no map
        if _has(t, r"change (the |my )?destination|go to .+ instead|instead of"):
            return I("change_destination", 0.8)
        if _has(t, r"\b(shorter|shortest|more direct|most direct|quicker route|faster route)\b"):
            return I("route_shorter", 0.85)
        if _has(t, r"\b(alternative|another|different|other)\b.*\b(route|path|option|way)\b|less steep|gentler|flatter|less difficult|easier route|"
                   r"avoid(ing)? (steep|slopes?)|lower slope|lower terrain"):
            return I("route_alternative", 0.85)
        if "ordinal" in s and route_word and s["display"] or _has(t, r"^(show|display|draw)\b.*\b(it|that|route|recommended|option|r\d)\b") \
                and "destination" not in s:
            return I("route_show", 0.8)
        if _has(t, r"\b(route|path|way|traverse)\b") and _has(t, r"\b(find|plan|generate|get|give|create|calculate|make|best|show|need|want|compute|suggest|recommend)\b") \
                or _has(t, r"how (do|can|would) i (get|walk|go|travel) to|take me to|navigate to|walk to"):
            return I("route_find", 0.85)
        # specific science topics before the generic EVA match ("radiation dose for this EVA" is a radiation question)
        if _has(t, r"\b(radiation|dose|cosmic ray|rad)\b"):
            return I("radiation", 0.85)
        if _has(t, r"space weather|solar|flare|\bsep\b|\bcme\b|proton"):
            return I("space_weather", 0.85)
        if _has(t, r"how long|duration|time budget|\beva\b|how much time|journey take|trip take|walk take|suit time"):
            return I("eva_estimate", 0.85)
        if _has(t, r"\b(risk|risks|hazard|hazards|danger|dangers|threat|concern|concerns|safe|safety)\b"):
            return I("risks", 0.8)
        if _has(t, r"environment|weather|temperature|pressure|\bwind\b|meda|atmospher|humidity"):
            return I("environment", 0.85)
        if _has(t, r"where am i|my (current )?(mission )?(location|position)|current (mission )?(location|position)|where are we"):
            return I("mission_location", 0.9)
        if _has(t, r"analy[sz]e|terrain|slope (here|around|at)|elevation (here|around|at|of)|how steep|"
                   r"(about|describe) (this|the selected|that|my selected) (location|point|place|spot)"):
            return I("terrain_analyze", 0.85)
        if _has(t, r"investigate next|what next|what's next|where (should|do) (i|we) go|next stop|should (i|we) (investigate|visit|explore|do)"):
            return I("next_target", 0.8)
        if _has(t, r"\b(save|bookmark)\b|\b(log|record) (this|it|that)\b"):
            return I("save_record", 0.75)
        if _has(t, r"history|previous analys|mission log|what have (we|i) done|past (routes|analyses)"):
            return I("history", 0.8)
        if _has(t, r"mission (status|context|state|summary)|status of (the|my) mission"):
            return I("mission_location", 0.8)
        if s.get("destination") and route_word:
            return I("route_find", 0.6)
        if s["layer"]:
            return I("change_layer", 0.6)
        if s["section"]:
            return I("open_section", 0.6)
        if _has(t, r"\b(what|why|how|explain|tell me|describe)\b"):
            return I("science_question", 0.5)
        return I("unknown", 0.2)

    # ================================================================== execution
    async def run(self, intent: Intent, text: str, orch) -> tuple[str, str]:
        """Executes the plan; returns (message, status)."""
        handler = getattr(self, f"_do_{intent.name}", None)
        if handler is None:
            return await self._do_unknown(intent, text, orch)
        return await handler(intent, text, orch)

    # ---------------------------------------------------------------- helpers
    def _known_places(self) -> str:
        return ", ".join(p["name"] for p in self.services.mission.pois[:6])

    def _active(self, orch):
        s = orch.session
        return s.routes.get(s.active_route_id or "")

    def _ui(self, orch, cap_id: str) -> bool:
        return cap_id in set(orch.session.frontend.get("capabilities", []))

    def _route_line(self, r: dict) -> str:
        m = r
        return (f"{r['route_id']} \"{r['label']}\": {_fmt_m(m.get('distance_m'))}, max slope {m.get('max_slope_deg')}°, "
                f"mean {m.get('avg_slope_deg')}°, about {_fmt_min(m.get('walk_time_out_min'))} walking out")

    @staticmethod
    def _fail(res) -> str:
        return (res.error or {}).get("message", "unknown error")

    async def _display(self, orch, route_id: str, emphasis="primary") -> str:
        if not self._ui(orch, "map.display_route"):
            return " (No connected map can display routes.)"
        res = await orch.call("map.display_route", {"route_id": route_id, "emphasis": emphasis, "fit": True})
        return f" Sending {route_id} to the map." if res.ok else f" I could not send it to the map: {self._fail(res)}."

    # ---------------------------------------------------------------- intents
    async def _do_empty(self, intent, text, orch):
        return "I didn't receive a message.", "needs_clarification"

    async def _do_help(self, intent, text, orch):
        return ("Marswalk AI online. I can plan and compare walking routes on the Jezero elevation data, analyse terrain around a point, "
                "estimate EVA time and the radiation dose reference, look up scientific documents, show the Perseverance rover's last "
                "known position, and control the map (routes, highlights, layers, sections). Try: \"Find me the best route to the delta\"."), "success"

    async def _do_route_find(self, intent, text, orch, mode=None):
        s, sess = intent.slots, orch.session
        dest = s.get("destination") or (sess.destination or {}).get("poi_id")
        if not dest:
            return (f"Where should the route go? Known places: {self._known_places()}. You can also select a point on the map and ask "
                    "for a route to the selected point."), "needs_clarification"
        await orch.call("mission.get_context", {})
        if s.get("max_slope_deg"):
            await orch.call("mission.set_preferences", {"max_slope_deg": s["max_slope_deg"]})
        args = {"destination": dest, "start": s.get("start", "current_position"), "mode": mode or "candidates"}
        if s.get("max_slope_deg"):
            args["max_slope_deg"] = s["max_slope_deg"]
        if _has(text.lower(), r"minimi[sz]e (terrain )?(difficulty|cost)|lowest (terrain )?cost") and not mode:
            args["mode"] = "candidates"
        res = await orch.call("route.generate", args)
        if not res.ok:
            return f"I could not compute a route: {self._fail(res)}", "error"
        d = res.data
        routes = d["routes"]
        rec = d.get("recommended_route_id")
        head = (f"Computed {len(routes)} candidate route{'s' if len(routes) > 1 else ''} from {d['start']['name']} to "
                f"{d['destination']['name']} on {d['dataset']['name'] if d.get('dataset') else 'the terrain model'}"
                f" (slope limit {d['max_slope_deg']:g}°, project-defined).")
        lines = [f"• {self._route_line(r)}{'  ← recommended' if r['route_id'] == rec else ''}" for r in routes]
        if rec:
            why = d.get("recommendation_rationale") or []
            tail = f"Recommended: {rec}, because it has the {why[0]}." if why else f"Recommended: {rec}."
        else:
            tail = f"None of them keeps every traversed cell at or below {d['max_slope_deg']:g}°, so I am not recommending one."
        for n in d.get("notes", []):
            tail += f" Note: {n}"
        msg = "\n".join([head, *lines, tail, "Meeting the slope limit does not make a route safe; this is a preliminary terrain assessment."])
        if intent.slots.get("display") and (rec or routes):
            msg += await self._display(orch, rec or routes[0]["route_id"])
        elif rec:
            msg += " Would you like me to display it?"
        return msg, "success"

    async def _do_change_destination(self, intent, text, orch):
        if not intent.slots.get("destination"):
            return f"Which destination should I plan to instead? Known places: {self._known_places()}.", "needs_clarification"
        return await self._do_route_find(intent, text, orch)

    async def _base_route(self, intent, orch):
        sess = orch.session
        if intent.slots.get("route_ids"):
            return sess.routes.get(intent.slots["route_ids"][0])
        if intent.slots.get("previous") and sess.previous_route_id:
            return sess.routes.get(sess.previous_route_id)
        return self._active(orch)

    async def _do_route_alternative(self, intent, text, orch):
        base = await self._base_route(intent, orch)
        if not base:
            if intent.slots.get("destination"):
                return await self._do_route_find(intent, text, orch, mode="min_slope")
            return "There is no route yet to find an alternative to. Where should I plan a route to?", "needs_clarification"
        gentle = _has(text.lower(), r"less steep|gentler|flatter|lower slope|easier|less difficult|avoid(ing)? (steep|slopes?)|lower terrain")
        args = {"destination": base["destination"].get("poi_id") or "active_route_end", "start": base["start"].get("poi_id") or "current_position"}
        bm = base["metrics"]
        res, how = None, ""
        if intent.slots.get("max_slope_deg"):
            res = await orch.call("route.generate", {**args, "mode": "min_slope", "max_slope_deg": intent.slots["max_slope_deg"]})
            how = f"with your {intent.slots['max_slope_deg']:g}° slope limit"
        elif gentle and bm.get("max_slope_deg") and bm["max_slope_deg"] - 1 >= 5:
            stricter = float(int(bm["max_slope_deg"] - 1))        # strictly below the current route's steepest cell
            res = await orch.call("route.generate", {**args, "mode": "min_slope", "max_slope_deg": stricter})
            how = f"with a stricter {stricter:g}° slope limit (the current route reaches {bm['max_slope_deg']}°)"
            if not res.ok and res.error.get("code") == "no_path_within_constraints":
                how = f"(no route exists below {stricter:g}° on this terrain model, so I searched a different corridor instead)"
                res = None
        if res is None or not res.ok:
            res = await orch.call("route.generate", {**args, "mode": "min_slope" if gentle else "balanced",
                                                     "avoid_route_ids": [base["route_id"]],
                                                     "max_slope_deg": base["constraints"]["max_slope_deg"]})
            how = how or f"in a different corridor from {base['route_id']}"
        if not res.ok:
            return f"I could not compute an alternative: {self._fail(res)}", "error"
        new = res.data["routes"][0]
        cmp_ = await orch.call("route.compare", {"route_ids": [base["route_id"], new["route_id"]]})
        nm = new
        if nm["max_slope_deg"] is not None and bm.get("max_slope_deg") is not None and nm["max_slope_deg"] < bm["max_slope_deg"]:
            verdict = f"It lowers the maximum slope from {bm['max_slope_deg']}° to {nm['max_slope_deg']}°"
        elif nm["avg_slope_deg"] is not None and bm.get("avg_slope_deg") is not None and nm["avg_slope_deg"] < bm["avg_slope_deg"]:
            verdict = (f"Its maximum slope is not lower ({nm['max_slope_deg']}° vs {bm['max_slope_deg']}°), but the mean slope drops "
                       f"from {bm['avg_slope_deg']}° to {nm['avg_slope_deg']}°")
        else:
            verdict = (f"It is not gentler than {base['route_id']} on this terrain model (max {nm['max_slope_deg']}° vs {bm['max_slope_deg']}°, "
                       f"mean {nm['avg_slope_deg']}° vs {bm['avg_slope_deg']}°)")
        msg = (f"Alternative {new['route_id']} \"{new['label']}\" to {base['destination']['name']}, planned {how}: {_fmt_m(nm['distance_m'])}, "
               f"about {_fmt_min(nm['walk_time_out_min'])} walking out. {verdict}; distance changes from {_fmt_m(bm['distance_m'])} "
               f"to {_fmt_m(nm['distance_m'])}.")
        if not cmp_.ok:
            msg += f" (Comparison failed: {self._fail(cmp_)}.)"
        if intent.slots.get("display"):
            msg += await self._display(orch, new["route_id"])
        return msg, "success"

    async def _do_route_shorter(self, intent, text, orch):
        base = await self._base_route(intent, orch)
        if not base:
            if intent.slots.get("destination"):
                return await self._do_route_find(intent, text, orch, mode="shortest")
            return "There is no route yet. Where should the shortest route go?", "needs_clarification"
        args = {"destination": base["destination"].get("poi_id") or "active_route_end", "start": base["start"].get("poi_id") or "current_position",
                "mode": "shortest", "max_slope_deg": base["constraints"]["max_slope_deg"]}
        res = await orch.call("route.generate", args)
        if not res.ok:
            return f"I could not compute a shorter route: {self._fail(res)}", "error"
        new = res.data["routes"][0]
        await orch.call("route.compare", {"route_ids": [base["route_id"], new["route_id"]]})
        bd, nd = base["metrics"]["distance_m"], new["distance_m"]
        if nd < bd - 1:
            verdict = (f"{new['route_id']} is {_fmt_m(bd - nd)} shorter than {base['route_id']} ({_fmt_m(nd)} vs {_fmt_m(bd)}), with a maximum slope "
                       f"of {new['max_slope_deg']}° (vs {base['metrics']['max_slope_deg']}°) and about {_fmt_min(new['walk_time_out_min'])} walking out.")
        else:
            verdict = (f"The most direct route within the {base['constraints']['max_slope_deg']:g}° limit is {new['route_id']} at {_fmt_m(nd)}, "
                       f"which is not shorter than {base['route_id']} ({_fmt_m(bd)}). {base['route_id']} is already as short as the slope limit allows "
                       "on this terrain model.")
        msg = verdict
        if intent.slots.get("display") or _has(text.lower(), r"\b(make|show)\b"):
            msg += await self._display(orch, new["route_id"])
        return msg, "success"

    async def _do_route_compare(self, intent, text, orch):
        sess = orch.session
        ids = intent.slots.get("route_ids")
        if not ids and _has(text.lower(), r"\b(current|this|my)\b.*\b(previous|last|earlier)\b|\bprevious\b"):
            ids = [x for x in (sess.previous_route_id, sess.active_route_id) if x]
        if len(sess.routes) < 2:
            return "I need at least two routes to compare. Ask me to find a route (I generate three candidates) first.", "needs_clarification"
        res = await orch.call("route.compare", {"route_ids": ids} if ids and len(ids) >= 2 else {})
        if not res.ok:
            return f"Comparison failed: {self._fail(res)}", "error"
        rows, best = res.data["routes"], res.data["best_by"]
        lines = [f"• {self._route_line(r)}" for r in rows]
        msg = "\n".join([f"Comparison of {', '.join(r['route_id'] for r in rows)}:", *lines,
                         f"Shortest: {best['shortest']}. Fastest walk out: {best['fastest']}. Lowest maximum slope: {best['lowest_max_slope']}. "
                         f"Lowest terrain cost: {best['lowest_terrain_cost']}."])
        return msg, "success"

    async def _do_route_time_compare(self, intent, text, orch):
        if len(orch.session.routes) < 2:
            r = self._active(orch)
            if not r:
                return "There are no routes yet. Ask me to find a route first.", "needs_clarification"
            return f"There is only one route ({r['route_id']}): about {_fmt_min(r['metrics']['walk_time_out_min'])} walking out.", "success"
        res = await orch.call("route.compare", {"route_ids": intent.slots["route_ids"]} if len(intent.slots.get("route_ids", [])) >= 2 else {})
        if not res.ok:
            return f"Comparison failed: {self._fail(res)}", "error"
        rows = sorted(res.data["routes"], key=lambda r: r["walk_time_out_min"] if r["walk_time_out_min"] is not None else 1e9)
        f = rows[0]
        others = "; ".join(f"{r['route_id']} {_fmt_min(r['walk_time_out_min'])}" for r in rows[1:])
        return (f"{f['route_id']} \"{f['label']}\" takes the least walking time: about {_fmt_min(f['walk_time_out_min'])} out "
                f"({_fmt_m(f['distance_m'])}), versus {others}. Times are modified-Tobler estimates for walking out only; "
                "ask for an EVA estimate to include stops and the return leg."), "success"

    async def _do_route_show(self, intent, text, orch):
        sess = orch.session
        rid = (intent.slots.get("route_ids") or [None])[0]
        if rid is None and "ordinal" in intent.slots:
            multi = next((rs for rs in reversed(sess.route_sets) if len(rs["route_ids"]) > 1), None)   # "the second option"
            if multi and intent.slots["ordinal"] < len(multi["route_ids"]):
                rid = multi["route_ids"][intent.slots["ordinal"]]
        if rid is None and _has(text.lower(), r"recommend"):
            rid = (sess.latest_set() or {}).get("recommended_id")
        rid = rid or sess.active_route_id
        if not rid or rid not in sess.routes:
            return "There is no such route yet. Ask me to find a route first.", "needs_clarification"
        r = sess.routes[rid]
        return f"{self._route_line(route_summary(r))}.{await self._display(orch, rid)}", "success"

    async def _do_route_steepest(self, intent, text, orch):
        r = await self._base_route(intent, orch)
        if not r:
            return "There is no active route. Ask me to find a route first.", "needs_clarification"
        res = await orch.call("route.evaluate", {"route_id": r["route_id"]})
        if not res.ok:
            return f"Route evaluation failed: {self._fail(res)}", "error"
        sp, secs = res.data.get("steepest_point"), res.data.get("steep_sections", [])
        if not sp or sp.get("slope_deg") is None:
            return f"{r['route_id']} has no slope data along its cells.", "partial"
        msg = (f"The steepest point on {r['route_id']} is {sp['slope_deg']}° at about {_fmt_m(sp['at_m'])} from the start "
               f"({sp['lat']:.5f}°N, {sp['lon']:.5f}°E).")
        if secs:
            top = secs[0]
            msg += (f" The steepest section runs from {top['from_m']:.0f} m to {top['to_m']:.0f} m ({top['length_m']:.0f} m long, max {top['max_slope_deg']}°). "
                    f"{len(secs)} section(s) exceed the {r['constraints']['caution_slope_deg']:g}° caution slope.")
        else:
            msg += f" No section exceeds the {r['constraints']['caution_slope_deg']:g}° caution slope."
        if self._ui(orch, "map.highlight_location"):
            h = await orch.call("map.highlight_location", {"target": "route_steepest", "route_id": r["route_id"], "style": "caution"})
            z = await orch.call("map.zoom_to_location", {"target": "route_steepest", "route_id": r["route_id"], "zoom": 17})
            msg += " Highlighting it on the map." if h.ok and z.ok else f" Map highlight failed: {self._fail(h if not h.ok else z)}."
        return msg, "success"

    async def _do_explain(self, intent, text, orch):
        sess = orch.session
        r = self._active(orch)
        if r is not None and not r.get("rationale") and _has(text.lower(), r"recommend"):
            # "why did you recommend ..." refers to the route that was actually recommended
            rec = next((sess.routes.get(rs["recommended_id"]) for rs in reversed(sess.route_sets) if rs.get("recommended_id")), None)
            r = rec or r
        last = sess.analyses[-1] if sess.analyses else None
        if r is None and last is None:
            return "There is nothing to explain yet: no route or analysis has been produced in this session.", "needs_clarification"
        if r is None or (last and last["type"] not in ("route_generation", "route_evaluation", "route_comparison") and not _has(text.lower(), r"route|recommend")):
            return (f"Last result ({last['analysis_id']}, {last['type'].replace('_', ' ')}): {last['summary']}. Data mode: {last['data_mode']}, "
                    f"provider: {last['provider']}."), "success"
        ev = await orch.call("route.evaluate", {"route_id": r["route_id"]})
        why = r.get("rationale")
        m = r["metrics"]
        if why:
            msg = f"I recommended {r['route_id']} \"{r['label']}\" because it has the " + "; ".join(why) + "."
        else:
            msg = (f"{r['route_id']} was not a recommendation; it is the \"{r['label']}\" option ({_fmt_m(m['distance_m'])}, max {m['max_slope_deg']}°, "
                   f"terrain cost {m['weighted_cost']}).")
        msg += (f" The recommendation is deterministic: among candidates that keep every traversed cell at or below {r['constraints']['max_slope_deg']:g}°, "
                "pick the lowest length-weighted terrain cost, where each cell costs 1 + (slope / limit)². ")
        if ev.ok and ev.data.get("classification"):
            msg += f"Project-defined terrain class: {ev.data['classification'].get('terrain_difficulty')}."
        doc = await orch.call("science.search_documents", {"query": "slope traversability terrain cost walking EVA spacesuit", "top_k": 2})
        if doc.ok and doc.data.get("results"):
            msg += " Background: " + "; ".join(f"[{d['citation_key']}] {d['title']}" for d in doc.data["results"]) + "."
        return msg, "success"

    async def _do_eva_estimate(self, intent, text, orch):
        r = await self._base_route(intent, orch)
        if not r:
            return "I need a route to estimate the EVA. Ask me to find a route first.", "needs_clarification"
        stops = 1 if (r["destination"].get("kind") == "science_stop") else 0
        res = await orch.call("eva.estimate", {"route_id": r["route_id"], "stops": stops})
        if not res.ok:
            return f"EVA estimation failed: {self._fail(res)}", "error"
        d = res.data
        e = normalize_eva(d)
        msg = (f"EVA estimate for {r['route_id']} ({r['start']['name']} → {r['destination']['name']} and back): walking out {_fmt_min(e['walk_out_min'])}, "
               f"science stops {_fmt_min(e['science_min'])}, walking back {_fmt_min(e['walk_back_min'])}; total {_fmt_min(e['total_min'])}.")
        if e.get("remaining_margin_min") is not None:
            msg += (f" Against a suit budget of {_fmt_min(e['suit_budget_min'])} with a {_fmt_min(e['required_reserve_min'])} reserve, "
                    f"{_fmt_min(e['remaining_margin_min'])} of margin remains (status: {e['status']}).")
        msg += f" Source: {res.provenance.get('provider')}; the times are model estimates, not measurements."
        return msg, "success"

    async def _do_risks(self, intent, text, orch):
        r = await self._base_route(intent, orch)
        bullets, status = [], "success"
        if r:
            ev = await orch.call("route.evaluate", {"route_id": r["route_id"]})
            if ev.ok:
                m, secs = ev.data["metrics"], ev.data.get("steep_sections", [])
                if not m.get("within_constraints"):
                    bullets.append(f"Slope: {r['route_id']} crosses terrain steeper than the {r['constraints']['max_slope_deg']:g}° limit (max {m['max_slope_deg']}°).")
                if secs:
                    t = secs[0]
                    bullets.append(f"Steep terrain: {len(secs)} section(s) above {r['constraints']['caution_slope_deg']:g}°; the steepest is {t['max_slope_deg']}° "
                                   f"at {t['from_m']:.0f}-{t['to_m']:.0f} m.")
                if m.get("end_cell_slope_deg") and m["end_cell_slope_deg"] > r["constraints"]["max_slope_deg"]:
                    bullets.append(f"Destination: the destination cell itself has a {m['end_cell_slope_deg']}° slope.")
            eva = await orch.call("eva.estimate", {"route_id": r["route_id"], "stops": 1 if r["destination"].get("kind") == "science_stop" else 0})
            if eva.ok:
                e = normalize_eva(eva.data)
                if e.get("status") and e["status"] != "nominal":
                    bullets.append(f"Time: the EVA estimate ({_fmt_min(e['total_min'])}) is {e['status']} against the suit budget.")
                else:
                    bullets.append(f"Time: the EVA estimate is {_fmt_min(e['total_min'])}, within the configured suit budget.")
        else:
            bullets.append("No active route, so route-specific terrain risks cannot be assessed.")
        sw = await orch.call("space_weather.analyze", {})
        if sw.ok:
            cls = sw.data.get("classification") or {}
            bullets.append(f"Space weather: {cls.get('space_weather_status') or sw.data.get('status')} (project-defined rule).")
        else:
            bullets.append(f"Space weather: not assessed ({self._fail(sw)}).")
            status = "partial"
        bullets.append("Radiation: only a Gale-crater reference rate is available; there is no Jezero measurement.")
        return "Main risks I can identify from the available data:\n" + "\n".join(f"• {b}" for b in bullets) + \
               "\nThis is a preliminary assessment from elevation data and models; it does not establish safety.", status

    async def _point_args(self, intent, text, orch) -> dict:
        sess = orch.session
        if intent.slots.get("destination") and not _has(text.lower(), r"\bthis\b|\bhere\b"):
            return {"target": "poi", "poi_id": intent.slots["destination"]}
        if (sess.client_context.get("selected") or {}).get("lat") is not None:
            return {"target": "selected"}
        if ((sess.client_context.get("map") or {}).get("center") or {}).get("lat") is not None and _has(text.lower(), r"\bthis\b|\bhere\b|map"):
            return {"target": "map_center"}
        return {"target": "current_position"}

    async def _do_terrain_analyze(self, intent, text, orch):
        args = await self._point_args(intent, text, orch)
        if intent.slots.get("radius_m"):
            args["radius_m"] = min(1000.0, max(20.0, intent.slots["radius_m"]))
        res = await orch.call("terrain.analyze", args)
        if not res.ok:
            return f"Terrain analysis failed: {self._fail(res)}", "error"
        d = res.data
        loc = d.get("location", {})
        if not d.get("data_available"):
            return f"No elevation data covers {loc.get('name', 'that point')}: {d.get('reason') or d.get('no_data_reason')}", "partial"
        basis = {"selected": "the selected point", "map_center": "the map centre", "current_position": "your current position"}.get(args["target"], loc.get("name"))
        msg = (f"Terrain within {d.get('radius_m', 0):.0f} m of {basis} ({loc['lat']:.5f}°N, {loc['lon']:.5f}°E), from {d['dataset']['name']} at "
               f"{d['dataset']['resolution_m']} m: elevation {d['elevation_m']} m (areoid), relief {d.get('relief_m')} m, mean slope "
               f"{d.get('slope_mean_deg')}°, 90th percentile {d.get('slope_p90_deg')}°, maximum {d.get('slope_max_deg')}°. "
               f"{d.get('area_above_caution_pct')}% of the area is above the caution slope. Class: {d['classification']['terrain_difficulty']} "
               f"(project-defined, {d['classification'].get('basis', '')}).")
        n = d.get("nearest_rover_localisation")
        if n:
            msg += f" Nearest Perseverance localisation: sol {n['sol']}, {_fmt_m(n['distance_m'])} away (rover-measured {n['elev_geoid_m']:.1f} m)."
        if args["target"] == "current_position" and _has(text.lower(), r"\bthis\b|\bhere\b"):
            msg += " (No point is selected on the map, so I used your current position.)"
        return msg, "success"

    async def _do_environment(self, intent, text, orch):
        res = await orch.call("environment.analyze", {})
        if not res.ok:
            return (f"No environmental data source is connected: {self._fail(res)} What I can provide instead: terrain from the elevation models, "
                    "the Curiosity RAD radiation reference (Gale crater) and historical Perseverance positions. I will not invent MEDA readings."), "partial"
        d = res.data
        meas = d.get("measurements") or {}
        cls = d.get("classification") or {}
        if not meas:
            return f"The environmental source returned no observations ({cls.get('environmental_status', 'unavailable')}).", "partial"
        t, p, w = meas.get("air_temperature_c") or {}, meas.get("atmospheric_pressure_pa") or {}, meas.get("wind_speed_ms") or {}
        return (f"MEDA record for sol {meas.get('sol')} ({meas.get('terrestrial_date')}) at {((meas.get('observation_location') or {}).get('site_name')) or 'the rover position'}: "
                f"air temperature {t.get('min')} to {t.get('max')} °C, pressure {p.get('min')} to {p.get('max')} Pa, wind up to {w.get('max')} m/s. "
                f"Status: {cls.get('environmental_status')}. These are historical in-situ values at the rover, not crater-wide or live weather; "
                f"source: {res.provenance.get('provider')}."), "success"

    async def _do_radiation(self, intent, text, orch):
        args = {}
        if intent.slots.get("hours"):
            args["eva_hours"] = intent.slots["hours"]
        else:
            r = self._active(orch)
            if r and not any(a["type"] == "eva_estimation" for a in orch.session.analyses):
                await orch.call("eva.estimate", {"route_id": r["route_id"], "stops": 1 if r["destination"].get("kind") == "science_stop" else 0})
        res = await orch.call("radiation.estimate", args)
        if not res.ok:
            return (f"Radiation estimate unavailable: {self._fail(res)} The reference value is the Curiosity RAD surface dose rate measured at "
                    "Gale crater; there is no Jezero measurement."), "partial"
        d = res.data
        calc, meas = d.get("calculations") or {}, d.get("measurements") or {}
        rate = d.get("reference_dose_rate_mgy_per_day") or meas.get("reference_dose_rate_mgy_per_day")
        hours = d.get("eva_hours") or meas.get("eva_duration_hours")
        dose = d.get("estimated_absorbed_dose_mgy") or calc.get("estimated_absorbed_dose_mgy")
        msg = f"Radiation reference: {rate} mGy/day galactic-cosmic-ray absorbed dose, measured at Gale crater by Curiosity RAD (not at Jezero)."
        if dose is not None:
            msg += f" For a {hours} h EVA that scales to about {dose} mGy absorbed dose (estimate)."
        else:
            msg += " No EVA duration is known yet, so I have not scaled it."
        return msg + " Solar particle events are not included; check space weather separately.", "success"

    async def _do_space_weather(self, intent, text, orch):
        res = await orch.call("space_weather.analyze", {})
        if not res.ok:
            return f"Space-weather data is not available: {self._fail(res)} I will not guess the solar conditions.", "partial"
        d = res.data
        cls, meas, calc = d.get("classification") or {}, d.get("measurements") or {}, d.get("calculations") or {}
        status = cls.get("space_weather_status") or d.get("status")
        return (f"Space weather (project-defined rule): {status}. Inspected {meas.get('inspected_flare_count', d.get('flare_count'))} flare(s), "
                f"{meas.get('inspected_sep_count', d.get('sep_count'))} SEP event(s), {meas.get('inspected_cme_count', '?')} CME(s); "
                f"{calc.get('active_hazards_count', 0)} triggered the caution rule. Data source: {meas.get('data_source') or d.get('source')}."), "success"

    async def _do_rover_location(self, intent, text, orch):
        res = await orch.call("rover.get_location", {})
        if not res.ok:
            return f"Rover data unavailable: {self._fail(res)}", "error"
        loc = res.data["latest_localisation"]
        msg = (f"Perseverance's last localisation in the local NASA snapshot: sol {loc['sol']}, {loc['lat']:.5f}°N {loc['lon']:.5f}°E, "
               f"{loc['elev_geoid_m']:.1f} m (areoid), after {_fmt_m(loc.get('dist_total_m'))} of driving. This is historical data, not a live position.")
        if self._ui(orch, "map.highlight_location"):
            h = await orch.call("map.highlight_location", {"target": "point", "lat": loc["lat"], "lon": loc["lon"],
                                                           "label": f"Perseverance, sol {loc['sol']}", "style": "rover"})
            z = await orch.call("map.zoom_to_location", {"target": "point", "lat": loc["lat"], "lon": loc["lon"], "zoom": 15,
                                                         "label": f"Perseverance, sol {loc['sol']}"})
            msg += " Marking it on the map." if h.ok and z.ok else f" Map update failed: {self._fail(h if not h.ok else z)}."
        return msg, "success"

    async def _do_mission_location(self, intent, text, orch):
        res = await orch.call("mission.get_context", {})
        if not res.ok:
            return f"Mission context unavailable: {self._fail(res)}", "error"
        d = res.data
        cp = d["current_position"]
        msg = (f"Mission {d['mission']['name']} (status: {d['mission']['status']}, mock). Current position: {cp['name']} at "
               f"{cp['lat']:.5f}°N, {cp['lon']:.5f}°E (assumed in this environment).")
        if d.get("destination"):
            msg += f" Destination: {d['destination']['name']}."
        if d.get("active_route"):
            a = d["active_route"]
            msg += f" Active route: {a['route_id']} ({_fmt_m(a['distance_m'])}, max {a['max_slope_deg']}°)."
        sel = (d.get("ui") or {}).get("selected")
        if sel and sel.get("lat") is not None:
            msg += f" Selected on the map: {sel['lat']:.5f}°N, {sel['lon']:.5f}°E."
        return msg, "success"

    async def _do_next_target(self, intent, text, orch):
        r = self._active(orch)
        args = {"target": "active_route_end" if r else "current_position", "radius_m": 5000}
        res = await orch.call("poi.get_nearby", args)
        if not res.ok:
            return f"I could not look up nearby places: {self._fail(res)}", "error"
        d = res.data
        cand = d.get("unvisited_planned_stops") or d.get("places") or []
        if not cand:
            return "No planned stops remain within 5 km of your position in the mission data.", "success"
        lines = [f"• {c['name']} ({_fmt_m(c['distance_m'])} away): {c.get('reason') or 'no reason recorded'}" for c in cand[:3]]
        return ("Planned stops you have not routed to yet, nearest first:\n" + "\n".join(lines) +
                "\nThe stop names and reasons are Marswalk's starter set and still need confirmation by the science team. "
                f"Say \"find a route to {cand[0]['name'].split(' · ')[0].lower()}\" to plan the next leg."), "success"

    async def _do_history(self, intent, text, orch):
        res = await orch.call("mission.get_history", {"limit": 10})
        if not res.ok:
            return f"History unavailable: {self._fail(res)}", "error"
        a = res.data.get("analyses", [])
        if not a and not res.data.get("saved_records"):
            return "No analyses or saved records yet in this session.", "success"
        lines = [f"• {x['analysis_id']} {x['type'].replace('_', ' ')}: {x['summary']}" for x in a[-8:]]
        recs = res.data.get("saved_records") or []
        return "Mission history (this session):\n" + "\n".join(lines) + (f"\nSaved log records: {len(recs)}." if recs else ""), "success"

    async def _do_save_record(self, intent, text, orch):
        r = self._active(orch)
        label = f"Route {r['route_id']} to {r['destination']['name']}" if r else "Assistant note"
        res = await orch.call("mission.save_record", {"label": label, "note": text[:500], **({"route_id": r["route_id"]} if r else {})})
        if res.status == "pending_confirmation":
            return f"Save \"{label}\" to the mission log? Please confirm.", "needs_confirmation"
        return (f"Saved \"{label}\"." if res.ok else f"Could not save: {self._fail(res)}"), ("success" if res.ok else "error")

    async def _do_change_layer(self, intent, text, orch):
        lid = intent.slots.get("layer")
        if not lid:
            names = ", ".join(l.get("label", l["id"]) for l in orch.session.frontend.get("layers") or [])
            return f"Which layer? Available: {names or 'none (no map connected)'}.", "needs_clarification"
        hide = _has(text.lower(), r"\b(hide|turn off|disable|remove)\b")
        res = await orch.call("map.change_layer", {"layer_id": lid, "visible": not hide})
        label = next((l.get("label", lid) for l in orch.session.frontend.get("layers") or [] if l["id"] == lid), lid)
        return ((f"{'Hiding' if hide else 'Switching on'} {label}." if res.ok else f"Layer change failed: {self._fail(res)}"),
                "success" if res.ok else "error")

    async def _do_open_section(self, intent, text, orch):
        sid = intent.slots.get("section")
        if not sid:
            secs = ", ".join(x.get("label", x["id"]) for x in orch.session.frontend.get("sections") or [])
            return (f"This interface has no such section. Available sections: {secs}." if secs
                    else "No connected interface offers navigable sections."), "needs_clarification"
        res = await orch.call("navigation.open_section", {"section_id": sid})
        label = next((x.get("label", sid) for x in orch.session.frontend.get("sections") or [] if x["id"] == sid), sid)
        return (f"Opening {label}." if res.ok else f"Navigation failed: {self._fail(res)}"), ("success" if res.ok else "error")

    async def _do_clear_routes(self, intent, text, orch):
        ids = intent.slots.get("route_ids")
        res = await orch.call("map.clear_route", {"route_id": ids[0]} if ids else {})
        return (("Clearing " + (f"{ids[0]}" if ids else "the assistant's route overlays") + " from the map. The routes stay in memory.")
                if res.ok else f"Could not clear routes: {self._fail(res)}"), ("success" if res.ok else "error")

    async def _do_science_question(self, intent, text, orch):
        res = await orch.call("science.search_documents", {"query": text[:300], "top_k": 3})
        if not res.ok:
            return (f"I can't answer that from documents: {self._fail(res)} An LLM provider is also not configured, so I can only run the "
                    "mission tools. Try asking for a route, terrain analysis, EVA estimate or rover location."), "partial"
        hits = res.data.get("results") or []
        if not hits:
            return "I found nothing relevant in the scientific document corpus, and I won't answer without a source.", "partial"
        lines = [f"[{h['citation_key']}] {h['title']} — {h['section']}: {first_sentence(h['text'])}" for h in hits]
        return ("Relevant passages from the scientific corpus (quoted, not interpreted, since no LLM is configured):\n" + "\n".join(lines)), "success"

    async def _do_unknown(self, intent, text, orch):
        return ("I'm not sure how to map that to an available mission capability. I can find and compare routes, analyse terrain, "
                "estimate EVA time and radiation, show the rover's last position, change map layers and open sections."), "needs_clarification"


def first_sentence(text: str) -> str:
    t = " ".join(text.replace("*", "").split())
    m = re.search(r"(.{30,280}?[.!?])(\s|$)", t)
    return m.group(1) if m else t[:280]


def route_summary(r: dict) -> dict:
    return {"route_id": r["route_id"], "label": r["label"], **r["metrics"]}


def normalize_eva(d: dict) -> dict:
    """Common view of the standalone, Science Engine and Marswalk EVA outputs (minutes)."""
    if "walk_out_min" in d and "total_min" in d and "suit_budget_min" in d:
        return d
    c = d.get("calculations") or {}
    if c:
        h = lambda k: None if c.get(k) is None else round(c[k] * 60, 1)  # noqa: E731
        cls = d.get("classification") or {}
        sev = (cls.get("severity") or "").lower() or None
        return {"walk_out_min": h("outbound_walking_hours"), "walk_back_min": h("return_walking_hours"),
                "science_min": h("scientific_activity_hours"), "total_min": h("total_eva_duration_hours"),
                "suit_budget_min": h("configured_budget_hours"), "required_reserve_min": h("required_reserve_hours"),
                "remaining_margin_min": h("remaining_margin_hours"), "status": sev}
    return {"walk_out_min": d.get("walk_out_min"), "walk_back_min": d.get("walk_back_min"), "science_min": d.get("science_min"),
            "total_min": d.get("total_min"), "suit_budget_min": (d.get("suit_limit_min")), "required_reserve_min": None,
            "remaining_margin_min": d.get("reserve_min"), "status": d.get("status")}
