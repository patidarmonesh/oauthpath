"""Render — Markdown export with the same content as the HTML page per §6.3."""

from __future__ import annotations

from app.schema import GuideRecord, Estimate, DifficultyInfo

OWNER_LABELS = {
    "you": "You",
    "agent": "Agent",
    "agent_prepares_you_submit": "Agent prepares, you submit",
    "your_customer_admin": "Your customer's admin",
    "provider": "Provider",
}


def format_owner(owner: str) -> str:
    """Format an owner string with canonical display label."""
    return OWNER_LABELS.get(owner, owner.replace("_", " ").title())


def escape_cell(text: str) -> str:
    """Escape vertical pipe characters for Markdown table cells."""
    if not text:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ")


def format_estimate(est) -> str:
    """Format a DraftEstimate or Estimate for display."""
    if not est:
        return "unknown"

    explanation = getattr(est, "explanation", "")
    if hasattr(est, "known") and not est.known:
        return f"unknown — {explanation}" if explanation else "unknown"

    low = getattr(est, "low", None)
    high = getattr(est, "high", None)
    basis = getattr(est, "basis", "")

    # Real zero estimate with provider or author explanation: display as "none"
    if low == 0 and high == 0 and basis in ("provider_estimate", "author_estimate"):
        return f"none — {explanation}" if explanation else "none"

    # Unknown when both bounds are None or 0
    if (low is None or low == 0) and (high is None or high == 0):
        return f"unknown — {explanation}" if explanation else "unknown"

    if basis == "unknown":
        return f"unknown — {explanation}" if explanation else "unknown"

    parts = []
    if low and high:
        if low == high:
            parts.append(f"{low}")
        else:
            parts.append(f"{low}–{high}")
    elif low:
        parts.append(f"~{low}")
    elif high:
        parts.append(f"up to {high}")

    unit = getattr(est, "unit", "")
    if unit and unit != "not_applicable":
        parts.append(unit.replace("_", " "))

    if basis:
        parts.append(f"({basis.replace('_', ' ')})")

    explanation = getattr(est, "explanation", "")
    if explanation:
        parts.append(f"— {explanation}")

    return " ".join(parts) if parts else "unknown"


