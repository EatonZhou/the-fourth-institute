"""Bounded Resident projections and path normalization."""
from __future__ import annotations
import copy
import re
from pathlib import Path
from typing import Any, Mapping

def _optional_absolute_paths(value: object) -> list[str]:
    from . import resident_project_domain
    if isinstance(value, str):
        raw_items = re.split(r"[\r\n,]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    seen: set[str] = set()
    paths: list[str] = []
    for item in raw_items:
        raw = str(item or "").strip()
        if not raw:
            continue
        path = resident_project_domain.optional_absolute_path(raw)
        if path and path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def _safe_relative_path(raw: object, default: str = "") -> str:
    text = str(raw or default or "").strip().replace("\\", "/").strip("/")
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        raise ValueError("relative path 不合法")
    return path.as_posix()


def _resident_lineage_node_projection(
    record: Mapping[str, Any],
) -> dict[str, Any]:
    """Bound the Resident data that may cross into the Lineage domain."""

    keys = (
        "principal_hierarchy",
        "resident_id",
        "lineage_id",
        "title",
        "lineage_title",
        "backend",
        "created_at",
        "updated_at",
        "workspace_path",
        "lineage_project_id",
        "project_id",
    )
    projection = {
        key: copy.deepcopy(record.get(key))
        for key in keys
        if key in record
    }
    return projection

