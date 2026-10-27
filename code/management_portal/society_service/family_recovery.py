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
    latest = max((item for item in attempts if _instant(item["occurred_at"]) >= retry_at), key=lambda item: _instant(item["occurred_at"]), default={})
    with get_lineage_data_store().database.read() as connection:
        routing_nodes = family_storage.routing_nodes(connection, facade["family"]["family_id"])
    failures = [item["result"] for item in latest.get("failures") or [] if item["node_id"] in routing_nodes]
    if latest.get("status") in {"failed", "cancelled"} and failures:
        failure = failures[0]
        detail = failure.get("failure") or failure.get("error") or failure
        return {"state": "failed", "error": detail, "attempt_at": latest["occurred_at"]}
    return {"state": "routing"}


def _allowed(facade, request, actor):
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
        if found and found.get("role") == "user" and quoted not in entries and _allowed(facade, found, message["author_id"]):
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
            if not request or not _allowed(facade, request, message["author_id"]):
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
    if any(item.get("request_message_id") == message_id and item.get("route_occurrence") for item in entries.values()):
        return None
    return message_id


def relay_completed(facade, request_id):
    repository = resident_conversation_stream.get_repository()
    for mid, entry in records(facade).items():
        if (entry.get("request_message_id") == request_id and entry.get("route_occurrence")
                and _state(repository, resident_lineage.resident_id(facade), mid)["state"] == "queued"):
            repository.set_input_state(resident_lineage.resident_id(facade), mid, "completed")


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
    result = {"state": "recovery", **entry, "original_message_id": entry.get("request_message_id") or ""}
    if entry.get("request_message_id") and entry.get("status") in {"routing", "observing"}:
        current = request_status(facade, entry["request_message_id"])
        result.update({key: value for key, value in current.items() if key != "state"})
        result["status"] = current["state"]
    return result
def archive_result(facade_id: str, message_id: str, steward_message_id: str, actor: str, *, reason: str) -> dict:
    """Attach a completed organizational review without executing the request again.

    Keep the original failed receipt and immutable messages. The existing relay
    projection exposes the completed source and its real reply IDs; the link
    records who reconciled the failure and why.
    """
    from . import resident_access, resident_record_domain

    facade = resident_record_domain.find_resident(facade_id)
    if not family_facade.is_facade(facade):
        raise ValueError("目标不是 Family 门面")
    steward = family_facade.family_steward_id(facade)
    if actor != steward and not resident_access.can_manage(facade, actor):
        raise PermissionError("只有本 Family Steward 或能管理门面的身份可归档成果")
    if not str(reason or "").strip():
        raise ValueError("归档必须说明已完成成果与原请求的关系")
    repository = resident_conversation_stream.get_repository()
    store = get_lineage_data_store()
    lineage_id = facade["lineage_id"]
    with resident_record_domain.state_lock(facade_id), store.family_quota_reviews.transaction(lineage_id, facade_id):
        original = repository.message(facade_id, message_id)
        if original is None or original.get("role") != "user":
            raise ValueError("门面原请求不存在")
        previous = repository.input_state(facade_id, message_id)
        if previous.get("state") == "cancelled":
            raise ValueError("原请求已取消，不能归档为完成")
        source = repository.message(steward, steward_message_id) if steward else None
        _validate_review_source(facade, original, source,
                                repository.message_business_ref(steward, steward_message_id) if source else "")
        completed = repository.input_state(steward, steward_message_id)
        replies = list(completed.get("reply_message_ids") or [])
        if completed.get("state") != "completed" or not replies:
            raise ValueError("复核输入必须已完成且有真实回复")
        for reply_id in replies:
            reply = repository.message(steward, reply_id)
            if not reply or reply.get("role") != "agent" or reply.get("author_id") != steward or reply.get("reply_to_message_id") != steward_message_id:
                raise ValueError("复核回复不属于该 Steward 输入")
        with store.family_relays.transaction(lineage_id, facade_id) as ledger:
            links = ledger.setdefault("links", {})
            link = links.get(message_id)
            if link:
                if (link.get("resident_id"), link.get("message_id")) != (steward, steward_message_id) or not link.get("archive"):
                    raise ValueError("原请求已有其他承接关系，不能覆盖")
            else:
                if previous.get("state") != "failed":
                    raise ValueError("仅可归档尚未转交的失败请求")
                links[message_id] = {
                    "resident_id": steward, "message_id": steward_message_id, "relayed_at": datetime.now(timezone.utc).isoformat(),
                    "archive": {"actor": actor, "reason": reason.strip(), "failed_receipt": previous,
                                "reply_message_ids": replies},
                }
        return {"status": "archived", "message_id": message_id,
                "steward_message_id": steward_message_id, "reply_message_ids": replies}


def _validate_review_source(facade: dict, original: dict, source: dict | None, business_ref: str) -> None:
    """Validate the immutable, facade-authored review envelope, not user-supplied evidence.

    Older DispatchFamilyTask used a duty ref and one JSON facts line. That
    authenticated envelope remains readable; new dispatches bind the original
    ID directly in business_ref and never need prompt parsing.
    """
    import json

    facade_id, mid = facade["resident_id"], original["message_id"]
    if (not source or source.get("role") != "user" or source.get("author_id") != facade_id
            or _instant(source.get("created_at")) < _instant(original.get("created_at"))):
        raise ValueError("成果来源不是原请求之后的本门面复核任务")
    if business_ref == f"family-review/{facade_id}/message/{mid}":
        return
    if business_ref == "duty/family-route_review":
        facts = []
        for part in source.get("content_parts") or []:
            if part.get("type") != "text":
                continue
            for line in str(part.get("text") or "").splitlines():
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    facts.append(value)
        if len(facts) == 1 and all(facts[0].get(key) == value for key, value in {
            "message_id": mid, "facade": facade_id, "lineage_id": facade["lineage_id"],
            "family_id": facade["family"]["family_id"],
        }.items()):
            return
    raise ValueError("复核任务未绑定同一 Family 的原请求")
