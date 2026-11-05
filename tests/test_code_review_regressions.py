"""Regressions for findings in docs/CODE_REVIEW_UPDATED.md (R1–R10 and additional items).

All tests use temporary in-memory/tmp_path databases and mocks — no real external credentials.
"""

import json
import pytest
import httpx
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from app.schema import (
    GuideRecord, DraftGuideContent, DraftClaim, DraftGate, DraftPath, DraftStep,
    DraftSummaryStatement, DraftNextAction, DraftEstimate, Estimate,
    Source, Attempt, ComposioInfo, ProviderInfo, Scenario, RunStats, ReviewInfo,
    make_guide_id, make_cache_key,
    OAuthSupport, GuideStatus, ComposioLookupStatus, ManagedOAuth, AttemptStatus,
)
from app.validation import (
    validate_guide, validate_composio, validate_references,
    validate_estimates_in_guide, validate_gate_evidence,
    validate_reviewed_record,
)
from app.resolver import resolve_provider_with_model, _search_composio
from app.pipeline import _load_attempts_for_provider
from app.store import Store
from app.render import render_markdown, format_estimate


# ── R1: Evidence Provenance ───────────────────────────────────────────────

def test_r1_load_attempts_skips_synthetic_examples(tmp_path):
    """Production attempt loader ignores example fixtures and synthetic records."""
    attempts_dir = tmp_path / "attempts"
    attempts_dir.mkdir()

    # Synthetic example
    (attempts_dir / "github-example.json").write_text(json.dumps({
        "id": "EXAMPLE-ATT-GH-01",
        "provider": "github",
        "action": "setup",
        "status": "example",
        "example": True,
        "observed_result": "[SYNTHETIC EXAMPLE] Mock run",
    }))

    # Legitimate non-example attempt
    (attempts_dir / "github-real.json").write_text(json.dumps({
        "id": "ATT-GH-REAL",
        "provider": "github",
        "action": "setup",
        "status": "blocked",
        "observed_result": "Consent flow blocked by org policy",
    }))

    with patch("app.pipeline.Path", return_value=attempts_dir):
        loaded = _load_attempts_for_provider("github")
        ids = [a.id for a in loaded]
        assert "ATT-GH-REAL" in ids
        assert "EXAMPLE-ATT-GH-01" not in ids


# ── R2: Offline Validation Provenance ─────────────────────────────────────

def test_r2_offline_validation_does_not_fabricate_read_provenance():
    """Offline validation sets maintainer references to reference_only with no fabricated publication date, not read."""
    guide = GuideRecord(
        id="test-r2",
        composio=ComposioInfo(
            toolkit_slug="github",
            lookup_status=ComposioLookupStatus.matched.value,
            selected_auth_method="oauth2",
            managed_oauth=ManagedOAuth.available.value,
        ),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[DraftPath(id="own_app_production", name="Prod")],
        ),
    )

    validate_composio(guide)

    c2 = next((s for s in guide.sources if s.id == "C2"), None)
    c3 = next((s for s in guide.sources if s.id == "C3"), None)

    assert c2 is not None
    assert c2.retrieval_status == "reference_only"
    assert c2.retrieval_time is None
    assert c2.publication_date is None

    assert c3 is not None
    assert c3.retrieval_status == "reference_only"
    assert c3.retrieval_time is None
    assert c3.publication_date is None

    # Crucially: sources read must count ONLY retrieval_status == 'read'
    sources_read = sum(1 for s in guide.sources if s.retrieval_status == "read")
    assert sources_read == 0


# ── R3: Unsupported Advice Downgrade ──────────────────────────────────────

def test_r3_unsupported_advice_downgraded_and_qualified():
    """Unsupported launch advice is replaced with evidence gathering, and summary qualified."""
    # A claim citing a nonexistent source S999
    claim = DraftClaim(
        id="C1",
        text="No approval is needed and self-service registration is instant",
        source_ids=["S999"],  # Nonexistent
        basis="documented",
    )
    guide = GuideRecord(
        id="test-r3",
        guide=DraftGuideContent(
            oauth_support="confirmed",
            recommended_path_id="own_app_production",
            summary=[DraftSummaryStatement(text="No approval is needed.", claim_ids=["C1"])],
            next_action=DraftNextAction(
                instruction="Launch publicly without review",
                owner="you",
                claim_ids=["C1"],
            ),
            paths=[
                DraftPath(
                    id="own_app_production",
                    name="Production",
                    gates=[DraftGate(kind="app_review", state="not_required", claim_ids=["C1"])],
                )
            ],
            claims=[claim],
        ),
        sources=[],  # S999 does not exist
    )

    validate_guide(guide)

    # Claim basis downgraded to unknown
    assert guide.guide.claims[0].basis == "unknown"

    # Gate downgraded to unknown
    assert guide.guide.paths[0].gates[0].state == "unknown"

    # Summary statement qualified
    assert guide.guide.summary[0].text.startswith("Unconfirmed:")

    # Next action must NOT tell the developer to launch without review
    na = guide.guide.next_action
    assert "launch publicly without review" not in na.instruction.lower()
    assert "verify" in na.instruction.lower()
    assert "official review requirements" in na.success_condition.lower()


# ── R4: Resolver Unverified Model Guesses ─────────────────────────────────

@pytest.mark.anyio
async def test_r4_model_guess_is_assumed_not_resolved():
    """Bare model JSON returns assumed identity, never resolved."""
    mock_msg = MagicMock()
    mock_msg.content = [
        MagicMock(type="text", text=json.dumps({
            "canonical_name": "Unverified App",
            "official_domain": "unverifiedapp.io",
            "is_ambiguous": False,
        }))
    ]
    mock_msg.usage = MagicMock(input_tokens=15, output_tokens=10)

    with patch("app.config.ANTHROPIC_API_KEY", "mock-key"):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_msg)
            mock_cls.return_value = mock_client

            info, candidates, stats = await resolve_provider_with_model("Unverified App")
            assert info.official_domain == "unverifiedapp.io"
            assert info.identity_status == "assumed"  # NOT resolved
            assert candidates is None
            assert stats["model_calls"] == 1


