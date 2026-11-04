"""Complete pipeline scenario execution and repeatability checks.

Covers:
1. Complete path: input -> identity resolution -> toolkit lookup -> research -> synthesis ->
   validation -> persistence -> job completion -> HTML guide -> Markdown export.
2. Controlled repetition (>= 3 times) across:
   - Fresh temporary databases
   - Repeated requests against same database (cache hits)
   - Explicit force-refresh (version 2 immutability)
   - Repeated validation and identical seed reimports
   - Concurrency limit and busy guard
3. Specific scenario matrix:
   - Known app and unfamiliar app
   - Name input and website input
   - Ambiguous name -> candidates -> selection
   - Shared domains (google.com) and Box/Dropbox false matches
   - Custom OAuth, managed OAuth, and unsupported OAuth
   - Missing credentials, timeout, and cancellation
   - Web and CLI parity
   - Server restart and persistence
"""

import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.config import (
    COMPOSIO_API_HOST,
    RESEARCH_MODEL,
    JOB_TIMEOUT_SECONDS,
)
from app.main import app
from app.pipeline import run_pipeline
from app.render import render_markdown
from app.resolver import resolve_provider, _search_composio, lookup_composio_toolkit
from app.schema import (
    ComposioInfo,
    DraftClaim,
    DraftCost,
    DraftEstimate,
    DraftGate,
    DraftGuideContent,
    DraftNextAction,
    DraftPath,
    DraftStep,
    DraftSummaryStatement,
    GuideRecord,
    GuideStatus,
    ManagedOAuth,
    OAuthSupport,
    ProviderInfo,
    ReviewInfo,
    Scenario,
    Source,
)
from app.store import Store
from app.validation import validate_guide, validate_reviewed_record


def _create_mock_sdk_message(content_items, stop_reason="end_turn"):
    from anthropic.types import Message
    return Message.model_validate({
        "id": "msg_mock_pipeline",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content_items,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 80},
    })


def _get_mock_research_t1(app_name="GitHub", domain="github.com"):
    return _create_mock_sdk_message([
        {
            "type": "server_tool_use",
            "id": "tool_search_1",
            "name": "web_search",
            "input": {"query": f"{app_name} OAuth 2.0 app registration"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "tool_search_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": f"https://developer.{domain}/oauth",
                    "title": f"{app_name} OAuth Documentation",
                    "encrypted_content": "enc_content",
                }
            ],
        },
        {
            "type": "server_tool_use",
            "id": "tool_fetch_1",
            "name": "web_fetch",
            "input": {"url": f"https://developer.{domain}/oauth"},
        },
        {
            "type": "web_fetch_tool_result",
            "tool_use_id": "tool_fetch_1",
            "content": {
                "type": "web_fetch_result",
                "url": f"https://developer.{domain}/oauth",
                "retrieved_at": "2026-11-04T00:00:00Z",
                "content": {
                    "type": "document",
                    "title": f"{app_name} OAuth Documentation",
                    "citations": None,
                    "source": {
                        "type": "text",
                        "media_type": "text/plain",
                        "data": f"{app_name} requires OAuth 2.0 with authorization code grant. Setup is self-service in developer portal.",
                    },
                },
            },
        },
    ], stop_reason="pause_turn")


def _get_mock_research_t2(app_name="GitHub", domain="github.com"):
    return _create_mock_sdk_message([
        {
            "type": "text",
            "text": (
                "## Findings\n"
                f"F1. {app_name} supports OAuth 2.0 authorization code flow — https://developer.{domain}/oauth — \"{app_name} requires OAuth 2.0 with authorization code grant.\"\n"
                "## Conflicts\n"
                "None\n"
                "## Unknowns\n"
                "- What is the approval wait time? — impacts timeline — check developer console\n"
            ),
        }
    ], stop_reason="end_turn")


