"""Configuration. Reads environment variables (and an optional .env file in marswalk-ai/)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
AI_ROOT = PACKAGE_DIR.parent.parent              # marswalk-ai/
DATA_DIR = PACKAGE_DIR / "data"
FRONTEND_DIR = AI_ROOT / "frontend"
VAR_DIR = AI_ROOT / "var"


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines; never overrides variables already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v is not None else default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")


def _path(v: str | None) -> str | None:
    """'none' / 'off' explicitly disables a path (e.g. to force synthetic terrain in tests)."""
    return None if v is None or v.strip().lower() in ("none", "off", "false") else v


def _default_path(*parts: str) -> str | None:
    """Default dataset paths point at the original Marswalk checkout when marswalk-ai/ sits inside it."""
    p = AI_ROOT.parent.joinpath(*parts)
    return str(p) if p.exists() else None


@dataclass
class Settings:
    # ---- LLM provider
    llm_provider: str = "auto"            # auto | offline | gemini | openai | anthropic
    llm_timeout_s: float = 45.0
    llm_temperature: float = 0.2
    max_tool_rounds: int = 6
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str | None = None
    openai_tool_mode: str = "native"      # native | json  (json = structured fallback for models without tool calling)
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"

    # ---- Integrations
    science_engine_url: str | None = None   # Scientific Intelligence Engine (FastAPI, /api/intelligence/*)
    marswalk_api_url: str | None = None     # original Marswalk backend (/api/*), when it exists
    marswalk_enabled_endpoints: list[str] = field(default_factory=list)
    knowledge_dir: str | None = None        # folder of Markdown documents for retrieval (e.g. the engine's data/documents)

    # ---- Terrain (standalone terrain service)
    ctx_dem_path: str | None = None
    hirise_dtm_path: str | None = None
    allow_synthetic_terrain: bool = True    # used only when no DEM is configured; results are labelled SIMULATED

    # ---- Project-defined planning parameters (NOT NASA standards)
    max_slope_deg: float = 15.0
    caution_slope_deg: float = 10.0
    k_suit: float = 0.60
    stop_minutes: float = 30.0
    suit_budget_hours: float = 4.0
    reserve_percent: float = 20.0
    rad_reference_mgy_day: float = 0.21     # Curiosity RAD, Gale crater (reference only)

    # ---- Persistence / server
    session_db: str = str(VAR_DIR / "sessions.sqlite3")
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(AI_ROOT / ".env")
        s = cls()
        s.llm_provider = (_env("LLM_PROVIDER", s.llm_provider) or "auto").lower()
        s.llm_timeout_s = _env_float("LLM_TIMEOUT_S", s.llm_timeout_s)
        s.llm_temperature = _env_float("LLM_TEMPERATURE", s.llm_temperature)
        s.max_tool_rounds = int(_env_float("MAX_TOOL_ROUNDS", s.max_tool_rounds))
        s.gemini_api_key = _env("GEMINI_API_KEY")
        s.gemini_model = _env("GEMINI_MODEL", s.gemini_model)
        s.openai_api_key = _env("OPENAI_API_KEY")
        s.openai_base_url = _env("OPENAI_BASE_URL", s.openai_base_url).rstrip("/")
        s.openai_model = _env("OPENAI_MODEL")
        s.openai_tool_mode = (_env("OPENAI_TOOL_MODE", s.openai_tool_mode) or "native").lower()
        s.anthropic_api_key = _env("ANTHROPIC_API_KEY")
        s.anthropic_model = _env("ANTHROPIC_MODEL", s.anthropic_model)
        s.science_engine_url = (_env("SCIENCE_ENGINE_URL") or "").rstrip("/") or None
        s.marswalk_api_url = (_env("MARSWALK_API_URL") or "").rstrip("/") or None
        s.marswalk_enabled_endpoints = [e.strip() for e in (_env("MARSWALK_ENABLED_ENDPOINTS") or "").split(",") if e.strip()]
        s.knowledge_dir = _env("KNOWLEDGE_DIR")
        s.ctx_dem_path = _path(_env("CTX_DEM_PATH", _default_path("data", "raw", "ctx", "M20_JezeroCrater_CTXDEM_20m.tif")))
        s.hirise_dtm_path = _path(_env("HIRISE_DTM_PATH", _default_path(
            "data", "raw", "hirise", "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif")))
        s.knowledge_dir = _path(s.knowledge_dir)
        s.allow_synthetic_terrain = _env_bool("ALLOW_SYNTHETIC_TERRAIN", s.allow_synthetic_terrain)
        s.max_slope_deg = _env_float("MAX_SLOPE_DEG", s.max_slope_deg)
        s.caution_slope_deg = _env_float("CAUTION_SLOPE_DEG", s.caution_slope_deg)
        s.k_suit = _env_float("K_SUIT", s.k_suit)
        s.stop_minutes = _env_float("STOP_MINUTES", s.stop_minutes)
        s.suit_budget_hours = _env_float("SUIT_BUDGET_HOURS", s.suit_budget_hours)
        s.reserve_percent = _env_float("RESERVE_PERCENT", s.reserve_percent)
        s.rad_reference_mgy_day = _env_float("RAD_REFERENCE_MGY_DAY", s.rad_reference_mgy_day)
        s.session_db = _env("SESSION_DB", s.session_db)
        s.cors_origins = [o.strip() for o in (_env("CORS_ORIGINS", "*") or "*").split(",")]
        return s
