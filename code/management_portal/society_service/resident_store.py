from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from management_portal.shared_state import atomic_replace_json, atomic_replace_text


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_replace_json(path, payload, sort_keys=False)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path, limit: int = 50_000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "\n\n[truncated]"
    return text


def replace_file_text(path: Path, text: str) -> None:
    atomic_replace_text(path, text)


def unlink(path: Path) -> None:
    path.unlink(missing_ok=True)
