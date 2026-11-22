"""Verified host boundary for Antigravity account isolation."""
from __future__ import annotations

import sys
from management_common.distribution_targets import identify_host


def _supported_platform_key() -> str | None:
    os_name = {"linux": "linux", "win32": "windows", "darwin": "darwin"}.get(sys.platform)
    if not os_name:
        return None
    arch = identify_host()["architecture"]
    return f"{os_name}-{arch}" if arch in {"x86_64", "aarch64"} else None


def supports_account_isolation() -> bool:
    return _supported_platform_key() is not None


def platform_key() -> str:
    key = _supported_platform_key()
    if key is None:
        raise RuntimeError("Antigravity is supported on Linux, Windows and macOS, on x86-64 and ARM64")
    return key

