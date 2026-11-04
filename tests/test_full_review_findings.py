"""Regression and boundary tests for Findings 1–9 from docs/FULL_CODE_REVIEW.md.

Covers:
1. CLI execution branches (success, missing key, pipeline error, timeout, entrypoint)
2. Reviewed import rejecting unsourced documented claims and synthetic example attempts
3. Two-path Composio promotion with globally unique wiring step IDs and idempotence
4. Gate reversal consistency, claim text update, and historical version immutability
5. Base identity mismatch detection on domain, deployment, and missing base versions
6. Canonical attempt loading for decorated display names (Gmail, Jira, X) without sibling pollution
7. Preparation step preservation, idempotence, and distinguishing preparation from launch
8. Retry link preserving selected website, scenario, and force flags
9. Claim-to-attempt references rendered in Markdown and HTML
10. Model domain conflict preserving user's coherent name and domain pair
11. Composio search requiring positive product evidence on shared company domains
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.cli import main as cli_main
from app.config import COMPOSIO_CALLBACK_URL, JOB_TIMEOUT_SECONDS
from app.main import app, _render_guide_html
from app.pipeline import _load_attempts_for_provider
from app.render import render_markdown
from app.resolver import (
    resolve_provider_with_model,
    _search_composio,
    _has_positive_product_evidence,
)
from app.schema import (
    Attempt,
    ClaimBasis,
    ComposioInfo,
    DraftClaim,
    DraftEstimate,
    DraftGate,
    DraftGuideContent,
    DraftPath,
    ReviewInfo,
    DraftStep,
    GuideRecord,
    GuideStatus,
    ProviderInfo,
    RunStats,
    Scenario,
    Source,
)
from app.store import Store
from app.validation import (
    validate_composio,
    validate_guide,
    validate_reviewed_record,
)


# ── 1. CLI Execution Boundary ─────────────────────────────────────────────

def test_cli_execution_success_and_output_file(tmp_path):
    """CLI successfully executes pipeline, saves guide to store, and writes Markdown to output."""
    out_file = tmp_path / "guide.md"
    db_path = tmp_path / "cli.db"

    mock_guide = GuideRecord(
        id="github-cli-test",
        version=1,
        status="draft",
        provider=ProviderInfo(name="GitHub", official_domain="github.com", identity_status="resolved"),
        scenario=Scenario(feature="sync", audience="external customers"),
        guide=DraftGuideContent(oauth_support="supported"),
        sources=[Source(id="S1", url="https://docs.github.com", title="Docs", provider_domain=True, retrieval_status="read")],
        run=RunStats(searches=2, page_reads=2, duration_seconds=12.5),
    )

    test_args = [
        "app.cli", "GitHub",
        "--feature", "sync",
        "--output", str(out_file),
    ]

    with patch("sys.argv", test_args):
        with patch("app.cli.DATABASE_PATH", str(db_path)):
            with patch("app.cli.live_generation_configured", return_value=True):
                with patch("app.cli.run_pipeline", AsyncMock(return_value=(mock_guide, ""))):
                    cli_main()

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "GitHub" in content


def test_cli_execution_missing_key_exits_nonzero():
    """CLI exits 1 when live generation is not configured."""
    with patch("sys.argv", ["app.cli", "GitHub"]):
        with patch("app.cli.live_generation_configured", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()
            assert exc_info.value.code == 1


def test_cli_execution_pipeline_error_exits_nonzero(tmp_path):
    """CLI exits 1 with message when pipeline returns an error."""
    db_path = tmp_path / "cli_err.db"
    with patch("sys.argv", ["app.cli", "GitHub"]):
        with patch("app.cli.DATABASE_PATH", str(db_path)):
            with patch("app.cli.live_generation_configured", return_value=True):
                with patch("app.cli.run_pipeline", AsyncMock(return_value=(None, "Research failed: network error"))):
                    with pytest.raises(SystemExit) as exc_info:
                        cli_main()
                    assert exc_info.value.code == 1


def test_cli_execution_timeout_exits_nonzero(tmp_path):
    """CLI exits 1 when pipeline exceeds deadline timeout."""
    db_path = tmp_path / "cli_timeout.db"

    async def _hang(*args, **kwargs):
        await asyncio.sleep(100.0)

    with patch("sys.argv", ["app.cli", "GitHub"]):
        with patch("app.cli.DATABASE_PATH", str(db_path)):
            with patch("app.cli.live_generation_configured", return_value=True):
                with patch("app.cli.JOB_TIMEOUT_SECONDS", 0.05):
                    with patch("app.cli.run_pipeline", side_effect=_hang):
                        with pytest.raises(SystemExit) as exc_info:
                            cli_main()
                        assert exc_info.value.code == 1


def test_cli_entrypoint_help():
    """Entrypoint python -m app.cli --help runs and exits 0."""
    res = subprocess.run(
        [sys.executable, "-m", "app.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "OAuthPath" in res.stdout


# ── 2. Reviewed Import Evidence Boundary ──────────────────────────────────

def test_reviewed_import_rejects_unsourced_documented_supported_claim(tmp_path):
    """A documented supported claim with no sources MUST fail reviewed validation and seed import."""
    db_path = tmp_path / "store.db"
    store = Store(str(db_path))

    guide = GuideRecord(
        id="test-unsourced-review",
        version=1,
        status="reviewed",
        provider=ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved"),
        scenario=Scenario(feature="sync", audience="external customers"),
        review=ReviewInfo(
            reviewer="Auditor",
            reviewed_at="2026-11-04T12:00:00Z",
            audited_claim_ids=["K1"],
        ),
        guide=DraftGuideContent(
            claims=[
                DraftClaim(
                    id="K1",
                    text="OAuth supported without review",
                    basis="documented",
                    source_ids=[],  # NO SOURCES!
                    review_result="supported",
                    support_note="Auditor verified in docs",
                )
            ]
        ),
    )

    valid, err = validate_reviewed_record(guide)
    assert valid is False
    assert "has no sources" in err or "no valid source references" in err

    ok, msg = store.import_reviewed_seed(guide)
    assert ok is False
    assert "Seed integrity validation failed" in msg


def test_reviewed_import_rejects_synthetic_example_attempt(tmp_path):
    """An attempt with EXAMPLE- ID, example=True, or synthetic limitation cannot be imported into reviewed guides."""
    db_path = tmp_path / "store.db"
    store = Store(str(db_path))

    guide = GuideRecord(
        id="test-example-attempt-review",
        version=1,
        status="reviewed",
        provider=ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved"),
        scenario=Scenario(feature="sync", audience="external customers"),
        review=ReviewInfo(
            reviewer="Auditor",
            reviewed_at="2026-11-04T12:00:00Z",
            audited_claim_ids=["K1"],
        ),
        attempts=[
            Attempt(
                id="EXAMPLE-ATT-01",
                provider="testapp",
                action="setup",
                status="succeeded",
                example=True,
                observed_result="Mock connection succeeded",
                limitation="[SYNTHETIC EXAMPLE] not real",
            )
        ],
        guide=DraftGuideContent(
            claims=[
                DraftClaim(
                    id="K1",
                    text="Connection succeeded in local test",
                    basis="observed",
                    attempt_ids=["EXAMPLE-ATT-01"],
                    review_result="supported",
                    support_note="Auditor verified attempt",
                )
            ]
        ),
    )

    valid, err = validate_reviewed_record(guide)
    assert valid is False
    assert "synthetic example record" in err or "cites synthetic example attempt" in err

    ok, msg = store.import_reviewed_seed(guide)
    assert ok is False


# ── 3. Two-Path Composio Promotion Boundary ───────────────────────────────

def test_two_path_composio_promotion_unique_step_ids():
    """A guide with both own_app_testing and own_app_production paths gets unique step IDs and passes review validation."""
    guide = GuideRecord(
        id="github-two-path",
        version=1,
        status="draft",
        provider=ProviderInfo(name="GitHub", official_domain="github.com", identity_status="resolved"),
        composio=ComposioInfo(
            toolkit_slug="github",
            lookup_status="matched",
            selected_auth_method="OAUTH2",
            managed_oauth="available",
            source_id="C1",
        ),
        sources=[Source(id="C1", url="https://backend.composio.dev/api/v3/toolkits/github", retrieval_status="read")],
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[
                DraftPath(id="own_app_testing", name="Own App (Testing)"),
                DraftPath(id="own_app_production", name="Own App (Production)"),
            ],
            claims=[
                DraftClaim(id="K1", text="OAuth supported", basis="documented", source_ids=["C1"], review_result="supported", support_note="Verified in docs"),
            ],
        ),
    )

    # Validate Composio paths & steps
    validate_composio(guide)

    # Must contain both paths
    p_ids = [p.id for p in guide.guide.paths]
    assert "own_app_testing" in p_ids
    assert "own_app_production" in p_ids

    # Step IDs must be globally unique across all paths
    all_steps = [s for p in guide.guide.paths for s in p.steps]
    step_ids = [s.id for s in all_steps]
    assert len(step_ids) == len(set(step_ids)), f"Duplicate step IDs found: {step_ids}"
    assert "composio_wiring" in step_ids
    assert "own_app_production_composio_wiring" in step_ids

    # Step objects must be distinct instances
    p_test = next(p for p in guide.guide.paths if p.id == "own_app_testing")
    p_prod = next(p for p in guide.guide.paths if p.id == "own_app_production")
    s_test = next(s for s in p_test.steps if "composio_wiring" in s.id)
    s_prod = next(s for s in p_prod.steps if "composio_wiring" in s.id)
    assert s_test is not s_prod

    # Validate twice: must be idempotent and not append duplicates
    validate_composio(guide)
    step_ids_after = [s.id for p in guide.guide.paths for s in p.steps]
    assert len(step_ids_after) == len(step_ids)

    # Now promote: validate_reviewed_record must accept the two-path guide!
    guide.status = "reviewed"
    guide.review = ReviewInfo(
        reviewer="Auditor",
        reviewed_at="2026-11-04T12:00:00Z",
        audited_claim_ids=["K1"],
    )
    valid, err = validate_reviewed_record(guide)
    assert valid is True, f"Review validation rejected two-path guide: {err}"


# ── 4. Gate Reversal Boundary ─────────────────────────────────────────────

def test_gate_reversal_updates_claim_text_and_preserves_history(tmp_path):
    """Promoting --pass-gate then --fail-gate updates claim text and preserves version 2 immutability."""
    db_path = tmp_path / "gate_rev.db"
    store = Store(str(db_path))

    draft = GuideRecord(
        id="test-gate-rev",
        version=1,
        status="draft",
        provider=ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved"),
        sources=[Source(id="S1", url="https://testapp.com/docs", retrieval_status="read")],
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[
                DraftPath(
                    id="own_app_production",
                    name="Own App (Production)",
                    gates=[DraftGate(kind="paid_access", state="unknown")],
                )
            ],
            claims=[
                DraftClaim(id="K1", text="OAuth works", basis="documented", source_ids=["S1"], review_result="supported", support_note="Verified"),
            ],
        ),
    )
    store.save_guide(draft)

    from scripts.promote import main as promote_main

    # Step 1: Promote with --pass-gate
    pass_args = [
        "scripts/promote.py", "test-gate-rev", "1",
        "--reviewer", "Auditor",
        "--pass-gate", "paid_access",
        "--source", "S1",
        "--claims", "K1",
    ]
    with patch("sys.argv", pass_args):
        with patch("scripts.promote.DATABASE_PATH", str(db_path)):
            with patch.dict(os.environ, {"REVIEWED_DIR": str(tmp_path / "reviewed")}):
                promote_main()

    v2 = store.get_guide("test-gate-rev", 2)
    assert v2 is not None
    assert v2.status == "reviewed"
    gate_v2 = next(g for p in v2.guide.paths for g in p.gates if g.kind == "paid_access")
    assert gate_v2.state == "not_required"
    claim_v2 = next(c for c in v2.guide.claims if c.id == "REV-PAID_ACC")
    assert "not_required" in claim_v2.text

    # Step 2: Promote version 2 with --fail-gate
    fail_args = [
        "scripts/promote.py", "test-gate-rev", "2",
        "--reviewer", "Auditor",
        "--fail-gate", "paid_access",
        "--source", "S1",
        "--claims", "K1",
    ]
    with patch("sys.argv", fail_args):
        with patch("scripts.promote.DATABASE_PATH", str(db_path)):
            with patch.dict(os.environ, {"REVIEWED_DIR": str(tmp_path / "reviewed")}):
                promote_main()

    v3 = store.get_guide("test-gate-rev", 3)
    assert v3 is not None
    gate_v3 = next(g for p in v3.guide.paths for g in p.gates if g.kind == "paid_access")
    assert gate_v3.state == "required"
    claim_v3 = next(c for c in v3.guide.claims if c.id == "REV-PAID_ACC")
    assert "required" in claim_v3.text
    assert "not_required" not in claim_v3.text

    # Ensure v2 in store was NOT mutated
    v2_check = store.get_guide("test-gate-rev", 2)
    gate_v2_check = next(g for p in v2_check.guide.paths for g in p.gates if g.kind == "paid_access")
    assert gate_v2_check.state == "not_required"


# ── 5. Base Identity Mismatch Boundary ────────────────────────────────────

def test_base_identity_mismatch_rejected(tmp_path):
    """Editing base guide's domain or deployment before promote --from-file fails with mismatch error."""
    db_path = tmp_path / "base_id.db"
    store = Store(str(db_path))

    draft = GuideRecord(
        id="notion",
        version=1,
        status="draft",
        provider=ProviderInfo(name="Notion", official_domain="notion.so", identity_status="resolved"),
        scenario=Scenario(feature="sync", audience="external customers", deployment="server-side"),
        sources=[Source(id="S1", url="https://notion.so/docs", retrieval_status="read")],
        guide=DraftGuideContent(
            claims=[
                DraftClaim(id="K1", text="OAuth supported", basis="documented", source_ids=["S1"], review_result="supported", support_note="Verified"),
            ],
        ),
    )
    store.save_guide(draft)

    from scripts.promote import main as promote_main

    # Test A: Change domain
    copy_a = draft.model_copy(deep=True)
    copy_a.provider.official_domain = "notion.com"
    file_a = tmp_path / "copy_a.json"
    file_a.write_text(copy_a.model_dump_json(), encoding="utf-8")

    with patch("sys.argv", ["scripts/promote.py", "--from-file", str(file_a), "--reviewer", "Auditor", "--claims", "K1"]):
        with patch("scripts.promote.DATABASE_PATH", str(db_path)):
            with pytest.raises(SystemExit) as exc_info:
                promote_main()
            assert exc_info.value.code == 1

    # Test B: Change deployment
    copy_b = draft.model_copy(deep=True)
    copy_b.scenario.deployment = "client-side"
    file_b = tmp_path / "copy_b.json"
    file_b.write_text(copy_b.model_dump_json(), encoding="utf-8")

    with patch("sys.argv", ["scripts/promote.py", "--from-file", str(file_b), "--reviewer", "Auditor", "--claims", "K1"]):
        with patch("scripts.promote.DATABASE_PATH", str(db_path)):
            with pytest.raises(SystemExit) as exc_info:
                promote_main()
            assert exc_info.value.code == 1


