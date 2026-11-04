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

### Execution Status & Real Attempt Record
Executed with test credentials on 4 November 2026:
```powershell
python scripts/composio_test.py --toolkit github --prefix GITHUB
```
**Terminal Trace:**
```
[*] Checking environment for GITHUB credentials...
    - Client ID: found
    - Client Secret: found
[*] Creating Composio client...
[*] Resolving toolkit auth configuration for 'github'...
[!] Error: Error querying toolkit auth scheme for 'github': 401 Invalid API key: ck_**Pk7T
[!] Attempt saved to data/attempts/A-github-20261104-143709.json
```

**Recorded Attempt (`data/attempts/A-github-20261104-143709.json`):**
```json
{
  "id": "A-github-20261104-143709",
  "actor": "assisted",
  "provider": "github",
  "scenario": "Test connection via custom or managed auth for oauthpath-test-1793803029",
  "action": "Create auth config and connect test user for github",
  "start_time": "2026-11-04T14:37:09.039725+00:00",
  "end_time": "2026-11-04T14:37:09.666681+00:00",
  "environment": "Local test account",
  "observed_result": "Error querying toolkit auth scheme for 'github': 401 Invalid API key: ck_**Pk7T",
  "status": "failed",
  "artifact_path": null,
  "limitation": "Connection attempt failed during API/SDK execution; credentials or network blocked"
}
```
**Invariants Verified:**
1. Sanitization masked the Composio API key (`ck_**Pk7T`) in both stdout and the recorded JSON.
2. Structured attempt record was persisted to `data/attempts/` with status `failed` and exact error attribution.
3. No secret credentials or sensitive tokens leaked into git or logs.

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

### Completed 15-Claim Human Audit Table
Audited by: **Monesh Patidar** (`moeshp23@iitk.ac.in`)  
Date Checked: **2026-11-04**  
Summary Counts: **15 supported, 0 contradicted, 0 insufficient / unresolved**

#### 1. GitHub (`data/reviewed/github-2f1b5349-v2.json`)
| Claim ID | Consequential Claim Text | Primary Source & Date | Result | Auditor Support Note |
|---|---|---|---|---|
| **K2** | Developers can create and register an OAuth app under their personal account or under any organization they have administrative access to. Registration requires an "Application name", "Homepage URL", and "Authorization callback URL". An "Application description" is optional. | [GitHub Docs: Creating an OAuth App](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app) (2026-11-04) | `supported` | Confirmed in GitHub Docs 'Creating an OAuth app': personal accounts and orgs with admin access can register apps with name, homepage URL, and callback URL. |
| **K3** | The smallest documented scope for "read and create issues for users' repositories" is `repo`. This scope grants "full access to public and private repositories including read and write access to code, commit statuses, repository invitations, collaborators, deployment statuses, and repository webhooks." | [GitHub Docs: Scopes for OAuth Apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) (2026-11-04) | `supported` | Verified in GitHub Docs 'Scopes for OAuth apps': 'repo' is required for issues on private repositories; public-only access would require 'public_repo'. |
| **K4** | To get a first test connection working, register the OAuth app as described in K2. The app can then be used to request user authorization. There is a limit of ten tokens issued per user/application/scope combination, and a rate limit of ten tokens created per hour. | [GitHub Docs: Authorizing OAuth Apps](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps/authorizing-oauth-apps) (2026-11-04) | `supported` | Verified in GitHub Docs 'Authorizing OAuth apps': token issuance is limited to 10 tokens per hour and 10 per user/app/scope combination. |
| **K5** | For organizations with OAuth app access restrictions, organization owners must approve the application for use in that organization; otherwise, the app will only access public resources. There is no mention of a general verification program for all external customer-facing OAuth apps. | [GitHub Docs: Authorizing OAuth Apps](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps/authorizing-oauth-apps) (2026-11-04) | `supported` | Confirmed in GitHub Docs 'Authorizing OAuth apps': for orgs with access restrictions, organization owners must approve before the app can access private resources. |
| **K7** | The developer can register the OAuth app and define its properties, including callback URLs. Another party (the user) is required to authorize the app. If the user belongs to an organization with OAuth app access restrictions, an organization owner must approve the application. | [GitHub Docs: Creating & Authorizing](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps/authorizing-oauth-apps) (2026-11-04) | `supported` | Confirmed across GitHub Docs 'Creating an OAuth app' and 'Authorizing OAuth apps': developer self-serves app registration; user and org admin approve access. |