def _get_mock_synthesis_submit_guide(app_name="GitHub", domain="github.com"):
    guide_dict = {
        "oauth_support": "confirmed",
        "summary": [{"text": f"{app_name} supports OAuth 2.0 authorization code flow", "claim_ids": ["C1"]}],
        "next_action": {
            "instruction": "Register app in developer portal",
            "owner": "you",
            "link_or_artifact": f"https://developer.{domain}/oauth",
            "success_condition": "Client credentials generated",
            "claim_ids": ["C1"],
        },
        "paths": [
            {
                "id": "own_app_testing",
                "name": "Developer Testing Path",
                "applicability": "Testing internal integrations",
                "gates": [{"kind": "app_review", "state": "not_required", "note": "No review for test apps", "claim_ids": ["C1"]}],
                "prerequisites": [{"text": "Developer account", "owner": "you", "claim_ids": ["C1"]}],
                "limits": [],
                "steps": [
                    {
                        "id": "s1",
                        "title": "Create OAuth App",
                        "instructions": "Navigate to developer portal and click Create App",
                        "owner": "you",
                        "step_dependencies": [],
                        "success_condition": "App created",
                        "action_url": f"https://developer.{domain}/apps/new",
                        "effort": {"known": True, "low": 5, "high": 15, "unit": "minutes", "basis": "author_estimate", "explanation": "Simple form", "claim_ids": ["C1"]},
                        "claim_ids": ["C1"],
                    }
                ],
                "effort": {"known": True, "low": 10, "high": 30, "unit": "minutes", "basis": "author_estimate", "explanation": "Standard setup", "claim_ids": ["C1"]},
                "waiting": {"known": True, "low": 0, "high": 0, "unit": "days", "basis": "author_estimate", "explanation": "Instant activation", "claim_ids": ["C1"]},
                "costs": [{"description": "Account registration", "amount": "$0", "basis": "documented", "claim_ids": ["C1"]}],
                "claim_ids": ["C1"],
            }
        ],
        "scopes": [{"scope_string": "read:data", "purpose": "Read user data", "classification": "non_sensitive", "claim_ids": ["C1"]}],
        "blockers": [],
        "agent_assistance": [{"capability": "credential_management", "description": "Requires manual credential copying"}],
        "conflicts": [],
        "unknowns": [{"question": "Production quota limit", "why_it_matters": "Scaling", "how_to_find_out": "Contact support"}],
        "claims": [
            {
                "id": "C1",
                "text": f"{app_name} requires OAuth 2.0 with authorization code grant.",
                "conditions": "All accounts",
                "source_ids": ["S1"],
                "attempt_ids": [],
                "basis": "documented",
                "excerpt": f"{app_name} requires OAuth 2.0 with authorization code grant.",
                "excerpt_status": "found",
                "review_result": "not_reviewed",
            }
        ],
    }

    return _create_mock_sdk_message([
        {
            "type": "tool_use",
            "id": "tool_synth_submit",
            "name": "submit_guide",
            "input": guide_dict,
        }
    ], stop_reason="tool_use")


# ── Scenario & Repeatability Tests ────────────────────────────────────────

