"""Product identity resolution and Composio toolkit matching per §7.6 and §8."""

from __future__ import annotations

import json
import os
import re
import inspect
from datetime import datetime, timezone
import httpx
import tldextract

from app.config import COMPOSIO_API_KEY, COMPOSIO_API_HOST
from app.schema import ComposioInfo, ProviderInfo, ManagedOAuth, ComposioLookupStatus, Source
from app.llm_gemini import generate

SHARED_COMPANY_DOMAINS: set[str] = {
    "google.com",
    "atlassian.com",
    "microsoft.com",
    "cisco.com",
    "salesforce.com",
    "zoho.com",
}


async def _close_client(client) -> None:
    """Safely close client, handling both real AsyncAnthropic and MagicMock test fixtures."""
    if client is not None:
        close_func = getattr(client, "close", None)
        if callable(close_func):
            res = close_func()
            if inspect.isawaitable(res):
                await res

# Bundled suffix list only (no network on first use)
_extractor = tldextract.TLDExtract(suffix_list_urls=())

# Small alias map for common toolkit slugs (no guesses)
TOOLKIT_ALIASES: dict[str, str] = {
    "github": "github",
    "slack": "slack",
    "gmail": "gmail",
    "google mail": "gmail",
    "google calendar": "googlecalendar",
    "gcal": "googlecalendar",
    "google drive": "googledrive",
    "gdrive": "googledrive",
    "drive": "googledrive",
    "google sheets": "googlesheets",
    "gsheets": "googlesheets",
    "hubspot": "hubspot",
    "salesforce": "salesforce",
    "jira": "jira",
    "notion": "notion",
    "dropbox": "dropbox",
    "outlook": "outlook",
    "zoom": "zoom",
    "asana": "asana",
    "trello": "trello",
    "linear": "linear",
    "discord": "discord",
    "twitter": "twitter",
    "x": "twitter",
    "xero": "xero",
}


def parse_domain(input_str: str) -> tuple[str, str]:
    """Parse a registrable domain from a URL or domain string.

    Returns (registrable_domain, full_hostname).
    Uses tldextract with bundled suffix list (no network).
    """
    if "://" not in input_str and "." in input_str:
        input_str = "https://" + input_str

    ext = _extractor(input_str)
    registrable = f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain
    full_hostname = ".".join(part for part in [ext.subdomain, ext.domain, ext.suffix] if part)
    return registrable, full_hostname


# Ambiguous names that could match multiple products per §4.2
AMBIGUOUS_NAMES: dict[str, list[dict[str, str]]] = {
    "box": [
        {"name": "Box", "domain": "box.com"},
        {"name": "Dropbox", "domain": "dropbox.com"},
    ],
    "monday": [
        {"name": "monday.com (Work OS)", "domain": "monday.com"},
        {"name": "Monday Hero", "domain": "mondayhero.io"},
    ],
    "teams": [
        {"name": "Microsoft Teams", "domain": "microsoft.com"},
        {"name": "Webex Teams (Cisco)", "domain": "webex.com"},
    ],
}


