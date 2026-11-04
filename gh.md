# GitHub — Read and create issues for users' repositories

v1 · draft · Researched 2026-11-04
Assumptions: Feature: Read and create issues for users' repositories · Audience: external customers · Deployment: server-side

Evidence: 7 documented · 0 observed · 0 estimated · 0 unknown · 3 sources read · not human-reviewed

Composio: lookup failed — unknown, which is not the same as unavailable

## Readiness summary

- GitHub supports the use of third-party OAuth apps for integration. [K1]
- To enable the feature "Read and create issues for users' repositories", the required OAuth scope is `repo`. [K3]
- You must register your own OAuth app on GitHub, providing an application name, homepage URL, and an authorization callback URL. [K2, K7]
- For external customers who are part of GitHub organizations with OAuth app access restrictions, their organization owners will need to approve your application for it to access private repositories. [K5, K7]

**Difficulty:** Requires coordination (provisional)
  - Required: Customer Admin Action
  - Not established: Security Assessment
  - Not established: Business Or Identity Verification
  - Not established: App Review
  - Not established: Partner Program
  - Not established: Paid Access
  - Based on production path: own_app_production

## Next action

**Register your GitHub OAuth app.**
Owner: You
Link: [https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)
Done when: Your OAuth app is successfully registered on GitHub, and you have obtained its Client ID and Client Secret.
Evidence: [K2, K7]

## Paths

| Path | Difficulty | Setup effort | Waiting |
|---|---|---|---|
| Own OAuth App (Testing) | Not enough evidence | 15–45 minutes (author estimate) — Estimated time for initial registration and a successful test authorization, assuming no organization owner approval is needed. | none — No explicit waiting periods are documented for self-service actions. Conditional waiting for organization owner approval is managed separately. |
| Own OAuth App (Production) | Requires coordination (provisional) | 25–75 minutes (author estimate) — Estimated time for initial registration, preparing customer communications, and guiding a customer through authorization, excluding organization owner approval time. | none — No explicit waiting periods are documented for self-service actions. Conditional waiting for organization owner approval depends on the customer's organization. |

### Own OAuth App (Testing)

*For internal testing and development purposes, before deploying to external customers.*

**Gates:**
- Customer Admin Action: Unknown [K5, K7]
  If testing within a GitHub organization that has OAuth app access restrictions, an organization owner might need to approve your application. The necessity depends on the organization's specific settings.

**Prerequisites:**
- You have administrative access to a GitHub personal account or an organization where you intend to register the OAuth app. (Owner: You) [K2]

**Limits:**
- There is a limit of ten tokens issued per user/application/scope combination. [K4]
- There is a rate limit of ten tokens created per hour. [K4]

**Steps:**
1. **Register your OAuth app** [You] [K2, K6]
   Navigate to your GitHub Developer settings, select 'OAuth Apps', and click 'New OAuth App'. Fill in the 'Application name', 'Homepage URL', and 'Authorization callback URL'. The callback URL for Composio is https://backend.composio.dev/api/v3/toolkits/auth/callback. An 'Application description' is optional. Ensure you only use public information when creating the app.
   *Done when: Your OAuth app is successfully registered on GitHub, and you have access to its Client ID and Client Secret.*
   Effort: 10–30 minutes (author estimate) — Based on typical web form submission and locating credentials.
   Link: https://github.com/settings/developers
2. **Request user authorization (test user)** [You] [K4, K6]
   Once the app is registered, use your OAuth app's Client ID to initiate the authorization flow for a test user. The test user must have a verified email address on GitHub before authorizing your app.
   *Done when: The test user successfully authorizes your OAuth app, and you receive an access token.*
   Effort: 5–15 minutes (author estimate) — Time for a developer to perform a basic authorization test.