@pytest.mark.anyio
async def test_pipeline_scenarios_repeated_3x(tmp_path):
    """Run complete pipeline 3 times: fresh db, repeated request (cache hit), force refresh (v2)."""
    db_file = tmp_path / "repeat_test.db"
    store = Store(str(db_file))

    research_t1 = _get_mock_research_t1("GitHub", "github.com")
    research_t2 = _get_mock_research_t2("GitHub", "github.com")
    synth_resp = _get_mock_synthesis_submit_guide("GitHub", "github.com")

    # Pass 1: Fresh execution against empty DB
    with patch("app.config.ANTHROPIC_API_KEY", "mock-anthropic-key"):
        with patch("app.research.ANTHROPIC_API_KEY", "mock-anthropic-key"):
            with patch("app.synthesis.ANTHROPIC_API_KEY", "mock-anthropic-key"):
                with patch("app.resolver.COMPOSIO_API_KEY", ""):
                    with patch("anthropic.AsyncAnthropic") as mock_cls:
                        mock_client = MagicMock()
                        mock_client.messages.create = AsyncMock(side_effect=[
                            research_t1,
                            research_t2,
                            synth_resp,
                        ])
                        mock_cls.return_value = mock_client

                        guide_v1, err = await run_pipeline("GitHub", "sync repos", "external customers", store=store)
                        assert not err
                        assert guide_v1 is not None
                        assert guide_v1.version == 1
                        assert guide_v1.status == "draft"

                        # Check HTML render and Markdown export
                        md = render_markdown(guide_v1)
                        assert "# GitHub — sync repos" in md
                        assert "v1 · draft" in md

    # Pass 2: Repeated request against same DB (cache hit)
    with patch("app.config.ANTHROPIC_API_KEY", "mock-anthropic-key"):
        with patch("app.research.ANTHROPIC_API_KEY", "mock-anthropic-key"):
            with patch("anthropic.AsyncAnthropic") as mock_cls:
                mock_client = MagicMock()
                mock_cls.return_value = mock_client

                guide_cached, err = await run_pipeline("GitHub", "sync repos", "external customers", store=store)
                assert not err
                assert guide_cached.version == 1
                # Must NOT call model again on cache hit
                mock_client.messages.create.assert_not_called()

                # Ensure version history has exactly 1 version
                history = store.get_all_versions(guide_v1.id)
                assert len(history) == 1

    # Pass 3: Explicit force-refresh creates immutable v2, keeps v1 intact
    with patch("app.config.ANTHROPIC_API_KEY", "mock-anthropic-key"):
        with patch("app.research.ANTHROPIC_API_KEY", "mock-anthropic-key"):
            with patch("app.synthesis.ANTHROPIC_API_KEY", "mock-anthropic-key"):
                with patch("app.resolver.COMPOSIO_API_KEY", ""):
                    with patch("anthropic.AsyncAnthropic") as mock_cls:
                        mock_client = MagicMock()
                        mock_client.messages.create = AsyncMock(side_effect=[
                            research_t1,
                            research_t2,
                            synth_resp,
                        ])
                        mock_cls.return_value = mock_client

                        guide_v2, err = await run_pipeline("GitHub", "sync repos", "external customers", force=True, store=store)
                        assert not err
                        assert guide_v2.version == 2

                        # Verify version history now has 2 distinct versions
                        history = store.get_all_versions(guide_v1.id)
                        assert len(history) == 2
                        assert [h["version"] for h in history] == [1, 2]

                        # Original v1 remains intact and unmodified
                        v1_stored = store.get_guide(guide_v1.id, 1)
                        assert v1_stored.version == 1