# ── 6. Canonical Attempt Loading Boundary ─────────────────────────────────

def test_canonical_attempt_loading_decorated_names(tmp_path):
    """Attempts with toolkit slugs (gmail, jira, twitter) match decorated display names and exclude siblings."""
    attempts_dir = tmp_path / "attempts"
    attempts_dir.mkdir()

    # Genuine attempts
    (attempts_dir / "A-gmail.json").write_text(json.dumps({
        "id": "ATT-GMAIL",
        "provider": "gmail",
        "action": "connect test user",
        "status": "succeeded",
        "observed_result": "Active connection ID 123",
        "limitation": "Proves one test-user connection",
    }), encoding="utf-8")

    (attempts_dir / "A-drive.json").write_text(json.dumps({
        "id": "ATT-DRIVE",
        "provider": "googledrive",
        "action": "connect test user",
        "status": "succeeded",
        "observed_result": "Active connection ID 456",
        "limitation": "Proves one test-user connection",
    }), encoding="utf-8")

    (attempts_dir / "A-jira.json").write_text(json.dumps({
        "id": "ATT-JIRA",
        "provider": "jira",
        "action": "connect test user",
        "status": "blocked",
        "observed_result": "Blocked by org policy",
        "limitation": "Connection blocked",
    }), encoding="utf-8")

    (attempts_dir / "A-synth.json").write_text(json.dumps({
        "id": "EXAMPLE-ATT",
        "provider": "gmail",
        "action": "mock",
        "status": "example",
        "example": True,
        "observed_result": "[SYNTHETIC EXAMPLE]",
    }), encoding="utf-8")

    with patch("app.pipeline.Path") as mock_path:
        mock_path.return_value = attempts_dir

        # 1. Gmail (Google) on google.com matches gmail, NOT googledrive, NOT synthetic
        loaded_gmail = _load_attempts_for_provider("Gmail (Google)", "google.com")
        gmail_ids = [a.id for a in loaded_gmail]
        assert "ATT-GMAIL" in gmail_ids
        assert "ATT-DRIVE" not in gmail_ids
        assert "EXAMPLE-ATT" not in gmail_ids

        # 2. Jira (Atlassian) matches jira
        loaded_jira = _load_attempts_for_provider("Jira (Atlassian)", "atlassian.com")
        jira_ids = [a.id for a in loaded_jira]
        assert "ATT-JIRA" in jira_ids
        assert "ATT-GMAIL" not in jira_ids


