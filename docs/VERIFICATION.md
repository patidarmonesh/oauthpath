# OAuthPath Verification & Provenance

**Date:** 4 November 2026  
**Audience:** Composio Engineering Reviewer  
**Scope:** Verification procedures, claim audit criteria, execution workflows, documentation boundaries, and live operational prerequisites.

---

## 1. The Setup Attempt Run

### Purpose & Architecture
The setup attempt workflow tests and documents custom OAuth credentials against a provider using the Composio SDK. It produces structured observation records stored in `data/attempts/` which provide experimental evidence (`observed` basis) for downstream guides.

### Prerequisites & Credentials
To execute an authentic setup attempt against a provider (e.g., GitHub):
1. Register a test OAuth app in the provider's developer console (e.g., [GitHub Developer Settings](https://github.com/settings/developers) -> OAuth Apps) with:
   - Application Name: `OAuthPath Test`
   - Homepage URL: `https://composio.dev`
   - Authorization Callback URL: `https://backend.composio.dev/api/v3/toolkits/auth/callback`
2. Obtain the OAuth app credentials (`Client ID` and `Client Secret`).
3. Place these in `.env`:
   ```env
   COMPOSIO_API_KEY=your_composio_api_key
   GITHUB_CLIENT_ID=your_github_client_id
   GITHUB_CLIENT_SECRET=your_github_client_secret
   ```

### Execution Command
Execute the test runner script using the toolkit name and corresponding credential prefix:
```powershell
python scripts/composio_test.py --toolkit github --prefix GITHUB
```

### Script Execution Flow & SDK Invariants
1. **Toolkit Verification:** Queries `client.toolkits.get(args.toolkit)` to retrieve supported auth schemes from `auth_config_details` or `composio_managed_auth_schemes`.
2. **Auth Scheme Normalization:** Uppercases the auth mode scheme (`auth_scheme = oauth_modes[0].upper()`), ensuring `OAUTH2` or `OAUTH1` as required by the Composio API.
3. **Custom Auth Configuration:** Calls `client.auth_configs.create(args.toolkit, options)` with:
   ```python
   options = {
       "type": "use_custom_auth",
       "auth_scheme": auth_scheme,
       "name": f"{args.toolkit}-custom-{int(time.time())}",
       "credentials": {
           "client_id": client_id,
           "client_secret": client_secret,
           "oauth_redirect_uri": args.redirect_uri,  # https://backend.composio.dev/api/v3/toolkits/auth/callback
       },
   }
   ```
4. **Connect Link Creation:** Calls `client.connected_accounts.link(user_id=user_id, auth_config_id=auth_config_id)`.
5. **Connection Polling:** Prompts user to approve in browser, then awaits connection with `client.connected_accounts.wait_for_connection(id=req_id, timeout=300)`.
6. **Redaction & Sanitization:** All console outputs and written records pass through `_sanitize_secrets()`, which redacts Authorization headers, Bearer tokens, query parameters, and configured secrets (`GITHUB_CLIENT_SECRET`, etc.).
7. **Attempt Record Persistence:** Saved to `data/attempts/A-{toolkit}-{timestamp}.json` (e.g. `data/attempts/A-github-20261104-180000.json`) with fields:
   - `id`: `A-github-<timestamp>`
   - `actor`: `"assisted"`
   - `provider`: `"github"`
   - `scenario`: `"Test connection via custom or managed auth for ..."`
   - `action`: `"Create auth config and connect test user for github"`
   - `start_time` / `end_time`: ISO 8601 UTC timestamps
   - `environment`: `"Local test account"`
   - `observed_result`: Sanitized observation text
   - `status`: `"succeeded"` or `"failed"` (the script exits with code 1 if not succeeded)
   - `artifact_path`: `None`
   - `limitation`:
     - On success: `"Proves one test-user connection; not refresh behaviour, org access or production approval"`
     - On timeout: `"Connection timed out waiting for user approval; flow was not completed"`
     - On error: `"Connection attempt failed during API/SDK execution; credentials or network blocked"`

*(Note: There is no `duration_seconds` field in the attempt record schema; elapsed time is reflected via `start_time` and `end_time`.)*

### Execution Status
Not run yet with real credentials. The test suite verifies this script via simulated SDK mocks (`test_composio_test_script_simulated_execution`). Once run with real GitHub and Composio credentials, the terminal trace and recorded JSON will be inserted here.

---

## 2. The 15-Claim Audit

### Purpose & Promotion Workflow
Machine-generated guides are initially created as drafts (version 1) with status `draft` or `partial`. A draft guide is elevated to an immutable, published version (`reviewed`, version 2) in the library only through an explicit human-in-the-loop audit of its individual claims using `scripts/promote.py`.

