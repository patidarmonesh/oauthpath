"""Smoke verification script for free-tier Gemini and Tavily integrations.

Checks generate(), search(), extract(), and structured synthesis.
Prints ONLY statuses and token counts — never credentials.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import GEMINI_API_KEY, TAVILY_API_KEY, GEMINI_MODEL
from app.llm_gemini import generate
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
            max_output_tokens=256,
        )
        text_out = (resp.text or "").strip()
        print(f"    - generate() status: success (text: '{text_out}')")
        print(f"    - prompt_tokens: {usage.get('prompt_token_count')}, candidate_tokens: {usage.get('candidates_token_count')}")
    except Exception as exc:
        print(f"    - generate() failed: {type(exc).__name__}: {exc}")
        return 1

    print("[*] Testing Tavily search...")
    try:
        results = await web_tools.search("GitHub OAuth documentation", max_results=2)
        print(f"    - search() returned {len(results)} results")
        if results:
            print(f"    - first result title: '{results[0].get('title', '')[:50]}'")
    except Exception as exc:
        print(f"    - search() failed: {type(exc).__name__}: {exc}")
        return 1

    print("[*] Testing Tavily extract...")
    try:
        sample_url = results[0]["url"] if results else "https://docs.github.com"
        success_map, fail_map = await web_tools.extract([sample_url])
        print(f"    - extract() succeeded: {len(success_map)} URLs, failed: {len(fail_map)} URLs")
        if sample_url in success_map:
            print(f"    - extracted content length: {len(success_map[sample_url])} chars")
    except Exception as exc:
        print(f"    - extract() failed: {type(exc).__name__}")
        return 1

    print("[*] Testing Gemini structured JSON synthesis on mini schema...")
    try:
        mini_schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "checks_passed": {"type": "integer"},
            },
            "required": ["status", "checks_passed"],
        }
        resp, usage = await generate(
            contents=["Output status as 'ready' and checks_passed as 3."],
            system="Return JSON matching the schema.",
            json_schema=mini_schema,
            timeout_s=30.0,
            max_output_tokens=256,
        )
        json_out = (resp.text or "").strip()
        print(f"    - synthesis status: success (json: {json_out})")
        print(f"    - prompt_tokens: {usage.get('prompt_token_count')}, candidate_tokens: {usage.get('candidates_token_count')}")
    except Exception as exc:
        print(f"    - synthesis failed: {type(exc).__name__}: {exc}")
        return 1

    print("\n[SUCCESS] All free-tier live components verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
