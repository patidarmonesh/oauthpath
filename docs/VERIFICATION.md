# OAuthPath verification

## Provenance and division of checks

### Developer verification (Monesh Patidar)
The developer personally executed the browser OAuth consent flow on 4 November 2026. The assisted setup run connected one test user through the Composio SDK and recorded an active account (`ca_mRIPTDQRWMQm`). Three earlier attempts recorded authentication failures (`401 Invalid API key`). This demonstrates the handoff from automated setup to the user's browser consent. Production approval, organization access, and token refresh remain unverified. [Successful attempt record](../data/attempts/A-github-20261104-182309.json)

The developer confirmed that a full human claim audit for all provider requirements remains pending. Accordingly, all bundled guides retain honest provenance labels (`status: "draft"`, `reviewer: null`, displayed as `not human-reviewed`).

### Assistant investigation and primary source checks
The assistant verified provider documentation against official developer portals on 5 November 2026:
- **Slack Marketplace installation requirement:** Checked against the [official Slack notice](https://docs.slack.dev/changelog/2026/09/01/slack-marketplace-install-requirement/). Replaced paraphrasing with the exact published passage: *"As of July 2026, we require that apps submitting to the Slack Marketplace have at least 10 installations on active workspaces, up from the temporary lower threshold. Your app needs to stay at 10 or more installed workspaces throughout the entire duration of the review."* Updated in `slack-e07c7c36` v4.
- **Gmail scope classifications and CASA assessment:** Checked against the [official Gmail scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes) and User Data Policy. Confirmed that `https://www.googleapis.com/auth/gmail.send` is sensitive (standard verification) and `https://www.googleapis.com/auth/gmail.readonly` is restricted. Delineated that send-only access avoids restricted review, whereas storing or processing restricted email data on servers mandates an annual Cloud Application Security Assessment (CASA Tier 2) by an authorized assessor. Updated in `gmail-google-b419b8d4` v4 with timing estimates of 14 to 56 calendar days (2 to 8 weeks).

| Provider | Draft claim vs Primary source finding | Status in v4 |
|---|---|---|
| **Slack** | Old draft claimed 5 active installs for Marketplace submission. Contradicted by official changelog notice requiring 10 active workspace installations maintained throughout review. | Fixed with exact quote citation (`S13`, `K11`) in production step and gate. |
| **Gmail** | Old draft classified both send and read as sensitive. Contradicted by Google scope reference: `gmail.send` is sensitive; `gmail.readonly` is restricted and requires CASA Tier 2 when handling server-side data. | Fixed across summary, blockers, claims, paths, gates, and timing estimates (14–56 days). |

Older local drafts (v1/v2) have been explicitly retracted. Both v3 records and corrected v4 records are bundled in `data/reviewed/`.

## Execution and test results

- **Automated test suite:** 259 passed, 0 failed, 1 deprecation warning in 50.30 seconds. Real service credentials were cleared in the test process and an isolated temporary database was used.
- **Fresh database startup:** Verified with an empty temporary SQLite database. On startup, `_import_reviewed_seeds()` populated all 3 providers. Homepage (`/`) rendered all guides (no "No guides yet"). Share links correctly redirected to v4 for Slack and Gmail, and v3 for GitHub.
- **Existing database copy startup:** Verified with a copy of `data/oauthpath.db`. Confirmed that v4 is displayed as the latest version on the homepage and redirects, while v3 remains accessible at `/guides/{id}/versions/3`.
- **Export formats:** Verified HTTP 200 responses for HTML pages, Markdown exports (`.md`), and JSON endpoints (`/api/...`) on both database instances.
- **Live generation:** A live Linear job completed in ~94 seconds with working HTML, Markdown, and JSON. A subsequent run reached free-tier rate limits, displaying an actionable retry UI and preserving user inputs. A hosted live unfamiliar-app verification run and public deployment links are pending.
