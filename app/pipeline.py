"""Pipeline — orchestrates one guide generation job per §6.2."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

from app.config import (
    JOB_TIMEOUT_SECONDS, CACHE_MAX_AGE_HOURS,
    SCHEMA_VERSION, PROMPT_VERSION,
    get_active_model_name,
)
from app.schema import (
    GuideRecord, Scenario, ProviderInfo, ComposioInfo,
    Source, Attempt, RunStats,
    make_guide_id, make_cache_key,
    OAuthSupport, GuideStatus, ComposioLookupStatus, ManagedOAuth,
)
from app.resolver import resolve_provider_with_model, lookup_composio_toolkit
from app.research import run_research
from app.synthesis import run_synthesis
from app.validation import validate_guide
from app.store import Store


def _load_attempts_for_provider(provider_name: str, domain: str = "") -> list[Attempt]:
    """Load observed setup attempts from data/attempts/ matching the provider."""
    import re
    from app.resolver import SHARED_COMPANY_DOMAINS, TOOLKIT_ALIASES

    attempts_dir = Path("data/attempts")
    if not attempts_dir.exists():
        return []

    p_clean = provider_name.lower().strip()
    targets = {p_clean}

    # Extract base name without parenthetical qualifiers (e.g. 'Gmail (Google)' -> 'gmail')
    base_name = re.sub(r'\(.*?\)', '', p_clean).strip()
    if base_name:
        targets.add(base_name)

    # Extract any parenthetical words (e.g. 'X (Twitter)' -> 'twitter')
    for inner in re.findall(r'\((.*?)\)', p_clean):
        w = inner.strip().lower()
        if w:
            targets.add(w)

    # Add canonical toolkit slugs and aliases
    for t in list(targets):
        slug = TOOLKIT_ALIASES.get(t)
        if slug:
            targets.add(slug)
    for alias, slug in TOOLKIT_ALIASES.items():
        if slug in targets:
            targets.add(alias)

    # Only match by domain if domain is NOT a shared company domain (to prevent cross-product sibling pollution)
    if domain:
        d_lower = domain.lower().strip()
        if d_lower not in SHARED_COMPANY_DOMAINS:
            targets.add(d_lower)

    targets.discard("")

    results: list[Attempt] = []
    for f in attempts_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Exclude synthetic / example fixture records per R1
            if data.get("example") is True or data.get("status") == "example":
                continue
            if str(data.get("id", "")).startswith("EXAMPLE-"):
                continue
            if "[SYNTHETIC" in str(data.get("observed_result", "")) or "[SYNTHETIC" in str(data.get("limitation", "")):
                continue
            p = str(data.get("provider", "")).lower().strip()
            p_slug = str(data.get("toolkit_slug", "") or data.get("toolkit", "")).lower().strip()
            p_domain = str(data.get("domain", "")).lower().strip()

            # Match on canonical provider name, toolkit slug, or dedicated domain
            matched = False
            if p and p in targets:
                matched = True
            elif p_slug and p_slug in targets:
                matched = True
            elif p_domain and p_domain in targets:
                matched = True

            if matched:
                results.append(Attempt(**data))
        except Exception:
            continue
    return results


async def run_pipeline(
    app_name: str,
    feature: str = "",
    audience: str = "external customers",
    deployment: str = "server-side",
    website: str = "",
    force: bool = False,
    store: Optional[Store] = None,
    on_event: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[GuideRecord], str]:
    """Run the full pipeline for one guide generation.

    Returns (guide_record, error_message).
    """
    start_time = time.time()
    deadline = start_time + JOB_TIMEOUT_SECONDS

    def event(msg: str):
        if on_event:
            on_event(msg)

    # 1. Validate input
    if not app_name.strip() and not website.strip():
        return None, "An app name or website is required"

    # 2. Resolve product identity
    event("Resolving product identity")
    provider, candidates, resolver_stats = await resolve_provider_with_model(app_name, website)

    # Check for ambiguous names per §4.2
    if candidates:
        return None, f"needs_input:ambiguous:{json.dumps(candidates)}"

    scenario = Scenario(
        feature=feature or "",
        audience=audience,
        deployment=deployment,
        assumptions=[],
    )
    if not feature:
        scenario.assumptions.append("Feature: general OAuth integration (read-only if appropriate)")

    # 3. Check cache (assumed identities skip cache per §6.7)
    guide_id = make_guide_id(provider.name, scenario, provider.official_domain)
    cache_key = make_cache_key(provider.name, provider.official_domain, scenario)

    if store and not force:
        if provider.identity_status == "assumed":
            event("Assumed identity — skipping cache")
        else:
            cached = store.check_cache(cache_key, CACHE_MAX_AGE_HOURS)
            if cached and cached.status != GuideStatus.partial.value:
                event("Cache hit — returning existing guide")
                return cached, ""

    # 4. Composio toolkit lookup (optional, non-blocking)
    event("Looking up Composio toolkit")
    try:
        composio_info, composio_sources = await asyncio.wait_for(
            lookup_composio_toolkit(app_name, provider.name, provider.official_domain),
            timeout=max(1.0, min(15.0, deadline - time.time()))
        )
    except (asyncio.TimeoutError, Exception):
        composio_info = ComposioInfo(
            lookup_status=ComposioLookupStatus.failed.value,
            managed_oauth=ManagedOAuth.unknown.value,
        )
        composio_sources = []
        event("Composio lookup timed out or failed — continuing")

    # Load recorded setup attempts
    attempts = _load_attempts_for_provider(provider.name, provider.official_domain)

    # 5. Research
    event("Starting research phase")
    research_deadline = start_time + (JOB_TIMEOUT_SECONDS * 0.55)

    findings, research_sources, research_stats, page_texts = await run_research(
        provider=provider,
        scenario=scenario,
        composio=composio_info,
        deadline=min(research_deadline, deadline),
        on_event=on_event,
    )

    if "error" in research_stats:
        if not findings.strip():
            return None, f"Research failed: {research_stats['error']}"
        # If findings exist, keep them, set research_timed_out=True, and save guide as partial
        research_stats["research_timed_out"] = True

    # If research successfully read pages from the official provider domain, corroborate identity to resolved
    if provider.identity_status == "assumed" and provider.official_domain:
        from app.resolver import SHARED_COMPANY_DOMAINS
        p_name_lower = provider.name.lower()
        is_shared = provider.official_domain.lower() in SHARED_COMPANY_DOMAINS

        has_corroborating_page = False
        for s in research_sources:
            if s.provider_domain and s.retrieval_status == "read":
                if not is_shared:
                    has_corroborating_page = True
                    break
                # On shared company domains, the page must specifically corroborate this product
                s_title = (s.title or "").lower()
                s_text = page_texts.get(s.id, "").lower()
                if p_name_lower in s_title or p_name_lower in s_text:
                    has_corroborating_page = True
                    break

        if has_corroborating_page:
            provider.identity_status = "resolved"

    # Merge sources
    all_sources = composio_sources + research_sources

    if not findings.strip():
        return None, "Research produced no findings"

    # 6. Synthesis
    event("Synthesizing guide from findings")
    guide_content, synthesis_stats = await run_synthesis(
        provider=provider,
        scenario=scenario,
        composio=composio_info,
        sources=all_sources,
        attempts=attempts,
        findings=findings,
        deadline=deadline,
        research_timed_out=research_stats.get("research_timed_out", False),
    )

    if guide_content is None:
        return None, f"Synthesis failed: {synthesis_stats.get('error', 'unknown error')}"

    # 7. Assemble and validate
    event("Validating guide")
    end_time = time.time()

    total_model_calls = (
        resolver_stats.get("model_calls", 0)
        + research_stats.get("model_calls", 0)
        + synthesis_stats.get("model_calls", 0)
    )
    total_input = (
        resolver_stats.get("tokens_input", 0)
        + research_stats.get("tokens_input", 0)
        + synthesis_stats.get("tokens_input", 0)
    )
    total_output = (
        resolver_stats.get("tokens_output", 0)
        + research_stats.get("tokens_output", 0)
        + synthesis_stats.get("tokens_output", 0)
    )

    # Reset model-supplied review/excerpt fields before running validation
    for claim in guide_content.claims:
        claim.excerpt_status = "not_checked"
        claim.review_result = "not_reviewed"
        claim.support_note = ""

    guide = GuideRecord(
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        id=guide_id,
        cache_key=cache_key,
        provider=provider,
        scenario=scenario,
        composio=composio_info,
        guide=guide_content,
        sources=all_sources,
        attempts=attempts,
        run=RunStats(
            model_calls=total_model_calls,
            searches=research_stats.get("searches", 0),
            page_reads=research_stats.get("page_reads", 0),
            fetch_attempts=research_stats.get("fetch_attempts", 0),
            tokens_input=total_input,
            tokens_output=total_output,
            cost="unavailable",
            cost_basis="Price not configured for cost calculation",
            model=research_stats.get("model") or get_active_model_name(),
            duration_seconds=round(end_time - start_time, 1),
            research_timed_out=research_stats.get("research_timed_out", False),
        ),
    )

    # Run validation (§6.5) with page_texts for excerpt checking
    warnings = validate_guide(guide, page_texts=page_texts)
    if resolver_stats.get("warning"):
        guide.warnings.append(resolver_stats["warning"])

    # 8. Save
    if store:
        event("Saving guide")
        version = store.save_guide(guide)
        event(f"Saved as version {version}")

    return guide, ""
