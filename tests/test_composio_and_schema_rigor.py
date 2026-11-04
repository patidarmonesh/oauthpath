"""Tests for Composio integration, toolkit matching, SDK test script, two-layer schema rigor, and promote workflow."""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import pytest

from app.config import COMPOSIO_API_HOST, COMPOSIO_CALLBACK_URL
from app.resolver import (
    lookup_composio_toolkit,
    _search_composio,
    _get_toolkit_details,
    resolve_provider,
    TOOLKIT_ALIASES,
    AMBIGUOUS_NAMES,
)
from app.schema import (
    ComposioInfo,
    ComposioLookupStatus,
    DraftClaim,
    DraftCost,
    DraftEstimate,
    DraftGate,
    DraftGuideContent,
    DraftNextAction,
    DraftPath,
    DraftPrerequisite,
    DraftScope,
    DraftStep,
    DraftSummaryStatement,
    Estimate,
    GuideRecord,
    GuideStatus,
    ManagedOAuth,
    ProviderInfo,
    ReviewInfo,
    Scenario,
    Source,
    convert_draft_estimate,
)
from app.store import Store
from app.synthesis import (
    GUIDE_SCHEMA,
    GATE_KIND_ENUM,
    GATE_STATE_ENUM,
    OWNER_ENUM,
    UNIT_ENUM,
    ESTIMATE_BASIS_ENUM,
    CLAIM_BASIS_ENUM,
)
from app.validation import validate_guide


_orig_async_client = httpx.AsyncClient

def _mock_client(transport):
    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _orig_async_client(*args, **kwargs)
    return factory


# ============================================================================
# 1. Composio REST API integration in app/resolver.py
# ============================================================================

