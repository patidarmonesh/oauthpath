"""Tests per §11.3 — focused, not cosmetic."""

import pytest
import json
import os
import sys
import tempfile

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schema import (
    GuideRecord, DraftGuideContent, DraftClaim, DraftGate, DraftPath,
    DraftStep, DraftEstimate, DraftSummaryStatement, DraftNextAction,
    DraftBlocker, DraftScope, DraftPrerequisite, DraftUnknown,
    Source, Attempt, ComposioInfo, ProviderInfo, Scenario,
    DifficultyInfo, RunStats, ReviewInfo,
    GateKind, GateState, OAuthSupport, GuideStatus, ManagedOAuth,
    make_guide_id, make_cache_key, convert_draft_estimate,
)
from app.validation import (
    validate_url, validate_claims, validate_estimates_in_guide,
    validate_urls_in_guide, validate_oauth_support, validate_composio,
    compute_difficulty, add_missing_critical_gates, determine_status,
    validate_guide, check_excerpts, validate_references,
    split_uncited_sources, check_review_integrity,
)
from app.store import Store
from app.render import render_markdown, format_estimate
from app.resolver import parse_domain, resolve_provider


# ── Estimates ──────────────────────────────────────────────────────────────

class TestEstimates:
    """Unknown becomes null; inverted bounds removed; valid ones kept."""

    def test_unknown_becomes_null(self):
        de = DraftEstimate(known=False, low=5, high=10, unit="hours", basis="unknown")
        est, warnings = convert_draft_estimate(de)
        assert est.low is None
        assert est.high is None
        assert est.basis == "unknown"

    def test_valid_estimate_kept(self):
        de = DraftEstimate(known=True, low=5, high=10, unit="hours", basis="author_estimate",
                           explanation="test")
        est, warnings = convert_draft_estimate(de)
        assert est.low == 5
        assert est.high == 10
        assert est.unit == "hours"
        assert not warnings

    def test_inverted_bounds_removed(self):
        de = DraftEstimate(known=True, low=20, high=5, unit="minutes", basis="provider_estimate")
        est, warnings = convert_draft_estimate(de)
        assert est.low is None
        assert est.high is None
        assert any("Inverted" in w for w in warnings)

    def test_negative_bounds_removed(self):
        de = DraftEstimate(known=True, low=-1, high=5, unit="hours", basis="author_estimate")
        est, warnings = convert_draft_estimate(de)
        assert any("Negative" in w or "negative" in w.lower() for w in warnings)

    def test_estimate_validation_in_guide(self):
        guide = _make_guide()
        guide.guide.paths = [
            DraftPath(
                id="own_app_testing", name="Test",
                effort=DraftEstimate(known=True, low=20, high=5),
                waiting=DraftEstimate(known=False),
            )
        ]
        warnings = validate_estimates_in_guide(guide)
        assert any("inverted" in w.lower() or "Inverted" in w for w in warnings)


# ── URL validation ────────────────────────────────────────────────────────

class TestURLValidation:
    """Rejects dangerous schemes; accepts https."""

    def test_rejects_javascript(self):
        valid, reason = validate_url("javascript:alert(1)")
        assert not valid

    def test_rejects_data(self):
        valid, reason = validate_url("data:text/html,<h1>Hi</h1>")
        assert not valid

    def test_rejects_http(self):
        valid, reason = validate_url("http://example.com")
        assert not valid

    def test_rejects_protocol_relative(self):
        valid, reason = validate_url("//evil.com/path")
        assert not valid

    def test_rejects_credentials_in_url(self):
        valid, reason = validate_url("https://user:pass@example.com")
        assert not valid

    def test_rejects_whitespace(self):
        valid, reason = validate_url("https://example.com/path with spaces")
        assert not valid

    def test_accepts_https(self):
        valid, reason = validate_url("https://docs.github.com/en/apps")
        assert valid

    def test_accepts_empty(self):
        valid, reason = validate_url("")
        assert valid

    def test_accepts_relative(self):
        valid, reason = validate_url("/guides/test")
        assert valid

    def test_rejects_localhost(self):
        valid, _ = validate_url("https://localhost/evil")
        assert not valid


# ── Claims validation ─────────────────────────────────────────────────────

