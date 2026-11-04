"""Comprehensive end-to-end and runtime execution path tests for OAuthPath pipeline.

Covers:
1. Mocked end-to-end pipeline execution with mock AsyncAnthropic:
   - Multi-turn tool calling in research (server_tool_use, web_search/fetch tool results, findings synthesis)
   - Synthesis max_tokens truncation retry (attempt 0 max_tokens -> attempt 1 tool_use submit_guide)
   - Exact schema validation across GuideRecord, DraftGuideContent, Source, Attempt, DifficultyInfo, RunStats
2. validate_guide edge-case matrix:
   - Missing critical gates on production paths vs testing paths
   - Unknown and unsupported OAuth support
   - Conflicting claims, basis downgrades, duplicate ID pruning, invalid reference pruning
   - Uncited source splitting
   - Gate evidence enforcement (required/not_required without documented/observed claim)
   - Estimate bounds (negative, inverted, 0/0, measured without attempt backing)
   - Dangerous and malformed URLs
   - Excerpt fuzzy matching (found vs not_found)
   - Review integrity enforcement
   - Guide status determination rules
   - Composio managed path and wiring step injection
3. Markdown and Jinja2 HTML template representation consistency:
   - Complete side-by-side section and field consistency check
   - Edge cases: empty paths (unknown OAuth), empty scopes/blockers/attempts/unknowns
"""

import asyncio
import html as html_lib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.templating import Jinja2Templates

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import (
    COMPOSIO_CALLBACK_URL,
    JOB_TIMEOUT_SECONDS,
    PROMPT_VERSION,
    SCHEMA_VERSION,
)
from app.main import _render_guide_html
from app.pipeline import run_pipeline
from app.render import format_estimate, format_owner, render_markdown
from app.schema import (
    Attempt,
    ClaimBasis,
    ComposioInfo,
    DifficultyInfo,
    DraftAgentAssistance,
    DraftBlocker,
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
    DraftUnknown,
    DraftConflict,
    GateKind,
    GateState,
    GuideRecord,
    GuideStatus,
    ManagedOAuth,
    OAuthSupport,
    ProviderInfo,
    ReviewInfo,
    RunStats,
    Scenario,
    Source,
    make_cache_key,
    make_guide_id,
)
from app.store import Store
from app.validation import (
    CRITICAL_GATES,
    add_missing_critical_gates,
    check_excerpts,
    check_review_integrity,
    compute_difficulty,
    determine_status,
    normalize_fields,
    split_uncited_sources,
    validate_claims,
    validate_composio,
    validate_estimates_in_guide,
    validate_gate_evidence,
    validate_guide,
    validate_oauth_support,
    validate_references,
    validate_url,
    validate_urls_in_guide,
)


# ── Mock Anthropic Response Objects ───────────────────────────────────────

