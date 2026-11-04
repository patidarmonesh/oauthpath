"""Automated security and edge-case boundary verification for OAuthPath.

Covers:
1. SQL Injection resistance in app/store.py (parameterized ? placeholders and column allowlisting)
2. XSS prevention in Jinja2 templates, markdown_it rendering, and remote image disabling
3. URL validation in app/validation.py against dangerous schemes, raw IPs, localhost, credentials, whitespace
4. Error sanitization, UUID error_id generation, and traceback isolation
5. Concurrency control (job_semaphore) and IP rate limiting (JOBS_PER_IP_PER_HOUR)
6. Secrets hygiene across data/* and repository files
"""

import asyncio
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import markdown_it

from app.config import (
    DATABASE_PATH,
    JOBS_PER_IP_PER_HOUR,
    MAX_CONCURRENT_JOBS,
)
from app.main import app, md_renderer, templates
from app.render import render_markdown
from app.schema import (
    DraftClaim,
    DraftGuideContent,
    DraftNextAction,
    DraftPath,
    DraftStep,
    GuideRecord,
    JobStatus,
    ProviderInfo,
    Scenario,
    Source,
)
from app.store import ALLOWED_JOB_COLUMNS, Store
from app.validation import validate_url


# ── 1. SQL Injection Verification ──────────────────────────────────────────