@pytest.mark.anyio
async def test_r4_model_guess_does_not_overwrite_user_website():
    """When user provides a website, never return candidates; keep user domain and record warning."""
    mock_msg = MagicMock()
    mock_msg.content = [
        MagicMock(type="text", text=json.dumps({
            "canonical_name": "Different Product",
            "official_domain": "differentdomain.com",
            "is_ambiguous": False,
        }))
    ]
    mock_msg.usage = MagicMock(input_tokens=15, output_tokens=10)

    with patch("app.config.ANTHROPIC_API_KEY", "mock-key"):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_msg)
            mock_cls.return_value = mock_client

            info, candidates, stats = await resolve_provider_with_model("My Product", website="userdomain.com")
            # Must preserve user domain, return NO candidates, and record warning
            assert info.official_domain == "userdomain.com"
            assert candidates is None
            assert "warning" in stats
            assert "differentdomain.com" in stats["warning"]


# ── R5: Toolkit Matching Rigor (Box vs Dropbox) ───────────────────────────

@pytest.mark.anyio
async def test_r5_box_does_not_match_dropbox():
    """Mock search for Box (box.com) returning only Dropbox rejects the match."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "items": [
            {"name": "Dropbox Cloud", "slug": "dropbox", "meta": {"app_url": "https://dropbox.com"}},
        ]
    }

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        matched = await _search_composio("box", official_domain="box.com")
        assert matched is None


# ── R6: Completed Jobs UI State ───────────────────────────────────────────

def test_r6_completed_job_renders_result_server_side():
    """Job template renders result box and link directly for completed/terminal statuses."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader("app/templates"))
    template = env.get_template("job.html")

    # Succeeded job
    succeeded_job = {
        "id": "job-succ",
        "app_name": "GitHub",
        "feature": "repo sync",
        "audience": "external customers",
        "status": "succeeded",
        "stage": "complete",
        "events": ["Research finished", "Guide saved"],
        "guide_id": "github-123",
        "guide_version": 1,
    }
    html_succ = template.render(job=succeeded_job)
    assert 'id="result-box"' in html_succ
    assert 'display: block;' in html_succ
    assert '/guides/github-123/versions/1' in html_succ

    # Failed job
    failed_job = {
        "id": "job-fail",
        "app_name": "UnknownApp",
        "feature": "",
        "audience": "external customers",
        "status": "failed",
        "stage": "failed",
        "events": [],
        "error_message": "Provider research failed",
        "error_id": "ERR-999",
    }
    html_fail = template.render(job=failed_job)
    assert 'id="error-box"' in html_fail
    assert 'display: block;' in html_fail
    assert "Provider research failed" in html_fail
    assert "ERR-999" in html_fail


# ── R7: Promote Gate Shortcuts Validation ─────────────────────────────────

