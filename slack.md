# Slack — Read channel history and post summaries in customers' workspaces

v1 · draft · Researched 2026-11-04
Assumptions: Feature: Read channel history and post summaries in customers' workspaces · Audience: external customers · Deployment: server-side

Evidence: 7 documented · 0 observed · 0 estimated · 0 unknown · 0 sources read · not human-reviewed

Composio: lookup failed — unknown, which is not the same as unavailable

## Readiness summary

- Slack supports third-party OAuth flows, enabling external apps to be installed on customers' workspaces. [K1]
- Any developer can register a Slack App via the Slack API portal and request scopes for reading channel history and posting messages. [K2, K3]
- Commercial distribution to external customers requires either an unlisted app for pilots or an approved listing in the Slack Marketplace. [K5]

**Difficulty:** Requires coordination (provisional)
  - Required: App Review
  - Required: Cap Before Approval
  - Not established: Paid Access
  - Not established: Customer Admin Action
  - Not established: Partner Program
  - Not established: Security Assessment
  - Not established: Business Or Identity Verification
  - Based on production path: own_app_production

## Next action

**Register a Slack App via the Slack API portal, configure the redirect URI, and request the required scopes.**
Owner: You
Link: [https://api.slack.com/](https://api.slack.com/)
Done when: Slack app is created and credentials (Client ID and Client Secret) are successfully generated.
Evidence: [K2, K4]

## Paths

| Path | Difficulty | Setup effort | Waiting |
|---|---|---|---|
| Unlisted App Testing / Pilot | Straightforward | unknown — App registration and basic OAuth callback setup. | unknown — No waiting period for unlisted testing apps. |
| Slack Marketplace Production Distribution | Requires coordination (provisional) | unknown — Effort varies based on verification and installation accumulation. | unknown — Marketplace review times are variable. |

### Unlisted App Testing / Pilot

*Suitable for testing out your app by running a pilot for early customers.*

**Gates:**
- App Review: Not Required [K5]
  Unlisted apps do not require Slack Marketplace approval for pilot usage.

**Prerequisites:**
- A free Slack account to access the Slack API portal. (Owner: You) [K2]

**Steps:**
1. **Register Slack App** [You] [K2, K4]
   Create an app in the Slack API portal and configure redirect URLs.
   *Done when: Client ID and Client Secret are available.*
   Effort: unknown — Standard developer account setup and basic form filling.
   Link: https://api.slack.com/

### Slack Marketplace Production Distribution

*Required for commercial distribution of apps intended for external customers.*

**Gates:**
- App Review: Required [K5, K6]
  Marketplace listing requires review and compliance with review guidelines.
- Cap Before Approval: Required [K6]
  Must have at least 5 active workspace installations prior to review submission.
- Paid Access: Unknown
  Not established by research
- Customer Admin Action: Unknown
  Not established by research
- Partner Program: Unknown
  Not established by research
- Security Assessment: Unknown
  Not established by research
- Business Or Identity Verification: Unknown
  Not established by research

**Prerequisites:**
- Achieve at least 5 active workspace installations. (Owner: You) [K6]

**Steps:**
1. **Submit to Slack Marketplace** [You] [K5, K6, K7]
   Ensure 5 active workspace installations are present, adhere to data usage restrictions, and submit the app for Marketplace review.
   *Done when: App is approved and listed on the Slack Marketplace.*
   Effort: unknown — Effort depends on review requirements and gathering installations.
   Link: https://api.slack.com/slack-marketplace/guidelines

## Scopes

| Scope | Purpose | Classification | Claims |
|---|---|---|---|
| `channels:history` | Read message history from public channels | bot or user token scope | K3 |
| `groups:history` | Read message history from private channels | bot or user token scope | K3 |
| `chat:write` | Post messages (summaries) to channels | bot token scope | K3 |

## Launch blockers

- **Failing to meet the active installation requirement prior to Marketplace review**: App submission for commercial distribution will not be accepted. [K6]
  Resolution: Acquire at least 5 active workspace installations via pilots or unlisted distribution. (Owner: You)

## What an agent can help with

- **Scope Mapping**: Assist in identifying and requesting the necessary Slack API scopes.
- **OAuth Flow Configuration**: Draft configuration settings for the OAuth authorization code flow.

## Unknowns

- **Whether Slack explicitly prohibits using channel history data obtained via API scopes for training proprietary AI models.**
  Why it matters: Compliance with Slack's Data Usage Restrictions is required for commercial distribution.
  How to find out: Review the latest Slack API Terms of Service and Developer Data Policy.

## Conflicts

- **Marketplace installation threshold**: S8 states an initial threshold of 10 installations as of July 2026, but later notes a reduction to 5 active workspaces starting August 11.
  Resolution: The updated rule of 5 active workspaces applies since the date passed August 11.

## Evidence details

### Claims

| ID | Claim | Conditions | Basis | Sources | Attempts | Excerpt | Excerpt status | Review | Support note |
|---|---|---|---|---|---|---|---|---|---|
| K1 | Third-party OAuth is supported by Slack via standard OAuth 2.0 authorization code flows allowing external apps to be installed on customers' workspaces. | If you want users to be able to install your app on additional workspaces or from the Slack Marketplace. | documented | S4 | — | If you want users to be able to install your app on additional workspaces or from the Slack Marketplace, you'll need to implement an OAuth flow. | not_checked | not reviewed | — |
| K2 | Any developer with a free Slack account can register an app via the Slack API portal by providing basic app information, redirect URLs, and selecting requested scopes. | When registering a new Slack application. | documented | S1 | — | Add any scopes that your application will need, such as chat:write to send messages or channels:history to read chats inside a channel. Use Slack’s documentation to find all needed scopes | not_checked | not reviewed | — |
| K3 | The smallest documented bot or user scopes to read channel history and post summaries are channels:history (or groups:history for private channels) and chat:write. | When accessing channel history and posting messages. | documented | S3 | — | History scopes are per conversation type. conversations.history requires channels:history, groups:history, im:history and mpim:history... chat:write scope reference — custom username and avatar, bot tokens only. | not_checked | not reviewed | — |
| K4 | A first test connection can be established by generating a Client ID and Client Secret in the Slack app management dashboard, configuring a redirect URL, and initiating an OAuth authorization code flow. | During initial app development and connection testing. | documented | S1 | — | To initiate the OAuth flow in your application, embed our pre-built Connect Portal UI directly in your frontend with just a few lines of code... use our provided redirect URL | not_checked | not reviewed | — |
| K5 | Commercial distribution to external customers requires either distributing an unlisted app for pilots or submitting and getting approved for listing in the Slack Marketplace. | When distributing apps to external customers. | documented | S6, S8 | — | Distributing your unlisted app is perfect for when you want to test out your app by running a pilot for early customers; however, apps intended for commercial distribution should be submitted and approved for listing in the Slack Marketplace. | not_checked | not reviewed | — |
| K6 | Slack Marketplace submission entries require that apps have at least 5 active workspace installations prior to review submission. | Prior to Marketplace review submission. | documented | S8 | — | starting August 11th we will be reducing this requirement from 10 to 5 active workspaces! | not_checked | not reviewed | — |
| K7 | Updated commercial distribution terms reinforce data usage restrictions regarding storing, using, and sharing data accessed via Slack APIs. | When handling data accessed via Slack APIs. | documented | S8 | — | Data Usage Restrictions: We're reinforcing safeguards around how third-party applications can store, use, and share data accessed via Slack APIs. | not_checked | not reviewed | — |

### Sources

| ID | Title | URL | Retrieval status | Retrieved at | Provider domain |
|---|---|---|---|---|---|
| S1 | Create a Slack App to enable OAuth for your Integr | https://www.useparagon.com/blog/create-a-slack-app-to-enable-oauth-for-your-integration | search_snippet_only | 2026-11-04 | third_party |
| S3 | Slack OAuth in Python: Connect Accounts and Call a | https://www.agenticfabriq.com/blog/slack-oauth-python | search_snippet_only | 2026-11-04 | third_party |
| S4 | Understanding OAuth scopes for bots \| Slack Develo | https://docs.slack.dev/tools/python-slack-sdk/tutorial/understanding-oauth-scopes | search_snippet_only | 2026-11-04 | third_party |
| S6 | App lifecycle & distribution \| Slack Developer Doc | https://api.slack.com/distribution | search_snippet_only | 2026-11-04 | official |
| S8 | Changelog \| Slack Developer Docs | https://api.slack.com/changelog | search_snippet_only | 2026-11-04 | official |

### Uncited sources

- S2: Installing via OAuth authorization code flow | Slack Developer Docs (search_snippet_only)
- S5: Legacy authentication | Slack Developer Docs (search_snippet_only)
- S7: Slack Marketplace app guidelines and requirements | Slack Developer Docs (search_snippet_only)
- S9: Changelog | Slack (search_snippet_only)
- S10: Slack developer FAQ | Slack Developer Docs (search_snippet_only)

### Validation warnings

- Path own_app_production: added unknown gate 'paid_access'
- Path own_app_production: added unknown gate 'customer_admin_action'
- Path own_app_production: added unknown gate 'partner_program'
- Path own_app_production: added unknown gate 'security_assessment'
- Path own_app_production: added unknown gate 'business_or_identity_verification'
- Gate paid_access: all supporting claims are 'unknown' — assertion is weakly supported
- Gate customer_admin_action: all supporting claims are 'unknown' — assertion is weakly supported
- Gate partner_program: all supporting claims are 'unknown' — assertion is weakly supported
- Gate security_assessment: all supporting claims are 'unknown' — assertion is weakly supported
- Gate business_or_identity_verification: all supporting claims are 'unknown' — assertion is weakly supported
- Moved 5 uncited source(s) to uncited_sources

### Run statistics

- Model: gemini-3.5-flash-lite
- Model calls: 4
- Searches: 2
- Page reads: 0
- Duration: 104.5s
- Cost: unavailable

---
Generated by OAuthPath · 2026-11-04T14:49:56.704152+00:00