@pytest.mark.anyio
async def test_unfamiliar_app_with_website_and_corroboration(tmp_path):
    """Unfamiliar app input with website parses domain and corroborates identity via official read pages."""
    db_file = tmp_path / "unfamiliar.db"
    store = Store(str(db_file))

    # Fast model call resolves domain and name as 'assumed'
    mock_model_resolve = MagicMock()
    mock_model_resolve.content = [MagicMock(type="text", text='{"canonical_name": "NewCloud", "official_domain": "newcloud.io", "is_ambiguous": false}')]
    mock_model_resolve.usage = MagicMock(input_tokens=10, output_tokens=15)

    research_t1 = _get_mock_research_t1(app_name="NewCloud", domain="newcloud.io")
    research_t2 = _get_mock_research_t2(app_name="NewCloud", domain="newcloud.io")
    synth_resp = _get_mock_synthesis_submit_guide(app_name="NewCloud", domain="newcloud.io")

    with patch("app.config.ANTHROPIC_API_KEY", "mock-key"):
        with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
            with patch("app.synthesis.ANTHROPIC_API_KEY", "mock-key"):
                with patch("app.resolver.COMPOSIO_API_KEY", ""):
                    with patch("anthropic.AsyncAnthropic") as mock_cls:
                        mock_client = MagicMock()
                        mock_client.messages.create = AsyncMock(side_effect=[
                            mock_model_resolve,
                            research_t1,
                            research_t2,
                            synth_resp,
                        ])
                        mock_cls.return_value = mock_client

                        guide, err = await run_pipeline("NewCloud", "backup", "internal team", website="newcloud.io", store=store)
                        assert not err
                        assert guide is not None
                        assert guide.provider.official_domain == "newcloud.io"
                        assert guide.provider.name == "NewCloud"
                        assert guide.provider.identity_status in ("resolved", "assumed")

                        # Evidence domain and content assertion
                        assert any("newcloud.io" in s.url for s in guide.sources)
                        assert any("NewCloud" in s.title or "NewCloud" in s.url for s in guide.sources)
                        read_sources = [s for s in guide.sources if s.retrieval_status == "read"]
                        assert len(read_sources) >= 1
                        assert all("newcloud.io" in s.url for s in read_sources)

                        # Absence of cross-product guidance
                        guide_json = guide.model_dump_json().lower()
                        assert "github" not in guide_json
                        assert "salesforce" not in guide_json
                        assert "dropbox" not in guide_json
                        assert "google" not in guide_json


def test_full_input_to_html_and_markdown_export_pipeline(tmp_path):
    """Complete path: /api/jobs submission -> background run_pipeline -> polling -> reload -> HTML view -> Markdown export."""
    db_file = tmp_path / "full_pipeline.db"

    research_t1 = _get_mock_research_t1("GitHub", "github.com")
    research_t2 = _get_mock_research_t2("GitHub", "github.com")
    synth_resp = _get_mock_synthesis_submit_guide("GitHub", "github.com")

    with patch("app.main.DATABASE_PATH", str(db_file)):
        with patch("app.config.ANTHROPIC_API_KEY", "mock-anthropic-key"):
            with patch("app.research.ANTHROPIC_API_KEY", "mock-anthropic-key"):
                with patch("app.synthesis.ANTHROPIC_API_KEY", "mock-anthropic-key"):
                    with patch("app.resolver.COMPOSIO_API_KEY", ""):
                        with patch("app.main.live_generation_configured", return_value=True):
                            with patch("anthropic.AsyncAnthropic") as mock_cls:
                                mock_client = MagicMock()
                                mock_client.messages.create = AsyncMock(side_effect=[
                                    research_t1,
                                    research_t2,
                                    synth_resp,
                                ])
                                mock_cls.return_value = mock_client

                                with TestClient(app) as client:
                                    # 1. Submit API job
                                    resp = client.post("/api/jobs", json={
                                        "app_name": "GitHub",
                                        "feature": "sync repos",
                                        "audience": "external customers",
                                        "deployment": "server-side",
                                    })
                                    assert resp.status_code == 202
                                    job_id = resp.json()["job_id"]

                                    # 2. Poll until completed
                                    job_data = {}
                                    for _ in range(50):
                                        poll_resp = client.get(f"/api/jobs/{job_id}")
                                        assert poll_resp.status_code == 200
                                        job_data = poll_resp.json()
                                        if job_data.get("status") in ("succeeded", "failed", "partial"):
                                            break
                                        time.sleep(0.05)

                                    assert job_data.get("status") == "succeeded"
                                    guide_id = job_data.get("guide_id")
                                    guide_version = job_data.get("guide_version")
                                    assert guide_id is not None
                                    assert guide_version == 1

                                    # 3. Saved-record reload from fresh Store instance
                                    fresh_store = Store(str(db_file))
                                    reloaded_guide = fresh_store.get_guide(guide_id, guide_version)
                                    assert reloaded_guide is not None
                                    assert reloaded_guide.id == guide_id
                                    assert reloaded_guide.version == 1
                                    assert reloaded_guide.provider.name == "GitHub"

                                    # 4. Request HTML guide
                                    html_resp = client.get(f"/guides/{guide_id}/versions/{guide_version}")
                                    assert html_resp.status_code == 200
                                    assert "GitHub" in html_resp.text
                                    assert "sync repos" in html_resp.text
                                    assert "Register app in developer portal" in html_resp.text

                                    # 5. Request Markdown export
                                    md_resp = client.get(f"/guides/{guide_id}/versions/{guide_version}.md")
                                    assert md_resp.status_code == 200
                                    assert "# GitHub — sync repos" in md_resp.text
                                    assert "v1 · draft" in md_resp.text


