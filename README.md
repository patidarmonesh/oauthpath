# OAuthPath

Helps developers see what it takes to set up and get approval for a provider
OAuth app: the next action, effort vs waiting, launch blockers and who owns
them, with visible evidence.

**Live:** *(deploy URL — see [Deployment](#deployment) section)* · **Source:** this repository

Runs entirely on free tiers: Google AI Studio Gemini (free tier), Tavily (free tier), Composio (Hobby plan), and Render (free tier). Also supports Anthropic Claude Sonnet via `LLM_PROVIDER=anthropic`.

## Try it
- Generate fresh draft guides live for any app or scenario (e.g. GitHub, Slack, Gmail)
- Browse reviewed guides from the library (promoted via `scripts/promote.py` after human audit)
- Inspect visible evidence, claim citations, gate assessments, and export full Markdown guides

## Run locally
Requires Python 3.10+. For live generation, obtain free API keys from Google AI Studio (`GEMINI_API_KEY`) and Tavily (`TAVILY_API_KEY`). A Composio API key is optional (`COMPOSIO_API_KEY` for toolkit metadata). Alternatively, set `LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`.

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

The project includes an automated test suite of **231 unit, integration, and regression tests** across 9 test modules, run via `pytest`:
- **Smoke Tests (`tests/smoke_test.py` - 10 tests)**: Route precedence, review fields immutability, gate evidence enforcement, Composio claims preservation, difficulty on explicit unknown gates, schema enums, domain-website mismatch protection, input validation, and ambiguous candidate prefill.
- **Core Specification Tests (`tests/test_oauthpath.py` - 22 tests)**: Estimate normalization, zero-estimate preservation, URL validation, claims & evidence rules, Composio toolkit matching, deterministic difficulty calculation, excerpt verification, SQLite version immutability, markdown table escaping, and web routes.
- **Pipeline E2E & Edge-Case Tests (`tests/test_pipeline_e2e.py` - 22 tests)**: Multi-turn research tool calling (`web_search` and `web_fetch`), real SDK 2.4.0 `Message.model_validate` shapes, synthesis `max_tokens` truncation retry, dual rendering parity between `render_markdown` and `guide.html`, and base guide validation.
- **Composio & Schema Rigor Tests (`tests/test_composio_and_schema_rigor.py` - 68 tests)**: Composio REST endpoints, search corroboration (exact -> domain), Composio SDK method calls (`auth_configs.create`, `connected_accounts.link`), two-layer schema rigor, promotion workflow (`scripts/promote.py`), DocumentBlock text extraction, failed fetch URL mapping, and zero wait estimates.
- **Security & Injection Tests (`tests/test_security_audit.py` - 39 tests)**: SQL injection protection via parameterization and column allowlists, XSS autoescaping, network boundary defense against SSRF / raw IPs, error sanitization, and IP rate limiting.
- **Code Review Regressions (`tests/test_code_review_regressions.py` - 26 tests)**: Regressions for R1–R10, Findings 1–8, Follow-Up Items 1–4, and research deadline/timeout partial saving: secret sanitization, non-mutating reviewed record ingestion, word-boundary toolkit matching without false domain sharing, shared-domain candidate disambiguation, unsupported step qualification, stale hidden website state reset, artifact text link preservation, research wrap-up deadline buffers, next action schema string type preservation, sibling product conflict prevention on all domains, genuine blocker observation support, research phase cancellation cleanup, user website conflict preservation without candidate loops, uppercase auth schemes, and research timeout partial guide persistence.
- **Pipeline Repeatability & Scenarios (`tests/test_pipeline_repeatability_and_scenarios.py` - 18 tests)**: Full input-to-HTML/export pipeline execution (`/api/jobs` -> polling -> disk reload -> HTML -> Markdown export), pipeline repetition (3 passes: fresh db, repeated cache hit, force refresh v2), unfamiliar app corroboration with NewCloud fixtures and absence of cross-product text, Composio custom/managed/unsupported modes, missing keys, delayed call timeouts, cancellation failure modes, ambiguous name candidate reload, idempotent seed imports across 3 server restarts, and concurrency guard.
- **Full Review Finding Regressions (`tests/test_full_review_findings.py` - 16 tests)**: Dedicated boundary regressions for Findings 1–9 from `docs/FULL_CODE_REVIEW.md`: CLI timeout & error exit codes, reviewed import rejection of unsourced claims and synthetic attempts, two-path Composio promotion with unique step IDs, gate reversal claim text updates with history preservation, base version identity and scenario mismatch rejections, canonical attempt loading with qualified product names, preparation step idempotence and URL preservation, retry link preservation of user-typed website and scenario parameters, rendered attempt references across Markdown and HTML, and conflicting model guess rejection preserving user domain and coherent name.
- **Gemini Free-Tier Path (`tests/test_gemini_path.py` - 10 tests)**: Research multi-turn loop with Tavily search and markdown extraction, failed read handling, budget exhaustion, unseen URL refusal, synthesis `max_tokens` truncation retry, schema rejection fallback with repair, 429 rate limit backoff retry and safe error sanitization, structured model resolver, and `/health` provider reporting.

All 231 tests pass cleanly:
```
======================= 231 passed, 1 warning in 29.72s =======================
```

- **Browser DOM Check (`scripts/verify_browser.py`)**: Local verification script that launches headless Chrome to inspect server-rendered DOM elements and form attributes (note: targets a hardcoded Windows Chrome path `C:\Program Files\Google\Chrome\Application\chrome.exe`).
- **Free-Tier Smoke Check (`scripts/smoke_free_tier.py`)**: Local verification script testing live `generate()`, `search()`, `extract()`, and JSON schema synthesis once keys are placed in `.env`.

## Validation & Verification
See `docs/VERIFICATION.md` for the 5-part verification report covering the setup attempt workflow, the 15-claim audit, live-run logging, documented vs observed evidence boundaries, and remaining live operational prerequisites. See `docs/DECISIONS.md` for architectural decision records.

## Deployment

OAuthPath is an ASGI FastAPI application with SQLite persistence and background task handling.

1. **Environment Setup:** Set `LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `TAVILY_API_KEY`, and `COMPOSIO_API_KEY` in your PaaS environment settings (e.g. Render).
2. **Persistent Storage / Re-import:** On free hosting platforms where disk storage is ephemeral (e.g. Render free tier), machine-generated draft guides reset on service restart, but reviewed library guides in `data/reviewed/` automatically re-import idempotently on startup.
3. **Ingress & Reverse Proxy:** Configure `FORWARDED_ALLOW_IPS=*` on Render/Railway.
4. **Execution:**
   ```bash
   python serve.py
   ```

## Limitations
- **Cold start:** On free PaaS hosting (Render free tier), the instance spins down after 15 minutes of inactivity; the initial request may take ~1 minute to wake.
- **Ephemeral disk:** On free hosting, dynamically generated draft guides reset on container restart. The curated, human-reviewed library (`data/reviewed/`) is bundled in git and re-imported automatically on every launch.
- **Free-tier rate limits:** Gemini free tier has tight per-minute request quotas; `MAX_CONCURRENT_JOBS` is defaulted to 1, and 429 responses trigger an automatic backoff retry before presenting a clean retry message.
- **Curated breadth:** Currently provides 3 complete, audited reference guides (GitHub, Slack, Gmail), rather than 100 unverified stubs.
- **No completed third-party approvals:** Provider developer console approvals, restricted scope grants, and refresh token expiry are documented from official provider policies rather than simulated or fabricated.
- **Privacy notice for free tiers:** Google AI Studio terms allow data sent over free-tier APIs to be used for model improvement. In OAuthPath, only public developer documentation, public scope strings, and public provider URLs are retrieved and processed — no private customer credentials or confidential data are ever submitted.

## Expanding to 100 apps
Same schema and pipeline. Prioritize by developer demand (Composio usage),
missing managed apps and gate complexity; generate drafts in batches; review the
top apps by hand; re-check on a schedule and when provider changelogs move;
label freshness on every guide. A catalog entry is not a guide.

## Stack
Python 3.10+, FastAPI, Jinja2 templates, plain CSS/JS, SQLite, Pydantic v2,
Google GenAI SDK (`google-genai==2.33.0`, Gemini 3.6 Flash / 3.5 Flash Lite),
Tavily Search & Extraction (`tavily-python==0.8.5`),
Anthropic Python SDK (`anthropic==2.4.0`, Claude Sonnet 5.5 optional fallback),
httpx, markdown-it-py, thefuzz, tldextract, python-dotenv.

All dependency versions pinned in `requirements.txt` and verified with automated test suite.
