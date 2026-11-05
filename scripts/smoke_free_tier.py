"""Smoke verification script for free-tier Gemini and Tavily integrations.

Checks generate(), search(), extract(), and structured synthesis.
Prints ONLY statuses and token counts — never credentials.
Exits non-zero on empty results, failed extraction, or bad JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import GEMINI_API_KEY, TAVILY_API_KEY, GEMINI_MODEL
from app.llm_gemini import generate, _sanitize_error
from app import web_tools


async def main() -> int:
    gemini_key = os.getenv("GEMINI_API_KEY", GEMINI_API_KEY)
    tavily_key = os.getenv("TAVILY_API_KEY", TAVILY_API_KEY)

    if not gemini_key or not tavily_key:
        print("[SKIP] GEMINI_API_KEY or TAVILY_API_KEY not configured in environment or .env.")
        print("Please add keys to .env to execute live smoke verification.")
        return 1

    print(f"[*] Testing Gemini generation using model: {GEMINI_MODEL}...")
    try:
        resp, usage = await generate(
            contents=["Respond with the exact word: OK"],
            system="You are a system health check.",
            timeout_s=30.0,
            max_output_tokens=1024,
        )
        text_out = (resp.text or "").strip()
        if not text_out:
            print("    - generate() FAILED: empty response text")
            return 1
        print(f"    - generate() status: success (text: '{text_out}')")
        print(f"    - prompt_tokens: {usage.get('prompt_token_count')}, candidate_tokens: {usage.get('candidates_token_count')}")
    except Exception as exc:
        print(f"    - generate() failed: {type(exc).__name__}: {_sanitize_error(str(exc))[:100]}")
        return 1

    print("[*] Testing Tavily search...")
    results = []
    try:
        results = await web_tools.search("GitHub OAuth documentation", max_results=2)
        if not results:
            print("    - search() FAILED: returned 0 results (empty list)")
            return 1
        print(f"    - search() returned {len(results)} results")
        print(f"    - first result title: '{results[0].get('title', '')[:50]}'")
    except Exception as exc:
        print(f"    - search() failed: {type(exc).__name__}: {_sanitize_error(str(exc))[:100]}")
        return 1

    print("[*] Testing Tavily extract...")
    try:
        sample_url = results[0]["url"] if results else "https://docs.github.com"
        success_map, fail_map = await web_tools.extract([sample_url])
        if not success_map:
            print(f"    - extract() FAILED: 0 successful extractions (failed: {list(fail_map.values())[:1]})")
            return 1
        content = success_map.get(sample_url, "")
        if not content:
            print("    - extract() FAILED: extracted content is empty")
            return 1
        print(f"    - extract() succeeded: {len(success_map)} URLs, failed: {len(fail_map)} URLs")
        print(f"    - extracted content length: {len(content)} chars")
    except Exception as exc:
        print(f"    - extract() failed: {type(exc).__name__}: {_sanitize_error(str(exc))[:100]}")
        return 1

    print("[*] Testing Gemini structured JSON synthesis...")
    try:
        mini_schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "checks_passed": {"type": "integer"},
                "provider": {"type": "string"},
                "model": {"type": "string"},
                "timestamp": {"type": "string"},
            },
            "required": ["status", "checks_passed", "provider", "model", "timestamp"],
        }
        resp, usage = await generate(
            contents=["Output status as 'ready', checks_passed as 3, provider as 'gemini', "
                      "model as 'smoke', timestamp as '2026-11-04T00:00:00Z'."],
            system="Return JSON matching the schema exactly.",
            json_schema=mini_schema,
            timeout_s=30.0,
            max_output_tokens=1024,
        )
        json_out = (resp.text or "").strip()
        if not json_out:
            print("    - synthesis FAILED: empty JSON output")
            return 1
        try:
            parsed = json.loads(json_out)
        except json.JSONDecodeError as je:
            print(f"    - synthesis FAILED: invalid JSON — {je}")
            return 1
        if parsed.get("status") != "ready":
            print(f"    - synthesis FAILED: expected status='ready', got '{parsed.get('status')}'")
            return 1
        if parsed.get("checks_passed") != 3:
            print(f"    - synthesis FAILED: expected checks_passed=3, got {parsed.get('checks_passed')}")
            return 1
        print(f"    - synthesis status: success (json validated, checks_passed={parsed['checks_passed']})")
        print(f"    - prompt_tokens: {usage.get('prompt_token_count')}, candidate_tokens: {usage.get('candidates_token_count')}")
    except Exception as exc:
        print(f"    - synthesis failed: {type(exc).__name__}: {_sanitize_error(str(exc))[:100]}")
        return 1

    print("\n[SUCCESS] All free-tier live components verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