class TestComposioRESTIntegration:
    """Audit Composio REST API endpoints, headers, and error handling."""

    @pytest.mark.anyio
    async def test_composio_lookup_not_attempted_without_key(self):
        """When COMPOSIO_API_KEY is unset, lookup returns not_attempted."""
        with patch("app.resolver.COMPOSIO_API_KEY", ""):
            info, sources = await lookup_composio_toolkit("github")
            assert info.lookup_status == ComposioLookupStatus.not_attempted.value
            assert info.managed_oauth == ManagedOAuth.unknown.value
            assert len(sources) == 0

    @pytest.mark.anyio
    async def test_composio_endpoints_and_headers(self):
        """Verify exact REST endpoints and x-api-key header used."""
        captured_requests = []

        def handler(request: httpx.Request):
            captured_requests.append(request)
            if "/api/v3/toolkits/github" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "composio_managed_auth_schemes": ["OAUTH2"],
                        "auth_config_details": [
                            {
                                "mode": "OAUTH2",
                                "fields": {
                                    "auth_config_creation": {"required": ["client_id", "client_secret"]},
                                    "connected_account_initiation": {"required": []},
                                },
                            }
                        ],
                        "auth_guide_url": "https://docs.composio.dev/toolkits/github",
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        with patch("app.resolver.COMPOSIO_API_KEY", "test-key-123"):
            with patch("httpx.AsyncClient", _mock_client(transport)):
                info, sources = await lookup_composio_toolkit("github")

                assert len(captured_requests) == 1
                req = captured_requests[0]
                assert req.headers.get("x-api-key") == "test-key-123"
                assert str(req.url) == f"{COMPOSIO_API_HOST}/api/v3/toolkits/github"
                assert info.lookup_status == ComposioLookupStatus.matched.value
                assert info.managed_oauth == ManagedOAuth.available.value
                assert info.selected_auth_method == "OAUTH2"
                assert info.required_config_fields == ["client_id", "client_secret"]
                assert len(sources) == 1
                assert sources[0].id == "C1"

    @pytest.mark.anyio
    async def test_composio_search_and_corroboration_endpoints(self):
        """Verify search endpoint called when app_name is not in aliases."""
        captured_requests = []

        def handler(request: httpx.Request):
            captured_requests.append(request)
            if "/api/v3/toolkits?" in str(request.url) or str(request.url).endswith("/api/v3/toolkits"):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "name": "Acme Tools",
                            "slug": "acme_tools",
                            "meta": {"app_url": "https://acme.com"},
                        }
                    ],
                )
            if "/api/v3/toolkits/acme_tools" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "composio_managed_auth_schemes": [],
                        "auth_config_details": [{"mode": "OAUTH2"}],
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        with patch("app.resolver.COMPOSIO_API_KEY", "test-key"):
            with patch("httpx.AsyncClient", _mock_client(transport)):
                info, sources = await lookup_composio_toolkit("Acme Tools", official_domain="acme.com")
                assert len(captured_requests) == 2
                assert captured_requests[0].headers.get("x-api-key") == "test-key"
                assert "search=Acme+Tools" in str(captured_requests[0].url) or "search=Acme" in str(captured_requests[0].url)
                assert info.toolkit_slug == "acme_tools"
                assert info.lookup_status == ComposioLookupStatus.matched.value
                assert info.managed_oauth == ManagedOAuth.unavailable.value

    @pytest.mark.anyio
    async def test_composio_404_details_marks_no_match(self):
        """A 404 from the details endpoint must mark lookup_status as no_match."""
        def handler(request: httpx.Request):
            return httpx.Response(404, text="Not Found")

        transport = httpx.MockTransport(handler)
        with patch("app.resolver.COMPOSIO_API_KEY", "test-key"):
            with patch("httpx.AsyncClient", _mock_client(transport)):
                info, sources = await lookup_composio_toolkit("github")
                assert info.lookup_status == ComposioLookupStatus.no_match.value
                assert info.managed_oauth == ManagedOAuth.unknown.value

    @pytest.mark.anyio
    async def test_composio_500_details_marks_failed(self):
        """A 500 server error from details endpoint must mark lookup_status as failed."""
        def handler(request: httpx.Request):
            return httpx.Response(500, text="Internal Server Error")

        transport = httpx.MockTransport(handler)
        with patch("app.resolver.COMPOSIO_API_KEY", "test-key"):
            with patch("httpx.AsyncClient", _mock_client(transport)):
                info, sources = await lookup_composio_toolkit("github")
                assert info.lookup_status == ComposioLookupStatus.failed.value
                assert info.managed_oauth == ManagedOAuth.unknown.value

    @pytest.mark.anyio
    async def test_composio_timeout_marks_failed(self):
        """A timeout during Composio API request must mark lookup_status as failed."""
        def handler(request: httpx.Request):
            raise httpx.ReadTimeout("Timeout connecting to backend.composio.dev")

        transport = httpx.MockTransport(handler)
        with patch("app.resolver.COMPOSIO_API_KEY", "test-key"):
            with patch("httpx.AsyncClient", _mock_client(transport)):
                info, sources = await lookup_composio_toolkit("github")
                assert info.lookup_status == ComposioLookupStatus.failed.value
                assert info.managed_oauth == ManagedOAuth.unknown.value

    @pytest.mark.anyio
    async def test_composio_search_404_marks_no_match(self):
        """If search API returns 404, it is treated as no match."""
        def handler(request: httpx.Request):
            return httpx.Response(404, text="Not Found")

        transport = httpx.MockTransport(handler)
        with patch("app.resolver.COMPOSIO_API_KEY", "test-key"):
            with patch("httpx.AsyncClient", _mock_client(transport)):
                info, sources = await lookup_composio_toolkit("unaliased_app")
                assert info.lookup_status == ComposioLookupStatus.no_match.value
                assert info.managed_oauth == ManagedOAuth.unknown.value

    @pytest.mark.anyio
    async def test_composio_missing_auth_metadata_marks_matched_unknown(self):
        """Matched toolkit with absent/malformed auth metadata must be managed_oauth=unknown per §8.3."""
        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                json={
                    "name": "Missing Meta App",
                    # composio_managed_auth_schemes and auth_config_details are absent
                },
            )

        transport = httpx.MockTransport(handler)
        with patch("app.resolver.COMPOSIO_API_KEY", "test-key"):
            with patch("httpx.AsyncClient", _mock_client(transport)):
                info, sources = await lookup_composio_toolkit("github")
                assert info.lookup_status == ComposioLookupStatus.matched.value
                assert info.managed_oauth == ManagedOAuth.unknown.value
                assert info.selected_auth_method is None