def test_r7_promote_rejects_missing_source_and_attempt(tmp_path):
    """promote.py reject nonexistent source ID and attempt ID with failure exit."""
    from scripts.promote import main
    import sys

    db_path = str(tmp_path / "test_promote.db")
    store = Store(db_path)

    guide = GuideRecord(
        id="test-guide",
        version=1,
        status="draft",
        provider=ProviderInfo(name="Test"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[DraftPath(id="own_app_testing", name="Path", gates=[DraftGate(kind="app_review", state="unknown")])],
        ),
    )
    store.save_guide(guide)

    # 1. Nonexistent source ID
    with patch("scripts.promote.DATABASE_PATH", db_path):
        with patch.object(sys, "argv", ["promote.py", "test-guide", "1", "--pass-gate", "app_review", "--source", "S999", "--reviewer", "Alice"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

        # 2. Nonexistent attempt ID
        with patch.object(sys, "argv", ["promote.py", "test-guide", "1", "--pass-gate", "app_review", "--attempt-id", "ATT-NONE", "--reviewer", "Alice"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

        # 3. Individual attempt cannot establish absence of app_review
        attempt = Attempt(id="ATT-VALID", status="succeeded", observed_result="connected")
        guide.attempts = [attempt]
        store.save_guide(guide)
        with patch.object(sys, "argv", ["promote.py", "test-guide", "1", "--pass-gate", "app_review", "--attempt-id", "ATT-VALID", "--reviewer", "Alice"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1


# ── R8: Reviewed Seed Ingestion Validation ─────────────────────────────────

def test_r8_seed_validation_rejects_malformed_and_unverified_records():
    """store.import_reviewed_seed rejects invalid states, missing audited claims, and nonexistent sources."""
    store = Store(":memory:")

    # Malformed oauth_support
    g1 = GuideRecord(
        id="g1", version=1, status="reviewed",
        guide=DraftGuideContent(oauth_support="not-a-real-state"),
    )
    ok1, msg1 = store.import_reviewed_seed(g1)
    assert not ok1
    assert "oauth_support" in msg1

    # Malformed lookup_status
    g2 = GuideRecord(
        id="g2", version=1, status="reviewed",
        composio=ComposioInfo(lookup_status="found"),
    )
    ok2, msg2 = store.import_reviewed_seed(g2)
    assert not ok2
    assert "lookup_status" in msg2

    # Nonexistent audited claim ID
    g3 = GuideRecord(
        id="g3", version=1, status="reviewed",
        review=ReviewInfo(reviewer="Alice", reviewed_at="2026-01-01T00:00:00Z", audited_claim_ids=["NO-SUCH-CLAIM"]),
        guide=DraftGuideContent(claims=[DraftClaim(id="C1", text="OAuth ok")]),
    )
    ok3, msg3 = store.import_reviewed_seed(g3)
    assert not ok3
    assert "NO-SUCH-CLAIM" in msg3


# ── R9: Nullable Estimates Serialization ──────────────────────────────────

def test_r9_unknown_estimate_serializes_as_null_in_json():
    """Unknown estimates serialize with low: null, high: null; legitimate zero serializes as 0."""
    guide = GuideRecord(
        id="test-r9",
        guide=DraftGuideContent(
            paths=[
                DraftPath(
                    id="own_app_testing",
                    name="Test Path",
                    effort=DraftEstimate(known=False, low=None, high=None, basis="unknown", explanation="Not known"),
                    waiting=DraftEstimate(known=True, low=0, high=0, basis="author_estimate", explanation="No wait"),
                )
            ]
        ),
    )

    validate_estimates_in_guide(guide)
    raw_json = guide.model_dump_json()
    data = json.loads(raw_json)

    effort_data = data["guide"]["paths"][0]["effort"]
    assert effort_data["known"] is False
    assert effort_data["low"] is None
    assert effort_data["high"] is None

    waiting_data = data["guide"]["paths"][0]["waiting"]
    assert waiting_data["known"] is True
    assert waiting_data["low"] == 0
    assert waiting_data["high"] == 0


# ── R10: Guide Identity & Scenario Distinction ────────────────────────────

def test_r10_guide_id_differentiates_deployment_and_domains():
    """make_guide_id incorporates scenario deployment and domain."""
    scenario_server = Scenario(feature="sync", audience="external customers", deployment="server-side")
    scenario_client = Scenario(feature="sync", audience="external customers", deployment="client-side")

    id_server = make_guide_id("GitHub", scenario_server, "github.com")
    id_client = make_guide_id("GitHub", scenario_client, "github.com")
    id_diff_domain = make_guide_id("GitHub", scenario_server, "github.enterprise.com")

    # Server vs Client deployment must produce DIFFERENT guide IDs
    assert id_server != id_client
    # Different domains must produce DIFFERENT guide IDs
    assert id_server != id_diff_domain


# ── Additional Corrections: Deadline, Limitations, Full Excerpt ───────────

@pytest.mark.anyio
async def test_deadline_expired_stops_immediately_without_model_call():
    """Research turn immediately halts if remaining time <= 0."""
    from app.research import run_research
    import time

    provider = ProviderInfo(name="Test", official_domain="test.com")
    scenario = Scenario()
    composio = ComposioInfo()

    with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            # Set deadline in the past
            findings, sources, stats, page_texts = await run_research(
                provider, scenario, composio, deadline=time.time() - 5.0
            )
            # Must NOT make any model calls
            assert stats["model_calls"] == 0
            assert stats["research_timed_out"] is True
            mock_client.messages.create.assert_not_called()


def test_markdown_and_html_render_full_excerpt_and_support_note():
    """Render displays full excerpts and support notes without truncation."""
    long_excerpt = "This is a very long official documentation excerpt explaining specific OAuth scopes and consent mechanisms in exhaustive detail."
    guide = GuideRecord(
        id="test-full-render",
        provider=ProviderInfo(name="TestApp"),
        guide=DraftGuideContent(
            claims=[
                DraftClaim(
                    id="C1",
                    text="OAuth supported",
                    excerpt=long_excerpt,
                    excerpt_status="found",
                    review_result="supported",
                    support_note="Verified against documentation section 4.2 by reviewer",
                )
            ]
        ),
    )

    md = render_markdown(guide)
    assert long_excerpt in md
    assert "Verified against documentation section 4.2 by reviewer" in md


# ── Current Review Findings (1–8) Regressions ─────────────────────────────

def test_current_review_finding1_credential_redaction():
    """Secrets in JSON, headers, plain text, and query params are sanitized."""
    from scripts.composio_test import _sanitize_secrets

    raw_json = '{"error": "Unauthorized", "api_key": "DUMMY_API_SECRET_987654321", "details": "bad key"}'
    sanitized = _sanitize_secrets(raw_json, ["DUMMY_API_SECRET_987654321"])
    assert "DUMMY_API_SECRET_987654321" not in sanitized
    assert "[REDACTED]" in sanitized

    raw_header = "Authorization: Bearer my_secret_token_12345"
    assert "my_secret_token_12345" not in _sanitize_secrets(raw_header)

    raw_query = "https://backend.composio.dev/api?client_secret=super_secret_client_val"
    assert "super_secret_client_val" not in _sanitize_secrets(raw_query)


def test_current_review_finding2_reviewed_record_integrity(tmp_path):
    """Reviewed records reject invalid source URLs (javascript:bad), duplicate IDs, and mismatched base identities."""
    from app.store import Store
    from app.validation import validate_reviewed_record

    db_path = str(tmp_path / "test_rev_integrity.db")
    store = Store(db_path)

    # 1. Reject source with javascript:bad
    bad_source = Source(
        id="S1",
        url="javascript:bad",
        retrieval_status="read",
        title="Malicious source",
    )
    claim = DraftClaim(
        id="K1",
        text="OAuth supported",
        basis="documented",
        source_ids=["S1"],
        review_result="supported",
        support_note="Audited by reviewer",
    )
    guide_bad = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="test-bad-url",
        version=1,
        status="reviewed",
        provider=ProviderInfo(name="BadApp", official_domain="badapp.com", identity_status="resolved"),
        scenario=Scenario(feature="sync"),
        guide=DraftGuideContent(oauth_support="confirmed", claims=[claim]),
        sources=[bad_source],
        review=ReviewInfo(
            reviewer="Alice",
            reviewed_at="2026-11-04T00:00:00Z",
            audited_claim_ids=["K1"],
            based_on_version=1,
        ),
    )

    valid, err = validate_reviewed_record(guide_bad)
    assert not valid
    assert "invalid URL" in err

    success, msg = store.import_reviewed_seed(guide_bad)
    assert not success
    assert "Seed integrity validation failed" in msg

    # 2. Reject duplicate source IDs
    s_dup1 = Source(id="S1", url="https://example.com/1", retrieval_status="read")
    s_dup2 = Source(id="S1", url="https://example.com/2", retrieval_status="read")
    guide_dup = guide_bad.model_copy(deep=True)
    guide_dup.sources = [s_dup1, s_dup2]
    guide_dup.guide.claims[0].source_ids = ["S1"]
    valid, err = validate_reviewed_record(guide_dup)
    assert not valid
    assert "Duplicate source ID" in err

    # 3. Valid reviewed content imports unchanged and reimports are idempotent
    s_ok = Source(id="S1", url="https://example.com/docs", retrieval_status="read")
    guide_ok = guide_bad.model_copy(deep=True)
    guide_ok.sources = [s_ok]
    guide_ok.guide.claims[0].source_ids = ["S1"]
    valid, err = validate_reviewed_record(guide_ok)
    assert valid

    success1, msg1 = store.import_reviewed_seed(guide_ok)
    assert success1
    # Idempotent reimport
    success2, msg2 = store.import_reviewed_seed(guide_ok)
    assert success2
    assert "Already imported" in msg2


@pytest.mark.anyio
async def test_current_review_finding3_toolkit_matching_rigor():
    """Toolkit matching rejects Box matching Dropbox (no app_url) and Google Drive matching Google Calendar."""
    from app.resolver import _search_composio, lookup_composio_toolkit

    # 1. Box vs Dropbox without app_url metadata
    mock_resp_dropbox = MagicMock()
    mock_resp_dropbox.status_code = 200
    mock_resp_dropbox.json.return_value = {
        "items": [
            {"name": "Dropbox", "slug": "dropbox", "meta": {}},
        ]
    }
    with patch("httpx.AsyncClient.get", return_value=mock_resp_dropbox):
        matched = await _search_composio("Box", official_domain="box.com")
        assert matched is None

    # 2. Google Drive vs Google Calendar on shared google.com domain
    mock_resp_gcal = MagicMock()
    mock_resp_gcal.status_code = 200
    mock_resp_gcal.json.return_value = {
        "items": [
            {"name": "Google Calendar", "slug": "googlecalendar", "meta": {"app_url": "https://calendar.google.com"}},
        ]
    }
    with patch("httpx.AsyncClient.get", return_value=mock_resp_gcal):
        matched = await _search_composio("Google Drive", official_domain="google.com")
        assert matched is None

    # 3. Explicit alias conflicts with website
    with patch("app.resolver.COMPOSIO_API_KEY", "test-key"):
        with patch("app.resolver._search_composio", return_value=None):
            info, _ = await lookup_composio_toolkit("dropbox", official_domain="box.com")
            # The dropbox alias should conflict with box.com domain and not resolve
            assert info.toolkit_slug != "dropbox"


@pytest.mark.anyio
async def test_followup_review_item2_sibling_products_unlisted_domains_and_missing_metadata():
    """Follow-up Item 2: Corroboration required on ALL domains; company domains cannot auto-match conflicting sibling products."""
    from app.resolver import _search_composio

    # 1. Salesforce Sales Cloud vs Salesforce Marketing Cloud on unlisted multi-product domain salesforce.com
    mock_resp_sf = MagicMock()
    mock_resp_sf.status_code = 200
    mock_resp_sf.json.return_value = {
        "items": [
            {
                "name": "Salesforce Marketing Cloud",
                "slug": "salesforcemarketingcloud",
                "meta": {"app_url": "https://www.salesforce.com"},
            }
        ]
    }
    with patch("httpx.AsyncClient.get", return_value=mock_resp_sf):
        matched = await _search_composio("Salesforce Sales Cloud", official_domain="salesforce.com")
        assert matched is None, "Conflicting sibling product on company domain must not auto-match"

    # 2. Zoho CRM vs Zoho Books on unlisted domain zoho.com
    mock_resp_zoho = MagicMock()
    mock_resp_zoho.status_code = 200
    mock_resp_zoho.json.return_value = {
        "items": [
            {
                "name": "Zoho Books",
                "slug": "zohobooks",
                "meta": {"app_url": "https://www.zoho.com"},
            }
        ]
    }
    with patch("httpx.AsyncClient.get", return_value=mock_resp_zoho):
        matched = await _search_composio("Zoho CRM", official_domain="zoho.com")
        assert matched is None, "Conflicting product name on same domain must not match"

    # 3. Missing metadata: Acme Mail without app_url when query is Acme Analytics
    mock_resp_missing_meta = MagicMock()
    mock_resp_missing_meta.status_code = 200
    mock_resp_missing_meta.json.return_value = {
        "items": [
            {
                "name": "Acme Mail",
                "slug": "acmemail",
                "meta": {},  # no app_url
            }
        ]
    }
    with patch("httpx.AsyncClient.get", return_value=mock_resp_missing_meta):
        matched = await _search_composio("Acme Analytics", official_domain="acme.com")
        assert matched is None, "Conflicting product without metadata must not match"

    # 4. Valid product corroboration: Linear App on linear.app with Linear
    mock_resp_linear = MagicMock()
    mock_resp_linear.status_code = 200
    mock_resp_linear.json.return_value = {
        "items": [
            {
                "name": "Linear",
                "slug": "linear",
                "meta": {"app_url": "https://linear.app"},
            }
        ]
    }
    with patch("httpx.AsyncClient.get", return_value=mock_resp_linear):
        matched = await _search_composio("Linear App", official_domain="linear.app")
        assert matched == "linear", "Corroborated product identity on matching domain must succeed"


def test_current_review_finding4_website_only_resolution_shared_domains():
    """Website-only inputs for shared domains (google.com, atlassian.com) return candidates, not arbitrary siblings."""
    from app.resolver import resolve_provider

    # google.com returns candidates (Gmail, Google), not arbitrary Gmail
    p_google, c_google = resolve_provider("google.com", "google.com")
    assert c_google is not None
    assert len(c_google) >= 2
    names = [c["name"] for c in c_google]
    assert any("Google" in n for n in names)
    assert p_google.identity_status == "assumed"

    # atlassian.com returns candidates (Jira, Trello)
    p_atl, c_atl = resolve_provider("atlassian.com", "atlassian.com")
    assert c_atl is not None
    assert len(c_atl) >= 2

    # notion.so has a unique product domain, resolving directly to Notion
    p_notion, c_notion = resolve_provider("notion.so", "notion.so")
    assert c_notion is None
    assert p_notion.name == "Notion"
    assert p_notion.identity_status == "resolved"


def test_current_review_finding5_unsupported_evidence_qualifies_setup_steps():
    """Unsupported evidence visibly qualifies setup steps asserting unconfirmed launch advice."""
    from app.validation import validate_guide

    step_launch = DraftStep(
        id="step_launch",
        title="Public launch",
        instructions="Publish for all external users; no provider approval is needed.",
        owner="you",
        claim_ids=["K1"],
    )
    path = DraftPath(
        id="own_app_testing",
        name="Testing Path",
        steps=[step_launch],
    )
    na = DraftNextAction(
        instruction="Go live without review",
        owner="you",
        link_or_artifact="https://example.com/deploy",
        claim_ids=["K1"],
    )
    # K1 cites source S1 which has failed retrieval status
    source = Source(id="S1", url="https://example.com/docs", retrieval_status="failed")
    claim = DraftClaim(id="K1", text="No review required", basis="documented", source_ids=["S1"])

    guide = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="test-unsupported-step",
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[path],
            next_action=na,
            claims=[claim],
        ),
        sources=[source],
    )

    validate_guide(guide)

    # Step instruction must be qualified
    modified_step = guide.guide.paths[0].steps[0]
    assert "Verify" in modified_step.instructions or "[Unconfirmed]" in modified_step.instructions
    assert "Publish for all external users; no provider approval is needed." not in modified_step.instructions

    # Next action must be updated consistently and link_or_artifact cleared
    assert "Verify provider OAuth" in guide.guide.next_action.instruction
    assert guide.guide.next_action.link_or_artifact == ""


def test_followup_review_item1_unsupported_next_action_persists_and_renders_cleanly(tmp_path):
    """Validate-save-reload-render boundary test: clearing next action must not corrupt string schema or break routes."""
    from starlette.testclient import TestClient
    from app.main import app
    from app.store import Store

    db_file = tmp_path / "item1_test.db"
    test_store = Store(str(db_file))

    claim = DraftClaim(
        id="C1",
        text="Publishing requires no provider approval",
        basis="documented",
        source_ids=["S1"],
    )
    source = Source(
        id="S1",
        url="javascript:alert(1)",
        kind="fetched_page",
        retrieval_status="failed",
    )
    path = DraftPath(
        id="own_app_production",
        name="Production Path",
        gates=[],
        steps=[
            DraftStep(
                id="s1",
                title="Publish App",
                owner="you",
                instructions="Publish for all external users; no provider approval is needed.",
                claim_ids=["C1"],
            )
        ],
    )
    na = DraftNextAction(
        instruction="Go live without review immediately",
        link_or_artifact="https://example.com/bad",
        claim_ids=["C1"],
    )
    guide = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="bad-next-action-test",
        version=1,
        status="draft",
        provider=ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved"),
        scenario=Scenario(),
        composio=ComposioInfo(lookup_status="no_match"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[path],
            next_action=na,
            claims=[claim],
        ),
        sources=[source],
    )

    validate_guide(guide)
    assert guide.guide.next_action.link_or_artifact == ""

    # Save to SQLite store
    v = test_store.save_guide(guide)
    assert v == 1

    # Reload from store - must parse without ValidationError
    reloaded = test_store.get_guide("bad-next-action-test", 1)
    assert reloaded is not None
    assert reloaded.guide.next_action.link_or_artifact == ""

    # list_guides must parse cleanly
    guides_list = test_store.list_guides()
    assert len(guides_list) == 1
    assert guides_list[0]["id"] == "bad-next-action-test"

    # Test HTTP routes with patched DATABASE_PATH
    with patch("app.main.DATABASE_PATH", str(db_file)):
        with TestClient(app, raise_server_exceptions=True) as client:
            # 1. Homepage listing
            resp_home = client.get("/")
            assert resp_home.status_code == 200
            assert "TestApp" in resp_home.text

            # 2. Guide HTML page
            resp_guide = client.get("/guides/bad-next-action-test/versions/1")
            assert resp_guide.status_code == 200
            assert "Verify provider OAuth and review requirements" in resp_guide.text

            # 3. Markdown export
            resp_md = client.get("/guides/bad-next-action-test/versions/1.md")
            assert resp_md.status_code == 200
            assert "Verify provider OAuth and review requirements" in resp_md.text


def test_current_review_finding6_stale_hidden_website_handling():
    """Prefilled website is preserved when app_name is unchanged, but cleared or rederived when edited."""
    # Simulation of index.html submit logic:
    def simulate_submit(app_name, initial_app, prefill_website, prefill_force_str):
        prefill_force = (prefill_force_str or "").strip() == "true"
        app_changed = bool(initial_app and app_name.lower() != initial_app.lower())

        website = ""
        force = False

        if not app_changed and prefill_website:
            website = prefill_website
            force = prefill_force
        elif "." in app_name and " " not in app_name:
            website = app_name

        return website, force

    # 1. Unchanged selection: Box + box.com + force=true
    w1, f1 = simulate_submit("Box", "Box", "box.com", "true")
    assert w1 == "box.com"
    assert f1 is True

    # 2. Changed selection: Box edited to Slack -> website cleared, force=false
    w2, f2 = simulate_submit("Slack", "Box", "box.com", "true")
    assert w2 == ""
    assert f2 is False

    # 3. Changed selection: Box edited to slack.com -> website re-derived as slack.com
    w3, f3 = simulate_submit("slack.com", "Box", "box.com", "true")
    assert w3 == "slack.com"
    assert f3 is False


def test_current_review_finding7_artifact_text_preserved_and_idempotent():
    """Ordinary artifact descriptions survive repeated validation without [invalid URL removed] prepended."""
    from app.validation import validate_guide

    guide = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="test-artifact-idempotent",
        guide=DraftGuideContent(
            oauth_support="confirmed",
            next_action=DraftNextAction(
                instruction="Prepare privacy policy",
                link_or_artifact="Privacy policy draft",
            ),
        ),
    )

    # Validate first time
    validate_guide(guide)
    assert guide.guide.next_action.link_or_artifact == "Privacy policy draft"

    # Validate second time (must be idempotent)
    validate_guide(guide)
    assert guide.guide.next_action.link_or_artifact == "Privacy policy draft"

    # Valid HTTPS URL is preserved
    guide.guide.next_action.link_or_artifact = "https://example.com/oauth-app"
    validate_guide(guide)
    assert guide.guide.next_action.link_or_artifact == "https://example.com/oauth-app"

    # Rejected URL gets stable prefix without compounding
    guide.guide.next_action.link_or_artifact = "javascript:alert(1)"
    validate_guide(guide)
    assert guide.guide.next_action.link_or_artifact.startswith("[invalid URL removed]")
    first_res = guide.guide.next_action.link_or_artifact

    validate_guide(guide)
    assert guide.guide.next_action.link_or_artifact == first_res


@pytest.mark.anyio
async def test_current_review_finding8_research_phase_deadlines():
    """Short deadlines under wrap-up buffer finalize research cleanly without crashing."""
    from app.research import run_research
    import time

    provider = ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved")
    scenario = Scenario()
    composio = ComposioInfo()

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(type="text", text="Wrap-up findings completed under deadline")]
    mock_resp.usage = MagicMock(input_tokens=50, output_tokens=30)
    mock_resp.stop_reason = "end_turn"

    with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            # Supply a short remaining deadline of 5 seconds (< 15s wrapup buffer)
            findings, sources, stats, page_texts = await run_research(
                provider, scenario, composio, deadline=time.time() + 5.0
            )

            assert "Wrap-up findings completed" in findings
            assert stats["research_timed_out"] is True
            assert stats["model_calls"] == 1
            # Verify tool_choice was set to none for finalization
            call_kwargs = mock_client.messages.create.call_args[1]
            assert call_kwargs["tool_choice"] == {"type": "none"}


