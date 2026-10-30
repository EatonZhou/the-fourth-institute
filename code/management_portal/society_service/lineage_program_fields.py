"""Value rules of authored Program fields: unknown fields, client keys, booleans, canvas
positions, thresholds, bounded integers, Workspace paths, times and IDs.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Any, Iterable

from . import lineage_primitives
from .lineage_program_vocabulary import MAX_PATH_CHARS, _CLIENT_KEY_RE, _RESIDENT_ID_RE
from .lineage_values import _text


def _reject_unknown_fields(value: dict[str, Any], allowed: Iterable[str], subject: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{subject} 不接受字段：" + "、".join(unknown))


def _client_key(item: dict[str, Any]) -> dict[str, str]:
    client_key = _text(item.get("client_key"), 160)
    if not client_key:
        return {}
    if not _CLIENT_KEY_RE.fullmatch(client_key):
        raise ValueError(
            "Program client_key 必须以字母或数字开头，且只能包含"
            "字母、数字、点、下划线、冒号和连字符"
        )
    return {"client_key": client_key}


def _boolean(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _text(value, 16).lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _position(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return round(number, 2)


def _percentage_threshold(
    value: object,
    *,
    default: float = 85.0,
) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        number = default
    else:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "百分比阈值必须是 0 到 100 之间的数字"
            ) from exc
    if not math.isfinite(number) or number < 0 or number > 100:
        raise ValueError("百分比阈值必须在 0 到 100 之间")
    return round(number, 1)


def _bounded_integer(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    field: str,
) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        number = default
    else:
        if isinstance(value, bool):
            raise ValueError(f"{field} 必须是整数")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 必须是整数") from exc
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise ValueError(f"{field} 必须是整数")
        number = int(numeric)
    if number < minimum or number > maximum:
        raise ValueError(
            f"{field} 必须在 {minimum} 到 {maximum} 之间"
        )
    return number


def _relative_path(value: object, *, required: bool = True) -> str:
    return lineage_primitives.normalize_workspace_relative_path(
        value,
        required=required,
        max_chars=MAX_PATH_CHARS,
    )


def _iso_datetime(value: object) -> str:
    raw = _text(value, 80)
    if not raw:
        raise ValueError("time.reached Event Source 必须设置触发时间")
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Event Source 时间必须使用有效的 ISO 日期时间") from exc
    return raw


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\0".join(str(part or "") for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _resident_id(value: object, *, field: str) -> str:
    resident_id = _text(value, 80).lower()
    if not _RESIDENT_ID_RE.fullmatch(resident_id):
        raise ValueError(f"{field} 必须是当前 HATG 中规范的 resident_* ID")
    return resident_id
