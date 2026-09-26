#!/usr/bin/env python3
"""One-time YouTube OAuth setup.

Usage:
    python scripts/setup_oauth.py /path/to/client_secrets.json

What it does:
    1. Opens your browser to grant upload access to your channel.
    2. Writes a token file to ./credentials/token.json
    3. Prints a BASE64 blob you paste into GitHub Secrets as YT_CREDS_1 (or _2, _3…).

You only need to run this ONCE per Google Cloud project / channel.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OUT_DIR = Path(__file__).resolve().parent.parent / "credentials"

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/setup_oauth.py <client_secrets.json>")
        sys.exit(1)

    client_secrets = Path(sys.argv[1])
    if not client_secrets.exists():
        print(f"[!] Client secrets file not found: {client_secrets}")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    token_path = OUT_DIR / "token.json"

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    # Opens a local server on port 8080 → sign in with the channel you want to upload to.
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")

    # Read the client config back so we can embed client_id/secret in the token JSON.
    with open(client_secrets) as f:
        cfg = json.load(f)
    installed = cfg.get("installed") or cfg.get("web") or {}

    token_payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "universe_domain": installed.get("universe_domain", "googleapis.com"),
    }

    token_path.write_text(json.dumps(token_payload, indent=2))
    print(f"\n[✓] Token saved to {token_path}")

    # Sanity check: confirm the API is reachable with these creds.
    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if items:
            print(f"[✓] Authenticated as channel: {items[0]['snippet']['title']}")
        else:
            print("[!] No channel found — is this a brand-new Google account?")
    except Exception as e:  # noqa: BLE001
        print(f"[!] Verification call failed (this is okay if quota is fresh): {e}")

    # Emit the base64 blob for GitHub Secrets.
    b64 = base64.b64encode(json.dumps(token_payload).encode()).decode()
    print("\n" + "=" * 72)
    print("COPY EVERYTHING BELOW and paste into GitHub → Settings → Secrets →")
    print("Actions → New repository secret.  Name it:  YT_CREDS_1")
    print("=" * 72)
    print(b64)
    print("=" * 72 + "\n")

if __name__ == "__main__":
    main()
