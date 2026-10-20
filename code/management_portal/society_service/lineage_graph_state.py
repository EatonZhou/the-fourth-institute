from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Iterator

from .state_store import get_lineage_data_store
from .lineage_primitives import now_iso as _now_iso


STATE_SCHEMA = "tfi.hatg-graph-state.v1"
MAX_STATE_CELLS = 128
MAX_VALUE_BYTES = 64 * 1024
MAX_STATE_BYTES = 512 * 1024
_EXPECTED_VALUE_UNSET = object()


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
        raise ValueError("单个 Graph State value 不能超过 64 KiB")
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
        raise ValueError("Graph State 最多包含 128 个 cell")
    result = {
        "schema": STATE_SCHEMA,
        "revision": max(0, int(item.get("revision") or 0)),
        "cells": normalized_cells,
        "updated_at": str(item.get("updated_at") or ""),
    }
    if len(
        json.dumps(result, ensure_ascii=False).encode("utf-8")
    ) > MAX_STATE_BYTES:
        raise ValueError("Graph State 总大小不能超过 512 KiB")
    return result


@contextmanager
def _state_transaction(lineage_id: str) -> Iterator[dict[str, Any]]:
    with get_lineage_data_store().lineage_shared_state.transaction(lineage_id) as stored:
        state = _normalize(stored)
        previous_revision = state["revision"]
        stored.clear()
        stored.update(state)
        yield stored
        normalized = _normalize(stored)
        stored.clear()
        stored.update(normalized)
    if normalized["revision"] != previous_revision:
        from .changes import lineage_changed

        lineage_changed(lineage_id, runtime=True)


def read_state(lineage_id: str) -> dict[str, Any]:
    return deepcopy(
        _normalize(
            get_lineage_data_store().lineage_shared_state.get(lineage_id)
        )
    )


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
) -> dict[str, Any]:
    """Write one cell atomically, optionally only while its current value equals ``expected_value``.

    A missing cell reads as null, so an expected null writes only an absent or null cell.
    """

    clean_key = _key(key)
    expected_supplied = expected_value is not _EXPECTED_VALUE_UNSET
    clean_expected = _value(expected_value) if expected_supplied else None
    clean_value = _value(value)
    with _state_transaction(lineage_id) as state:
        existed = clean_key in state["cells"]
        previous_value = deepcopy(state["cells"].get(clean_key))
        matched = not expected_supplied or previous_value == clean_expected
        changed = matched and (not existed or previous_value != clean_value)
        if changed:
            if not existed and len(state["cells"]) >= MAX_STATE_CELLS:
                raise ValueError("Graph State 最多包含 128 个 cell")
            state["cells"][clean_key] = clean_value
            state["revision"] = int(state["revision"]) + 1
            state["updated_at"] = _now_iso()
            normalized = _normalize(state)
            state.clear()
            state.update(normalized)
        return {
            "key": clean_key,
            "previous_value": previous_value,
            **({"expected_value": deepcopy(clean_expected)} if expected_supplied else {}),
            "value": deepcopy(clean_value if matched else previous_value),
            "changed": changed,
            "matched": matched,
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
    with _state_transaction(lineage_id) as state:
        existed = clean_key in state["cells"]
        previous_value = deepcopy(state["cells"].get(clean_key))
        matched = (
            not expected_supplied
            or previous_value == clean_expected
        )
        deleted = existed and matched
        if deleted:
            del state["cells"][clean_key]
            state["revision"] = int(state["revision"]) + 1
            state["updated_at"] = _now_iso()
            normalized = _normalize(state)
            state.clear()
            state.update(normalized)
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
