"""
Marswalk AI Assistant - Configuration
Centralized configuration management using Pydantic Settings (v2).

All paths are resolved relative to this backend folder (not the current working directory),
so `python main.py` works from anywhere.
"""
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent.parent          # marswalk-combined/ (the Marswalk app)


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Marswalk AI Assistant"
    APP_VERSION: str = "1.1.0"
    DEBUG: bool = False

    # Server (8000 is used by the Scientific Intelligence Engine, 8085 by the Marswalk app)
    HOST: str = "127.0.0.1"
    PORT: int = 8095

    # Ollama (local LLM). qwen3:1.7b runs on CPU; qwen3:4b is better at tool selection but ~2.5x slower.
    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    DEFAULT_MODEL: str = "qwen3:1.7b"
    MODEL_TIMEOUT: int = 180                 # seconds per model call (CPU inference)
    NUM_CTX: int = 8192                      # context window given to the model
    THINK: bool = False                      # qwen3 "thinking" mode: better reasoning, much slower on CPU
    KEEP_ALIVE: str = "30m"                  # keep the model loaded between questions
    MAX_TOOL_ROUNDS: int = 3                 # model → tools → model … at most this many tool rounds per question

    # Marswalk application (real data: DEM terrain, routes, EVA budget, stops, mission log)
    MARSWALK_API_URL: str = "http://127.0.0.1:8085"

    # Embedding model (optional - only used when chromadb + sentence-transformers are installed)
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DEVICE: str = "cpu"

    # Storage
    DATABASE_URL: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'marswalk_ai.db').as_posix()}"
    CHROMA_PERSIST_DIR: str = str(BACKEND_DIR / "data" / "chroma")
    CHROMA_COLLECTION_NAME: str = "marswalk_knowledge"

    # Document ingestion: everything in DOCUMENTS_DIR is indexed at startup (new/changed files only)
    DOCUMENTS_DIR: str = str(BACKEND_DIR / "data" / "documents")
    CHUNK_SIZE: int = 900
    CHUNK_OVERLAP: int = 120

    # Conversation
    MAX_HISTORY_MESSAGES: int = 8

    # Tool calling
    ENABLE_TOOLS: bool = True
    TOOL_TIMEOUT: int = 120                  # route planning on the 1 m DEM can take a while

    # CORS: the Marswalk page (8085) and local static servers
    CORS_ORIGINS: List[str] = ["*"]

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8-sig",       # tolerate a BOM written by Windows editors
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()

# Ensure data directories exist
Path(settings.DATABASE_URL.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
Path(settings.CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.DOCUMENTS_DIR).mkdir(parents=True, exist_ok=True)
