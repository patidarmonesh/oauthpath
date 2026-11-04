"""OAuthPath configuration — environment variables with validation."""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Schema and prompt versions — part of the cache key
SCHEMA_VERSION = "2"
PROMPT_VERSION = "1"

# Composio callback constant
COMPOSIO_CALLBACK_URL = "https://backend.composio.dev/api/v3/toolkits/auth/callback"
COMPOSIO_API_HOST = "https://backend.composio.dev"

# ----- Environment variables -----

LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini").lower().strip()
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY")

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
RESEARCH_MODEL: str = os.getenv("RESEARCH_MODEL", "claude-sonnet-5-5").strip()

COMPOSIO_API_KEY: str | None = os.getenv("COMPOSIO_API_KEY")

DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/oauthpath.db")
JOB_TIMEOUT_SECONDS: int = int(os.getenv("JOB_TIMEOUT_SECONDS", "420"))
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))
MAX_SEARCHES: int = int(os.getenv("MAX_SEARCHES", "6"))
MAX_FETCHES: int = int(os.getenv("MAX_FETCHES", "8"))
MAX_FETCH_TOKENS: int = int(os.getenv("MAX_FETCH_TOKENS", "10000"))
MAX_TURNS: int = int(os.getenv("MAX_TURNS", "8"))
JOBS_PER_IP_PER_HOUR: int = int(os.getenv("JOBS_PER_IP_PER_HOUR", "5"))
CACHE_MAX_AGE_HOURS: int = int(os.getenv("CACHE_MAX_AGE_HOURS", "168"))

PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")


def get_active_provider(caller_module=None) -> str:
    """Return the active LLM provider: explicit LLM_PROVIDER, or auto-detect from keys."""
    prov = os.getenv("LLM_PROVIDER")
    if prov:
        return prov.lower().strip()

    anthropic_k = None
    gemini_k = None
    tavily_k = None

    if caller_module:
        anthropic_k = getattr(caller_module, "ANTHROPIC_API_KEY", None)
        gemini_k = getattr(caller_module, "GEMINI_API_KEY", None)
        tavily_k = getattr(caller_module, "TAVILY_API_KEY", None)

    import app.config as cfg
    if not anthropic_k:
        anthropic_k = getattr(cfg, "ANTHROPIC_API_KEY", None) or os.getenv("ANTHROPIC_API_KEY")
    if not gemini_k:
        gemini_k = getattr(cfg, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
    if not tavily_k:
        tavily_k = getattr(cfg, "TAVILY_API_KEY", None) or os.getenv("TAVILY_API_KEY")

    if anthropic_k and not (gemini_k and tavily_k):
        return "anthropic"
    if gemini_k and tavily_k:
        return "gemini"

    default_prov = getattr(cfg, "LLM_PROVIDER", "gemini")
    return default_prov.lower().strip() if default_prov else "gemini"


def live_generation_configured() -> bool:
    """Return True if the active provider has required API keys configured."""
    provider = get_active_provider()
    import app.config as cfg
    if provider == "gemini":
        gemini_k = getattr(cfg, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
        tavily_k = getattr(cfg, "TAVILY_API_KEY", None) or os.getenv("TAVILY_API_KEY")
        return bool(gemini_k and tavily_k)
    elif provider == "anthropic":
        anthropic_k = getattr(cfg, "ANTHROPIC_API_KEY", None) or os.getenv("ANTHROPIC_API_KEY")
        return bool(anthropic_k)
    return False


def get_active_model_name() -> str:
    """Return the active model name based on LLM_PROVIDER."""
    provider = get_active_provider()
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", GEMINI_MODEL)
    return os.getenv("RESEARCH_MODEL", RESEARCH_MODEL)


def composio_lookup_configured() -> bool:
    """Return True if the Composio API key is set."""
    return bool(os.getenv("COMPOSIO_API_KEY", COMPOSIO_API_KEY))


def get_data_dir() -> Path:
    """Return the data directory, creating it if needed."""
    p = Path(DATABASE_PATH).parent
    p.mkdir(parents=True, exist_ok=True)
    return p
