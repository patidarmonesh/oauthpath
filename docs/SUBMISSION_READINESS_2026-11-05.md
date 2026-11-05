# OAuthPath submission readiness — 5 November 2026

**Verdict: do not submit this snapshot yet.** The application has improved, but the deliverable still lacks bundled examples and submission links, and its verification document still asserts an audit that the owner previously confirmed was written by the agent. Several reproducible code issues remain.

Reviewed the current working tree, including staged changes after `9afc2ee`. This was a review, not an implementation pass. Existing source changes were left intact.

## Checks actually performed today

| Check | Observed result |
|---|---|
| Full automated suite | **255 passed, 1 deprecation warning, 58.15 seconds.** Real service credentials were cleared in the test process and a temporary database was used. |
| Dependency consistency | `pip check` passed. |
| Live Composio GitHub lookup | **Matched**, managed OAuth available, one metadata source. The previous rejected-key issue is resolved for this read-only lookup. |
| Browser on a fresh temporary database | Homepage rendered; library showed **“No guides yet.”** |
| Live browser submission | Linear / read and create issues / external customers reached research and search, then failed with a quota message. No guide was produced in this run. |
| Failure recovery | The browser displayed an actionable quota message. Retry preserved the entered scenario. A subsequent Box job was accepted and displayed the expected disambiguation choices, confirming capacity was released. |
| Existing-data render checks | On an isolated copy of the local database, all four visible draft records returned HTTP 200 for HTML, Markdown and JSON. |
| Withdrawn versions | The three retracted v2 records returned HTTP 410 from HTML, Markdown and JSON endpoints. |
| Current secrets in tracked text files | No matches for the configured secret values. This is not a historical secret audit. |
| Submission links | README still contains a deployment placeholder; public base URL is localhost; no Git remote is configured in this checkout. A separately deployed URL/repository was not provided or verified. |

The configured model is `gemini-3.6-flash`. The successful 94-second Linear generation recorded in the previous review is an earlier result, not today's live result. No further model retries were made after the quota failure.

The owner confirmed that they personally approved GitHub consent in the browser. `A-github-20261104-182309.json` records a successful test-user connection. The owner also confirmed that the human claim audit is still pending. The consent and connection evidence applies to this one test account.

## Submission blockers

### 1. P1 — The verification document still claims the old human audit

Locations: `docs/VERIFICATION.md:153`, `:156`, `:349`; `README.md:124`.

The document still calls the audit completed, reports 15 supported claims, and attributes human verification. The owner confirmed that the coding agent wrote the table and that the human claim audit is pending. Replace this attribution and the unsupported counts before submission.

**Required correction:** replace the old audit table/narrative with accurate results and provenance. If a human review has since occurred, record its actual checked sources, dates, outcomes and corrections. Otherwise label the work as agent investigation and leave human review pending. Do not claim three complete audited examples until the shipped examples and evidence exist. Update the stale test count from the real final run.

### 2. P1 — A clean installation has no examples, and local fallback drafts still contain the old errors

Locations: `data/reviewed/`, `README.md:122`, `README.md:124`; local database v1 records for Slack and Gmail.

The reviewed directory contains only `.gitkeep`. The database is excluded from Git and the Docker image. A clean startup therefore has no examples, despite the README claiming three complete audited reference guides. I confirmed the empty library in a fresh browser session.

Retraction correctly blocks v2, but the existing local library falls back to v1. Those accessible drafts still say Slack needs five active workspace installations and that both Gmail scopes are sensitive. Corrected prose in a separate review document does not correct these served guides.

Slack's official notice specifies 10 active workspace installations maintained during Marketplace review. [Slack requirement](https://docs.slack.dev/changelog/2026/09/01/slack-marketplace-install-requirement/). Google classifies `gmail.send` as sensitive and `gmail.readonly` as restricted. [Gmail scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes).

**Required correction:** produce and check three varied, useful examples and bundle them with reproducible loading. They may be honestly labeled drafts or agent-checked examples if that is what they are; do not falsely promote them to human-reviewed merely to use the current seed loader. Correct the claims, scope objects, gate descriptions, summaries and estimates together. Make old erroneous versions explicitly withdrawn or clearly superseded, including cached/latest routes. Test a fresh database and an existing database.

### 3. P1 — Live generation and submission links are not ready for the evaluator

Location: `README.md:7` and current deployment configuration.

Today's browser generation failed due to quota. Failure handling is better, but the evaluator needs to be able to submit an unfamiliar app and receive a guide. The repo also does not supply the required hosted experience and repository links.

**Required correction:** check the project's actual available quota/model configuration, then perform a bounded fresh generation after capacity is available. Do not assume every 429 is a daily quota or that a new API key resets quota. Publish the final code and tested examples, deploy, replace the README placeholders, and verify generation plus share/export links on that deployment. Preserve actual failures in the verification notes. Do not silently switch to a paid service.

