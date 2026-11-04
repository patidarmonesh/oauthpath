"""Tavily web search and page extraction wrapper."""

from __future__ import annotations

import os
from typing import Any
from tavily import AsyncTavilyClient

from app.config import TAVILY_API_KEY, MAX_FETCH_TOKENS
from app.validation import validate_url

_tavily_client: AsyncTavilyClient | None = None


def get_tavily_client() -> AsyncTavilyClient:
    """Return a cached AsyncTavilyClient instance."""
    global _tavily_client
    if _tavily_client is None:
        key = os.getenv("TAVILY_API_KEY", TAVILY_API_KEY)
        if not key:
            raise ValueError("TAVILY_API_KEY is not configured")
        _tavily_client = AsyncTavilyClient(api_key=key)
    return _tavily_client


async def search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Execute Tavily basic web search returning list of result dicts."""
    client = get_tavily_client()
    try:
        resp = await client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_published_date=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Tavily search failed: {exc}") from exc

    results = []
    for r in resp.get("results", []):
        url = (r.get("url") or "").strip()
        if not url.startswith("https://"):
            continue
        valid, _ = validate_url(url, is_source=True)
        if not valid:
            continue

        results.append({
            "title": (r.get("title") or "").strip(),
            "url": url,
            "content": (r.get("content") or "").strip(),
            "published_date": r.get("published_date") or None,
        })
    return results


async def extract(urls: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Extract page content via Tavily Markdown extractor.

    Returns:
        (success_map, failure_map) where:
        success_map: {url: truncated_markdown_content}
        failure_map: {url: error_message}
    """
    valid_urls = []
    failure_map: dict[str, str] = {}

    for u in urls:
        clean_u = (u or "").strip()
        if not clean_u.startswith("https://"):
            failure_map[clean_u] = "Non-https URL rejected"
            continue
        valid, reason = validate_url(clean_u, is_source=True)
        if not valid:
            failure_map[clean_u] = f"Invalid URL: {reason}"
            continue
        valid_urls.append(clean_u)

    if not valid_urls:
        return {}, failure_map

    client = get_tavily_client()
    max_chars = MAX_FETCH_TOKENS * 4

    try:
        resp = await client.extract(
            urls=valid_urls,
            extract_depth="basic",
            format="markdown",
        )
    except Exception as exc:
        for u in valid_urls:
            failure_map[u] = str(exc)
        return {}, failure_map

    success_map: dict[str, str] = {}

    # 1. Process successful results
    for item in resp.get("results", []):
        u = item.get("url") or ""
        content = item.get("raw_content") or ""
        if len(content) > max_chars:
            content = content[:max_chars]
        success_map[u] = content

    # 2. Process failed results
    for item in resp.get("failed_results", []):
        u = item.get("url") or ""
        err = item.get("error") or "Extraction failed"
        failure_map[u] = str(err)

    return success_map, failure_map
