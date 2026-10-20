from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def canonical_record(
    resource_id: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate a domain object into the one Resource Record identity contract."""
    clean_id = str(resource_id or "").strip()
    if not clean_id:
        raise ValueError("resource provider returned no resource ID")
    record = dict(value)
    for key, item in tuple(record.items()):
        if (
            key != "resource_id"
            and (key == "id" or key.endswith("_id"))
            and str(item or "").strip() == clean_id
        ):
            record.pop(key)
    record["resource_id"] = clean_id
    return record


def canonical_resolution(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("resource provider must return an object")
    resolution = dict(value)
    resource_id = str(
        resolution.get("root_id") or resolution.get("resource_id") or ""
    ).strip()
    record = resolution.get("record")
    if not isinstance(record, Mapping):
        raise TypeError("resource provider resolution requires a record object")
    resolution["record"] = canonical_record(resource_id, record)
    for key, item in tuple(resolution.items()):
        if (
            key not in {"root_id", "resource_id"}
            and (key == "id" or key.endswith("_id"))
            and str(item or "").strip() == resource_id
        ):
            resolution.pop(key)
    return resolution


PUBLIC_LOCATION = "@public"


def _location_items(value: object) -> list[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        values = []
    return [str(item).strip() for item in values if str(item).strip()]


def explicit_project_ids(value: object, kind: str) -> list[str]:
    """``registration_project_ids`` for a type whose location must be given: Projects, or ``@public``."""
    if not _location_items(value):
        raise ValueError(f"{kind} 需要在 project_ids 中给出所属 Project；公共内容写 {PUBLIC_LOCATION}")
    return registration_project_ids(value)


def registration_project_ids(value: object) -> list[str]:
    """The Projects a registration names; ``@public`` alone names public content (no Project)."""
    projects = _location_items(value)
    if PUBLIC_LOCATION in projects:
        if len(projects) > 1:
            raise ValueError(f"{PUBLIC_LOCATION} 表示公共内容，不能和 Project 同时给出")
        return []
    return projects
