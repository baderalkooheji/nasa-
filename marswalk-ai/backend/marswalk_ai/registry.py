"""Capability Registry: the allowlist of operations the assistant may execute.

Only registered capabilities can run. Every call is validated against the
capability's input schema before its handler is invoked. Adapters (mock,
Science Engine, real Marswalk backend, frontend) register what they actually
support; a higher-priority provider replaces a lower-priority one for the same id.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from .schema import apply_defaults, validate

Side = Literal["backend", "frontend"]
Permission = Literal["read", "write", "confirm"]
Availability = Literal["available", "degraded", "unavailable"]


class CapabilityError(Exception):
    """Expected failure inside a handler (bad target, out of coverage, upstream down...)."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class CapResult:
    data: dict
    status: str = "success"                  # success | pending_ack
    provenance: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


Handler = Callable[[dict, Any], Awaitable[CapResult]]


@dataclass
class Capability:
    id: str
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    handler: Handler | None
    side: Side = "backend"
    category: str = "general"                 # route | terrain | science | mission | map | navigation | knowledge
    changes_state: bool = False
    permission: Permission = "read"
    availability: Availability = "available"
    availability_reason: str = ""
    provider: str = "mock"                    # which adapter implements it
    data_mode: str = "n/a"                    # real | simulated | mixed | reference | n/a
    priority: int = 10
    timeout_s: float = 30.0
    error_policy: str = "Report the failure to the user; never substitute invented values."
    notes: str = ""
    schema_builder: Callable[[Any], dict] | None = None   # per-session schema (e.g. frontend enums)

    @property
    def llm_name(self) -> str:
        # OpenAI / Anthropic tool names may not contain dots
        return self.id.replace(".", "__")

    def schema_for(self, session: Any) -> dict:
        return self.schema_builder(session) if self.schema_builder and session is not None else self.input_schema

    def describe(self, session: Any = None) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "side": self.side, "category": self.category,
            "changes_state": self.changes_state, "permission": self.permission,
            "availability": self.availability, "availability_reason": self.availability_reason,
            "provider": self.provider, "data_mode": self.data_mode, "priority": self.priority,
            "timeout_s": self.timeout_s, "error_policy": self.error_policy, "notes": self.notes,
            "input_schema": self.schema_for(session), "output_schema": self.output_schema,
        }


@dataclass
class ToolResult:
    call_id: str
    capability_id: str
    arguments: dict
    status: str                     # success | pending_ack | pending_confirmation | error | rejected | unavailable
    ok: bool
    data: dict = field(default_factory=dict)
    error: dict | None = None
    provenance: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0
    origin: str = "rules"

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id, "capability_id": self.capability_id, "arguments": self.arguments,
            "status": self.status, "ok": self.ok, "data": self.data, "error": self.error,
            "provenance": self.provenance, "warnings": self.warnings,
            "duration_ms": self.duration_ms, "origin": self.origin,
        }


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    # ------------------------------------------------------------ registration
    def register(self, cap: Capability) -> bool:
        """Register or replace. A lower-priority provider never replaces a higher one."""
        current = self._caps.get(cap.id)
        if current and current.priority > cap.priority and current.availability != "unavailable":
            return False
        self._caps[cap.id] = cap
        return True

    def unregister(self, cap_id: str, provider: str | None = None) -> bool:
        cap = self._caps.get(cap_id)
        if not cap or (provider and cap.provider != provider):
            return False
        del self._caps[cap_id]
        return True

    def get(self, cap_id: str) -> Capability | None:
        return self._caps.get(cap_id)

    def by_llm_name(self, name: str) -> Capability | None:
        return self._caps.get(name.replace("__", ".")) or next((c for c in self._caps.values() if c.llm_name == name), None)

    def all(self) -> list[Capability]:
        return sorted(self._caps.values(), key=lambda c: (c.side, c.category, c.id))

    def usable(self, session: Any) -> list[Capability]:
        """Capabilities the assistant may offer to the LLM for this session."""
        out = []
        for c in self.all():
            if c.availability == "unavailable":
                continue
            if c.side == "frontend" and not _frontend_supports(session, c.id):
                continue
            out.append(c)
        return out

    # ------------------------------------------------------------ execution
    async def execute(self, cap_id: str, arguments: dict | None, ctx: Any, origin: str = "rules",
                      call_id: str | None = None) -> ToolResult:
        call_id = call_id or f"call-{uuid.uuid4().hex[:10]}"
        args = arguments if isinstance(arguments, dict) else {}
        t0 = time.perf_counter()

        def done(status: str, ok: bool, **kw) -> ToolResult:
            return ToolResult(call_id=call_id, capability_id=cap_id, arguments=args, status=status, ok=ok,
                              duration_ms=int((time.perf_counter() - t0) * 1000), origin=origin, **kw)

        cap = self.get(cap_id)
        if cap is None:
            return done("rejected", False, error={"code": "unknown_capability",
                                                  "message": f"'{cap_id}' is not a registered capability."})
        if not isinstance(arguments, dict):
            return done("rejected", False, error={"code": "invalid_arguments", "message": "Arguments must be a JSON object."})
        if cap.availability == "unavailable":
            return done("unavailable", False, error={"code": "capability_unavailable",
                                                     "message": cap.availability_reason or f"{cap.name} is not available."},
                        provenance={"provider": cap.provider})
        session = getattr(ctx, "session", None)
        if cap.side == "frontend" and not _frontend_supports(session, cap.id):
            return done("unavailable", False, error={"code": "frontend_not_connected",
                                                     "message": "No connected frontend supports this action."})
        schema = cap.schema_for(session)
        args = apply_defaults(arguments, schema)
        errors = validate(args, schema)
        if errors:
            return done("rejected", False, error={"code": "invalid_arguments", "message": "; ".join(errors[:6])})

        if cap.permission == "confirm" and origin != "confirm":
            pending = ctx.turn.request_confirmation(cap, args)
            return done("pending_confirmation", True, data={"confirmation_id": pending["confirmation_id"],
                                                            "note": "Waiting for the user's confirmation. Not executed yet."},
                        provenance={"provider": cap.provider})

        try:
            res: CapResult = await asyncio.wait_for(cap.handler(args, ctx), timeout=cap.timeout_s)
        except CapabilityError as e:
            return done("error", False, error={"code": e.code, "message": e.message, **({"details": e.details} if e.details else {})},
                        provenance={"provider": cap.provider})
        except asyncio.TimeoutError:
            return done("error", False, error={"code": "timeout", "message": f"{cap.name} did not finish within {cap.timeout_s:.0f} s."},
                        provenance={"provider": cap.provider})
        except Exception as e:  # noqa: BLE001 - must never crash the conversation
            return done("error", False, error={"code": "internal_error", "message": f"{type(e).__name__}: {e}"},
                        provenance={"provider": cap.provider})

        # output schemas are a soft contract: required keys and types are checked, extra keys are allowed
        warnings = [f"output: {w}" for w in validate(res.data, {**cap.output_schema, "additionalProperties": True})] if cap.output_schema else []
        prov = {"provider": cap.provider, "data_mode": cap.data_mode, **res.provenance}
        if hasattr(ctx, "turn"):
            ctx.turn.add_evidence(res.evidence)
            ctx.turn.add_limitations(res.limitations)
        return done(res.status, True, data=res.data, provenance=prov, warnings=warnings)


def _frontend_supports(session: Any, cap_id: str) -> bool:
    if session is None:
        return False
    return cap_id in set(getattr(session, "frontend", {}).get("capabilities", []))
