"""Mint a Supabase JWT for a test user — useful for hitting protected endpoints
locally without a frontend.

Usage::

    uv run python scripts/get_test_token.py EMAIL PASSWORD
    uv run python scripts/get_test_token.py EMAIL PASSWORD --curl   # prints a curl snippet too

The user must already exist (Dashboard → Authentication → Users → "Add user"
with "Auto Confirm User" checked).
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx
from jose import jwt

from backend.config import get_settings


def main() -> int:
    p = argparse.ArgumentParser(description="Get a Supabase access token.")
    p.add_argument("email")
    p.add_argument("password")
    p.add_argument(
        "--curl",
        action="store_true",
        help="Also print a curl command for hitting the local backend.",
    )
    p.add_argument(
        "--decode",
        action="store_true",
        help="Print the decoded header and claims (no signature verification).",
    )
    args = p.parse_args()

    settings = get_settings()
    anon = settings.public_supabase_key
    if anon is None:
        print("error: SUPABASE_ANON_KEY (or PUBLISHABLE_KEY) is not set", file=sys.stderr)
        return 2

    url = f"{settings.supabase_url}auth/v1/token?grant_type=password"
    headers = {
        "apikey": anon.get_secret_value(),
        "Content-Type": "application/json",
    }
    body = {"email": args.email, "password": args.password}

    with httpx.Client(timeout=10.0) as client:
        r = client.post(url, headers=headers, json=body)

    if r.status_code != 200:
        print(f"FAILED [{r.status_code}]: {r.text}", file=sys.stderr)
        return 1

    payload = r.json()
    access_token = payload["access_token"]
    print(access_token)

    if args.decode:
        header = jwt.get_unverified_header(access_token)
        claims = jwt.get_unverified_claims(access_token)
        print("\n# Header:", file=sys.stderr)
        print(json.dumps(header, indent=2), file=sys.stderr)
        print("\n# Claims:", file=sys.stderr)
        print(json.dumps(claims, indent=2), file=sys.stderr)

    if args.curl:
        print("\n# Hit the backend with this token:", file=sys.stderr)
        print(
            f'curl http://localhost:8000/api/auth/me \\\n'
            f'  -H "Authorization: Bearer {access_token}"',
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
