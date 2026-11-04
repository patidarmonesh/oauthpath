"""Validation rules per §6.5 — run before anything is saved or shown."""

from __future__ import annotations

import re
import ipaddress
from urllib.parse import urlparse
from typing import Optional
from datetime import datetime, timezone

from thefuzz import fuzz

from app.schema import (
    GuideRecord, DraftGuideContent, DraftClaim, DraftGate, DraftPath,
    Source, Attempt, ComposioInfo, DifficultyInfo,
    GateKind, GateState, OAuthSupport, GuideStatus, ManagedOAuth,
    ComposioLookupStatus, AttemptStatus,
    ClaimBasis, ExcerptStatus, ReviewResult,
    DraftEstimate, Estimate, convert_draft_estimate,
    DraftStep, DraftBlocker, DraftScope, DraftSummaryStatement,
    DraftNextAction, DraftPrerequisite, DraftCost,
)

VALID_GATE_KINDS = {gk.value for gk in GateKind}
VALID_GATE_STATES = {"required", "not_required", "unknown"}
VALID_OWNERS = {"you", "agent", "agent_prepares_you_submit", "your_customer_admin", "provider"}
VALID_UNITS = {"minutes", "hours", "business_days", "calendar_days", "not_applicable"}
VALID_ESTIMATE_BASES = {"measured", "provider_estimate", "author_estimate", "anecdotal", "unknown"}
VALID_CLAIM_BASES = {"documented", "observed", "estimated", "unknown"}

CRITICAL_GATES = {
    GateKind.app_review.value,
    GateKind.security_assessment.value,
    GateKind.business_or_identity_verification.value,
    GateKind.partner_program.value,
    GateKind.paid_access.value,
    GateKind.customer_admin_action.value,
}

COORDINATION_GATES = (CRITICAL_GATES - {GateKind.security_assessment.value}) | {
    GateKind.per_customer_registration.value
}


def validate_url(url: str, allow_relative: bool = True, is_source: bool = False) -> tuple[bool, str]:
    """Validate a URL per §6.5 rule 5. Returns (valid, reason)."""
    if not url or not url.strip():
        return True, ""
    url = url.strip()

    lower = url.lower()
    for bad in ("javascript:", "data:", "file:", "ftp:", "vbscript:", "//"):
        if lower.startswith(bad):
            return False, "Rejected URL with dangerous scheme"

    is_http_source = False
    if lower.startswith("http://"):
        if is_source:
            is_http_source = True
        else:
            return False, "URL must be https"
    elif url.startswith("/"):
        if allow_relative:
            return True, ""
        return False, "Relative URLs are not permitted in model-written fields"
    elif not lower.startswith("https://"):
        return False, f"URL must be https: {url[:50]}"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, f"Cannot parse URL: {url[:50]}"

    if not parsed.hostname or parsed.hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False, f"URL has no valid hostname: {url[:50]}"

    # Reject raw IP addresses (IPv4 or IPv6)
    try:
        ipaddress.ip_address(parsed.hostname.strip("[]"))
        return False, f"Raw IP addresses are not permitted: {parsed.hostname}"
    except ValueError:
        pass

    # Require a dot in hostname
    if "." not in parsed.hostname or parsed.hostname.startswith(".") or parsed.hostname.endswith("."):
        return False, f"Hostname must contain a domain suffix: {parsed.hostname}"

    if parsed.username or parsed.password:
        return False, f"URL contains credentials: {url[:50]}"

    if re.search(r'[\s"\'\\<>]', url):
        return False, f"URL contains forbidden characters: {url[:50]}"

    if is_http_source:
        return True, "insecure_http"

    return True, ""


def normalize_fields(guide: GuideRecord) -> list[str]:
    """Normalize model fields to fixed enums and drop unexpected path IDs."""
    warnings = []

    # Filter paths: only own-app paths allowed from model
    valid_paths = []
    for path in guide.guide.paths:
        if path.id not in ("own_app_testing", "own_app_production", "composio_managed"):
            warnings.append(f"Dropped unknown path ID: {path.id}")
            continue
        valid_paths.append(path)
    guide.guide.paths = valid_paths

    # Normalize next action owner
    na = guide.guide.next_action
    if na.owner not in VALID_OWNERS:
        na.owner = "you"

    for path in guide.guide.paths:
        # Normalize gates
        kept_gates = []
        for g in path.gates:
            if g.kind not in VALID_GATE_KINDS:
                warnings.append(f"Dropped invalid gate kind: {g.kind}")
                continue
            if g.state not in VALID_GATE_STATES:
                g.state = "unknown"
            kept_gates.append(g)
        path.gates = kept_gates

        # Normalize steps
        for step in path.steps:
            if step.owner not in VALID_OWNERS:
                step.owner = "you"
            if step.effort.unit not in VALID_UNITS:
                step.effort.unit = "not_applicable"
            if step.effort.basis not in VALID_ESTIMATE_BASES:
                step.effort.basis = "unknown"

        for p in path.prerequisites:
            if p.owner not in VALID_OWNERS:
                p.owner = "you"

        for lim in path.limits:
            if lim.owner not in VALID_OWNERS:
                lim.owner = "you"

        for c in path.costs:
            if c.basis not in VALID_ESTIMATE_BASES:
                c.basis = "unknown"

        if path.effort.unit not in VALID_UNITS:
            path.effort.unit = "not_applicable"
        if path.effort.basis not in VALID_ESTIMATE_BASES:
            path.effort.basis = "unknown"
        if path.waiting.unit not in VALID_UNITS:
            path.waiting.unit = "not_applicable"
        if path.waiting.basis not in VALID_ESTIMATE_BASES:
            path.waiting.basis = "unknown"

    for b in guide.guide.blockers:
        if b.owner not in VALID_OWNERS:
            b.owner = "you"

    for c in guide.guide.claims:
        if c.basis not in VALID_CLAIM_BASES:
            c.basis = "unknown"

    return warnings


