"""The resource index record: its format, its validation and the ChangeSet adapters return."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from management_common.resource_identity import get_resource_type_registry
from management_common.text_values import stripped_text
from management_portal.resources.portal import portal_url_for_resource
from management_portal.resources.schema import normalize_resource_attributes


RESOURCE_IDENTITY = get_resource_type_registry()


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_iso() -> str:
    return _utc_iso(datetime.now(timezone.utc))


def _dedupe(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = stripped_text(value)
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _timestamp_iso(value: object) -> str:
    clean = stripped_text(value)
    if not clean:
        return ""
    try:
        return _utc_iso(datetime.fromtimestamp(float(clean), timezone.utc))
    except (TypeError, ValueError, OSError, OverflowError):
        return clean


def _path_modified_at(path: str | Path, fallback: object = "") -> str:
    try:
        return _utc_iso(datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc))
    except (OSError, ValueError):
        return _timestamp_iso(fallback)


def _validate_resource_id(resource_id: str, resource_type: str) -> None:
    RESOURCE_IDENTITY.validate(resource_id, resource_type)


def _json_value(value: object) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"resource index value is not JSON serializable: {exc}") from exc


def resource_record(
    *,
    resource_id: object,
    resource_type: object,
    title: object,
    summary: object = "",
    adapter: str,
    path: object = "",
    modified_at: object = "",
    facets: Iterable[object] = (),
    attributes: dict[str, Any] | None = None,
    search_terms: Iterable[object] = (),
    url: object = "",
) -> dict[str, Any]:
    clean_id = stripped_text(resource_id)
    clean_type = stripped_text(resource_type)
    clean_title = stripped_text(title) or clean_id
    clean_summary = stripped_text(summary)
    clean_path = stripped_text(path)
    clean_facets = _dedupe([clean_type, *facets])
    clean_terms = _dedupe([
        clean_id,
        clean_title,
        *(term for term in search_terms if stripped_text(term) != clean_summary),
    ])
    raw_attributes = dict(attributes) if isinstance(attributes, dict) else attributes
    try:
        clean_attributes = normalize_resource_attributes(raw_attributes)
    except (TypeError, ValueError):
        clean_attributes = raw_attributes
    return {
        "resource_id": clean_id,
        "resource_type": clean_type,
        "title": clean_title,
        "summary": clean_summary,
        "source": {
            "adapter": stripped_text(adapter),
            "path": clean_path,
            "modified_at": stripped_text(modified_at) or _path_modified_at(clean_path),
        },
        "facets": clean_facets,
        "attributes": clean_attributes,
        "search_terms": clean_terms,
        "url": portal_url_for_resource(clean_id, clean_type, record={"attributes": clean_attributes}) or stripped_text(url) or f"/r/{quote(clean_id, safe='')}",
    }


def _validate_record(record: dict[str, Any], adapter: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("resource record must be an object")
    resource_id = stripped_text(record.get("resource_id"))
    resource_type = stripped_text(record.get("resource_type"))
    _validate_resource_id(resource_id, resource_type)
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    if stripped_text(source.get("adapter")) != adapter:
        raise ValueError(f"resource {resource_id} is assigned to the wrong adapter")
    if not stripped_text(record.get("title")):
        raise ValueError(f"resource {resource_id} has no title")
    normalized = _json_value(record)
    normalized["facets"] = _dedupe(normalized.get("facets") or [])
    normalized["search_terms"] = _dedupe(normalized.get("search_terms") or [])
    raw_attributes = normalized.get("attributes")
    if not isinstance(raw_attributes, dict):
        raise ValueError(f"resource {resource_id} attributes must be an object")
    normalized["attributes"] = normalize_resource_attributes(raw_attributes)
    return normalized


@dataclass(frozen=True)
class ChangeSet:
    adapter: str
    upserts: tuple[dict[str, Any], ...] = ()
    deletes: tuple[str, ...] = ()
    source_state: dict[str, Any] = field(default_factory=dict)
