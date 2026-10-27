"""Resident domain responsibilities: directory."""
from __future__ import annotations
from . import business_fact_views

import os

from . import (
    family_facade,
    resident_access,
    resident_backend,
    resident_catalog,
    resident_conversation_projection_domain,
    resident_conversation_stream,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_lineage,
    resident_record_domain,
    resident_runtime_domain,
    resident_watchboard_domain,
)
from .domain_ports import execution_port, resource_port
from .resident_access import (
    can_manage_resident as _can_manage_resident,
    ensure_resident_visible_to_actor,
    resident_visible_to_actor as _resident_visible_to_actor,
)
from management_common.actor_identity import canonical_actor_id
from management_common.database_time import instant
from management_portal.execution.accounts import shared_account_configuration
from management_portal.portal_access_control import is_active_user_or_resident
from pathlib import Path
from typing import Any, Iterable, Mapping
import copy
from management_portal.execution import resident_runtime_settings
from management_portal import project_registry

from . import resident_domain_common


RECENT_RESIDENT_MESSAGES = 120

OWNER_DERIVED_RESIDENT_LIMIT = 0

RESIDENT_RESOURCE_PROJECTION_SCHEMA = "tfi.resident-resource-projection.v1"

RESIDENT_RESOURCE_ACTIVITY_LIMIT = 24

RESIDENT_RESOURCE_ACTIVITY_READ_LIMIT = 48

RESIDENT_RESOURCE_ACTIVITY_ITEM_CHARS = 6000


CONTEXT_PRESSURE_WARM_PERCENT = 70.0

CONTEXT_PRESSURE_HOT_PERCENT = 85.0

CONTEXT_PRESSURE_WARM_FALLBACK_TOKENS = max(
    1,
    int(os.getenv("RESIDENT_CONTEXT_PRESSURE_WARM_TOKENS", "160000")),
)

CONTEXT_PRESSURE_HOT_FALLBACK_TOKENS = max(
    CONTEXT_PRESSURE_WARM_FALLBACK_TOKENS,
    int(os.getenv("RESIDENT_CONTEXT_PRESSURE_HOT_TOKENS", "220000")),
)