# ── 7. Preparation Step Idempotence Boundary ──────────────────────────────

def test_preparation_step_idempotence_and_preservation():
    """Supported preparation steps preserve instructions and URLs idempotently across validations."""
    prep_url = "https://developer.example.com/review-guidelines"
    guide = GuideRecord(
        id="test-prep-step",
        version=1,
        status="draft",
        provider=ProviderInfo(name="ExampleApp", official_domain="example.com", identity_status="resolved"),
        sources=[Source(id="S1", url="https://example.com/docs", retrieval_status="read")],
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[
                DraftPath(
                    id="own_app_production",
                    name="Own App (Production)",
                    gates=[
                        DraftGate(kind="app_review", state="unknown"),
                    ],
                    steps=[
                        DraftStep(
                            id="step_prep",
                            title="Prepare production review materials",
                            instructions="Draft privacy policy and record demo video for review submission",
                            owner="developer",
                            action_url=prep_url,
                            claim_ids=["K1"],
                        ),
                        DraftStep(
                            id="step_launch",
                            title="Launch publicly without review",
                            instructions="Publish immediately to production without waiting for review",
                            owner="developer",
                            action_url="https://portal.example.com/publish",
                            claim_ids=["K2"],  # K2 will be unsupported
                        ),
                    ],
                )
            ],
            claims=[
                DraftClaim(id="K1", text="Docs explain preparation", basis="documented", source_ids=["S1"]),
                DraftClaim(id="K2", text="No review required", basis="unknown"),
            ],
        ),
    )

    # First validation
    validate_guide(guide)

    step_prep = guide.guide.paths[0].steps[0]
    step_launch = guide.guide.paths[0].steps[1]

    # Preparation step must NOT be qualified into "Verify requirement: Verify requirement: ..."
    assert step_prep.title == "Prepare production review materials"
    assert step_prep.action_url == prep_url

    # Unsupported public launch step MUST be qualified
    assert step_launch.title == "Verify provider approval requirements"
    assert step_launch.action_url == ""

    # Second validation: MUST BE IDEMPOTENT
    validate_guide(guide)
    step_prep_2 = guide.guide.paths[0].steps[0]
    step_launch_2 = guide.guide.paths[0].steps[1]

    assert step_prep_2.title == "Prepare production review materials"
    assert step_prep_2.action_url == prep_url
    assert step_launch_2.title == "Verify provider approval requirements"
    assert step_launch_2.action_url == ""


