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

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
COMPOSIO_API_KEY: str | None = os.getenv("COMPOSIO_API_KEY")
RESEARCH_MODEL: str = os.getenv("RESEARCH_MODEL", "claude-sonnet-5-5")

DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/oauthpath.db")
JOB_TIMEOUT_SECONDS: int = int(os.getenv("JOB_TIMEOUT_SECONDS", "420"))
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
MAX_SEARCHES: int = int(os.getenv("MAX_SEARCHES", "6"))
MAX_FETCHES: int = int(os.getenv("MAX_FETCHES", "8"))
MAX_FETCH_TOKENS: int = int(os.getenv("MAX_FETCH_TOKENS", "10000"))
MAX_TURNS: int = int(os.getenv("MAX_TURNS", "8"))
JOBS_PER_IP_PER_HOUR: int = int(os.getenv("JOBS_PER_IP_PER_HOUR", "5"))
CACHE_MAX_AGE_HOURS: int = int(os.getenv("CACHE_MAX_AGE_HOURS", "168"))

PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")


def live_generation_configured() -> bool:
    """Return True if the Anthropic API key is set."""
    return bool(ANTHROPIC_API_KEY)


def composio_lookup_configured() -> bool:
    """Return True if the Composio API key is set."""
    return bool(COMPOSIO_API_KEY)


def get_data_dir() -> Path:
    """Return the data directory, creating it if needed."""
    p = Path(DATABASE_PATH).parent
    p.mkdir(parents=True, exist_ok=True)
    return p
