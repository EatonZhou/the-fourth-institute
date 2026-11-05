"""Verified host boundary for Antigravity account isolation."""
from __future__ import annotations

import sys
from management_common.distribution_targets import identify_host


def platform_key():
    # macOS's global Google keychain entry is not yet verified to isolate accounts.
    os_name = {"linux": "linux", "win32": "windows"}.get(sys.platform)
    arch = identify_host()["architecture"]
    if not os_name or arch not in {"x86_64", "aarch64"}:
        raise RuntimeError("Antigravity account isolation is supported on Linux and Windows only")
    return f"{os_name}-{arch}"