class TestClaimsValidation:
    """Invented sources downgraded; observed without attempt downgraded."""

    def test_invented_source_ids_downgraded(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Test claim", source_ids=["S999"], basis="documented"),
        ]
        guide.sources = []  # no real sources
        warnings = validate_claims(guide)
        assert guide.guide.claims[0].basis == "unknown"
        assert any("invalid source" in w.lower() or "unknown" in w.lower() for w in warnings)

    def test_observed_without_attempt_downgraded(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Observed claim", source_ids=["S1"],
                       attempt_ids=["A999"], basis="observed"),
        ]
        guide.sources = [Source(id="S1", url="https://example.com", retrieval_status="read")]
        guide.attempts = []  # no matching attempt
        warnings = validate_claims(guide)
        assert guide.guide.claims[0].basis == "documented"
        assert any("observed" in w.lower() for w in warnings)

    def test_observed_with_matching_attempt_kept(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Observed claim", source_ids=["S1"],
                       attempt_ids=["A1"], basis="observed"),
        ]
        guide.sources = [Source(id="S1", url="https://example.com", retrieval_status="read")]
        guide.attempts = [Attempt(id="A1", action="test", status="succeeded")]
        warnings = validate_claims(guide)
        assert guide.guide.claims[0].basis == "observed"

    def test_duplicate_claim_ids_dropped(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="First"),
            DraftClaim(id="K1", text="Duplicate"),
            DraftClaim(id="K2", text="Second"),
        ]
        warnings = validate_claims(guide)
        assert len(guide.guide.claims) == 2
        assert guide.guide.claims[0].id == "K1"
        assert guide.guide.claims[1].id == "K2"

    def test_documented_without_source_becomes_unknown(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="No source", source_ids=[], basis="documented"),
        ]
        guide.sources = []
        warnings = validate_claims(guide)
        assert guide.guide.claims[0].basis == "unknown"


# ── Composio validation ───────────────────────────────────────────────────

class TestComposioValidation:
    """No match = no wiring; matched+available = managed path."""

    def test_no_match_no_wiring(self):
        guide = _make_guide()
        guide.composio = ComposioInfo(
            lookup_status="no_match",
            managed_oauth="unknown",
        )
        guide.guide.oauth_support = "confirmed"
        guide.guide.paths = [
            DraftPath(id="own_app_testing", name="Test"),
        ]
        warnings = validate_composio(guide)
        # No composio_managed path
        assert not any(p.id == "composio_managed" for p in guide.guide.paths)
        # No wiring step
        for p in guide.guide.paths:
            assert not any(s.id == "composio_wiring" for s in p.steps)

    def test_matched_available_adds_managed_path(self):
        guide = _make_guide()
        guide.composio = ComposioInfo(
            toolkit_slug="github",
            lookup_status="matched",
            selected_auth_method="OAUTH2",
            managed_oauth="available",
            source_id="C1",
        )
        guide.guide.oauth_support = "confirmed"
        guide.guide.paths = [
            DraftPath(id="own_app_testing", name="Test"),
        ]
        guide.sources = [Source(id="C1", url="https://backend.composio.dev/api/v3/toolkits/github")]
        warnings = validate_composio(guide)
        assert any(p.id == "composio_managed" for p in guide.guide.paths)
        # Managed path should be first
        assert guide.guide.paths[0].id == "composio_managed"

    def test_matched_adds_wiring_step(self):
        guide = _make_guide()
        guide.composio = ComposioInfo(
            toolkit_slug="slack",
            lookup_status="matched",
            selected_auth_method="OAUTH2",
            managed_oauth="unavailable",
            source_id="C1",
        )
        guide.guide.oauth_support = "confirmed"
        guide.guide.paths = [
            DraftPath(id="own_app_testing", name="Test"),
        ]
        guide.sources = [Source(id="C1", url="https://backend.composio.dev/api/v3/toolkits/slack")]
        warnings = validate_composio(guide)
        # No managed path (unavailable)
        assert not any(p.id == "composio_managed" for p in guide.guide.paths)
        # Wiring step added
        assert any(s.id == "composio_wiring" for s in guide.guide.paths[0].steps)

    def test_failed_lookup_means_unknown(self):
        guide = _make_guide()
        guide.composio = ComposioInfo(
            lookup_status="failed",
            managed_oauth="unknown",
        )
        guide.guide.oauth_support = "confirmed"
        guide.guide.paths = [DraftPath(id="own_app_testing", name="Test")]
        warnings = validate_composio(guide)
        assert not any(p.id == "composio_managed" for p in guide.guide.paths)

    def test_missing_auth_metadata_means_unknown(self):
        guide = _make_guide()
        guide.composio = ComposioInfo(
            toolkit_slug="xero",
            lookup_status="matched",
            selected_auth_method=None,  # No OAuth method found
            managed_oauth="unknown",
        )
        guide.guide.oauth_support = "confirmed"
        guide.guide.paths = [DraftPath(id="own_app_testing", name="Test")]
        warnings = validate_composio(guide)
        assert not any(p.id == "composio_managed" for p in guide.guide.paths)


