"""Small value conversions shared by Lineage definition and runtime code."""

from __future__ import annotations

import re
import secrets
import json
from typing import Any


ITEM_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,80}$")


def _clone_json(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe_strings(values: object, *, limit: int = 80, item_limit: int = 500) -> list[str]:
    raw = values if isinstance(values, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        item = _text(value, item_limit)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _safe_actor(actor: str) -> str:
    value = _text(actor, 120)
    if not value:
        raise PermissionError("需要登录后使用 Lineage")
    return value


def _item_id(value: object, prefix: str) -> str:
    candidate = _text(value, 80).lower()
    if candidate and ITEM_ID_RE.fullmatch(candidate):
        return candidate
    return f"{prefix}_{secrets.token_hex(6)}"


def _bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)
