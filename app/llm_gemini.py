"""Thin asynchronous wrapper around google-genai for Gemini free tier."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.config import GEMINI_API_KEY, GEMINI_MODEL


class RateLimitExceeded(Exception):
    """Raised when Gemini 429 rate limit is reached after retry."""
    pass


_gemini_client: genai.Client | None = None


def get_gemini_client() -> genai.Client:
    """Return a cached google-genai Client."""
    global _gemini_client
    if _gemini_client is None:
        key = os.getenv("GEMINI_API_KEY", GEMINI_API_KEY)
        if not key:
            raise ValueError("GEMINI_API_KEY is not configured")
        _gemini_client = genai.Client(api_key=key)
    return _gemini_client


def _is_rate_limit(exc: Exception) -> bool:
    """Detect HTTP 429, 503 or resource exhausted / unavailable errors."""
    code = getattr(exc, "code", None)
    if code in (429, 503):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code in (429, 503):
        return True
    msg = str(exc).lower()
    return "429" in msg or "503" in msg or "unavailable" in msg or "resource_exhausted" in msg or "rate limit" in msg or "quota" in msg


def _extract_retry_delay(exc: Exception) -> float:
    """Extract suggested delay in seconds, defaulting to 10s."""
    msg = str(exc)
    match = re.search(r'retry(?:ing)? after (\d+(?:\.\d+)?)s', msg, re.IGNORECASE)
    if match:
        try:
            return min(float(match.group(1)), 30.0)
        except ValueError:
            pass
    return 10.0


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
) -> tuple[types.GenerateContentResponse, dict[str, int]]:
    """Execute generate_content against Gemini with automatic 429 retry.

    Returns:
        (response, usage) where usage has prompt_token_count and candidates_token_count.
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

    # Call with single retry on 429
    last_exc: Exception | None = None
    for attempt in range(2):
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
            if _is_rate_limit(exc) and attempt == 0:
                delay = _extract_retry_delay(exc)
                await asyncio.sleep(delay)
                continue
            break

    if last_exc:
        if _is_rate_limit(last_exc):
            raise RateLimitExceeded("Free-tier model limit reached. Try again in a minute.") from None
        # Sanitize error to avoid leaking credentials
        sanitized_msg = re.sub(r'key=[^&\s"\']+', 'key=[REDACTED]', str(last_exc))
        raise RuntimeError(f"Gemini API error: {sanitized_msg}") from None

    raise RuntimeError("Gemini generate call returned no response.")
