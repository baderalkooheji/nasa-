"""Provider-neutral types.

Transcript items exchanged with providers:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str | None, "tool_calls": [ToolCallRequest...], "raw": <provider-specific>}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}   # JSON string
Each provider converts this to its own wire format.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """The LLM call failed (network, auth, quota, malformed output). Never contains the API key."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: Any            # dict when well-formed; anything else is rejected by schema validation


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    raw: Any = None
    usage: dict = field(default_factory=dict)


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""
    native_tools: bool = True

    @abstractmethod
    async def complete(self, system: str, transcript: list[dict], tools: list[ToolSpec]) -> LLMResponse: ...

    def describe(self) -> dict:
        return {"name": self.name, "model": self.model, "native_tools": self.native_tools}


def parse_arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw or "{}")
            return v if isinstance(v, dict) else {"__invalid__": raw}
        except json.JSONDecodeError:
            return {"__invalid_json__": raw[:200]}
    return {"__invalid__": str(raw)[:200]}


def extract_json_object(text: str) -> dict | None:
    """First balanced {...} object in a model reply (for the JSON tool protocol)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    start = text.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def safe_error(resp) -> str:
    """Short error text from an HTTP response without echoing request headers."""
    try:
        j = resp.json()
        msg = j.get("error", {}).get("message") if isinstance(j.get("error"), dict) else j.get("error") or j.get("message")
    except Exception:  # noqa: BLE001
        msg = resp.text[:200]
    return f"HTTP {resp.status_code}: {str(msg)[:300]}"
