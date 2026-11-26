"""A facade continuation recovers a particular request, never an unbound new task.

The facade owns selection and retry occurrence identity. The member mailbox owns
restoring execution input; the provider adapter owns native continuation safety.
"""
from __future__ import annotations

from typing import Any, Mapping
from datetime import datetime, timezone

from management_common.actor_identity import same_actor
from management_common.principal import message_quote

from . import family_facade, resident_conversation_stream, resident_lineage
from .state_store import get_lineage_data_store


def _instant(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else datetime.min.replace(tzinfo=timezone.utc)


def is_command(message: Mapping[str, Any]) -> bool:
    parts = message.get("content_parts") or []
    return bool(parts) and all(part.get("type") == "text" for part in parts) and " ".join(
        str(part.get("text") or "") for part in parts
    ).strip().rstrip("。.!！").casefold() in {"continue", "继续", "继续执行", "继续处理"}


def records(facade: Mapping[str, Any]) -> dict:
    facade_id = resident_lineage.resident_id(facade)
    return get_lineage_data_store().family_relays.by_keys([facade_id]).get(facade_id, {}).get("recoveries") or {}


def route_restorations(facade: Mapping[str, Any]) -> dict:
    """Direct failed-route recovery intents, separate from conversational continue inputs."""
    return get_lineage_data_store().family_relays.by_keys([facade["resident_id"]]).get(
        facade["resident_id"], {}).get("route_restorations") or {}


def register(facade: Mapping[str, Any], message: Mapping[str, Any]) -> bool:
    """Register before publishing the queued input, so arrival cannot route 'continue'."""
    if not is_command(message):
        return False
    with get_lineage_data_store().family_relays.transaction(resident_lineage.lineage_id(facade), resident_lineage.resident_id(facade)) as state:
        state.setdefault("recoveries", {}).setdefault(message["message_id"], {"status": "selecting"})
    return True


def _state(repository, resident_id: str, message_id: str) -> dict:
    try:
        return repository.input_state(resident_id, message_id)
    except KeyError:
        return {"state": "missing"}


def request_status(facade: Mapping[str, Any], message_id: str) -> dict:
    from . import family_storage, trigger_paths
    from . import resident_record_domain

    facade_id = resident_lineage.resident_id(facade)
    repository = resident_conversation_stream.get_repository()
    links = family_facade.relay_links(facade)
    if message_id in links:
        via, (member, member_message) = family_facade.relay_holder(facade_id, message_id)
        record = resident_record_domain.find_resident(member) or {}
        return {**_state(repository, member, member_message), "resident_id": member,
                "member_message_id": member_message, "title": record.get("title") or member, "via": via}
    own = _state(repository, facade_id, message_id)
    if own["state"] != "queued":
        return own
    lineage_id = resident_lineage.lineage_id(facade)
    attempts = trigger_paths.find_event_paths(lineage_id, {"resident_id": facade_id, "message_id": message_id})
    attempts += trigger_paths.find_event_paths(lineage_id, {"input_business_ref": f"family-route/{facade_id}/message/{message_id}"})
    retry_at = max((_instant(item.get("requested_at")) for item in records(facade).values()
                    if item.get("request_message_id") == message_id and item.get("route_occurrence")), default=_instant(""))
    retry_at = max([retry_at, *(_instant(item["requested_at"]) for item in
                               route_restorations(facade).get(message_id) or [])])
    latest = max((item for item in attempts if _instant(item["occurred_at"]) >= retry_at), key=lambda item: _instant(item["occurred_at"]), default={})
    with get_lineage_data_store().database.read() as connection:
        routing_nodes = family_storage.routing_nodes(connection, facade["family"]["family_id"])
    failures = [item["result"] for item in latest.get("failures") or [] if item["node_id"] in routing_nodes]
    if latest.get("status") in {"failed", "cancelled"} and failures:
        failure = failures[0]
        detail = failure.get("failure") or failure.get("error") or failure
        return {"state": "failed", "error": detail, "attempt_at": latest["occurred_at"]}
    return {"state": "routing"}


def can_recover(facade, request, actor):
    """The original sender or facade manager may continue the same request."""
    from . import resident_access
    return same_actor(request.get("author_id"), actor) or resident_access.can_manage(facade, actor)


def _select(facade, message, entries):
    repository = resident_conversation_stream.get_repository()
    facade_id = resident_lineage.resident_id(facade)
    quoted = str(message_quote(message).get("message_id") or "")
    if quoted:
        quoted = str((entries.get(quoted) or {}).get("request_message_id") or quoted)
        found = repository.message(facade_id, quoted)
        if not found:
            reply = family_facade.relayed_reply(facade, quoted)
            quoted = str((reply or {}).get("request_message_id") or "")
            found = repository.message(facade_id, quoted) if quoted else None
        if found and found.get("role") == "user" and quoted not in entries and can_recover(facade, found, message["author_id"]):
            return {"request_message_id": quoted}
        return {"status": "unavailable", "error": "引用的消息不是你可以恢复的 Family 请求。"}
    candidates, waiting, seen = [], [], set()
    completed = None
    # Bare continue only considers the sender's requests, never another user's work.
    for item in reversed(repository.read_messages(facade_id, limit=200)):
        mid = str(item.get("message_id") or "")
        if mid == message["message_id"] or item.get("role") != "user" or not same_actor(item.get("author_id"), message["author_id"]):
            continue
        historical = mid in entries
        if historical:
            mid = str(entries[mid].get("request_message_id") or "")
            item = repository.message(facade_id, mid) if mid else None
            if not item or item.get("role") != "user" or not same_actor(item.get("author_id"), message["author_id"]):
                continue
        if mid in seen or mid in entries:
            continue
        seen.add(mid)
        if any(item.get(key) for key in family_facade.INFO_MARKS):
            continue
        status = request_status(facade, mid)["state"]
        if status == "failed":
            candidates.append(item)
        elif status in {"queued", "running", "routing"}:
            waiting.append(item)
        elif historical and status == "completed" and completed is None:
            completed = mid
    choices = candidates or waiting
    if len(choices) == 1:
        return {"request_message_id": choices[0]["message_id"]}
    if choices:
        return {"status": "choose", "candidates": [{"message_id": item["message_id"], "author_id": item["author_id"],
                "text": " ".join(str(p.get("text") or "") for p in item.get("content_parts") or [])[:200]} for item in choices]}
    if completed:
        return {"request_message_id": completed}
    return {"status": "unavailable", "error": "没有找到唯一可恢复的请求。请引用原请求后发送 continue。"}


def handle(facade: Mapping[str, Any], message_id: str) -> bool:
    """Repeatable after a crash. Selection is durable before any execution changes."""
    from . import resident_record_domain
    from .resident_input_recovery import requeue

    facade_id, lineage_id = resident_lineage.resident_id(facade), resident_lineage.lineage_id(facade)
    repository = resident_conversation_stream.get_repository()
    with resident_record_domain.state_lock(facade_id):
        entries = records(facade)
        entry = entries.get(message_id)
        if entry is None:
            return False
        if _state(repository, facade_id, message_id)["state"] == "cancelled":
            return True
        if entry.get("status") == "routing":
            # Finish admission if the process stopped after saving the retry intent.
            previous = _state(repository, facade_id, entry["request_message_id"])
            if previous["state"] == "cancelled":
                # Withdrawal wins over a saved retry intent, regardless of timestamps.
                entry.update(status="observing")
                with get_lineage_data_store().family_relays.transaction(lineage_id, facade_id) as state:
                    state.setdefault("recoveries", {})[message_id] = entry
                repository.set_input_state(facade_id, message_id, "completed")
                return True
            if previous["state"] == "failed" and _instant(previous.get("updated_at")) <= _instant(entry.get("requested_at")):
                repository.reopen_input(facade_id, entry["request_message_id"])
            return True
        if entry.get("status") not in {"selecting", "selected"}:
            return True
        message = repository.message(facade_id, message_id)
        if entry["status"] == "selecting":
            entry = {"status": "selected", "requested_at": _state(repository, facade_id, message_id).get("updated_at") or message["created_at"],
                     **_select(facade, message, entries)}
            with get_lineage_data_store().family_relays.transaction(lineage_id, facade_id) as state:
                state.setdefault("recoveries", {})[message_id] = entry
        root = entry.get("request_message_id")
        if root:
            request = repository.message(facade_id, root)
            if not request or not can_recover(facade, request, message["author_id"]):
                entry.update(status="unavailable", error="你没有恢复这条请求的权限。")
            else:
                current = request_status(facade, root)
                try:
                    if current["state"] == "failed":
                        if current.get("resident_id"):
                            member = resident_record_domain.find_resident(current["resident_id"])
                            if not member or member.get("deleted_at"):
                                raise ValueError("原接手成员已不可用，请先安排成员。")
                            # Authorization belongs to the facade request, not arbitrary member mailbox access.
                            requeue(current["resident_id"], str(message["author_id"]), current["member_message_id"],
                                    portal_reason="Family sender resumes the original relayed request")
                            entry.update(status="observing", resumed=True)
                        else:
                            # This input is the new occurrence; payload remains the original request.
                            entry.update(status="routing", route_occurrence=message_id, resumed=True)
                            with get_lineage_data_store().family_relays.transaction(lineage_id, facade_id) as state:
                                state.setdefault("recoveries", {})[message_id] = entry
                            if _state(repository, facade_id, root)["state"] == "failed":
                                repository.reopen_input(facade_id, root)
                    else:
                        entry.update(status="observing")
                except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
                    # A concurrent automatic recovery may have won. Observe it, never duplicate it.
                    if request_status(facade, root)["state"] in {"queued", "running"} and current.get("resident_id"):
                        entry.update(status="observing")
                    else:
                        entry.update(status="failed", error=str(exc))
        with get_lineage_data_store().family_relays.transaction(lineage_id, facade_id) as state:
            state.setdefault("recoveries", {})[message_id] = entry
        if entry.get("status") != "routing":
            repository.set_input_state(facade_id, message_id, "completed")
        return True


def arrival(facade, message_id: str) -> str | None:
    """Translate a recovery occurrence into its original routing payload, or suppress it."""
    entries = records(facade)
    if message_id in entries:
        handle(facade, message_id)
        entries = records(facade)
        entry = entries[message_id]
        if _state(resident_conversation_stream.get_repository(), resident_lineage.resident_id(facade), message_id)["state"] != "queued":
            return None
        related = [item for item in entries.values() if item.get("route_occurrence")
                   and item.get("request_message_id") == entry.get("request_message_id")]
        latest = max(related, key=lambda item: _instant(item.get("requested_at")), default={})
        return str(entry["request_message_id"]) if latest.get("route_occurrence") == message_id else None
    # The recovery occurrence owns the retry; the reopened original must not also trigger it.
    if message_id in route_restorations(facade):
        return None  # Explicit recovery already owns direct delivery to its verified target.
    if any(item.get("request_message_id") == message_id and item.get("route_occurrence") for item in entries.values()):
        return None
    return message_id


def relay_completed(facade, request_id):
    repository = resident_conversation_stream.get_repository()
    for mid, entry in records(facade).items():
        if (entry.get("request_message_id") == request_id and entry.get("route_occurrence")
                and _state(repository, resident_lineage.resident_id(facade), mid)["state"] == "queued"):
            repository.set_input_state(resident_lineage.resident_id(facade), mid, "completed")


def reconcile_failed_route_tasks() -> None:
    """Repair lost failure notifications from durable task and input receipts."""
    from .family_task_receipts import RECEIPTS

    with get_lineage_data_store().database.read() as connection:
        rows = connection.execute(f"""SELECT DISTINCT child.resident_id, child.message_id
            FROM ({RECEIPTS}) task
            JOIN conversation_input_states child
              ON child.resident_id=task.target_id
             AND child.message_id=task.message_id AND child.state='failed'
            JOIN conversation_input_states parent
              ON parent.resident_id=task.facade_id
             AND parent.message_id=task.source_message_id
             AND parent.state='queued'
            WHERE task.trigger='route'""").fetchall()
    for row in rows:
        route_task_failed(row["resident_id"], row["message_id"])


def route_task_failed(resident_id: str, message_id: str) -> None:
    """Project an admitted Router input's failure without requiring a Trigger Path."""
    from . import family_task_receipts, resident_record_domain, trigger_paths

    router = resident_record_domain.find_resident(resident_id)
    if not router:
        return
    repository = resident_conversation_stream.get_repository()
    state = _state(repository, resident_id, message_id)
    if state["state"] != "failed":
        return
    original = repository.message(resident_id, message_id) or {}
    ref = repository.message_business_ref(resident_id, message_id)
    source = family_facade.parse_relay_mark(ref.removeprefix("family-route/")) if ref.startswith("family-route/") else None
    family = router.get("family") or {}
    if not source or family.get("role") != "router" or original.get("author_id") != source[0]:
        return
    facade = resident_record_domain.find_resident(source[0])
    if not facade or (facade.get("family") or {}).get("family_id") != family.get("family_id"):
        return
    lineage_id = router["lineage_id"]
    handoff = trigger_paths.resident_input(lineage_id, resident_id, message_id) or {}
    event = handoff.get("event") or {}
    payload = event.get("payload") or {}
    witnessed = (event.get("type") == "resident.message_received"
                 and (payload.get("resident_id"), payload.get("message_id")) == source)
    if not witnessed and family_task_receipts.route_source(lineage_id, resident_id, message_id) != source:
        return
    routing_failed({"occurred_at": original.get("created_at"), "payload": {
        "resident_id": source[0], "message_id": source[1],
    }}, str((state.get("error") or {}).get("message") or "Family Router 执行失败"))


def routing_failed(event, error: str, *, node_id: str = ""):
    """Close a failed routing attempt so the facade shows it and can explicitly retry."""
    from . import resident_record_domain
    payload = event.get("payload") or {}
    facade_id, request_id = str(payload.get("resident_id") or ""), str(payload.get("message_id") or "")
    ref = str(payload.get("input_business_ref") or "")
    if ref.startswith("family-route/"):
        parsed = family_facade.parse_relay_mark(ref.removeprefix("family-route/"))
        if parsed:
            facade_id, request_id = parsed
    facade = resident_record_domain.find_resident(facade_id) if facade_id else None
    if not family_facade.is_facade(facade) or not request_id or request_id in family_facade.relay_links(facade):
        return
    if node_id:
        from . import family_storage
        with get_lineage_data_store().database.read() as connection:
            if node_id not in family_storage.routing_nodes(connection, facade["family"]["family_id"]):
                return
    with resident_record_domain.state_lock(facade_id):
        entries = records(facade)
        related = [(mid, item) for mid, item in entries.items() if item.get("request_message_id") == request_id and item.get("route_occurrence")]
        if any(_instant(item["requested_at"]) > _instant(event.get("occurred_at"))
               for item in route_restorations(facade).get(request_id) or []):
            return
        if related and max(_instant(item.get("requested_at")) for _, item in related) > _instant(event.get("occurred_at")):
            return  # An earlier attempt cannot fail a newer retry.
        repository = resident_conversation_stream.get_repository()
        if request_id in family_facade.relay_links(facade) or _state(repository, facade_id, request_id)["state"] != "queued":
            return
        repository.set_input_state(facade_id, request_id, "failed", error={"message": error})
        for mid, _ in related:
            if _state(repository, facade_id, mid)["state"] == "queued":
                repository.set_input_state(facade_id, mid, "failed", error={"message": error})


def view(facade, entry):
    """Project recovery progress with the same member objects as ordinary relays."""
    result = {"state": "recovery", **entry, "original_message_id": entry.get("request_message_id") or ""}
    if entry.get("request_message_id") and entry.get("status") in {"routing", "observing"}:
        current = request_status(facade, entry["request_message_id"])
        result.update({key: value for key, value in current.items() if key != "state"})
        result["status"] = current["state"]
    if "via" in result:
        result["via"] = [
            {"resident_id": resident_id, **family_facade._member_view(resident_id)}
            for resident_id in result["via"]
        ]
    return result
