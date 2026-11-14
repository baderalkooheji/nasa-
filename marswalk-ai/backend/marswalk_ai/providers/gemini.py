"""Google Gemini (generateContent REST API) provider with function calling."""
from __future__ import annotations

import json
import uuid

import httpx

from .base import LLMProvider, LLMResponse, ProviderError, ToolCallRequest, ToolSpec, parse_arguments, safe_error

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_KEEP = {"type", "format", "description", "nullable", "enum", "properties", "required", "items", "minimum", "maximum", "minItems", "maxItems"}


def sanitize_schema(schema: dict) -> dict:
    """Reduce JSON Schema to the OpenAPI subset Gemini accepts."""
    out = {}
    for k, v in schema.items():
        if k not in _KEEP:
            continue
        if k == "type" and isinstance(v, list):
            non_null = [t for t in v if t != "null"]
            out["type"] = non_null[0] if non_null else "string"
            if "null" in v:
                out["nullable"] = True
        elif k == "properties":
            out[k] = {pk: sanitize_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = sanitize_schema(v)
        elif k == "enum":
            out[k] = [str(x) for x in v]
        else:
            out[k] = v
    return out


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, http: httpx.AsyncClient, timeout: float, temperature: float):
        self.api_key, self.model, self.http, self.timeout, self.temperature = api_key, model, http, timeout, temperature

    @staticmethod
    def _contents(transcript: list[dict]) -> list[dict]:
        out: list[dict] = []
        for item in transcript:
            if item["role"] == "user":
                out.append({"role": "user", "parts": [{"text": item["content"]}]})
            elif item["role"] == "assistant":
                if item.get("raw"):
                    out.append(item["raw"])                  # keeps thought signatures intact
                else:
                    parts = ([{"text": item["content"]}] if item.get("content") else []) + [
                        {"functionCall": {"name": c.name, "args": c.arguments if isinstance(c.arguments, dict) else {}}}
                        for c in item.get("tool_calls") or []]
                    out.append({"role": "model", "parts": parts})
            elif item["role"] == "tool":
                try:
                    resp = json.loads(item["content"])
                except json.JSONDecodeError:
                    resp = {"text": item["content"]}
                part = {"functionResponse": {"name": item["name"], "response": {"result": resp}}}
                if out and out[-1]["role"] == "user" and any("functionResponse" in p for p in out[-1]["parts"]):
                    out[-1]["parts"].append(part)
                else:
                    out.append({"role": "user", "parts": [part]})
        return out

    async def complete(self, system: str, transcript: list[dict], tools: list[ToolSpec]) -> LLMResponse:
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": self._contents(transcript),
                "generationConfig": {"temperature": self.temperature}}
        if tools:
            decls = []
            for t in tools:
                d = {"name": t.name, "description": t.description}
                params = sanitize_schema(t.parameters)
                if params.get("properties"):
                    d["parameters"] = params
                decls.append(d)
            body["tools"] = [{"functionDeclarations": decls}]
        try:
            r = await self.http.post(API.format(model=self.model), json=body, headers={"x-goog-api-key": self.api_key},
                                     timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"gemini request failed: {type(e).__name__}") from None
        if r.status_code >= 400:
            raise ProviderError(f"gemini {safe_error(r)}")
        data = r.json()
        try:
            content = data["candidates"][0]["content"]
        except (KeyError, IndexError):
            reason = (data.get("promptFeedback") or {}).get("blockReason") or "no candidates"
            raise ProviderError(f"gemini returned no content ({reason})") from None
        parts = content.get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        calls = [ToolCallRequest(id=f"gm-{uuid.uuid4().hex[:8]}", name=p["functionCall"]["name"],
                                 arguments=parse_arguments(p["functionCall"].get("args", {})))
                 for p in parts if "functionCall" in p]
        return LLMResponse(text=text, tool_calls=calls, raw={"role": "model", "parts": parts}, usage=data.get("usageMetadata") or {})
