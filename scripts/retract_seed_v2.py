"""
One-shot migration: retract the falsely-attributed v2 seed rows in SQLite.

Background
----------
Three seed files in data/reviewed/ were previously imported with their review
attributed to "Monesh Patidar", but that review was performed by the OAuthPath
coding agent.  The seed files have already been updated on disk
(retracted: true), but any rows that were imported into the SQLite database
before that correction still carry the wrong reviewer identity.

Run this script once against your live database (DATABASE_PATH in .env) to
set those rows to status='retracted' so they are excluded from public views.

Usage
-----
    python scripts/retract_seed_v2.py

The script is idempotent: re-running it after rows are already retracted prints
"Not found" (the store query filters out already-retracted rows) and exits 0.

Owner action required
---------------------
This script must be run by the deployment owner; it is not called automatically
on startup.
"""

import sys
from pathlib import Path

# Allow running from either repo root or the scripts/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATABASE_PATH  # noqa: E402
from app.store import Store  # noqa: E402

RETRACTION_REASON = (
    "Reviewer identity was incorrectly attributed to Monesh Patidar; "
    "the audit was performed by the OAuthPath coding agent (automated). "
    "Retracted from active publication."
)

TARGETS = [
    ("github-2f1b5349", 2),
    ("slack-e07c7c36", 2),
    ("gmail-google-b419b8d4", 2),
]


def main() -> int:
    store = Store(DATABASE_PATH)
    any_error = False
    for guide_id, version in TARGETS:
        try:
            ok = store.retract_guide_version(guide_id, version, RETRACTION_REASON)
            status = "Retracted" if ok else "Not found (already retracted or never imported)"
            print(f"  {status}: {guide_id} v{version}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR retracting {guide_id} v{version}: {exc}", file=sys.stderr)
            any_error = True
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