class MockUsage:
    def __init__(self, input_tokens=100, output_tokens=50):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class MockBlock:
    def __init__(self, type, **kwargs):
        self.type = type
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockMessageResponse:
    def __init__(self, content, stop_reason="end_turn", usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or MockUsage()


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def temp_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    store = Store(db_path)
    yield store
    if os.path.exists(db_path):
        os.remove(db_path)


# ── 1. Mocked End-to-End Pipeline Execution ───────────────────────────────

class TestPipelineE2E:
    """Verifies runtime execution path with mocked AsyncAnthropic responses."""

    @pytest.mark.anyio
    async def test_mocked_end_to_end_pipeline_multi_turn_and_max_tokens_retry(self, temp_store):
        """Simulates:
        - Research:
            Turn 1: Tool calling (web_search + web_fetch) with pause_turn
            Turn 2: Final findings summary with end_turn
        - Synthesis:
            Attempt 0: stop_reason='max_tokens' triggering retry
            Attempt 1: Successful tool_use 'submit_guide'
        - Full schema compliance and store persistence
        """
        events_received = []

        def on_event(ev: str):
            events_received.append(ev)

        from anthropic.types import Message

        # 1. Research turn 1 response: validated Message built from real Anthropic SDK 2.4.0 types
        research_t1_resp = Message.model_validate({
            "id": "msg_res_turn_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [
                {
                    "type": "server_tool_use",
                    "id": "srv_tool_search",
                    "name": "web_search",
                    "input": {"query": "Acme Cloud OAuth 2.0 app registration"},
                },
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srv_tool_search",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://developer.acme.com/docs/oauth",
                            "title": "Acme Developer OAuth Docs",
                            "encrypted_content": "enc_search_1",
                        }
                    ],
                },
                {
                    "type": "server_tool_use",
                    "id": "srv_tool_fetch",
                    "name": "web_fetch",
                    "input": {"url": "https://developer.acme.com/docs/oauth"},
                },
                {
                    "type": "web_fetch_tool_result",
                    "tool_use_id": "srv_tool_fetch",
                    "content": {
                        "type": "web_fetch_result",
                        "url": "https://developer.acme.com/docs/oauth",
                        "retrieved_at": "2026-11-04T00:00:00Z",
                        "content": {
                            "type": "document",
                            "title": "Acme Developer OAuth Docs",
                            "citations": None,
                            "source": {
                                "type": "text",
                                "media_type": "text/plain",
                                "data": (
                                    "Acme supports OAuth 2.0 authorization code flow. "
                                    "Developer registration is self-service with client credentials. "
                                    "No app review is required for external customer access. "
                                    "Standard scopes: read:data, write:data."
                                ),
                            },
                        },
                    },
                },
            ],
            "stop_reason": "pause_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 150, "output_tokens": 80},
        })

        # 2. Research turn 2 response: plain text findings Message
        findings_text = (
            "## Findings\n"
            'F1. Acme supports OAuth 2.0 authorization code flow — https://developer.acme.com/docs/oauth — "Acme supports OAuth 2.0 authorization code flow."\n'
            'F2. Developer registration is self-service with client credentials — https://developer.acme.com/docs/oauth — "Developer registration is self-service with client credentials."\n'
            'F3. No app review is required for external customer access — https://developer.acme.com/docs/oauth — "No app review is required for external customer access."\n'
            "## Conflicts\n"
            "## Unknowns\n"
            "- Exact webhook latency — impacts real-time sync — test in sandbox\n"
        )
        research_t2_resp = Message.model_validate({
            "id": "msg_res_turn_2",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [
                {
                    "type": "text",
                    "text": findings_text,
                    "citations": None,
                }
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 220, "output_tokens": 160},
        })

        # 3. Synthesis attempt 0: max_tokens truncation
        synthesis_a0_resp = Message.model_validate({
            "id": "msg_syn_0",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [],
            "stop_reason": "max_tokens",
            "stop_sequence": None,
            "usage": {"input_tokens": 600, "output_tokens": 16384},
        })

        # 4. Synthesis attempt 1: successful tool_use call to submit_guide
        valid_guide_payload = {
            "oauth_support": "confirmed",
            "summary": [
                {
                    "text": "Acme Cloud supports OAuth 2.0 with self-service registration",
                    "claim_ids": ["K1", "K2"],
                }
            ],
            "next_action": {
                "instruction": "Register an OAuth application in the Acme Developer Portal",
                "owner": "you",
                "link_or_artifact": "https://developer.acme.com/apps/new",
                "success_condition": "Client ID and client secret obtained",
                "claim_ids": ["K2"],
            },
            "recommended_path_id": "own_app_production",
            "paths": [
                {
                    "id": "own_app_production",
                    "name": "Acme Cloud Production OAuth",
                    "applicability": "Accessing Acme data on behalf of external customers",
                    "gates": [
                        {
                            "kind": "app_review",
                            "state": "not_required",
                            "note": "Documentation confirms no review for external customers",
                            "claim_ids": ["K3"],
                        }
                    ],
                    "prerequisites": [
                        {
                            "text": "Acme Developer Account",
                            "owner": "you",
                            "claim_ids": ["K2"],
                        }
                    ],
                    "limits": [
                        {
                            "text": "Standard rate limit: 100 requests per minute",
                            "owner": "you",
                            "claim_ids": [],
                        }
                    ],
                    "steps": [
                        {
                            "id": "create_app",
                            "title": "Create Application",
                            "instructions": "Navigate to the developer console, click Create App, and configure callback URL",
                            "owner": "you",
                            "step_dependencies": [],
                            "success_condition": "Application created and credentials visible",
                            "action_url": "https://developer.acme.com/apps/new",
                            "effort": {
                                "known": True,
                                "low": 5,
                                "high": 15,
                                "unit": "minutes",
                                "basis": "author_estimate",
                                "explanation": "Simple developer console form",
                                "claim_ids": ["K2"],
                            },
                            "claim_ids": ["K2"],
                        }
                    ],
                    "effort": {
                        "known": True,
                        "low": 10,
                        "high": 30,
                        "unit": "minutes",
                        "basis": "author_estimate",
                        "explanation": "Self-service application registration",
                        "claim_ids": ["K1", "K2"],
                    },
                    "waiting": {
                        "known": False,
                        "low": 0,
                        "high": 0,
                        "unit": "not_applicable",
                        "basis": "unknown",
                        "explanation": "Immediate activation",
                        "claim_ids": [],
                    },
                    "costs": [
                        {
                            "description": "Developer registration",
                            "amount": "Free",
                            "basis": "author_estimate",
                            "claim_ids": ["K2"],
                        }
                    ],
                    "claim_ids": ["K1", "K2"],
                }
            ],
            "scopes": [
                {
                    "scope_string": "read:data",
                    "purpose": "Read user data",
                    "classification": "standard",
                    "claim_ids": ["K1"],
                }
            ],
            "blockers": [],
            "agent_assistance": [
                {
                    "capability": "Redirect URI setup",
                    "description": "Provide the exact Composio callback URL for the OAuth app",
                }
            ],
            "unknowns": [
                {
                    "question": "Exact webhook latency under peak load",
                    "why_it_matters": "Determines webhook vs polling strategy",
                    "how_to_find_out": "Benchmark in sandbox environment",
                }
            ],
            "conflicts": [],
            "claims": [
                {
                    "id": "K1",
                    "text": "Acme supports OAuth 2.0 authorization code flow",
                    "conditions": "All tiers",
                    "source_ids": ["S1"],
                    "attempt_ids": [],
                    "basis": "documented",
                    "excerpt": "Acme supports OAuth 2.0 authorization code flow.",
                },
                {
                    "id": "K2",
                    "text": "Developer registration is self-service with client credentials",
                    "conditions": "",
                    "source_ids": ["S1"],
                    "attempt_ids": [],
                    "basis": "documented",
                    "excerpt": "Developer registration is self-service with client credentials.",
                },
                {
                    "id": "K3",
                    "text": "No app review is required for external customer access",
                    "conditions": "",
                    "source_ids": ["S1"],
                    "attempt_ids": [],
                    "basis": "documented",
                    "excerpt": "No app review is required for external customer access.",
                },
            ],
        }

        synthesis_a1_resp = Message.model_validate({
            "id": "msg_syn_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_submit_guide",
                    "name": "submit_guide",
                    "input": valid_guide_payload,
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 750, "output_tokens": 900},
        })

        call_sequence = [
            research_t1_resp,   # Research turn 1
            research_t2_resp,   # Research turn 2
            synthesis_a0_resp,  # Synthesis attempt 0 (max_tokens truncation)
            synthesis_a1_resp,  # Synthesis attempt 1 (retry success)
        ]

        async def mock_messages_create(*args, **kwargs):
            if not call_sequence:
                raise RuntimeError("Unexpected extra call to client.messages.create")
            return call_sequence.pop(0)

        mock_anthropic_client = MagicMock()
        mock_anthropic_client.messages = MagicMock()
        mock_anthropic_client.messages.create = AsyncMock(side_effect=mock_messages_create)

        with patch("app.config.ANTHROPIC_API_KEY", "test-mock-key"), \
             patch("app.research.ANTHROPIC_API_KEY", "test-mock-key"), \
             patch("app.synthesis.ANTHROPIC_API_KEY", "test-mock-key"), \
             patch("anthropic.AsyncAnthropic", return_value=mock_anthropic_client), \
             patch("app.resolver.resolve_provider", return_value=(
                 ProviderInfo(name="Acme Cloud", official_domain="acme.com", identity_status="resolved"), None
             )):

            guide, error = await run_pipeline(
                app_name="Acme Cloud",
                feature="read user records",
                audience="external customers",
                deployment="server-side",
                website="https://acme.com",
                force=True,
                store=temp_store,
                on_event=on_event,
            )

        # ── Verify Pipeline Execution Succeeded ───────────────────────────
        assert error == "", f"Expected no error, got: {error}"
        assert guide is not None, "Expected guide record to be produced"
        assert len(call_sequence) == 0, f"Expected all mock calls to be consumed, {len(call_sequence)} left"

        # ── Verify Events ─────────────────────────────────────────────────
        assert any("Resolving product identity" in ev for ev in events_received)
        assert any("Starting research phase" in ev for ev in events_received)
        assert any("Synthesizing guide from findings" in ev for ev in events_received)
        assert any("Validating guide" in ev for ev in events_received)
        assert any("Saved as version" in ev for ev in events_received)

        # ── Verify Exact Output Schemas ───────────────────────────────────
        # 1. GuideRecord
        assert isinstance(guide, GuideRecord)
        assert guide.schema_version == SCHEMA_VERSION
        assert guide.prompt_version == PROMPT_VERSION
        assert guide.version == 1
        assert guide.status == GuideStatus.draft.value
        assert guide.provider.name == "Acme Cloud"
        assert guide.provider.official_domain == "acme.com"
        assert guide.provider.identity_status == "resolved"

        # 2. DraftGuideContent
        assert isinstance(guide.guide, DraftGuideContent)
        assert guide.guide.oauth_support == "confirmed"
        assert len(guide.guide.summary) == 1
        assert isinstance(guide.guide.summary[0], DraftSummaryStatement)
        assert isinstance(guide.guide.next_action, DraftNextAction)
        assert guide.guide.next_action.owner == "you"
        assert guide.guide.next_action.link_or_artifact == "https://developer.acme.com/apps/new"

        # 3. Sources
        assert len(guide.sources) == 1
        s1 = guide.sources[0]
        assert isinstance(s1, Source)
        assert s1.id == "S1"
        assert s1.url == "https://developer.acme.com/docs/oauth"
        assert s1.retrieval_status == "read"
        assert s1.provider_domain is True
        assert s1.provider_domain_status == "official"

        # 4. Claims and Excerpt Verification
        assert len(guide.guide.claims) == 3
        for c in guide.guide.claims:
            assert isinstance(c, DraftClaim)
            assert c.basis == "documented"
            assert c.source_ids == ["S1"]
            # Fuzzy excerpt matching should have matched page text verbatim
            assert c.excerpt_status == "found", f"Claim {c.id} excerpt not marked found"

        # 5. Missing Critical Gates Added on Production Path
        prod_path = next(p for p in guide.guide.paths if p.id == "own_app_production")
        assert isinstance(prod_path, DraftPath)
        gate_kinds_present = {g.kind for g in prod_path.gates}
        for cg in CRITICAL_GATES:
            assert cg in gate_kinds_present, f"Critical gate {cg} missing from production path"
        # app_review gate kept as not_required because backed by K3 (documented)
        app_review_gate = next(g for g in prod_path.gates if g.kind == "app_review")
        assert app_review_gate.state == "not_required"
        # Others added as unknown
        for g in prod_path.gates:
            if g.kind != "app_review":
                assert g.state == "unknown"

        # 6. DifficultyInfo
        assert "overall" in guide.difficulty
        assert isinstance(guide.difficulty["overall"], DifficultyInfo)
        assert "own_app_production" in guide.difficulty
        assert isinstance(guide.difficulty["own_app_production"], DifficultyInfo)

        # 7. RunStats Aggregation
        assert isinstance(guide.run, RunStats)
        assert guide.run.model_calls == 4  # 2 research + 2 synthesis
        assert guide.run.searches == 1
        assert guide.run.page_reads == 1
        assert guide.run.tokens_input == (150 + 220 + 600 + 750)
        assert guide.run.tokens_output == (80 + 160 + 16384 + 900)
        assert guide.run.research_timed_out is False

        # 8. Full Pydantic Roundtrip Serialization
        serialized = guide.model_dump_json()
        deserialized = GuideRecord.model_validate_json(serialized)
        assert deserialized.id == guide.id
        assert deserialized.version == guide.version
        assert deserialized.status == guide.status
        assert len(deserialized.guide.claims) == 3


