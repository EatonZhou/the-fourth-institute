from __future__ import annotations

import json
import mimetypes
from typing import Any, Iterable
from urllib.parse import quote


from management_portal.resources import get_resource_service


def _text(value: object) -> str:
    return str(value or "").strip()


def _json_value(value: object) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _query_all(service: Any, **kwargs: Any) -> dict[str, Any]:
    """Every record a query matches, read page by page.

    A ResourceService query returns at most 5,000 records per call; a single
    call silently cut a list at that size (the Paper Library had 4,055
    papers). The first page's metadata is kept and ``resources`` holds all
    pages, each record once even if the index changed between pages.
    """
    payload: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    while True:
        page = service.query(**kwargs, limit=5000, offset=offset)
        if not payload:
            payload = dict(page)
        page_records = [
            record
            for record in page.get("resources") or []
            if isinstance(record, dict)
        ]
        for record in page_records:
            resource_id = _text(record.get("resource_id"))
            if resource_id and resource_id in seen:
                continue
            seen.add(resource_id)
            records.append(record)
        offset += len(page_records)
        if not page_records or offset >= int(page.get("total") or 0):
            break
    payload["resources"] = records
    return payload


def _knowledge_item(record: dict[str, Any]) -> dict[str, Any]:
    attrs = (
        record.get("attributes")
        if isinstance(record.get("attributes"), dict)
        else {}
    )
    resource_id = _text(record.get("resource_id"))
    project_ids = list(attrs.get("project_ids") or [])
    item = {
        "kb_id": resource_id,
        "resource_type": _text(record.get("resource_type")),
        "title": _text(record.get("title")),
        "preview": _text(record.get("summary")),
        "project": _text(project_ids[0]) if project_ids else "",
    }
    for key in (
        "type",
        "created_at",
        "version",
        "status",
        "importance",
        "ai_generated",
        "filename",
        "mtime",
        "file_count",
        "source",
        "source_label",
        "read_scope",
        "label",
        "kind",
    ):
        if key in attrs:
            item[key] = _json_value(attrs[key])
    return item


def knowledge_index(
    *,
    active_only: bool = True,
    semantic_search_id: str = "",
    offset: int = 0,
    limit: int | None = None,
    refresh: bool = False,
    wait_seconds: float = 0,
    excluded_resource_types: Iterable[str] = (),
    actor: str = "",
    service: Any = None,
) -> dict[str, Any]:
    service = service or get_resource_service()
    semantic_id = _text(semantic_search_id)
    semantic_view = None
    if semantic_id:
        from management_portal import semantic_search_jobs

        semantic_view = semantic_search_jobs.search_view(
            semantic_id, actor=actor, surfaces=semantic_search_jobs.RESOURCE_SCOPES,
        )
    excluded_types = {
        _text(value)
        for value in excluded_resource_types
        if _text(value)
    }
    refresh_adapters = ["project_knowledge"]
    if "skill" not in excluded_types:
        refresh_adapters.append("skill")
    if refresh:
        service.refresh(
            adapters=refresh_adapters,
            wait=bool(wait_seconds),
            timeout=wait_seconds or 180,
        )
    payload = _query_all(
        service,
        facets=["knowledge"],
        actor=str(actor or ""),
        include_attributes=True,
    )
    records = list(payload.get("resources") or [])
    if excluded_types:
        records = [
            record
            for record in records
            if _text(record.get("resource_type")) not in excluded_types
        ]
    if active_only:
        records = [
            record
            for record in records
            if _text((record.get("attributes") or {}).get("project_status"))
            in {"active", "system"}
        ]
    records.sort(
        key=lambda record: (
            int((record.get("attributes") or {}).get("importance") or 0),
            _text((record.get("attributes") or {}).get("created_at")),
            int((record.get("attributes") or {}).get("mtime") or 0),
        ),
        reverse=True,
    )
    items = [_knowledge_item(record) for record in records]
    semantic_summary = None
    if semantic_view:
        semantic_summary = semantic_view["summary"]
        if semantic_summary:
            results = semantic_view["result_map"]
            items = [
                {
                    **item,
                    "semantic_rank": int(results[item["kb_id"]].get("rank") or 0),
                    "semantic_score": int(results[item["kb_id"]].get("score") or 0),
                    "semantic_reason": _text(
                        results[item["kb_id"]].get("reason")
                    ),
                }
                for item in items
                if item["kb_id"] in results
            ]
            items.sort(
                key=lambda item: (
                    int(item.get("semantic_rank") or 10_000),
                    _text(item.get("title")),
                )
            )
    state = service.projection_state("project_knowledge")
    skill_state = service.projection_state("skill")
    visible_project_ids = {
        _text((record.get("attributes") or {}).get("project_ids", [""])[0])
        for record in records
        if (record.get("attributes") or {}).get("project_ids")
    }
    projects = [
        dict(item)
        for item in state.get("projects") or []
        if isinstance(item, dict)
        and (
            not actor
            or _text(item.get("id")) in visible_project_ids
        )
    ]
    if "skill" not in excluded_types:
        known_projects = {_text(item.get("id")) for item in projects}
        projects.extend(
            dict(item)
            for item in skill_state.get("projects") or []
            if isinstance(item, dict)
            and _text(item.get("id")) not in known_projects
        )
    allowed_types = list(state.get("allowed_types") or [])
    if "skill" not in allowed_types:
        allowed_types.append("skill")
    if excluded_types:
        allowed_types = [
            resource_type
            for resource_type in allowed_types
            if resource_type not in excluded_types
        ]
    total = len(items)
    safe_offset = max(0, int(offset or 0))
    safe_limit = total if limit is None else max(1, int(limit))
    page_items = items[safe_offset : safe_offset + safe_limit]
    status = service.status()
    return {
        "generated_at": payload.get("index_updated_at"),
        "allowed_types": allowed_types,
        "projects": projects,
        "items": page_items,
        "semantic_search": semantic_summary,
        "refresh": {
            "running": bool(
                set(refresh_adapters)
                & set(status.get("running_adapters") or [])
            ),
        },
        "page": {
            "offset": safe_offset,
            "limit": safe_limit,
            "count": len(page_items),
            "total": total,
            "next_offset": safe_offset + len(page_items),
            "has_more": safe_offset + len(page_items) < total,
        },
    }


