"""Shared fixtures. Tests run on SYNTHETIC terrain by default so they work without the DEMs;
tests that need the real rasters are skipped when they are absent."""
import asyncio
import os
from pathlib import Path

import httpx
import pytest

from marswalk_ai.config import Settings
from marswalk_ai.main import build_app_state

FIXTURE_DOCS = str(Path(__file__).parent / "fixtures" / "docs")

FULL_FRONTEND = {
    "client": "pytest",
    "capabilities": ["map.display_route", "map.highlight_location", "map.zoom_to_location", "map.change_layer",
                     "map.clear_route", "navigation.open_section"],
    "sections": [{"id": "map", "label": "Main map", "keywords": ["main map"]},
                 {"id": "dashboard", "label": "Mission dashboard", "keywords": ["dashboard", "mission dashboard"]},
                 {"id": "history", "label": "Mission history", "keywords": ["mission history", "history"]}],
    "layers": [{"id": "mola", "label": "MOLA shaded elevation", "keywords": ["terrain map", "elevation map"]},
               {"id": "hirise", "label": "HiRISE orthomosaic", "keywords": ["hirise"]}],
}


def run(coro):
    return asyncio.run(coro)


def make_settings(**over) -> Settings:
    s = Settings()
    s.llm_provider = "offline"
    s.session_db = ":memory:"
    s.ctx_dem_path = None
    s.hirise_dtm_path = None
    s.allow_synthetic_terrain = True
    s.knowledge_dir = FIXTURE_DOCS
    for k, v in over.items():
        setattr(s, k, v)
    return s


class Env:
    """Built services + orchestrator, plus helpers. Use as a context manager inside one asyncio.run()."""

    def __init__(self, settings=None, http=None, provider=None, provider_note=None):
        self.settings = settings or make_settings()
        self.http = http
        self.provider = provider
        self.provider_note = provider_note

    async def __aenter__(self):
        self.services, self.registry, self.orch = await build_app_state(
            self.settings, self.http or httpx.AsyncClient(), self.provider, self.provider_note or ("scripted" if self.provider else None))
        return self

    async def __aexit__(self, *exc):
        await self.services.http.aclose()

    def session(self, frontend=None):
        return self.services.store.create(FULL_FRONTEND if frontend is None else frontend)

    async def ask(self, session, text, client_context=None):
        return await self.orch.chat(session, text, client_context)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL", "SCIENCE_ENGINE_URL", "MARSWALK_API_URL"):
        monkeypatch.delenv(k, raising=False)
    yield


REAL_DEM = os.path.exists(Path(__file__).resolve().parents[2] / "data" / "raw" / "ctx" / "M20_JezeroCrater_CTXDEM_20m.tif")