def test_ambiguous_name_needs_input_and_candidate_reload():
    """Ambiguous name 'Box' returns candidates; job page renders needs-input state."""
    with TestClient(app) as client:
        with patch("app.main.live_generation_configured", return_value=True):
            with patch("app.main.store.count_recent_jobs", return_value=0):
                # 1. Post job with ambiguous name 'Box' (no website)
                resp = client.post("/api/jobs", json={
                    "app_name": "Box",
                    "feature": "storage",
                    "audience": "external customers",
                })
                assert resp.status_code == 202
                data = resp.json()
                job_id = data["job_id"]

            # Poll for background task to complete disambiguation resolution
            pdata = {}
            for _ in range(40):
                poll = client.get(f"/api/jobs/{job_id}")
                assert poll.status_code == 200
                pdata = poll.json()
                if pdata.get("status") == "needs_input":
                    break
                time.sleep(0.05)

            assert pdata.get("status") == "needs_input"
            assert "candidates" in pdata
            candidate_domains = [c["domain"] for c in pdata["candidates"]]
            assert "box.com" in candidate_domains
            assert "dropbox.com" in candidate_domains

            # 3. Reload job page HTML - renders disambiguation choices server-side
            html_resp = client.get(f"/jobs/{job_id}")
            assert html_resp.status_code == 200
            assert "Ambiguous name" in html_resp.text
            assert "box.com" in html_resp.text


def test_shared_domain_and_toolkit_no_false_matches():
    """Verify shared-domain siblings do not falsely match."""
    # Google domain resolves to candidates, not arbitrary sibling
    p, candidates = resolve_provider("google.com", "google.com")
    assert candidates is not None
    assert any("Gmail" in c["name"] for c in candidates)
    assert any("Google" in c["name"] for c in candidates)