def validate_claims(guide: GuideRecord) -> list[str]:
    """Validate claims per §6.5 rule 1."""
    warnings = []
    claims = guide.guide.claims
    source_ids = {s.id for s in guide.sources}
    failed_source_ids = {s.id for s in guide.sources if s.retrieval_status == "failed"}
    attempt_ids = {a.id for a in guide.attempts}

    seen_ids = set()
    valid_claims = []
    for c in claims:
        if not c.id or c.id in seen_ids:
            warnings.append(f"Dropped duplicate or empty claim ID: {c.id!r}")
            continue
        seen_ids.add(c.id)
        valid_claims.append(c)

    for c in valid_claims:
        original_sources = c.source_ids[:]
        c.source_ids = [s for s in c.source_ids if s in source_ids and s not in failed_source_ids]
        if len(c.source_ids) != len(original_sources):
            removed = set(original_sources) - set(c.source_ids)
            warnings.append(f"Claim {c.id}: removed invalid source refs: {removed}")

        original_attempts = c.attempt_ids[:]
        c.attempt_ids = [a for a in c.attempt_ids if a in attempt_ids]
        if len(c.attempt_ids) != len(original_attempts):
            warnings.append(f"Claim {c.id}: removed invalid attempt refs")

        if c.basis == ClaimBasis.observed.value and not c.attempt_ids:
            if c.source_ids:
                c.basis = ClaimBasis.documented.value
                warnings.append(f"Claim {c.id}: 'observed' without attempt, downgraded to 'documented'")
            else:
                c.basis = ClaimBasis.unknown.value
                warnings.append(f"Claim {c.id}: 'observed' without attempt or sources, downgraded to 'unknown'")

        if c.basis == ClaimBasis.documented.value and not c.source_ids:
            c.basis = ClaimBasis.unknown.value
            warnings.append(f"Claim {c.id}: 'documented' without valid sources, downgraded to 'unknown'")

    guide.guide.claims = valid_claims
    return warnings


def validate_gate_evidence(guide: GuideRecord) -> list[str]:
    """A gate required or not_required without a documented or observed claim becomes unknown."""
    warnings = []
    claims_by_id = {c.id: c for c in guide.guide.claims}

    for path in guide.guide.paths:
        for gate in path.gates:
            if gate.state in ("required", "not_required"):
                has_doc_or_obs = any(
                    cid in claims_by_id
                    and claims_by_id[cid].basis in ("documented", "observed")
                    and claims_by_id[cid].review_result not in ("contradicted", "insufficient")
                    for cid in gate.claim_ids
                )
                if not has_doc_or_obs:
                    gate.state = "unknown"
                    warnings.append(
                        f"Gate '{gate.kind}' on path '{path.id}' lacks documented/observed supporting claim; downgraded to 'unknown'"
                    )

    return warnings


def validate_estimates_in_guide(guide: GuideRecord) -> list[str]:
    """Validate all estimates per §6.5 rule 4 & §5.6."""
    warnings = []
    attempt_ids = {a.id for a in guide.attempts}

    def _fix_estimate(de: DraftEstimate, ctx: str) -> list[str]:
        w = []
        if de.low is not None and de.low < 0:
            w.append(f"{ctx}: negative low bound removed")
            de.low = None
            de.known = False
        if de.high is not None and de.high < 0:
            w.append(f"{ctx}: negative high bound removed")
            de.high = None
            de.known = False
        if de.known and de.low is not None and de.high is not None and de.low > de.high:
            w.append(f"{ctx}: inverted bounds ({de.low}>{de.high}) removed")
            de.known = False
            de.low = None
            de.high = None

        is_zero_estimate = (
            de.known
            and de.low == 0
            and de.high == 0
            and de.basis in ("provider_estimate", "author_estimate")
            and bool(de.explanation and de.explanation.strip())
        )
        if not de.known or de.basis == "unknown" or ((de.low == 0 and de.high == 0) and not is_zero_estimate) or (de.low is None and de.high is None):
            de.known = False
            de.low = None
            de.high = None
            de.basis = "unknown"
            return w

        # 'measured' only when backed by an attempt record (directly or via cited claim)
        if de.basis == "measured":
            claims_by_id = {c.id: c for c in guide.guide.claims}
            has_attempt = any(
                (cid in attempt_ids) or
                (cid in claims_by_id and (
                    claims_by_id[cid].basis == "observed" or
                    any(aid in attempt_ids for aid in claims_by_id[cid].attempt_ids)
                ))
                for cid in de.claim_ids
            )
            if not has_attempt:
                de.basis = "author_estimate"
                w.append(f"{ctx}: 'measured' basis without attempt record downgraded to 'author_estimate'")

        return w

    for path in guide.guide.paths:
        warnings.extend(_fix_estimate(path.effort, f"Path {path.id} effort"))
        warnings.extend(_fix_estimate(path.waiting, f"Path {path.id} waiting"))
        for step in path.steps:
            warnings.extend(_fix_estimate(step.effort, f"Step {step.id} effort"))

    return warnings


