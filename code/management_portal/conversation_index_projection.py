from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

from management_portal import conversation_read_state
from management_portal.conversation_completion import completion_signature
from management_portal.society_service import get_society_service
from management_portal.resident_avatar import get_avatar, projection_fields


def _same_actor(left: object, right: object) -> bool:
    left_id = str(left or "").strip()
    return bool(left_id) and left_id.casefold() == str(right or "").strip().casefold()


def activity_timestamp(value: object) -> float:
    """Normalize activity times for both projection and index ordering."""
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        numeric = float(text)
    except ValueError:
        numeric = 0.0
    if numeric:
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _iso_time(value: object) -> str:
    # Keep the source's precision: Residents are paged by exact activity times,
    # and a list showing them rounded could order a page boundary differently.
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    timestamp = activity_timestamp(value)
    if not timestamp:
        return str(value or "").strip()
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _latest_time(*values: object) -> str:
    return _iso_time(max(values, key=activity_timestamp, default=""))


def _completion_payload(
    actor: str,
    *,
    completion_id: object = "",
    input_message_id: object = "",
    completed_at: object = "",
) -> dict[str, Any]:
    clean_completion_id = str(completion_id or "").strip()
    if not clean_completion_id:
        return {}
    return {
        "schema_version": 1,
        "request_actor": str(actor or "").strip(),
        "completion_id": clean_completion_id,
        "input_message_id": str(input_message_id or "").strip(),
        "completed_at": _iso_time(completed_at),
    }


def _completion_from_public(value: object, actor: str) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    request_actor = str(source.get("request_actor") or "").strip()
    if not request_actor or not _same_actor(request_actor, actor):
        return {}
    return _completion_payload(
        request_actor,
        completion_id=source.get("completion_id"),
        input_message_id=source.get("input_message_id"),
        completed_at=source.get("completed_at"),
    )


def _resident_status(snapshot: object) -> str:
    source_snapshot = snapshot if isinstance(snapshot, dict) else {}
    state = source_snapshot.get("state")
    source = state if isinstance(state, dict) else {}
    status = str(source.get("work_state") or "idle").strip().lower()
    family_state = (source.get("family_activity") or {}).get("work_state")
    if family_state in {"idle", "queued", "running"}:
        return family_state
    if status in {"running", "stopping"}:
        return "running"
    if status == "queued":
        return "queued"
    latest_input = source_snapshot.get("latest_input_state")
    terminal_status = str(
        latest_input.get("state") if isinstance(latest_input, dict) else ""
    ).strip().lower()
    if terminal_status == "failed":
        return "failed"
    if terminal_status in {"cancelled", "canceled", "interrupted"}:
        return "interrupted"
    return "idle"


def resident_state_snapshots(
    actor: str,
    resident_ids: list[str],
) -> dict[str, dict[str, Any]]:
    payload = get_society_service().resident.get_resident_states(
        resident_ids,
        actor,
    )
    return {
        str(item.get("resident_id") or ""): item
        for item in (
            payload.get("items") if isinstance(payload, dict) else []
        ) or []
        if isinstance(item, dict)
    }


def _entry(
    *,
    key: str,
    title: object,
    subtitle: object,
    surface: str,
    surface_label: str,
    kind: str,
    url: str,
    updated_at: object,
    status: str = "idle",
    completion: object = None,
    resident_id: object = "",
    cursor: object = 0,
    intake_state: object = "",
    account_id: object = "",
) -> dict[str, Any]:
    entry = {
        "key": str(key),
        "title": str(title or "Agent conversation").strip()[:240],
        "subtitle": str(subtitle or "").strip()[:320],
        "surface": str(surface),
        "surface_label": str(surface_label),
        "kind": str(kind),
        "url": str(url),
        "updated_at": _iso_time(updated_at),
        "status": (
            status
            if status in {
                "idle", "queued", "running", "failed", "interrupted", "archived"
            }
            else "idle"
        ),
        "completion_signature": completion_signature(completion),
    }
    if str(resident_id or "").strip():
        entry["resident_id"] = str(resident_id).strip()
        entry["account_id"] = str(account_id or "").strip()
        entry.update(projection_fields(get_avatar(entry["resident_id"])))
        entry["cursor"] = max(0, int(cursor or 0))
        normalized_intake = str(intake_state or "").strip().lower()
        if normalized_intake in {"open", "closed"}:
            entry["intake_state"] = normalized_intake
    return entry


def _lineage_url(
    lineage_id: object,
    resident_id: object,
) -> str:
    params = [
        ("lineage", str(lineage_id or "")),
        ("resident", str(resident_id or "")),
    ]
    return f"/lineage?{urlencode(params)}"


