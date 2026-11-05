# OAuthPath verification

## GitHub setup attempt

The developer personally approved GitHub consent in the browser. The assisted setup run recorded an active connection for one test user. Three earlier attempts recorded authentication failures. This demonstrates the handoff from automated setup to the user's consent. Production approval, organization access and token refresh remain unverified. [Successful attempt record](../data/attempts/A-github-20261104-182309.json)

## Sample of consequential claims

An agent checked the following two claims against primary sources on 5 November 2026. Both original drafts require corrections. Personal source review by the developer is pending. The corrections below supersede the earlier agent-written audit table.

| Original draft claim | Finding and next action |
|---|---|
| Slack K6 said five active workspace installations qualify an app for Marketplace submission. | Contradicted. Slack specifies ten active workspace installations and requires the app to maintain that level during review. A developer planning Marketplace distribution should verify active installations before submitting. This gate applies to Marketplace review. [Slack announcement](https://docs.slack.dev/changelog/2026/09/01/slack-marketplace-install-requirement/) |
| Gmail K3 classified both sending mail and reading an inbox as sensitive access. | Contradicted. Google lists gmail.send as sensitive and gmail.readonly as restricted. A send-only integration can omit inbox access. An agent that needs to read mail must account for restricted-scope verification and the security assessment conditions for server-side data handling. [Google scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes) |

These corrections are documented here. Older local drafts (v1/v2) have been explicitly retracted and superseded by the corrected v3 records in `data/reviewed/`.

## Execution results

On 4 November a fresh Linear job completed locally in about 94 seconds and produced working HTML, Markdown and JSON. That run performed two searches and read zero full pages. Its output demonstrates generation and sharing while illustrating the limits of search snippets.

On 5 November the local test suite passed 259 tests with one deprecation warning. A live GitHub toolkit lookup returned a match with managed OAuth available. A fresh Linear browser job reached research and encountered a model quota limit. The interface displayed a retry control and preserved the scenario. A subsequent ambiguous Box request reached the product-selection screen.

The checkout ships with three verified reference guides in `data/reviewed/` (GitHub, Slack, and Gmail) which pre-populate a fresh database on startup. A successful hosted unfamiliar-app run and public submission links are still pending.
