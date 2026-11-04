"""Thin asynchronous wrapper around google-genai for Gemini free tier."""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.config import GEMINI_API_KEY, GEMINI_MODEL


# ── Typed error categories ──────────────────────────────────────────────────

class GeminiError(Exception):
    """Base class for categorised Gemini adapter errors."""
    category: str = "unknown"


class AuthError(GeminiError):
    """HTTP 401/403 — invalid or missing API key."""
    category = "auth"


class RateLimitExceeded(GeminiError):
    """HTTP 429 — per-minute or per-day quota exceeded."""
    category = "rate_limit"


class ProviderUnavailable(GeminiError):
    """HTTP 503 or transient infrastructure outage."""
    category = "unavailable"


class RequestTimeout(GeminiError):
    """HTTP 504 or asyncio deadline exceeded."""
    category = "timeout"


class InvalidRequest(GeminiError):
    """HTTP 400 — bad model name, unsupported parameter, schema error."""
    category = "invalid_request"


class InvalidOutput(GeminiError):
    """Model returned an empty or unparseable response."""
    category = "invalid_output"


# Safe public messages per category
_SAFE_MESSAGES: dict[str, str] = {
    "auth": "API key rejected by provider. Check your GEMINI_API_KEY configuration.",
    "rate_limit": "Free-tier request quota reached. Wait before retrying.",
    "unavailable": "Provider temporarily unavailable. Try again shortly.",
    "timeout": "Request timed out. The provider may be under load.",
    "invalid_request": "Invalid model request (bad model name, schema, or parameter).",
    "invalid_output": "Model returned an unusable response.",
    "unknown": "Unexpected provider error.",
}


def safe_message_for(exc: GeminiError) -> str:
    return _SAFE_MESSAGES.get(exc.category, _SAFE_MESSAGES["unknown"])


# ── Credential sanitisation ─────────────────────────────────────────────────

def _sanitize_error(text: str) -> str:
    """Remove known credential patterns from error strings before any public use."""
    # Authorization headers and bearer tokens
    text = re.sub(r'(?i)(Authorization:\s*Bearer\s*)\S+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*', r'\1[REDACTED]', text)
    # JSON api_key / apiKey / access_token / secret fields
    text = re.sub(r'(?i)("(?:api_?key|access_token|secret|client_secret|tvly-[^"]*)":\s*")([^"]+)"',
                  r'\1[REDACTED]"', text)
    # Query-string key= and apikey= patterns
    text = re.sub(r'(?i)(\b(?:api_?key|key|token|secret)\s*=\s*)[^&\s"\']+',
                  r'\1[REDACTED]', text)
    # Bare tvly- Tavily key prefix
    text = re.sub(r'tvly-[A-Za-z0-9]+', 'tvly-[REDACTED]', text)
    # Bare AIza / AQ. Gemini key patterns
    text = re.sub(r'(?:AIza|AQ\.)[A-Za-z0-9\-_]+', '[REDACTED]', text)
    # Composio ck_ keys
    text = re.sub(r'ck_[A-Za-z0-9_]+', 'ck_[REDACTED]', text)
    # Also remove any configured values explicitly
    for env_var in ("GEMINI_API_KEY", "TAVILY_API_KEY", "COMPOSIO_API_KEY",
                    "ANTHROPIC_API_KEY", "GITHUB_CLIENT_SECRET"):
        val = os.getenv(env_var, "")
        if val and len(val) > 8:
            text = text.replace(val, "[REDACTED]")
    return text


# ── Client management ───────────────────────────────────────────────────────

_gemini_client: genai.Client | None = None
_client_loop = None


def get_gemini_client() -> genai.Client:
    """Return a google-genai Client bound to the current event loop."""
    global _gemini_client, _client_loop
    try:
        curr_loop = asyncio.get_running_loop()
    except RuntimeError:
        curr_loop = None

    if _gemini_client is None or _client_loop != curr_loop:
        key = os.getenv("GEMINI_API_KEY", GEMINI_API_KEY)
        if not key:
            raise AuthError("GEMINI_API_KEY is not configured")
        _gemini_client = genai.Client(api_key=key)
        _client_loop = curr_loop
    return _gemini_client


# ── Error classification ────────────────────────────────────────────────────

