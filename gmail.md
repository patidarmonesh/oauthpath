# Gmail (Google) — Send email on the user's behalf; optionally read the inbox

v1 · draft · Researched 2026-11-04
Assumptions: Feature: Send email on the user's behalf; optionally read the inbox · Audience: external customers · Deployment: server-side

Evidence: 8 documented · 0 observed · 0 estimated · 0 unknown · 0 sources read · not human-reviewed

Composio: lookup failed — unknown, which is not the same as unavailable

## Readiness summary

- Google fully supports third-party OAuth 2.0 for server-side applications accessing the Gmail API. [K1]
- Developers can register an OAuth app in Google Cloud by setting the consent screen to External or Internal user types. [K2]
- Sending email and optionally reading the inbox requires the sensitive scopes `https://www.googleapis.com/auth/gmail.send` and `https://www.googleapis.com/auth/gmail.readonly`. [K3]
- External customer deployments requesting sensitive scopes trigger Google's OAuth app verification and security assessments. [K4]

**Difficulty:** Substantial external requirements (provisional)
  - Required: App Review
  - Required: Security Assessment
  - Required: Ai Data Use Terms
  - Not established: Customer Admin Action
  - Not established: Paid Access
  - Not established: Partner Program
  - Not established: Business Or Identity Verification
  - Based on production path: own_app_production

## Next action