def resolve_provider(app_name: str, website: str = "") -> tuple[ProviderInfo, list[dict] | None]:
    """Resolve product identity from name and optional website.

    Returns (ProviderInfo, candidates_or_None).
    """
    name = app_name.strip()
    domain = ""
    identity_status = "assumed"

    if website:
        registrable, _ = parse_domain(website)
        domain = registrable
        identity_status = "assumed"

    # Well-known products
    known = {
        "github": ("GitHub", "github.com"),
        "slack": ("Slack", "slack.com"),
        "gmail": ("Gmail (Google)", "google.com"),
        "google": ("Google", "google.com"),
        "hubspot": ("HubSpot", "hubspot.com"),
        "salesforce": ("Salesforce", "salesforce.com"),
        "notion": ("Notion", "notion.so"),
        "jira": ("Jira (Atlassian)", "atlassian.com"),
        "dropbox": ("Dropbox", "dropbox.com"),
        "zoom": ("Zoom", "zoom.us"),
        "asana": ("Asana", "asana.com"),
        "trello": ("Trello (Atlassian)", "atlassian.com"),
        "linear": ("Linear", "linear.app"),
        "discord": ("Discord", "discord.com"),
        "twitter": ("X (Twitter)", "x.com"),
        "x": ("X (Twitter)", "x.com"),
        "microsoft": ("Microsoft", "microsoft.com"),
        "outlook": ("Outlook (Microsoft)", "microsoft.com"),
        "xero": ("Xero", "xero.com"),
    }

    lower = name.lower().strip()

    # Ambiguity check (only when no website disambiguates)
    if lower in AMBIGUOUS_NAMES and not domain:
        candidates = AMBIGUOUS_NAMES[lower]
        if len(candidates) == 1:
            c = candidates[0]
            return ProviderInfo(
                name=c["name"],
                official_domain=c["domain"],
                identity_status="assumed",
            ), None
        return ProviderInfo(
            name=name,
            official_domain="",
            identity_status="assumed",
        ), candidates

    if lower in known and not domain:
        resolved_name, resolved_domain = known[lower]
        return ProviderInfo(
            name=resolved_name,
            official_domain=resolved_domain,
            identity_status="resolved",
        ), None

    if domain:
        looks_like_domain = "." in lower and " " not in lower
        is_domain_only = (not lower) or (looks_like_domain and parse_domain(lower)[0].lower() == domain.lower())

        # If user explicitly provided a known product name matching the domain
        if lower in known and known[lower][1].lower() == domain.lower():
            resolved_name, resolved_domain = known[lower]
            return ProviderInfo(name=resolved_name, official_domain=resolved_domain, identity_status="resolved"), None

        # Domain-only input: collect all matching known products
        if is_domain_only:
            domain_matches = [(k, v) for k, v in known.items() if v[1].lower() == domain.lower()]
            if domain.lower() in SHARED_COMPANY_DOMAINS or len(domain_matches) > 1:
                candidates = [{"name": v[0], "domain": v[1]} for k, v in domain_matches]
                return ProviderInfo(name=name, official_domain=domain, identity_status="assumed"), candidates
            elif len(domain_matches) == 1:
                resolved_name, resolved_domain = domain_matches[0][1]
                return ProviderInfo(name=resolved_name, official_domain=resolved_domain, identity_status="resolved"), None

        # Otherwise keep the user's name with the website domain
        return ProviderInfo(name=name, official_domain=domain, identity_status="assumed"), None

    return ProviderInfo(
        name=name,
        official_domain=domain or "",
        identity_status=identity_status,
    ), None


