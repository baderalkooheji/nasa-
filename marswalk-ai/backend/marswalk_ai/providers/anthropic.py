"""Anthropic Messages API provider (native tool use)."""
from __future__ import annotations

import httpx

from .base import LLMProvider, LLMResponse, ProviderError, ToolCallRequest, ToolSpec, parse_arguments, safe_error

API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, http: httpx.AsyncClient, timeout: float, temperature: float, max_tokens: int = 2048):
        self.api_key, self.model, self.http = api_key, model, http
        self.timeout, self.temperature, self.max_tokens = timeout, temperature, max_tokens

    @staticmethod
    def _messages(transcript: list[dict]) -> list[dict]:
        msgs: list[dict] = []
        for item in transcript:
            if item["role"] == "user":
                msgs.append({"role": "user", "content": item["content"]})
            elif item["role"] == "assistant":
                if item.get("raw"):
                    content = item["raw"]
                else:
                    content = ([{"type": "text", "text": item["content"]}] if item.get("content") else []) + [
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments if isinstance(c.arguments, dict) else {}}
                        for c in item.get("tool_calls") or []]
                msgs.append({"role": "assistant", "content": content})
            elif item["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": item["tool_call_id"], "content": item["content"]}
                if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
                    msgs[-1]["content"].append(block)          # all results of one turn go in one user message
                else:
                    msgs.append({"role": "user", "content": [block]})
        return msgs

    async def complete(self, system: str, transcript: list[dict], tools: list[ToolSpec]) -> LLMResponse:
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": system, "temperature": self.temperature,
                "messages": self._messages(transcript)}
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            r = await self.http.post(API_URL, json=body, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"anthropic request failed: {type(e).__name__}") from None
        if r.status_code >= 400:
            raise ProviderError(f"anthropic {safe_error(r)}")
        data = r.json()
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [ToolCallRequest(id=b["id"], name=b["name"], arguments=parse_arguments(b.get("input")))
                 for b in blocks if b.get("type") == "tool_use"]
        return LLMResponse(text=text, tool_calls=calls, raw=blocks, usage=data.get("usage") or {})
