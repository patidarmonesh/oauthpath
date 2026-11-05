# OAuthPath

OAuthPath helps developers plan the work between registering a provider OAuth app, connecting a first test user and launching to external customers. Enter an app name or website, describe the feature and choose an audience. The resulting guide identifies the next action, who owns it, expected effort, approval dependencies and supporting sources. Each saved version has a shareable page and a Markdown export.

## Run locally

Use Python 3.10 or later. From the repository root on Windows:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe serve.py
```

Before starting the server, set LLM_PROVIDER=gemini, GEMINI_MODEL, GEMINI_API_KEY and TAVILY_API_KEY in the local .env. Set COMPOSIO_API_KEY for toolkit lookup. Open http://localhost:8000. To run the tests, use `.\.venv\Scripts\python.exe -m pytest tests -q`.

## Product decisions

- Ask for the feature and audience because requested permissions and external distribution change the approval path.
- Show setup effort separately from provider waiting time so developers can identify work they can do today.
- Distinguish provider documentation, agent estimates, observed connection attempts and unresolved questions. The developer handles account decisions and consent.
- Save versioned guides in SQLite so teammates can discuss the same result. For hosting, configure PUBLIC_BASE_URL and persistent storage for DATABASE_PATH.

## Verification and limits

The developer approved one GitHub test connection. A source check found consequential errors in earlier Slack and Gmail guidance. The verification notes give the corrections and their sources. [Verification notes](docs/VERIFICATION.md)

A clean checkout pre-populates with three verified reference guides in `data/reviewed/` (GitHub, Slack, and Gmail). Model quotas can interrupt live generation on free-tier keys. Public deployment and source links are pending.