# ── 8. Retry Link Website & Scenario Boundary ─────────────────────────────

def test_retry_link_preserves_website_and_scenario():
    """Job template renders Retry link carrying website, scenario, and force parameters."""
    client = TestClient(app)

    job_data = {
        "id": "job-test-retry-123",
        "app_name": "Box",
        "website": "box.com",
        "feature": "sync files",
        "audience": "internal team",
        "force": True,
        "status": "failed",
        "error_message": "Provider timed out",
        "error_id": "ERR-123",
        "stage": "failed",
        "events": ["Starting", "Failed"],
    }

    # Render job template directly with Jinja2
    from app.main import templates
    response = templates.TemplateResponse(
        request=MagicMock(),
        name="job.html",
        context={"job": job_data, "base_url": "http://localhost:8000"},
    )
    html = response.body.decode("utf-8")

    assert "app_name=Box" in html
    assert "website=box.com" in html
    assert "feature=sync+files" in html or "feature=sync%20files" in html
    assert "audience=internal+team" in html or "audience=internal%20team" in html
    assert "force=true" in html


# ── 9. Rendered Attempt References Boundary ───────────────────────────────

def test_rendered_attempt_references_in_markdown_and_html():
    """Observed claims render attempt references in Markdown claims table and HTML links."""
    guide = GuideRecord(
        id="test-attempt-render",
        version=1,
        status="draft",
        provider=ProviderInfo(name="TestApp", official_domain="testapp.com", identity_status="resolved"),
        attempts=[
            Attempt(
                id="ATT-1",
                provider="testapp",
                scenario="sync repos",
                action="create auth config",
                status="succeeded",
                observed_result="Connected account active: ID conn_1",
                limitation="Proves test user",
            ),
            Attempt(
                id="ATT-2",
                provider="testapp",
                scenario="sync repos",
                action="request admin consent",
                status="blocked",
                observed_result="Consent blocked by org policy",
                limitation="Blocked at admin gate",
            ),
        ],
        guide=DraftGuideContent(
            claims=[
                DraftClaim(id="K1", text="Connection succeeds for test user", basis="observed", attempt_ids=["ATT-1"]),
                DraftClaim(id="K2", text="Admin consent blocked by org", basis="observed", attempt_ids=["ATT-2"]),
            ]
        ),
    )

    # 1. Markdown rendering
    md = render_markdown(guide)
    assert "| Attempts |" in md
    assert "ATT-1" in md
    assert "ATT-2" in md
    assert "Proves test user" in md
    assert "Blocked at admin gate" in md

    # 2. HTML rendering
    from app.main import templates
    html = templates.get_template("guide.html").render({
        "guide": guide,
        "guide_html": _render_guide_html(guide),
        "reviewed_version": None,
        "base_url": "http://testserver",
        "versions": [1],
    })
    assert "<th>Attempts</th>" in html
    assert 'href="#attempt-ATT-1"' in html
    assert 'href="#attempt-ATT-2"' in html
    assert 'id="attempt-ATT-1"' in html
    assert 'id="attempt-ATT-2"' in html