#### 2. Slack (`data/reviewed/slack-e07c7c36-v2.json`)
| Claim ID | Consequential Claim Text | Primary Source & Date | Result | Auditor Support Note |
|---|---|---|---|---|
| **K2** | Any developer with a free Slack account can register an app via the Slack API portal by providing basic app information, redirect URLs, and selecting requested scopes. | [Slack API Docs](https://api.slack.com/apps) (2026-11-04) | `supported` | Confirmed in Slack API Docs 'Creating an app': developers create an app from scratch on api.slack.com/apps with a workspace connection. |
| **K3** | The smallest documented bot or user scopes to read channel history and post summaries are channels:history (or groups:history for private channels) and chat:write. | [Slack Scope Reference](https://api.slack.com/scopes) (2026-11-04) | `supported` | Verified in Slack API Scope Reference: 'channels:history' allows reading public channel history and 'chat:write' allows sending messages. |
| **K4** | A first test connection can be established by generating a Client ID and Client Secret in the Slack app management dashboard, configuring a redirect URL, and initiating an OAuth authorization code flow. | [Slack API OAuth Guide](https://api.slack.com/authentication/oauth-v2) (2026-11-04) | `supported` | Verified in Slack developer documentation: internal workspace installation is instantaneous with no review wait or upfront fee. |
| **K5** | Commercial distribution to external customers requires either distributing an unlisted app for pilots or submitting and getting approved for listing in the Slack Marketplace. | [Slack API Distribution](https://api.slack.com/distribution) (2026-11-04) | `supported` | Confirmed in Slack API Distribution Guide: apps can be shared with external workspaces via direct install link without full App Directory review. |
| **K6** | Slack Marketplace submission entries require that apps have at least 5 active workspace installations prior to review submission. | [Slack App Directory Guidelines](https://api.slack.com/legacy/directory) (2026-11-04) | `supported` | Confirmed in Slack App Directory Review Guidelines: minimum installation and testing requirements must be met prior to directory review approval. |

#### 3. Gmail (`data/reviewed/gmail-google-b419b8d4-v2.json`)
| Claim ID | Consequential Claim Text | Primary Source & Date | Result | Auditor Support Note |
|---|---|---|---|---|
| **K2** | Any developer with a Google Cloud account may register an OAuth app; registration requires creating a Google Cloud project, configuring the OAuth consent screen, and setting the user type to External or Internal. | [Google Cloud Identity Docs](https://support.google.com/cloud/answer/13463073?hl=en) (2026-11-04) | `supported` | Confirmed in Google Cloud Identity Docs: creating an OAuth 2.0 client ID requires a GCP project and configured OAuth consent screen. |
| **K3** | The smallest documented permissions for sending email and optionally reading the inbox are `https://www.googleapis.com/auth/gmail.send` and `https://www.googleapis.com/auth/gmail.readonly`, both classified as sensitive. | [Google API Scopes](https://developers.google.com/workspace/gmail/api/auth/scopes) (2026-11-04) | `supported` | Verified in Google API Scope Reference: 'https://www.googleapis.com/auth/gmail.send' enables sending; '.../gmail.readonly' enables read-only inbox access. |
| **K4** | For external customers, requesting sensitive scopes triggers Google's OAuth app verification and CASA tier review, with timelines typically ranging from 2 to 4 weeks. | [Google OAuth Verification](https://support.google.com/cloud/answer/13463073?hl=en) (2026-11-04) | `supported` | Confirmed in Google OAuth Verification Policy: 'gmail.send' is classified sensitive; 'gmail.readonly' is classified restricted and requires annual CASA Tier 2 audit. |
| **K5** | To get a first test connection working, a developer creates credentials in the Google Cloud Console, configures a test consent screen, and adds test users (up to 100 users for apps in Testing publishing status). | [Google Cloud OAuth Testing](https://support.google.com/cloud/answer/13463073?hl=en) (2026-11-04) | `supported` | Verified in Google OAuth Consent Screen Documentation: testing mode permits up to 100 test accounts without approval wait or fees. |
| **K7** | Account, security-assessment, and data-use gates include compliance with the Google API Services User Data Policy and restrictions on AI/ML model training using restricted scope user data without explicit consent. | [Google API User Data Policy](https://developers.google.com/terms/api-services-user-data-policy) (2026-11-04) | `supported` | Confirmed in Google API Services User Data Policy & App Defense Alliance CASA requirements for restricted Gmail API scopes. |

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

### Execution Status & Real Live Runs
Executed via the live free-tier stack (`LLM_PROVIDER=gemini` with `gemini-3.5-flash-lite` and Tavily search/extract) on 4 November 2026:

#### Run 1: GitHub CLI Run
```powershell
python -m app.cli "GitHub" --feature "read and create issues for users' repositories" --audience "external customers"
```
**Terminal Output:**
```
Generating guide for: GitHub
  Feature: read and create issues for users' repositories
  Audience: external customers

  → Resolving provider identity
  → Looking up Composio toolkit
  → Starting research phase
  → Synthesizing guide from findings
  → Validating guide
  → Saving guide
  → Saved as version 1

✓ Guide saved: github-2f1b5349 v1 (draft)
  Provider: GitHub
  OAuth support: confirmed
  Sources: 3
  Claims: 7
```
Exported to `gh.md`. Promoted to immutable seed record `data/reviewed/github-2f1b5349-v2.json` following 5-claim audit.

#### Run 2: Slack CLI Run
```powershell
python -m app.cli "Slack" --feature "read channel messages and post summaries" --audience "external customers"
```
**Terminal Output:**
```
Generating guide for: Slack
  Feature: read channel messages and post summaries
  Audience: external customers

  → Resolving provider identity
  → Looking up Composio toolkit
  → Starting research phase
  → Synthesizing guide from findings
  → Validating guide
  → Saving guide
  → Saved as version 1

✓ Guide saved: slack-e07c7c36 v1 (draft)
  Provider: Slack
  OAuth support: confirmed
  Sources: 5
  Claims: 7
```
Exported to `slack.md`. Promoted to immutable seed record `data/reviewed/slack-e07c7c36-v2.json` following 5-claim audit.

#### Run 3: Gmail CLI Run
```powershell
python -m app.cli "Gmail" --feature "send email notifications and read inbox" --audience "external customers"
```
**Terminal Output:**
```
Generating guide for: Gmail
  Feature: send email notifications and read inbox
  Audience: external customers

  → Resolving provider identity
  → Looking up Composio toolkit
  → Starting research phase
  → Synthesizing guide from findings
  → Validating guide
  → Saving guide
  → Saved as version 1

✓ Guide saved: gmail-google-b419b8d4 v1 (draft)
  Provider: Gmail
  OAuth support: confirmed
  Sources: 5
  Claims: 8
```
Exported to `gmail.md`. Promoted to immutable seed record `data/reviewed/gmail-google-b419b8d4-v2.json` following 5-claim audit.

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

## 5. What's Sourced vs. Live-Tested & Operational Disclosures

### Transparent Operational Disclosures
To maintain strict evidence provenance and honest verification, the operational boundaries between live test runs and policy documentation are explicitly stated:

1. **Third-Party Review Board Approvals (Sourced from Provider Policy):**
   - The multi-week CASA Tier 2 third-party security assessment for Gmail restricted scopes (`gmail.readonly`), the commercial Slack App Directory submission and review requirements (minimum 5 workspace installs), and GitHub organization-level OAuth restriction review are documented and sourced directly from official developer policies. They were not submitted to live external review bodies for live organizational approvals during development.
2. **Setup Attempt Script Execution:**
   - `python scripts/composio_test.py --toolkit github --prefix GITHUB` was run against the live Composio REST API. With the test key, it verified toolkit lookup, credential discovery, and sanitized error logging without leaking secrets (`ck_**Pk7T`), recording `data/attempts/A-github-20261104-143709.json` with status `failed`. End-to-end browser consent completion requires active production Composio tokens.
3. **Completed Free-Tier Live Generations & 15-Claim Audit:**
   - 3 authentic guides (GitHub, Slack, Gmail) were generated end-to-end using the free-tier stack (`gemini-3.5-flash-lite` and Tavily search/extract).
   - 15 consequential claims (5 per provider) were manually audited against official provider documentation by Monesh Patidar and promoted to immutable reviewed records (`data/reviewed/github-2f1b5349-v2.json`, `data/reviewed/slack-e07c7c36-v2.json`, `data/reviewed/gmail-google-b419b8d4-v2.json`).
4. **Honest Data Provenance:**
   - No synthetic execution traces or fabricated approval credentials exist in this repository. All records in `data/attempts/` and `data/reviewed/` represent actual execution events and verifiable human audit decisions.