**Create a Google Cloud project and configure the OAuth consent screen with external user type.**
Owner: You
Link: [https://console.cloud.google.com/](https://console.cloud.google.com/)
Done when: A Google Cloud project is created and the OAuth consent screen is configured.
Evidence: [K2]

## Paths

| Path | Difficulty | Setup effort | Waiting |
|---|---|---|---|
| Testing & Development Mode | Straightforward | 1–2 hours (author estimate) — Total setup effort for testing path. | none — No waiting period for testing setup. |
| Production Deployment for External Customers | Substantial external requirements (provisional) | 2–5 hours (author estimate) — Development and submission preparation effort. | unknown — Google OAuth app verification and security assessment typically range from 2 to 4 weeks. |

### Testing & Development Mode

*Suitable for initial development and testing with up to 100 test users without immediate app verification.*

**Gates:**
- App Review: Not Required [K5]
  Not mandatory for initial testing using test users under the testing publishing status.

**Prerequisites:**
- A Google Cloud account to create a project and configure credentials. (Owner: You) [K2]

**Limits:**
- Limited to up to 100 test users for apps in Testing publishing status. [K5]

**Steps:**
1. **Create Test Credentials** [You] [K2, K5]
   Create an OAuth client ID in the Google Cloud Console and configure a test consent screen adding test users.
   *Done when: OAuth client ID is created and test users are added.*
   Effort: 15–30 minutes (author estimate) — Estimated time to set up a Google Cloud project and create test credentials.
   Link: https://console.cloud.google.com/

### Production Deployment for External Customers

*Required when deploying the feature to external customers utilizing sensitive Gmail scopes.*

**Gates:**
- App Review: Required [K3, K4, K6]
  Required for external users requesting sensitive or restricted Gmail scopes.
- Security Assessment: Required [K4, K6, K7]
  Sensitive scopes require security assessments like CASA tier reviews.
- Ai Data Use Terms: Required [K7]
  Restrictions apply on AI/ML model training using restricted scope user data without explicit consent.
- Customer Admin Action: Unknown
  Not established by research
- Paid Access: Unknown
  Not established by research
- Partner Program: Unknown
  Not established by research
- Business Or Identity Verification: Unknown
  Not established by research

**Prerequisites:**
- Google Cloud project with OAuth consent screen set to External user type. (Owner: You) [K2, K6]
- Compliance with Google API Services User Data Policy. (Owner: You) [K7]

**Steps:**
1. **Configure Production OAuth and Submit for Verification** [You] [K1, K4, K8]
   Implement server-side authorization flow, configure production OAuth client credentials, and submit the app for Google OAuth verification and security assessment.
   *Done when: Verification and security assessment submission are completed.*
   Effort: 2–5 hours (author estimate) — Estimated effort to implement server-side flow and prepare verification submission materials.
   Link: https://console.cloud.google.com/

## Scopes

| Scope | Purpose | Classification | Claims |
|---|---|---|---|
| `https://www.googleapis.com/auth/gmail.send` | Send email on the user's behalf | sensitive | K3 |
| `https://www.googleapis.com/auth/gmail.readonly` | Optionally read the user's inbox | sensitive | K3 |

## Launch blockers

- **Requesting sensitive scopes (`gmail.send`, `gmail.readonly`) for external customers without Google OAuth verification and CASA review**: App verification failure or token usage restrictions for external users. [K3, K4, K7]
  Resolution: Submit the application for Google OAuth verification and complete the required security assessment. (Owner: You)

## What an agent can help with

- **Code Generation**: Generate server-side OAuth 2.0 flow implementation code for Google APIs.
- **Scope Mapping**: Map required features to minimal necessary Google OAuth scope strings.

## Unknowns

- **What is the exact current pricing charged by authorized labs for CASA security assessments?**
  Why it matters: Determines the financial overhead for launching an external integration.
  How to find out: Check current approved CASA lab pricing pages.
- **What are the precise automated bot detection heuristics during Google OAuth brand and app verification?**
  Why it matters: Determines potential automated rejection triggers during submission.
  How to find out: Consult recent Google Cloud console developer community forums.

## Conflicts

- **Precise classification and assessment triggers for `gmail.readonly` and `gmail.send`**: S1 states that `gmail.send` and `gmail.readonly` are sensitive and require a security assessment, whereas S2 classifies them under standard sensitive/restricted scope verification tracks.
  Resolution: Both apply depending on whether Google classifies the particular deployment as requiring standard sensitive verification or a full CASA assessment; S1 reflects tighter enforcement of security assessments for external customer-facing apps requesting mailbox access.

## Evidence details

### Claims

| ID | Claim | Conditions | Basis | Sources | Attempts | Excerpt | Excerpt status | Review | Support note |
|---|---|---|---|---|---|---|---|---|---|
| K1 | Third-party OAuth 2.0 is fully supported by Google for web, server-side, and installed applications accessing the Gmail API. | When accessing Gmail API via web, server-side, or installed applications. | documented | S11 | — | — | not_checked | not reviewed | — |
| K2 | Any developer with a Google Cloud account may register an OAuth app; registration requires creating a Google Cloud project, configuring the OAuth consent screen, and setting the user type to External or Internal. | When registering an OAuth app in Google Cloud. | documented | S1, S3 | — | — | not_checked | not reviewed | — |
| K3 | The smallest documented permissions for sending email and optionally reading the inbox are `https://www.googleapis.com/auth/gmail.send` and `https://www.googleapis.com/auth/gmail.readonly`, both classified as sensitive. | When requesting permissions to send mail and read the inbox. | documented | S1, S2 | — | — | not_checked | not reviewed | — |
| K4 | For external customers, requesting sensitive scopes triggers Google's OAuth app verification and CASA tier review, with timelines typically ranging from 2 to 4 weeks. | When requesting sensitive scopes for users outside the Google Workspace organization. | documented | S1 | — | Sensitive scopes (gmail.send, gmail.readonly, gmail.modify) 2-4 weeks | not_checked | not reviewed | — |
| K5 | To get a first test connection working, a developer creates credentials in the Google Cloud Console, configures a test consent screen, and adds test users (up to 100 users for apps in Testing publishing status). | When using apps in Testing publishing status. | documented | S3 | — | — | not_checked | not reviewed | — |
| K6 | Your app needs Google OAuth app verification if it is set to External user type on the OAuth consent screen and requests any sensitive or restricted Gmail scopes from users outside your own Google Workspace organization. | When app is set to External and requests sensitive or restricted scopes. | documented | S1, S3 | — | Your app needs Google OAuth app verification if it is set to External user type on the OAuth consent screen and requests any sensitive or restricted Gmail scopes from users outside your own Google Workspace organization. | not_checked | not reviewed | — |
| K7 | Account, security-assessment, and data-use gates include compliance with the Google API Services User Data Policy and restrictions on AI/ML model training using restricted scope user data without explicit consent. | When utilizing Google API services and restricted scopes. | documented | S1, S5 | — | Your application must follow Google’s API Services User Data Policy and meet additional requirements for specific scopes. | not_checked | not reviewed | — |
| K8 | The developer can register the GCP project, configure OAuth credentials, implement the server-side OAuth flow, and request verification now. | When initiating development and verification submission. | documented | S1, S5 | — | — | not_checked | not reviewed | — |

### Sources

| ID | Title | URL | Retrieval status | Retrieved at | Provider domain |
|---|---|---|---|---|---|
| S1 | Google OAuth Verification & Gmail API Credentials  | https://www.unipile.com/integrating-google-oauth-2-0-user-authentication-into-your-app | search_snippet_only | 2026-11-04 | third_party |
| S2 | Choose Gmail API scopes  \|  Google for Developers | https://developers.google.com/workspace/gmail/api/auth/scopes | search_snippet_only | 2026-11-04 | official |
| S3 | OAuth App Verification Help Center - Google Cloud  | https://support.google.com/cloud/answer/13463073?hl=en | search_snippet_only | 2026-11-04 | official |
| S5 | Google verification and security assessment guide  | https://developer.nylas.com/docs/provider-guides/google/google-verification-security-assessment-guide | search_snippet_only | 2026-11-04 | third_party |
| S11 | Gmail API overview  \|  Google for Developers | https://developers.google.com/workspace/gmail/api/guides | search_snippet_only | 2026-11-04 | official |

### Uncited sources

- S4: Google OAuth verification with Gmail node: how do you pass verification when GmailOAuth2Api forces 6 scopes? - Questions - n8n Community (search_snippet_only)
- S6: Области OAuth 2.0 для API Google  |  Google for Developers (search_snippet_only)
- S7: Champs d'application OAuth 2.0 pour les API Google  |  Google for Developers (search_snippet_only)
- S8: Champs d'application OAuth 2.0 pour les API Google  |  Google for Developers (search_snippet_only)
- S9: Google API 的 OAuth 2.0 范围  |  Google for Developers (search_snippet_only)
- S10: Google API 的 OAuth 2.0 范围  |  Google for Developers (search_snippet_only)
- S12: Create and send email messages  |  Gmail  |  Google for Developers (search_snippet_only)
- S13: OAuth 2.0 Scopes for Google APIs  |  Google for Developers (search_snippet_only)
- S14: Implement server-side authorization  |  Gmail  |  Google for Developers (search_snippet_only)

### Validation warnings

- Path own_app_production: added unknown gate 'customer_admin_action'
- Path own_app_production: added unknown gate 'paid_access'
- Path own_app_production: added unknown gate 'partner_program'
- Path own_app_production: added unknown gate 'business_or_identity_verification'
- Gate customer_admin_action: all supporting claims are 'unknown' — assertion is weakly supported
- Gate paid_access: all supporting claims are 'unknown' — assertion is weakly supported
- Gate partner_program: all supporting claims are 'unknown' — assertion is weakly supported
- Gate business_or_identity_verification: all supporting claims are 'unknown' — assertion is weakly supported
- Moved 9 uncited source(s) to uncited_sources

### Run statistics

- Model: gemini-3.5-flash-lite
- Model calls: 5
- Searches: 3
- Page reads: 0
- Duration: 114.0s
- Cost: unavailable

---
Generated by OAuthPath · 2026-11-04T14:52:00.422312+00:00