def validate_urls_in_guide(guide: GuideRecord) -> list[str]:
    """Validate all URLs per §6.5 rule 5."""
    warnings = []

    # Source URLs
    for s in guide.sources:
        valid, reason = validate_url(s.url, allow_relative=False, is_source=True)
        if not valid:
            warnings.append(f"Source {s.id}: {reason}")
            s.url = ""
            s.retrieval_status = "failed"

    # Step action URLs: relative not permitted
    for path in guide.guide.paths:
        for step in path.steps:
            valid, reason = validate_url(step.action_url, allow_relative=False)
            if not valid:
                warnings.append(f"Step {step.id}: {reason}")
                step.action_url = ""

    # Next action link / artifact
    na = guide.guide.next_action
    if na.link_or_artifact:
        val = na.link_or_artifact.strip()
        is_url_like = bool(re.match(r'^(https?://|javascript:|ftp://|file://|//|www\.)', val, re.I) or "://" in val)
        if is_url_like:
            clean_val = val
            if clean_val.startswith("[invalid URL removed]"):
                clean_val = clean_val.replace("[invalid URL removed]", "").strip()
            valid, reason = validate_url(clean_val, allow_relative=False)
            if not valid:
                warnings.append(f"Next action link: {reason}")
                na.link_or_artifact = f"[invalid URL removed] {clean_val}"
            else:
                na.link_or_artifact = clean_val

    return warnings


def validate_oauth_support(guide: GuideRecord) -> list[str]:
    """Per §6.5 rule 6: if OAuth unsupported/unknown, remove own-app paths."""
    warnings = []
    oauth = guide.guide.oauth_support

    if oauth in (OAuthSupport.unsupported.value, OAuthSupport.unknown.value):
        own_paths = [p for p in guide.guide.paths if p.id.startswith("own_app")]
        if own_paths:
            guide.guide.paths = [p for p in guide.guide.paths if not p.id.startswith("own_app")]
            warnings.append(f"OAuth support is {oauth}: removed own-app paths")

    return warnings


def validate_composio(guide: GuideRecord) -> list[str]:
    """Per §6.5 rule 7 and §8.4: add Composio wiring/managed path, claims KC1-KC3, and sources C2-C3."""
    warnings = []
    ci = guide.composio

    # Remove any model-written composio paths
    guide.guide.paths = [p for p in guide.guide.paths if p.id != "composio_managed"]

    if ci.lookup_status != "matched" or not ci.toolkit_slug:
        return warnings

    oauth = guide.guide.oauth_support
    if oauth != OAuthSupport.confirmed.value:
        return warnings

    auth_method = ci.selected_auth_method or ""
    is_oauth = "oauth" in auth_method.lower()
    if not is_oauth:
        return warnings

    from app.config import COMPOSIO_CALLBACK_URL

    # Ensure claim KC1 exists
    if not any(c.id == "KC1" for c in guide.guide.claims):
        guide.guide.claims.append(DraftClaim(
            id="KC1",
            text=f"Composio toolkit `{ci.toolkit_slug}` supports {auth_method} with managed OAuth {ci.managed_oauth}",
            source_ids=[ci.source_id or "C1"],
            basis="documented" if ci.source_id else "unknown",
        ))

    # Add wiring step to own-app paths + Source C2 + Claim KC2
    if not any(s.id == "C2" for s in guide.sources):
        guide.sources.append(Source(
            id="C2",
            url="https://docs.composio.dev/docs/authentication/programmatic-auth-configs",
            title="Composio programmatic auth configs",
            kind="maintainer_reference",
            retrieval_status="reference_only",
            provider_domain=False,
            provider_domain_status="third_party",
            retrieval_time=None,
            publication_date=None,
        ))
    if not any(c.id == "KC2" for c in guide.guide.claims):
        guide.guide.claims.append(DraftClaim(
            id="KC2",
            text=f"Composio OAuth redirect URI is {COMPOSIO_CALLBACK_URL}",
            source_ids=["C2"],
            basis="documented",
        ))

    own_app_paths = [p for p in guide.guide.paths if p.id.startswith("own_app")]
    for idx, path in enumerate(own_app_paths):
        step_id = "composio_wiring" if idx == 0 else f"{path.id}_composio_wiring"
        if not any(s.id in (step_id, "composio_wiring") or s.id.endswith("_composio_wiring") for s in path.steps):
            path.steps.append(DraftStep(
                id=step_id,
                title="Connect through Composio",
                instructions=(
                    f"Create a Composio auth config for toolkit `{ci.toolkit_slug}` "
                    f"with your client ID and secret (custom auth, {auth_method}, "
                    f"oauth_redirect_uri = {COMPOSIO_CALLBACK_URL}). "
                    f"Create a Connect Link for a test user, complete consent, "
                    f"and confirm an ACTIVE connected account via the API."
                ),
                owner="agent_prepares_you_submit",
                success_condition="Connected account status is ACTIVE via the Composio API",
                effort=DraftEstimate(known=True, low=5, high=20, unit="minutes",
                                      basis="author_estimate",
                                      explanation="Assumes the provider app already exists"),
                claim_ids=["KC1", "KC2"],
            ))

    # Add managed path if available + Source C3 + Claim KC3
    if ci.managed_oauth == ManagedOAuth.available.value:
        if not any(s.id == "C3" for s in guide.sources):
            guide.sources.append(Source(
                id="C3",
                url="https://docs.composio.dev/docs/authentication/custom-app-vs-managed-app",
                title="Composio custom app vs managed app",
                kind="maintainer_reference",
                retrieval_status="reference_only",
                provider_domain=False,
                provider_domain_status="third_party",
                retrieval_time=None,
                publication_date=None,
            ))
        if not any(c.id == "KC3" for c in guide.guide.claims):
            guide.guide.claims.append(DraftClaim(
                id="KC3",
                text="Composio managed OAuth apps can remain in production when branding, default scopes, and quotas fit",
                source_ids=["C3"],
                basis="documented",
            ))

        managed_path = DraftPath(
            id="composio_managed",
            name="Composio-managed OAuth",
            applicability="When Composio's consent branding, default scopes, and shared quota fit your use case",
            gates=[],
            prerequisites=[
                DraftPrerequisite(text="A Composio account and API key", owner="you", claim_ids=["KC1"]),
            ],
            limits=[
                DraftPrerequisite(text="Composio's name on the consent screen", owner="you", claim_ids=["KC3"]),
                DraftPrerequisite(text="Composio's default scopes (may not cover your needs)", owner="you", claim_ids=["KC3"]),
                DraftPrerequisite(text="Quota shared across Composio users", owner="you", claim_ids=["KC3"]),
                DraftPrerequisite(
                    text="Trigger polling minimum (see Composio docs for current value)",
                    owner="you", claim_ids=["KC3"]
                ),
            ],
            steps=[
                DraftStep(
                    id="managed_connect",
                    title="Create managed auth config and connect",
                    instructions=(
                        f"Create a managed auth config for toolkit `{ci.toolkit_slug}`, "
                        f"connect a test user, and confirm ACTIVE status server-side. "
                        f"No registration wait for your own provider OAuth app."
                    ),
                    owner="agent_prepares_you_submit",
                    success_condition="Connected account status is ACTIVE",
                    effort=DraftEstimate(known=True, low=5, high=15, unit="minutes",
                                          basis="author_estimate",
                                          explanation="Assumes eligible Composio account"),
                    claim_ids=["KC1", "KC3"],
                ),
            ],
            effort=DraftEstimate(known=True, low=5, high=15, unit="minutes",
                                  basis="author_estimate",
                                  explanation="Assumes eligible Composio account"),
            waiting=DraftEstimate(known=False),
            claim_ids=["KC1", "KC3"],
        )
        guide.guide.paths.insert(0, managed_path)
        warnings.append("Added Composio-managed path based on confirmed toolkit metadata")

    return warnings