### Schema & Review Field Specifications
During the audit of a candidate draft containing claims (identified as `K1, K2, ...` per schema and synthesis prompt):
- **Audited Claims (`review.audited_claim_ids`):** An explicit list of claim IDs (e.g. `["K1", "K2", "K3", "K4", "K5"]`) that were manually inspected against primary provider documentation.
- **Review Result (`review_result`):** Each audited claim must be marked as one of:
  - `supported`: Primary provider documentation directly confirms the claim text and cited excerpt.
  - `contradicted`: Official documentation contradicts the claim.
  - `insufficient`: Cited sources do not provide clear evidence for the claim.
- **Support Note (`support_note`):** A substantive, non-empty explanation written by the auditor citing specific developer documentation sections or policies (e.g. `"Confirmed in GitHub Docs under 'Authorizing OAuth Apps' section on scopes"`).
- **Reviewer Identity (`reviewer`):** The auditor's verified name or handle.
- **Reviewed At (`reviewed_at`):** ISO 8601 UTC timestamp of the audit.

### Step-by-Step Audit Procedure
1. **Export Draft Working Copy:**
   ```powershell
   python scripts/promote.py <guide_id> 1 --export-only
   ```
   This generates an editable audit template at `data/review-copy-<guide_id>-v1.json`.
2. **Conduct Manual Verification:**
   Open the exported JSON file in an editor. Inspect each claim:
   - Check the `claim.text` against the cited `sources` and live provider documentation URLs.
   - Set `claim.review_result` (`"supported"`, `"contradicted"`, or `"insufficient"`).
   - Enter your audit explanation in `claim.support_note`.
   - Update `review.audited_claim_ids` with all reviewed claim IDs.
3. **Ingest Reviewed Guide:**
   ```powershell
   python scripts/promote.py --from-file data/review-copy-<guide_id>-v1.json --reviewer "Monesh Patidar" --claims K1,K2,K3,K4,K5
   ```

### Ingestion Boundary Invariants (`validate_reviewed_record`)
The promotion script and ingestion loader strictly enforce:
- No claim can be marked `supported` without a non-empty `support_note` and valid source URL.
- No attempt claim can cite a non-existent or synthetic attempt ID.
- Rejects duplicate IDs (sources, claims, steps, attempts).
- Rejects dangerous or malformed URLs (e.g., `javascript:`, raw IPs, unresolvable localhost).
- Validates all schema enums (`GateKind`, `GateState`, `OAuthSupport`, `ClaimBasis`, `ReviewResult`).
- Enforces non-mutating ingestion: the ingested record is stored verbatim without model rewrites or synthetic re-validations.

### Execution Status
Not done yet: pending live generation of a draft guide to audit and promote.

---

## 3. The Live-Run Log

### Execution Methods
Live guide generation can be initiated either via terminal CLI or through the web interface:

**Via CLI:**
```powershell
python -m app.cli "GitHub" --feature "sync repositories" --audience "external customers"
```

**Via Web Interface:**
1. Start the server: `python serve.py` (or `uvicorn app.main:app --host 127.0.0.1 --port 8000`).
2. Open `http://127.0.0.1:8000` in a browser.
3. Submit App Name (`GitHub`), Feature (`sync repositories`), Audience (`external customers`), and optional Website.
4. The client redirects to `/jobs/{job_id}`, polling `GET /api/jobs/{job_id}` until completion.

### Pipeline Progression & Execution Bounds
During execution, the pipeline runs under strict architectural bounds:
- **Total Job Timeout:** `JOB_TIMEOUT_SECONDS = 420` seconds.
- **Research Phase Deadline:** 55% of job deadline (`~231` seconds).
- **CLI Logging:** Real-time progression events are printed to `stdout` (`print()`).
- **Web UI Polling:** Browser polls `/api/jobs/{id}` for job status updates.
- **Phase 1: Provider Identity Resolution (`app/resolver.py`):**
  - Resolves canonical name and official domain.
  - When user provides a website, user domain is preserved and conflicting model suggestions are recorded as warnings.
  - Initial identity status: `assumed`.
- **Phase 2: Composio Toolkit Lookup (`app/resolver.py`):**
  - Bounded non-blocking query (15s timeout).
  - Queries `GET https://backend.composio.dev/api/v3/toolkits` and `GET https://backend.composio.dev/api/v3/toolkits/{slug}`.
  - Validates exact word boundaries and checks managed OAuth support.
- **Phase 3: Attempt Association (`app/pipeline.py`):**
  - Scans `data/attempts/` for genuine setup observations matching provider domain.
- **Phase 4: Multi-Turn Research Agent (`app/research.py`):**
  - Tool-using loop bounded by 231s budget.
  - Budgets: `MAX_TURNS = 8` turns, `MAX_SEARCHES = 6` searches, `MAX_FETCHES = 8` page reads, `MAX_FETCH_TOKENS = 10000` tokens per page.
  - If a call nears the deadline after findings exist, findings are kept, `research_stats["research_timed_out"] = True`, and the guide is saved with `status="partial"`.
