"""Composio connection test script — per §8.5.

Creates a custom or managed auth config, generates a Connect Link, waits for user consent,
and writes an Attempt record. Never writes secrets.

Usage:
    python scripts/composio_test.py --toolkit github --prefix GITHUB
    (Reads GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET from .env)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()


def _sanitize_secrets(text: str, secrets: list[str] | None = None) -> str:
    """Sanitize secrets, tokens, and credentials from log strings and error messages."""
    if not text:
        return ""
    result = str(text)

    # 1. Exact string replacements (longest first)
    if secrets:
        sorted_secrets = sorted([s for s in secrets if s and len(s) >= 4], key=len, reverse=True)
        for s in sorted_secrets:
            result = result.replace(s, "[REDACTED]")

    # 2. HTTP Authorization headers and Bearer tokens
    result = re.sub(
        r'\bBearer\s+[A-Za-z0-9_\-\.]+',
        'Bearer [REDACTED]',
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r'\bAuthorization\s*:\s*(?:Bearer\s+)?[A-Za-z0-9_\-\.]+',
        'Authorization: [REDACTED]',
        result,
        flags=re.IGNORECASE,
    )

    # 3. JSON key-value pairs (e.g. {"api_key": "secret"}, {"client_secret": "secret"})
    result = re.sub(
        r'("?(?:api[_-]?key|client[_-]?secret|client[_-]?id|secret|token|password)"?\s*[:=]\s*)"?([A-Za-z0-9_\-\.]{6,})"?',
        r'\1"[REDACTED]"',
        result,
        flags=re.IGNORECASE,
    )

    # 4. URL query params
    result = re.sub(
        r'([?&](?:api[_-]?key|client[_-]?secret|client[_-]?id|secret|token|access_token)=)[^&\s]+',
        r'\1[REDACTED]',
        result,
        flags=re.IGNORECASE,
    )

    # 5. Generic token / key patterns
    result = re.sub(
        r'\b(api[_-]?key|client[_-]?secret|client[_-]?id|secret|token)\b\s*[:=]\s*["\']?[A-Za-z0-9_\-\.]{6,}["\']?',
        r'\1=[REDACTED]',
        result,
        flags=re.IGNORECASE,
    )

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Test Composio OAuth connection for a provider"
    )
    parser.add_argument("--toolkit", required=True, help="Composio toolkit slug (e.g. github)")
    parser.add_argument("--prefix", default="",
                        help="Env var prefix for credentials (e.g. GITHUB)")
    parser.add_argument("--user-id", default=None,
                        help="Test user ID (defaults to dynamic ID)")
    parser.add_argument("--managed", action="store_true",
                        help="Use Composio managed auth instead of custom")
    parser.add_argument("--redirect-uri",
                        default="https://backend.composio.dev/api/v3/toolkits/auth/callback",
                        help="OAuth redirect URI for custom auth (default: https://backend.composio.dev/api/v3/toolkits/auth/callback)")

    args = parser.parse_args()

    composio_key = os.getenv("COMPOSIO_API_KEY")
    if not composio_key:
        print("ERROR: COMPOSIO_API_KEY not set in .env")
        sys.exit(1)

    client_id = ""
    client_secret = ""
    if not args.managed:
        if not args.prefix:
            print("ERROR: --prefix required when not using --managed")
            sys.exit(1)
        client_id = os.getenv(f"{args.prefix}_CLIENT_ID")
        client_secret = os.getenv(f"{args.prefix}_CLIENT_SECRET")
        if not client_id or not client_secret:
            print(f"ERROR: {args.prefix}_CLIENT_ID and {args.prefix}_CLIENT_SECRET must be in .env")
            sys.exit(1)

    try:
        from composio import Composio, exceptions
    except ImportError:
        print("ERROR: Composio SDK not installed. Install with: pip install composio")
        sys.exit(1)

    configured_secrets = [s for s in [composio_key, client_id, client_secret] if s]

    user_id = args.user_id or f"oauthpath-test-{int(time.time())}"
    start_time = datetime.now(timezone.utc)
    attempt_id = f"A-{args.toolkit}-{start_time.strftime('%Y%m%d-%H%M%S')}"
    observed_result = ""
    status = "failed"
    sdk_call_made = False

    try:
        client = Composio(api_key=composio_key)
        sdk_call_made = True
        print(f"Composio SDK initialized")
        print(f"Toolkit: {args.toolkit}")
        print(f"Auth type: {'managed' if args.managed else 'custom'}")
        print(f"User ID: {user_id}")

        # Step 1: Create auth config
        print("Step 1: Creating auth config...")
        if args.managed:
            options = {"type": "use_composio_managed_auth"}
        else:
            # Query toolkit to get its supported auth scheme instead of hardcoding
            auth_scheme = None
            try:
                tk = client.toolkits.get(args.toolkit)
                if hasattr(tk, "auth_config_details") and tk.auth_config_details:
                    modes = [getattr(d, "mode", "") for d in tk.auth_config_details if getattr(d, "mode", "")]
                    oauth_modes = [m for m in modes if "oauth" in m.lower()]
                    if oauth_modes:
                        auth_scheme = oauth_modes[0].upper()
                elif hasattr(tk, "composio_managed_auth_schemes") and tk.composio_managed_auth_schemes:
                    managed_oauth = [m for m in tk.composio_managed_auth_schemes if "oauth" in str(m).lower()]
                    if managed_oauth:
                        auth_scheme = str(managed_oauth[0]).upper()
            except Exception as e:
                err_clean = _sanitize_secrets(str(e), configured_secrets)
                observed_result = f"Error querying toolkit auth scheme for '{args.toolkit}': {err_clean}"
                status = "failed"
                print(f"[!] Error: {observed_result}")
                sys.exit(1)

            if not auth_scheme:
                observed_result = f"Error during execution: No OAuth authentication mode found for toolkit '{args.toolkit}'"
                status = "failed"
                print(f"[!] Error: {observed_result}")
                sys.exit(1)

            options = {
                "type": "use_custom_auth",
                "auth_scheme": auth_scheme,
                "name": f"{args.toolkit}-custom-{int(time.time())}",
                "credentials": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "oauth_redirect_uri": args.redirect_uri,
                },
            }

        auth_config = client.auth_configs.create(args.toolkit, options)
        auth_config_id = auth_config.id
        print(f"  Auth config created: {auth_config_id}")

        # Step 2: Create Connect Link
        print("\nStep 2: Creating Connect Link...")
        connection_req = client.connected_accounts.link(
            user_id=user_id,
            auth_config_id=auth_config_id,
        )
        redirect_url = getattr(connection_req, "redirect_url", "")
        req_id = getattr(connection_req, "id", "")

        # Step 3: User action needed
        print("\n" + "=" * 50)
        print("ACTION REQUIRED: Open the redirect URL below,")
        print("sign in, and approve the connection:")
        print(f"  {redirect_url}")
        print("=" * 50 + "\n")

        # Step 4: Wait for connection
        print("Step 4: Waiting for connection (timeout: 5 minutes)...")
        try:
            connected = client.connected_accounts.wait_for_connection(
                id=req_id,
                timeout=300,
            )
            status = "succeeded"
            conn_id = getattr(connected, "id", "active")
            observed_result = f"Connected account active: ID {conn_id}"
            print(f"[OK] Success: {observed_result}")
        except exceptions.ComposioSDKTimeoutError:
            status = "failed"
            observed_result = "Connection timed out after 300 seconds waiting for user consent"
            print(f"[!] Timeout: {observed_result}")

    except Exception as e:
        err_msg = _sanitize_secrets(str(e), configured_secrets)
        observed_result = f"Error during execution: {err_msg}"
        status = "failed"
        print(f"[!] Error: {observed_result}")

    finally:
        if sdk_call_made:
            if status == "succeeded":
                limitation = "Proves one test-user connection; not refresh behaviour, org access or production approval"
            elif "timed out" in observed_result.lower():
                limitation = "Connection timed out waiting for user approval; flow was not completed"
            else:
                limitation = "Connection attempt failed during API/SDK execution; credentials or network blocked"

            # Redact sensitive error details like API keys or tokens
            redacted_result = _sanitize_secrets(observed_result, configured_secrets)

            _write_attempt(
                attempt_id, args.toolkit, user_id, start_time,
                redacted_result, status,
                limitation,
            )

        if status != "succeeded":
            sys.exit(1)


def _write_attempt(attempt_id, toolkit, user_id, start_time, observed_result, status, limitation):
    """Write an Attempt record to data/attempts/."""
    end_time = datetime.now(timezone.utc)

    attempt = {
        "id": attempt_id,
        "actor": "assisted",
        "provider": toolkit,
        "scenario": f"Test connection via custom or managed auth for {user_id}",
        "action": f"Create auth config and connect test user for {toolkit}",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "environment": "Local test account",
        "observed_result": observed_result,
        "status": status,
        "artifact_path": None,
        "limitation": limitation,
    }

    attempts_dir = Path("data/attempts")
    attempts_dir.mkdir(parents=True, exist_ok=True)
    out_path = attempts_dir / f"{attempt_id}.json"
    out_path.write_text(json.dumps(attempt, indent=2), encoding="utf-8")

    print(f"\n{'[OK]' if status == 'succeeded' else '[!]'} Attempt recorded: {out_path}")
    print(f"  Status: {status}")
    print(f"  Result: {observed_result[:200]}")


if __name__ == "__main__":
    main()