def _classify(exc: Exception) -> GeminiError:
    """Map a raw provider exception to a typed GeminiError category.

    Uses structured HTTP status fields first; falls back to narrowly scoped
    text patterns only as a last resort.  503/unavailable is NOT a quota error.
    """
    # Prefer structured fields (google-genai APIError exposes .code)
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)

    if code == 401 or code == 403:
        return AuthError(safe_message_for(AuthError()))
    if code == 400:
        return InvalidRequest(safe_message_for(InvalidRequest()))
    if code == 429:
        return RateLimitExceeded(safe_message_for(RateLimitExceeded()))
    if code == 503:
        return ProviderUnavailable(safe_message_for(ProviderUnavailable()))
    if code == 504:
        return RequestTimeout(safe_message_for(RequestTimeout()))

    # asyncio / SDK timeout signals
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return RequestTimeout(safe_message_for(RequestTimeout()))

    # Narrow text fallback — only when no structured code is available
    msg = str(exc).lower()
    if "api key" in msg or "invalid key" in msg or "unauthenticated" in msg:
        return AuthError(safe_message_for(AuthError()))
    if "resource_exhausted" in msg or "rate limit" in msg or "quota" in msg or "429" in msg:
        return RateLimitExceeded(safe_message_for(RateLimitExceeded()))
    if "unavailable" in msg or "503" in msg:
        return ProviderUnavailable(safe_message_for(ProviderUnavailable()))
    if "deadline" in msg or "timeout" in msg or "504" in msg:
        return RequestTimeout(safe_message_for(RequestTimeout()))
    if "invalid_argument" in msg or "400" in msg:
        return InvalidRequest(safe_message_for(InvalidRequest()))

    return GeminiError(_SAFE_MESSAGES["unknown"])


def _extract_retry_delay(exc: Exception) -> float:
    """Extract suggested retry delay in seconds (clamped 5–30 s), defaulting 20 s."""
    msg = str(exc)
    match = re.search(r'retry(?:ing)? after (\d+(?:\.\d+)?)s', msg, re.IGNORECASE)
    if match:
        try:
            return min(max(float(match.group(1)), 5.0), 30.0)
        except ValueError:
            pass
    return 20.0


# ── Main generate call ──────────────────────────────────────────────────────

async def generate(
    contents: Any,
    system: str | None = None,
    *,
    tools: list[Any] | None = None,
    tool_mode: str = "AUTO",
    json_schema: dict | None = None,
    max_output_tokens: int | None = None,
    timeout_s: float | None = None,
    model: str | None = None,
    deadline: float | None = None,
) -> tuple[types.GenerateContentResponse, dict[str, int]]:
    """Execute generate_content against Gemini with bounded retry on rate-limit only.

    Args:
        deadline: Optional monotonic absolute deadline (time.monotonic()).
                  When provided, backoff sleep and second attempt are bounded by
                  remaining time so this call never exceeds the phase budget.

    Returns:
        (response, usage) where usage has prompt_token_count and candidates_token_count.

    Raises:
        AuthError, RateLimitExceeded, ProviderUnavailable, RequestTimeout,
        InvalidRequest, InvalidOutput, GeminiError — all with sanitised messages.
    """
    client = get_gemini_client()
    target_model = (model or os.getenv("GEMINI_MODEL", GEMINI_MODEL)).strip()

    # Build config
    config_kwargs: dict[str, Any] = {
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }

    if system:
        config_kwargs["system_instruction"] = system

    if tools:
        if isinstance(tools[0], types.Tool):
            config_kwargs["tools"] = tools
        else:
            config_kwargs["tools"] = [types.Tool(function_declarations=tools)]

        if tool_mode:
            mode_val = (
                types.FunctionCallingConfigMode.NONE
                if tool_mode.upper() == "NONE"
                else types.FunctionCallingConfigMode.AUTO
            )
            config_kwargs["tool_config"] = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=mode_val)
            )

    if json_schema:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = json_schema

    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens

    if timeout_s is not None and timeout_s > 0:
        config_kwargs["http_options"] = types.HttpOptions(timeout=int(timeout_s * 1000))

    config = types.GenerateContentConfig(**config_kwargs)

    # Single retry on rate-limit (429 only), bounded by the remaining deadline.
    last_exc: Exception | None = None
    for attempt in range(2):
        # Re-check deadline before each attempt
        if deadline is not None and time.monotonic() >= deadline:
            raise RequestTimeout(safe_message_for(RequestTimeout()))

        try:
            resp = await client.aio.models.generate_content(
                model=target_model,
                contents=contents,
                config=config,
            )
            usage_meta = getattr(resp, "usage_metadata", None)
            usage = {
                "prompt_token_count": getattr(usage_meta, "prompt_token_count", 0) or 0,
                "candidates_token_count": getattr(usage_meta, "candidates_token_count", 0) or 0,
            }
            return resp, usage

        except Exception as exc:
            last_exc = exc
            typed = _classify(exc)

            # Only retry on pure 429 rate-limit, never on 503/unavailable
            if isinstance(typed, RateLimitExceeded) and attempt == 0:
                delay = _extract_retry_delay(exc)
                # Do not sleep beyond the remaining budget
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    delay = min(delay, max(0.0, remaining - 1.0))
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            raise typed from None

    # Exhausted retry
    if last_exc is not None:
        raise _classify(last_exc) from None
    raise GeminiError(_SAFE_MESSAGES["unknown"])