# ── OAuth support ─────────────────────────────────────────────────────────

class TestOAuthSupport:
    """Unknown OAuth = no own-app paths, partial status."""

    def test_unknown_oauth_removes_paths(self):
        guide = _make_guide()
        guide.guide.oauth_support = "unknown"
        guide.guide.paths = [
            DraftPath(id="own_app_testing", name="Test"),
            DraftPath(id="own_app_production", name="Prod"),
        ]
        warnings = validate_oauth_support(guide)
        assert len(guide.guide.paths) == 0
        assert any("removed own-app" in w.lower() for w in warnings)

    def test_confirmed_oauth_keeps_paths(self):
        guide = _make_guide()
        guide.guide.oauth_support = "confirmed"
        guide.guide.paths = [
            DraftPath(id="own_app_testing", name="Test"),
        ]
        warnings = validate_oauth_support(guide)
        assert len(guide.guide.paths) == 1


# ── Difficulty ────────────────────────────────────────────────────────────

class TestDifficulty:
    """Unknown critical gate = provisional; app review = coordination."""

    def test_unknown_critical_gate_provisional(self):
        guide = _make_guide()
        guide.guide.paths = [
            DraftPath(
                id="own_app_production", name="Prod",
                gates=[
                    DraftGate(kind="app_review", state="required"),
                    DraftGate(kind="security_assessment", state="unknown"),
                ],
            ),
        ]
        add_missing_critical_gates(guide)
        compute_difficulty(guide)
        diff = guide.difficulty.get("own_app_production")
        assert diff is not None
        assert "provisional" in diff.label.lower()

    def test_app_review_requires_coordination(self):
        guide = _make_guide()
        guide.guide.paths = [
            DraftPath(
                id="own_app_testing", name="Test",
                gates=[
                    DraftGate(kind="app_review", state="required"),
                    DraftGate(kind="security_assessment", state="not_required"),
                    DraftGate(kind="business_or_identity_verification", state="not_required"),
                    DraftGate(kind="partner_program", state="not_required"),
                    DraftGate(kind="paid_access", state="not_required"),
                    DraftGate(kind="customer_admin_action", state="not_required"),
                ],
            ),
        ]
        compute_difficulty(guide)
        diff = guide.difficulty.get("own_app_testing")
        assert diff is not None
        assert "coordination" in diff.label.lower()

    def test_no_gates_straightforward(self):
        guide = _make_guide()
        guide.guide.paths = [
            DraftPath(
                id="own_app_testing", name="Test",
                gates=[
                    DraftGate(kind="app_review", state="not_required"),
                    DraftGate(kind="security_assessment", state="not_required"),
                    DraftGate(kind="business_or_identity_verification", state="not_required"),
                    DraftGate(kind="partner_program", state="not_required"),
                    DraftGate(kind="paid_access", state="not_required"),
                    DraftGate(kind="customer_admin_action", state="not_required"),
                ],
            ),
        ]
        compute_difficulty(guide)
        diff = guide.difficulty.get("own_app_testing")
        assert "straightforward" in diff.label.lower()

    def test_security_assessment_substantial(self):
        guide = _make_guide()
        guide.guide.paths = [
            DraftPath(
                id="own_app_production", name="Prod",
                gates=[
                    DraftGate(kind="security_assessment", state="required"),
                ],
            ),
        ]
        add_missing_critical_gates(guide)
        compute_difficulty(guide)
        diff = guide.difficulty.get("own_app_production")
        assert "substantial" in diff.label.lower()


# ── Excerpts ──────────────────────────────────────────────────────────────