async def resolve_provider_with_model(
    app_name: str,
    website: str = "",
) -> tuple[ProviderInfo, list[dict] | None, dict]:
    """Resolve product identity, using a fast model call for unknown apps.

    Returns (provider_info, candidates, run_stats).
    Identity returned from a model call alone is ALWAYS 'assumed', never 'resolved',
    until corroborated by official provider pages during research.
    """
    stats = {"model_calls": 0, "tokens_input": 0, "tokens_output": 0}
    info, candidates = resolve_provider(app_name, website)
    if info.identity_status == "resolved" or candidates:
        return info, candidates, stats

    user_domain = ""
    if website.strip():
        user_domain, _ = parse_domain(website.strip())

    import sys
    from app.config import ANTHROPIC_API_KEY, RESEARCH_MODEL, LLM_PROVIDER, get_active_provider
    llm = get_active_provider(sys.modules[__name__])

    if llm == "gemini":
        from app.config import GEMINI_API_KEY
        if os.getenv("GEMINI_API_KEY", GEMINI_API_KEY) and app_name.strip():
            try:
                resolver_schema = {
                    "type": "object",
                    "properties": {
                        "canonical_name": {"type": "string"},
                        "official_domain": {"type": "string"},
                        "is_ambiguous": {"type": "boolean"},
                        "candidates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "domain": {"type": "string"},
                                },
                                "required": ["name", "domain"],
                            },
                        },
                    },
                    "required": ["canonical_name", "official_domain", "is_ambiguous"],
                }
                system_prompt = (
                    "You identify the official registrable domain and canonical name for software products. "
                    "Return ONLY valid JSON matching the schema."
                )
                resp, usage = await generate(
                    contents=[f"App name: {app_name}, Website: {website}"],
                    system=system_prompt,
                    json_schema=resolver_schema,
                    timeout_s=15.0,
                    max_output_tokens=256,
                )
                stats["model_calls"] = 1
                stats["tokens_input"] = usage.get("prompt_token_count", 0)
                stats["tokens_output"] = usage.get("candidates_token_count", 0)

                raw = (resp.text or "").strip()
                if raw.startswith("```"):
                    parts = raw.split("```")
                    if len(parts) >= 2:
                        raw = parts[1]
                        if raw.startswith("json"):
                            raw = raw[4:]
                data = json.loads(raw.strip())

                if data.get("is_ambiguous") and data.get("candidates") and not user_domain:
                    return ProviderInfo(name=app_name, official_domain="", identity_status="assumed"), data["candidates"], stats

                c_name = data.get("canonical_name") or app_name
                c_domain = data.get("official_domain") or ""
                reg = ""
                if c_domain:
                    reg, _ = parse_domain(c_domain)

                if user_domain:
                    resolved_name = c_name
                    if reg and reg.lower() != user_domain.lower():
                        stats["warning"] = f"Using user-specified domain '{user_domain}'; model suggested alternative '{c_name}' on '{reg}'"
                        resolved_name = app_name
                    return ProviderInfo(name=resolved_name, official_domain=user_domain, identity_status="assumed"), None, stats

                return ProviderInfo(name=c_name, official_domain=reg or "", identity_status="assumed"), None, stats
            except Exception:
                pass

    if ANTHROPIC_API_KEY and app_name.strip():
        client = None
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=RESEARCH_MODEL,
                max_tokens=256,
                system=(
                    "You identify the official registrable domain and canonical name for software products. "
                    "Return ONLY valid JSON with keys: 'canonical_name' (str), 'official_domain' (str, e.g. 'linear.app'), "
                    "'is_ambiguous' (bool), 'candidates' (list of {'name': str, 'domain': str} if ambiguous)."
                ),
                messages=[{"role": "user", "content": f"App name: {app_name}, Website: {website}"}],
                timeout=8.0,
            )
            stats["model_calls"] = 1
            if hasattr(resp, "usage") and resp.usage:
                stats["tokens_input"] = getattr(resp.usage, "input_tokens", 0)
                stats["tokens_output"] = getattr(resp.usage, "output_tokens", 0)

            for b in resp.content:
                if getattr(b, "type", "") == "text" and b.text:
                    raw = b.text.strip()
                    if raw.startswith("```"):
                        raw = raw.split("```")[1]
                        if raw.startswith("json"):
                            raw = raw[4:]
                    data = json.loads(raw.strip())
                    # When website is provided, never return candidates; keep user's domain
                    if data.get("is_ambiguous") and data.get("candidates") and not user_domain:
                        return ProviderInfo(name=app_name, official_domain="", identity_status="assumed"), data["candidates"], stats

                    c_name = data.get("canonical_name") or app_name
                    c_domain = data.get("official_domain") or ""
                    reg = ""
                    if c_domain:
                        reg, _ = parse_domain(c_domain)

                    if user_domain:
                        resolved_name = c_name
                        if reg and reg.lower() != user_domain.lower():
                            stats["warning"] = f"Using user-specified domain '{user_domain}'; model suggested alternative '{c_name}' on '{reg}'"
                            resolved_name = app_name  # Retain user's coherent name/domain pair!
                        return ProviderInfo(name=resolved_name, official_domain=user_domain, identity_status="assumed"), None, stats

                    # Bare model JSON can only establish 'assumed' identity per §4 / R4
                    return ProviderInfo(name=c_name, official_domain=reg or "", identity_status="assumed"), None, stats
        except Exception:
            pass
        finally:
            if client is not None:
                await _close_client(client)

    return info, None, stats


KNOWN_SLUG_DOMAINS: dict[str, str] = {
    "dropbox": "dropbox.com",
    "box": "box.com",
    "github": "github.com",
    "slack": "slack.com",
    "gmail": "google.com",
    "googlecalendar": "google.com",
    "googledrive": "google.com",
    "googlesheets": "google.com",
    "hubspot": "hubspot.com",
    "salesforce": "salesforce.com",
    "jira": "atlassian.com",
    "trello": "atlassian.com",
    "notion": "notion.so",
    "outlook": "microsoft.com",
    "zoom": "zoom.us",
    "asana": "asana.com",
    "linear": "linear.app",
    "discord": "discord.com",
    "twitter": "x.com",
    "xero": "xero.com",
}


def _slug_domain_conflicts(slug: str, official_domain: str) -> bool:
    """Check if a known slug's domain conflicts with the requested official domain."""
    if not official_domain or not slug:
        return False
    expected = KNOWN_SLUG_DOMAINS.get(slug.lower())
    if expected:
        return expected.lower() != official_domain.lower()
    return False


