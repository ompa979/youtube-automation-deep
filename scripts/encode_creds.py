#!/usr/bin/env python3
"""Encode an existing token JSON into base64 for GitHub Secrets.

Usage:
    python scripts/encode_creds.py credentials/token.json
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/encode_creds.py <token.json>")
        sys.exit(1)
    p = Path(sys.argv[1])
    data = json.loads(p.read_text())
    b64 = base64.b64encode(json.dumps(data).encode()).decode()
    print(b64)

if __name__ == "__main__":
    main()
