"""Deployment configuration for OAuthPath.

Supports running on:
- Local development (uvicorn --reload)
- Railway, Render, Fly.io, or similar PaaS

Set environment variables in the host's secret settings.
"""

import os
import sys

# Ensure the app module is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def get_port() -> int:
    """Get the port from environment (PaaS sets PORT)."""
    return int(os.getenv("PORT") or "8000")


if __name__ == "__main__":
    import uvicorn
    # Restrict forwarded_allow_ips to localhost by default to prevent client IP spoofing.
    # When deployed behind a reverse proxy (Railway, Render, Fly.io, Cloudflare),
    # set FORWARDED_ALLOW_IPS to the proxy's IP/subnet or "*" if ingress is isolated.
    forwarded_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=get_port(),
        workers=1,  # Single worker per §6.7
        proxy_headers=True,
        forwarded_allow_ips=forwarded_ips,
    )