def knowledge_item(
    resource_id: str,
    *,
    service: Any = None,
    actor: str = "",
) -> dict[str, Any]:
    resource_service = service or get_resource_service()
    record = (
        resource_service.get(resource_id, actor=actor)
        if actor
        else resource_service.get(resource_id)
    )
    if "knowledge" not in set(record.get("facets") or []):
        raise KeyError(resource_id)
    return _knowledge_item(record)


def knowledge_detail(
    address: str,
    *,
    service: Any = None,
    actor: str = "",
) -> dict[str, Any]:
    """Project authorized Resource content into the Knowledge document view."""
    clean_address = _text(address).strip("/")
    if not clean_address:
        raise ValueError("knowledge address required")
    resource_service = service or get_resource_service()
    resolved = resource_service.resolve(clean_address, actor=_text(actor))
    detail = resolved.get("detail")
    if not isinstance(detail, dict):
        raise KeyError("knowledge item not found")
    kind = _text(resolved.get("content_kind")) or "binary"
    content_type = mimetypes.guess_type(_text(resolved.get("filename")))[0] or "application/octet-stream"
    data = b""
    if kind in {"markdown", "text", "code"}:
        data, content_type, resolved = resource_service.read_content(
            clean_address, actor=_text(actor),
        )
    content_url = "/api/resources/content?address=" + quote(
        _text(resolved.get("address")) or clean_address, safe="",
    )
    return {
        **_json_value(detail),
        "address": _text(resolved.get("address")) or clean_address,
        "path": _text(resolved.get("resolved_path")),
        "filename": _text(resolved.get("filename")),
        "kind": kind,
        "language": _text(resolved.get("content_language")),
        "media_type": content_type,
        "text": data.decode("utf-8", errors="replace") if kind in {"markdown", "text", "code"} else "",
        "raw_url": content_url if kind in {"image", "video", "audio", "pdf"} else "",
        "download_url": content_url + "&download=1",
    }


def project_index(*, actor: str = "", service: Any = None) -> dict[str, Any]:
    service = service or get_resource_service()
    payload = _query_all(
        service,
        resource_types=["project"],
        actor=str(actor or ""),
        include_attributes=True,
    )
    state = service.projection_state("project")
    projects = []
    for record in payload.get("resources") or []:
        if not isinstance(record, dict):
            continue
        attributes = (
            record.get("attributes")
            if isinstance(record.get("attributes"), dict)
            else {}
        )
        project_id = _text(record.get("resource_id"))
        projects.append(
            {
                "resource_id": project_id,
                "name": _text(
                    attributes.get("name") or record.get("title")
                ),
                "status": _text(attributes.get("status")),
                "project_type": _text(attributes.get("project_type")),
                "description": _text(record.get("summary")),
                "path": _text(attributes.get("path")),
                "ddl": _text(attributes.get("ddl")),
                "tags": list(attributes.get("tags") or []),
            }
        )
    order = {
        _text(project_id): index
        for index, project_id in enumerate(state.get("project_ids") or [])
    }
    projects.sort(
        key=lambda item: (
            order.get(_text(item.get("resource_id")), len(order)),
            _text(item.get("resource_id")).casefold(),
        )
    )
    schema_version = state.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise RuntimeError("Project projection requires schema_version 1")
    return {
        "schema_version": schema_version,
        "status_vocab": list(state.get("status_vocab") or []),
        "tag_vocab": list(state.get("tag_vocab") or []),
        "projects": projects,
    }








def paper_records(*, actor: str = "", service: Any = None) -> list[dict[str, Any]]:
    payload = _query_all(
        service or get_resource_service(),
        resource_types=["paper"],
        actor=str(actor or ""),
        include_attributes=True,
    )
    papers = []
    for record in payload.get("resources") or []:
        attributes = dict(record.get("attributes") or {})
        attributes["id"] = record["resource_id"]
        attributes["title"] = record["title"]
        attributes["abstract"] = (
            record.get("summary")
            or attributes.get("abstract")
            or ""
        )
        papers.append(attributes)
    return papers