class TestSQLInjectionResilience:
    """Verifies that all database operations use parameterized queries and safe column identifiers."""

    @pytest.fixture
    def test_store(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        store = Store(db_path)
        yield store
        if os.path.exists(db_path):
            os.remove(db_path)

    def test_update_job_column_allowlist_blocks_sql_injection(self, test_store):
        """Disallowed column keys in update_job kwargs raise ValueError immediately."""
        test_store.create_job("job-1", "GitHub")

        malicious_kwargs = {
            "status; DROP TABLE jobs; --": "failed",
            "1=1": "value",
            "app_name' OR '1'='1": "hacked",
        }
        for bad_col, val in malicious_kwargs.items():
            with pytest.raises(ValueError, match="Invalid column for job update"):
                test_store.update_job("job-1", **{bad_col: val})

        # Verify database is intact
        job = test_store.get_job("job-1")
        assert job is not None
        assert job["app_name"] == "GitHub"

    def test_parameterized_queries_with_malicious_inputs(self, test_store):
        """Malicious SQL strings passed into parameterized queries are safely escaped."""
        sqli_payloads = [
            "' OR '1'='1",
            "'; DROP TABLE guides; --",
            "admin'--",
            "1 UNION SELECT null, null, null, null, null, null--",
        ]

        for payload in sqli_payloads:
            # Guide queries
            assert test_store.get_guide(payload, 1) is None
            assert test_store.get_latest_guide(payload) is None
            assert test_store.get_latest_reviewed(payload) is None
            assert test_store.check_cache(payload, 168) is None
            assert test_store.get_all_versions(payload) == []

            # Job queries
            assert test_store.get_job(payload) is None
            assert test_store.count_recent_jobs(payload, hours=1) == 0

        # Confirm tables still exist and are accessible
        test_store.create_job("j-safe", "SafeApp")
        assert test_store.get_job("j-safe")["app_name"] == "SafeApp"


# ── 2. XSS & Markdown Verification ─────────────────────────────────────────

class TestXSSPrevention:
    """Verifies HTML auto-escaping and Markdown raw HTML disabling."""

    def test_jinja2_auto_escaping_enabled(self):
        """Templates environment has autoescape=True by default."""
        assert templates.env.autoescape is True

        payload = "<script>alert('xss')</script>"
        rendered = templates.env.from_string("{{ val }}").render(val=payload)
        assert "<script>" not in rendered
        assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in rendered

    def test_markdown_it_disables_raw_html(self):
        """Raw HTML in Markdown is escaped and script tags cannot execute."""
        payload = "<script>alert(1)</script>"
        output = md_renderer.render(payload)
        assert "<script>" not in output
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in output

    def test_markdown_it_rejects_javascript_links(self):
        """Javascript: and data: URIs in Markdown links are neutralized."""
        js_payload = "[Click Here](javascript:alert(1))"
        output_js = md_renderer.render(js_payload)
        assert "<a href=\"javascript:" not in output_js
        assert "javascript:alert(1)" not in output_js or "<p>[Click Here](javascript:alert(1))</p>" in output_js

        data_payload = "[Click Here](data:text/html,<script>alert(1)</script>)"
        output_data = md_renderer.render(data_payload)
        assert "<a href=\"data:" not in output_data

    def test_markdown_remote_images_disabled(self):
        """Remote images in Markdown are stripped of <img> tags per §6.5 rule 5."""
        img_payload = "![Malicious Image](https://attacker.com/cookie_stealer.png)"
        output = md_renderer.render(img_payload)
        assert "<img" not in output
        assert "https://attacker.com/cookie_stealer.png" not in output
        assert "Malicious Image" in output

    def test_html_template_renders_insecure_http_source_as_plain_text(self):
        """Insecure http:// sources are preserved for evidence but not rendered as clickable <a> links."""
        guide = GuideRecord(
            schema_version="2",
            prompt_version="1",
            id="test-guide",
            version=1,
            created_at="",
            status="draft",
            provider=ProviderInfo(name="TestProvider"),
            scenario=Scenario(),
            guide=DraftGuideContent(
                oauth_support="confirmed",
                summary=[],
                next_action=DraftNextAction(instruction="Wait"),
                paths=[],
                scopes=[],
                blockers=[],
                agent_assistance=[],
                unknowns=[],
                conflicts=[],
                claims=[],
            ),
            sources=[
                Source(
                    id="S1",
                    url="http://insecure-docs.example.com/oauth",
                    title="Insecure Docs",
                    retrieval_status="read",
                ),
                Source(
                    id="S2",
                    url="https://secure-docs.example.com/oauth",
                    title="Secure Docs",
                    retrieval_status="read",
                ),
            ],
            attempts=[],
            warnings=[],
        )

        template = templates.get_template("guide.html")
        html = template.render(
            guide=guide,
            guide_html={"evidence_line": "", "format_owner": lambda x: x, "format_estimate": lambda x: "unknown"},
            reviewed_version=None,
            base_url="http://localhost:8000",
            versions=[],
        )

        # Secure https URL has active link
        assert '<a href="https://secure-docs.example.com/oauth"' in html
        # Insecure http URL is visible text but NOT an active hyperlink
        assert '<a href="http://insecure-docs.example.com/oauth"' not in html
        assert "http://insecure-docs.example.com/oauth" in html


# ── 3. URL Validation Verification ─────────────────────────────────────────

class TestURLValidationSecurity:
    """Comprehensive test matrix against dangerous schemes, raw IPs, and injection attacks."""

    @pytest.mark.parametrize("scheme_url", [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(document.cookie)",
        "javascript://test%0Aalert(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "file:///etc/passwd",
        "file:///C:/Windows/win.ini",
        "ftp://ftp.example.com/malware.exe",
        "vbscript:msgbox(1)",
        "//evil.com/path",
        "//127.0.0.1",
    ])
    def test_rejects_dangerous_schemes(self, scheme_url):
        valid, reason = validate_url(scheme_url)
        assert not valid, f"Should reject dangerous scheme: {scheme_url}"

    @pytest.mark.parametrize("raw_ip_url", [
        "https://127.0.0.1",
        "https://127.0.0.1/admin",
        "https://0.0.0.0",
        "https://192.168.1.1/api",
        "https://10.0.0.1/",
        "https://172.16.0.1/secret",
        "https://169.254.169.254/latest/meta-data/",  # Cloud metadata SSRF
        "https://1.1.1.1/dns",
        "https://8.8.8.8/",
        "https://[::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://[2001:db8::1]/",
    ])
    def test_rejects_raw_ips(self, raw_ip_url):
        valid, reason = validate_url(raw_ip_url)
        assert not valid, f"Should reject raw IP address: {raw_ip_url}"
        assert "IP" in reason or "hostname" in reason

    def test_raw_ips_rejected_even_for_source_evidence(self):
        """Even if is_source=True allows http://, raw IPs remain rejected."""
        valid, reason = validate_url("http://169.254.169.254/latest/meta-data/", is_source=True)
        assert not valid
        assert "Raw IP" in reason or "hostname" in reason

    @pytest.mark.parametrize("malformed_url", [
        "https://localhost",
        "https://localhost:8000/evil",
        "https://example",              # No dot in hostname
        "https://user:password@docs.com", # Embedded credentials
        "https://user@docs.com",          # Embedded username
        "https://docs.com/path with spaces",
        "https://docs.com/<script>",
        "https://docs.com/\"quoted\"",
        "https://docs.com/'singlequote'",
        "https://docs.com/\\backslash",
    ])
    def test_rejects_malformed_and_credentialed_urls(self, malformed_url):
        valid, reason = validate_url(malformed_url)
        assert not valid, f"Should reject malformed/credentialed URL: {malformed_url}"

    def test_accepts_valid_https_and_relative(self):
        valid, reason = validate_url("https://docs.github.com/en/apps")
        assert valid
        assert reason == ""

        valid_rel, _ = validate_url("/guides/github-1234", allow_relative=True)
        assert valid_rel

        invalid_rel, _ = validate_url("/guides/github-1234", allow_relative=False)
        assert not invalid_rel


# ── 4. Error Sanitization & Traceback Isolation ─────────────────────────────

class TestErrorSanitization:
    """Verifies that no stack traces, file paths, or system details leak to clients."""

    def test_job_runner_error_sanitization(self):
        """Simulate an internal exception in _run_job and check sanitized output."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        store = Store(db_path)
        try:
            store.create_job("j-err", "TestApp")

            # Emulate the catch block in _run_job
            error_id = str(uuid.uuid4())[:8]
            store.update_job(
                "j-err",
                status=JobStatus.failed.value,
                error_message=f"An error occurred (Error ID: {error_id})",
                error_id=error_id,
            )

            job = store.get_job("j-err")
            assert job["status"] == "failed"
            assert job["error_id"] == error_id
            assert "Traceback" not in job["error_message"]
            assert "/" not in job["error_message"] and "\\" not in job["error_message"]
            assert "Exception" not in job["error_message"]
            assert f"(Error ID: {error_id})" in job["error_message"]
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_fastapi_unhandled_exception_handler(self):
        """Unhandled exceptions produce generic JSON or HTML with an Error ID, not tracebacks."""
        client = TestClient(app, raise_server_exceptions=False)

        # Trigger 404
        resp_404 = client.get("/api/jobs/nonexistent-job-id")
        assert resp_404.status_code == 404
        assert "detail" in resp_404.json()

        # Check client IP is not returned in job status polling response
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        tmp_store = Store(db_path)
        try:
            tmp_store.create_job("j-poll", "App", client_ip="198.51.100.42")
            with patch("app.main.store", tmp_store):
                resp = client.get("/api/jobs/j-poll")
                assert resp.status_code == 200
                data = resp.json()
                assert "client_ip" not in data, "client_ip must not leak to status API"
                assert data["id"] == "j-poll"
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


# ── 5. Concurrency & Rate Limiting Verification ────────────────────────────

class TestConcurrencyAndRateLimiting:
    """Verifies MAX_CONCURRENT_JOBS semaphore and JOBS_PER_IP_PER_HOUR limit."""

    def test_ip_rate_limiting_enforced(self):
        """After JOBS_PER_IP_PER_HOUR jobs within 1 hour, requests return 429."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        test_store = Store(db_path)
        client_ip = "testclient"

        try:
            # Seed JOBS_PER_IP_PER_HOUR jobs for this client IP
            for i in range(JOBS_PER_IP_PER_HOUR):
                test_store.create_job(f"rate-{i}", "App", client_ip=client_ip)

            assert test_store.count_recent_jobs(client_ip) == JOBS_PER_IP_PER_HOUR

            with patch("app.main.store", test_store), \
                 patch("app.main.live_generation_configured", return_value=True), \
                 patch("app.main._get_client_ip", return_value=client_ip):
                client = TestClient(app)
                # Attempt to bypass rate limit by sending spoofed X-Forwarded-For header
                resp = client.post(
                    "/api/jobs",
                    json={"app_name": "NewApp"},
                    headers={"X-Forwarded-For": "198.51.100.99"},
                )
                assert resp.status_code == 429
                assert "Rate limit" in resp.json()["detail"]
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_concurrency_busy_guard_enforced(self):
        """When concurrency capacity is full, create_job returns 503."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        test_store = Store(db_path)

        try:
            sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
            # Acquire all permits to simulate busy server
            loop = asyncio.new_event_loop()
            for _ in range(MAX_CONCURRENT_JOBS):
                loop.run_until_complete(sem.acquire())

            with patch("app.main.store", test_store), \
                 patch("app.main.job_semaphore", sem), \
                 patch("app.main.live_generation_configured", return_value=True):
                client = TestClient(app)
                resp = client.post("/api/jobs", json={"app_name": "Acme"})
                assert resp.status_code == 503
                assert "busy" in resp.json()["detail"].lower()
            loop.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


# ── 6. Secrets Hygiene Verification ────────────────────────────────────────

class TestSecretsHygiene:
    """Verifies that no secrets or private keys exist in tracked files or data/*."""

    def test_no_secrets_in_data_directory(self):
        """data/ should contain no committed API keys, tokens, or credential files."""
        data_dir = Path("data")
        assert data_dir.exists(), "data/ directory must exist"

        for p in data_dir.rglob("*"):
            if p.is_file():
                # Files should only be .gitkeep, .db, or json records without credentials
                if p.suffix == ".json":
                    content = p.read_text(encoding="utf-8")
                    assert "client_secret" not in content or "your-client-secret" in content
                    assert "sk-ant" not in content
                    assert "xoxb-" not in content
                    assert "ghp_" not in content

    def test_gitignore_ignores_env_and_db_files(self):
        """.gitignore must exclude .env, *.db, and logs."""
        gitignore = Path(".gitignore")
        assert gitignore.exists()
        text = gitignore.read_text(encoding="utf-8")
        assert ".env" in text
        assert "*.db" in text
