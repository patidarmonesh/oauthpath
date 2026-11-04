"""Tests for the Gemini LLM provider path and Tavily web tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types
from google.genai.errors import APIError

from app.schema import ProviderInfo, Scenario, ComposioInfo, DraftGuideContent, Source
from app.research import run_research_gemini
from app.synthesis import run_synthesis_gemini, GUIDE_SCHEMA
from app.resolver import resolve_provider_with_model
from app.llm_gemini import generate, RateLimitExceeded
from app import web_tools


# ── 1. Research Loop: Search -> Read -> Text ───────────────────────────────

@pytest.mark.anyio
async def test_gemini_research_loop_search_then_read_then_text():
    """Gemini research loop executes 1 search, 1 read, and returns synthesized findings."""
    doc_url = "https://docs.github.com/en/apps"
    provider = ProviderInfo(name="GitHub", official_domain="github.com", identity_status="assumed")
    scenario = Scenario(feature="issues", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    # Turn 1: model calls web_search
    fc1 = types.FunctionCall(name="web_search", args={"query": "github oauth apps"})
    cand1 = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc1)]))
    resp1 = types.GenerateContentResponse(
        candidates=[cand1],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=50, candidates_token_count=10),
    )

    # Turn 2: model calls read_page
    fc2 = types.FunctionCall(name="read_page", args={"url": doc_url})
    cand2 = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc2)]))
    resp2 = types.GenerateContentResponse(
        candidates=[cand2],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=10),
    )

    # Turn 3: model outputs final text findings
    findings_md = (
        "## Findings\n"
        f'F1. GitHub supports OAuth apps — S1 — "GitHub Apps use OAuth 2.0 to access repositories"\n\n'
        "## Conflicts\nNone.\n\n"
        "## Unknowns\nNone."
    )
    cand3 = types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text=findings_md)]))
    resp3 = types.GenerateContentResponse(
        candidates=[cand3],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=150, candidates_token_count=40),
    )

    with patch("app.research.generate", AsyncMock(side_effect=[(resp1, {"prompt_token_count": 50, "candidates_token_count": 10}),
                                                               (resp2, {"prompt_token_count": 100, "candidates_token_count": 10}),
                                                               (resp3, {"prompt_token_count": 150, "candidates_token_count": 40})])):
        with patch("app.web_tools.search", AsyncMock(return_value=[{
            "title": "GitHub App Docs",
            "url": doc_url,
            "content": "GitHub Apps use OAuth 2.0 to access repositories",
            "published_date": "2026-01-01",
        }])):
            with patch("app.web_tools.extract", AsyncMock(return_value=(
                {doc_url: "Full documentation text: GitHub Apps use OAuth 2.0 to access repositories"},
                {}
            ))):
                with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key", "TAVILY_API_KEY": "fake_key"}):
                    events = []
                    findings, sources, stats, page_texts = await run_research_gemini(
                        provider, scenario, composio, deadline=9999999999.0, on_event=events.append
                    )

    assert "F1. GitHub supports OAuth apps" in findings
    assert len(sources) == 1
    assert sources[0].id == "S1"
    assert sources[0].url == doc_url
    assert sources[0].retrieval_status == "read"
    assert sources[0].kind == "fetched_page"
    assert "S1" in page_texts
    assert "Full documentation text" in page_texts["S1"]
    assert stats["searches"] == 1
    assert stats["page_reads"] == 1
    assert stats["fetch_attempts"] == 1
    assert stats["model_calls"] == 3


# ── 2. Failed Read Handling ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_gemini_research_failed_read():
    """When a read_page call fails extraction, the source is marked failed."""
    doc_url = "https://example.com/protected"
    provider = ProviderInfo(name="Example", official_domain="example.com", identity_status="assumed")
    scenario = Scenario(feature="sync", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    fc1 = types.FunctionCall(name="web_search", args={"query": "example oauth"})
    cand1 = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc1)]))
    resp1 = types.GenerateContentResponse(candidates=[cand1])

    fc2 = types.FunctionCall(name="read_page", args={"url": doc_url})
    cand2 = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc2)]))
    resp2 = types.GenerateContentResponse(candidates=[cand2])

    cand3 = types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="## Findings\nF1. Read failed")]))
    resp3 = types.GenerateContentResponse(candidates=[cand3])

    with patch("app.research.generate", AsyncMock(side_effect=[(resp1, {}), (resp2, {}), (resp3, {})])):
        with patch("app.web_tools.search", AsyncMock(return_value=[{
            "title": "Protected Docs",
            "url": doc_url,
            "content": "Snippet only",
        }])):
            with patch("app.web_tools.extract", AsyncMock(return_value=(
                {},
                {doc_url: "HTTP 403 Forbidden"}
            ))):
                with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key", "TAVILY_API_KEY": "fake_key"}):
                    findings, sources, stats, page_texts = await run_research_gemini(
                        provider, scenario, composio, deadline=9999999999.0
                    )

    assert len(sources) == 1
    assert sources[0].retrieval_status == "failed"
    assert "Failed fetch" in sources[0].title
    assert doc_url not in page_texts
    assert stats["page_reads"] == 0
    assert stats["fetch_attempts"] == 1


# ── 3. Budget Refusal ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_gemini_research_budget_refusal():
    """Calls exceeding search budget return an explicit error payload."""
    provider = ProviderInfo(name="Example", official_domain="example.com", identity_status="assumed")
    scenario = Scenario(feature="sync", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    # Turn 1: model returns two search calls in the same turn
    fc1 = types.FunctionCall(name="web_search", args={"query": "search 1"})
    fc2 = types.FunctionCall(name="web_search", args={"query": "search 2"})
    resp1 = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc1), types.Part(function_call=fc2)]))])

    # Turn 2: final wrap-up text
    resp2 = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="Done.")]))])

    captured_contents = []

    async def mock_generate(contents, **kwargs):
        captured_contents.append(list(contents))
        if len(captured_contents) == 1:
            return resp1, {}
        return resp2, {}

    with patch("app.research.generate", side_effect=mock_generate):
        with patch("app.research.MAX_SEARCHES", 1):
            with patch("app.web_tools.search", AsyncMock(return_value=[])):
                with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key", "TAVILY_API_KEY": "fake_key"}):
                    findings, sources, stats, _ = await run_research_gemini(
                        provider, scenario, composio, deadline=9999999999.0
                    )

    # In turn 1 responses sent back to the model, part 1 must report search budget exhausted
    user_turn = captured_contents[1][2]
    assert user_turn.parts[1].function_response.response == {"error": "search budget exhausted"}


# ── 4. Refuse Unseen URLs for read_page ────────────────────────────────────

@pytest.mark.anyio
async def test_gemini_research_read_page_refuses_unseen_urls():
    """read_page refuses URLs that never appeared in search results or allowed list."""
    provider = ProviderInfo(name="Example", official_domain="example.com", identity_status="assumed")
    scenario = Scenario(feature="sync", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    fc = types.FunctionCall(name="read_page", args={"url": "https://unseen-portal.com/private"})
    resp1 = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))])
    resp2 = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="Refused.")]))])

    captured_contents = []

    async def mock_generate(contents, **kwargs):
        captured_contents.append(list(contents))
        if len(captured_contents) == 1:
            return resp1, {}
        return resp2, {}

    with patch("app.research.generate", side_effect=mock_generate):
        with patch("app.web_tools.extract", AsyncMock()) as mock_extract:
            with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key", "TAVILY_API_KEY": "fake_key"}):
                findings, sources, stats, _ = await run_research_gemini(
                    provider, scenario, composio, deadline=9999999999.0
                )

    mock_extract.assert_not_called()
    last_turn = captured_contents[1][-1]
    assert last_turn.parts[0].function_response.response == {"error": "URL not from search results"}


# ── 5. Synthesis: Truncated then Retried ────────────────────────────────────

@pytest.mark.anyio
async def test_gemini_synthesis_truncated_then_retried():
    """When output finish_reason is MAX_TOKENS on first attempt, retry requests more concise JSON."""
    provider = ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved")
    scenario = Scenario(feature="test", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    cand1 = types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text='{"oauth_support": "confirmed"')]),
        finish_reason=types.FinishReason.MAX_TOKENS,
    )
    resp1 = types.GenerateContentResponse(candidates=[cand1])

    valid_guide = {
        "oauth_support": "confirmed",
        "summary": [{"text": "OAuth supported", "claim_ids": ["K1"]}],
        "next_action": {
            "instruction": "Register app",
            "owner": "you",
            "link_or_artifact": "https://testapp.com/apps",
            "success_condition": "Client credentials created",
            "claim_ids": ["K1"],
        },
        "recommended_path_id": "own_app_production",
        "paths": [{
            "id": "own_app_production",
            "name": "Production Path",
            "applicability": "Direct API",
            "gates": [{"kind": "app_review", "state": "not_required", "note": "None", "claim_ids": ["K1"]}],
            "prerequisites": [],
            "steps": [{
                "id": "step_1",
                "title": "Register",
                "instructions": "Create credentials",
                "owner": "you",
                "action_url": "https://testapp.com/apps",
                "success_condition": "Done",
                "effort": {"known": True, "low": 5, "high": 10, "unit": "minutes", "basis": "author_estimate", "explanation": "Quick"},
                "claim_ids": ["K1"],
            }],
            "limits": [],
            "costs": [],
            "effort": {"known": True, "low": 5, "high": 10, "unit": "minutes", "basis": "author_estimate", "explanation": "Quick"},
            "waiting": {"known": True, "low": 0, "high": 0, "unit": "business_days", "basis": "author_estimate", "explanation": "Immediate"},
        }],
        "claims": [{
            "id": "K1",
            "text": "OAuth works",
            "conditions": "",
            "basis": "documented",
            "source_ids": ["S1"],
            "attempt_ids": [],
            "excerpt": "OAuth works",
        }],
        "agent_assistance": [],
        "unknowns": [],
        "conflicts": [],
    }

    cand2 = types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(valid_guide))]),
        finish_reason=types.FinishReason.STOP,
    )
    resp2 = types.GenerateContentResponse(candidates=[cand2])

    call_count = 0

    async def mock_generate(contents, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return resp1, {"prompt_token_count": 100, "candidates_token_count": 16384}
        assert "truncated by max_tokens" in contents[0]
        return resp2, {"prompt_token_count": 200, "candidates_token_count": 500}

    with patch("app.synthesis.generate", side_effect=mock_generate):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
            guide, stats = await run_synthesis_gemini(
                provider, scenario, composio,
                sources=[Source(id="S1", url="https://testapp.com/docs", retrieval_status="read")],
                attempts=[], findings="F1. OAuth works", deadline=9999999999.0
            )

    assert guide is not None
    assert guide.oauth_support == "confirmed"
    assert stats["model_calls"] == 2


# ── 6. Synthesis: Schema Rejection Fallback ────────────────────────────────

@pytest.mark.anyio
async def test_gemini_synthesis_schema_rejected_fallback():
    """When Gemini returns a 400 schema error, retry without json_schema and parse plain JSON."""
    provider = ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved")
    scenario = Scenario(feature="test", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    valid_guide = {
        "oauth_support": "confirmed",
        "summary": [{"text": "OAuth supported", "claim_ids": ["K1"]}],
        "next_action": {
            "instruction": "Register app",
            "owner": "you",
            "link_or_artifact": "https://testapp.com/apps",
            "success_condition": "Client credentials created",
            "claim_ids": ["K1"],
        },
        "recommended_path_id": "",
        "paths": [],
        "claims": [{"id": "K1", "text": "OAuth works", "basis": "documented", "source_ids": ["S1"], "attempt_ids": [], "excerpt": ""}],
        "agent_assistance": [],
        "unknowns": [],
        "conflicts": [],
    }

    call_count = 0

    async def mock_generate(contents, json_schema=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert json_schema is not None
            raise RuntimeError("400 INVALID_ARGUMENT: Schema too complex for model")
        # In retry, json_schema must be None
        assert json_schema is None
        cand = types.Candidate(
            content=types.Content(role="model", parts=[types.Part.from_text(text="```json\n" + json.dumps(valid_guide) + "\n```")]),
            finish_reason=types.FinishReason.STOP,
        )
        return types.GenerateContentResponse(candidates=[cand]), {}

    with patch("app.synthesis.generate", side_effect=mock_generate):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
            guide, stats = await run_synthesis_gemini(
                provider, scenario, composio,
                sources=[Source(id="S1", url="https://testapp.com/docs", retrieval_status="read")],
                attempts=[], findings="F1. OAuth works", deadline=9999999999.0
            )

    assert guide is not None
    assert guide.oauth_support == "confirmed"


# ── 7. 429 Retry and Safe Error Handling ───────────────────────────────────

@pytest.mark.anyio
async def test_gemini_429_retry_and_safe_error():
    """HTTP 429 retries once; on persistent 429 raises RateLimitExceeded without secret leakage."""
    secret_key = "mock_confidential_key_12345"

    client = MagicMock()
    err_429 = APIError(429, {"error": {"message": "Resource exhausted: 429 rate limit reached. Key=" + secret_key}})
    client.aio.models.generate_content = AsyncMock(side_effect=err_429)

    with patch("app.llm_gemini.get_gemini_client", return_value=client):
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            with pytest.raises(RateLimitExceeded) as exc_info:
                await generate(["Hello"], model="gemini-3.6-flash")

    # Slept once before retry
    mock_sleep.assert_called_once()
    # Message is safe and masks the secret
    assert "Free-tier model limit reached" in str(exc_info.value)
    assert secret_key not in str(exc_info.value)


# ── 8. Resolver with Gemini ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_gemini_resolver_with_model():
    """Resolver calls Gemini structured schema and returns assumed ProviderInfo."""
    model_resp = {
        "canonical_name": "AcmeTool",
        "official_domain": "acmetool.com",
        "is_ambiguous": False,
        "candidates": [],
    }

    cand = types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(model_resp))]))
    resp = types.GenerateContentResponse(candidates=[cand])

    with patch("app.resolver.generate", AsyncMock(return_value=(resp, {"prompt_token_count": 20, "candidates_token_count": 15}))):
        with patch.dict("os.environ", {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "fake_key"}):
            info, candidates, stats = await resolve_provider_with_model("AcmeTool")

    assert info.name == "AcmeTool"
    assert info.official_domain == "acmetool.com"
    assert info.identity_status == "assumed"
    assert candidates is None
    assert stats["model_calls"] == 1


# ── 9. Tavily Tools: Search & Extract ──────────────────────────────────────

@pytest.mark.anyio
async def test_tavily_web_tools_search_and_extract():
    """Tavily search filters invalid URLs and extract parses both results and failed_results."""
    mock_tavily_client = MagicMock()
    mock_tavily_client.search = AsyncMock(return_value={
        "results": [
            {"title": "Valid Doc", "url": "https://example.com/docs", "content": "Sample content", "published_date": "2026-02-01"},
            {"title": "Insecure Doc", "url": "http://example.com/docs", "content": "Insecure"},
            {"title": "Bad Scheme", "url": "javascript:alert(1)", "content": "XSS"},
        ]
    })
    mock_tavily_client.extract = AsyncMock(return_value={
        "results": [
            {"url": "https://example.com/docs", "raw_content": "# Documentation\nFull page content"},
        ],
        "failed_results": [
            {"url": "https://example.com/404", "error": "Not Found"},
        ]
    })

    with patch("app.web_tools.get_tavily_client", return_value=mock_tavily_client):
        # 1. Search
        search_res = await web_tools.search("example oauth")
        assert len(search_res) == 1
        assert search_res[0]["url"] == "https://example.com/docs"

        # 2. Extract
        success_map, failure_map = await web_tools.extract(["https://example.com/docs", "https://example.com/404"])
        assert "https://example.com/docs" in success_map
        assert "Documentation" in success_map["https://example.com/docs"]
        assert "https://example.com/404" in failure_map
        assert "Not Found" in failure_map["https://example.com/404"]


# ── 10. Health Endpoint and Safe Error Mapping ────────────────────────────

def test_health_endpoint_reports_gemini():
    """Health endpoint reports provider='gemini' and live_generation status."""
    from starlette.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    with patch.dict("os.environ", {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "fake_gemini", "TAVILY_API_KEY": "fake_tvly"}):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provider"] == "gemini"
        assert data["live_generation"] is True