def compute_difficulty(guide: GuideRecord) -> list[str]:
    """Per §5.5: compute difficulty per path and overall."""
    warnings = []
    difficulty = {}

    for path in guide.guide.paths:
        gate_map = {g.kind: g.state for g in path.gates}

        has_security_assessment = gate_map.get(GateKind.security_assessment.value) == GateState.required.value
        required_coord = sum(
            1 for gk in COORDINATION_GATES
            if gate_map.get(gk) == GateState.required.value
        )
        # Count only gates explicitly present in path.gates and marked unknown
        unknown_critical = [
            g.kind for g in path.gates
            if g.kind in CRITICAL_GATES and g.state == GateState.unknown.value
        ]

        reasons = []
        for g in path.gates:
            if g.state == GateState.required.value:
                reasons.append(f"Required: {g.kind.replace('_', ' ').title()}")
        for gk in unknown_critical:
            reasons.append(f"Not established: {gk.replace('_', ' ').title()}")

        if has_security_assessment or required_coord >= 2:
            base_label = "Substantial external requirements"
        elif required_coord == 1:
            base_label = "Requires coordination"
        else:
            base_label = "Straightforward"

        if unknown_critical:
            if required_coord == 0 and not has_security_assessment:
                label = "Not enough evidence"
            else:
                label = f"{base_label} (provisional)"
        else:
            label = base_label

        difficulty[path.id] = DifficultyInfo(
            label=label,
            reasons=reasons,
            unknown_gates=unknown_critical,
        )

    # Overall: use production path if present, otherwise testing or first path
    prod_path = next((p for p in guide.guide.paths if "production" in p.id), None)
    if prod_path and prod_path.id in difficulty:
        difficulty["overall"] = DifficultyInfo(
            label=difficulty[prod_path.id].label,
            reasons=difficulty[prod_path.id].reasons + [f"Based on production path: {prod_path.id}"],
            unknown_gates=difficulty[prod_path.id].unknown_gates,
        )
    elif guide.guide.paths:
        first = guide.guide.paths[0]
        if first.id in difficulty:
            difficulty["overall"] = DifficultyInfo(
                label=difficulty[first.id].label,
                reasons=difficulty[first.id].reasons + [f"Based on path: {first.id}"],
                unknown_gates=difficulty[first.id].unknown_gates,
            )

    if not difficulty:
        difficulty["overall"] = DifficultyInfo(
            label="Not enough evidence",
            reasons=["No applicable paths established"],
            unknown_gates=list(CRITICAL_GATES),
        )

    guide.difficulty = difficulty
    return warnings