class TestExcerpts:
    """Found vs not found."""

    def test_excerpt_found(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Test", source_ids=["S1"],
                       basis="documented",
                       excerpt="This is the exact text from the page"),
        ]
        guide.sources = [Source(id="S1", url="https://example.com", retrieval_status="read")]
        page_texts = {"S1": "Some intro. This is the exact text from the page. Some outro."}
        warnings = check_excerpts(guide, page_texts)
        # Should be found (no warning about not found)
        assert not any("not found" in w.lower() for w in warnings)

    def test_excerpt_not_found(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Test", source_ids=["S1"],
                       basis="documented",
                       excerpt="Completely different text that is nowhere in the page"),
        ]
        guide.sources = [Source(id="S1", url="https://example.com", retrieval_status="read")]
        page_texts = {"S1": "This page is about something totally unrelated to the claim."}
        warnings = check_excerpts(guide, page_texts)
        assert any("not found" in w.lower() for w in warnings)


# ── Store ─────────────────────────────────────────────────────────────────

class TestStore:
    """Immutable versions; reviewed import; startup interruption."""

    def setup_method(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.store = Store(self.tmpfile.name)

    def teardown_method(self):
        os.unlink(self.tmpfile.name)

    def test_versions_immutable(self):
        guide = _make_guide()
        guide.id = "test-guide"
        v1 = self.store.save_guide(guide)
        assert v1 == 1

        guide2 = _make_guide()
        guide2.id = "test-guide"
        v2 = self.store.save_guide(guide2)
        assert v2 == 2

        r1 = self.store.get_guide("test-guide", 1)
        r2 = self.store.get_guide("test-guide", 2)
        assert r1 is not None
        assert r2 is not None
        assert r1.version == 1
        assert r2.version == 2

    def test_reviewed_import_idempotent(self):
        guide = _make_guide()
        guide.id = "test-reviewed"
        guide.version = 1
        guide.created_at = "2026-01-01T00:00:00Z"
        guide.status = "reviewed"
        guide.sources = [Source(id="S1", url="https://example.com/docs", retrieval_status="read")]
        guide.guide.claims = [DraftClaim(id="K1", text="OAuth supported", source_ids=["S1"], basis="documented", review_result="supported", support_note="Verified by reviewer")]
        guide.review = ReviewInfo(
            reviewer="Alice",
            reviewed_at="2026-01-01T00:00:00Z",
            audited_claim_ids=["K1"],
        )

        ok1, msg1 = self.store.import_reviewed_seed(guide)
        assert ok1

        ok2, msg2 = self.store.import_reviewed_seed(guide)
        assert ok2
        assert "identical" in msg2.lower() or "already" in msg2.lower()

    def test_startup_marks_interrupted(self):
        self.store.create_job("j1", "GitHub")
        self.store.update_job("j1", status="running")
        self.store.create_job("j2", "Slack")
        # j2 is queued

        self.store.mark_running_jobs_interrupted()

        j1 = self.store.get_job("j1")
        j2 = self.store.get_job("j2")
        assert j1["status"] == "interrupted"
        assert j2["status"] == "interrupted"

    def test_cache_hits_respect_freshness(self):
        guide = _make_guide()
        guide.id = "cache-test"
        guide.cache_key = "test|key"
        self.store.save_guide(guide)

        hit = self.store.check_cache("test|key", 168)
        assert hit is not None

        miss = self.store.check_cache("wrong|key", 168)
        assert miss is None

    def test_promotion_preserves_original(self):
        guide = _make_guide()
        guide.id = "promote-test"
        v1 = self.store.save_guide(guide)

        # Simulate promotion: save a new reviewed version
        reviewed = _make_guide()
        reviewed.id = "promote-test"
        reviewed.status = "reviewed"
        reviewed.review = ReviewInfo(
            reviewer="Test User",
            reviewed_at="2026-01-15T00:00:00Z",
            audited_claim_ids=["K1"],
            based_on_version=v1,
        )
        v2 = self.store.save_guide(reviewed)
        assert v2 == 2

        # Original still there
        r1 = self.store.get_guide("promote-test", v1)
        assert r1 is not None
        assert r1.status == "draft"


# ── Export and routes ─────────────────────────────────────────────────────

class TestExportAndRoutes:
    """Markdown contains required sections; no javascript: in output."""

    def test_markdown_contains_required_sections(self):
        guide = _make_guide_with_content()
        md = render_markdown(guide)
        assert "Assumptions:" in md or "Audience:" in md
        assert "Next action" in md
        assert "Unknowns" in md
        assert "Conflicts" in md
        assert "Evidence details" in md
        assert "Validation warnings" in md or "Run statistics" in md

    def test_markdown_contains_waiting_unknown(self):
        guide = _make_guide_with_content()
        guide.guide.paths[0].waiting = DraftEstimate(known=False)
        md = render_markdown(guide)
        assert "unknown" in md.lower()

    def test_no_javascript_in_markdown(self):
        guide = _make_guide_with_content()
        # Inject a javascript URL in a step
        guide.guide.paths[0].steps[0].action_url = "javascript:alert(1)"
        # Validate first (should remove it)
        validate_guide(guide)
        md = render_markdown(guide)
        assert "javascript:" not in md

    def test_format_estimate_unknown(self):
        de = DraftEstimate(known=False)
        assert format_estimate(de) == "unknown"

    def test_format_estimate_range(self):
        de = DraftEstimate(known=True, low=5, high=10, unit="minutes", basis="author_estimate")
        result = format_estimate(de)
        assert "5" in result
        assert "10" in result
        assert "minutes" in result


# ── Domain parsing ────────────────────────────────────────────────────────

class TestDomainParsing:
    def test_parse_simple_domain(self):
        reg, full = parse_domain("github.com")
        assert reg == "github.com"

    def test_parse_subdomain(self):
        reg, full = parse_domain("app.hubspot.com")
        assert reg == "hubspot.com"
        assert "app" in full

    def test_parse_url(self):
        reg, full = parse_domain("https://docs.github.com/en/apps")
        assert reg == "github.com"

    def test_resolve_known_provider(self):
        p, candidates = resolve_provider("GitHub")
        assert p.name == "GitHub"
        assert p.official_domain == "github.com"
        assert p.identity_status == "resolved"
        assert candidates is None

    def test_resolve_unknown_provider(self):
        p, candidates = resolve_provider("SomeObscureApp")
        assert p.identity_status == "assumed"
        assert candidates is None

    def test_resolve_ambiguous_returns_candidates(self):
        p, candidates = resolve_provider("Box")
        assert candidates is not None
        assert len(candidates) >= 2

    def test_resolve_ambiguous_with_website_resolves(self):
        p, candidates = resolve_provider("Box", website="box.com")
        assert candidates is None


# ── Reference validation ──────────────────────────────────────────────────

class TestReferenceValidation:
    """Invalid claim refs pruned from dependent content."""

    def test_invalid_claim_refs_pruned_from_summary(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Valid claim", source_ids=[], basis="estimated"),
        ]
        guide.guide.summary = [
            DraftSummaryStatement(text="Statement", claim_ids=["K1", "K999"]),
        ]
        warnings = validate_references(guide)
        assert "K999" not in guide.guide.summary[0].claim_ids
        assert "K1" in guide.guide.summary[0].claim_ids
        assert any("invalid claim refs" in w.lower() for w in warnings)

    def test_all_unknown_claims_warned(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Unknown claim", basis="unknown"),
        ]
        guide.guide.summary = [
            DraftSummaryStatement(text="Depends on K1", claim_ids=["K1"]),
        ]
        warnings = validate_references(guide)
        assert any("weakly supported" in w.lower() for w in warnings)


# ── Uncited sources ───────────────────────────────────────────────────────

class TestUncitedSources:
    """Sources not cited by any claim move to uncited_sources."""

    def test_uncited_source_moved(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Test", source_ids=["S1"], basis="documented"),
        ]
        guide.sources = [
            Source(id="S1", url="https://example.com/cited", retrieval_status="read"),
            Source(id="S2", url="https://example.com/uncited", retrieval_status="read"),
        ]
        warnings = split_uncited_sources(guide)
        assert len(guide.sources) == 1
        assert guide.sources[0].id == "S1"
        assert len(guide.uncited_sources) == 1
        assert guide.uncited_sources[0].id == "S2"

    def test_all_cited_stays(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Test", source_ids=["S1", "S2"], basis="documented"),
        ]
        guide.sources = [
            Source(id="S1", url="https://a.com", retrieval_status="read"),
            Source(id="S2", url="https://b.com", retrieval_status="read"),
        ]
        warnings = split_uncited_sources(guide)
        assert len(guide.sources) == 2
        assert len(guide.uncited_sources) == 0


# ── Review integrity ─────────────────────────────────────────────────────

class TestReviewIntegrity:
    """Claim listed as audited but not_reviewed produces warning."""

    def test_audited_but_not_reviewed_warned(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Test", basis="documented",
                       review_result="not_reviewed"),
        ]
        guide.review = ReviewInfo(audited_claim_ids=["K1"])
        warnings = check_review_integrity(guide)
        assert any("audited" in w.lower() and "not_reviewed" in w.lower() for w in warnings)

    def test_audited_and_reviewed_no_warning(self):
        guide = _make_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Test", basis="documented",
                       review_result="supported"),
        ]
        guide.review = ReviewInfo(audited_claim_ids=["K1"])
        warnings = check_review_integrity(guide)
        assert not warnings