# ============================================================================
# 2. Composio Toolkit slug matching and alias resolution
# ============================================================================

class TestToolkitMatchingAndAliases:
    """Audit TOOLKIT_ALIASES, ambiguous name detection, and corroboration rules."""

    def test_toolkit_aliases_complete_and_lowercase(self):
        """All aliases are lowercase and map to non-empty slug strings."""
        assert len(TOOLKIT_ALIASES) >= 20
        for alias, slug in TOOLKIT_ALIASES.items():
            assert alias == alias.lower()
            assert slug == slug.lower()
            assert len(slug) > 0

        # Common core aliases
        assert TOOLKIT_ALIASES["github"] == "github"
        assert TOOLKIT_ALIASES["slack"] == "slack"
        assert TOOLKIT_ALIASES["gmail"] == "gmail"
        assert TOOLKIT_ALIASES["google calendar"] == "googlecalendar"
        assert TOOLKIT_ALIASES["gcal"] == "googlecalendar"
        assert TOOLKIT_ALIASES["gdrive"] == "googledrive"
        assert TOOLKIT_ALIASES["xero"] == "xero"

    def test_ambiguous_name_detection_box_teams_monday(self):
        """Ambiguous names (box, teams, monday) return multiple candidates for disambiguation."""
        for name in ["box", "teams", "monday"]:
            provider, candidates = resolve_provider(name)
            assert candidates is not None, f"Expected candidates for ambiguous name: {name}"
            assert len(candidates) >= 2, f"Expected at least 2 candidates for {name}"
            for c in candidates:
                assert "name" in c and "domain" in c

    def test_ambiguous_name_resolved_with_website(self):
        """Providing a website disambiguates an ambiguous name without returning candidates."""
        for name, domain in [("box", "box.com"), ("teams", "microsoft.com"), ("monday", "monday.com")]:
            provider, candidates = resolve_provider(name, website=domain)
            assert candidates is None
            assert provider.official_domain == domain

    @pytest.mark.anyio
    async def test_corroboration_exact_match(self):
        """Exact match takes priority over contained or domain match."""
        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                json=[
                    {"name": "Slack Enterprise Grid", "slug": "slack_enterprise"},
                    {"name": "Slack", "slug": "slack"},
                ],
            )

        transport = httpx.MockTransport(handler)
        with patch("httpx.AsyncClient", _mock_client(transport)):
            slug = await _search_composio("slack", official_domain="slack.com")
            assert slug == "slack"

    @pytest.mark.anyio
    async def test_corroboration_contained_match(self):
        """Contained match matches when query is a substring of name or slug."""
        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                json=[
                    {"name": "Salesforce Marketing Cloud", "slug": "sf_marketing"},
                ],
            )

        transport = httpx.MockTransport(handler)
        with patch("httpx.AsyncClient", _mock_client(transport)):
            slug = await _search_composio("Salesforce", official_domain="")
            assert slug == "sf_marketing"

    @pytest.mark.anyio
    async def test_corroboration_domain_match_via_meta_app_url(self):
        """When names do not match, domain match via meta.app_url corroborates."""
        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "Different Product Name",
                        "slug": "unique_slug",
                        "meta": {"app_url": "https://sub.linear.app/auth"},
                    },
                ],
            )

        transport = httpx.MockTransport(handler)
        with patch("httpx.AsyncClient", _mock_client(transport)):
            slug = await _search_composio("unmatched_query", official_domain="linear.app")
            assert slug == "unique_slug"


# ============================================================================
# 3. Composio SDK test script (scripts/composio_test.py)
# ============================================================================