def render_markdown(guide: GuideRecord) -> str:
    """Render a guide record as complete Markdown."""
    g = guide.guide
    lines = []

    # Title and assumptions
    lines.append(f"# {guide.provider.name} — {guide.scenario.feature or 'OAuth integration guide'}")
    lines.append("")
    lines.append(
        f"v{guide.version} · {guide.status} · "
        f"Researched {guide.created_at[:10] if guide.created_at else 'pending'}"
    )

    assumptions = [
        f"Audience: {guide.scenario.audience}",
        f"Deployment: {guide.scenario.deployment}",
    ]
    if guide.scenario.feature:
        assumptions.insert(0, f"Feature: {guide.scenario.feature}")
    if guide.scenario.assumptions:
        assumptions.extend(guide.scenario.assumptions)
    lines.append(f"Assumptions: {' · '.join(assumptions)}")
    lines.append("")

    # Evidence line
    basis_counts = {"documented": 0, "observed": 0, "estimated": 0, "unknown": 0}
    for claim in g.claims:
        basis_counts[claim.basis] = basis_counts.get(claim.basis, 0) + 1
    sources_read = sum(1 for s in guide.sources if s.retrieval_status == "read")

    evidence_parts = [
        f"{basis_counts['documented']} documented",
        f"{basis_counts['observed']} observed",
        f"{basis_counts['estimated']} estimated",
        f"{basis_counts['unknown']} unknown",
        f"{sources_read} sources read",
    ]
    if guide.review.reviewer:
        evidence_parts.append(
            f"Reviewed by {guide.review.reviewer} on {guide.review.reviewed_at}"
        )
    else:
        evidence_parts.append("not human-reviewed")
    lines.append(f"Evidence: {' · '.join(evidence_parts)}")
    lines.append("")

    # Composio status
    ci = guide.composio
    if ci.lookup_status == "matched" and ci.toolkit_slug:
        managed_str = ci.managed_oauth
        lines.append(
            f"Composio: toolkit `{ci.toolkit_slug}` ({ci.selected_auth_method or 'unknown method'}); "
            f"managed OAuth {managed_str}"
        )
    elif ci.lookup_status == "no_match":
        lines.append("Composio: no matching toolkit found by search — availability unknown")
    elif ci.lookup_status == "failed":
        lines.append("Composio: lookup failed — unknown, which is not the same as unavailable")
    elif ci.lookup_status == "not_attempted":
        lines.append("Composio: lookup not attempted on this server")
    lines.append("")

    # Readiness summary
    lines.append("## Readiness summary")
    lines.append("")
    for stmt in g.summary:
        claim_refs = ", ".join(stmt.claim_ids) if stmt.claim_ids else ""
        lines.append(f"- {stmt.text}" + (f" [{claim_refs}]" if claim_refs else ""))
    lines.append("")

    # Overall difficulty
    overall_diff = guide.difficulty.get("overall")
    if overall_diff:
        lines.append(f"**Difficulty:** {overall_diff.label}")
        for r in overall_diff.reasons:
            lines.append(f"  - {r}")
        lines.append("")

    # Next action
    lines.append("## Next action")
    lines.append("")
    na = g.next_action
    lines.append(f"**{na.instruction}**")
    lines.append(f"Owner: {format_owner(na.owner)}")
    if na.link_or_artifact:
        val = na.link_or_artifact.strip()
        if val.startswith("https://") and not val.startswith("[invalid"):
            lines.append(f"Link: [{val}]({val})")
        else:
            lines.append(f"Link/Artifact: {val}")
    if na.success_condition:
        lines.append(f"Done when: {na.success_condition}")
    if na.claim_ids:
        lines.append(f"Evidence: [{', '.join(na.claim_ids)}]")
    lines.append("")

    # Paths
    if g.paths:
        lines.append("## Paths")
        lines.append("")

        # Summary table
        lines.append("| Path | Difficulty | Setup effort | Waiting |")
        lines.append("|---|---|---|---|")
        for path in g.paths:
            diff = guide.difficulty.get(path.id)
            diff_label = diff.label if diff else "Unknown"
            effort_str = format_estimate(path.effort)
            waiting_str = format_estimate(path.waiting)
            lines.append(f"| {escape_cell(path.name)} | {escape_cell(diff_label)} | {escape_cell(effort_str)} | {escape_cell(waiting_str)} |")
        lines.append("")

        # Path details
        for path in g.paths:
            lines.append(f"### {path.name}")
            lines.append("")
            if path.applicability:
                lines.append(f"*{path.applicability}*")
                lines.append("")

            # Gates
            if path.gates:
                lines.append("**Gates:**")
                for gate in path.gates:
                    state_str = gate.state.replace("_", " ").title()
                    c_ref = f" [{', '.join(gate.claim_ids)}]" if gate.claim_ids else ""
                    lines.append(f"- {gate.kind.replace('_', ' ').title()}: {state_str}{c_ref}")
                    if gate.note:
                        lines.append(f"  {gate.note}")
                lines.append("")

            # Prerequisites
            if path.prerequisites:
                lines.append("**Prerequisites:**")
                for p in path.prerequisites:
                    c_ref = f" [{', '.join(p.claim_ids)}]" if p.claim_ids else ""
                    lines.append(f"- {p.text} (Owner: {format_owner(p.owner)}){c_ref}")
                lines.append("")

            # Limits
            if path.limits:
                lines.append("**Limits:**")
                for lim in path.limits:
                    c_ref = f" [{', '.join(lim.claim_ids)}]" if lim.claim_ids else ""
                    lines.append(f"- {lim.text}{c_ref}")
                lines.append("")

            # Steps
            if path.steps:
                lines.append("**Steps:**")
                for i, step in enumerate(path.steps, 1):
                    owner = format_owner(step.owner)
                    c_ref = f" [{', '.join(step.claim_ids)}]" if step.claim_ids else ""
                    lines.append(f"{i}. **{step.title}** [{owner}]{c_ref}")
                    lines.append(f"   {step.instructions}")
                    if step.success_condition:
                        lines.append(f"   *Done when: {step.success_condition}*")
                    effort_disp = format_estimate(step.effort)
                    if effort_disp != "unknown":
                        lines.append(f"   Effort: {effort_disp}")
                    if step.action_url:
                        lines.append(f"   Link: {step.action_url}")
                lines.append("")

            # Costs
            if path.costs:
                lines.append("**Costs:**")
                for cost in path.costs:
                    c_ref = f" [{', '.join(cost.claim_ids)}]" if cost.claim_ids else ""
                    lines.append(f"- {cost.description}: {cost.amount or 'unknown'} ({cost.basis}){c_ref}")
                lines.append("")

    # Scopes
    if g.scopes:
        lines.append("## Scopes")
        lines.append("")
        lines.append("| Scope | Purpose | Classification | Claims |")
        lines.append("|---|---|---|---|")
        for scope in g.scopes:
            c_ref = ", ".join(scope.claim_ids) if scope.claim_ids else "—"
            lines.append(f"| `{escape_cell(scope.scope_string)}` | {escape_cell(scope.purpose)} | {escape_cell(scope.classification)} | {escape_cell(c_ref)} |")
        lines.append("")

    # Blockers
    lines.append("## Launch blockers")
    lines.append("")
    if g.blockers:
        for b in g.blockers:
            c_ref = f" [{', '.join(b.claim_ids)}]" if b.claim_ids else ""
            lines.append(f"- **{b.trigger}**: {b.consequence}{c_ref}")
            lines.append(f"  Resolution: {b.resolution} (Owner: {format_owner(b.owner)})")
    else:
        lines.append("None recorded.")
    lines.append("")

    # Setup attempts
    if guide.attempts:
        lines.append("## Setup attempts")
        lines.append("")
        lines.append("| ID | Action | Scenario | Environment | Status | Observed result | Limitation |")
        lines.append("|---|---|---|---|---|---|---|")
        for a in guide.attempts:
            lines.append(
                f"| {escape_cell(a.id)} | {escape_cell(a.action)} | {escape_cell(a.scenario or '—')} | {escape_cell(a.environment)} | "
                f"{escape_cell(a.status)} | {escape_cell(a.observed_result)} | {escape_cell(a.limitation)} |"
            )
        lines.append("")

    # Agent assistance
    lines.append("## What an agent can help with")
    lines.append("")
    if g.agent_assistance:
        for aa in g.agent_assistance:
            lines.append(f"- **{aa.capability}**: {aa.description}")
    else:
        lines.append("None recorded.")
    lines.append("")

    # Unknowns
    lines.append("## Unknowns")
    lines.append("")
    if g.unknowns:
        for u in g.unknowns:
            lines.append(f"- **{u.question}**")
            if u.why_it_matters:
                lines.append(f"  Why it matters: {u.why_it_matters}")
            if u.how_to_find_out:
                lines.append(f"  How to find out: {u.how_to_find_out}")
    else:
        lines.append("None recorded.")
    lines.append("")

    # Conflicts
    lines.append("## Conflicts")
    lines.append("")
    if g.conflicts:
        for c in g.conflicts:
            lines.append(f"- **{c.topic}**: {c.description}")
            if c.resolution:
                lines.append(f"  Resolution: {c.resolution}")
    else:
        lines.append("None recorded.")
    lines.append("")

    # Evidence details
    lines.append("## Evidence details")
    lines.append("")

    # Claims table
    if g.claims:
        lines.append("### Claims")
        lines.append("")
        lines.append("| ID | Claim | Conditions | Basis | Sources | Attempts | Excerpt | Excerpt status | Review | Support note |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for claim in g.claims:
            sources_str = ", ".join(claim.source_ids) if claim.source_ids else "—"
            attempts_str = ", ".join(claim.attempt_ids) if claim.attempt_ids else "—"
            excerpt_display = claim.excerpt or "—"
            review_str = claim.review_result.replace("_", " ") if hasattr(claim, "review_result") else "not reviewed"
            support_note = claim.support_note or "—"
            lines.append(
                f"| {escape_cell(claim.id)} | {escape_cell(claim.text)} | {escape_cell(claim.conditions or '—')} | "
                f"{escape_cell(claim.basis)} | {escape_cell(sources_str)} | {escape_cell(attempts_str)} | {escape_cell(excerpt_display)} | "
                f"{escape_cell(claim.excerpt_status)} | {escape_cell(review_str)} | {escape_cell(support_note)} |"
            )
        lines.append("")

    # Sources table
    if guide.sources:
        lines.append("### Sources")
        lines.append("")
        lines.append("| ID | Title | URL | Retrieval status | Retrieved at | Provider domain |")
        lines.append("|---|---|---|---|---|---|")
        for s in guide.sources:
            title = s.title[:50] if s.title else "—"
            retrieved = s.retrieval_time[:10] if getattr(s, "retrieval_time", "") else "—"
            lines.append(
                f"| {escape_cell(s.id)} | {escape_cell(title)} | {escape_cell(s.url)} | "
                f"{escape_cell(s.retrieval_status)} | {escape_cell(retrieved)} | "
                f"{escape_cell(s.provider_domain_status)} |"
            )
        lines.append("")

    # Uncited sources
    if guide.uncited_sources:
        lines.append("### Uncited sources")
        lines.append("")
        for s in guide.uncited_sources:
            lines.append(f"- {s.id}: {s.title or s.url} ({s.retrieval_status})")
        lines.append("")

    # Warnings
    if guide.warnings:
        lines.append("### Validation warnings")
        lines.append("")
        for w in guide.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # Run stats
    lines.append("### Run statistics")
    lines.append("")
    r = guide.run
    lines.append(f"- Model: {r.model or 'not recorded'}")
    lines.append(f"- Model calls: {r.model_calls}")
    lines.append(f"- Searches: {r.searches}")
    lines.append(f"- Page reads: {r.page_reads}")
    lines.append(f"- Duration: {r.duration_seconds}s")
    lines.append(f"- Cost: {r.cost}")
    if r.research_timed_out:
        lines.append(f"- ⚠ Research timed out")
    lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"Generated by OAuthPath · {guide.created_at}")
    lines.append("")

    return "\n".join(lines)