# ── Web routes ────────────────────────────────────────────────────────────

class TestWebRoutes:
    """Verify HTTP route behavior per §6.6."""

    @pytest.fixture(autouse=True)
    def setup_isolated_env(self, tmp_path, monkeypatch):
        import app.main as main_mod
        from app.store import Store
        test_db = str(tmp_path / "test_oauthpath.db")
        test_store = Store(test_db)
        monkeypatch.setattr(main_mod, "store", test_store)
        monkeypatch.setattr(main_mod, "live_generation_configured", lambda: False)
        yield

    def test_health_check(self):
        from starlette.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "live_generation" in data
            assert "composio_lookup" in data

    def test_index_page(self):
        from starlette.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "Know what it takes to launch your OAuth integration" in resp.text

    def test_create_job_without_key_gives_503(self):
        from starlette.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            resp = client.post("/api/jobs", json={"app_name": "GitHub"})
            # live generation is not configured by default in tests
            assert resp.status_code == 503

    def test_get_nonexistent_job_gives_404(self):
        from starlette.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            resp = client.get("/api/jobs/nonexistent-id")
            assert resp.status_code == 404

    def test_get_nonexistent_guide_gives_404(self):
        from starlette.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            resp = client.get("/guides/nonexistent-id")
            assert resp.status_code == 404


