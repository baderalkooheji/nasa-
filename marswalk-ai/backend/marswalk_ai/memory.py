"""Conversation memory and mission-aware session state.

Two things are kept separately on purpose:
  * conversation  : recent messages and the current topic
  * mission state : position, destination, routes, analyses, preferences,
                    frontend actions and their outcomes
Only a compact summary of the mission state plus the last few messages is
sent to the LLM; full tool results stay here.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_MESSAGES = 40
MAX_ANALYSES = 30


@dataclass
class SessionState:
    id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[dict] = field(default_factory=list)
    frontend: dict = field(default_factory=dict)          # capabilities, sections, layers advertised by the UI
    client_context: dict = field(default_factory=dict)    # latest UI state sent with a chat request
    routes: dict[str, dict] = field(default_factory=dict)
    route_sets: list[dict] = field(default_factory=list)
    active_route_id: str | None = None
    previous_route_id: str | None = None
    destination: dict | None = None
    preferences: dict = field(default_factory=dict)
    analyses: list[dict] = field(default_factory=list)
    actions: dict[str, dict] = field(default_factory=dict)
    pending_confirmations: dict[str, dict] = field(default_factory=dict)
    topic: str | None = None
    counters: dict = field(default_factory=lambda: {"route": 0, "analysis": 0})

    # ---------------------------------------------------------------- conversation
    def add_message(self, role: str, text: str, **extra) -> None:
        self.messages.append({"role": role, "text": text, "ts": time.time(), **extra})
        del self.messages[:-MAX_MESSAGES]

    def recent_messages(self, n: int = 8) -> list[dict]:
        return self.messages[-n:]

    # ---------------------------------------------------------------- routes
    def next_route_id(self) -> str:
        self.counters["route"] = self.counters.get("route", 0) + 1
        return f"R{self.counters['route']}"

    def add_route_set(self, routes: list[dict], recommended_id: str | None, request: dict) -> dict:
        for r in routes:
            self.routes[r["route_id"]] = r
        rs = {"set_id": f"S{len(self.route_sets) + 1}", "route_ids": [r["route_id"] for r in routes],
              "recommended_id": recommended_id, "request": request, "created_at": time.time()}
        self.route_sets.append(rs)
        self.set_active_route(recommended_id or routes[0]["route_id"])
        return rs

    def set_active_route(self, route_id: str | None) -> None:
        if route_id and route_id != self.active_route_id:
            if self.active_route_id:
                self.previous_route_id = self.active_route_id
            self.active_route_id = route_id

    def latest_set(self) -> dict | None:
        return self.route_sets[-1] if self.route_sets else None

    # ---------------------------------------------------------------- analyses
    def add_analysis(self, kind: str, summary: str, key_values: dict, provider: str, data_mode: str) -> str:
        self.counters["analysis"] = self.counters.get("analysis", 0) + 1
        aid = f"A{self.counters['analysis']}"
        self.analyses.append({"analysis_id": aid, "type": kind, "summary": summary, "key_values": key_values,
                              "provider": provider, "data_mode": data_mode, "ts": time.time()})
        del self.analyses[:-MAX_ANALYSES]
        return aid

    # ---------------------------------------------------------------- LLM summary
    def mission_summary(self, mission: dict) -> dict:
        """Compact, LLM-facing view of the mission state (no full geometries)."""
        def route_brief(r: dict) -> dict:
            m = r.get("metrics", {})
            return {"route_id": r["route_id"], "label": r.get("label"), "objective": r.get("objective"),
                    "to": r.get("destination", {}).get("name"), "distance_m": m.get("distance_m"),
                    "max_slope_deg": m.get("max_slope_deg"), "avg_slope_deg": m.get("avg_slope_deg"),
                    "walk_time_min": m.get("walk_time_out_min"), "weighted_cost": m.get("weighted_cost"),
                    "within_constraints": m.get("within_constraints"), "recommended": r.get("recommended", False),
                    "data_mode": r.get("provenance", {}).get("data_mode")}
        latest = self.latest_set()
        return {
            "mission": {"name": mission.get("name"), "status": mission.get("status"),
                        "current_position": mission.get("current_position")},
            "destination": self.destination,
            "active_route_id": self.active_route_id,
            "previous_route_id": self.previous_route_id,
            "latest_route_set": latest and {"set_id": latest["set_id"], "route_ids": latest["route_ids"],
                                            "recommended_id": latest["recommended_id"]},
            "routes": [route_brief(r) for r in list(self.routes.values())[-8:]],
            "recent_analyses": [{k: a[k] for k in ("analysis_id", "type", "summary", "data_mode")} for a in self.analyses[-6:]],
            "preferences": self.preferences,
            "ui": {k: self.client_context.get(k) for k in ("section", "visible_layers", "selected", "map") if k in self.client_context},
            "recent_frontend_actions": [{"action_id": k, "type": v["type"], "status": v["status"], "detail": v.get("detail")}
                                        for k, v in list(self.actions.items())[-6:]],
            "topic": self.topic,
        }

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, s: str) -> "SessionState":
        return cls(**json.loads(s))


class SessionStore:
    """In-memory sessions with optional SQLite persistence (stdlib only)."""

    def __init__(self, db_path: str | None = None):
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._db = None
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(db_path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, session_id TEXT, data TEXT NOT NULL, created REAL)")
            self._db.commit()
        self._records_mem: list[dict] = []

    def create(self, frontend: dict | None = None) -> SessionState:
        s = SessionState(id=f"ses-{uuid.uuid4().hex[:12]}", frontend=frontend or {})
        self.save(s)
        return s

    def get(self, session_id: str) -> SessionState | None:
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
        if self._db:
            row = self._db.execute("SELECT data FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row:
                s = SessionState.from_json(row[0])
                with self._lock:
                    self._sessions[session_id] = s
                return s
        return None

    def get_or_create(self, session_id: str | None, frontend: dict | None = None) -> SessionState:
        s = self.get(session_id) if session_id else None
        if s is None:
            s = self.create(frontend)
            if session_id:   # keep a caller-chosen id (used by the legacy /api/chat shim)
                with self._lock:
                    self._sessions.pop(s.id, None)
                s.id = session_id
                self.save(s)
        return s

    def save(self, s: SessionState) -> None:
        s.updated_at = time.time()
        with self._lock:
            self._sessions[s.id] = s
        if self._db:
            self._db.execute("INSERT OR REPLACE INTO sessions (id, data, updated) VALUES (?,?,?)", (s.id, s.to_json(), s.updated_at))
            self._db.commit()

    # ---- mission records (mock persistence for mission.save_record / mission.get_history)
    def add_record(self, session_id: str, record: dict) -> dict:
        rec = {"record_id": f"rec-{uuid.uuid4().hex[:8]}", "session_id": session_id, "created_at": time.time(), **record}
        if self._db:
            self._db.execute("INSERT INTO records (id, session_id, data, created) VALUES (?,?,?,?)",
                             (rec["record_id"], session_id, json.dumps(rec), rec["created_at"]))
            self._db.commit()
        else:
            self._records_mem.append(rec)
        return rec

    def list_records(self, query: str = "", limit: int = 20) -> list[dict]:
        if self._db:
            rows = [json.loads(r[0]) for r in self._db.execute("SELECT data FROM records ORDER BY created DESC LIMIT 200")]
        else:
            rows = list(reversed(self._records_mem))
        q = query.lower().strip()
        if q:
            rows = [r for r in rows if q in json.dumps(r).lower()]
        return rows[:limit]