3. **(Conditional) Organization Owner Approval** [Your customer's admin] [K5, K7]
   If your test user belongs to an organization with OAuth app access restrictions, the organization owner must approve your application.
   *Done when: The organization owner approves the application, granting access to private resources.*
   Effort: 5–60 minutes (author estimate) — Time can vary significantly depending on the organization owner's responsiveness.

### Own OAuth App (Production)

*For external customers accessing their GitHub repositories.*

**Gates:**
- Customer Admin Action: Required [K5, K7]
  If an external customer uses your application within a GitHub organization that has OAuth app access restrictions, an organization owner must approve your application. Without this approval, the app will only access public resources.
- Security Assessment: Unknown [K6]
  It is unclear if GitHub requires formal security assessments or certifications for production-level OAuth apps used by external customers at scale, beyond general advice to use public information (K6). This requires further investigation.
- Ai Data Use Terms: Unknown [K6]
  Specific data-use gates and terms, especially regarding AI/ML training, bulk export, or automation of data accessed via OAuth apps, are not documented (K6). This requires further investigation.
- Business Or Identity Verification: Unknown
  Not established by research
- App Review: Unknown
  Not established by research
- Partner Program: Unknown
  Not established by research
- Paid Access: Unknown
  Not established by research

**Prerequisites:**
- You have administrative access to a GitHub personal account or an organization. (Owner: You) [K2]
- You have a registered GitHub OAuth app. (Owner: You) [K2]

**Limits:**
- There is a limit of ten tokens issued per user/application/scope combination. [K4]
- There is a rate limit of ten tokens created per hour. [K4]

**Steps:**
1. **Register your OAuth app** [You] [K2, K6]
   Navigate to your GitHub Developer settings, select 'OAuth Apps', and click 'New OAuth App'. Fill in the 'Application name', 'Homepage URL', and 'Authorization callback URL'. The callback URL for Composio is https://backend.composio.dev/api/v3/toolkits/auth/callback. An 'Application description' is optional. Ensure you only use public information when creating the app.
   *Done when: Your OAuth app is successfully registered on GitHub, and you have access to its Client ID and Client Secret.*
   Effort: 10–30 minutes (author estimate) — Based on typical web form submission and locating credentials.
   Link: https://github.com/settings/developers
2. **Inform customers about email verification** [You] [K6]
   Inform your external customers that they must verify their email address associated with their GitHub account before they can authorize your OAuth app.
   *Done when: Customers are aware of the email verification requirement for authorization.*
   Effort: 10–30 minutes (author estimate) — Time for drafting and disseminating communication to customers.
3. **Guide customer through authorization** [You] [K4, K7]
   Guide your customers to authorize your application using the OAuth flow. This will involve directing them to GitHub's authorization page with your app's Client ID and requested scopes.
   *Done when: Customer successfully authorizes your OAuth app.*
   Effort: 5–15 minutes (author estimate) — Per-customer time to complete the authorization process.
4. **(Conditional) Organization owner approval** [Your customer's admin] [K5, K7]
   If a customer belongs to an organization with OAuth app access restrictions, their organization owner must approve your application for it to access non-public resources. You may need to provide guidance to the customer on how to request this approval.
   *Done when: The organization owner approves the application, granting access to private resources.*
   Effort: 5–60 minutes (author estimate) — Time can vary significantly depending on the organization owner's responsiveness and internal processes.

## Scopes

| Scope | Purpose | Classification | Claims |
|---|---|---|---|
| `repo` | Grants full access to public and private repositories including read and write access to code, commit statuses, repository invitations, collaborators, deployment statuses, and repository webhooks. | read and create issues for users' repositories | K3 |

## Launch blockers

- **An external customer attempts to use your application within a GitHub organization that has OAuth app access restrictions.**: Your application will only be able to access public resources for that organization. This will prevent the desired "Read and create issues for users' repositories" functionality if those repositories are private. [K5, K7]
  Resolution: The organization owner must explicitly approve your application for use within their organization. (Owner: Your customer's admin)

## Setup attempts

| ID | Action | Scenario | Environment | Status | Observed result | Limitation |
|---|---|---|---|---|---|---|
| A-github-20261104-143709 | Create auth config and connect test user for github | Test connection via custom or managed auth for oauthpath-test-1793803029 | Local test account | failed | Error querying toolkit auth scheme for 'github': 401 Invalid API key: ck_**Pk7T | Connection attempt failed during API/SDK execution; credentials or network blocked |

## What an agent can help with

None recorded.

## Unknowns

- **Recent changes (about the last 18 months) to GitHub OAuth apps:**
  Why it matters: It is important to know if any policies or technical requirements have changed that could impact the developer's integration.
  How to find out: Search GitHub developer blog or changelog for OAuth app updates.
- **Formal security assessment requirements for production apps:**
  Why it matters: Beyond general advice, it is unclear if there are specific security audits or certifications required for apps used by external customers at scale.
  How to find out: Search GitHub documentation for "OAuth app security audit" or "production requirements".
- **Costs associated with OAuth app API usage or registration:**
  Why it matters: While no costs were mentioned, it's common for API providers to have usage tiers or specific charges.
  How to find out: Search GitHub pricing for developer tools or API usage.
- **Specific data-use gates and terms, especially regarding AI/ML training, bulk export, or automation:**
  Why it matters: The current documentation does not specify policies on how data accessed through OAuth apps can be used for these purposes.
  How to find out: Search GitHub terms of service or developer agreement for data use, AI/ML, and automation clauses.

## Conflicts

None recorded.

## Evidence details

### Claims

| ID | Claim | Conditions | Basis | Sources | Attempts | Excerpt | Excerpt status | Review | Support note |
|---|---|---|---|---|---|---|---|---|---|
| K1 | Third-party OAuth apps are supported. | — | documented | S2 | — | Creating an OAuth app | found | not reviewed | — |
| K2 | Developers can create and register an OAuth app under their personal account or under any organization they have administrative access to. Registration requires an "Application name", "Homepage URL", and "Authorization callback URL". An "Application description" is optional. | — | documented | S2 | — | You can create and register an OAuth app under your personal account or under any organization you have administrative access to. | found | not reviewed | — |
| K3 | The smallest documented scope for "read and create issues for users' repositories" is `repo`. This scope grants "full access to public and private repositories including read and write access to code, commit statuses, repository invitations, collaborators, deployment statuses, and repository webhooks." | — | documented | S6 | — | Grants full access to public and private repositories including read and write access to code, commit statuses, repository invitations, collaborators, deployment statuses, and repository webhooks. | found | not reviewed | — |
| K4 | To get a first test connection working, register the OAuth app as described in K2. The app can then be used to request user authorization. There is a limit of ten tokens issued per user/application/scope combination, and a rate limit of ten tokens created per hour. | — | documented | S2, S4 | — | When an OAuth app wants to identify you by your account on GitHub, you'll see a page with the app's developer contact information and a list of the specific data that's being requested. | not_found | not reviewed | — |
| K5 | For organizations with OAuth app access restrictions, organization owners must approve the application for use in that organization; otherwise, the app will only access public resources. There is no mention of a general verification program for all external customer-facing OAuth apps. | — | documented | S4 | — | For organizations *with* OAuth app access restrictions, you can request that organization owners approve the application for use in that organization. | found | not reviewed | — |
| K6 | Developers should only use public information when creating an OAuth app and avoid sensitive data like internal URLs. Users must verify their email address before authorizing an OAuth app. No specific costs for registration or API usage, security assessment programs, or explicit data-use gates (including AI/ML training, bulk export, or automation terms) are documented in the provided sources. | — | documented | S2, S4 | — | Only use information in your OAuth app that you consider public. Avoid using sensitive data, such as internal URLs, when creating an OAuth app. | found | not reviewed | — |
| K7 | The developer can register the OAuth app and define its properties, including callback URLs. Another party (the user) is required to authorize the app. If the user belongs to an organization with OAuth app access restrictions, an organization owner must approve the application. | — | documented | S2, S4 | — | Click Register application. | found | not reviewed | — |

### Sources

| ID | Title | URL | Retrieval status | Retrieved at | Provider domain |
|---|---|---|---|---|---|
| S2 | Creating an OAuth app - GitHub Docs | https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app | read | 2026-11-04 | official |
| S4 | Authorizing OAuth apps - GitHub Docs | https://docs.github.com/en/apps/oauth-apps/using-oauth-apps/authorizing-oauth-apps | read | 2026-11-04 | official |
| S6 | Scopes for OAuth apps - GitHub Docs | https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps | read | 2026-11-04 | official |

### Uncited sources

- S1: How to register your own GitHub App OAuth app - Nango Docs (search_snippet_only)
- S3: Registering a GitHub App - GitHub Docs (search_snippet_only)
- S5: How to Set Up a GitHub OAuth Application (search_snippet_only)
- S7: GitHub Apps vs. OAuth Apps: Choose the right GitHub connection · Logto blog (search_snippet_only)
- S8: Integrating with GitHub - GitHub Apps and OAuth | Blog — Northflank (search_snippet_only)
- S9: Read-Only OAuth Scope · community · Discussion #7891 · GitHub (search_snippet_only)

### Validation warnings

- Path own_app_production: added unknown gate 'business_or_identity_verification'
- Path own_app_production: added unknown gate 'app_review'
- Path own_app_production: added unknown gate 'partner_program'
- Path own_app_production: added unknown gate 'paid_access'
- Gate business_or_identity_verification: all supporting claims are 'unknown' — assertion is weakly supported
- Gate app_review: all supporting claims are 'unknown' — assertion is weakly supported
- Gate partner_program: all supporting claims are 'unknown' — assertion is weakly supported
- Gate paid_access: all supporting claims are 'unknown' — assertion is weakly supported
- Claim K4: excerpt not found in source S2 (ratio=48)
- Moved 6 uncited source(s) to uncited_sources

### Run statistics

- Model: gemini-2.5-flash
- Model calls: 6
- Searches: 2
- Page reads: 3
- Duration: 77.4s
- Cost: unavailable

---
Generated by OAuthPath · 2026-11-04T14:41:02.502868+00:00
