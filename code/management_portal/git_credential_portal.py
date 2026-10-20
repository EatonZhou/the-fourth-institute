#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import time
import sys
from pathlib import Path

# Git starts this helper directly, often from a checkout outside the Portal.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.product_environment import read_product_env
from management_common import file_permissions


def _request() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in sys.stdin:
        line = raw.rstrip("\r\n")
        if not line:
            break
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def _material() -> dict[str, str]:
    try:
        raw_path = read_product_env("PORTAL_GIT_CREDENTIAL_FILE", "")
        if not raw_path:
            return {}
        payload = json.loads(file_permissions.read_private_bytes(Path(raw_path), required_mode=0o600))
        if not isinstance(payload, dict):
            return {}
        # Older operation-scoped material lacked expiry/path; new producers set both.
        if "expires_at" in payload:
            expiry = float(payload["expires_at"])
            if not math.isfinite(expiry) or expiry <= time.time():
                return {}
        for key in ("username", "secret"):
            value = payload.get(key, "")
            if not isinstance(value, str) or any(c in value for c in "\r\n\0"):
                return {}
        return payload
    except (OSError, ValueError, TypeError):
        return {}


def main() -> int:
    operation = str(sys.argv[1] if len(sys.argv) > 1 else "").strip().casefold()
    request = _request()
    if operation != "get":
        return 0
    material = _material()
    if (
        not material
        or request.get("protocol", "").casefold()
        != str(material.get("protocol") or "").casefold()
        or request.get("host", "").casefold()
        != str(material.get("host") or "").casefold()
        or (material.get("path") and request.get("path", "").lstrip("/") != material["path"])
    ):
        print("quit=true")
        return 0
    username = str(material.get("username") or "")
    secret = str(material.get("secret") or "")
    if not username or not secret:
        print("quit=true")
        return 0
    print(f"username={username}")
    print(f"password={secret}")
    print("quit=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
