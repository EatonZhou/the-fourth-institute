from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .state_store import get_lineage_data_store
from .lineage_primitives import now_iso as _now_iso


STATE_SCHEMA = "tfi.hatg-graph-state.v1"
MAX_STATE_CELLS = 4096
MAX_VALUE_BYTES = 128 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024
_EXPECTED_VALUE_UNSET = object()
MAX_TTL_SECONDS = 2**31 - 1


def empty_state() -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "revision": 0,
        "cells": {},
        "updated_at": "",
    }


def _key(value: object) -> str:
    key = str(value or "").strip()
    if (
        not key
        or len(key) > 160
        or any(ord(char) < 32 for char in key)
    ):
        raise ValueError("Graph State key 无效")
    return key


def _value(value: object) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Graph State value 必须是 JSON 值") from exc
    if len(encoded) > MAX_VALUE_BYTES:
        raise ValueError(f"单个 Graph State value 不能超过 {MAX_VALUE_BYTES // 1024} KiB")
    return json.loads(encoded.decode("utf-8"))


def normalize_state_key(value: object) -> str:
    """Validate one public Graph State cell key."""

    return _key(value)


def normalize_state_value(value: object) -> Any:
    """Return one isolated, size-bounded JSON Graph State value."""

    return _value(value)


def _normalize(raw: object) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    cells = item.get("cells") if isinstance(item.get("cells"), dict) else {}
    normalized_cells: dict[str, Any] = {}
    for raw_key, raw_value in cells.items():
        normalized_cells[_key(raw_key)] = _value(raw_value)
    if len(normalized_cells) > MAX_STATE_CELLS:
        raise ValueError(f"Graph State 最多包含 {MAX_STATE_CELLS} 个 cell")
    result = {
        "schema": STATE_SCHEMA,
        "revision": max(0, int(item.get("revision") or 0)),
        "cells": normalized_cells,
        "updated_at": str(item.get("updated_at") or ""),
    }
    if len(
        json.dumps(result, ensure_ascii=False).encode("utf-8")
    ) > MAX_STATE_BYTES:
        raise ValueError("Graph State 总大小不能超过 2 MiB")
    raw_expiry = item.get("expires_at")
    expiry = {
        key: value for key, value in (raw_expiry if isinstance(raw_expiry, dict) else {}).items()
        if key in normalized_cells and isinstance(value, str)
    }
    if expiry:
        result["expires_at"] = expiry
    receipts = item.get("expiration_observations")
    if receipts:
        result["expiration_observations"] = receipts
    return result


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_time(now: datetime, ttl_seconds: int) -> str:
    return (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="microseconds")


def _due_keys(state: dict[str, Any], now: datetime) -> list[str]:
    instant = now.isoformat(timespec="microseconds")
    return sorted(key for key, deadline in state.get("expires_at", {}).items() if deadline <= instant)


def _touch(state: dict[str, Any], count: int = 1) -> None:
    state["revision"] += count
    state["updated_at"] = _now_iso()


def _expire(lineage_id: str, state: dict[str, Any], now: datetime) -> None:
    due = _due_keys(state, now)
    if not due:
        return
    # Retain one marker only for a key watched by a live graph definition.
    # A replacement before the next scan must not hide the expiration, even
    # when it restores the same value. Absence due to expiry matches no predicate.
    store = get_lineage_data_store()
    with store.database.read() as connection:
        watched = {
            row["key"] for row in connection.execute("""
                SELECT c.config_json::jsonb->>'key' AS key
                FROM lineage_nodes n JOIN lineage_components c USING(node_id)
                WHERE n.lineage_id=%s AND n.node_type='event_source' AND n.deleted_at IS NULL
                AND (c.config_json::jsonb->>'event_type'='state.changed'
                     OR (c.config_json::jsonb->>'event_type'='state.condition_met'
                         AND c.config_json::jsonb->>'operator'='missing'))
            """, (lineage_id,)).fetchall()
        }
    observations = {key: value for key, value in state.get("expiration_observations", {}).items()
                    if key in watched}
    for index, key in enumerate(due, 1):
        del state["cells"][key]
        del state["expires_at"][key]
        if key in watched:
            observations[key] = {"revision": state["revision"] + index, "absent": True}
    state["expiration_observations"] = observations
    _touch(state, len(due))


@contextmanager
def _state_transaction(lineage_id: str) -> Iterator[tuple[dict[str, Any], datetime]]:
    with get_lineage_data_store().lineage_shared_state.transaction(lineage_id) as stored:
        state = _normalize(stored)
        previous_revision = state["revision"]
        stored.clear()
        stored.update(state)
        now = _now()
        _expire(lineage_id, stored, now)
        yield stored, now
        normalized = _normalize(stored)
        stored.clear()
        stored.update(normalized)
    if normalized["revision"] != previous_revision:
        from .changes import lineage_changed

        lineage_changed(lineage_id, runtime=True)


def read_state(lineage_id: str) -> dict[str, Any]:
    state = _normalize(get_lineage_data_store().lineage_shared_state.get(lineage_id))
    for key in _due_keys(state, _now()):
        del state["cells"][key]
    state.pop("expires_at", None)
    state.pop("expiration_observations", None)
    return state


