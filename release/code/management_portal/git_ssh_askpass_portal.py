#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

# Git starts this helper directly, often from a checkout outside the Portal.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.git_credential_portal import _material


def main() -> int:
    payload = _material()
    secret = str(payload.get("secret") or "") if isinstance(payload, dict) else ""
    if not secret:
        return 1
    print(secret)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
