"""Browser DOM rendering and form attribute verification script using real Chrome in headless mode.

Tests rendered DOM, form inputs, candidate choices, prefill attributes, and guide views
in Google Chrome without requiring external selenium/playwright drivers.
Does not claim interactive simulated event dispatching (typing/clicking).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8976


def run_chrome_dump_dom(url: str, wait_seconds: float = 1.0) -> str:
    """Run headless Chrome to load URL and dump rendered DOM."""
    temp_out = Path("temp_chrome_dom.html")
    cmd = [
        CHROME_PATH,
        "--headless=new",
        "--disable-gpu",
        "--dump-dom",
        f"--virtual-time-budget={int(wait_seconds * 1000)}",
        url,
    ]
    with open(temp_out, "w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True, timeout=15)

    content = temp_out.read_text(encoding="utf-8")
    if temp_out.exists():
        temp_out.unlink()
    return content


def setup_browser_test_db(db_path: str):
    """Populate test DB with sample guide and ambiguous job."""
    from app.store import Store
    from app.schema import (
        GuideRecord, Scenario, ProviderInfo, ComposioInfo,
        DraftGuideContent, DraftSummaryStatement, DraftNextAction,
        DraftClaim, Source, RunStats, JobStatus
    )

    store = Store(db_path)

    # 1. Add sample guide
    guide = GuideRecord(
        schema_version="2",
        prompt_version="1",
        id="browser-test-guide",
        version=1,
        status="draft",
        cache_key="browser:test:server-side",
        provider=ProviderInfo(name="GitHub", official_domain="github.com", identity_status="resolved"),
        scenario=Scenario(feature="sync repos", audience="external customers", deployment="server-side"),
        composio=ComposioInfo(lookup_status="no_match"),
        guide=DraftGuideContent(
            oauth_support="confirmed",
            summary=[DraftSummaryStatement(text="GitHub OAuth verified in browser test", claim_ids=["C1"])],
            next_action=DraftNextAction(instruction="Register GitHub App", link_or_artifact="https://github.com/settings/apps", claim_ids=["C1"]),
            claims=[DraftClaim(id="C1", text="GitHub OAuth 2.0 supported", basis="documented", excerpt="GitHub OAuth 2.0 supported", excerpt_status="found", source_ids=["S1"])],
        ),
        sources=[Source(id="S1", url="https://docs.github.com/apps", title="GitHub Apps Docs", kind="fetched_page", retrieval_status="read")],
        attempts=[],
        run=RunStats(model_calls=2, searches=1, page_reads=1, fetch_attempts=1, tokens_input=100, tokens_output=50, model="claude-sonnet-5-5", duration_seconds=5.0),
    )
    store.save_guide(guide)

    # 2. Add sample ambiguous job
    store.create_job(
        job_id="browser-job-box",
        app_name="Box",
        feature="storage",
        audience="external customers",
        website="",
        client_ip="127.0.0.1",
    )
    candidates = [{"name": "Box", "domain": "box.com"}, {"name": "Dropbox", "domain": "dropbox.com"}]
    store.update_job(
        "browser-job-box",
        status=JobStatus.needs_input.value,
        stage="Awaiting input: ambiguous name",
        candidates=json.dumps(candidates),
    )


def main():
    if not Path(CHROME_PATH).exists():
        print(f"Chrome not found at {CHROME_PATH}")
        sys.exit(1)

    test_db = Path("data/browser_test.db")
    if test_db.exists():
        test_db.unlink()

    print("[1/5] Setting up browser test database...")
    setup_browser_test_db(str(test_db))

    print("[2/5] Starting FastAPI uvicorn server on port", PORT)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(test_db)
    env["FORWARDED_ALLOW_IPS"] = "127.0.0.1"

    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Give uvicorn a second to bind
        time.sleep(2.0)

        # Test 1: Home page loads cleanly
        print("[3/5] Testing Home page rendering in Chrome...")
        home_dom = run_chrome_dump_dom(f"http://127.0.0.1:{PORT}/")
        assert "<title>OAuthPath" in home_dom or "OAuthPath" in home_dom
        assert 'name="app_name"' in home_dom
        assert 'id="prefill_website"' in home_dom
        print("  -> Home page rendered successfully.")

        # Test 2: Prefilled Home page with initial app data attribute
        print("[4/5] Testing Prefilled form and disambiguation state...")
        prefilled_dom = run_chrome_dump_dom(f"http://127.0.0.1:{PORT}/?app_name=Box&website=box.com")
        assert 'value="Box"' in prefilled_dom
        assert 'value="box.com"' in prefilled_dom
        assert 'data-initial-app="Box"' in prefilled_dom
        print("  -> Prefill attributes and form data rendered properly in Chrome.")

        # Test 3: Job page in needs_input state
        print("[5/5] Testing Job Disambiguation page in Chrome...")
        job_dom = run_chrome_dump_dom(f"http://127.0.0.1:{PORT}/jobs/browser-job-box")
        assert "Awaiting input: ambiguous name" in job_dom
        assert "Ambiguous name." in job_dom
        assert "box.com" in job_dom
        assert "dropbox.com" in job_dom
        print("  -> Disambiguation buttons and candidate links rendered successfully in Chrome.")

        # Test 4: Guide page
        guide_dom = run_chrome_dump_dom(f"http://127.0.0.1:{PORT}/guides/browser-test-guide/versions/1")
        assert "GitHub" in guide_dom
        assert "GitHub OAuth verified in browser test" in guide_dom
        assert "Register GitHub App" in guide_dom
        assert "GitHub Apps Docs" in guide_dom
        print("  -> Guide view with full claims and sources rendered cleanly in Chrome.")

        print("\nAll browser DOM rendering and form attribute checks passed in Google Chrome!")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        if test_db.exists():
            test_db.unlink()


if __name__ == "__main__":
    main()
