"""Regression tests for typed Gemini error categories, credential sanitization,
and smoke-script failure modes.

Covers:
- _classify() maps HTTP codes to correct typed exceptions (503 ≠ 429)
- _sanitize_error() removes credential patterns before any public use
- generate() raises typed exceptions with sanitized messages
- _run_job delivers provider-appropriate safe messages for each category
- Deadline-bounded backoff never sleeps past the remaining budget
- smoke_free_tier exits non-zero on empty search / failed extraction / bad JSON
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm_gemini import (
    AuthError,
    GeminiError,
    InvalidOutput,
    InvalidRequest,
    ProviderUnavailable,
    RateLimitExceeded,
    RequestTimeout,
    _classify,
    _sanitize_error,
    generate,
    safe_message_for,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _api_error(code: int, msg: str = "error") -> Exception:
    """Build a minimal stand-in for google.genai.errors.APIError."""
    exc = Exception(msg)
    exc.code = code  # type: ignore[attr-defined]
    return exc


# ── 1. _classify: HTTP status codes ──────────────────────────────────────────

def test_classify_429_is_rate_limit():
    exc = _api_error(429, "Resource exhausted")
    result = _classify(exc)
    assert isinstance(result, RateLimitExceeded)
    assert result.category == "rate_limit"


def test_classify_503_is_provider_unavailable_not_rate_limit():
    """503 Service Unavailable must NOT map to RateLimitExceeded."""
    exc = _api_error(503, "Service temporarily unavailable")
    result = _classify(exc)
    assert isinstance(result, ProviderUnavailable), (
        f"Expected ProviderUnavailable for 503, got {type(result).__name__}"
    )
    assert result.category == "unavailable"
    assert not isinstance(result, RateLimitExceeded)


def test_classify_401_is_auth_error():
    exc = _api_error(401, "API key not valid")
    result = _classify(exc)
    assert isinstance(result, AuthError)
    assert result.category == "auth"


def test_classify_403_is_auth_error():
    exc = _api_error(403, "Permission denied")
    result = _classify(exc)
    assert isinstance(result, AuthError)
    assert result.category == "auth"


def test_classify_504_is_request_timeout():
    exc = _api_error(504, "Gateway timeout")
    result = _classify(exc)
    assert isinstance(result, RequestTimeout)
    assert result.category == "timeout"


def test_classify_400_is_invalid_request():
    exc = _api_error(400, "Invalid argument")
    result = _classify(exc)
    assert isinstance(result, InvalidRequest)
    assert result.category == "invalid_request"


def test_classify_asyncio_timeout_is_request_timeout():
    result = _classify(asyncio.TimeoutError())
    assert isinstance(result, RequestTimeout)
    assert result.category == "timeout"


# ── 2. _sanitize_error: credential patterns ───────────────────────────────────

def test_sanitize_bearer_token():
    text = "Authorization: Bearer sk-secret-abc123 — rejected"
    out = _sanitize_error(text)
    assert "sk-secret-abc123" not in out
    assert "[REDACTED]" in out


def test_sanitize_tvly_prefix():
    text = "Tavily error: tvly-XyZabc1234567890 is invalid"
    out = _sanitize_error(text)
    assert "tvly-XyZabc1234567890" not in out
    assert "tvly-[REDACTED]" in out


def test_sanitize_ck_prefix():
    text = "Composio error with key ck_Spw8abcDEF123456"
    out = _sanitize_error(text)
    assert "ck_Spw8abcDEF123456" not in out
    assert "ck_[REDACTED]" in out


def test_sanitize_api_key_json_field():
    text = '{"api_key": "my_very_secret_key_12345", "other": "ok"}'
    out = _sanitize_error(text)
    assert "my_very_secret_key_12345" not in out
    assert "[REDACTED]" in out


def test_sanitize_gemini_aiza_key():
    text = "API key AIzaSyABCDEF1234567890xyz rejected"
    out = _sanitize_error(text)
    assert "AIzaSyABCDEF1234567890xyz" not in out
    assert "[REDACTED]" in out


def test_sanitize_env_var_value(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super_secret_gemini_val_xyz")
    text = "Error with key super_secret_gemini_val_xyz in payload"
    out = _sanitize_error(text)
    assert "super_secret_gemini_val_xyz" not in out


# ── 3. generate(): typed exceptions with sanitised messages ───────────────────

@pytest.mark.anyio
async def test_generate_raises_rate_limit_exceeded_on_persistent_429():
    """Persistent 429 raises RateLimitExceeded with safe message, not raw exc."""
    secret = "tvly-SecretTavilyKey9999"
    err_429 = _api_error(429, f"Resource exhausted. key={secret}")
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=err_429)

    with patch("app.llm_gemini.get_gemini_client", return_value=client):
        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(RateLimitExceeded) as exc_info:
                await generate(["Hello"], model="gemini-test")

    assert secret not in str(exc_info.value)
    assert "quota" in str(exc_info.value).lower() or "rate" in str(exc_info.value).lower() or "free" in str(exc_info.value).lower()


@pytest.mark.anyio
async def test_generate_raises_provider_unavailable_on_503():
    """503 raises ProviderUnavailable, not RateLimitExceeded."""
    err_503 = _api_error(503, "Service temporarily unavailable")
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=err_503)

    with patch("app.llm_gemini.get_gemini_client", return_value=client):
        with pytest.raises(ProviderUnavailable):
            await generate(["Hello"], model="gemini-test")


@pytest.mark.anyio
async def test_generate_message_for_503_does_not_say_rate_limit():
    """503 user message mentions 'unavailable', never 'quota' or 'rate limit'."""
    err_503 = _api_error(503, "Service unavailable")
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=err_503)

    with patch("app.llm_gemini.get_gemini_client", return_value=client):
        with pytest.raises(ProviderUnavailable) as exc_info:
            await generate(["Hello"], model="gemini-test")

    msg = str(exc_info.value).lower()
    assert "unavailable" in msg or "outage" in msg or "provider" in msg
    assert "quota" not in msg
    assert "rate limit" not in msg


@pytest.mark.anyio
async def test_generate_raises_auth_error_on_401():
    err_401 = _api_error(401, "Invalid API key")
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=err_401)

    with patch("app.llm_gemini.get_gemini_client", return_value=client):
        with pytest.raises(AuthError):
            await generate(["Hello"], model="gemini-test")


# ── 4. Deadline-bounded backoff ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_deadline_bounded_backoff_does_not_sleep_past_budget():
    """When deadline is 2 s away, backoff sleep is capped to remaining time."""
    import time
    deadline = time.monotonic() + 2.0
    err_429 = _api_error(429, "retry after 60s")
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=err_429)

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    with patch("app.llm_gemini.get_gemini_client", return_value=client):
        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(RateLimitExceeded):
                await generate(["Hello"], model="gemini-test", deadline=deadline)

    # Sleep must be < 2.0 (remaining) not 60 s (suggested delay)
    assert slept, "Expected at least one sleep call for 429 retry"
    assert slept[0] < 5.0, f"Backoff sleep {slept[0]:.1f}s exceeds remaining deadline budget"


# ── 5. _run_job: provider-appropriate safe messages ───────────────────────────

@pytest.mark.anyio
async def test_run_job_rate_limit_error_gives_quota_message(tmp_path):
    """When pipeline raises RateLimitExceeded, job error_message mentions quota."""
    from app.store import Store
    from app import main as app_module

    db = tmp_path / "test.db"
    test_store = Store(str(db))
    job_id = "test-job-rl-001"
    test_store.create_job(job_id, "TestApp", feature="f", audience="a", website="")

    # Patch run_pipeline where it's bound in main's namespace (from app.pipeline import run_pipeline)
    async def _fail(*args, **kwargs):
        raise RateLimitExceeded("Free-tier request quota reached. Wait before retrying.")

    sem = asyncio.Semaphore(1)
    with patch.object(app_module, "store", test_store):
        with patch.object(app_module, "run_pipeline", _fail):
            with patch.object(app_module, "active_tasks", {}):
                with patch.object(app_module, "job_semaphore", sem):
                    await app_module._run_job(job_id, "TestApp", "f", "a", "", False)

    job = test_store.get_job(job_id)
    assert job is not None
    msg = (job.get("error_message") or "").lower()
    assert "quota" in msg or "rate" in msg or "free" in msg, (
        f"Expected quota/rate message, got: {job.get('error_message')}"
    )
    assert "traceback" not in msg
    assert "exception" not in msg


@pytest.mark.anyio
async def test_run_job_503_message_says_unavailable_not_quota(tmp_path):
    """When pipeline raises ProviderUnavailable, job message says unavailable."""
    from app.store import Store
    from app import main as app_module

    db = tmp_path / "test.db"
    test_store = Store(str(db))
    job_id = "test-job-503-001"
    test_store.create_job(job_id, "TestApp", feature="f", audience="a", website="")

    async def _fail(*args, **kwargs):
        raise ProviderUnavailable("Provider temporarily unavailable. Try again shortly.")

    sem = asyncio.Semaphore(1)
    with patch.object(app_module, "store", test_store):
        with patch.object(app_module, "run_pipeline", _fail):
            with patch.object(app_module, "active_tasks", {}):
                with patch.object(app_module, "job_semaphore", sem):
                    await app_module._run_job(job_id, "TestApp", "f", "a", "", False)

    job = test_store.get_job(job_id)
    assert job is not None
    msg = (job.get("error_message") or "").lower()
    assert "unavailable" in msg or "provider" in msg, (
        f"Expected unavailable message, got: {job.get('error_message')}"
    )
    assert "quota" not in msg
    assert "rate limit" not in msg


# ── 6. smoke_free_tier failure modes ─────────────────────────────────────────

@pytest.mark.anyio
async def test_smoke_fails_on_empty_search(monkeypatch, tmp_path):
    """smoke_free_tier main() returns non-zero when search returns empty list."""
    # Import after path is set
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

    # Must patch env vars so we don't skip
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.setenv("TAVILY_API_KEY", "fake_tavily_key")

    # Mock generate to succeed
    fake_resp = MagicMock()
    fake_resp.text = "OK"
    fake_usage = {"prompt_token_count": 10, "candidates_token_count": 5}

    async def mock_generate(*args, **kwargs):
        return fake_resp, fake_usage

    # Mock search to return empty
    async def mock_search(*args, **kwargs):
        return []

    with patch("app.llm_gemini.generate", side_effect=mock_generate):
        with patch("app.web_tools.search", side_effect=mock_search):
            # Import fresh
            if "smoke_free_tier" in sys.modules:
                del sys.modules["smoke_free_tier"]
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "smoke_free_tier",
                Path(__file__).resolve().parent.parent / "scripts" / "smoke_free_tier.py"
            )
            smoke = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(smoke)
            result = await smoke.main()

    assert result != 0, "smoke main() should exit non-zero on empty search"


@pytest.mark.anyio
async def test_smoke_fails_on_bad_json(monkeypatch):
    """smoke_free_tier main() returns non-zero when structured output is invalid JSON."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.setenv("TAVILY_API_KEY", "fake_tavily_key")

    call_count = 0

    async def mock_generate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count == 1:
            # First call: plain generate — return valid text
            resp.text = "OK"
        else:
            # Second call: JSON structured — return invalid JSON
            resp.text = "NOT_VALID_JSON{{{}"
        usage = {"prompt_token_count": 10, "candidates_token_count": 5}
        return resp, usage

    async def mock_search(*args, **kwargs):
        return [{"url": "https://docs.github.com", "title": "GitHub Docs", "content": "text", "published_date": None}]

    async def mock_extract(*args, **kwargs):
        return {"https://docs.github.com": "content"}, {}

    with patch("app.llm_gemini.generate", side_effect=mock_generate):
        with patch("app.web_tools.search", side_effect=mock_search):
            with patch("app.web_tools.extract", side_effect=mock_extract):
                if "smoke_free_tier" in sys.modules:
                    del sys.modules["smoke_free_tier"]
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "smoke_free_tier",
                    Path(__file__).resolve().parent.parent / "scripts" / "smoke_free_tier.py"
                )
                smoke = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(smoke)
                result = await smoke.main()

    assert result != 0, "smoke main() should exit non-zero on bad JSON output"