def _resident_session_url(
    resident_id: str,
    *,
    session_routes: dict[str, str] | None = None,
) -> str:
    exact_resident_id = str(resident_id or "").strip()
    lineage_url = f"/lineage?resident={quote(exact_resident_id)}"
    routes = session_routes if isinstance(session_routes, dict) else {}
    lineage_id = str(routes.get(exact_resident_id, "") or "").strip()
    if lineage_id:
        lineage_url = _lineage_url(lineage_id, exact_resident_id)
    return lineage_url


def get_resident_navigation(
    resident_id: str,
    *,
    actor: str,
) -> dict[str, Any]:
    actor_id = str(actor or "").strip()
    exact_resident_id = str(resident_id or "").strip()
    identity = get_society_service().resident.resolve_resident_identity(
        exact_resident_id,
        actor_id,
    )
    url = _resident_session_url(
        exact_resident_id,
        session_routes={
            exact_resident_id: str(identity.get("lineage_id") or "")
        },
    )
    return {
        "resident_id": exact_resident_id,
        "url": url,
    }


def resident_pin_entry(actor: str, resident_id: str) -> dict[str, Any]:
    """Project an explicitly pinned, visible Resident before any conversation exists."""
    resident = get_society_service().resident.get_resident(resident_id, actor)
    snapshot = get_society_service().resident.get_resident_state(resident_id, actor)
    return _entry(
        key=f"resident:{resident_id}",
        title=resident.get("title") or resident_id,
        subtitle=resident.get("lineage_title") or resident.get("lineage_id") or "Resident",
        surface="lineage",
        surface_label="Lineage",
        kind="resident-main",
        url=_resident_session_url(resident_id, session_routes={resident_id: resident.get("lineage_id") or ""}),
        updated_at=resident.get("updated_at") or resident.get("created_at"),
        status=_resident_status(snapshot),
        resident_id=resident_id,
        cursor=snapshot.get("cursor"),
        account_id=resident.get("account"),
        intake_state=(snapshot.get("state") or {}).get("intake_state", "open"),
    )


