"""Resident domain responsibilities: project."""
from __future__ import annotations

from pathlib import Path


def optional_absolute_path(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("开发地址必须是绝对路径")
    return str(path.resolve(strict=False))