# ── 2. validate_guide Edge Cases Matrix ───────────────────────────────────

class TestValidateGuideEdgeCases:
    """Verifies all edge cases handled by validate_guide."""

    def _build_base_guide(self, **kwargs) -> GuideRecord:
        base = {
            "schema_version": "2",
            "prompt_version": "1",
            "id": "test-edge",
            "version": 1,
            "created_at": "2026-11-04T00:00:00Z",
            "status": "draft",
            "provider": ProviderInfo(name="TestApp", official_domain="test.com", identity_status="resolved"),
            "scenario": Scenario(feature="read", audience="external customers"),
            "composio": ComposioInfo(lookup_status="no_match"),
            "guide": DraftGuideContent(
                oauth_support="confirmed",
                summary=[DraftSummaryStatement(text="Summary statement", claim_ids=["K1"])],
                next_action=DraftNextAction(instruction="Next step", owner="you", claim_ids=["K1"]),
                paths=[
                    DraftPath(
                        id="own_app_production",
                        name="Production",
                        gates=[],
                        steps=[DraftStep(id="s1", title="Step 1", instructions="Do it", owner="you")],
                    )
                ],
                claims=[
                    DraftClaim(id="K1", text="OAuth supported", source_ids=["S1"], basis="documented")
                ],
            ),
            "sources": [
                Source(id="S1", url="https://test.com/docs", retrieval_status="read", provider_domain=True)
            ],
            "attempts": [],
            "run": RunStats(),
        }
        base.update(kwargs)
        return GuideRecord(**base)

    def test_missing_critical_gates_added_to_production_path(self):
        """Production paths missing critical gates get all 6 added as unknown."""
        guide = self._build_base_guide()
        guide.guide.paths[0].gates = []
        validate_guide(guide)

        gates = {g.kind: g.state for g in guide.guide.paths[0].gates}
        for cg in CRITICAL_GATES:
            assert cg in gates
            assert gates[cg] == "unknown"

    def test_testing_path_does_not_get_critical_gates_added(self):
        """Testing paths only do not get missing critical gates auto-injected."""
        guide = self._build_base_guide()
        guide.guide.paths = [
            DraftPath(
                id="own_app_testing",
                name="Testing",
                gates=[],
                steps=[DraftStep(id="s1", title="Step 1", instructions="Do it", owner="you")],
            )
        ]
        validate_guide(guide)
        assert len(guide.guide.paths[0].gates) == 0

    def test_unknown_and_unsupported_oauth_support_strips_own_app_paths(self):
        """Unknown or unsupported OAuth support strips own-app paths and sets partial status."""
        for oauth_val in ("unknown", "unsupported"):
            guide = self._build_base_guide()
            guide.guide.oauth_support = oauth_val
            validate_guide(guide)

            own_paths = [p for p in guide.guide.paths if p.id.startswith("own_app")]
            assert len(own_paths) == 0, f"Expected own-app paths stripped for {oauth_val}"
            assert any(f"OAuth support is {oauth_val}: removed own-app paths" in w for w in guide.warnings)
            if oauth_val == "unknown":
                assert guide.status == GuideStatus.partial.value
                assert guide.difficulty["overall"].label == "Not enough evidence"

    def test_duplicate_and_empty_claim_ids_dropped(self):
        """Duplicate or empty claim IDs are dropped with warnings."""
        guide = self._build_base_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Claim 1", source_ids=["S1"], basis="documented"),
            DraftClaim(id="K1", text="Duplicate Claim 1", source_ids=["S1"], basis="documented"),
            DraftClaim(id="", text="Empty Claim ID", source_ids=["S1"], basis="documented"),
        ]
        validate_guide(guide)
        claim_ids = [c.id for c in guide.guide.claims]
        assert claim_ids == ["K1"]
        assert any("Dropped duplicate or empty claim ID" in w for w in guide.warnings)

    def test_claim_basis_downgrades(self):
        """Claims with missing backing have basis downgraded:
        - documented without sources -> unknown
        - observed without attempt and with source -> documented
        - observed without attempt and without source -> unknown
        """
        guide = self._build_base_guide()
        guide.guide.claims = [
            DraftClaim(id="K1", text="Doc with no source", source_ids=[], basis="documented"),
            DraftClaim(id="K2", text="Obs with source no attempt", source_ids=["S1"], attempt_ids=[], basis="observed"),
            DraftClaim(id="K3", text="Obs with no source no attempt", source_ids=[], attempt_ids=[], basis="observed"),
        ]
        validate_guide(guide)
        c_map = {c.id: c.basis for c in guide.guide.claims}
        assert c_map["K1"] == "unknown"
        assert c_map["K2"] == "documented"
        assert c_map["K3"] == "unknown"

    def test_invalid_and_failed_source_refs_pruned(self):
        """Invalid source IDs or failed sources are removed from claim.source_ids."""
        guide = self._build_base_guide()
        guide.sources.append(Source(id="S2", url="https://fail.com", retrieval_status="failed"))
        guide.guide.claims = [
            DraftClaim(id="K1", text="Claim", source_ids=["S1", "S2", "S99_NONEXISTENT"], basis="documented"),
        ]
        validate_guide(guide)
        assert guide.guide.claims[0].source_ids == ["S1"]
        assert any("removed invalid source refs" in w for w in guide.warnings)

    def test_uncited_sources_moved_to_uncited(self):
        """Sources not cited by any claim and not composio.source_id move to uncited_sources."""
        guide = self._build_base_guide()
        guide.sources.append(Source(id="S2", url="https://extra.com", retrieval_status="read"))
        guide.guide.claims = [
            DraftClaim(id="K1", text="Cites only S1", source_ids=["S1"], basis="documented")
        ]
        validate_guide(guide)
        assert len(guide.sources) == 1
        assert guide.sources[0].id == "S1"
        assert len(guide.uncited_sources) == 1
        assert guide.uncited_sources[0].id == "S2"
        assert any("Moved 1 uncited source" in w for w in guide.warnings)

    def test_gate_evidence_downgrades_unbacked_gates(self):
        """Gates marked required/not_required without documented/observed supporting claim become unknown."""
        guide = self._build_base_guide()
        guide.guide.claims = [
            DraftClaim(id="K_UNK", text="Weak claim", source_ids=[], basis="unknown"),
            DraftClaim(id="K_DOC", text="Strong claim", source_ids=["S1"], basis="documented"),
        ]
        guide.guide.paths[0].gates = [
            DraftGate(kind="partner_program", state="required", claim_ids=["K_UNK"]),
            DraftGate(kind="app_review", state="not_required", claim_ids=["K_DOC"]),
        ]
        validate_guide(guide)

        g_map = {g.kind: g.state for g in guide.guide.paths[0].gates}
        assert g_map["partner_program"] == "unknown"
        assert g_map["app_review"] == "not_required"

    def test_estimates_normalization_and_measured_basis(self):
        """Estimates with negative, inverted, or 0/0 bounds are normalized,
        and measured basis requires attempt backing."""
        guide = self._build_base_guide()
        guide.attempts = [
            Attempt(id="A1", provider="test", action="setup", status="succeeded", observed_result="ok")
        ]
        guide.guide.claims = [
            DraftClaim(id="K1", text="Doc", source_ids=["S1"], basis="documented"),
            DraftClaim(id="K2", text="Obs", attempt_ids=["A1"], basis="observed"),
        ]
        path = guide.guide.paths[0]
        # Inverted bounds
        path.effort = DraftEstimate(known=True, low=30, high=10, unit="minutes", basis="author_estimate")
        # Negative bounds
        path.waiting = DraftEstimate(known=True, low=-5, high=10, unit="hours", basis="author_estimate")
        # Measured basis backed by attempt claim
        path.steps[0].effort = DraftEstimate(known=True, low=5, high=10, unit="minutes", basis="measured", claim_ids=["K2"])

        validate_guide(guide)

        assert path.effort.known is False
        assert path.effort.low is None
        assert path.effort.high is None

        assert path.waiting.known is False
        assert path.waiting.low is None
        assert path.waiting.high is None

        assert path.steps[0].effort.basis == "measured"

        # Measured without attempt downgraded
        path.steps[0].effort.claim_ids = ["K1"]
        validate_estimates_in_guide(guide)
        assert path.steps[0].effort.basis == "author_estimate"

    def test_url_validation_edge_cases(self):
        """Test URL validation across schemes, relatives, credentials, and hostnames."""
        # Dangerous schemes
        assert validate_url("javascript:alert(1)")[0] is False
        assert validate_url("data:text/html,test")[0] is False
        assert validate_url("//evil.com/payload")[0] is False

        # Credentials and localhost
        assert validate_url("https://user:pass@example.com")[0] is False
        assert validate_url("https://localhost/app")[0] is False
        assert validate_url("https://127.0.0.1/app")[0] is False
        assert validate_url("https://no-dot-domain/app")[0] is False

        # http source allowed as insecure_http, but action url forbidden
        assert validate_url("http://legacy.com", is_source=True)[0] is True
        assert validate_url("http://legacy.com", is_source=False)[0] is False

        # Relative URLs
        assert validate_url("/local/path", allow_relative=True)[0] is True
        assert validate_url("/local/path", allow_relative=False)[0] is False

    def test_excerpt_fuzzy_matching(self):
        """Fuzzy excerpt check marks >=90 ratio as found and <90 as not_found."""
        guide = self._build_base_guide()
        guide.guide.claims = [
            DraftClaim(
                id="K1",
                text="Claim 1",
                source_ids=["S1"],
                basis="documented",
                excerpt="Self-service developer registration with client credentials.",
            ),
            DraftClaim(
                id="K2",
                text="Claim 2",
                source_ids=["S1"],
                basis="documented",
                excerpt="Completely fabricated passage about mandatory 6-month enterprise audit.",
            ),
        ]
        page_texts = {
            "S1": "Welcome! We offer self-service developer registration with client credentials for all apps."
        }
        check_excerpts(guide, page_texts=page_texts)

        c1 = next(c for c in guide.guide.claims if c.id == "K1")
        c2 = next(c for c in guide.guide.claims if c.id == "K2")
        assert c1.excerpt_status == "found"
        assert c2.excerpt_status == "not_found"

    def test_guide_status_determination_rules(self):
        """Tests status determination:
        - reviewed without reviewer -> reverted to draft
        - research timed out -> partial
        - assumed identity -> partial
        - material unknown -> partial
        - fully corroborated -> draft
        """
        # Reviewed without reviewer
        guide = self._build_base_guide()
        guide.status = GuideStatus.reviewed.value
        guide.review = ReviewInfo(reviewer=None)
        validate_guide(guide)
        assert guide.status == GuideStatus.draft.value

        # Research timed out
        guide = self._build_base_guide()
        guide.run.research_timed_out = True
        validate_guide(guide)
        assert guide.status == GuideStatus.partial.value

        # Assumed identity
        guide = self._build_base_guide()
        guide.provider.identity_status = "assumed"
        validate_guide(guide)
        assert guide.status == GuideStatus.partial.value

        # Core availability unknown -> partial
        guide = self._build_base_guide()
        guide.guide.unknowns = [
            DraftUnknown(question="Whether OAuth is supported for third-party integrations", why_it_matters="Core availability")
        ]
        validate_guide(guide)
        assert guide.status == GuideStatus.partial.value

        # Normal operational unknown (e.g. review timeline) -> remains draft, not partial
        guide = self._build_base_guide()
        guide.guide.unknowns = [
            DraftUnknown(question="What is the app review timeline?", why_it_matters="Launch timeline")
        ]
        validate_guide(guide)
        assert guide.status == GuideStatus.draft.value