# ── Punch list verification ───────────────────────────────────────────────

class TestPunchListVerification:
    """Verify bug fixes and evidence rules from P0-P4 punch list."""

    def test_gate_evidence_enforced(self):
        guide = _make_guide()
        guide.guide.oauth_support = "confirmed"
        guide.guide.paths = [
            DraftPath(
                id="own_app_production",
                name="Production",
                gates=[
                    DraftGate(kind="app_review", state="not_required", claim_ids=[]),
                ],
            )
        ]
        validate_guide(guide)
        g = guide.guide.paths[0].gates[0]
        assert g.state == "unknown"
        assert any("lacks documented/observed supporting claim" in w for w in guide.warnings)

    def test_gate_evidence_kept_when_backed(self):
        guide = _make_guide()
        guide.guide.oauth_support = "confirmed"
        guide.guide.claims = [
            DraftClaim(id="K1", text="No review required", basis="documented", source_ids=["S1"]),
        ]
        guide.sources = [
            Source(id="S1", url="https://example.com/docs", retrieval_status="read"),
        ]
        guide.guide.paths = [
            DraftPath(
                id="own_app_production",
                name="Production",
                gates=[
                    DraftGate(kind="app_review", state="not_required", claim_ids=["K1"]),
                ],
            )
        ]
        validate_guide(guide)
        g = guide.guide.paths[0].gates[0]
        assert g.state == "not_required"

    def test_composio_kc_claims_added_and_not_pruned(self):
        guide = _make_guide()
        guide.guide.oauth_support = "confirmed"
        guide.composio = ComposioInfo(
            toolkit_slug="slack",
            lookup_status="matched",
            selected_auth_method="OAUTH2",
            managed_oauth="available",
            source_id="C1",
        )
        guide.sources = [
            Source(id="C1", url="https://backend.composio.dev/api/v3/toolkits/slack", retrieval_status="read"),
        ]
        validate_guide(guide)
        claim_ids = {c.id for c in guide.guide.claims}
        assert "KC1" in claim_ids
        assert "KC2" in claim_ids
        assert "KC3" in claim_ids
        source_ids = {s.id for s in guide.sources}
        assert "C2" in source_ids
        assert "C3" in source_ids

    def test_zero_zero_estimate_formats_as_unknown(self):
        de = DraftEstimate(known=True, low=0, high=0, unit="business_days", basis="documented")
        assert format_estimate(de) == "unknown"

    def test_create_job_request_validation(self):
        from app.main import CreateJobRequest
        import pydantic
        import pytest
        # Valid
        req = CreateJobRequest(app_name="Slack", feature="send messages", audience="external customers")
        assert req.app_name == "Slack"
        # Invalid: short app_name
        with pytest.raises(pydantic.ValidationError):
            CreateJobRequest(app_name="a")
        # Invalid: empty app_name and empty website
        with pytest.raises(pydantic.ValidationError):
            CreateJobRequest(app_name="", website="")
        # Invalid: bad audience
        with pytest.raises(pydantic.ValidationError):
            CreateJobRequest(app_name="Slack", audience="super admins")

    def test_measured_estimate_kept_when_backed_by_attempt(self):
        guide = _make_guide()
        guide.guide.oauth_support = "confirmed"
        guide.attempts = [
            Attempt(id="A1", provider="github", status="succeeded", observed_result="connected"),
        ]
        guide.guide.claims = [
            DraftClaim(id="K1", text="Connection took 5m", basis="observed", attempt_ids=["A1"]),
        ]
        guide.guide.paths = [
            DraftPath(
                id="own_app_testing",
                name="Testing",
                effort=DraftEstimate(known=True, low=5, high=10, unit="minutes", basis="measured", claim_ids=["K1"]),
            )
        ]
        validate_guide(guide)
        assert guide.guide.paths[0].effort.basis == "measured"

    def test_measured_estimate_downgraded_without_attempt(self):
        guide = _make_guide()
        guide.guide.oauth_support = "confirmed"
        guide.guide.claims = [
            DraftClaim(id="K1", text="Claim without attempt", basis="documented", source_ids=["S1"]),
        ]
        guide.sources = [
            Source(id="S1", url="https://example.com/docs", retrieval_status="read"),
        ]
        guide.guide.paths = [
            DraftPath(
                id="own_app_testing",
                name="Testing",
                effort=DraftEstimate(known=True, low=5, high=10, unit="minutes", basis="measured", claim_ids=["K1"]),
            )
        ]
        validate_guide(guide)
        assert guide.guide.paths[0].effort.basis == "author_estimate"
        assert any("measured" in w and "downgraded" in w for w in guide.warnings)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_guide() -> GuideRecord:
    """Create a minimal valid guide for testing."""
    return GuideRecord(
        schema_version="1",
        prompt_version="1",
        id="test",
        provider=ProviderInfo(name="TestProvider"),
        scenario=Scenario(),
        composio=ComposioInfo(),
        guide=DraftGuideContent(),
        run=RunStats(),
    )


