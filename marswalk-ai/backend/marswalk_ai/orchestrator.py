"""Conversation orchestrator.

    user message
      → context manager (session memory + latest UI state)
      → LLM tool loop (provider)  ── or ──  rule planner (offline / fallback)
      → Capability Registry (allowlist, schema validation, permissions, timeouts)
      → adapters (standalone / Science Engine / Marswalk backend / frontend actions)
      → validated results → cards, frontend actions, evidence, limitations
      → grounding check of the reply → structured AssistantResponse
"""
from __future__ import annotations

import json
import re
import time
import uuid

from .cards import build_cards, suggestions_for
from .context import ExecContext, TurnRecorder
from .planner import RulePlanner
from .prompts import build_system_prompt
from .providers.base import LLMProvider, ProviderError, ToolSpec
from .registry import CapabilityRegistry, ToolResult

INTENT_BY_CAP = {
    "route.generate": "route_planning", "route.compare": "route_comparison", "route.evaluate": "route_evaluation",
    "terrain.analyze": "terrain_analysis", "eva.estimate": "eva_estimation", "radiation.estimate": "radiation",
    "space_weather.analyze": "space_weather", "environment.analyze": "environment", "rover.get_location": "rover_location",
    "science.search_documents": "science_question", "poi.get_nearby": "next_target", "mission.get_history": "history",
    "mission.save_record": "save_record", "map.display_route": "map_control", "map.highlight_location": "map_control",
    "map.zoom_to_location": "map_control", "map.change_layer": "map_control", "map.clear_route": "map_control",
    "navigation.open_section": "navigation", "mission.get_context": "mission_context",
}
HEAVY_KEYS = {"geometry", "slope_classes", "profile", "_waypoints", "waypoints"}
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?")


def _trim(data, depth=0):
    """Tool data as sent to the LLM / the response: drop heavy arrays, cap list lengths."""
    if isinstance(data, dict):
        return {k: _trim(v, depth + 1) for k, v in data.items() if k not in HEAVY_KEYS}
    if isinstance(data, list):
        return [_trim(v, depth + 1) for v in data[:12]]
    return data


class TurnRun:
    """Executes capabilities for one user turn and records everything."""

    def __init__(self, orch: "Orchestrator", session, user_text: str):
        self.orch = orch
        self.session = session
        self.turn = TurnRecorder(session, user_text)
        self.ctx = ExecContext(session=session, services=orch.services, turn=self.turn)
        self.calls: list[dict] = []

    async def call(self, cap_id: str, args: dict | None, origin: str = "rules", call_id: str | None = None) -> ToolResult:
        self.ctx.origin = origin
        res = await self.orch.registry.execute(cap_id, args if args is not None else {}, self.ctx, origin=origin, call_id=call_id)
        self.calls.append({"call_id": res.call_id, "capability_id": cap_id, "arguments": res.arguments, "origin": origin})
        self.turn.results.append(res)
        if res.ok:
            self.turn.absorb(res.data)
        else:
            self.turn.errors.append({"capability_id": cap_id, **(res.error or {"code": "error", "message": "failed"})})
        return res