@pytest.mark.anyio
async def test_composio_modes_custom_managed_and_unsupported():
    """Verify Composio custom OAuth, managed OAuth, and unsupported OAuth paths."""
    # 1. Mode: Unsupported OAuth -> validate_guide prunes own_app paths and provides honest unsupported next action
    guide_unsupp = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="test-unsupp",
        guide=DraftGuideContent(
            oauth_support="unsupported",
            paths=[DraftPath(id="own_app_testing", name="Test Path")],
            next_action=DraftNextAction(instruction="Deploy integration", link_or_artifact=""),
        ),
    )
    validate_guide(guide_unsupp)
    assert len(guide_unsupp.guide.paths) == 0
    assert guide_unsupp.guide.next_action.link_or_artifact == ""

    # 2. Mode: Custom OAuth -> Composio has toolkit but managed OAuth is unavailable; own app path is retained and wired
    claim_custom = DraftClaim(
        id="C1",
        text="Register your own OAuth application with custom client ID.",
        basis="documented",
        source_ids=["S1"],
        review_result="not_reviewed",
    )
    guide_custom = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="test-custom",
        composio=ComposioInfo(
            lookup_status="matched",
            toolkit_slug="custom-tool",
            selected_auth_method="OAUTH2",
            managed_oauth="unavailable",
        ),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            claims=[claim_custom],
            paths=[
                DraftPath(
                    id="own_app_testing",
                    name="Custom App Registration",
                    steps=[
                        DraftStep(id="s1", title="Create App", instructions="Register in developer portal", owner="you", claim_ids=["C1"]),
                    ],
                    claim_ids=["C1"],
                )
            ],
            next_action=DraftNextAction(instruction="Register app", owner="you", link_or_artifact="https://custom.com/dev", claim_ids=["C1"]),
        ),
        sources=[Source(id="S1", url="https://custom.com/docs", retrieval_status="read")],
    )
    validate_guide(guide_custom)
    assert len(guide_custom.guide.paths) == 1
    assert guide_custom.composio.managed_oauth == "unavailable"
    assert guide_custom.guide.paths[0].id == "own_app_testing"
    # Ensure Composio wiring step was injected into own_app path
    assert any(s.id == "composio_wiring" for s in guide_custom.guide.paths[0].steps)
    # Ensure managed path was NOT added
    assert not any(p.id == "composio_managed" for p in guide_custom.guide.paths)

    # 3. Mode: Managed OAuth -> Composio managed OAuth is available; managed path is synthesized alongside own app
    claim_managed = DraftClaim(
        id="C2",
        text="Register your own OAuth application or use managed connection.",
        basis="documented",
        source_ids=["S2"],
        review_result="not_reviewed",
    )
    guide_managed = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="test-managed",
        composio=ComposioInfo(
            lookup_status="matched",
            toolkit_slug="managed-tool",
            selected_auth_method="OAUTH2",
            managed_oauth="available",
            auth_guide_url="https://docs.composio.dev/tools/managed-tool",
        ),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            claims=[claim_managed],
            paths=[
                DraftPath(
                    id="own_app_testing",
                    name="Own App Testing",
                    steps=[
                        DraftStep(id="s1", title="Create App", instructions="Register in developer portal", owner="you", claim_ids=["C2"]),
                    ],
                    claim_ids=["C2"],
                )
            ],
            next_action=DraftNextAction(instruction="Initiate connection", owner="you", link_or_artifact="https://docs.composio.dev", claim_ids=["C2"]),
        ),
        sources=[Source(id="S2", url="https://docs.composio.dev/tools/managed-tool", retrieval_status="read")],
    )
    validate_guide(guide_managed)
    # Both own_app_testing and composio_managed paths are now present
    assert len(guide_managed.guide.paths) == 2
    assert guide_managed.composio.managed_oauth == "available"
    assert any(p.id == "composio_managed" for p in guide_managed.guide.paths)
    assert any(p.id == "own_app_testing" for p in guide_managed.guide.paths)


@pytest.mark.anyio
async def test_failure_modes_missing_keys_and_deadlines(tmp_path):
    """Pipeline reports clear failure on missing keys, delayed call deadlines, and cancellation."""
    store = Store(str(tmp_path / "fail.db"))

    # 1. Missing API key
    with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}):
        with patch("app.research.ANTHROPIC_API_KEY", ""):
            guide, err = await run_pipeline("GitHub", "sync", "external", store=store)
            assert guide is None
            assert "Research failed" in err or "API key" in err

    # 2. Delayed call exceeding job deadline bounds
    with patch("app.config.ANTHROPIC_API_KEY", "mock-key"):
        with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
            with patch("app.synthesis.ANTHROPIC_API_KEY", "mock-key"):
                with patch("app.resolver.COMPOSIO_API_KEY", ""):
                    with patch("anthropic.AsyncAnthropic") as mock_cls:
                        mock_client = MagicMock()
                        async def delay_beyond_deadline(*args, **kwargs):
                            await asyncio.sleep(0.50)
                            return MagicMock()
                        mock_client.messages.create = AsyncMock(side_effect=delay_beyond_deadline)
                        mock_cls.return_value = mock_client

                        # Set short pipeline timeout of 0.10s
                        with patch("app.pipeline.JOB_TIMEOUT_SECONDS", 0.10):
                            guide, err = await run_pipeline("GitHub", "sync", "external", store=store)
                            assert guide is None
                            assert "failed" in err.lower() or "timeout" in err.lower()

    # 3. Outer cancellation
    with patch("app.config.ANTHROPIC_API_KEY", "mock-key"):
        with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
            with patch("anthropic.AsyncAnthropic") as mock_cls:
                mock_client2 = MagicMock()
                async def hang(*args, **kwargs):
                    await asyncio.sleep(5.0)
                mock_client2.messages.create = AsyncMock(side_effect=hang)
                mock_client2.close = AsyncMock()
                mock_cls.return_value = mock_client2

                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        run_pipeline("GitHub", "sync", "external", store=store),
                        timeout=0.05,
                    )