def test_followup_review_item3_blocked_attempt_can_support_blocker_observation(tmp_path):
    """Follow-up Item 3: A blocked attempt can support an audited description of that blocker, but not success."""
    from app.validation import validate_reviewed_record
    from app.store import Store

    db_file = tmp_path / "item3_test.db"
    store = Store(str(db_file))

    attempt = Attempt(
        id="A1",
        provider_name="TestCorp",
        connection_type="custom_oauth",
        started_at="2026-11-04T00:00:00Z",
        completed_at="2026-11-04T00:05:00Z",
        duration_seconds=300.0,
        status="blocked",
        observed_result="Organization admin approval required to authorize app",
        limitation="Proves admin consent blocker encountered during test connection",
        environment="test",
        auth_mode="OAUTH2",
    )

    # 1. Genuinely reviewed blocker claim backed by blocked attempt
    claim_blocker = DraftClaim(
        id="K1",
        text="This test connection was blocked pending organization admin approval.",
        basis="observed",
        attempt_ids=["A1"],
        review_result="supported",
        support_note="Verified against logged admin consent screen error in attempt A1",
    )

    guide = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="blocked-attempt-test",
        version=2,
        status="reviewed",
        provider=ProviderInfo(name="TestCorp", official_domain="testcorp.com", identity_status="resolved"),
        scenario=Scenario(),
        composio=ComposioInfo(lookup_status="no_match"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            claims=[claim_blocker],
        ),
        attempts=[attempt],
        review=ReviewInfo(
            reviewer="Auditor Alice",
            reviewed_at="2026-11-04T01:00:00Z",
            audited_claim_ids=["K1"],
            summary_assessment="Admin approval blocker accurately observed in test attempt",
            based_on_version=1,
        ),
    )

    valid, err = validate_reviewed_record(guide)
    assert valid is True, f"Blocked attempt should validly support blocker claim, but failed: {err}"

    # Import seed into store
    ok, msg = store.import_reviewed_seed(guide)
    assert ok is True, f"Seed with blocker observation should import successfully, but failed: {msg}"

    # 2. But the blocked attempt CANNOT support a claim asserting successful connection or no review
    claim_false_success = DraftClaim(
        id="K2",
        text="OAuth connection succeeded without review required.",
        basis="observed",
        attempt_ids=["A1"],
        review_result="supported",
        support_note="Auditor falsely claiming success on blocked attempt",
    )
    guide_invalid = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="blocked-attempt-invalid",
        version=2,
        status="reviewed",
        provider=ProviderInfo(name="TestCorp", official_domain="testcorp.com", identity_status="resolved"),
        scenario=Scenario(),
        composio=ComposioInfo(lookup_status="no_match"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            claims=[claim_false_success],
        ),
        attempts=[attempt],
        review=ReviewInfo(
            reviewer="Auditor Bob",
            reviewed_at="2026-11-04T01:00:00Z",
            audited_claim_ids=["K2"],
            summary_assessment="Invalid review",
            based_on_version=1,
        ),
    )

    valid_inv, err_inv = validate_reviewed_record(guide_invalid)
    assert valid_inv is False
    assert "asserts successful connection or lack of review" in err_inv


