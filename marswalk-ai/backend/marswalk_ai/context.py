"""Shared services and the per-turn execution context handed to capability handlers."""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import DATA_DIR, Settings
from .memory import SessionState, SessionStore
from .services.terrain import ground_distance_m


class MissionData:
    """Mission POIs and the Perseverance waypoint snapshot (see scripts/build_mock_data.py)."""

    def __init__(self, mission: dict | None = None, rover: dict | None = None):
        self.mission = mission or json.loads((DATA_DIR / "mission.json").read_text(encoding="utf-8"))
        self.rover = rover or json.loads((DATA_DIR / "rover_waypoints.json").read_text(encoding="utf-8"))
        self.source = "mock"   # becomes "marswalk" when the real backend supplies stops

    @property
    def pois(self) -> list[dict]:
        return self.mission["pois"]

    @property
    def bounds(self) -> list:
        return self.mission["bounds"]

    def get(self, poi_id: str) -> dict | None:
        return next((p for p in self.pois if p["id"] == poi_id), None)

    def in_bounds(self, lat: float, lon: float) -> bool:
        (s, w), (n, e) = self.bounds
        return s <= lat <= n and w <= lon <= e

    def resolve(self, text: str) -> list[dict]:
        """POIs whose id, name or alias occurs in `text`, best (longest) match first."""
        t = f" {text.lower()} "
        scored = []
        for p in self.pois:
            names = [p["id"], p["name"].lower(), *p.get("aliases", [])]
            best = max((len(n) for n in names if re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", t)), default=0)
            if best:
                scored.append((best, p))
        return [p for _, p in sorted(scored, key=lambda x: -x[0])]

    def current_position(self) -> dict:
        p = self.get(self.mission.get("current_position_poi", "habitat")) or self.pois[0]
        return {"poi_id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"],
                "source": self.mission.get("current_position_source", "")}

    def latest_rover(self) -> dict:
        w = self.rover["waypoints"][-1]
        return {**w, "source": self.rover["source"], "note": self.rover["note"]}

    def nearest_rover(self, lat: float, lon: float, k: int = 3) -> list[dict]:
        ws = sorted(self.rover["waypoints"], key=lambda w: ground_distance_m(lat, lon, w["lat"], w["lon"]))[:k]
        return [{**w, "distance_m": round(ground_distance_m(lat, lon, w["lat"], w["lon"]), 1)} for w in ws]


@dataclass
class Services:
    settings: Settings
    mission: MissionData
    store: SessionStore
    terrain: Any = None
    planner: Any = None
    knowledge: Any = None
    http: Any = None          # httpx.AsyncClient for external backends
    status: dict = field(default_factory=dict)


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


class TurnRecorder:
    """Collects everything that happens while answering one user message."""

    def __init__(self, session: SessionState, user_text: str = ""):
        self.session = session
        self.user_text = user_text
        self.results: list = []
        self.frontend_actions: list[dict] = []
        self.evidence: list[dict] = []
        self.limitations: list[str] = []
        self.errors: list[dict] = []
        self.confirmations: list[dict] = []
        self.known_coords: list[tuple[float, float]] = []
        self.numbers: set[float] = set()

    # ---- frontend actions (executed by the UI, confirmed by an ack)
    def add_action(self, action_type: str, capability_id: str, payload: dict, summary: str) -> dict:
        action = {"action_id": f"act-{uuid.uuid4().hex[:10]}", "type": action_type, "capability_id": capability_id,
                  "payload": payload, "summary": summary, "status": "pending", "requires_ack": True}
        self.frontend_actions.append(action)
        self.session.actions[action["action_id"]] = {"type": action_type, "status": "pending", "summary": summary,
                                                     "detail": None, "ts": time.time()}
        return action

    def request_confirmation(self, cap, args: dict) -> dict:
        what = f"“{args['label']}”" if isinstance(args.get("label"), str) else json.dumps(args, ensure_ascii=False)[:200]
        pending = {"confirmation_id": f"cnf-{uuid.uuid4().hex[:10]}", "capability_id": cap.id, "arguments": args,
                   "summary": f"{cap.name}: {what}", "created_at": time.time()}
        self.session.pending_confirmations[pending["confirmation_id"]] = pending
        self.confirmations.append(pending)
        return pending

    def add_evidence(self, items: list[dict]) -> None:
        for e in items or []:
            key = (e.get("title"), e.get("section") or e.get("relevant_text", "")[:40])
            if all((x.get("title"), x.get("section") or x.get("relevant_text", "")[:40]) != key for x in self.evidence):
                self.evidence.append(e)

    def add_limitations(self, items: list[str]) -> None:
        for s in items or []:
            if s and s not in self.limitations:
                self.limitations.append(s)

    # ---- grounding
    def absorb(self, data: Any) -> None:
        """Remember coordinates and numbers produced by tools (used for grounding checks)."""
        if isinstance(data, dict):
            if isinstance(data.get("lat"), (int, float)) and isinstance(data.get("lon"), (int, float)):
                self.known_coords.append((float(data["lat"]), float(data["lon"])))
            for v in data.values():
                self.absorb(v)
        elif isinstance(data, list):
            if len(data) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in data):
                self.known_coords.append((float(data[0]), float(data[1])))
            for v in data[:400]:
                self.absorb(v)
        elif isinstance(data, (int, float)) and not isinstance(data, bool):
            self.numbers.add(round(float(data), 6))

    def coordinate_is_grounded(self, lat: float, lon: float, mission: MissionData, tol: float = 1.5e-4) -> bool:
        cands = list(self.known_coords)
        cands += [(p["lat"], p["lon"]) for p in mission.pois]
        lr = mission.latest_rover()
        cands.append((lr["lat"], lr["lon"]))
        for r in self.session.routes.values():
            g = r.get("geometry") or []
            cands += [tuple(x) for x in g]
            sp = r.get("steepest_point")
            if sp:
                cands.append((sp["lat"], sp["lon"]))
            cands += [(s["lat"], s["lon"]) for s in r.get("steep_sections", [])]
        cc = self.session.client_context
        for key in ("selected", "map"):
            v = cc.get(key) or {}
            if key == "map":
                v = v.get("center") or {}
            if isinstance(v.get("lat"), (int, float)) and isinstance(v.get("lon"), (int, float)):
                cands.append((v["lat"], v["lon"]))
        typed = {float(x) for x in _NUM.findall(self.user_text)}
        if any(abs(lat - t) < 1e-9 for t in typed) and any(abs(lon - t) < 1e-9 for t in typed):
            return True
        return any(abs(lat - a) <= tol and abs(lon - b) <= tol for a, b in cands)


@dataclass
class ExecContext:
    session: SessionState
    services: Services
    turn: TurnRecorder
    origin: str = "rules"