def _lineage_summary(record: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    del registry
    identity = resident_runtime_domain.lineage_identity_record(record)
    lineage_node_ids = list(
        identity.get("lineage_node_ids")
        or []
    )
    return {
        "lineage_id": resident_lineage.lineage_id(record),
        "title": str(record.get("lineage_title") or record.get("title") or ""),
        "resident_count": len(lineage_node_ids),
        "lineage_node_ids": lineage_node_ids,
        "residents_preview": [],
    }


def _annotate_resident_lineage_fields(
    record: dict[str, Any],
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lineage_id = resident_lineage.lineage_id(record)
    record["lineage_id"] = lineage_id
    record["backend_capabilities"] = resident_runtime_domain.resident_backend_capabilities(record)
    record["lineage_resident"] = resident_lineage_resident(record)
    record["lineage"] = _lineage_summary(record, registry)
    return record


def _engaged_conversation_records(
    resident_ids: Iterable[str],
    actor: str,
) -> list[dict[str, Any]]:
    """Return visible identity facts for Residents engaged by the actor."""

    requested = list(dict.fromkeys(
        str(resident_id or "").strip()
        for resident_id in resident_ids
        if str(resident_id or "").strip()
    ))
    if not requested:
        return []
    visible = resident_access.visible_resident_records(requested, actor, "portal")
    # The account is the route the Resident's session runs on, for every Provider.
    binding_ids = [resident_backend.execution_binding_id(record) for record in visible]
    bindings = resident_backend.session_routes_for_bindings(binding_ids) if binding_ids else {}
    summaries: list[dict[str, Any]] = []
    with shared_account_configuration():
        for record in visible:
            summaries.append({
                key: copy.deepcopy(record.get(key))
                for key in (
                    "resident_id",
                    "title",
                    "lineage_id",
                    "lineage_title",
                    "created_at",
                    "updated_at",
                )
                if record.get(key) not in (None, "", [], {})
            })
            summaries[-1]["account_id"] = resident_backend.account_id(
                record,
                binding=bindings.get(resident_backend.execution_binding_id(record), {}),
            )
    return summaries


def _list_actor_conversation_records(
    actor: str,
    resident_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return exactly the Residents with conversation activity by actor."""

    actor_id = canonical_actor_id(actor)
    requested_ids = (
        None if resident_ids is None else list(dict.fromkeys(
            str(item or "").strip() for item in resident_ids
            if str(item or "").strip()
        ))
    )
    if requested_ids == []:
        return {"residents": []}
    actor_activity = resident_conversation_stream.get_repository().actor_activity(
        actor_id, resident_ids=requested_ids,
    )
    requested = (
        list(actor_activity)
        if requested_ids is None
        else [
            resident_id for resident_id in requested_ids
            if resident_id in actor_activity
        ]
    )
    with resident_lineage.access_snapshot_scope():
        residents = _engaged_conversation_records(
            requested,
            actor_id,
        )
        summaries = resident_conversation_projection_domain.conversation_family_summaries(
            [str(item.get("resident_id") or "") for item in residents],
            actor_id,
        )
    for resident in residents:
        resident.update(
            summaries.get(str(resident.get("resident_id") or ""), {})
        )
        # Lists order and page conversations by this time; entries show it.
        resident["activity_at"] = actor_activity.get(str(resident.get("resident_id") or ""), "")
    residents.sort(
        key=lambda item: actor_activity.get(
            str(item.get("resident_id") or ""),
            "",
        ),
        reverse=True,
    )
    return {"residents": residents}


def list_resident_conversation_activity(
    actor: str,
    resident_ids: Iterable[str] | None = None,
    *,
    page_size: int | None = None,
    page_token: str = "",
) -> dict[str, Any]:
    """Return actor-scoped Resident conversation activity facts."""

    if page_size is not None:
        if resident_ids is not None:
            raise ValueError("Targeted conversations do not use list pagination")
        size = int(page_size)
        if not 1 <= size <= 100:
            raise ValueError("Conversation page size must be between 1 and 100")
        actor_id = canonical_actor_id(actor)
        repository = resident_conversation_stream.get_repository()
        residents: list[dict[str, Any]] = []
        next_token = page_token
        activity: dict[str, str] = {}
        # "清除全部" hides a conversation until its activity passes the clear
        # (agent_conversation_index_backend.decorate_entries).  Leave those out
        # before their records and summaries are read, so that a page fills
        # with conversations the Mailbox shows (one user had hidden conversations).
        hidden = repository.dismissed_activity(actor_id)
        # Permission filtering can require several candidate pages for one
        # response. They share one lazy account build, just like a single page.
        # Each candidate page groups all of the actor's activity whatever its
        # size (a large input history took under a second), so read the most a page allows.
        with shared_account_configuration():
            for page in repository.iter_actor_activity_pages(
                actor_id, limit=100, page_token=page_token,
            ):
                activity.update(page["activity"])
                shown = [
                    resident_id for resident_id, at in page["activity"].items()
                    if resident_id not in hidden or instant(at) > hidden[resident_id]
                ]
                with resident_lineage.access_snapshot_scope():
                    residents.extend(_engaged_conversation_records(shown, actor_id))
                next_token = page["next_page_token"]
                if len(residents) >= size or not next_token:
                    break
        order = {resident_id: index for index, resident_id in enumerate(activity)}
        residents.sort(key=lambda item: order[str(item["resident_id"])])
        has_more = bool(next_token) or len(residents) > size
        residents = residents[:size]
        summaries = resident_conversation_projection_domain.conversation_family_summaries(
            [str(item["resident_id"]) for item in residents], actor_id,
        )
        for resident in residents:
            resident.update(summaries.get(str(resident["resident_id"]), {}))
            resident["activity_at"] = activity[str(resident["resident_id"])]
        if has_more and residents:
            last_id = str(residents[-1]["resident_id"])
            next_token = repository.activity_page_token(last_id, activity[last_id])
        return {"residents": residents, "next_page_token": next_token}
    return _list_actor_conversation_records(actor, resident_ids)


def get_resident_user_message_counts(
    resident_ids: Iterable[str],
    actor: str = "",
) -> dict[str, int]:
    actor_id = canonical_actor_id(actor)
    requested = {
        str(resident_id or "").strip()
        for resident_id in resident_ids
        if str(resident_id or "").strip()
    }
    visible_resident_ids: list[str] = []
    with resident_lineage.access_snapshot_scope():
        records = resident_catalog.get_catalog().get_many(requested)
        for record in records:
            resident_id = resident_domain_common.record_resident_id(record)
            try:
                ensure_resident_visible_to_actor(record, actor_id)
            except PermissionError:
                continue
            visible_resident_ids.append(resident_id)
    return resident_conversation_stream.get_repository().user_message_counts(
        visible_resident_ids
    )


def get_resident_user_message_count(
    resident_id: str,
    actor: str = "",
) -> int:
    key = str(resident_id or "").strip()
    if not key:
        raise KeyError("resident not found")
    counts = get_resident_user_message_counts([key], actor)
    if key not in counts:
        raise KeyError("resident session not found")
    return counts[key]


def _session_context_tokens(usage: object) -> int:
    cleaned = resident_domain_common.clean_token_usage(usage)
    input_tokens = resident_domain_common.nonnegative_int(cleaned.get("input_tokens"), 0)
    cached_input_tokens = resident_domain_common.nonnegative_int(cleaned.get("cached_input_tokens"), 0)
    total_tokens = resident_domain_common.nonnegative_int(cleaned.get("total_tokens"), 0)
    output_tokens = resident_domain_common.nonnegative_int(cleaned.get("output_tokens"), 0)
    reasoning_tokens = resident_domain_common.nonnegative_int(cleaned.get("reasoning_output_tokens"), 0)
    non_output_tokens = max(0, total_tokens - output_tokens - reasoning_tokens)
    # Codex reports cached input as a subset of input, while some other backends
    # report it as a separate component. Taking the maximum avoids double
    # counting Codex cache hits and still captures separate-cache providers.
    return max(input_tokens, cached_input_tokens, non_output_tokens)


def _session_context_pressure(
    usage: object,
    *,
    updated_at: str = "",
    model_context_window: object = 0,
    source: str = "",
) -> dict[str, Any]:
    context_tokens = _session_context_tokens(usage)
    context_window = resident_domain_common.nonnegative_int(model_context_window, 0)
    if context_window and context_tokens > context_window:
        return {
            "context_tokens": 0,
            "model_context_window": context_window,
            "used_percent": None,
            "heat_level": "normal",
            "updated_at": str(updated_at or ""),
            "source": "invalid_context_usage",
        }
    used_percent = (
        round((context_tokens / context_window) * 100, 1)
        if context_tokens and context_window
        else None
    )
    if used_percent is not None:
        heat_level = (
            "hot"
            if used_percent >= CONTEXT_PRESSURE_HOT_PERCENT
            else "warm"
            if used_percent >= CONTEXT_PRESSURE_WARM_PERCENT
            else "normal"
        )
    else:
        heat_level = (
            "hot"
            if context_tokens >= CONTEXT_PRESSURE_HOT_FALLBACK_TOKENS
            else "warm"
            if context_tokens >= CONTEXT_PRESSURE_WARM_FALLBACK_TOKENS
            else "normal"
        )
    return {
        "context_tokens": context_tokens,
        "model_context_window": context_window,
        "used_percent": used_percent,
        "heat_level": heat_level,
        "updated_at": str(updated_at or ""),
        "source": str(
            source or ("latest_session_turn" if context_tokens else "")
        ),
    }


def _active_execution_context_pressure(
    job: dict[str, Any],
) -> dict[str, Any]:
    snapshot = resident_execution_work.execution_snapshot(job)
    checkpoint = (
        snapshot.get("checkpoint")
        if isinstance(snapshot.get("checkpoint"), dict)
        else {}
    )
    usage = resident_domain_common.clean_token_usage(checkpoint.get("context_usage"))
    context_window = resident_domain_common.nonnegative_int(
        checkpoint.get("model_context_window"),
        0,
    )
    updated_at = str(
        checkpoint.get("context_usage_at")
        or snapshot.get("updated_at")
        or job.get("updated_at")
        or ""
    )
    if usage:
        return _session_context_pressure(
            usage,
            updated_at=updated_at,
            model_context_window=context_window,
            source="active_execution",
        )
    return _session_context_pressure(
        {},
        updated_at=updated_at,
        model_context_window=context_window,
        source="active_execution_pending",
    )


def _resident_scope_context_pressure(
    scope: dict[str, Any],
    *,
    active_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(active_job, dict) and resident_execution_work.is_active(active_job):
        return _active_execution_context_pressure(active_job)
    usage_field = "last_token_usage"
    usage_at_field = "last_token_usage_at"
    context_window_field = "last_model_context_window"
    raw_usage = (
        scope.get(usage_field)
        if isinstance(scope.get(usage_field), dict)
        else {}
    )
    stored_usage = resident_domain_common.clean_token_usage(raw_usage)
    projection = resident_domain_common.execution_token_usage_contract(raw_usage)
    context_usage = stored_usage or resident_domain_common.clean_token_usage(
        projection.get("context_usage")
    )
    context_window = (
        resident_domain_common.nonnegative_int(scope.get(context_window_field), 0)
        or resident_domain_common.nonnegative_int(
            projection.get("model_context_window"),
            0,
        )
    )
    updated_at = str(scope.get(usage_at_field) or "")
    if not stored_usage and projection.get("context_updated_at"):
        updated_at = str(projection["context_updated_at"])
    return _session_context_pressure(
        context_usage,
        updated_at=updated_at,
        model_context_window=context_window,
    )


def get_resident_context_pressures(
    resident_ids: list[str],
    actor: str = "",
) -> dict[str, dict[str, Any]]:
    actor_id = canonical_actor_id(actor)
    requested = {
        str(resident_id or "").strip()
        for resident_id in resident_ids
        if str(resident_id or "").strip()
    }
    if not requested:
        return {}
    with resident_lineage.access_snapshot_scope():
        records = [
            copy.deepcopy(record)
            for record in resident_catalog.get_catalog().get_many(requested)
        ]
        records = business_fact_views.canonical_views(
            records,
            id_field="resident_id",
            actor=actor_id,
        )
    projection: dict[str, dict[str, Any]] = {}
    for record in records:
        resident_id = resident_domain_common.record_resident_id(record)
        if not _resident_visible_to_actor(record, actor_id):
            continue
        projection[resident_id] = _resident_scope_context_pressure(
            record,
            active_job=None,
        )
    return projection


def _resident_display_title(record: dict) -> str:
    return str(record.get("title") or record.get("resident_id") or "Resident").strip() or "Resident"


def _forward_lineage_title(record: dict[str, Any]) -> str:
    lineage = record.get("lineage") if isinstance(record.get("lineage"), dict) else {}
    return str(
        record.get("lineage_title")
        or lineage.get("title")
        or record.get("title")
        or resident_lineage.lineage_id(record)
    ).strip()


def _forward_scope_id(record: dict[str, Any]) -> str:
    project_id = str(record.get("project_id") or "").strip()
    return f"project:{project_id}" if project_id else "system"


def _forward_project_titles() -> dict[str, str]:
    return {str(item["id"]): str(item.get("name") or item["id"])
            for item in load_projects_registry() if item.get("id")}


def _forward_scope(
    record: dict[str, Any],
    project_titles: Mapping[str, str],
) -> dict[str, str]:
    scope_id = _forward_scope_id(record)
    if scope_id == "system":
        return {"id": scope_id, "kind": "system", "title": "System Agents"}
    project_id = str(record.get("project_id") or "").strip()
    return {
        "id": scope_id,
        "kind": "project",
        "project_id": project_id,
        "title": str(project_titles.get(project_id) or project_id),
    }


def _forward_target(
    record: dict[str, Any],
) -> dict[str, Any]:
    resident_id = str(record.get("resident_id") or "").strip()
    resident_title = _resident_display_title(record)
    lineage_id = resident_lineage.lineage_id(record)
    return {
        "resident_id": resident_id,
        "title": resident_title,
        "scope_id": _forward_scope_id(record),
        "project_id": str(record.get("project_id") or "").strip(),
        "lineage_id": lineage_id,
        "lineage_title": _forward_lineage_title(record),
    }


def _forward_targets_for_record(
    record: dict[str, Any],
    actor_id: str,
) -> list[dict[str, Any]]:
    return [_forward_target(record)] if _resident_visible_to_actor(record, actor_id) else []


def _resolve_forward_target_by_resident_id(
    resident_id: str,
    actor_id: str,
) -> dict[str, Any]:
    exact_id = str(resident_id or "").strip()
    record = resident_record_domain.find_resident(exact_id)
    if not record:
        raise KeyError("resident not found")
    if not _resident_visible_to_actor(record, actor_id):
        raise PermissionError("当前用户不能向这个 Resident 转发")
    return _forward_target(record)


def list_resident_forward_targets(
    actor: str = "",
    *,
    scope_id: str = "",
    lineage_id: str = "",
    resident_id: str = "",
    preferred_lineage_id: str = "",
) -> dict[str, Any]:
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后选择联系人")
    with resident_lineage.access_snapshot_scope():
        if resident_id:
            return {"target": _resolve_forward_target_by_resident_id(resident_id, actor_id)}
        records = [dict(item) for item in resident_lineage.all() if isinstance(item, dict)]
        records.sort(key=lambda item: (
            str(item.get("project_id") or ""),
            _resident_display_title(item).casefold(),
        ))
        visible_records = [
            record
            for record in records
            if (
                str(record.get("resident_id") or "").strip()
                and _resident_visible_to_actor(record, actor_id)
            )
        ]
        eligible_records = [
            record
            for record in visible_records
            if _forward_targets_for_record(record, actor_id)
        ]
        clean_lineage_id = str(lineage_id or "").strip()
        if clean_lineage_id:
            targets = [
                _forward_target(record)
                for record in eligible_records
                if resident_lineage.lineage_id(record) == clean_lineage_id
            ]
            return {"lineage_id": clean_lineage_id, "targets": targets}

        clean_scope_id = str(scope_id or "").strip()
        if clean_scope_id:
            lineages_by_id: dict[str, dict[str, Any]] = {}
            for record in eligible_records:
                if _forward_scope_id(record) != clean_scope_id:
                    continue
                current_lineage_id = resident_lineage.lineage_id(record)
                lineages_by_id.setdefault(current_lineage_id, {
                    "id": current_lineage_id,
                    "title": _forward_lineage_title(record),
                })
            lineages = sorted(
                lineages_by_id.values(),
                key=lambda item: (
                    str(item.get("title") or "").casefold(),
                    str(item.get("id") or ""),
                ),
            )
            return {"scope_id": clean_scope_id, "lineages": lineages}

        project_titles = _forward_project_titles()
        scopes_by_id: dict[str, dict[str, str]] = {}
        for record in eligible_records:
            scope = _forward_scope(record, project_titles)
            scopes_by_id.setdefault(scope["id"], scope)
        kind_order = {"personal": 0, "project": 1, "system": 2}
        scopes = sorted(
            scopes_by_id.values(),
            key=lambda item: (
                kind_order.get(str(item.get("kind") or ""), 9),
                str(item.get("title") or "").casefold(),
                str(item.get("id") or ""),
            ),
        )
        clean_preferred_lineage_id = str(preferred_lineage_id or "").strip()
        preferred_scope_id = next((
            _forward_scope_id(record)
            for record in eligible_records
            if resident_lineage.lineage_id(record) == clean_preferred_lineage_id
        ), "")
        return {"scopes": scopes, "preferred_scope_id": preferred_scope_id}


def _resident_conversation_activity(
    record: dict,
    limit: int = RECENT_RESIDENT_MESSAGES,
) -> list[dict[str, Any]]:
    """Build the bounded Resource projection from canonical Messages."""

    resident_id = resident_domain_common.record_resident_id(record)
    repository = resident_conversation_stream.get_repository()
    output: list[dict[str, Any]] = []
    for order, message in enumerate(
        repository.read_messages(resident_id, limit=limit),
        1,
    ):
        role = str(message.get("role") or "")
        author_id = str(message.get("author_id") or "")
        source = {
            "user": "human_prompt",
            "agent": "backend_output",
            "system": "portal",
        }.get(role, "portal")
        content = resident_domain_common.protocol_message_text(message)
        output.append({
            "id": str(message.get("message_id") or ""),
            "message_id": str(message.get("message_id") or ""),
            "created_at": str(message.get("created_at") or ""),
            "source": source,
            "kind": "resident_message",
            "title": (
                author_id
                if role == "user" and author_id
                else resident_backend.backend_label(record)
                if role == "agent"
                else "Portal"
            ),
            "author": author_id if role == "user" else role,
            "resident_id": resident_id,
            "content": resident_domain_common.truncate_text(content),
            "order": order,
        })
    return output


def _resident_resource_text(value: object, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _resident_resource_activity(
    items: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Project recent work with an explicit field and text budget."""

    selected = sorted(
        (item for item in items if isinstance(item, dict)),
        key=lambda item: (
            str(item.get("created_at") or ""),
            int(item.get("order") or 0),
            str(item.get("id") or ""),
        ),
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in selected:
        key = (
            str(item.get("id") or ""),
            str(item.get("source") or ""),
            str(item.get("created_at") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    truncated = len(deduped) > RESIDENT_RESOURCE_ACTIVITY_LIMIT
    remaining = resident_domain_common.RESIDENT_RESOURCE_ACTIVITY_TOTAL_CHARS
    output: list[dict[str, Any]] = []
    for item in deduped[-RESIDENT_RESOURCE_ACTIVITY_LIMIT:]:
        projected = {
            key: str(item.get(key) or "")
            for key in (
                "id",
                "created_at",
                "source",
                "kind",
                "title",
                "author",
                "resident_id",
                "message_id",
            )
            if item.get(key) not in (None, "")
        }
        content = str(item.get("content") or "")
        content_limit = min(
            RESIDENT_RESOURCE_ACTIVITY_ITEM_CHARS,
            max(0, remaining),
        )
        if content and content_limit:
            projected["content"] = _resident_resource_text(
                content,
                content_limit,
            )
            remaining -= len(projected["content"])
            if len(projected["content"]) < len(content):
                truncated = True
        elif content:
            truncated = True
        output.append(projected)
    return output, truncated


def get_resident_resource_projection(resident_id: str) -> dict[str, Any]:
    """Return the bounded cross-Resident work/status read model."""

    target = resident_record_domain.resolve_resident_resource_target(resident_id)
    record = target.record
    activity_items = [
        *_resident_conversation_activity(
            record,
            limit=RESIDENT_RESOURCE_ACTIVITY_READ_LIMIT,
        ),
    ]
    last_error = str(record.get("last_error") or "")
    title = str(
        record.get("title")
        or target.resident_id
    )
    summary = str(record.get("description") or "")
    activity, activity_truncated = _resident_resource_activity(activity_items)
    conversation = (
        resident_conversation_stream.get_repository()
        .current_projection(target.resident_id)
    )
    resident_protocol_state = dict(conversation.get("state") or {})
    state = str(resident_protocol_state.get("work_state") or "idle")
    errors = [_resident_resource_text(last_error, 4000)] if last_error.strip() else []
    updated_at = max(
        (
            str(record.get("last_interaction_at") or ""),
            str(record.get("interaction_updated_at") or ""),
            str(record.get("updated_at") or ""),
        ),
        default="",
    )
    return {
        "schema": RESIDENT_RESOURCE_PROJECTION_SCHEMA,
        "resource_id": target.resident_id,
        "resource_type": "resident",
        "resident_id": target.resident_id,
        "source_resident_id": str(record.get("source_resident_id") or ""),
        "lineage_id": str(record.get("lineage_id") or ""),
        "title": _resident_resource_text(title, 500),
        "summary": _resident_resource_text(summary, 4000),
        "project_id": str(record.get("project_id") or ""),
        "backend": str(record.get("backend") or ""),
        "state": state or "idle",
        "user_message_count": resident_lifecycle_domain.resident_user_message_count(target.resident_id),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": updated_at,
        "last_interaction_at": str(
            record.get("last_interaction_at")
            or record.get("interaction_updated_at")
            or ""
        ),
        "resident_state": resident_protocol_state,
        "resident_cursor": int(conversation.get("cursor") or 0),
        "errors": errors,
        "recent_activity": activity,
        "recent_activity_truncated": activity_truncated,
    }


def _resident_workspace_public_record(
    record: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Project only Resident identity and workspace fields used by the Portal UI."""

    resident_id = resident_domain_common.record_resident_id(record)
    public = {
        key: copy.deepcopy(record.get(key))
        for key in (
            "principal_hierarchy",
            "title",
            "description",
            "project_id",
            "workspace_path",
            "workspace_bindings",
            "workspace_root",
                "workspace_paths",
            "family",
            "registration_state",
            "created_at",
            "updated_at",
            "created_by",
            "source_resident_id",
        )
        if record.get(key) not in (None, "", [], {})
    }
    public.update({
        "resident_id": resident_id,
        "title": str(record.get("title") or resident_id),
        "lineage_id": resident_lineage.lineage_id(record),
        "backend": resident_backend.execution_backend(record),
        "backend_capabilities": resident_runtime_domain.resident_backend_capabilities(record),
        "workspace_roots": resident_record_domain.resident_workspace_roots(record),
    })
    _annotate_resident_lineage_fields(
        public,
        resident_lineage.new_snapshot(record),
    )
    return public


def _resident_provider_session(
    record: Mapping[str, Any],
    session_record: Mapping[str, Any],
) -> dict[str, str]:
    session_id = resident_backend.session_id(
        session_record.get("backend_session_id")
    )
    if not session_id:
        return {}
    return {
        "provider": str(
            session_record.get("provider")
            or resident_backend.execution_provider(record)
        ).strip(),
        "id": session_id,
    }


def get_lineage_node_metadata(
    resident_id: str,
    actor: str = "",
) -> dict[str, Any]:
    """Return the one session projection consumed by the Lineage workspace."""

    with resident_lineage.access_snapshot_scope():
        return _get_lineage_node_metadata(
            resident_id,
            actor,
        )


def _resident_session_projection_from_record(
    record: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Own the one canonical projection shared by every Resident Chat surface."""

    actor_id = canonical_actor_id(actor)
    exact_resident_id = resident_domain_common.record_resident_id(record)
    runtime_settings = resident_runtime_domain.read_resident_runtime_settings(record)
    exact_session_record = resident_backend.session_record(record)
    provider_session = _resident_provider_session(record, exact_session_record)
    session_account_id = resident_runtime_domain.resident_session_account_id(
        record,
        session_record=exact_session_record,
    )
    resident_public = _resident_workspace_public_record(
        record,
        actor_id,
    )
    return {
        "schema": "tfi.resident-session-projection.v1",
        "revision": resident_domain_common.resident_status_signature({
            "record_updated_at": str(record.get("updated_at") or ""),
            "provider_session": provider_session,
            "runtime_settings": runtime_settings,
        }),
        "resident_id": exact_resident_id,
        "resident": resident_public,
        "lineage": resident_public.get("lineage") or {},
        "lineage_resident": resident_public.get("lineage_resident") or {},
        "backend_capabilities": resident_public.get("backend_capabilities") or {},
        "provider_session": provider_session,
        "execution_account": resident_backend.execution_account_projection(record),
        "account_selection": resident_runtime_domain.account_selection_payload(
            record,
            session_account_id,
        ),
        "runtime_settings": runtime_settings,
        "agent_harness_defaults": resident_runtime_settings.normalize_settings({})["agent_harness"]["features"],
        "watchboard": {
            "enabled": resident_watchboard_domain.has_watchboard(record),
            "resident_id": exact_resident_id,
        },
        "watchboard_capabilities": resident_watchboard_domain.watchboard_capabilities(record, actor_id),
        "family_facade": family_facade.chat_presentation(record),
        "allows": resident_access.resident_allows(record, actor_id),
    }


def get_resident_session_projection(
    resident_id: str,
    actor: str = "",
) -> dict[str, Any]:
    """Return the bounded projection required to render one Resident Chat.

    This read is intentionally independent from the Lineage aggregate lock and
    from family and conversation scans. ResidentCatalog and ResourceService
    return detached committed views, so a Chat read does not need to serialize
    with unrelated reads from the same Lineage.
    """

    actor_id = canonical_actor_id(actor)
    with resident_lineage.access_snapshot_scope():
        resolved = resident_record_domain.resolve_resident_runtime_target(
            resident_id,
            actor_id,
            require_visible=True,
        )
        return _resident_session_projection_from_record(
            resolved.record,
            actor_id,
        )


def _get_lineage_node_metadata(
    resident_id: str,
    actor: str,
) -> dict[str, Any]:
    exact_resident_id = str(resident_id or "").strip()
    if not exact_resident_id:
        raise KeyError("resident not found")
    target = resident_record_domain.resolve_resident_runtime_target(
        exact_resident_id,
        actor,
        require_visible=True,
    )
    if not _can_manage_resident(target.record, actor):
        raise PermissionError("无权读取该 Resident 的 HATG 节点详情")
    record = target.record
    session_record = resident_backend.session_record(record)
    session_account_id = resident_runtime_domain.resident_session_account_id(
        record,
        session_record=session_record,
    )
    backend = resident_backend.execution_backend(record)
    return {
        "schema": "tfi.lineage-node-metadata.v1",
        "revision": resident_domain_common.resident_status_signature({
            "record_updated_at": str(record.get("updated_at") or ""),
            "backend": backend,
            "session_account_id": session_account_id,
        }),
        "resident_id": exact_resident_id,
        "resident": {
            "resident_id": exact_resident_id,
            "title": str(record.get("title") or exact_resident_id),
            "backend": backend,
        },
        "account_selection": resident_runtime_domain.account_selection_payload(
            record,
            session_account_id,
        ),
    }


def load_projects_registry() -> list[dict[str, Any]]:
    return project_registry.projects()


def resident_lineage_resident(record: dict[str, Any]) -> dict[str, Any]:
    resident_id = resident_domain_common.record_resident_id(record)
    return {
        "lineage_id": resident_lineage.lineage_id(record),
        "resident_id": resident_id,
        "title": str(record.get("title") or ""),
        "backend_binding": resident_runtime_domain.resident_backend_binding(record),
        "backend_capabilities": resident_runtime_domain.resident_backend_capabilities(record),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
    }
