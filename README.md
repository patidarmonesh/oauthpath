# OAuthPath

Helps developers see what it takes to set up and get approval for a provider
OAuth app: the next action, effort vs waiting, launch blockers and who owns
them, with visible evidence.

**Live:** *(deploy URL — see [Deployment](#deployment) section)* · **Source:** this repository

## Try it
- Generate fresh draft guides live for any app or scenario (e.g. GitHub, Slack, Google)
- Browse reviewed guides from the library (promoted via `scripts/promote.py` after human audit)
- Inspect visible evidence, claim citations, gate assessments, and export full Markdown guides

## Run locally
Requires Python 3.10+ and an Anthropic API key with web search/fetch access.
A Composio API key is optional (toolkit metadata only).

**Windows (PowerShell):**
```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env        # then fill in keys locally
.\.venv\Scripts\python.exe -m app.cli "GitHub"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

**macOS / Linux:**
```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # then fill in keys locally
python -m app.cli "GitHub"
uvicorn app.main:app --reload
```

**Tests:**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

## How it works
Resolve the product → look up Composio toolkit metadata (optional) → a bounded
research agent searches and reads sources → a structured guide is synthesized →
code validates references, links, estimates and gates → an immutable version is
saved and rendered as HTML and Markdown.

```
Input → Resolver → Composio Lookup → Research Agent → Synthesis → Validation → Store → Render
         │              │                   │              │            │
         │              │                   │              │            ├─ Difficulty (computed)
         │              │                   │              │            ├─ Composio path (if confirmed)
         │              │                   │              │            └─ Status (draft/partial/reviewed)
         │              │                   │              └─ Structured output + bounded repair
         │              │                   └─ web_search + web_fetch with budgets
         │              └─ REST API: toolkit slug, auth method, managed status
         └─ Product identity + domain parsing
```

## Key decisions
- Every guide states its scenario: feature, audience, deployment.
- Managed and custom OAuth are alternatives, including in production.
- Sources and setup observations are recorded by code, never by the model.
- Unknowns stay unknown: nullable estimates (`low: null, high: null`), provisional difficulty.
- Evidence provenance is protected: active production loaders (`data/attempts/` and `data/reviewed/`) require authentic records and reject synthetic or unverified data.
- Drafts and human-reviewed versions are separate, versioned, and shareable.

## Architecture

| Component | File | Responsibility |
|---|---|---|
| Config | `app/config.py` | Environment variables, defaults, constants |
| Schema | `app/schema.py` | Two-layer models: model-writable drafts + code-only records |
| Resolver | `app/resolver.py` | Product identity + Composio toolkit matching without substring false positives |
| Research | `app/research.py` | Bounded tool-using agent with source registry and deadline enforcement |
| Synthesis | `app/synthesis.py` | Structured output from findings via Claude Sonnet |
| Validation | `app/validation.py` | All §6.5 rules: claims, URLs, gates, estimates, Composio |
| Store | `app/store.py` | SQLite: immutable versions, jobs, reviewed seeds |
| Pipeline | `app/pipeline.py` | Orchestrates one job under deadline with identity corroboration |
| Render | `app/render.py` | Markdown export with same full evidence content as HTML |
| Web App | `app/main.py` | FastAPI routes, templates, background jobs, server-side initial states |
| CLI | `app/cli.py` | Terminal guide generation with outer deadline bound |

## Test Suite & Automated Coverage

The project includes an automated test suite of **221 unit, integration, and regression tests** across 8 test modules, run via `pytest`:
- **Smoke Tests (`tests/smoke_test.py` - 10 tests)**: Route precedence, review fields immutability, gate evidence enforcement, Composio claims preservation, difficulty on explicit unknown gates, schema enums, domain-website mismatch protection, input validation, and ambiguous candidate prefill.
- **Core Specification Tests (`tests/test_oauthpath.py` - 22 tests)**: Estimate normalization, zero-estimate preservation, URL validation, claims & evidence rules, Composio toolkit matching, deterministic difficulty calculation, excerpt verification, SQLite version immutability, markdown table escaping, and web routes.
- **Pipeline E2E & Edge-Case Tests (`tests/test_pipeline_e2e.py` - 22 tests)**: Multi-turn research tool calling (`web_search` and `web_fetch`), real SDK 2.4.0 `Message.model_validate` shapes, synthesis `max_tokens` truncation retry, dual rendering parity between `render_markdown` and `guide.html`, and base guide validation.
- **Composio & Schema Rigor Tests (`tests/test_composio_and_schema_rigor.py` - 68 tests)**: Composio REST endpoints, search corroboration (exact -> domain), Composio SDK method calls (`auth_configs.create`, `connected_accounts.link`), two-layer schema rigor, promotion workflow (`scripts/promote.py`), DocumentBlock text extraction, failed fetch URL mapping, and zero wait estimates.
- **Security & Injection Tests (`tests/test_security_audit.py` - 39 tests)**: SQL injection protection via parameterization and column allowlists, XSS autoescaping, network boundary defense against SSRF / raw IPs, error sanitization, and IP rate limiting.
- **Code Review Regressions (`tests/test_code_review_regressions.py` - 26 tests)**: Regressions for R1–R10, Findings 1–8, Follow-Up Items 1–4, and research deadline/timeout partial saving: secret sanitization, non-mutating reviewed record ingestion, word-boundary toolkit matching without false domain sharing, shared-domain candidate disambiguation, unsupported step qualification, stale hidden website state reset, artifact text link preservation, research wrap-up deadline buffers, next action schema string type preservation, sibling product conflict prevention on all domains, genuine blocker observation support, research phase cancellation cleanup, user website conflict preservation without candidate loops, uppercase auth schemes, and research timeout partial guide persistence.
- **Pipeline Repeatability & Scenarios (`tests/test_pipeline_repeatability_and_scenarios.py` - 18 tests)**: Full input-to-HTML/export pipeline execution (`/api/jobs` -> polling -> disk reload -> HTML -> Markdown export), pipeline repetition (3 passes: fresh db, repeated cache hit, force refresh v2), unfamiliar app corroboration with NewCloud fixtures and absence of cross-product text, Composio custom/managed/unsupported modes, missing keys, delayed call timeouts, cancellation failure modes, ambiguous name candidate reload, idempotent seed imports across 3 server restarts, and concurrency guard.
- **Full Review Finding Regressions (`tests/test_full_review_findings.py` - 16 tests)**: Dedicated boundary regressions for Findings 1–9 from `docs/FULL_CODE_REVIEW.md`: CLI timeout & error exit codes, reviewed import rejection of unsourced claims and synthetic attempts, two-path Composio promotion with unique step IDs, gate reversal claim text updates with history preservation, base version identity and scenario mismatch rejections, canonical attempt loading with qualified product names, preparation step idempotence and URL preservation, retry link preservation of user-typed website and scenario parameters, rendered attempt references across Markdown and HTML, and conflicting model guess rejection preserving user domain and coherent name.

All 221 tests pass cleanly:
```
======================= 221 passed, 1 warning in 32.90s =======================
```

- **Browser DOM Check (`scripts/verify_browser.py`)**: Local verification script that launches headless Chrome to inspect server-rendered DOM elements and form attributes (note: targets a hardcoded Windows Chrome path `C:\Program Files\Google\Chrome\Application\chrome.exe`).

## Validation & Verification
See `docs/VERIFICATION.md` for the 5-part verification report covering the setup attempt workflow, the 15-claim audit, live-run logging, documented vs observed evidence boundaries, and remaining live operational prerequisites. See `docs/DECISIONS.md` for architectural decision records.

## Deployment

OAuthPath is an ASGI FastAPI application with SQLite persistence and background task handling.

1. **Environment Setup:** Set `ANTHROPIC_API_KEY` and optionally `COMPOSIO_API_KEY`.
2. **Persistent Storage:** Ensure the `data/` directory (specifically `data/oauthpath.db`) is mounted on persistent storage. SQLite WAL mode handles concurrent read queries efficiently.
3. **Ingress & Reverse Proxy:** Run behind a TLS reverse proxy (e.g. Caddy, Nginx, or Cloudflare). Configure `FORWARDED_ALLOW_IPS` to match your ingress proxy IP (defaults to `127.0.0.1` to prevent client IP spoofing).
4. **Execution:**
   ```bash
   python serve.py
   ```

## Limitations
- Live guide generation requires an Anthropic API key with web search/fetch tool access (`claude-sonnet-5-5`).
- The library works without live generation (browse human-reviewed guides).
- Provider requirements change; guides show their research date.
- JavaScript-heavy documentation portals may yield fewer readable text blocks.
- Single-worker deployment; SQLite on persistent storage required.

## Expanding to 100 apps
Same schema and pipeline. Prioritize by developer demand (Composio usage),
missing managed apps and gate complexity; generate drafts in batches; review the
top apps by hand; re-check on a schedule and when provider changelogs move;
label freshness on every guide. A catalog entry is not a guide.

## Stack
Python 3.10, FastAPI, Jinja2 templates, plain CSS/JS, SQLite, Pydantic v2,
Anthropic Python SDK (claude-sonnet-5-5), httpx, markdown-it-py,
thefuzz, tldextract, python-dotenv.

All dependency versions pinned in `requirements.txt` and verified with automated test suite.
