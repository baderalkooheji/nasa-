"""Choose the LLM provider from settings. Returns None for the offline rule-based mode."""
from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import LLMProvider
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider


def create_provider(settings, http) -> tuple[LLMProvider | None, str]:
    """(provider or None, explanation)."""
    choice = settings.llm_provider
    t, temp = settings.llm_timeout_s, settings.llm_temperature

    def gemini():
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, http, t, temp) if settings.gemini_api_key else None

    def anthropic():
        return AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model, http, t, temp) if settings.anthropic_api_key else None

    def openai():
        if not settings.openai_model:
            return None
        if not settings.openai_api_key and "api.openai.com" in settings.openai_base_url:
            return None
        return OpenAICompatProvider(settings.openai_api_key, settings.openai_model, settings.openai_base_url, http, t, temp,
                                    settings.openai_tool_mode)

    if choice == "offline":
        return None, "LLM_PROVIDER=offline: rule-based intent recognition and deterministic responses."
    makers = {"gemini": gemini, "anthropic": anthropic, "openai": openai}
    if choice in makers:
        p = makers[choice]()
        return (p, f"{choice} ({p.model})") if p else (None, f"LLM_PROVIDER={choice} but its key/model is not configured; using offline mode.")
    for name in ("gemini", "anthropic", "openai"):            # auto
        p = makers[name]()
        if p:
            return p, f"auto-selected {name} ({p.model})"
    return None, "No LLM API key configured; using offline rule-based mode."