- **Phase 5: Identity Corroboration:**
  - Upon reading official documentation pages, provider identity is promoted from `assumed` to `resolved`.
- **Phase 6: Synthesis (`app/synthesis.py`):**
  - Structured JSON draft extraction from research findings, citing only collected sources (`S1`, `S2`, `C1`...) and claims (`K1`, `K2`...).
- **Phase 7: Validation (§6.5 & `app/validation.py`):**
  - Normalizes fields, verifies URL schemes, validates claim citations, adds missing critical gates as unknowns, validates Composio paths, verifies excerpt matching (`thefuzz.fuzz.partial_ratio >= 90`), computes categorical difficulty label (`low`, `medium`, `high`, `provisional`), and checks review integrity.
- **Phase 8: Storage & Rendering:**
  - Stores immutable version in SQLite (`data/oauthpath.db`).
  - Pre-renders server-side HTML and provides exact Markdown export via `GET /guides/{guide_id}/versions/{version}.md`.

### Execution Status
Not run yet with real credentials. The pipeline has been comprehensively verified across 221 automated tests across 8 test modules with simulated fixtures. Once run with live API keys, the actual terminal log will be captured here.

Planned terminal log output format:
```
Generating guide for: GitHub
  Feature: sync repositories
  Audience: external customers

  → Resolving provider identity
  → Looking up Composio toolkit
  → Starting research phase
  → Synthesizing guide from findings
  → Validating guide
  → Saving guide
  → Saved as version 1

✓ Guide saved: github-external_customers-... v1 (draft)
  Provider: GitHub
  OAuth support: supported
  Sources: 4 (3 searches, 3 reads)
  Claims: 15
  Duration: 24.3s
```

---

## 4. What's Only Sourced

### Documentation vs Experimental Observation
The research agent gathers facts from published documentation. These facts are classified under the `documented` basis in accordance with §6.5:

| Information Type | What Primary Sources Provide (`documented`) | What Requires Observation / Audit (`observed`) |
|---|---|---|
| **App Registration** | Developer portal URLs, required app fields, redirect URI formats, token lifetimes | Whether registration requires phone verification or domain TXT record validation on a specific account |
| **OAuth Scopes** | Scope names, permissions granted, sensitivity tiers (e.g. read vs write) | Actual authorization prompt UI and whether granular scope selection is permitted |
| **Review Policies** | Written app review requirements, guidelines, security questionnaire docs | Turnaround duration in practice, reviewer pushback, and undocumented organizational approval gates |
| **Composio Modes** | Published Composio integration docs, catalog listings, auth scheme types | Successful runtime token exchange via `composio_test.py` with custom client credentials |
| **Costs & Plans** | Official pricing tiers, developer program fees, enterprise requirements | Hidden API usage fees or custom contract requirements |

### Provenance Safeguards
- **Excerpt Verification:** Claims cited with `documented` basis are validated by checking that the cited excerpt exists in the fetched page text (`thefuzz.fuzz.partial_ratio >= 90`).
- **Uncited Downgrading:** Any claim citing sources that were not actually fetched, or failing the excerpt check (ratio < 90), is flagged with a warning or downgraded to `unknown`.
- **Advice Qualification:** If supporting evidence for a launch claim is missing or unconfirmed, validation replaces affirmative launch advice with: `"Verify provider OAuth and review requirements before public launch"`.

---

## 5. What's Not Done

### Transparent Operational Disclosures
To maintain strict evidence provenance and honest verification, the following items require external credentials or live manual actions:

1. **Not done yet: Live External API Credentials:**
   - **Anthropic API:** Live research and synthesis requires setting `ANTHROPIC_API_KEY` in `.env` for `claude-sonnet-5-5`.
   - **Composio API & GitHub OAuth App:** Live toolkit testing requires setting `COMPOSIO_API_KEY`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET` in `.env` with the GitHub app configured with callback `https://backend.composio.dev/api/v3/toolkits/auth/callback`.
2. **Not done yet: Live Setup Attempt Execution:**
   - Running `python scripts/composio_test.py --toolkit github --prefix GITHUB` to produce the first authentic observation in `data/attempts/`.
3. **Not done yet: Live Guide Generation & 5-Claim Human Audit:**
   - Generating a live GitHub guide via `python -m app.cli "GitHub"`, reviewing 5 consequential claims against official docs, and promoting via `python scripts/promote.py --from-file ... --claims K1,K2,K3,K4,K5` to populate `data/reviewed/`.
4. **Not done yet: Cloud Production Deployment:**
   - Deploying OAuthPath to cloud hosting (e.g. Fly.io, Railway, or VPS) with persistent SQLite volume mounting, configuring `FORWARDED_ALLOW_IPS`, and updating the live URL in `README.md`.
5. **No Fabricated Data:**
   - `data/attempts/` and `data/reviewed/` contain only `.gitkeep` until real operations are executed. No fake execution traces or invented credentials exist in this repository.