class Orchestrator:
    def __init__(self, services, registry: CapabilityRegistry, provider: LLMProvider | None, provider_note: str):
        self.services = services
        self.registry = registry
        self.provider = provider
        self.provider_note = provider_note
        self.planner = RulePlanner(services)

    # ================================================================== chat
    async def chat(self, session, message: str, client_context: dict | None = None) -> dict:
        t0 = time.perf_counter()
        if client_context:
            session.client_context = {**session.client_context, **client_context}
        run = TurnRun(self, session, message)
        mode, intent = "rules", None
        status, text = "success", ""
        if self.provider is not None:
            mode = "llm"
            try:
                text, status = await self._llm_loop(run, message)
                intent = self._intent_from_calls(run, "llm")
            except ProviderError as e:
                run.turn.errors.append({"capability_id": None, "code": "llm_unavailable", "message": str(e)})
                if any(r for r in run.turn.results):
                    text, status = self._summary_after_llm_failure(run), "partial"
                    intent = self._intent_from_calls(run, "llm")
                else:
                    mode = "rules-fallback"
        if mode in ("rules", "rules-fallback"):
            it = self.planner.parse(message, session)
            text, status = await self.planner.run(it, message, run)
            intent = {"name": it.name, "confidence": it.confidence, "source": "rules"}
            if mode == "rules-fallback":
                text = "(Language model unavailable; answered with deterministic mission rules.) " + text
        if run.turn.confirmations and status == "success":
            status = "needs_confirmation"
        if status == "success" and any(not r.ok and r.status != "pending_confirmation" for r in run.turn.results):
            status = "partial"
        if mode == "llm":
            self._grounding_check(run, text)
        session.add_message("user", message)
        session.add_message("assistant", text, intent=intent["name"])
        session.topic = intent["name"]
        self.services.store.save(session)
        return self._response(run, text, status, intent, mode, t0)

    async def _llm_loop(self, run: TurnRun, message: str) -> tuple[str, str]:
        session = run.session
        caps = self.registry.usable(session)
        tools = []
        for c in caps:
            flags = []
            if c.side == "frontend":
                flags.append("frontend action: returns pending_ack")
            if c.permission == "confirm":
                flags.append("requires user confirmation")
            if c.data_mode not in ("n/a", ""):
                flags.append(f"data: {c.data_mode}")
            tools.append(ToolSpec(c.llm_name, c.description + (f" [{'; '.join(flags)}]" if flags else ""), c.schema_for(session)))
        system = build_system_prompt(session.mission_summary(self._mission_for_prompt()), session.frontend, self.services.status)
        transcript = self._history(session) + [{"role": "user", "content": message}]
        for _ in range(self.services.settings.max_tool_rounds):
            resp = await self.provider.complete(system, transcript, tools)
            if not resp.tool_calls:
                return (resp.text.strip() or "(The model returned an empty reply.)"), "success"
            transcript.append({"role": "assistant", "content": resp.text, "tool_calls": resp.tool_calls, "raw": resp.raw})
            for tc in resp.tool_calls:
                cap = self.registry.by_llm_name(tc.name)
                res = await run.call(cap.id if cap else tc.name.replace("__", "."), tc.arguments, origin="llm", call_id=tc.id)
                payload = {"status": res.status, "ok": res.ok, "data": _trim(res.data), "error": res.error,
                           "provenance": res.provenance}
                transcript.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                                   "content": json.dumps(payload, default=str)[:6000]})
        return ("I stopped after the maximum number of tool steps for one request. The completed results are shown below; "
                "please narrow the request."), "partial"

    def _history(self, session) -> list[dict]:
        out: list[dict] = []
        for m in session.recent_messages(8):
            role = "user" if m["role"] == "user" else "assistant"
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n" + m["text"]
            else:
                out.append({"role": role, "content": m["text"]})
        while out and out[0]["role"] != "user":
            out.pop(0)
        if out and out[-1]["role"] == "user":       # the new message must follow an assistant turn
            out.append({"role": "assistant", "content": "(no reply recorded)"})
        return out

    def _mission_for_prompt(self) -> dict:
        m = self.services.mission
        return {"name": m.mission["name"], "status": m.mission["status"] + " (mock)", "current_position": m.current_position(),
                "places": [{"poi_id": p["id"], "name": p["name"]} for p in m.pois]} | {"current_position": m.current_position()}

    def _intent_from_calls(self, run: TurnRun, source: str) -> dict:
        names = [INTENT_BY_CAP.get(c["capability_id"]) for c in run.calls]
        names = [n for n in names if n and n != "mission_context"] or [n for n in names if n] or ["conversation"]
        return {"name": names[0], "confidence": None, "source": source, "capabilities": [c["capability_id"] for c in run.calls]}

    def _summary_after_llm_failure(self, run: TurnRun) -> str:
        done = [f"{r.capability_id}: {'ok' if r.ok else (r.error or {}).get('message', 'failed')}" for r in run.turn.results]
        return ("The language model failed part-way through this request, so I can't write a full answer. Operations that ran: "
                + "; ".join(done) + ". The result cards below show their actual outputs.")

    def _grounding_check(self, run: TurnRun, text: str) -> None:
        allowed = set(run.turn.numbers)
        for blob in (json.dumps(run.session.mission_summary(self._mission_for_prompt()), default=str), run.turn.user_text,
                     json.dumps(self.services.status, default=str)):
            allowed |= {float(x.replace(",", ".")) for x in _NUMBER.findall(blob)}
        s = self.services.settings
        allowed |= {s.max_slope_deg, s.caution_slope_deg, s.k_suit, s.rad_reference_mgy_day, s.suit_budget_hours, s.reserve_percent, 24.0}
        bad = []
        for raw in _NUMBER.findall(text):
            n = float(raw.replace(",", "."))
            if abs(n) <= 10 and float(n).is_integer():
                continue                                    # counts, ordinals, list markers
            if not any(abs(n - v) <= max(0.051, 0.006 * abs(v)) or abs(n - v / 1000) <= 0.006 + 0.006 * abs(v / 1000)
                       or abs(n - v / 60) <= 0.051 or abs(n - v * 60) <= 0.6 for v in allowed):
                bad.append(raw)
        if bad:
            run.turn.add_limitations([f"Grounding check: the reply mentions value(s) not found in any tool result ({', '.join(sorted(set(bad))[:6])}). "
                                      "Rely on the result cards for exact figures."])
        if re.search(r"\b(?:is|are|completely|totally|perfectly)\s+safe\b", text, re.I) and not re.search(r"\bnot\s+(?:\w+\s+)?safe\b", text, re.I):
            run.turn.add_limitations(["The reply used the word 'safe'. Marswalk results are preliminary terrain assessments and never establish safety."])

    # ================================================================== buttons, confirmations, action acks
    async def command(self, session, capability_id: str, arguments: dict) -> dict:
        t0 = time.perf_counter()
        run = TurnRun(self, session, "")
        res = await run.call(capability_id, arguments, origin="user_button")
        if res.ok and res.status == "pending_ack":
            text, status = (run.turn.frontend_actions[-1]["summary"] + "."), "success"
        elif res.ok and res.status == "pending_confirmation":
            text, status = "Please confirm this action.", "needs_confirmation"
        elif res.ok:
            text, status = f"{capability_id} completed.", "success"
        else:
            text, status = f"{capability_id} failed: {(res.error or {}).get('message')}", "error"
        self.services.store.save(session)
        return self._response(run, text, status, {"name": INTENT_BY_CAP.get(capability_id, "command"), "confidence": 1.0, "source": "button"},
                              "command", t0)

    async def confirm(self, session, confirmation_id: str, approve: bool) -> dict:
        t0 = time.perf_counter()
        pending = session.pending_confirmations.pop(confirmation_id, None)
        run = TurnRun(self, session, "")
        if not pending:
            text, status = "That confirmation is no longer pending.", "error"
        elif not approve:
            text, status = f"Cancelled: {pending['summary']}", "success"
        else:
            res = await run.call(pending["capability_id"], pending["arguments"], origin="confirm")
            text, status = (f"Done: {pending['summary']}", "success") if res.ok else (f"Failed: {(res.error or {}).get('message')}", "error")
        session.add_message("assistant", text, intent="confirmation")
        self.services.store.save(session)
        return self._response(run, text, status, {"name": "confirmation", "confidence": 1.0, "source": "button"}, "command", t0)

    def action_result(self, session, action_id: str, ok: bool, detail: str | None, action_type: str | None = None) -> dict:
        rec = session.actions.get(action_id)
        if rec is None:
            rec = session.actions[action_id] = {"type": action_type or "unknown", "summary": "client-initiated", "ts": time.time()}
        rec["status"] = "succeeded" if ok else "failed"
        rec["detail"] = (detail or "")[:300] or None
        self.services.store.save(session)
        return {"action_id": action_id, "status": rec["status"], "recorded": True}

    # ================================================================== response
    def _response(self, run: TurnRun, text: str, status: str, intent: dict, mode: str, t0: float) -> dict:
        results = run.turn.results
        modes = sorted({r.provenance.get("data_mode") for r in results if r.ok and r.provenance.get("data_mode")} - {"n/a", None})
        simulated = "simulated" in modes or any((r.data.get("dataset") or {}).get("data_mode") == "simulated"
                                                for r in results if r.ok and isinstance(r.data.get("dataset"), dict))
        mock = []
        if any(r.capability_id.startswith("mission.") and r.provenance.get("provider") == "standalone" for r in results):
            mock.append("mission status and astronaut position are mock values")
        if any(r.capability_id == "route.generate" and r.provenance.get("provider") == "standalone" and r.ok for r in results):
            mock.append("routes from the standalone route service (not the Marswalk route engine)")
        if simulated:
            mock.append("SIMULATED terrain (no DEM configured)")
        return {
            "response_id": f"rsp-{uuid.uuid4().hex[:10]}",
            "session_id": run.session.id,
            "message": text,
            "intent": intent,
            "status": status,
            "tool_calls": run.calls,
            "tool_results": [{**r.to_dict(), "data": _trim(r.data)} for r in results],
            "frontend_actions": run.turn.frontend_actions,
            "cards": build_cards(results, run.session),
            "evidence": run.turn.evidence,
            "limitations": run.turn.limitations,
            "errors": run.turn.errors,
            "pending_confirmations": [{k: c[k] for k in ("confirmation_id", "capability_id", "arguments", "summary")} for c in run.turn.confirmations],
            "suggestions": suggestions_for(run.session, intent.get("name", "")),
            "provider": {"mode": mode, "name": self.provider.name if self.provider else "offline-rules",
                         "model": getattr(self.provider, "model", None), "note": self.provider_note},
            "data_modes": modes,
            "simulated": simulated,
            "mock_components": mock,
            "timing_ms": int((time.perf_counter() - t0) * 1000),
        }
