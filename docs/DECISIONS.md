# Decisions

## 1. User problem and scope

Developers building agent integrations through Composio need to know what stands between them and a production OAuth connection. Credential creation is already documented. The expensive surprise is the approval path — reviews, verification, security assessments, and admin consent — owned by people other than the developer and scattered across each provider's docs.

Three providers cover different challenges:
- **GitHub**: direct self-service baseline, plus the distinction between personal consent and organization access (org owners can restrict OAuth apps).
- **Slack**: distribution vs Marketplace listing, method-specific rate limits for unlisted apps, and customer-admin ownership.
- **Gmail**: scope-dependent review requirements — send-only (sensitive) vs inbox-reading (restricted) — and the security assessment implication for server-side access.

## 2. What was cut

A reliable unfamiliar-app research flow and honest evidence labeling mattered more than breadth. Cut in order: animations, filters, extra providers beyond three, numeric scoring, automated grading, submission-pack generation, browser automation. A catalog of 100 names is not 100 guides; the README explains the path to 100 through the shared schema, pipeline, review workflow, and freshness policy.

## 3. What evidence changed

During technical validation and testing of the pipeline:
1. **Gate evidence discipline**: Previously, a production path with unevidenced gates could be labeled "Straightforward". We instituted the rule that any gate marked `required` or `not_required` without a supporting `documented` or `observed` claim is automatically downgraded to `unknown` with an explicit warning, resulting in a provisional or not-enough-evidence overall difficulty.
2. **Estimate boundary integrity**: Estimates with `basis="unknown"` or 0–0 bounds previously retained unit strings ("business days"). These now strictly evaluate to null bounds and render as `unknown`.
3. **Composio claim corroboration**: Composio managed paths and wiring steps now explicitly generate claims `KC1`, `KC2`, and `KC3` and cite maintainer sources `C2` and `C3` before reference validation runs, ensuring that evidence chains never dangle or get silently dropped.

## 4. Next investment

After validating usefulness and claim quality with these three guides:
1. **Coverage**: prioritize by Composio toolkit usage × missing managed apps × gate complexity. Generate drafts in batches; review the top apps by hand.
2. **Freshness**: re-check on a cadence tied to change risk; watch provider changelogs to flag affected guides. Diff runs to catch silent changes.
3. **Integration**: a readiness card inside Composio's auth-config flow; a tool coding agents can call; internal prioritization of which toolkits most need a managed app.
4. **Measurement**: time from creating a custom auth config to the first ACTIVE connected account; share of custom configs reaching users outside the developer's org.

## 5. Free-tier infrastructure stack

To ensure any evaluator, developer, or teammate can run, test, and verify OAuthPath completely without requiring paid API credits or credit card registrations, OAuthPath operates natively on free tiers:
- **Google AI Studio (Gemini 3.6 Flash / 3.5 Flash Lite)**: Zero-cost LLM tier providing structured JSON schema synthesis, multi-turn tool calling, and fast identity resolution.
- **Tavily (1,000 free monthly credits)**: Dedicated search and markdown extraction API, providing fresh web results and page content while offloading web crawling so OAuthPath maintains zero SSRF exposure.
- **Composio Hobby ($0)**: Live toolkit metadata discovery and local test connection verification.
- **Render Free Tier**: Zero-cost web hosting with persistent git-backed seed import on service launch.
- **Dual-Stack Preservation**: The Anthropic path (`LLM_PROVIDER=anthropic`) is preserved intact behind the same two-layer schema, code-owned evidence registry, and deadline invariants.
