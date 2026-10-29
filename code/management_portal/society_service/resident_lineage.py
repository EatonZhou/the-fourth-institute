from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

from . import (
    identity_contract,
    lineage_catalog,
    resident_catalog,
)
from management_common.clock import utc_now_iso


_ACCESS_SNAPSHOT: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "resident_lineage_access_snapshot",
    default=None,
)






@contextmanager
def access_snapshot_scope() -> Iterator[dict[str, dict[str, Any]]]:
    active = _ACCESS_SNAPSHOT.get()
    if active is not None:
        yield active
        return
    token = _ACCESS_SNAPSHOT.set({})
    try:
        yield _ACCESS_SNAPSHOT.get() or {}
    finally:
        _ACCESS_SNAPSHOT.reset(token)


def cached_access_projection(key: str) -> dict[str, Any] | None:
    snapshot = _ACCESS_SNAPSHOT.get()
    if snapshot is None:
        return None
    value = snapshot.get(str(key or ""))
    return value if isinstance(value, dict) else None


def remember_access_projection(
    key: str,
    projection: Mapping[str, object],
) -> None:
    snapshot = _ACCESS_SNAPSHOT.get()
    if snapshot is not None:
        snapshot[str(key or "")] = dict(projection)


def invalidate_access_projection(key: str) -> None:
    snapshot = _ACCESS_SNAPSHOT.get()
    if snapshot is not None:
        snapshot.pop(str(key or ""), None)


def resident_id(record: Mapping[str, object]) -> str:
    return str(record.get("resident_id") or "").strip()


def lineage_id(record: Mapping[str, object]) -> str:
    value = str(record.get("lineage_id") or "").strip()
    if not identity_contract.is_valid_id(value, "lineage"):
        raise ValueError("lineage_id must be a canonical lineage_* ID")
    return value


def all() -> list[dict[str, Any]]:
    return resident_catalog.get_catalog().all()


def for_lineage(target_lineage_id: str) -> list[dict[str, Any]]:
    return resident_catalog.get_catalog().for_lineage(target_lineage_id)


def canonical_for_lineage(target_lineage_id: str) -> list[dict[str, Any]]:
    """Read Lineage-owned Resident records without ResourceService hydration."""

    return resident_catalog.get_catalog().for_lineage(target_lineage_id)


def find(target_resident_id: str) -> dict[str, Any] | None:
    target = str(target_resident_id or "").strip()
    if not target:
        return None
    try:
        record = resident_catalog.get_catalog().get(target)
    except KeyError:
        return None

    return dict(record)


def new_snapshot(record: Mapping[str, object]) -> dict[str, Any]:
    return {
        "lineage_id": lineage_id(record),
        "anchor_resident_id": resident_id(record),
        "residents": [],
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def snapshot(record: Mapping[str, object]) -> dict[str, Any]:
    anchor_id = resident_id(record)
    payload = new_snapshot(record)
    payload["residents"] = [
        item
        for item in for_lineage(lineage_id(record))
        if resident_id(item) != anchor_id
    ]
    validate_snapshot(record, payload)
    return payload


def canonical_snapshot(record: Mapping[str, object]) -> dict[str, Any]:
    """Build a lock-safe family snapshot containing only canonical state."""

    anchor_id = resident_id(record)
    payload = new_snapshot(record)
    payload["residents"] = [
        item
        for item in canonical_for_lineage(lineage_id(record))
        if resident_id(item) != anchor_id
    ]
    validate_snapshot(record, payload)
    return payload


def validate_snapshot(
    record: Mapping[str, object],
    payload: Mapping[str, object],
) -> None:
    expected_lineage_id = lineage_id(record)
    for item in payload.get("residents") or []:
        if not isinstance(item, Mapping):
            raise ValueError("Lineage Resident snapshot must contain objects")
        identity_contract.validate_id(resident_id(item), "resident")
        if lineage_id(item) != expected_lineage_id:
            raise ValueError("Resident must belong to the same Lineage")


def find_in_snapshot(
    payload: Mapping[str, object],
    target_resident_id: str,
) -> dict[str, Any] | None:
    target = str(target_resident_id or "").strip()
    return next(
        (
            item
            for item in payload.get("residents") or []
            if isinstance(item, dict) and resident_id(item) == target
        ),
        None,
    )


def leader_id(record: Mapping[str, object]) -> str:
    nested = record.get("lineage")
    lineage = nested if isinstance(nested, dict) else {}
    projected = str(
        lineage.get("leader_resident_id")
        or record.get("lineage_leader_resident_id")
        or ""
    ).strip()
    if identity_contract.is_valid_id(projected, "resident"):
        return projected
    try:
        definition = lineage_catalog.get_catalog().get(lineage_id(record))
    except (KeyError, OSError, ValueError):
        return ""
    value = str(definition.get("leader_resident_id") or "").strip()
    return value if identity_contract.is_valid_id(value, "resident") else ""


def is_lineage_leader(record: Mapping[str, object]) -> bool:
    value = resident_id(record)
    return bool(value) and value == leader_id(record)