async def lookup_composio_toolkit(
    app_name: str,
    provider_name: str = "",
    official_domain: str = "",
) -> tuple[ComposioInfo, list[Source]]:
    """Look up a Composio toolkit via REST API per §8."""
    sources: list[Source] = []

    if not COMPOSIO_API_KEY:
        return ComposioInfo(
            lookup_status=ComposioLookupStatus.not_attempted.value,
            managed_oauth=ManagedOAuth.unknown.value,
        ), sources

    # Check original input first, then resolved provider name
    slug = _find_slug(app_name) or _find_slug(provider_name)
    if slug and official_domain and _slug_domain_conflicts(slug, official_domain):
        slug = None

    if not slug:
        # Search API with corroboration
        try:
            query = app_name or provider_name
            slug = await _search_composio(query, official_domain)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ComposioInfo(
                    lookup_status=ComposioLookupStatus.no_match.value,
                    managed_oauth=ManagedOAuth.unknown.value,
                ), sources
            return ComposioInfo(
                lookup_status=ComposioLookupStatus.failed.value,
                managed_oauth=ManagedOAuth.unknown.value,
            ), sources
        except Exception:
            return ComposioInfo(
                lookup_status=ComposioLookupStatus.failed.value,
                managed_oauth=ManagedOAuth.unknown.value,
            ), sources

    if not slug:
        return ComposioInfo(
            lookup_status=ComposioLookupStatus.no_match.value,
            managed_oauth=ManagedOAuth.unknown.value,
        ), sources

    try:
        info, detail_source = await _get_toolkit_details(slug)
        if detail_source:
            sources.append(detail_source)
        return info, sources
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return ComposioInfo(
                toolkit_slug=slug,
                lookup_status=ComposioLookupStatus.no_match.value,
                managed_oauth=ManagedOAuth.unknown.value,
            ), sources
        return ComposioInfo(
            toolkit_slug=slug,
            lookup_status=ComposioLookupStatus.failed.value,
            managed_oauth=ManagedOAuth.unknown.value,
        ), sources
    except Exception:
        return ComposioInfo(
            toolkit_slug=slug,
            lookup_status=ComposioLookupStatus.failed.value,
            managed_oauth=ManagedOAuth.unknown.value,
        ), sources


def _find_slug(name: str) -> str | None:
    """Find a toolkit slug from the alias map."""
    lower = name.lower().strip()
    return TOOLKIT_ALIASES.get(lower)


def _is_conflicting_sibling(query: str, item: dict) -> bool:
    """Return True if query and item represent conflicting sibling products.

    Sibling conflict occurs when query and candidate share a common company/brand token
    (or domain) but specify differing distinctive product terms
    (e.g. 'Salesforce Sales Cloud' vs 'Salesforce Marketing Cloud',
          'Zoho CRM' vs 'Zoho Books',
          'Acme Analytics' vs 'Acme Mail',
          'Google Drive' vs 'Google Calendar').
    """
    query_lower = query.lower().strip()
    item_name = (item.get("name") or "").lower().strip()
    item_slug = (item.get("slug") or "").lower().strip()

    if query_lower == item_name or query_lower == item_slug:
        return False
    query_clean = re.sub(r'[^a-z0-9]', '', query_lower)
    name_clean = re.sub(r'[^a-z0-9]', '', item_name)
    slug_clean = re.sub(r'[^a-z0-9]', '', item_slug)
    if query_clean and (query_clean == name_clean or query_clean == slug_clean):
        return False
    if TOOLKIT_ALIASES.get(query_lower) == item_slug:
        return False

    STOPWORDS = {"app", "apps", "cloud", "software", "api", "integration", "tool", "toolkit", "oauth", "the", "for", "and", "by", "platform", "suite"}
    q_tokens = {w for w in re.findall(r'[a-z0-9]+', query_lower) if w not in STOPWORDS}
    m_tokens = {w for w in re.findall(r'[a-z0-9]+', item_name) if w not in STOPWORDS}
    s_tokens = {w for w in re.findall(r'[a-z0-9]+', item_slug) if w not in STOPWORDS}
    item_tokens = m_tokens | s_tokens

    if not q_tokens or not item_tokens:
        return False

    shared = q_tokens & item_tokens
    q_diff = q_tokens - item_tokens
    item_diff = item_tokens - q_tokens

    # Conflicting sibling: they share a brand/company name, but have conflicting distinctive product tokens
    if shared and q_diff and item_diff:
        return True

    return False