@pytest.mark.anyio
async def test_followup_review_item4_research_deadline_and_cancellation_cleanup():
    """Follow-up Item 4: Application-level phase deadlines cancel delayed calls and client is closed on cancellation."""
    from app.research import run_research
    import asyncio
    import time

    provider = ProviderInfo(name="SlowAPI", official_domain="slowapi.com", identity_status="resolved")
    scenario = Scenario()
    composio = ComposioInfo()

    # 1. Delayed mock call exceeding deadline is bounded by application-level wait_for
    async def slow_create(*args, **kwargs):
        await asyncio.sleep(0.35)
        mock_r = MagicMock()
        mock_r.content = [MagicMock(type="text", text="Too late")]
        mock_r.usage = MagicMock(input_tokens=10, output_tokens=10)
        mock_r.stop_reason = "end_turn"
        return mock_r

    with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(side_effect=slow_create)
            mock_client.close = AsyncMock()
            mock_cls.return_value = mock_client

            t0 = time.time()
            # 0.05s budget, mock takes 0.35s
            findings, sources, stats, page_texts = await run_research(
                provider, scenario, composio, deadline=t0 + 0.05
            )
            elapsed = time.time() - t0

            # Must finish well before mock's 0.35s sleep
            assert elapsed < 0.20, f"Elapsed time {elapsed:.3f}s exceeded phase deadline bound"
            assert stats["research_timed_out"] is True
            assert mock_client.close.await_count >= 1

    # 2. Cancellation by outer caller executes finally block and closes client
    with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client2 = MagicMock()
            async def hang_forever(*args, **kwargs):
                await asyncio.sleep(5.0)
            mock_client2.messages.create = AsyncMock(side_effect=hang_forever)
            mock_client2.close = AsyncMock()
            mock_cls.return_value = mock_client2

            # Outer cancel via wait_for timeout
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    run_research(provider, scenario, composio, deadline=time.time() + 10.0),
                    timeout=0.04,
                )

            # Client close MUST have been awaited despite cancellation
            assert mock_client2.close.await_count >= 1

    # 3. Expired deadline starts no calls
    with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client3 = MagicMock()
            mock_client3.messages.create = AsyncMock()
            mock_client3.close = AsyncMock()
            mock_cls.return_value = mock_client3

            findings, sources, stats, page_texts = await run_research(
                provider, scenario, composio, deadline=time.time() - 1.0
            )
            assert stats["model_calls"] == 0
            assert stats["research_timed_out"] is True
            mock_client3.messages.create.assert_not_called()
            assert mock_client3.close.await_count >= 1