def lineage_human_notification_entries(
    actor: str,
    lineage_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    actor_id = str(actor or "").strip()
    payload = get_society_service().lineage.list_lineage_human_notifications(
        actor_id,
        sorted(lineage_ids) if lineage_ids is not None else None,
    )
    definitions = payload.get("lineages") if isinstance(payload, dict) else []
    entries: list[dict[str, Any]] = []
    for lineage in definitions:
        lineage_id = str(lineage.get("lineage_id") or "").strip()
        notifications = lineage.get("notifications") or []
        for notification in notifications:
            notification_id = str(notification.get("id") or "").strip()
            if not notification_id:
                continue
            completed_at = notification["created_at"]
            summary = notification["message"]
            entry = _entry(
                key=conversation_read_state.notification_key(lineage_id, notification_id),
                title=notification.get("title") or "通知",
                subtitle=summary or "Human notification",
                surface="lineage" if lineage_id else "portal",
                surface_label="Lineage" if lineage_id else "Portal",
                kind="lineage-notification",
                url=f"/lineage?{urlencode({'lineage': lineage_id})}" if lineage_id else "/",
                updated_at=completed_at,
            )
            # Notification details are a recipient-authorized fact, separate
            # from the bounded list preview. Conversation bodies stay absent.
            entry["notification"] = {
                "id": notification_id,
                "message": str(notification.get("message") or ""),
                "severity": str(notification.get("severity") or "info"),
                "created_by": str(notification.get("created_by") or ""),
                "created_at": str(notification.get("created_at") or completed_at),
                "lineage_id": lineage_id,
                "lineage_title": str(lineage.get("title") or ""),
                "trigger_path_id": str(notification.get("trigger_path_id") or ""),
            }
            entries.append(entry)
    return entries


def resident_entries(
    actor: str,
    resident_payload: object,
    lineage_sessions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    payload = resident_payload if isinstance(resident_payload, dict) else {}
    session_routes = lineage_sessions if isinstance(lineage_sessions, dict) else {}
    resident_items = [
        item for item in payload.get("residents") or [] if isinstance(item, dict)
    ]
    state_ids = [
        str(item.get("resident_id") or "")
        for item in resident_items
    ]
    state_ids.extend(
        str(family.get("resident_id") or "")
        for item in resident_items
        for family in item.get("family_residents_preview") or []
        if isinstance(family, dict)
    )
    snapshots = resident_state_snapshots(actor, state_ids)
    top_level_resident_ids = {
        str(item.get("resident_id") or "").strip()
        for item in resident_items
        if str(item.get("resident_id") or "").strip()
    }
    entries: list[dict[str, Any]] = []
    for resident in resident_items:
        resident_id = str(resident.get("resident_id") or "").strip()
        if not resident_id:
            continue
        title = str(resident.get("title") or resident_id)
        snapshot = snapshots.get(resident_id) or {}
        resident_state = snapshot.get("state")
        public_completion = _completion_from_public(
            resident.get("unread_signature"), actor
        )
        last_user_is_actor = _same_actor(resident.get("last_user_actor"), actor)
        actor_last_user_message_at = resident.get("actor_last_user_message_at")
        # The actor talked here, or a message here was sent to the actor (decision_b4e5ea4a47ec).
        actor_engaged = bool(activity_timestamp(actor_last_user_message_at) or activity_timestamp(resident.get("actor_addressed_at")))
        if actor_engaged:
            url = _resident_session_url(
                resident_id,
                session_routes=session_routes,
            )
            entries.append(
                _entry(
                    key=f"resident:{resident_id}",
                    title=title,
                    subtitle=resident.get("lineage_title") or resident.get("lineage_id") or "Resident",
                    surface="lineage",
                    surface_label="Lineage",
                    kind="resident-main",
                    url=url,
                    # The time the list was ordered and paged by: the actor's
                    # messages, messages to them and their requests' outcomes.
                    updated_at=resident.get("activity_at") or _latest_time(
                        public_completion.get("completed_at"),
                        actor_last_user_message_at,
                        (
                            resident.get("interaction_updated_at")
                            or resident.get("last_token_usage_at")
                        )
                        if last_user_is_actor
                        else "",
                    ),
                    status=_resident_status(snapshot),
                    completion=public_completion,
                    resident_id=resident_id,
                    account_id=resident.get("account_id"),
                    cursor=snapshot.get("cursor"),
                    intake_state=(resident_state or {}).get("intake_state", "open"),
                )
            )
        for family_resident in resident.get("family_residents_preview") or []:
            if not isinstance(family_resident, dict):
                continue
            family_resident_id = str(
                family_resident.get("resident_id") or ""
            ).strip()
            if (
                not family_resident_id
                or family_resident_id in top_level_resident_ids
            ):
                continue
            snapshot = snapshots.get(family_resident_id) or {}
            resident_state = snapshot.get("state")
            public_completion = _completion_from_public(
                family_resident.get("unread_signature"), actor
            )
            last_user_is_actor = _same_actor(
                family_resident.get("last_user_actor"), actor
            )
            actor_last_user_message_at = family_resident.get(
                "actor_last_user_message_at"
            )
            if not activity_timestamp(actor_last_user_message_at):
                continue
            url = _resident_session_url(
                family_resident_id,
                session_routes=session_routes,
            )
            entries.append(
                _entry(
                    key=f"resident:{family_resident_id}",
                    title=family_resident.get("label") or f"{title} derived Resident",
                    subtitle=family_resident.get("lineage_title") or resident.get("lineage_title") or title,
                    surface="lineage",
                    surface_label="Lineage",
                    kind="resident",
                    url=url,
                    updated_at=_latest_time(
                        public_completion.get("completed_at"),
                        actor_last_user_message_at,
                        family_resident.get("last_interaction_at")
                        if last_user_is_actor
                        else "",
                    ),
                    status=_resident_status(snapshot),
                    completion=public_completion,
                    resident_id=family_resident_id,
                    account_id=family_resident.get("account_id"),
                    cursor=snapshot.get("cursor"),
                    intake_state=(resident_state or {}).get("intake_state", "open"),
                )
            )
    return entries


def collect_entries(
    *,
    actor: str,
    resident_payload: object = None,
    resident_ids: list[str] | None = None,
    lineage_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    resident_filter = (
        {str(item).strip() for item in resident_ids if str(item).strip()}
        if resident_ids is not None
        else None
    )
    lineage_filter = (
        {str(item).strip() for item in lineage_ids if str(item).strip()}
        if lineage_ids is not None
        else None
    )
    if resident_payload is None:
        resident_payload = get_society_service().resident.list_resident_conversation_activity(
            actor,
            sorted(resident_filter) if resident_filter is not None else None,
        )
    routes = {
        str(item.get("resident_id") or ""): str(item.get("lineage_id") or "")
        for item in (
            resident_payload.get("residents")
            if isinstance(resident_payload, dict)
            else []
        ) or []
        if isinstance(item, dict)
    }
    return [
        *lineage_human_notification_entries(actor, lineage_filter),
        *resident_entries(actor, resident_payload, routes),
    ]