def add_missing_critical_gates(guide: GuideRecord) -> list[str]:
    """Per §6.5 rule 3: production paths get missing critical gates as unknown."""
    warnings = []
    for path in guide.guide.paths:
        if "production" in path.id or path.id == "own_app_production":
            existing = {g.kind for g in path.gates}
            for gk in CRITICAL_GATES:
                if gk not in existing:
                    path.gates.append(DraftGate(
                        kind=gk, state="unknown",
                        note="Not established by research",
                    ))
                    warnings.append(f"Path {path.id}: added unknown gate '{gk}'")
    return warnings


def check_excerpts(guide: GuideRecord, page_texts: dict[str, str] | None = None) -> list[str]:
    """Per §6.5 rule 8: fuzzy excerpt check."""
    warnings = []
    if not page_texts:
        return warnings

    source_map = {s.id: s for s in guide.sources}

    for claim in guide.guide.claims:
        if not claim.excerpt:
            continue
        if not claim.source_ids:
            continue

        first_source_id = claim.source_ids[0]
        source = source_map.get(first_source_id)
        if not source or source.retrieval_status != "read":
            continue

        page_text = page_texts.get(first_source_id, "")
        if not page_text:
            continue

        if not isinstance(page_text, str):
            if hasattr(page_text, "source") and hasattr(page_text.source, "data"):
                page_text = str(page_text.source.data)
            else:
                page_text = str(page_text)

        ratio = fuzz.partial_ratio(
            claim.excerpt.lower().strip(),
            page_text.lower().strip()
        )
        if ratio >= 90:
            claim.excerpt_status = "found"
        else:
            claim.excerpt_status = "not_found"
            warnings.append(f"Claim {claim.id}: excerpt not found in source {first_source_id} (ratio={ratio})")

    return warnings


def determine_status(guide: GuideRecord) -> list[str]:
    """Per §6.5 rule 9: determine guide status."""
    warnings = []

    if guide.status == GuideStatus.reviewed.value:
        if not guide.review.reviewer:
            guide.status = GuideStatus.draft.value
            warnings.append("Guide marked reviewed without reviewer; reverted to draft")
            return warnings
        return warnings

    is_partial = False
    reasons = []

    if guide.run.research_timed_out:
        is_partial = True
        reasons.append("Research hit time budget")

    if guide.guide.oauth_support == OAuthSupport.unknown.value:
        is_partial = True
        reasons.append("OAuth support is unknown")

    if guide.provider.identity_status == "assumed":
        is_partial = True
        reasons.append("Provider identity is uncorroborated")

    # Summary statement not backed by documented or observed claim -> partial
    claims_by_id = {c.id: c for c in guide.guide.claims}
    for stmt in guide.guide.summary:
        has_doc_or_obs = any(
            cid in claims_by_id and claims_by_id[cid].basis in ("documented", "observed")
            for cid in stmt.claim_ids
        )
        if not has_doc_or_obs:
            is_partial = True
            reasons.append(f"Summary statement lacks documented/observed claim backing: '{stmt.text[:50]}'")
            break

    # Core OAuth availability unknown -> partial (only when OAuth availability itself is in doubt)
    for u in guide.guide.unknowns:
        q_lower = u.question.lower()
        if any(kw in q_lower for kw in ["whether oauth is supported", "whether oauth is available", "is oauth supported", "is oauth available"]):
            is_partial = True
            reasons.append(f"Core OAuth availability unknown: '{u.question[:50]}'")
            break

    if is_partial:
        guide.status = GuideStatus.partial.value
        for r in reasons:
            warnings.append(f"Status partial: {r}")
    else:
        guide.status = GuideStatus.draft.value

    return warnings