class TestComposioSDKScript:
    """Verify scripts/composio_test.py follows SDK signatures, dynamic IDs, and single attempt file."""

    def test_composio_test_script_argparse_and_signatures(self):
        """Verify composio_test.py accepts required arguments and has default redirect URI."""
        script_path = Path("scripts/composio_test.py")
        assert script_path.exists()
        code = script_path.read_text(encoding="utf-8")

        # Required SDK calls
        assert "client.auth_configs.create(" in code
        assert "client.connected_accounts.link(" in code
        assert "client.connected_accounts.wait_for_connection(" in code

        # Dynamic user ID
        assert "user_id = args.user_id or f\"oauthpath-test-" in code

        # Single attempt record in finally block
        assert "finally:" in code
        assert "_write_attempt(" in code

        # Default redirect URI is v3 callback
        assert COMPOSIO_CALLBACK_URL in code or "/api/v3/toolkits/auth/callback" in code

    def test_composio_test_script_simulated_execution(self, tmp_path):
        """Verify single attempt file creation in finally block with no secrets logged."""
        from scripts import composio_test

        attempt_id = "A-test-20261103"
        toolkit = "github"
        user_id = "test-user-dynamic-42"
        start_time = datetime.now(timezone.utc)
        observed = "Connected account active: ID 123"
        status = "succeeded"
        limitation = "Proves one test-user connection"

        with patch("scripts.composio_test.Path") as mock_path_cls:
            mock_attempts_dir = MagicMock()
            mock_out_file = MagicMock()
            mock_path_cls.return_value = mock_attempts_dir
            mock_attempts_dir.__truediv__.return_value = mock_out_file

            composio_test._write_attempt(
                attempt_id, toolkit, user_id, start_time, observed, status, limitation
            )

            # Assert write_text was called exactly once
            assert mock_out_file.write_text.call_count == 1
            written_json = mock_out_file.write_text.call_args[0][0]
            record = json.loads(written_json)

            assert record["id"] == attempt_id
            assert record["actor"] == "assisted"
            assert record["provider"] == toolkit
            assert record["status"] == "succeeded"
            assert record["limitation"] == limitation
            assert "client_secret" not in written_json
            assert "access_token" not in written_json


# ============================================================================
# 4. Two-layer schema compliance in app/schema.py and app/synthesis.py
# ============================================================================

class TestTwoLayerSchemaCompliance:
    """Ensure Draft models (model-writable) and final models (code-only) remain strictly separate."""

    def test_guide_schema_has_all_required_properties(self):
        """All properties in GUIDE_SCHEMA are marked as required."""
        top_required = GUIDE_SCHEMA.get("required", [])
        top_props = list(GUIDE_SCHEMA.get("properties", {}).keys())
        assert set(top_required) == set(top_props), "All top-level properties must be required"

        # Check required fields in nested objects
        path_schema = GUIDE_SCHEMA["properties"]["paths"]["items"]
        assert set(path_schema["required"]) == set(path_schema["properties"].keys())

        step_schema = path_schema["properties"]["steps"]["items"]
        assert set(step_schema["required"]) == set(step_schema["properties"].keys())

        claim_schema = GUIDE_SCHEMA["properties"]["claims"]["items"]
        assert set(claim_schema["required"]) == set(claim_schema["properties"].keys())

    def test_enums_are_strictly_constrained(self):
        """Permitted enum values in synthesis are strictly constrained."""
        assert "you" in OWNER_ENUM
        assert "agent_prepares_you_submit" in OWNER_ENUM
        assert "not_applicable" in UNIT_ENUM
        assert "minutes" in UNIT_ENUM
        assert "author_estimate" in ESTIMATE_BASIS_ENUM
        assert "documented" in CLAIM_BASIS_ENUM
        assert "app_review" in GATE_KIND_ENUM
        assert "required" in GATE_STATE_ENUM

    def test_human_review_and_verification_fields_excluded_from_synthesis(self):
        """Model cannot write excerpt_status, review_result, or support_note in GUIDE_SCHEMA."""
        claim_props = GUIDE_SCHEMA["properties"]["claims"]["items"]["properties"]
        assert "excerpt_status" not in claim_props
        assert "review_result" not in claim_props
        assert "support_note" not in claim_props

    def test_convert_draft_estimate_prevents_unsafe_defaults(self):
        """DraftEstimate with known=False becomes null bounds; negative/inverted bounds removed."""
        # known=False
        de_unknown = DraftEstimate(known=False, low=10, high=20)
        est, w = convert_draft_estimate(de_unknown)
        assert est.low is None
        assert est.high is None

        # inverted bounds
        de_inv = DraftEstimate(known=True, low=100, high=50)
        est_inv, w_inv = convert_draft_estimate(de_inv)
        assert est_inv.low is None
        assert est_inv.high is None
        assert any("Inverted bounds" in warn for warn in w_inv)

        # negative bounds
        de_neg = DraftEstimate(known=True, low=-5, high=10)
        est_neg, w_neg = convert_draft_estimate(de_neg)
        assert est_neg.low is None
        assert est_neg.high == 10
        assert any("Negative low bound removed" in warn for warn in w_neg)


