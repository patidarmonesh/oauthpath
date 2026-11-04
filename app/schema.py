"""OAuthPath schema — two layers per §6.4.

Model-writable draft models: all fields required, no union types.
Code-only models: Scenario, ComposioInfo, Source, Attempt, GuideRecord.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────

class ClaimBasis(str, Enum):
    documented = "documented"
    observed = "observed"
    estimated = "estimated"
    unknown = "unknown"


class ExcerptStatus(str, Enum):
    found = "found"
    not_found = "not_found"
    not_checked = "not_checked"


class ReviewResult(str, Enum):
    not_reviewed = "not_reviewed"
    supported = "supported"
    contradicted = "contradicted"
    insufficient = "insufficient"


class GateKind(str, Enum):
    app_review = "app_review"
    security_assessment = "security_assessment"
    business_or_identity_verification = "business_or_identity_verification"
    partner_program = "partner_program"
    paid_access = "paid_access"
    cap_before_approval = "cap_before_approval"
    customer_admin_action = "customer_admin_action"
    per_customer_registration = "per_customer_registration"
    recurring_recertification = "recurring_recertification"
    ai_data_use_terms = "ai_data_use_terms"


class GateState(str, Enum):
    required = "required"
    not_required = "not_required"
    unknown = "unknown"


class OAuthSupport(str, Enum):
    confirmed = "confirmed"
    restricted = "restricted"
    unsupported = "unsupported"
    unknown = "unknown"


class DifficultyLabel(str, Enum):
    straightforward = "Straightforward"
    requires_coordination = "Requires coordination"
    substantial_external = "Substantial external requirements"
    not_enough_evidence = "Not enough evidence"
    provisional_straightforward = "Straightforward (provisional)"
    provisional_coordination = "Requires coordination (provisional)"
    provisional_substantial = "Substantial external requirements (provisional)"


class GuideStatus(str, Enum):
    draft = "draft"
    partial = "partial"
    reviewed = "reviewed"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    partial = "partial"
    needs_input = "needs_input"
    failed = "failed"
    interrupted = "interrupted"


class ComposioLookupStatus(str, Enum):
    matched = "matched"
    no_match = "no_match"
    failed = "failed"
    not_attempted = "not_attempted"


class ManagedOAuth(str, Enum):
    available = "available"
    unavailable = "unavailable"
    unknown = "unknown"


class IdentityStatus(str, Enum):
    resolved = "resolved"
    assumed = "assumed"


class EstimateUnit(str, Enum):
    minutes = "minutes"
    hours = "hours"
    business_days = "business_days"
    calendar_days = "calendar_days"
    not_applicable = "not_applicable"


class EstimateBasisKind(str, Enum):
    measured = "measured"
    provider_estimate = "provider_estimate"
    author_estimate = "author_estimate"
    anecdotal = "anecdotal"
    unknown = "unknown"


class SourceKind(str, Enum):
    fetched_page = "fetched_page"
    search_result = "search_result"
    composio_api = "composio_api"
    maintainer_reference = "maintainer_reference"


class SourceRetrievalStatus(str, Enum):
    read = "read"
    search_snippet_only = "search_snippet_only"
    reference_only = "reference_only"
    failed = "failed"


class AttemptStatus(str, Enum):
    succeeded = "succeeded"
    blocked = "blocked"
    failed = "failed"


class AttemptActor(str, Enum):
    agent = "agent"
    human = "human"
    assisted = "assisted"


class OwnerKind(str, Enum):
    you = "you"
    agent = "agent"
    agent_prepares_you_submit = "agent_prepares_you_submit"
    your_customer_admin = "your_customer_admin"
    provider = "provider"


# ── Model-writable draft models (all fields required, no unions) ───────────

class DraftEstimate(BaseModel):
    """Model-facing estimate: all fields required. known=false → null bounds at persistence."""
    known: bool
    low: Optional[int] = None
    high: Optional[int] = None
    unit: str = "not_applicable"
    basis: str = "unknown"
    explanation: str = ""
    claim_ids: list[str] = Field(default_factory=list)


class DraftGate(BaseModel):
    kind: str
    state: str  # required / not_required / unknown
    note: str = ""
    claim_ids: list[str] = Field(default_factory=list)


class DraftStep(BaseModel):
    id: str
    title: str
    instructions: str
    owner: str
    step_dependencies: list[str] = Field(default_factory=list)
    success_condition: str = ""
    action_url: str = ""
    effort: DraftEstimate = Field(default_factory=lambda: DraftEstimate(known=False))
    claim_ids: list[str] = Field(default_factory=list)


class DraftCost(BaseModel):
    description: str = ""
    amount: str = ""
    basis: str = "unknown"
    claim_ids: list[str] = Field(default_factory=list)


class DraftPrerequisite(BaseModel):
    text: str
    owner: str = "you"
    claim_ids: list[str] = Field(default_factory=list)


class DraftPath(BaseModel):
    id: str  # own_app_testing or own_app_production
    name: str
    applicability: str = ""
    gates: list[DraftGate] = Field(default_factory=list)
    prerequisites: list[DraftPrerequisite] = Field(default_factory=list)
    limits: list[DraftPrerequisite] = Field(default_factory=list)
    steps: list[DraftStep] = Field(default_factory=list)
    effort: DraftEstimate = Field(default_factory=lambda: DraftEstimate(known=False))
    waiting: DraftEstimate = Field(default_factory=lambda: DraftEstimate(known=False))
    costs: list[DraftCost] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class DraftScope(BaseModel):
    scope_string: str
    purpose: str = ""
    classification: str = ""
    claim_ids: list[str] = Field(default_factory=list)


class DraftBlocker(BaseModel):
    trigger: str
    consequence: str = ""
    resolution: str = ""
    owner: str = "you"
    claim_ids: list[str] = Field(default_factory=list)


class DraftConflict(BaseModel):
    topic: str
    description: str
    source_ids: list[str] = Field(default_factory=list)
    resolution: str = ""


class DraftUnknown(BaseModel):
    question: str
    why_it_matters: str = ""
    how_to_find_out: str = ""


class DraftSummaryStatement(BaseModel):
    text: str
    claim_ids: list[str] = Field(default_factory=list)


class DraftNextAction(BaseModel):
    instruction: str
    owner: str = "you"
    link_or_artifact: str = ""
    success_condition: str = ""
    claim_ids: list[str] = Field(default_factory=list)


class DraftClaim(BaseModel):
    id: str
    text: str
    conditions: str = ""
    source_ids: list[str] = Field(default_factory=list)
    attempt_ids: list[str] = Field(default_factory=list)
    basis: str = "unknown"
    excerpt: str = ""
    excerpt_status: str = "not_checked"
    review_result: str = "not_reviewed"
    support_note: str = ""


class DraftAgentAssistance(BaseModel):
    capability: str
    description: str = ""


class DraftGuideContent(BaseModel):
    """The model writes this; code adds Composio paths/steps and computes difficulty."""
    oauth_support: str = "unknown"
    summary: list[DraftSummaryStatement] = Field(default_factory=list)
    next_action: DraftNextAction = Field(
        default_factory=lambda: DraftNextAction(instruction="Research pending")
    )
    recommended_path_id: str = ""
    paths: list[DraftPath] = Field(default_factory=list)
    scopes: list[DraftScope] = Field(default_factory=list)
    blockers: list[DraftBlocker] = Field(default_factory=list)
    agent_assistance: list[DraftAgentAssistance] = Field(default_factory=list)
    unknowns: list[DraftUnknown] = Field(default_factory=list)
    conflicts: list[DraftConflict] = Field(default_factory=list)
    claims: list[DraftClaim] = Field(default_factory=list)


# ── Code-only models ──────────────────────────────────────────────────────

class Scenario(BaseModel):
    feature: str = ""
    audience: str = "external customers"
    deployment: str = "server-side"
    assumptions: list[str] = Field(default_factory=list)


class ProviderInfo(BaseModel):
    name: str
    official_domain: str = ""
    identity_status: str = "assumed"


class ComposioInfo(BaseModel):
    toolkit_slug: Optional[str] = None
    lookup_status: str = "not_attempted"
    selected_auth_method: Optional[str] = None
    managed_oauth: str = "unknown"
    required_config_fields: list[str] = Field(default_factory=list)
    required_connect_fields: list[str] = Field(default_factory=list)
    auth_guide_url: Optional[str] = None
    checked_at: Optional[str] = None
    source_id: Optional[str] = None


class Source(BaseModel):
    id: str
    url: str
    final_url: str = ""
    title: str = ""
    kind: str = "fetched_page"
    provider_domain: bool = False
    provider_domain_status: str = "unknown"
    retrieval_time: Optional[str] = None
    publication_date: Optional[str] = None
    retrieval_status: str = "search_snippet_only"
    excerpt: str = ""
    content_hash: str = ""


class Attempt(BaseModel):
    id: str
    actor: str = "assisted"
    provider: str = ""
    scenario: str = ""
    action: str = ""
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    environment: str = ""
    observed_result: str = ""
    status: str = "blocked"
    artifact_path: Optional[str] = None
    limitation: str = ""
    example: bool = False


class Estimate(BaseModel):
    """Persisted estimate: unknown has null bounds."""
    low: Optional[int] = None
    high: Optional[int] = None
    unit: str = "not_applicable"
    basis: str = "unknown"
    explanation: str = ""
    claim_ids: list[str] = Field(default_factory=list)


class DifficultyInfo(BaseModel):
    label: str = "Not enough evidence"
    reasons: list[str] = Field(default_factory=list)
    unknown_gates: list[str] = Field(default_factory=list)


class ReviewInfo(BaseModel):
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    audited_claim_ids: list[str] = Field(default_factory=list)
    based_on_version: Optional[int] = None


class RunStats(BaseModel):
    model_calls: int = 0
    searches: int = 0
    page_reads: int = 0
    fetch_attempts: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost: str = "unavailable"
    cost_basis: str = ""
    model: str = ""
    duration_seconds: float = 0.0
    research_timed_out: bool = False


class GuideRecord(BaseModel):
    """Complete immutable guide version."""
    schema_version: str = "2"
    prompt_version: str = "1"
    id: str = ""
    version: int = 0
    created_at: str = ""
    status: str = "draft"
    cache_key: str = ""

    provider: ProviderInfo = Field(default_factory=lambda: ProviderInfo(name=""))
    scenario: Scenario = Field(default_factory=Scenario)
    composio: ComposioInfo = Field(default_factory=ComposioInfo)

    guide: DraftGuideContent = Field(default_factory=DraftGuideContent)
    difficulty: dict[str, DifficultyInfo] = Field(default_factory=dict)

    sources: list[Source] = Field(default_factory=list)
    uncited_sources: list[Source] = Field(default_factory=list)
    attempts: list[Attempt] = Field(default_factory=list)
    review: ReviewInfo = Field(default_factory=ReviewInfo)
    warnings: list[str] = Field(default_factory=list)
    run: RunStats = Field(default_factory=RunStats)


def make_guide_id(provider_name: str, scenario: Scenario, official_domain: str = "") -> str:
    """Create a stable ID from provider + domain + scenario with a short hash."""
    key = (
        f"{provider_name.lower().strip()}|"
        f"{official_domain.lower().strip()}|"
        f"{scenario.feature.lower().strip()}|"
        f"{scenario.audience.lower().strip()}|"
        f"{scenario.deployment.lower().strip()}"
    )
    h = hashlib.sha256(key.encode()).hexdigest()[:8]
    slug = provider_name.lower().strip().replace(" ", "-")
    # keep only alphanumeric and hyphens
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return f"{slug}-{h}"


def make_cache_key(provider_name: str, official_domain: str, scenario: Scenario) -> str:
    """Build the full cache key."""
    parts = [
        provider_name.lower().strip(),
        official_domain.lower().strip(),
        scenario.feature.lower().strip(),
        scenario.audience.lower().strip(),
        scenario.deployment.lower().strip(),
        f"sv{SCHEMA_VERSION}",
        f"pv{PROMPT_VERSION}",
    ]
    return "|".join(parts)


from app.config import SCHEMA_VERSION, PROMPT_VERSION


def convert_draft_estimate(de: DraftEstimate) -> tuple[Estimate, list[str]]:
    """Convert a model-writable DraftEstimate to a persisted Estimate.

    If known=False, both bounds become None.
    Inverted bounds are removed with a warning returned.
    """
    warnings = []
    if not de.known:
        return Estimate(
            low=None, high=None, unit=de.unit, basis="unknown",
            explanation=de.explanation, claim_ids=de.claim_ids
        ), warnings

    low = de.low
    high = de.high

    if low is not None and low < 0:
        low = None
        warnings.append(f"Negative low bound removed from estimate: {de.explanation}")
    if high is not None and high < 0:
        high = None
        warnings.append(f"Negative high bound removed from estimate: {de.explanation}")

    if low is not None and high is not None and low > high:
        warnings.append(f"Inverted bounds ({low}>{high}) removed from estimate: {de.explanation}")
        low = None
        high = None

    return Estimate(
        low=low, high=high, unit=de.unit, basis=de.basis,
        explanation=de.explanation, claim_ids=de.claim_ids
    ), warnings