def validate_references(guide: GuideRecord) -> list[str]:
    """Per §6.5 rule 2: validate that references to claims are valid.

    Prune invalid references, then qualify dependent assertions.
    """
    warnings = []
    valid_claim_ids = {c.id for c in guide.guide.claims}
    unknown_claim_ids = {
        c.id for c in guide.guide.claims
        if c.basis == "unknown" or c.review_result in ("contradicted", "insufficient")
    }

    def _prune(claim_ids: list[str], context: str) -> list[str]:
        pruned = [cid for cid in claim_ids if cid in valid_claim_ids]
        removed = set(claim_ids) - set(pruned)
        if removed:
            warnings.append(f"{context}: removed invalid claim refs: {removed}")
        return pruned

    def _is_unsupported(claim_ids: list[str]) -> bool:
        return not claim_ids or all(cid in unknown_claim_ids for cid in claim_ids)

    # 1. Summary statements
    for i, stmt in enumerate(guide.guide.summary):
        stmt.claim_ids = _prune(stmt.claim_ids, f"Summary[{i}]")
        if _is_unsupported(stmt.claim_ids):
            warnings.append(
                f"Summary[{i}]: all supporting claims are 'unknown' — assertion is weakly supported (qualifying assertion)"
            )
            if not stmt.text.startswith("Unconfirmed:"):
                stmt.text = f"Unconfirmed: {stmt.text}"

    # 2. Next action
    na = guide.guide.next_action
    na.claim_ids = _prune(na.claim_ids, "Next action")
    na_unsupported = _is_unsupported(na.claim_ids)

    # Check if recommended path has unknown critical gates
    rec_path = next((p for p in guide.guide.paths if p.id == guide.guide.recommended_path_id), None)
    has_unknown_gates = False
    if rec_path:
        has_unknown_gates = any(g.state == "unknown" for g in rec_path.gates)

    # Patterns indicating premature launch or skipping review
    unconditional_launch_patterns = (
        "launch without review",
        "launch publicly without",
        "no approval needed",
        "no approval is needed",
        "no review required",
        "go live without",
        "deploy to production without",
        "ready for production",
    )
    is_launch_advice = any(pat in na.instruction.lower() for pat in unconditional_launch_patterns)

    if (na_unsupported or has_unknown_gates) and is_launch_advice:
        warnings.append("Next action: unsupported launch advice replaced with evidence-gathering action")
        na.instruction = "Verify provider OAuth and review requirements before public launch"
        na.owner = "you"
        na.success_condition = "Official review requirements and approval timeline confirmed from provider documentation"
        na.link_or_artifact = ""
    elif na_unsupported:
        warnings.append("Next action: supporting evidence is unconfirmed — qualifying action")
        if not na.instruction.lower().startswith("verify") and not na.instruction.startswith("Unconfirmed:"):
            na.instruction = f"Verify requirement: {na.instruction}"

    # 3. Paths, gates, steps, prereqs, limits, costs, scopes, blockers
    for path in guide.guide.paths:
        path.claim_ids = _prune(path.claim_ids, f"Path {path.id}")
        for gate in path.gates:
            gate.claim_ids = _prune(gate.claim_ids, f"Gate {gate.kind}")
            if _is_unsupported(gate.claim_ids):
                warnings.append(
                    f"Gate {gate.kind}: all supporting claims are 'unknown' — assertion is weakly supported"
                )
        path_has_unknown_gates = any(g.state == "unknown" for g in path.gates)
        for step in path.steps:
            step.claim_ids = _prune(step.claim_ids, f"Step {step.id}")
            step_unsupported = _is_unsupported(step.claim_ids)
            step_text = f"{step.title} {step.instructions}".lower()
            is_prep = any(term in step_text for term in ("prepare", "draft", "gather", "create test", "test app", "configure", "submit for review", "request review", "materials", "review package", "documentation"))
            is_unconditional_launch = any(pat in step_text for pat in unconditional_launch_patterns)
            is_step_launch = is_unconditional_launch or (
                not is_prep and any(term in step_text for term in ("publish", "launch", "production", "go live"))
            )
            if step_unsupported and (is_unconditional_launch or is_step_launch):
                if step.title != "Verify provider approval requirements":
                    warnings.append(f"Step {step.id}: unsupported launch assertion qualified")
                    step.title = "Verify provider approval requirements"
                    step.instructions = "Verify whether provider approval or security review is required before publishing (unconfirmed)"
                    step.action_url = ""
                    step.success_condition = "Provider approval requirements and gates are verified"
            elif path_has_unknown_gates and is_step_launch and not is_prep:
                if not step.title.startswith("Verify requirement:"):
                    step.title = f"Verify requirement: {step.title}"
                if not step.instructions.startswith("Verify requirement:"):
                    step.instructions = f"Verify requirement: {step.instructions}"
                step.action_url = ""
            elif step_unsupported and not is_prep:
                if not step.instructions.startswith("[Unconfirmed]") and not step.instructions.lower().startswith("verify"):
                    warnings.append(f"Step {step.id}: evidence is unconfirmed — qualifying step")
                    step.instructions = f"[Unconfirmed] {step.instructions}"

        for prereq in path.prerequisites:
            prereq.claim_ids = _prune(prereq.claim_ids, f"Prereq in {path.id}")
            if _is_unsupported(prereq.claim_ids):
                prereq_text = prereq.text.lower()
                is_prereq_launch = any(pat in prereq_text for pat in unconditional_launch_patterns)
                if is_prereq_launch:
                    prereq.text = "Verify provider approval requirements before publishing (unconfirmed)"
                elif not prereq.text.startswith("[Unconfirmed]") and not prereq.text.lower().startswith("verify"):
                    prereq.text = f"[Unconfirmed] {prereq.text}"
        for lim in path.limits:
            lim.claim_ids = _prune(lim.claim_ids, f"Limit in {path.id}")
        for cost in path.costs:
            cost.claim_ids = _prune(cost.claim_ids, f"Cost in {path.id}")
        path.effort.claim_ids = _prune(path.effort.claim_ids, f"Path {path.id} effort")
        path.waiting.claim_ids = _prune(path.waiting.claim_ids, f"Path {path.id} waiting")
        for step in path.steps:
            step.effort.claim_ids = _prune(step.effort.claim_ids, f"Step {step.id} effort")

    for scope in guide.guide.scopes:
        scope.claim_ids = _prune(scope.claim_ids, f"Scope {scope.scope_string}")

    for blocker in guide.guide.blockers:
        blocker.claim_ids = _prune(blocker.claim_ids, f"Blocker {blocker.trigger[:30]}")

    return warnings


