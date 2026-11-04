"""CLI — run one guide generation from the terminal (Milestone A)."""

from __future__ import annotations

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import DATABASE_PATH, live_generation_configured
from app.store import Store
from app.pipeline import run_pipeline
from app.render import render_markdown


def main():
    parser = argparse.ArgumentParser(
        description="OAuthPath — generate an OAuth launch guide from the CLI"
    )
    parser.add_argument("app_name", help="App name (e.g. 'GitHub', 'Slack', 'Gmail')")
    parser.add_argument("--feature", default="", help="What the agent should do")
    parser.add_argument("--audience", default="external customers",
                        help="Target audience (default: external customers)")
    parser.add_argument("--website", default="", help="Official website URL")
    parser.add_argument("--force", action="store_true", help="Force regeneration (bypass cache)")
    parser.add_argument("--output", default="", help="Write Markdown to file")

    args = parser.parse_args()

    if not live_generation_configured():
        print("ERROR: ANTHROPIC_API_KEY not set. Cannot generate guides.")
        print("Copy .env.example to .env and fill in your API key.")
        sys.exit(1)

    store = Store(DATABASE_PATH)

    def on_event(msg: str):
        print(f"  → {msg}")

    print(f"Generating guide for: {args.app_name}")
    if args.feature:
        print(f"  Feature: {args.feature}")
    print(f"  Audience: {args.audience}")
    print()

    async def _run():
        return await asyncio.wait_for(
            run_pipeline(
                app_name=args.app_name,
                feature=args.feature,
                audience=args.audience,
                website=args.website,
                force=args.force,
                store=store,
                on_event=on_event,
            ),
            timeout=JOB_TIMEOUT_SECONDS + 10.0,
        )

    try:
        guide, error = asyncio.run(_run())
    except asyncio.TimeoutError:
        print(f"\nERROR: Pipeline timed out after {JOB_TIMEOUT_SECONDS}s")
        sys.exit(1)

    if error:
        print(f"\nERROR: {error}")
        sys.exit(1)

    print(f"\n✓ Guide saved: {guide.id} v{guide.version} ({guide.status})")
    print(f"  Provider: {guide.provider.name}")
    print(f"  OAuth support: {guide.guide.oauth_support}")
    print(f"  Sources: {len(guide.sources)} ({guide.run.searches} searches, {guide.run.page_reads} reads)")
    print(f"  Claims: {len(guide.guide.claims)}")
    print(f"  Duration: {guide.run.duration_seconds}s")

    if guide.warnings:
        print(f"  Warnings: {len(guide.warnings)}")
        for w in guide.warnings[:5]:
            print(f"    - {w}")

    # Output Markdown
    md = render_markdown(guide)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n  Markdown written to: {args.output}")
    else:
        print("\n" + "=" * 60)
        print(md)


if __name__ == "__main__":
    main()