# ============================================================================
# 5. Promotion workflow in scripts/promote.py
# ============================================================================

class TestPromotionWorkflow:
    """Ensure --from-file workflow re-validates guides, checks reviewer and audited claims, and exports clean immutable versions."""

    def test_from_file_workflow_success(self, tmp_path):
        """Full --from-file promotion flow: validate, verify claims/reviewer, increment version, export seed."""
        # Create a temporary sqlite db
        db_path = str(tmp_path / "test_promote.db")
        store = Store(db_path)

        # Create a draft guide v1
        draft_claim = DraftClaim(
            id="K1",
            text="Provider uses OAuth 2.0 authorization code grant",
            basis="documented",
            source_ids=["S1"],
            review_result="supported",
            support_note="Verified against official RFC documentation by Alice",
        )
        guide_record = GuideRecord(
            schema_version="2",
            prompt_version="1",
            id="test-promote-guide",
            version=1,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="draft",
            provider=ProviderInfo(name="PromoteTest", official_domain="promotetest.com", identity_status="resolved"),
            scenario=Scenario(feature="Testing promotion", audience="external customers"),
            composio=ComposioInfo(lookup_status="no_match"),
            guide=DraftGuideContent(
                oauth_support="confirmed",
                summary=[DraftSummaryStatement(text="Summary statement", claim_ids=["K1"])],
                next_action=DraftNextAction(instruction="Complete setup", claim_ids=["K1"]),
                paths=[
                    DraftPath(
                        id="own_app_testing",
                        name="Testing Path",
                        steps=[DraftStep(id="s1", title="Step 1", instructions="Do it", owner="you")],
                    )
                ],
                claims=[draft_claim],
            ),
            sources=[
                Source(
                    id="S1",
                    url="https://promotetest.com/docs",
                    retrieval_status="read",
                    title="PromoteTest Docs",
                )
            ],
            review=ReviewInfo(
                reviewer="Alice Reviewer",
                audited_claim_ids=["K1"],
            ),
        )

        v1 = store.save_guide(guide_record)
        assert v1 == 1

        # Write working file
        working_file = tmp_path / "review-copy.json"
        working_file.write_text(guide_record.model_dump_json(indent=2), encoding="utf-8")

        # Run scripts/promote.py --from-file
        cmd = [
            sys.executable,
            "scripts/promote.py",
            "--from-file",
            str(working_file),
            "--reviewer",
            "Alice Reviewer",
            "--claims",
            "K1",
        ]

        test_reviewed_dir = tmp_path / "reviewed"
        env = os.environ.copy()
        env["DATABASE_PATH"] = db_path
        env["REVIEWED_DIR"] = str(test_reviewed_dir)
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"Promotion failed: {res.stderr}\n{res.stdout}"
        assert "Promoted from file to reviewed version: test-promote-guide v2" in res.stdout

        # Verify in store
        promoted = store.get_guide("test-promote-guide", 2)
        assert promoted is not None
        assert promoted.status == GuideStatus.reviewed.value
        assert promoted.version == 2
        assert promoted.review.reviewer == "Alice Reviewer"
        assert promoted.review.audited_claim_ids == ["K1"]
        assert promoted.review.based_on_version == 1

        # Verify seed file was written in isolated test directory
        seed_file = test_reviewed_dir / "test-promote-guide-v2.json"
        assert seed_file.exists()

    def test_from_file_rejects_missing_reviewer(self, tmp_path):
        """--from-file fails if no reviewer is specified or present."""
        working_file = tmp_path / "review-copy-no-rev.json"
        guide_record = GuideRecord(
            schema_version="2",
            prompt_version="1",
            id="test-no-rev",
            guide=DraftGuideContent(oauth_support="confirmed"),
            review=ReviewInfo(reviewer="", audited_claim_ids=["K1"]),
        )
        working_file.write_text(guide_record.model_dump_json(indent=2), encoding="utf-8")

        cmd = [sys.executable, "scripts/promote.py", "--from-file", str(working_file)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode != 0
        assert "ERROR: --reviewer must be specified" in res.stdout

    def test_from_file_rejects_audited_claim_without_support_note(self, tmp_path):
        """--from-file fails if an audited claim has empty support_note or not_reviewed status."""
        working_file = tmp_path / "review-copy-bad-claim.json"
        claim = DraftClaim(id="K1", text="Text", basis="documented", review_result="supported", support_note="")
        guide_record = GuideRecord(
            schema_version="2",
            prompt_version="1",
            id="test-bad-claim",
            guide=DraftGuideContent(oauth_support="confirmed", claims=[claim]),
            review=ReviewInfo(reviewer="Bob", audited_claim_ids=["K1"]),
        )
        working_file.write_text(guide_record.model_dump_json(indent=2), encoding="utf-8")

        cmd = [
            sys.executable,
            "scripts/promote.py",
            "--from-file",
            str(working_file),
            "--reviewer",
            "Bob",
            "--claims",
            "K1",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode != 0
        assert "empty support_note" in res.stdout


# ── 6. Regression & Hardening Tests for Punch List Items ──────────────────

class TestPunchListRegressions:
    """Verifies targeted fixes for resolver, zero-estimates, document blocks, and promote."""

    def test_resolver_exact_name_matching_preserves_google_calendar(self):
        """Google Calendar with google.com preserves user's product name."""
        from app.resolver import resolve_provider
        info, candidates = resolve_provider("Google Calendar", "google.com")
        assert info.name == "Google Calendar"
        assert info.official_domain == "google.com"
        assert info.identity_status == "assumed"

        # Exact match on known key still resolves
        info_exact, _ = resolve_provider("Google", "google.com")
        assert info_exact.name == "Google"
        assert info_exact.identity_status == "resolved"

    @pytest.mark.anyio
    async def test_composio_contained_match_ambiguity_falls_back_to_domain(self):
        """If multiple toolkits match via 'contained', do not pick the first one; fall back to domain match."""
        from app.resolver import _search_composio
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # 'box' is contained in both 'dropbox' and 'boxed', but neither is an exact match
        mock_resp.json.return_value = {
            "items": [
                {"name": "Dropbox Cloud", "slug": "dropbox", "meta": {"app_url": "https://dropbox.com"}},
                {"name": "Boxed Enterprise", "slug": "boxed", "meta": {"app_url": "https://box.com"}},
            ]
        }

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            # With official_domain="box.com", it skips ambiguous contained matches and picks domain match
            matched = await _search_composio("box", "box.com")
            assert matched == "boxed"

            # Without official_domain, ambiguous contained match returns None (no_match)
            no_domain_match = await _search_composio("box", "")
            assert no_domain_match is None

    @pytest.mark.anyio
    async def test_composio_single_contained_match_accepted(self):
        """If exactly one toolkit qualifies under contained match, it is accepted."""
        from app.resolver import _search_composio
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"name": "UniqueWidget SaaS", "slug": "unique-widget", "meta": {}},
            ]
        }

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            matched = await _search_composio("UniqueWidget", "")
            assert matched == "unique-widget"

    def test_zero_wait_estimate_display_and_validation(self):
        """0-0 wait estimate with explanation displays as 'none' and survives validation."""
        from app.schema import DraftEstimate
        from app.validation import validate_estimates_in_guide
        from app.render import format_estimate

        # Validation keeps 0-0 estimate when known=True, basis=author_estimate/provider_estimate, and explanation present
        guide = GuideRecord(
            schema_version="2",
            prompt_version="1",
            id="test-zero-est",
            guide=DraftGuideContent(
                oauth_support="confirmed",
                paths=[
                    DraftPath(
                        id="own_app_testing",
                        name="Testing Path",
                        waiting=DraftEstimate(
                            known=True,
                            low=0,
                            high=0,
                            unit="days",
                            basis="author_estimate",
                            explanation="No review for test use",
                        ),
                    )
                ],
            ),
        )
        warnings = validate_estimates_in_guide(guide)
        assert guide.guide.paths[0].waiting.known is True
        assert guide.guide.paths[0].waiting.low == 0
        assert guide.guide.paths[0].waiting.high == 0
        assert guide.guide.paths[0].waiting.basis == "author_estimate"

        # format_estimate renders 0-0 with author_estimate as 'none — explanation'
        rendered = format_estimate(guide.guide.paths[0].waiting)
        assert rendered == "none — No review for test use"

    @pytest.mark.anyio
    async def test_research_document_block_extraction_and_error_url_mapping(self):
        """Research handles real Anthropic SDK DocumentBlock, skips PDFs, and maps fetch errors to URLs."""
        from anthropic.types import Message
        from app.research import run_research
        from app.schema import ProviderInfo, Scenario, ComposioInfo
        from unittest.mock import patch, MagicMock, AsyncMock

        msg_t1 = Message.model_validate({
            "id": "msg_sdk_types",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [
                {
                    "type": "server_tool_use",
                    "id": "fetch_tool_ok",
                    "name": "web_fetch",
                    "input": {"url": "https://example.com/oauth-docs"},
                },
                {
                    "type": "web_fetch_tool_result",
                    "tool_use_id": "fetch_tool_ok",
                    "content": {
                        "type": "web_fetch_result",
                        "url": "https://example.com/oauth-docs",
                        "retrieved_at": "2026-11-04T00:00:00Z",
                        "content": {
                            "type": "document",
                            "title": "OAuth Docs",
                            "citations": None,
                            "source": {
                                "type": "text",
                                "media_type": "text/plain",
                                "data": "Full text of OAuth documentation for verification.",
                            },
                        },
                    },
                },
                {
                    "type": "server_tool_use",
                    "id": "fetch_tool_err",
                    "name": "web_fetch",
                    "input": {"url": "https://example.com/broken-page"},
                },
                {
                    "type": "web_fetch_tool_result",
                    "tool_use_id": "fetch_tool_err",
                    "content": {
                        "type": "web_fetch_tool_result_error",
                        "error_code": "unavailable",
                    },
                },
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })

        provider = ProviderInfo(name="Example", official_domain="example.com", identity_status="resolved")
        scenario = Scenario(feature="test")
        composio = ComposioInfo()

        with patch("app.research.ANTHROPIC_API_KEY", "mock-key"):
            with patch("anthropic.AsyncAnthropic") as mock_cls:
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(return_value=msg_t1)
                mock_cls.return_value = mock_client

                import time
                findings, sources, stats, page_texts = await run_research(provider, scenario, composio, deadline=time.time() + 60)

                # Check page_texts contains string and matches source
                assert len(page_texts) == 1
                ok_source = next(s for s in sources if s.url == "https://example.com/oauth-docs")
                assert ok_source.retrieval_status == "read"
                assert isinstance(page_texts[ok_source.id], str)
                assert "Full text of OAuth documentation" in page_texts[ok_source.id]

                # Check error source mapped URL by tool_use_id
                err_source = next(s for s in sources if s.url == "https://example.com/broken-page")
                assert err_source.retrieval_status == "failed"
                assert "unavailable" in err_source.title

    def test_promote_gate_flags_require_source_or_attempt(self, tmp_path):
        """--pass-gate without source or attempt is rejected with clear error."""
        db_path = str(tmp_path / "test_promote_gate.db")
        store = Store(db_path)
        guide_record = GuideRecord(
            schema_version="2",
            prompt_version="1",
            id="gate-guide",
            guide=DraftGuideContent(oauth_support="confirmed"),
        )
        store.save_guide(guide_record)

        # Without --source and without --attempt-id
        cmd = [
            sys.executable,
            "scripts/promote.py",
            "gate-guide",
            "1",
            "--reviewer",
            "Alice",
            "--pass-gate",
            "app_review",
        ]
        env = os.environ.copy()
        env["DATABASE_PATH"] = db_path
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        assert res.returncode != 0
        assert "--source (URL or S-id) or --attempt-id is required" in res.stdout