def split_uncited_sources(guide: GuideRecord) -> list[str]:
    """Per §6.4: move sources not cited by any claim to uncited_sources."""
    warnings = []
    cited_source_ids = set()
    for claim in guide.guide.claims:
        cited_source_ids.update(claim.source_ids)

    if guide.composio.source_id:
        cited_source_ids.add(guide.composio.source_id)

    cited = []
    uncited = []
    for source in guide.sources:
        if source.id in cited_source_ids:
            cited.append(source)
        else:
            uncited.append(source)

    if uncited:
        guide.uncited_sources = uncited
        guide.sources = cited
        warnings.append(
            f"Moved {len(uncited)} uncited source(s) to uncited_sources"
        )

    return warnings


def check_review_integrity(guide: GuideRecord) -> list[str]:
    """Per §6.5 rule 10: review integrity checks."""
    warnings = []
    audited_ids = set(guide.review.audited_claim_ids)

    for claim in guide.guide.claims:
        if claim.id in audited_ids and claim.review_result == "not_reviewed":
            warnings.append(
                f"Claim {claim.id}: listed as audited but review_result is still 'not_reviewed'"
            )

    return warnings


def validate_guide(guide: GuideRecord, page_texts: dict[str, str] | None = None) -> list[str]:
    """Run all validation rules. Returns warnings."""
    all_warnings = []

    all_warnings.extend(normalize_fields(guide))
    all_warnings.extend(validate_urls_in_guide(guide))
    all_warnings.extend(validate_claims(guide))
    all_warnings.extend(validate_oauth_support(guide))
    all_warnings.extend(add_missing_critical_gates(guide))
    all_warnings.extend(validate_composio(guide))
    all_warnings.extend(validate_gate_evidence(guide))
    all_warnings.extend(validate_references(guide))
    all_warnings.extend(validate_estimates_in_guide(guide))
    all_warnings.extend(compute_difficulty(guide))
    all_warnings.extend(check_excerpts(guide, page_texts))
    all_warnings.extend(split_uncited_sources(guide))
    all_warnings.extend(check_review_integrity(guide))
    all_warnings.extend(determine_status(guide))

    guide.warnings = all_warnings
    return all_warnings


