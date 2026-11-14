"""OpenAI-compatible Chat Completions provider (OpenAI, Azure-style gateways, Ollama, vLLM, LM Studio, OpenRouter...).

tool_mode="native": uses the `tools` / `tool_calls` fields.
tool_mode="json":   for models without tool calling - the tool catalogue goes in the
                    system prompt and the model must answer with a JSON object
                    {"tool_calls": [{"name": ..., "arguments": {...}}]} or {"final": "..."}.
"""
from __future__ import annotations

import json
import uuid

import httpx

from .base import LLMProvider, LLMResponse, ProviderError, ToolCallRequest, ToolSpec, extract_json_object, parse_arguments, safe_error

JSON_PROTOCOL = """
You cannot call functions natively. To use a tool, reply with ONLY this JSON (no prose):
{"tool_calls": [{"name": "<tool name>", "arguments": {...}}]}
When you have everything you need, reply with ONLY:
{"final": "<your answer to the user>"}
Available tools (name: description; JSON schema of arguments):
"""


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str | None, model: str, base_url: str, http: httpx.AsyncClient, timeout: float,
                 temperature: float, tool_mode: str = "native"):
        self.api_key, self.model, self.base_url = api_key, model, base_url
        self.http, self.timeout, self.temperature = http, timeout, temperature
        self.native_tools = tool_mode != "json"

    def _messages(self, system: str, transcript: list[dict], tools: list[ToolSpec]) -> list[dict]:
        if not self.native_tools:
            catalogue = "\n".join(f"- {t.name}: {t.description}\n  {json.dumps(t.parameters)}" for t in tools)
            system = system + "\n" + JSON_PROTOCOL + catalogue
        msgs = [{"role": "system", "content": system}]
        for item in transcript:
            if item["role"] == "user":
                msgs.append({"role": "user", "content": item["content"]})
            elif item["role"] == "assistant":
                if self.native_tools:
                    m = {"role": "assistant", "content": item.get("content") or None}
                    if item.get("tool_calls"):
                        m["tool_calls"] = [{"id": c.id, "type": "function",
                                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in item["tool_calls"]]
                    msgs.append(m)
                else:
                    msgs.append({"role": "assistant", "content": item.get("raw") or item.get("content") or ""})
            elif item["role"] == "tool":
                if self.native_tools:
                    msgs.append({"role": "tool", "tool_call_id": item["tool_call_id"], "content": item["content"]})
                else:
                    msgs.append({"role": "user", "content": f"TOOL RESULT {item['name']}: {item['content']}"})
        return msgs

    async def complete(self, system: str, transcript: list[dict], tools: list[ToolSpec]) -> LLMResponse:
        body = {"model": self.model, "messages": self._messages(system, transcript, tools), "temperature": self.temperature}
        if self.native_tools and tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                             for t in tools]
            body["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            r = await self.http.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} request failed: {type(e).__name__}") from None
        if r.status_code >= 400:
            raise ProviderError(f"{self.name} {safe_error(r)}")
        data = r.json()
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError):
            raise ProviderError(f"{self.name}: unexpected response shape") from None
        usage = data.get("usage") or {}
        if self.native_tools:
            calls = [ToolCallRequest(id=c.get("id") or f"oa-{uuid.uuid4().hex[:8]}", name=c["function"]["name"],
                                     arguments=parse_arguments(c["function"].get("arguments")))
                     for c in msg.get("tool_calls") or []]
            return LLMResponse(text=msg.get("content") or "", tool_calls=calls, raw=msg, usage=usage)
        content = msg.get("content") or ""
        obj = extract_json_object(content)
        if obj is None:
            return LLMResponse(text=content, raw=content, usage=usage)
        if obj.get("tool_calls"):
            calls = [ToolCallRequest(id=f"js-{uuid.uuid4().hex[:8]}", name=str(c.get("name")), arguments=parse_arguments(c.get("arguments", {})))
                     for c in obj["tool_calls"] if isinstance(c, dict)]
            return LLMResponse(tool_calls=calls, raw=content, usage=usage)
        return LLMResponse(text=str(obj.get("final", content)), raw=content, usage=usage)