## Reproduced code findings

### 4. P2 — Truncation followed by bad JSON crashes the synthesis error handler

Location: `app/synthesis.py:590–596`.

`_sanitize_error` is imported only inside the first exception branch. When the first response ends with `MAX_TOKENS`, execution retries without visiting that branch. If the second response contains invalid JSON, the second exception branch references an uninitialized local variable.

**Actual probe result:** `UnboundLocalError: local variable '_sanitize_error' referenced before assignment`.

**Fix:** import the helper at module/function scope before either attempt. Return a structured invalid-output result after an unsuccessful repair. Add a regression for first response truncated, second response malformed; also cover a provider error on the second attempt.

### 5. P2 — Synthesis still loses typed quota errors

Locations: `app/synthesis.py:577`, `app/pipeline.py:238–241`, `app/main.py:133–137`.

Research now preserves categories, but synthesis catches a quota exception as a general repair failure. It calls the model a second time and returns no `error_category`. Pipeline then explicitly labels the result `[unknown]`; the UI gives that marker priority and shows a generic failure.

**Actual probe result:** two mocked model calls; no synthesis error category; public message `Generation failed (Error ID: test-id)` for a known rate-limit failure.

**Fix:** handle typed provider errors separately from JSON/schema repair failures. Preserve `exc.category` in synthesis stats/results, carry it through pipeline, and display its safe message. Do not retry authentication/quota failures as if the JSON were malformed. Test synthesis-to-job polling, not only `_run_job` with a directly raised exception.

### 6. P2 — The new deadline parameter does not bound an in-flight call

Locations: `app/llm_gemini.py:257–266`; calls in `app/research.py:567`, `:601`, `:654`, `:670`, `:708`, `:778`; `app/synthesis.py:529`.

The adapter checks its optional monotonic deadline before attempts and backoff, but the SDK await itself is not wrapped in an application timeout. Research and synthesis do not pass that deadline. Tavily operations remain directly awaited. The HTTP timeout is not a total phase budget.

**Actual probes:** a call given a 30 ms timeout and deadline returned after approximately 203 ms instead of timing out; the research phase also exceeded its 30 ms budget while awaiting a mock model operation.

The outer web-job timeout still limits total job lifetime to its configured timeout plus 10 seconds. This finding is about the research/synthesis reservation and retry budget, not a claim that web jobs have no outer timeout.

**Fix:** use one monotonic clock for phase budgets; pass the remaining deadline consistently; wrap complete awaited operations in `asyncio.wait_for` or an equivalent supported timeout context. Include retries/backoff and web operations. Keep evidence already obtained and reserve time for synthesis where possible. Test slow operations as well as already-expired deadlines.

### 7. P2 — Empty extracted text can be reported as a page read

Locations: `app/web_tools.py` successful extraction processing; `app/research.py:655–660` and `:779–784`.

The adapter accepts an extraction result with empty `raw_content`. Research checks only that the URL key exists, then labels the source `read` and increments page-read counts.

**Actual probe result:** extraction `{url: ""}` produced `page_reads=1`, `retrieval_status="read"`, and zero nonempty page texts.

The fallback verification also checks `total_fetches == 0`, which means attempted-but-failed reads prevent that fallback even when another suitable source is available. Empty or unsuccessful retrieval must not count as verified evidence.

**Fix:** require meaningful non-whitespace extracted content before marking a source read. Record empty extraction as a failed read. Base the fallback on successful relevant reads, within the existing budget. Attempt another appropriate primary source where practical, otherwise expose incomplete coverage. Keep semantic verification separate: a nonempty page still does not prove that it supports a claim.

## Exact finishing sequence

1. Fix the four reproduced code findings above and add the targeted regressions.
2. Replace the inaccurate verification claims with honest, source-linked results. Record the actual actor for setup attempts and reviews.
3. Correct and bundle the three examples. Verify a fresh checkout shows them without using the author's local database. Confirm an existing database no longer presents erroneous older versions as the recommended current guide.
4. Run the complete suite and dependency check again. Run the corrected smoke checks with bounded live usage.
5. Obtain a successful fresh unfamiliar-app browser run with real source reads; inspect its claims and HTML/Markdown/JSON output. Exercise a repeat/cache run and force refresh without overwriting old versions.
6. Publish the current working tree and deploy. Add the actual hosted URL and source URL, then test the hosted form, status polling, guide links, exports and restart behavior.
7. Reconcile README and verification notes with those exact final results, including limitations. The assignment does not require completed provider approvals or 100 finished guides.

**Submission-ready means:** a working hosted unfamiliar-app flow, a reproducible varied example library, truthful checked evidence, working share/export links, and source/setup instructions that reproduce the submitted behavior.

## Browser evidence from this review

The live research job reached a quota failure and exposed a usable retry control:

![Observed quota failure in the browser](C:/Users/mayan/Downloads/Composio/docs/review-2026-11-05-browser.png)