def _has_positive_product_evidence(query: str, item: dict, official_domain: str = "") -> bool:
    """Check if there is positive product evidence connecting query to item.

    A shared company domain alone is not sufficient; there must be positive
    name, slug, alias, or distinctive product token correspondence.
    """
    query_lower = query.lower().strip()
    item_name = (item.get("name") or "").lower().strip()
    item_slug = (item.get("slug") or "").lower().strip()

    if query_lower in (item_name, item_slug):
        return True
    if TOOLKIT_ALIASES.get(query_lower) == item_slug:
        return True

    query_clean = re.sub(r'[^a-z0-9]', '', query_lower)
    name_clean = re.sub(r'[^a-z0-9]', '', item_name)
    slug_clean = re.sub(r'[^a-z0-9]', '', item_slug)
    if query_clean and (query_clean == name_clean or query_clean == slug_clean):
        return True

    STOPWORDS = {"app", "apps", "cloud", "software", "api", "integration", "tool", "toolkit", "oauth", "the", "for", "and", "by", "platform", "suite"}
    q_tokens = {w for w in re.findall(r'[a-z0-9]+', query_lower) if w not in STOPWORDS}
    m_tokens = {w for w in re.findall(r'[a-z0-9]+', item_name) if w not in STOPWORDS}
    s_tokens = {w for w in re.findall(r'[a-z0-9]+', item_slug) if w not in STOPWORDS}
    item_tokens = m_tokens | s_tokens

    # Check for distinctive token overlap
    shared = q_tokens & item_tokens
    is_shared_domain = official_domain.lower() in SHARED_COMPANY_DOMAINS if official_domain else False

    if is_shared_domain:
        # On shared company domains (google.com, salesforce.com, etc.), generic company tokens (e.g. 'google', 'salesforce')
        # do not count as positive product evidence for a specific tool.
        COMPANY_TOKENS = {"google", "salesforce", "atlassian", "microsoft", "cisco", "zoho"}
        distinctive_shared = shared - COMPANY_TOKENS
        return len(distinctive_shared) > 0

    # On non-shared domains, token overlap or matching domain root is positive evidence
    if shared:
        return True
    if official_domain:
        domain_root = parse_domain(official_domain)[0].split('.')[0]
        if domain_root and domain_root not in STOPWORDS and domain_root in q_tokens:
            return True

    return False


