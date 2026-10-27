"""Deployment-owned Provider entry paths, preserving the final version link."""
from __future__ import annotations

from pathlib import Path

from management_common.portal_paths import resolve_directory


def runtime_entry(settings: dict, key: str, default: Path) -> Path:
    """Resolve the parent only: resolving the leaf would pin today's version.

    Explicit entries are absolute deployment paths. Source-only installations
    retain their portable bootstrap defaults until an entry is configured.
    """
    if key not in settings:
        return default
    value = settings[key]
    if (not isinstance(value, str) or not value.strip()
            or any(c in value for c in "\0\r\n")):
        raise ValueError(f"deployment.providers.{key} must be an absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise ValueError(f"deployment.providers.{key} must be an absolute path")
    return resolve_directory(str(path.parent)) / path.name
