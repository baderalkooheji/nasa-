"""
Marswalk AI Assistant - LLM Client
Wrapper for the local Ollama server with native tool calling.

Qwen3 supports Ollama's native tool-calling API, which is far more reliable than asking the model to print JSON.
The text-JSON parser from the first version is kept as a fallback for models without native tool support.
Qwen3's "thinking" mode is off by default (settings.THINK): on a CPU it multiplies the answer time.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

import ollama
from pydantic import BaseModel

from config import settings

logger = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>", re.S)


class ToolCall(BaseModel):
    name: str
    arguments: Dict[str, Any]
    id: Optional[str] = None


class LLMResponse(BaseModel):
    content: str
    tool_calls: List[ToolCall] = []
    model: str
    usage: Optional[Dict[str, int]] = None
    raw_message: Optional[Dict[str, Any]] = None     # assistant message to append to the history before tool results


class LLMUnavailable(Exception):
    """Ollama is not running, or the model is not installed."""


class MarswalkLLM:
    def __init__(self, model: str = None, host: str = None):
        self.model = model or settings.DEFAULT_MODEL
        self.host = host or settings.OLLAMA_HOST
        self.client = ollama.AsyncClient(host=self.host, timeout=settings.MODEL_TIMEOUT)
        self._available_models: List[str] = []

    async def list_models(self) -> List[str]:
        try:
            response = await self.client.list()
            self._available_models = [m.model for m in response.models]
        except Exception as e:
            logger.warning("Ollama not reachable at %s: %s", self.host, e)
            self._available_models = []
        return self._available_models

    async def pull_model(self, model: str) -> bool:
        try:
            await self.client.pull(model)
            await self.list_models()
            return True
        except Exception as e:
            logger.error("Failed to pull model %s: %s", model, e)
            return False

    async def switch_model(self, model: str) -> bool:
        if model in await self.list_models():
            self.model = model
            return True
        return False

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
                   temperature: float = 0.3, max_tokens: int = 700) -> LLMResponse:
        """One model call. `tools` are Ollama function definitions; the caller executes any tool calls returned."""
        kwargs: Dict[str, Any] = dict(
            model=self.model, messages=messages, keep_alive=settings.KEEP_ALIVE,
            options={"temperature": temperature, "num_predict": max_tokens, "num_ctx": settings.NUM_CTX},
        )
        if tools:
            kwargs["tools"] = tools
        try:
            try:
                response = await self.client.chat(think=settings.THINK, **kwargs)
            except TypeError:                                   # older ollama client without the `think` argument
                response = await self.client.chat(**kwargs)
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise LLMUnavailable(f"Model '{self.model}' is not installed in Ollama. Run: ollama pull {self.model}") from e
            raise LLMUnavailable(f"Ollama error: {e.error}") from e
        except Exception as e:                                  # connection refused, timeout
            raise LLMUnavailable(f"Cannot reach Ollama at {self.host} ({e.__class__.__name__}: {e}). Is Ollama running?") from e
        return self._parse_response(response)

    def _parse_response(self, response) -> LLMResponse:
        msg = response.message if hasattr(response, "message") else response.get("message", {})
        get = (lambda k, d=None: getattr(msg, k, d)) if not isinstance(msg, dict) else msg.get
        content = _THINK.sub("", get("content") or "").strip()
        tool_calls: List[ToolCall] = []
        for i, tc in enumerate(get("tool_calls") or []):
            fn = tc.function if hasattr(tc, "function") else tc.get("function", {})
            name = fn.name if hasattr(fn, "name") else fn.get("name", "")
            args = fn.arguments if hasattr(fn, "arguments") else fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(name=name, arguments=dict(args or {}), id=f"call_{i}"))
        if not tool_calls:                                      # fallback: model printed the tool call as JSON text
            tool_calls = self._extract_tool_calls(content)
            if tool_calls:
                content = self._remove_tool_calls(content)
        raw = {"role": "assistant", "content": content}
        if tool_calls:
            raw["tool_calls"] = [{"function": {"name": t.name, "arguments": t.arguments}} for t in tool_calls]
        model = getattr(response, "model", None) or (response.get("model") if isinstance(response, dict) else None) or self.model
        pe = getattr(response, "prompt_eval_count", None) if not isinstance(response, dict) else response.get("prompt_eval_count")
        ec = getattr(response, "eval_count", None) if not isinstance(response, dict) else response.get("eval_count")
        return LLMResponse(content=content, tool_calls=tool_calls, model=model,
                           usage={"prompt_tokens": pe or 0, "completion_tokens": ec or 0}, raw_message=raw)

    # ------------------------------------------------------------------ text-JSON fallback (from the first version)
    def _extract_tool_calls(self, content: str) -> List[ToolCall]:
        calls: List[ToolCall] = []
        candidates = re.findall(r'\{\s*"tool_calls"\s*:\s*\[.*?\]\s*\}', content, re.DOTALL) or [content.strip()]
        for c in candidates:
            try:
                data = json.loads(self._strip_json_comments(c))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                for tc in data.get("tool_calls", []):
                    if isinstance(tc, dict) and tc.get("name"):
                        calls.append(ToolCall(name=tc["name"], arguments=tc.get("arguments") or {}, id=tc.get("id")))
        return calls

    @staticmethod
    def _strip_json_comments(text: str) -> str:
        text = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
        return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    @staticmethod
    def _remove_tool_calls(content: str) -> str:
        return re.sub(r'\{\s*"tool_calls"\s*:\s*\[.*?\]\s*\}', "", content, flags=re.DOTALL).strip()


llm_client = MarswalkLLM()