def expire_due(lineage_id: str) -> None:
    # Probe before locking so graphs with permanent state are read-only on a poll.
    raw = get_lineage_data_store().lineage_shared_state.get(lineage_id)
    if _due_keys(raw, _now()):
        with _state_transaction(lineage_id):
            pass


def read_observable_state(lineage_id: str) -> dict[str, Any]:
    """Observe expiry only after the deletion and its revision are durable."""
    raw = get_lineage_data_store().lineage_shared_state.get(lineage_id)
    if not _due_keys(raw, _now()):
        return _normalize(raw)
    with _state_transaction(lineage_id) as (state, _):
        return deepcopy(state)


def due_lineage_ids() -> list[str]:
    """Include due cells in graphs with no automatic Event Source."""
    store = get_lineage_data_store()
    now = _now().isoformat(timespec="microseconds")
    with store.database.read() as connection:
        return [row["lineage_id"] for row in connection.execute("""
            SELECT lineage_id FROM lineage_shared_state
            WHERE EXISTS (
                SELECT 1 FROM jsonb_each_text(cells_json::jsonb->'expires_at') AS expiry(key, deadline)
                WHERE expiry.deadline <= %s
            )
            ORDER BY lineage_id
        """, (now,)).fetchall()]


def read_cell(
    lineage_id: str,
    key: object,
) -> dict[str, Any]:
    clean_key = _key(key)
    state = read_state(lineage_id)
    exists = clean_key in state["cells"]
    return {
        "key": clean_key,
        "exists": exists,
        "value": deepcopy(state["cells"].get(clean_key)),
        "state_revision": int(state["revision"]),
        "updated_at": str(state.get("updated_at") or ""),
    }


def update_cell(
    lineage_id: str,
    key: object,
    value: object,
    expected_value: object = _EXPECTED_VALUE_UNSET,
    *,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Write one cell atomically, optionally only while its current value equals ``expected_value``.

    A missing cell reads as null, so an expected null writes only an absent or null cell.
    """

    clean_key = _key(key)
    expected_supplied = expected_value is not _EXPECTED_VALUE_UNSET
    clean_expected = _value(expected_value) if expected_supplied else None
    clean_value = _value(value)
    if ttl_seconds is not None and (type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS):
        raise ValueError(f"ttl_seconds 必须是 1 到 {MAX_TTL_SECONDS} 的整数")
    with _state_transaction(lineage_id) as (state, now):
        existed = clean_key in state["cells"]
        previous_value = deepcopy(state["cells"].get(clean_key))
        matched = not expected_supplied or previous_value == clean_expected
        changed = matched and (not existed or previous_value != clean_value)
        if matched:
            deadline = _expiry_time(now, ttl_seconds) if ttl_seconds is not None else ""
            if deadline:
                state.setdefault("expires_at", {})[clean_key] = deadline
            elif "expires_at" in state:
                state["expires_at"].pop(clean_key, None)
        if changed:
            if not existed and len(state["cells"]) >= MAX_STATE_CELLS:
                raise ValueError(f"Graph State 最多包含 {MAX_STATE_CELLS} 个 cell")
            state["cells"][clean_key] = clean_value
            marker = state.get("expiration_observations", {}).get(clean_key)
            if marker:
                marker["absent"] = False
            _touch(state)
        return {
            "key": clean_key,
            "previous_value": previous_value,
            **({"expected_value": deepcopy(clean_expected)} if expected_supplied else {}),
            "value": deepcopy(clean_value if matched else previous_value),
            "changed": changed,
            "matched": matched,
            **({"expires_at": state["expires_at"][clean_key]} if clean_key in state.get("expires_at", {}) else {}),
            "state_revision": int(state["revision"]),
            "updated_at": str(state.get("updated_at") or ""),
        }


def delete_cell(
    lineage_id: str,
    key: object,
    expected_value: object = _EXPECTED_VALUE_UNSET,
) -> dict[str, Any]:
    """Delete one cell atomically, optionally guarded by its current value."""

    clean_key = _key(key)
    expected_supplied = expected_value is not _EXPECTED_VALUE_UNSET
    clean_expected = (
        _value(expected_value) if expected_supplied else None
    )
    with _state_transaction(lineage_id) as (state, _):
        existed = clean_key in state["cells"]
        previous_value = deepcopy(state["cells"].get(clean_key))
        matched = (
            not expected_supplied
            or previous_value == clean_expected
        )
        deleted = existed and matched
        if deleted:
            del state["cells"][clean_key]
            state.get("expires_at", {}).pop(clean_key, None)
            _touch(state)
        return {
            "key": clean_key,
            "previous_value": previous_value,
            **(
                {"expected_value": deepcopy(clean_expected)}
                if expected_supplied
                else {}
            ),
            "matched": matched,
            "deleted": deleted,
            "changed": deleted,
            "state_revision": int(state["revision"]),
            "updated_at": str(state.get("updated_at") or ""),
        }