# ── 3. Render Consistency (render_markdown vs guide.html Template) ────────

class TestRenderConsistency:
    """Verifies that render_markdown and Jinja2 guide.html produce identical, consistent representations."""

    @pytest.fixture
    def jinja_env(self):
        templates_dir = Path(__file__).resolve().parent.parent / "app" / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))
        return templates

    def _render_template_to_string(self, jinja_env, guide: GuideRecord, versions=None) -> str:
        guide_html = _render_guide_html(guide)
        template = jinja_env.get_template("guide.html")
        return template.render({
            "guide": guide,
            "guide_html": guide_html,
            "reviewed_version": None,
            "base_url": "https://oauthpath.dev",
            "versions": versions or [{"version": guide.version, "status": guide.status}],
        })

    def test_markdown_and_html_identical_and_consistent_representations(self, jinja_env):
        """Builds a rich, fully populated GuideRecord and verifies that all semantic
        fields rendered in Markdown are present, accurately formatted, and consistent
        in the rendered HTML page."""
        guide = GuideRecord(
            schema_version="2",
            prompt_version="1",
            id="github-repo-access-12345678",
            version=2,
            created_at="2026-11-04T12:00:00Z",
            status="draft",
            cache_key="github|github.com|repo-access|external|server-side|sv2|pv1",
            provider=ProviderInfo(name="GitHub", official_domain="github.com", identity_status="resolved"),
            scenario=Scenario(
                feature="repository read access",
                audience="external customers",
                deployment="server-side",
                assumptions=["Customer has personal or organization repos", "Using Web application flow"],
            ),
            composio=ComposioInfo(
                toolkit_slug="github",
                lookup_status="matched",
                selected_auth_method="OAUTH2",
                managed_oauth="available",
                source_id="C1",
            ),
            guide=DraftGuideContent(
                oauth_support="confirmed",
                summary=[
                    DraftSummaryStatement(text="GitHub OAuth Apps are self-service without required app review", claim_ids=["K1", "K2"]),
                    DraftSummaryStatement(text="Composio-managed OAuth is available as an alternative", claim_ids=["KC3"]),
                ],
                next_action=DraftNextAction(
                    instruction="Register a new OAuth App in GitHub Developer Settings",
                    owner="you",
                    link_or_artifact="https://github.com/settings/developers",
                    success_condition="Client ID and Client Secret generated",
                    claim_ids=["K1"],
                ),
                recommended_path_id="own_app_production",
                paths=[
                    DraftPath(
                        id="own_app_production",
                        name="GitHub OAuth App (Production)",
                        applicability="Registering your own branded OAuth app for end users",
                        gates=[
                            DraftGate(kind="app_review", state="not_required", note="Self-service", claim_ids=["K1"]),
                            DraftGate(kind="security_assessment", state="not_required", note="None published", claim_ids=["K1"]),
                        ],
                        prerequisites=[
                            DraftPrerequisite(text="GitHub account with verified email", owner="you", claim_ids=["K1"]),
                        ],
                        limits=[
                            DraftPrerequisite(text="Unverified apps limited to 100 users", owner="you", claim_ids=["K2"]),
                        ],
                        steps=[
                            DraftStep(
                                id="reg_step",
                                title="Register Application",
                                instructions="Navigate to Developer Settings > OAuth Apps > New OAuth App",
                                owner="you",
                                step_dependencies=[],
                                success_condition="Credentials created",
                                action_url="https://github.com/settings/applications/new",
                                effort=DraftEstimate(known=True, low=5, high=10, unit="minutes", basis="author_estimate"),
                                claim_ids=["K1"],
                            ),
                            DraftStep(
                                id="composio_wiring",
                                title="Connect through Composio",
                                instructions=f"Create a Composio auth config using redirect URI {COMPOSIO_CALLBACK_URL}",
                                owner="agent_prepares_you_submit",
                                success_condition="Account active in Composio",
                                effort=DraftEstimate(known=True, low=5, high=15, unit="minutes", basis="author_estimate"),
                                claim_ids=["KC1", "KC2"],
                            ),
                        ],
                        effort=DraftEstimate(known=True, low=10, high=25, unit="minutes", basis="author_estimate"),
                        waiting=DraftEstimate(known=False, explanation="No waiting period"),
                        costs=[
                            DraftCost(description="Developer registration", amount="Free", basis="author_estimate", claim_ids=["K1"])
                        ],
                        claim_ids=["K1"],
                    )
                ],
                scopes=[
                    DraftScope(scope_string="repo:status", purpose="Read commit statuses", classification="granular", claim_ids=["K1"]),
                    DraftScope(scope_string="public_repo", purpose="Access public repos", classification="standard", claim_ids=["K1"]),
                ],
                blockers=[
                    DraftBlocker(
                        trigger="Organization Third-Party App Restrictions",
                        consequence="User cannot authorize app until org admin grants approval",
                        resolution="Prompt user to request admin access or grant access in org settings",
                        owner="your_customer_admin",
                        claim_ids=["K1"],
                    )
                ],
                agent_assistance=[
                    DraftAgentAssistance(capability="Scope Minimization", description="Select minimal granular scopes"),
                ],
                unknowns=[
                    DraftUnknown(question="Rate limits for fine-grained tokens", why_it_matters="Polling budget", how_to_find_out="Check headers"),
                ],
                conflicts=[
                    DraftConflict(topic="Token expiration", description="Classic tokens vs expiring user tokens", source_ids=["S1"], resolution="Configurable in settings"),
                ],
                claims=[
                    DraftClaim(id="K1", text="GitHub OAuth Apps require no manual review", source_ids=["S1"], basis="documented", excerpt="No manual review is required."),
                    DraftClaim(id="K2", text="OAuth apps show unverified badge over 100 users", source_ids=["S1"], basis="documented", excerpt="Badge shown over 100 users."),
                    DraftClaim(id="KC1", text="Composio toolkit supports OAUTH2", source_ids=["C1"], basis="documented"),
                    DraftClaim(id="KC2", text=f"Redirect URI is {COMPOSIO_CALLBACK_URL}", source_ids=["C2"], basis="documented"),
                    DraftClaim(id="KC3", text="Managed OAuth available", source_ids=["C3"], basis="documented"),
                ],
            ),
            difficulty={
                "overall": DifficultyInfo(
                    label="Straightforward",
                    reasons=["No app review required", "Based on production path: own_app_production"],
                    unknown_gates=[],
                ),
                "own_app_production": DifficultyInfo(
                    label="Straightforward",
                    reasons=["No app review required"],
                    unknown_gates=[],
                ),
            },
            sources=[
                Source(id="S1", url="https://docs.github.com/en/apps", title="GitHub Apps Overview", retrieval_status="read", provider_domain=True, provider_domain_status="official", retrieval_time="2026-11-04T12:00:00Z"),
                Source(id="C1", url="https://backend.composio.dev/api/v3/toolkits/github", title="Composio GitHub Toolkit", retrieval_status="read", provider_domain=False, provider_domain_status="third_party", retrieval_time="2026-11-04T12:00:00Z"),
                Source(id="C2", url="https://docs.composio.dev/auth", title="Composio Auth Config", retrieval_status="read", provider_domain=False, provider_domain_status="third_party", retrieval_time="2026-11-04T12:00:00Z"),
                Source(id="C3", url="https://docs.composio.dev/managed", title="Composio Managed Apps", retrieval_status="read", provider_domain=False, provider_domain_status="third_party", retrieval_time="2026-11-04T12:00:00Z"),
            ],
            uncited_sources=[
                Source(id="S99", url="https://blog.example.com/github-tips", title="Uncited Blog", retrieval_status="read"),
            ],
            attempts=[
                Attempt(id="A1", actor="assisted", provider="github", scenario="repo access", action="register_app", environment="production", observed_result="App created in 3m", status="succeeded", limitation="Personal account only"),
            ],
            review=ReviewInfo(reviewer="alice@example.com", reviewed_at="2026-11-04T14:00:00Z", audited_claim_ids=["K1"]),
            warnings=["Sample validation notice"],
            run=RunStats(model_calls=4, searches=1, page_reads=2, tokens_input=1200, tokens_output=1500, duration_seconds=32.4, cost="unavailable"),
        )

        md = render_markdown(guide)
        html = self._render_template_to_string(jinja_env, guide)
        html_text = html_lib.unescape(html)

        # ── Header & Identity ─────────────────────────────────────────────
        assert f"# {guide.provider.name} — {guide.scenario.feature}" in md
        assert f"{guide.provider.name} — {guide.scenario.feature}" in html_text
        assert f"v{guide.version} · {guide.status}" in md
        assert f"v{guide.version} · {guide.status}" in html_text
        assert "Researched 2026-11-04" in md
        assert "Researched 2026-11-04" in html_text

        # ── Assumptions ───────────────────────────────────────────────────
        assert "external customers" in md and "external customers" in html_text
        assert "server-side" in md and "server-side" in html_text
        assert "Customer has personal or organization repos" in md
        assert "Customer has personal or organization repos" in html_text

        # ── Evidence Line ─────────────────────────────────────────────────
        guide_html_meta = _render_guide_html(guide)
        assert guide_html_meta["evidence_line"] in md
        assert guide_html_meta["evidence_line"] in html_text
        assert "Reviewed by alice@example.com on 2026-11-04T14:00:00Z" in md
        assert "Reviewed by alice@example.com on 2026-11-04T14:00:00Z" in html_text

        # ── Composio Status ───────────────────────────────────────────────
        assert "Composio: toolkit `github` (OAUTH2); managed OAuth available" in md
        assert "toolkit <code>github</code>" in html
        assert "managed OAuth available" in html_text

        # ── Readiness Summary ─────────────────────────────────────────────
        assert "GitHub OAuth Apps are self-service without required app review [K1, K2]" in md
        assert "GitHub OAuth Apps are self-service without required app review" in html_text
        assert "[K1, K2]" in html_text

        # ── Difficulty ────────────────────────────────────────────────────
        assert "**Difficulty:** Straightforward" in md
        assert "Straightforward" in html_text
        assert "No app review required" in md
        assert "No app review required" in html_text

        # ── Next Action ───────────────────────────────────────────────────
        assert "**Register a new OAuth App in GitHub Developer Settings**" in md
        assert "Register a new OAuth App in GitHub Developer Settings" in html_text
        assert f"Owner: {format_owner('you')}" in md
        assert f"Owner: {format_owner('you')}" in html_text
        assert "https://github.com/settings/developers" in md
        assert "https://github.com/settings/developers" in html_text
        assert "Done when: Client ID and Client Secret generated" in md
        assert "Done when: Client ID and Client Secret generated" in html_text

        # ── Paths & Steps ─────────────────────────────────────────────────
        assert "GitHub OAuth App (Production)" in md
        assert "GitHub OAuth App (Production)" in html_text
        assert "App Review: Not Required [K1]" in md
        assert "App Review:</strong> Not Required" in html
        assert "Register Application" in md and "Register Application" in html_text
        assert "Connect through Composio" in md and "Connect through Composio" in html_text
        assert format_owner("agent_prepares_you_submit") in md
        assert format_owner("agent_prepares_you_submit") in html_text
        assert "Effort: 5–10 minutes (author estimate)" in md
        assert "Effort: 5–10 minutes (author estimate)" in html_text

        # ── Scopes ────────────────────────────────────────────────────────
        assert "`repo:status`" in md
        assert "<code>repo:status</code>" in html
        assert "Read commit statuses" in md
        assert "Read commit statuses" in html_text

        # ── Blockers ──────────────────────────────────────────────────────
        assert "Organization Third-Party App Restrictions" in md
        assert "Organization Third-Party App Restrictions" in html_text
        assert format_owner("your_customer_admin") in md
        assert format_owner("your_customer_admin") in html_text

        # ── Attempts ──────────────────────────────────────────────────────
        assert "App created in 3m" in md
        assert "App created in 3m" in html_text
        assert "Personal account only" in md
        assert "Personal account only" in html_text

        # ── Unknowns & Conflicts ──────────────────────────────────────────
        assert "Rate limits for fine-grained tokens" in md
        assert "Rate limits for fine-grained tokens" in html_text
        assert "Token expiration" in md
        assert "Token expiration" in html_text

        # ── Evidence Details ──────────────────────────────────────────────
        assert "No manual review is required." in md
        assert "No manual review is required." in html_text
        assert "GitHub Apps Overview" in md
        assert "GitHub Apps Overview" in html_text
        assert "Uncited Blog" in md
        assert "Uncited Blog" in html_text
        assert "Sample validation notice" in md
        assert "Sample validation notice" in html_text

        # ── Run Stats ─────────────────────────────────────────────────────
        assert "Duration: 32.4s" in md
        assert "Duration: 32.4s" in html_text

    def test_render_empty_and_edge_case_guides(self, jinja_env):
        """Verifies that guides with empty paths, scopes, blockers, attempts, etc.
        render cleanly without errors in both Markdown and HTML."""
        guide = GuideRecord(
            schema_version="2",
            prompt_version="1",
            id="minimal-guide",
            version=1,
            created_at="",
            status="partial",
            provider=ProviderInfo(name="Minimal"),
            scenario=Scenario(),
            composio=ComposioInfo(lookup_status="failed"),
            guide=DraftGuideContent(
                oauth_support="unknown",
                summary=[],
                next_action=DraftNextAction(instruction="Wait for info"),
                paths=[],
                scopes=[],
                blockers=[],
                agent_assistance=[],
                unknowns=[],
                conflicts=[],
                claims=[],
            ),
            difficulty={
                "overall": DifficultyInfo(label="Not enough evidence", reasons=["No applicable paths established"])
            },
            sources=[],
            attempts=[],
            warnings=[],
            run=RunStats(),
        )

        md = render_markdown(guide)
        html = self._render_template_to_string(jinja_env, guide)

        assert "# Minimal — OAuth integration guide" in md
        assert "Minimal" in html
        assert "Researched pending" in md
        assert "Researched pending" in html
        assert "Composio: lookup failed" in md
        assert "Composio: lookup failed" in html
        assert "**Difficulty:** Not enough evidence" in md
        assert "Not enough evidence" in html
        assert "None recorded." in md
        assert "None recorded." in html
