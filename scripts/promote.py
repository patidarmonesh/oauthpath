"""Promote a guide version to reviewed — per §6.7 promote workflow.

Supports:
1. Direct promotion:
   python scripts/promote.py <guide_id> <version> --reviewer "Name" --claims K1,K2
2. Editing workflow:
   python scripts/promote.py <guide_id> <version> --export-only
   (edit data/review-copy-<guide_id>-v<version>.json)
   python scripts/promote.py --from-file data/review-copy-<guide_id>-v<version>.json --reviewer "Name"
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import DATABASE_PATH
from app.store import Store
from app.schema import GuideRecord, ReviewInfo, GuideStatus, DraftClaim, Source, Attempt
from app.validation import validate_guide, validate_reviewed_record
from app.pipeline import _load_attempts_for_provider


def _next_source_id(guide: GuideRecord) -> str:
    """Scan both guide.sources and guide.uncited_sources for the highest S<number> suffix."""
    max_num = 0
    all_sources = (guide.sources or []) + (guide.uncited_sources or [])
    for s in all_sources:
        if s.id.startswith("S"):
            suffix = s.id[1:]
            if suffix.isdigit():
                max_num = max(max_num, int(suffix))
    return f"S{max_num + 1}"


def main():
    parser = argparse.ArgumentParser(description="Promote a guide version to reviewed")
    parser.add_argument("guide_id", nargs="?", default="", help="Guide ID")
    parser.add_argument("version", nargs="?", type=int, default=0, help="Version to base on")
    parser.add_argument("--reviewer", help="Reviewer name")
    parser.add_argument("--claims", help="Comma-separated audited claim IDs")
    parser.add_argument("--pass-gate", help="Gate kind to mark not_required with audited claim")
    parser.add_argument("--fail-gate", help="Gate kind to mark required with audited claim")
    parser.add_argument("--source", help="Source URL or S-id backing the gate verification claim")
    parser.add_argument("--attempt-id", help="Attempt ID backing the gate verification claim")
    parser.add_argument("--export-only", action="store_true",
                        help="Export a working copy instead of promoting")
    parser.add_argument("--from-file", help="Path to edited JSON working copy to promote")
    parser.add_argument("--note", default="", help="Substantive support note for the audited gate verification claim")

    args = parser.parse_args()
    store = Store(DATABASE_PATH)

    # 1. From-file workflow
    if args.from_file:
        file_path = Path(args.from_file)
        if not file_path.exists():
            print(f"ERROR: File {file_path} not found")
            sys.exit(1)

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            guide = GuideRecord.model_validate(data)
        except Exception as e:
            print(f"ERROR: Failed to parse guide from {file_path}: {e}")
            sys.exit(1)

        reviewer = args.reviewer or (guide.review.reviewer if guide.review else "")
        if not reviewer:
            print("ERROR: --reviewer must be specified or present in the file")
            sys.exit(1)

        audited_ids = guide.review.audited_claim_ids if guide.review else []
        if args.claims:
            audited_ids = [c.strip() for c in args.claims.split(",") if c.strip()]

        if not audited_ids:
            print("ERROR: At least one audited claim ID must be specified (via --claims or in file)")
            sys.exit(1)

        guide.review = ReviewInfo(
            reviewer=reviewer,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            audited_claim_ids=audited_ids,
            based_on_version=guide.version,
        )
        guide.status = GuideStatus.reviewed.value

        # Check audited claims integrity
        claims_by_id = {c.id: c for c in guide.guide.claims}
        missing_claims = [cid for cid in audited_ids if cid not in claims_by_id]
        if missing_claims:
            print(f"ERROR: Audited claim IDs not found in guide claims: {missing_claims}")
            sys.exit(1)

        for cid in audited_ids:
            claim = claims_by_id[cid]
            if claim.review_result == "not_reviewed":
                print(f"ERROR: Claim {cid} is listed as audited but review_result is 'not_reviewed'")
                sys.exit(1)
            if not claim.support_note or not claim.support_note.strip():
                print(f"ERROR: Claim {cid} is listed as audited but has an empty support_note")
                sys.exit(1)

        # For edited-file promotion, verify base version exists and has same provider/scenario
        base_v = guide.version if guide.version > 0 else (guide.review.based_on_version if guide.review else None)
        if not base_v or base_v <= 0:
            print("ERROR: Promotion from file requires an existing base version (guide.version or review.based_on_version must be > 0)")
            sys.exit(1)

        base = store.get_guide(guide.id, base_v)
        if not base:
            print(f"ERROR: Base guide version {base_v} not found in store")
            sys.exit(1)
        if base.provider.name.lower() != guide.provider.name.lower():
            print(f"ERROR: Provider name mismatch between base ({base.provider.name}) and review ({guide.provider.name})")
            sys.exit(1)
        if (base.provider.official_domain or "").lower() != (guide.provider.official_domain or "").lower():
            print(f"ERROR: Provider domain mismatch between base ({base.provider.official_domain}) and review ({guide.provider.official_domain})")
            sys.exit(1)
        if base.scenario.feature != guide.scenario.feature or base.scenario.audience != guide.scenario.audience or base.scenario.deployment != guide.scenario.deployment:
            print(f"ERROR: Scenario mismatch between base and review copy (feature, audience, or deployment changed)")
            sys.exit(1)

        valid, err = validate_reviewed_record(guide)
        if not valid:
            print(f"ERROR: Review validation failed: {err}")
            sys.exit(1)

        warnings = validate_guide(guide)
        if warnings:
            print(f"Validation warnings ({len(warnings)}):")
            for w in warnings[:10]:
                print(f"  - {w}")

        new_version = store.save_guide(guide)
        print(f"\n[OK] Promoted from file to reviewed version: {guide.id} v{new_version}")
        print(f"  Reviewer: {reviewer}")
        print(f"  Audited claims: {', '.join(audited_ids)}")

        reviewed_dir = Path(os.getenv("REVIEWED_DIR", "data/reviewed"))
        seed_path = reviewed_dir / f"{guide.id}-v{new_version}.json"
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text(guide.model_dump_json(indent=2), encoding="utf-8")
        print(f"  Saved seed file: {seed_path}")
        return

    # 2. In-store workflow
    if not args.guide_id or not args.version:
        print("ERROR: guide_id and version are required when not using --from-file")
        sys.exit(1)

    guide = store.get_guide(args.guide_id, args.version)
    if not guide:
        print(f"ERROR: Guide {args.guide_id} v{args.version} not found")
        sys.exit(1)

    if args.export_only:
        out_path = Path(f"data/review-copy-{args.guide_id}-v{args.version}.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(guide.model_dump_json(indent=2), encoding="utf-8")
        print(f"Exported working copy to: {out_path}")
        print("Edit the file, then promote with:")
        print(f"  python scripts/promote.py --from-file {out_path} --reviewer \"<Your Name>\"")
        return

    if not args.reviewer:
        print("ERROR: --reviewer is required to promote")
        sys.exit(1)

    audited_ids = [c.strip() for c in args.claims.split(",")] if args.claims else []

    # Handle --pass-gate or --fail-gate
    if args.pass_gate or args.fail_gate:
        if not args.source and not args.attempt_id:
            print("ERROR: --source (URL or S-id) or --attempt-id is required when using --pass-gate or --fail-gate")
            sys.exit(1)

        gate_kind = args.pass_gate or args.fail_gate
        new_state = "not_required" if args.pass_gate else "required"
        cid = f"REV-{gate_kind.upper()[:8]}"
        claim_text = f"Reviewer verified gate '{gate_kind}' state is {new_state}"

        source_ids = []
        attempt_ids = []
        basis = "documented"

        if args.source:
            if args.source.startswith("http://") or args.source.startswith("https://"):
                src = next((s for s in guide.sources if s.url == args.source), None)
                if not src:
                    sid = _next_source_id(guide)
                    src = Source(
                        id=sid,
                        url=args.source,
                        title=f"Reviewer verification for {gate_kind}",
                        kind="maintainer_reference",
                        retrieval_status="read",
                        retrieval_time=datetime.now(timezone.utc).isoformat(),
                    )
                    guide.sources.append(src)
                source_ids.append(src.id)
            else:
                existing_src = next((s for s in guide.sources if s.id == args.source), None)
                if not existing_src:
                    print(f"ERROR: Source ID '{args.source}' not found in guide sources")
                    sys.exit(1)
                source_ids.append(args.source)
            basis = "documented"

        if args.attempt_id:
            # Check if attempt exists in guide.attempts or data/attempts/
            existing_att = next((a for a in guide.attempts if a.id == args.attempt_id), None)
            if not existing_att:
                loaded = [a for a in _load_attempts_for_provider(guide.provider.name, guide.provider.official_domain) if a.id == args.attempt_id]
                if loaded:
                    existing_att = loaded[0]
                    guide.attempts.append(existing_att)

            if not existing_att:
                print(f"ERROR: Attempt ID '{args.attempt_id}' not found in guide attempts or recorded attempts")
                sys.exit(1)

            # A single individual connection cannot establish that an organizational gate is not required
            if gate_kind in ("app_review", "security_assessment", "customer_admin_action", "business_or_identity_verification") and new_state == "not_required":
                print(f"ERROR: A setup attempt cannot establish that '{gate_kind}' is not required")
                sys.exit(1)

            attempt_ids.append(args.attempt_id)
            basis = "observed"

        support_note = args.note.strip() if getattr(args, "note", None) and args.note.strip() else f"Gate '{gate_kind}' state '{new_state}' reviewed and verified by {args.reviewer}"

        # Add or update claim
        existing_claim = next((c for c in guide.guide.claims if c.id == cid), None)
        if not existing_claim:
            existing_claim = DraftClaim(
                id=cid,
                text=claim_text,
                basis=basis,
                source_ids=source_ids,
                attempt_ids=attempt_ids,
                review_result="supported",
                support_note=support_note,
            )
            guide.guide.claims.append(existing_claim)
        else:
            existing_claim.text = claim_text
            existing_claim.basis = basis
            if source_ids:
                existing_claim.source_ids = source_ids
                existing_claim.attempt_ids = []
            elif attempt_ids:
                existing_claim.attempt_ids = attempt_ids
                existing_claim.source_ids = []
            existing_claim.review_result = "supported"
            existing_claim.support_note = support_note

        if cid not in audited_ids:
            audited_ids.append(cid)

        # Update gates in all paths
        for path in guide.guide.paths:
            for g in path.gates:
                if g.kind == gate_kind:
                    g.state = new_state
                    if cid not in g.claim_ids:
                        g.claim_ids.append(cid)

    if not audited_ids:
        print("ERROR: At least one audited claim ID required (via --claims or --pass-gate/--fail-gate)")
        sys.exit(1)

    claims_by_id = {c.id: c for c in guide.guide.claims}
    for cid in audited_ids:
        if cid not in claims_by_id:
            print(f"ERROR: Claim {cid} not found in guide claims")
            sys.exit(1)
        c = claims_by_id[cid]
        # In non-file mode, require explicit review_result and support_note to prevent generic bypass
        if c.review_result == "not_reviewed" or not c.support_note or not c.support_note.strip():
            print(f"ERROR: Claim {cid} has not been explicitly reviewed (review_result='{c.review_result}', missing support_note).")
            print("To ensure review integrity, use the --from-file workflow to audit claims:")
            print(f"  1. python scripts/promote.py {args.guide_id} {args.version} --export-only")
            print(f"  2. Edit data/review-copy-{args.guide_id}-v{args.version}.json to set review_result and support_note")
            print(f"  3. python scripts/promote.py --from-file data/review-copy-{args.guide_id}-v{args.version}.json --reviewer \"{args.reviewer}\"")
            sys.exit(1)

    guide.review = ReviewInfo(
        reviewer=args.reviewer,
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        audited_claim_ids=audited_ids,
        based_on_version=args.version,
    )
    guide.status = GuideStatus.reviewed.value

    valid, err = validate_reviewed_record(guide)
    if not valid:
        print(f"ERROR: Review validation failed: {err}")
        sys.exit(1)

    # Revalidate
    warnings = validate_guide(guide)
    if warnings:
        print(f"Validation produced {len(warnings)} warnings:")
        for w in warnings[:10]:
            print(f"  - {w}")

    new_version = store.save_guide(guide)
    print(f"\n[OK] Promoted to reviewed version: {args.guide_id} v{new_version}")
    print(f"  Reviewer: {args.reviewer}")
    print(f"  Audited claims: {', '.join(audited_ids)}")
    print(f"  Based on: v{args.version}")

    reviewed_dir = Path(os.getenv("REVIEWED_DIR", "data/reviewed"))
    seed_path = reviewed_dir / f"{args.guide_id}-v{new_version}.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(guide.model_dump_json(indent=2), encoding="utf-8")
    print(f"  Seed file: {seed_path}")


if __name__ == "__main__":
    main()