async def _search_composio(query: str, official_domain: str = "") -> str | None:
    """Search Composio API for a toolkit, requiring corroborated matching."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{COMPOSIO_API_HOST}/api/v3/toolkits",
            params={"search": query, "limit": 5},
            headers={"x-api-key": COMPOSIO_API_KEY or ""},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        items = data if isinstance(data, list) else data.get("items", data.get("toolkits", []))
        if not items:
            return None

        query_lower = query.lower().strip()

        # Filter out items whose app_url directly contradicts official_domain
        filtered_items = []
        for item in items:
            meta = item.get("meta", {}) if isinstance(item.get("meta"), dict) else {}
            app_url = meta.get("app_url", "")
            if app_url and official_domain:
                reg, _ = parse_domain(app_url)
                if reg and reg.lower() != official_domain.lower():
                    # Domain contradicts official_domain — cannot match
                    continue
            filtered_items.append(item)

        # 1. Exact or normalized match on name or slug (only from non-contradictory items)
        query_clean = re.sub(r'[^a-z0-9]', '', query_lower)
        for item in filtered_items:
            item_name = (item.get("name") or "").lower().strip()
            item_slug = (item.get("slug") or "").lower().strip()
            name_clean = re.sub(r'[^a-z0-9]', '', item_name)
            slug_clean = re.sub(r'[^a-z0-9]', '', item_slug)
            if (
                query_lower == item_name
                or query_lower == item_slug
                or (query_clean and (query_clean == name_clean or query_clean == slug_clean))
                or TOOLKIT_ALIASES.get(query_lower) == item_slug
            ):
                return item.get("slug")

        # 2. Corroborated domain matching via meta.app_url
        if official_domain:
            domain_matches = []
            is_shared_domain = official_domain.lower() in SHARED_COMPANY_DOMAINS
            for item in filtered_items:
                meta = item.get("meta", {}) if isinstance(item.get("meta"), dict) else {}
                app_url = meta.get("app_url", "")
                if app_url:
                    reg, _ = parse_domain(app_url)
                    if reg and reg.lower() == official_domain.lower():
                        # Exclude conflicting sibling products
                        if _is_conflicting_sibling(query, item):
                            continue
                        # On shared company domains, domain match alone is insufficient: require positive product evidence
                        if is_shared_domain and not _has_positive_product_evidence(query, item, official_domain):
                            continue
                        domain_matches.append(item)

            if len(domain_matches) == 1:
                return domain_matches[0].get("slug")
            elif len(domain_matches) > 1:
                # Ambiguous: multiple matching toolkits on this domain
                return None

        # 3. Whole-word match — accept only if not a conflicting sibling product and positively evidenced
        word_matches = []
        if len(query_lower) >= 3:
            pattern = rf"\b{re.escape(query_lower)}\b"
            for item in filtered_items:
                item_name = (item.get("name") or "").lower()
                item_slug = (item.get("slug") or "").lower().replace("-", " ").replace("_", " ")
                if (
                    re.search(pattern, item_name)
                    or re.search(pattern, item_slug)
                ):
                    if not _is_conflicting_sibling(query, item) and _has_positive_product_evidence(query, item, official_domain):
                        word_matches.append(item)

        if len(word_matches) == 1:
            return word_matches[0].get("slug")

        return None


async def _get_toolkit_details(slug: str) -> tuple[ComposioInfo, Source | None]:
    """Get toolkit details from the Composio API."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{COMPOSIO_API_HOST}/api/v3/toolkits/{slug}",
            headers={"x-api-key": COMPOSIO_API_KEY or ""},
        )
        if resp.status_code == 404:
            return ComposioInfo(
                toolkit_slug=slug,
                lookup_status=ComposioLookupStatus.no_match.value,
                managed_oauth=ManagedOAuth.unknown.value,
            ), None
        resp.raise_for_status()
        data = resp.json()

    now = datetime.now(timezone.utc).isoformat()

    source = Source(
        id="C1",
        url=f"{COMPOSIO_API_HOST}/api/v3/toolkits/{slug}",
        title=f"Composio toolkit: {slug}",
        kind="composio_api",
        retrieval_status="read",
        provider_domain=False,
        provider_domain_status="third_party",
        retrieval_time=now,
    )

    raw_managed = data.get("composio_managed_auth_schemes")
    raw_configs = data.get("auth_config_details")

    # If required auth metadata is absent or malformed per §8.3:
    if raw_managed is None or raw_configs is None or not isinstance(raw_configs, list) or not isinstance(raw_managed, list):
        return ComposioInfo(
            toolkit_slug=slug,
            lookup_status=ComposioLookupStatus.matched.value,
            selected_auth_method=None,
            managed_oauth=ManagedOAuth.unknown.value,
            checked_at=now,
            source_id="C1",
        ), source

    managed_schemes = raw_managed
    auth_configs = raw_configs

    selected_method = None
    for ac in auth_configs:
        if isinstance(ac, dict):
            mode = ac.get("mode", "").upper()
            if "OAUTH" in mode:
                selected_method = mode
                break

    # Determine managed availability per §8.3:
    # A matched toolkit with no OAuth mode is unavailable
    if not selected_method:
        managed = ManagedOAuth.unavailable.value
    elif managed_schemes and selected_method in [s.upper() for s in managed_schemes if isinstance(s, str)]:
        managed = ManagedOAuth.available.value
    else:
        managed = ManagedOAuth.unavailable.value

    config_fields = []
    connect_fields = []
    for ac in auth_configs:
        if isinstance(ac, dict) and ac.get("mode", "").upper() == (selected_method or ""):
            fields_info = ac.get("fields", {}) if isinstance(ac.get("fields"), dict) else {}
            config_req = fields_info.get("auth_config_creation", {}).get("required", [])
            connect_req = fields_info.get("connected_account_initiation", {}).get("required", [])
            config_fields = [f.get("name", f) if isinstance(f, dict) else f for f in config_req]
            connect_fields = [f.get("name", f) if isinstance(f, dict) else f for f in connect_req]
            break

    auth_guide_url = data.get("auth_guide_url")

    return ComposioInfo(
        toolkit_slug=slug,
        lookup_status=ComposioLookupStatus.matched.value,
        selected_auth_method=selected_method,
        managed_oauth=managed,
        required_config_fields=config_fields,
        required_connect_fields=connect_fields,
        auth_guide_url=auth_guide_url,
        checked_at=now,
        source_id="C1",
    ), source