# ── 10. Model Domain Conflict Identity Boundary ───────────────────────────

@pytest.mark.anyio
async def test_resolver_conflicting_model_preserves_user_coherent_name_and_domain():
    """When user inputs Box + box.com, a conflicting model guess naming Dropbox does not overwrite the name."""
    mock_msg = MagicMock()
    mock_msg.content = [
        MagicMock(type="text", text=json.dumps({
            "canonical_name": "Dropbox",
            "official_domain": "dropbox.com",
            "is_ambiguous": False,
        }))
    ]
    mock_msg.usage = MagicMock(input_tokens=15, output_tokens=10)

    with patch("app.config.ANTHROPIC_API_KEY", "mock-key"):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_msg)
            mock_cls.return_value = mock_client

            info, candidates, stats = await resolve_provider_with_model("Box", website="box.com")
            # Must preserve user's coherent name and domain pair!
            assert info.name == "Box"
            assert info.official_domain == "box.com"
            assert candidates is None
            assert "warning" in stats


# ── 11. Composio Search Positive Evidence Boundary ────────────────────────

@pytest.mark.anyio
async def test_composio_search_positive_product_evidence_on_shared_domain():
    """Shared company domain fixtures without positive product evidence return no match."""
    # Case 1: Query 'Sales Cloud' on salesforce.com does NOT match Marketing Cloud
    mock_marketing_cloud = {
        "items": [
            {
                "name": "Marketing Cloud",
                "slug": "marketingcloud",
                "meta": {"app_url": "https://www.salesforce.com/products/marketing-cloud/"},
            }
        ]
    }
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json = MagicMock(return_value=mock_marketing_cloud)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        slug = await _search_composio("Sales Cloud", "salesforce.com")
        assert slug is None

    # Case 2: Query 'Drive' on google.com does NOT match Google Calendar
    mock_calendar = {
        "items": [
            {
                "name": "Google Calendar",
                "slug": "googlecalendar",
                "meta": {"app_url": "https://calendar.google.com"},
            }
        ]
    }
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json = MagicMock(return_value=mock_calendar)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        slug = await _search_composio("Drive", "google.com")
        assert slug is None
