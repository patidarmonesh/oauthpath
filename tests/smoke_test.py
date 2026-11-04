"""Quick integration smoke test with assertions covering items 8, 10-13, 15, 21, 28, 30."""
import sys
import os
from pathlib import Path
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schema import *
from app.validation import validate_guide
from app.render import render_markdown
from app.synthesis import GUIDE_SCHEMA
from app.resolver import resolve_provider
from app.main import app, CreateJobRequest


def test_smoke():
    guide = GuideRecord(
        schema_version="2", prompt_version="1",
        id="test-github", version=1, created_at="2026-11-03T00:00:00Z",
        provider=ProviderInfo(name="GitHub", official_domain="github.com", identity_status="resolved"),
        scenario=Scenario(feature="repo access", audience="external customers"),
        composio=ComposioInfo(toolkit_slug="github", lookup_status="matched",
                              selected_auth_method="OAUTH2", managed_oauth="available", source_id="C1"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            summary=[DraftSummaryStatement(text="GitHub OAuth is self-service", claim_ids=["K1"])],
            next_action=DraftNextAction(instruction="Register an OAuth App", owner="you",
                                         success_condition="Client ID visible"),
            paths=[DraftPath(id="own_app_testing", name="Your app: Testing",
                            gates=[DraftGate(kind="app_review", state="not_required", claim_ids=["K1"])],
                            steps=[DraftStep(id="register", title="Register", instructions="Go to GH settings",
                                           owner="you", success_condition="Done")])],
            claims=[DraftClaim(id="K1", text="GitHub supports OAuth", source_ids=["S1"], basis="documented")],
        ),
        sources=[
            Source(id="C1", url="https://backend.composio.dev/api/v3/toolkits/github", retrieval_status="read"),
            Source(id="S1", url="https://docs.github.com/en/apps", retrieval_status="read"),
        ],
    )

    warnings = validate_guide(guide)
    md = render_markdown(guide)

    assert guide.status in ["draft", "partial", "reviewed"], f"Unexpected status: {guide.status}"
    assert len(guide.guide.paths) >= 2, f"Expected at least 2 paths, got {len(guide.guide.paths)}"
    managed = any(p.id == "composio_managed" for p in guide.guide.paths)
    assert managed, "Expected composio_managed path to be present"
    wiring = any(s.id == "composio_wiring" for p in guide.guide.paths for s in p.steps)
    assert wiring, "Expected composio_wiring step to be present"
    overall = guide.difficulty.get("overall")
    assert overall is not None, "Expected overall difficulty to be computed"
    assert len(md) > 100, "Markdown rendering produced too little content"
    assert "GitHub" in md, "Expected GitHub in Markdown output"
    assert "javascript:" not in md, "Dangerous scheme found in Markdown"


def test_smoke_item_8_markdown_route_precedence():
    """Item 8: Markdown route is declared above HTML route to avoid capturing 1.md."""
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    md_idx = routes.index("/guides/{guide_id}/versions/{version}.md")
    html_idx = routes.index("/guides/{guide_id}/versions/{version}")
    assert md_idx < html_idx, f".md route ({md_idx}) must precede HTML route ({html_idx})"


def test_smoke_item_10_model_cannot_write_review_fields():
    """Item 10: excerpt_status is removed from GUIDE_SCHEMA."""
    claim_props = GUIDE_SCHEMA["properties"]["claims"]["items"]["properties"]
    assert "excerpt_status" not in claim_props, "excerpt_status must not be in synthesis GUIDE_SCHEMA"


def test_smoke_item_11_gate_evidence_enforced():
    """Item 11: Gate without documented/observed claim is downgraded to unknown with warning."""
    gate = DraftGate(kind="app_review", state="not_required", claim_ids=[])
    guide = GuideRecord(
        schema_version="2", prompt_version="1", id="test-gate", version=1,
        created_at="2026-11-03T00:00:00Z",
        provider=ProviderInfo(name="Test", official_domain="test.com", identity_status="resolved"),
        scenario=Scenario(feature="f", audience="external customers"),
        composio=ComposioInfo(lookup_status="no_match"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[DraftPath(id="own_app_production", name="Prod", gates=[gate], steps=[])],
            claims=[]
        ),
        sources=[]
    )
    warnings = validate_guide(guide)
    assert guide.guide.paths[0].gates[0].state == "unknown", "Gate without claims must be downgraded to unknown"
    assert any("downgraded to 'unknown'" in w for w in warnings), "Must emit warning for unbacked gate"