def validate_reviewed_record(guide: GuideRecord) -> tuple[bool, str]:
    """Validate referential, schema, and audit integrity for a reviewed record.

    Used at ingestion boundaries (seed import and promote).
    Returns (is_valid, error_message).
    """
    # 1. Enforce enum schemas
    valid_oauth_support = {e.value for e in OAuthSupport}
    if guide.guide.oauth_support not in valid_oauth_support:
        return False, f"Invalid oauth_support: {guide.guide.oauth_support!r}; must be one of {valid_oauth_support}"

    valid_lookup_status = {e.value for e in ComposioLookupStatus}
    if guide.composio.lookup_status not in valid_lookup_status:
        return False, f"Invalid Composio lookup_status: {guide.composio.lookup_status!r}; must be one of {valid_lookup_status}"

    valid_managed_oauth = {e.value for e in ManagedOAuth}
    if guide.composio.managed_oauth not in valid_managed_oauth:
        return False, f"Invalid managed_oauth: {guide.composio.managed_oauth!r}; must be one of {valid_managed_oauth}"

    valid_guide_status = {e.value for e in GuideStatus}
    if guide.status not in valid_guide_status:
        return False, f"Invalid guide status: {guide.status!r}; must be one of {valid_guide_status}"

    valid_attempt_statuses = {e.value for e in AttemptStatus}
    for att in guide.attempts:
        if att.status not in valid_attempt_statuses:
            return False, f"Attempt {att.id} has invalid status {att.status!r}; must be one of {valid_attempt_statuses}"

    # 2. Check reviewed status requirements
    if guide.status == GuideStatus.reviewed.value:
        if not guide.review or not guide.review.reviewer or not guide.review.reviewer.strip():
            return False, "Reviewed guide must specify a non-empty reviewer"
        if not guide.review.reviewed_at or not guide.review.reviewed_at.strip():
            return False, "Reviewed guide must specify a reviewed_at timestamp"
        if not guide.review.audited_claim_ids:
            return False, "Reviewed guide must have non-empty audited_claim_ids"

        existing_claim_map = {c.id: c for c in guide.guide.claims}
        for cid in guide.review.audited_claim_ids:
            if cid not in existing_claim_map:
                return False, f"audited_claim_ids contains nonexistent claim ID: {cid!r}"
            c = existing_claim_map[cid]
            if c.review_result == "not_reviewed" or c.review_result not in ("supported", "contradicted", "insufficient"):
                return False, f"Audited claim {cid} review_result is invalid or still 'not_reviewed': {c.review_result!r}"
            if not c.support_note or not c.support_note.strip():
                return False, f"Audited claim {cid} must have a non-empty support_note"

    # 3. Duplicate ID checks
    seen_sids = set()
    for s in guide.sources:
        if s.id in seen_sids:
            return False, f"Duplicate source ID: {s.id!r}"
        seen_sids.add(s.id)

    seen_aids = set()
    for a in guide.attempts:
        if a.id in seen_aids:
            return False, f"Duplicate attempt ID: {a.id!r}"
        seen_aids.add(a.id)
        if getattr(a, "example", False) or a.status == "example" or str(a.id).startswith("EXAMPLE-") or (a.limitation and "[SYNTHETIC" in a.limitation):
            return False, f"Attempt {a.id!r} is a synthetic example record and cannot be included in a reviewed guide"

    seen_cids = set()
    for c in guide.guide.claims:
        if c.id in seen_cids:
            return False, f"Duplicate claim ID: {c.id!r}"
        seen_cids.add(c.id)

    seen_steps = set()
    for p in guide.guide.paths:
        for s in p.steps:
            if s.id in seen_steps:
                return False, f"Duplicate step ID: {s.id!r}"
            seen_steps.add(s.id)

    # 4. Source URL and state validity
    for s in guide.sources:
        valid, reason = validate_url(s.url, allow_relative=False)
        if not valid:
            return False, f"Source {s.id} has invalid URL {s.url!r}: {reason}"

    # 5. Referential integrity and audit consistency
    source_ids = {s.id for s in guide.sources}
    attempt_ids = {a.id for a in guide.attempts}
    sources_by_id = {s.id: s for s in guide.sources}
    attempts_by_id = {a.id: a for a in guide.attempts}

    for claim in guide.guide.claims:
        if claim.basis == "documented" and not claim.source_ids:
            return False, f"Claim {claim.id} has 'documented' basis but no valid source references"
        if claim.basis == "observed" and not claim.attempt_ids:
            return False, f"Claim {claim.id} has 'observed' basis but no valid attempt references"
        for sid in claim.source_ids:
            if sid not in source_ids:
                return False, f"Claim {claim.id} references nonexistent source ID: {sid!r}"
        for aid in claim.attempt_ids:
            if aid not in attempt_ids:
                return False, f"Claim {claim.id} references nonexistent attempt ID: {aid!r}"
            att = attempts_by_id.get(aid)
            if att and (getattr(att, "example", False) or att.status == "example" or str(att.id).startswith("EXAMPLE-") or (att.limitation and "[SYNTHETIC" in att.limitation)):
                return False, f"Claim {claim.id} cites synthetic example attempt: {aid!r}"

    if guide.status == GuideStatus.reviewed.value and guide.review:
        existing_claim_map = {c.id: c for c in guide.guide.claims}
        for cid in guide.review.audited_claim_ids:
            c = existing_claim_map[cid]
            if c.review_result == "supported":
                if c.basis == "unknown":
                    return False, f"Audited claim {cid} is marked 'supported' but has 'unknown' basis"
                if c.basis == "documented":
                    if not c.source_ids:
                        return False, f"Audited claim {cid} is marked 'supported' with 'documented' basis but has no sources"
                    for sid in c.source_ids:
                        src = sources_by_id.get(sid)
                        if not src or src.retrieval_status == "failed":
                            return False, f"Audited claim {cid} is marked 'supported' but cites failed or missing source {sid!r}"
                elif c.basis == "observed":
                    if not c.attempt_ids:
                        return False, f"Audited claim {cid} is marked 'supported' with 'observed' basis but has no attempts"
                    for aid in c.attempt_ids:
                        att = attempts_by_id.get(aid)
                        if not att:
                            return False, f"Audited claim {cid} is marked 'supported' but cites missing attempt {aid!r}"
                        if getattr(att, "example", False) or att.status == "example" or str(att.id).startswith("EXAMPLE-") or (att.limitation and "[SYNTHETIC" in att.limitation):
                            return False, f"Audited claim {cid} cites synthetic example attempt {aid!r}"
                        if att.status == "succeeded":
                            pass
                        elif att.status in ("blocked", "failed"):
                            # Blocked or failed attempts may validly support blocker or failure descriptions,
                            # but cannot support claims asserting successful connection or lack of review.
                            c_text_lower = c.text.lower()
                            unsupportable_by_failure = (
                                "no approval needed",
                                "no approval is needed",
                                "no review needed",
                                "no review required",
                                "review not required",
                                "successfully connected",
                                "connection succeeded",
                                "setup succeeded",
                                "instant activation",
                                "ready for production",
                                "no admin approval",
                            )
                            if any(pat in c_text_lower for pat in unsupportable_by_failure):
                                return False, f"Audited claim {cid} asserts successful connection or lack of review, but cites {att.status} attempt {aid!r}"
                        else:
                            return False, f"Audited claim {cid} cites attempt {aid!r} with unsupported status {att.status!r}"

        # Organizational review gates cannot be marked not_required solely by setup attempts
        for path in guide.guide.paths:
            for gate in path.gates:
                if gate.kind in ("app_review", "security_assessment", "customer_admin_action", "business_or_identity_verification"):
                    if gate.state == "not_required":
                        for cid in gate.claim_ids:
                            claim = existing_claim_map.get(cid)
                            if claim and claim.attempt_ids and not claim.source_ids:
                                return False, f"Gate '{gate.kind}' cannot be marked 'not_required' based solely on setup attempt {claim.attempt_ids}"

        # 6. Non-mutating verification: ensure normalization would not alter audited content
        import copy
        guide_copy = copy.deepcopy(guide)
        validate_guide(guide_copy)
        for cid in guide.review.audited_claim_ids:
            orig_c = existing_claim_map[cid]
            norm_c = next((c for c in guide_copy.guide.claims if c.id == cid), None)
            if not norm_c:
                return False, f"Audited claim {cid} would be removed during normalization"
            if orig_c.review_result == "supported" and norm_c.basis == "unknown":
                return False, f"Audited claim {cid} evidence is inconsistent: normalization would downgrade basis to unknown"
            if orig_c.source_ids != norm_c.source_ids:
                return False, f"Audited claim {cid} sources would be mutated during normalization"

    return True, ""