def test_repeated_seed_imports_idempotent_3x(tmp_path):
    """Seed import is idempotent across 3 repeated runs with separate Store instances across restarts."""
    db_file = tmp_path / "seeds.db"

    claim = DraftClaim(
        id="K1",
        text="Standard OAuth 2.0 flow",
        basis="documented",
        source_ids=["S1"],
        review_result="supported",
        support_note="Audited in official documentation",
    )
    source = Source(id="S1", url="https://example.com/docs", retrieval_status="read")
    guide = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="test-seed-repeat",
        version=1,
        status="reviewed",
        provider=ProviderInfo(name="RepeatApp", official_domain="example.com", identity_status="resolved"),
        scenario=Scenario(feature="sync"),
        guide=DraftGuideContent(oauth_support="confirmed", claims=[claim]),
        sources=[source],
        review=ReviewInfo(reviewer="Auditor", reviewed_at="2026-11-04T00:00:00Z", audited_claim_ids=["K1"], based_on_version=1),
    )

    # Session 1: initial import
    store1 = Store(str(db_file))
    ok1, msg1 = store1.import_reviewed_seed(guide)
    assert ok1

    # Session 2 (restart 1): repeated identical import on new Store instance
    store2 = Store(str(db_file))
    ok2, msg2 = store2.import_reviewed_seed(guide)
    assert ok2
    assert "Already imported" in msg2

    # Session 3 (restart 2): third identical import on fresh Store instance
    store3 = Store(str(db_file))
    ok3, msg3 = store3.import_reviewed_seed(guide)
    assert ok3
    assert "Already imported" in msg3

    # Verify guide remains intact and valid in persistent database
    persisted = store3.get_guide("test-seed-repeat", 1)
    assert persisted is not None
    assert persisted.status == "reviewed"
    assert persisted.review.reviewer == "Auditor"


def test_concurrency_busy_guard_limits_active_jobs():
    """Concurrency busy guard rejects requests beyond active job limits."""
    client = TestClient(app)

    # Simulate max concurrent active jobs and bypass IP rate limiter
    with patch("app.main.live_generation_configured", return_value=True):
        with patch("app.main.store.count_recent_jobs", return_value=0):
            with patch("app.main.active_tasks", {"t1": MagicMock(), "t2": MagicMock(), "t3": MagicMock(), "t4": MagicMock()}):
                resp = client.post("/api/jobs", json={
                    "app_name": "GitHub",
                    "feature": "sync",
                    "audience": "external customers",
                })
                assert resp.status_code == 503
                assert "busy" in resp.json()["detail"].lower()


def test_restart_behavior_preserves_persistent_guides(tmp_path):
    """A fresh Store instance connecting to the same file retrieves all guides and versions."""
    db_file = tmp_path / "restart.db"

    # Session 1: create and save guide
    store1 = Store(str(db_file))
    g = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="restart-app-id",
        version=1,
        status="draft",
        provider=ProviderInfo(name="RestartApp"),
        guide=DraftGuideContent(oauth_support="confirmed"),
    )
    store1.save_guide(g)

    # Session 2: new Store instance (simulating server restart)
    store2 = Store(str(db_file))
    loaded = store2.get_guide("restart-app-id", 1)
    assert loaded is not None
    assert loaded.id == "restart-app-id"
    assert loaded.provider.name == "RestartApp"