@pytest.mark.anyio
async def test_research_timeout_with_partial_findings_saves_partial_guide():
    """If research times out after partial findings exist, keep them and save guide as partial.
    If no findings exist, fail the job."""
    from app.pipeline import run_pipeline
    from app.schema import DraftGuideContent, Source, GuideStatus

    # Case 1: Timeout with findings -> succeeds as partial
    mock_draft = DraftGuideContent(
        canonical_provider_name="TestApp",
        overview="Partial overview",
        summary_statement={"text": "Test statement"},
        oauth_support="supported",
        paths=[],
        claims=[],
        sources=[Source(id="s1", url="https://testapp.com/docs", title="Docs", provider_domain=True, retrieval_status="read")],
        next_action={"instruction": "Continue research", "owner": "you"},
    )

    with patch("app.pipeline.resolve_provider_with_model", return_value=(ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved"), None, {})):
        with patch("app.pipeline.lookup_composio_toolkit", return_value=(ComposioInfo(lookup_status="no_match"), [])):
            with patch("app.pipeline.run_research", return_value=(
                "Partial findings collected before timeout",
                [Source(id="s1", url="https://testapp.com/docs", title="Docs", provider_domain=True, retrieval_status="read")],
                {"error": "Research model call timed out", "research_timed_out": True, "model_calls": 2},
                {"s1": "Docs text"}
            )):
                with patch("app.pipeline.run_synthesis", return_value=(mock_draft, {"model_calls": 1})):
                    guide, err = await run_pipeline("TestApp", "sync", "team", store=None)
                    assert err == ""
                    assert guide is not None
                    assert guide.status == GuideStatus.partial.value
                    assert guide.run.research_timed_out is True

    # Case 2: Timeout with no findings -> fails job
    with patch("app.pipeline.resolve_provider_with_model", return_value=(ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved"), None, {})):
        with patch("app.pipeline.lookup_composio_toolkit", return_value=(ComposioInfo(lookup_status="no_match"), [])):
            with patch("app.pipeline.run_research", return_value=(
                "",
                [],
                {"error": "Research model call timed out", "research_timed_out": True, "model_calls": 1},
                {}
            )):
                guide, err = await run_pipeline("TestApp", "sync", "team", store=None)
                assert guide is None
                assert "timeout" in err.lower()


# ── Regressions for 5 November 2026 Code Review Findings ────────────────────
import time

@pytest.mark.anyio
async def test_review_5nov_finding4_truncation_followed_by_bad_json_does_not_crash():
    """P2 Finding 4: Truncation (MAX_TOKENS) on attempt 0 followed by bad JSON on attempt 1
    does not raise UnboundLocalError and returns structured invalid_output category."""
    from google.genai import types
    from app.synthesis import run_synthesis_gemini
    from app.schema import ProviderInfo, Scenario, ComposioInfo

    provider = ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved")
    scenario = Scenario(feature="sync", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    # Attempt 0: Truncated by MAX_TOKENS
    cand0 = types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text='{"overview": "incomplet')]),
        finish_reason=types.FinishReason.MAX_TOKENS,
    )
    resp0 = types.GenerateContentResponse(candidates=[cand0])

    # Attempt 1: Malformed JSON text
    cand1 = types.Candidate(
        content=types.Content(role="model", parts=[types.Part.from_text(text="Sorry, here is the text without json {invalid")]),
        finish_reason=types.FinishReason.STOP,
    )
    resp1 = types.GenerateContentResponse(candidates=[cand1])

    with patch("app.synthesis.generate", AsyncMock(side_effect=[(resp0, {}), (resp1, {})])):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
            guide, stats = await run_synthesis_gemini(
                provider, scenario, composio, sources=[], attempts=[], findings="Findings", deadline=time.time() + 60.0
            )

    assert guide is None
    assert stats["error_category"] == "invalid_output"
    assert "Synthesis failed after repair" in stats["error"]
    assert stats["model_calls"] == 2


@pytest.mark.anyio
async def test_review_5nov_finding5_synthesis_quota_error_preserved_in_pipeline_and_jobs():
    """P2 Finding 5: Synthesis preserves typed RateLimitExceeded error category and does not retry as JSON failure.
    Pipeline carries category and job displays safe quota message."""
    from app.synthesis import run_synthesis_gemini
    from app.llm_gemini import RateLimitExceeded
    from app.schema import ProviderInfo, Scenario, ComposioInfo
    from app.main import _public_error_message, _error_category

    provider = ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved")
    scenario = Scenario(feature="sync", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    # generate raises RateLimitExceeded
    mock_gen = AsyncMock(side_effect=RateLimitExceeded("Resource has been exhausted (e.g. check quota)"))

    with patch("app.synthesis.generate", mock_gen):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
            guide, stats = await run_synthesis_gemini(
                provider, scenario, composio, sources=[], attempts=[], findings="Findings", deadline=time.time() + 60.0
            )

    # Must fail immediately on first attempt without retrying as malformed JSON
    assert guide is None
    assert mock_gen.call_count == 1
    assert stats["error_category"] == "rate_limit"

    # Pipeline maps category properly
    pipeline_err = f"Synthesis failed [{stats['error_category']}]: {stats['error']}"
    assert _error_category(pipeline_err) == "rate_limit"
    public_msg = _public_error_message(pipeline_err, "err-123")
    assert "Free-tier request quota reached" in public_msg
    assert "Resource has been exhausted" not in public_msg


@pytest.mark.anyio
async def test_review_5nov_finding6_deadline_bounds_inflight_gemini_call():
    """P2 Finding 6: An in-flight generate() call exceeding deadline raises RequestTimeout bounded by deadline."""
    import asyncio
    import time
    from app.llm_gemini import generate, RequestTimeout

    async def slow_generate(*args, **kwargs):
        await asyncio.sleep(0.50)
        return MagicMock()

    with patch("app.llm_gemini.get_gemini_client") as mock_client_getter:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = slow_generate
        mock_client_getter.return_value = mock_client

        t0 = time.monotonic()
        with pytest.raises(RequestTimeout):
            await generate(
                ["Test prompt"],
                model="gemini-test",
                deadline=t0 + 0.05,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 0.25, f"Call was not bounded by deadline: took {elapsed:.3f}s"


@pytest.mark.anyio
async def test_review_5nov_finding7_empty_extracted_text_marked_failed_not_read():
    """P2 Finding 7: Extraction returning empty text is recorded as failed, not read.
    Fallback verification pass tries unread snippets instead of skipping due to attempted fetches."""
    from google.genai import types
    from app.research import run_research_gemini
    from app.schema import ProviderInfo, Scenario, ComposioInfo

    provider = ProviderInfo(name="Example", official_domain="example.com", identity_status="assumed")
    scenario = Scenario(feature="sync", audience="external customers")
    composio = ComposioInfo(lookup_status="no_match")

    url1 = "https://example.com/empty"
    url2 = "https://example.com/valid"

    # Turn 1: model searches
    fc_search = types.FunctionCall(name="web_search", args={"query": "example oauth"})
    cand1 = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc_search)]))
    resp1 = types.GenerateContentResponse(candidates=[cand1])

    # Turn 2: model reads url1
    fc_read = types.FunctionCall(name="read_page", args={"url": url1})
    cand2 = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc_read)]))
    resp2 = types.GenerateContentResponse(candidates=[cand2])

    # Turn 3: model returns snippet-only findings
    cand3 = types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="## Findings\nOAuth info")]))
    resp3 = types.GenerateContentResponse(candidates=[cand3])

    # Turn 4: evidence check calls model after reading url2
    cand4 = types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="## Refined Findings\nVerified from url2")]))
    resp4 = types.GenerateContentResponse(candidates=[cand4])

    async def mock_extract(urls):
        success = {}
        failure = {}
        for u in urls:
            if u == url1:
                # Returns empty whitespace
                success[u] = "   \n\t   "
            elif u == url2:
                success[u] = "Valid documentation content about OAuth endpoints"
        return success, failure

    with patch("app.research.generate", AsyncMock(side_effect=[(resp1, {}), (resp2, {}), (resp3, {}), (resp4, {})])):
        with patch("app.web_tools.search", AsyncMock(return_value=[
            {"title": "Empty Page", "url": url1, "content": "Snippet 1"},
            {"title": "Valid Docs", "url": url2, "content": "Snippet 2"},
        ])):
            with patch("app.web_tools.extract", side_effect=mock_extract):
                with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key", "TAVILY_API_KEY": "fake_key"}):
                    findings, sources, stats, page_texts = await run_research_gemini(
                        provider, scenario, composio, deadline=9999999999.0
                    )

    # url1 must be failed, NOT read
    s1 = next(s for s in sources if s.url == url1)
    assert s1.retrieval_status == "failed"
    assert s1.id not in page_texts

    # url2 must have been picked up by the fallback evidence check and marked read
    s2 = next(s for s in sources if s.url == url2)
    assert s2.retrieval_status == "read"
    assert s2.id in page_texts
    assert page_texts[s2.id] == "Valid documentation content about OAuth endpoints"

    # stats must report 1 successful page read (not 2, not 0)
    assert stats["page_reads"] == 1
    assert stats["fetch_attempts"] == 2