def test_smoke_item_12_composio_claims_and_sources():
    """Item 12: Composio claims KC1-KC3 and sources C2-C3 are added and not pruned."""
    guide = GuideRecord(
        schema_version="2", prompt_version="1", id="test-comp", version=1,
        created_at="2026-11-03T00:00:00Z",
        provider=ProviderInfo(name="GitHub", official_domain="github.com", identity_status="resolved"),
        scenario=Scenario(feature="f", audience="external customers"),
        composio=ComposioInfo(toolkit_slug="github", lookup_status="matched", selected_auth_method="OAUTH2",
                              managed_oauth="available", source_id="C1"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[DraftPath(id="own_app_testing", name="Test", gates=[],
                             steps=[DraftStep(id="s1", title="S", instructions="I", owner="you", success_condition="D")])],
            claims=[]
        ),
        sources=[Source(id="C1", url="https://backend.composio.dev/api/v3/toolkits/github", retrieval_status="read")]
    )
    validate_guide(guide)
    claim_ids = {c.id for c in guide.guide.claims}
    assert {"KC1", "KC2", "KC3"}.issubset(claim_ids), f"KC claims missing: {claim_ids}"
    source_ids = {s.id for s in guide.sources}
    assert {"C2", "C3"}.issubset(source_ids), f"C2/C3 sources missing: {source_ids}"


def test_smoke_item_13_difficulty_explicit_unknowns_only():
    """Item 13: Non-production paths only count explicit unknown gates towards difficulty."""
    gate = DraftGate(kind="app_review", state="not_required", claim_ids=["K1"])
    guide = GuideRecord(
        schema_version="2", prompt_version="1", id="test-diff", version=1,
        created_at="2026-11-03T00:00:00Z",
        provider=ProviderInfo(name="Test", official_domain="test.com", identity_status="resolved"),
        scenario=Scenario(feature="f", audience="internal developers"),
        composio=ComposioInfo(lookup_status="no_match"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[DraftPath(id="own_app_testing", name="Testing", gates=[gate], steps=[])],
            claims=[DraftClaim(id="K1", text="Docs say no review", source_ids=["S1"], basis="documented")]
        ),
        sources=[Source(id="S1", url="https://test.com/docs", retrieval_status="read")]
    )
    validate_guide(guide)
    diff = guide.difficulty.get("own_app_testing")
    assert diff is not None, "Difficulty for own_app_testing should exist"
    assert diff.label == "Straightforward", f"Expected Straightforward, got {diff.label}"


def test_smoke_item_15_plain_text_fields_normalized_and_enums():
    """Item 15: GUIDE_SCHEMA enums and path ID normalization."""
    gate_props = GUIDE_SCHEMA["properties"]["paths"]["items"]["properties"]["gates"]["items"]["properties"]
    assert "enum" in gate_props["kind"], "Gate kind must have enum constraint in GUIDE_SCHEMA"
    assert "enum" in gate_props["state"], "Gate state must have enum constraint in GUIDE_SCHEMA"

    bad_path = DraftPath(id="custom_unknown_path", name="Bad", gates=[], steps=[])
    guide = GuideRecord(
        schema_version="2", prompt_version="1", id="test-norm", version=1,
        created_at="2026-11-03T00:00:00Z",
        provider=ProviderInfo(name="Test", official_domain="test.com", identity_status="resolved"),
        scenario=Scenario(feature="f", audience="external customers"),
        composio=ComposioInfo(lookup_status="no_match"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            paths=[bad_path],
            claims=[]
        ),
        sources=[]
    )
    validate_guide(guide)
    path_ids = [p.id for p in guide.guide.paths]
    assert "custom_unknown_path" not in path_ids, f"Invalid path ID must be dropped, found {path_ids}"


def test_smoke_item_21_website_does_not_override_mismatched_name():
    """Item 21: Website domain does not override mismatched app names."""
    info1, _ = resolve_provider("Confluence", website="atlassian.com")
    assert info1.name != "Jira", f"Confluence must not be overridden to Jira, got {info1.name}"
    info2, _ = resolve_provider("Google Calendar", website="google.com")
    assert info2.name != "Gmail", f"Google Calendar must not be overridden to Gmail, got {info2.name}"
    info3, _ = resolve_provider("Outlook", website="microsoft.com")
    assert info3.name != "Microsoft", f"Outlook must not be overridden to Microsoft, got {info3.name}"


def test_smoke_item_28_job_request_input_validation():
    """Item 28: Pydantic body validation for CreateJobRequest."""
    try:
        CreateJobRequest(app_name="a")
        assert False, "Should fail on app_name < 2 chars"
    except ValidationError:
        pass

    try:
        CreateJobRequest(app_name="a" * 121)
        assert False, "Should fail on app_name > 120 chars"
    except ValidationError:
        pass

    try:
        CreateJobRequest(app_name="Valid App", audience="everyone")
        assert False, "Should fail on invalid audience"
    except ValidationError:
        pass

    valid = CreateJobRequest(app_name="Valid App", audience="external customers")
    assert valid.app_name == "Valid App"


def test_smoke_item_30_ambiguous_flow_prefills_domain():
    """Item 30: Ambiguous candidate links prefill parameters without losing context."""
    template_path = Path(__file__).resolve().parent.parent / "app" / "templates" / "job.html"
    html = template_path.read_text(encoding="utf-8")
    assert "app_name=" in html, "job.html must prefill app_name for ambiguous candidates"
    assert "feature=" in html, "job.html must preserve feature context"