def _make_guide_with_content() -> GuideRecord:
    """Create a guide with substantive content for rendering tests."""
    return GuideRecord(
        schema_version="1",
        prompt_version="1",
        id="test-full",
        version=1,
        created_at="2026-01-01T00:00:00Z",
        status="draft",
        provider=ProviderInfo(name="GitHub", official_domain="github.com"),
        scenario=Scenario(
            feature="repo access",
            audience="external customers",
            deployment="server-side",
        ),
        composio=ComposioInfo(
            toolkit_slug="github",
            lookup_status="matched",
            managed_oauth="available",
        ),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            summary=[
                DraftSummaryStatement(text="GitHub supports OAuth", claim_ids=["K1"]),
            ],
            next_action=DraftNextAction(
                instruction="Create a GitHub OAuth App",
                owner="you",
                success_condition="Client ID visible in settings",
            ),
            paths=[
                DraftPath(
                    id="own_app_testing",
                    name="Your app: Testing",
                    steps=[
                        DraftStep(
                            id="register",
                            title="Register OAuth App",
                            instructions="Go to GitHub Settings > Developer settings",
                            owner="you",
                            success_condition="App registered",
                            action_url="https://github.com/settings/developers",
                        ),
                    ],
                    effort=DraftEstimate(known=True, low=10, high=15, unit="minutes",
                                          basis="author_estimate"),
                    waiting=DraftEstimate(known=False),
                ),
            ],
            scopes=[
                DraftScope(scope_string="repo", purpose="Access repositories",
                           classification="standard"),
            ],
            unknowns=[
                DraftUnknown(question="Org access policy"),
            ],
            claims=[
                DraftClaim(id="K1", text="GitHub supports OAuth Apps",
                           source_ids=["S1"], basis="documented"),
            ],
        ),
        sources=[
            Source(id="S1", url="https://docs.github.com/en/apps/oauth-apps",
                   title="GitHub OAuth Apps", retrieval_status="read"),
        ],
        run=RunStats(model_calls=2, searches=3, page_reads=2, duration_seconds=45.2),
        warnings=["Test warning"],
    )
