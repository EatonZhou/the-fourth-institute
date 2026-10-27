"""Input retry checkpoints; Agent execution_inputs owns execution history.

Original preparation is retained at admission, before projection can fail.
Stopped rounds and accepted requests without an execution location can requeue.
Mailbox views read Execution outcomes and admitted message references from Agent.
"""
from __future__ import annotations

import copy
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


from . import (
    resident_conversation_stream,
    resident_held_infos,
    resident_execution_work,
    resident_lineage,
    resident_mailbox,
    resident_state,
)
from .resident_mailbox import MailboxConflict
from .state_store import get_lineage_data_store

MAX_CHECKPOINTS = 10
REQUEUEABLE_STATES = frozenset({"cancelled", "failed"})
FINISHED_STATES = frozenset({"completed", "failed", "cancelled"})
PREVIEW_CHARS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _address(record: Mapping[str, Any]) -> tuple[str, str]:
    return resident_lineage.lineage_id(record), resident_lineage.resident_id(record)


def checkpoints(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The checkpoints that can still go back to the queue, oldest first."""

    try:
        lineage_id, resident_id = _address(record)
    except (KeyError, ValueError):
        return []
    if not lineage_id:
        return []
    state = get_lineage_data_store().resident_mailbox_progress.get(lineage_id, resident_id) or {}
    return [dict(item) for item in state.get("checkpoints") or [] if isinstance(item, dict)]


def queue_item(job: Mapping[str, Any]) -> dict[str, Any]:
    """The original input, without Execution preparation fields."""

    from .resident_execution_storage import queue_input
    return queue_input(job)


def retain_input_checkpoint(record: Mapping[str, Any], job: Mapping[str, Any], carried: list[Mapping[str, Any]]) -> None:
    """Retain the input needed for requeue and attach its infos to the Execution.

    Only inputs that someone sent are checkpoints: a /compact, a Fork's preparation,
    a project creation and an isolated authorized call are not.
    """

    from . import resident_input_scope

    message_id = str(job.get("user_message_id") or "").strip()
    if not message_id or str(job.get("operation") or "") or resident_execution_work.is_compact(job):
        return
    lineage_id, resident_id = _address(record)
    if not lineage_id or resident_input_scope.get(resident_id, message_id):
        return
    expected_execution_id = str(job.get("execution_id") or "")
    durable = resident_execution_work.read(record)
    # Stored preparation keeps the item as it was queued; the argument may
    # carry a prompt rebuilt for this delivery.
    source = durable if expected_execution_id and str(durable.get("execution_id") or "") == expected_execution_id else job
    repository = resident_conversation_stream.get_repository()
    completed: set[str] = set()
    for entry in checkpoints(record):
        if "item" not in entry:
            continue
        try:
            if repository.input_state(resident_id, entry.get("message_id"))["state"] == "completed":
                completed.add(str(entry.get("message_id") or ""))
        except KeyError:
            completed.add(str(entry.get("message_id") or ""))
    entry = {
        "message_id": message_id,
        "execution_id": expected_execution_id,
        "kind": "group" if source.get("mailbox_members") else "request",
        "started_at": _now(),
        "infos": [
            {"message_id": str(item.get("message_id") or ""), "author_id": str(item.get("author_id") or ""),
             "held_at": str(item.get("held_at") or "")}
            for item in carried if item.get("message_id")
        ],
        "item": queue_item(source),
    }
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.execution_inputs import attach
    if carried:
        attach(get_agent_data_store(), expected_execution_id, [
            {"message_id":item["message_id"],"role":"info","accepted_at":item.get("held_at") or entry["started_at"]}
            for item in carried if item.get("message_id")
        ])
    store = get_lineage_data_store()
    with store.resident_mailbox_progress.transaction(lineage_id, resident_id) as state:
        kept = [item for item in state.get("checkpoints") or [] if isinstance(item, dict)]
        existing = next((item for item in kept if item.get("message_id") == message_id
                         and item.get("execution_id") == expected_execution_id), None)
        if existing is not None:
            # Reservation captured the original input before any projection or
            # prompt work. Later preparation may attach infos, not replace it.
            if carried:
                existing["infos"] = entry["infos"]
            return
        kept = [item for item in kept if item.get("message_id") != message_id]
        for item in kept:
            if item.get("message_id") in completed:
                item.pop("item", None)
        state["checkpoints"] = [*kept, entry][-MAX_CHECKPOINTS:]


def recent_executions(resident_id: str) -> list[dict[str, Any]]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.execution_inputs import recent
    return list(reversed(recent(get_agent_data_store(),resident_id,limit=MAX_CHECKPOINTS)))


def _forget(record: Mapping[str, Any], message_id: str, previous: Mapping[str, Any], organizer: str) -> None:
    lineage_id,resident_id = _address(record)
    with get_lineage_data_store().resident_mailbox_progress.transaction(lineage_id,resident_id) as state:
        state["checkpoints"] = [item for item in state.get("checkpoints") or []
                           if isinstance(item,dict) and item.get("message_id") != message_id]


def _restored_media(record: Mapping[str, Any], message_id: str, item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The item's images; a copy cleaned up when the round ended is staged again from its source."""

    media = [dict(part) for part in item.get("input_media") or [] if isinstance(part, Mapping)]
    gone = [
        index for index, part in enumerate(media)
        if part.get("original_source") and not Path(str(part.get("source") or "")).is_file()
    ]
    if not gone:
        return media
    if any(not Path(str(media[index]["original_source"])).is_file() for index in gone):
        raise MailboxConflict("这一轮的图片附件已经清理，不能放回队列；请重新发送这条消息")
    from . import resident_media_domain, resident_record_domain

    staged = resident_media_domain.stage_resident_input_images(
        [{**part, "source": part["original_source"]} if index in gone else part for index, part in enumerate(media)],
        resident_record_domain.sessions_path(dict(record)), message_id,
    )
    return [
        {**part, "original_source": media[index].get("original_source") or part.get("original_source")}
        for index, part in enumerate(staged)
    ]




def _mailbox_input(entry: Mapping[str, Any]) -> dict[str, Any]:
    from .state_store import STORED_INPUT_MESSAGE
    item = copy.deepcopy(entry.get("item") or {})
    if item.pop(STORED_INPUT_MESSAGE, False):
        item["input_message"] = copy.deepcopy(entry["message"])
    return queue_item(item)


def _accepted_checkpoint(record: Mapping[str, Any], message_id: str) -> dict[str, Any]:
    """Recover preparation only from this Resident's accepted Mailbox rows.

    Receipts and instruction references prove acceptance, not message content.
    In particular, a group's assembled prompt cannot recreate missing members.
    """
    resident_id = resident_lineage.resident_id(record)
    stored = get_lineage_data_store().mailbox_entry(resident_id, message_id)
    if not stored or stored["place"] != "pending" or stored["group_id"]:
        raise MailboxConflict(f"没有可恢复的原始接收记录：{message_id}")
    item = _mailbox_input(stored)
    message = item.get("input_message") or {}
    ids = [str(m.get("message_id") or "") for p in message.get("content_parts") or []
           if p.get("type") == "mailbox" for m in p.get("members") or []]
    if ids:
        entries = {entry["message_id"]: entry for entry in get_lineage_data_store().mailbox_entries(
            [resident_id], message_ids=ids,
        )}
        missing = [mid for mid in ids if mid not in entries or not entries[mid].get("item")]
        if missing:
            raise MailboxConflict("缺少确切原消息或接收封装，不能恢复：" + "、".join(missing))
        if any(entries[mid]["place"] != "pending" or entries[mid]["group_id"] != message_id for mid in ids):
            raise MailboxConflict("群体成员已有其他位置或归属，不能恢复")
        item["mailbox_members"] = [_mailbox_input(entries[mid]) for mid in ids]
    return {"message_id": message_id, "kind": "group" if ids else "request", "item": item, "infos": []}


def _validate_recovery_item(record: Mapping[str, Any], item: Mapping[str, Any], job: Mapping[str, Any]) -> None:
    """Validate every exact local envelope before changing any message or queue."""
    from . import resident_input_scope
    repository = resident_conversation_stream.get_repository()
    resident_id = resident_lineage.resident_id(record)
    root_id = str(item.get("user_message_id") or "")
    seen: set[str] = set()
    for value in resident_execution_work.input_items(item):
        message_id = str(value.get("user_message_id") or "")
        message = value.get("input_message") or {}
        if (not message_id or message_id in seen or message.get("message_id") != message_id
                or message.get("role") != "user" or not message.get("author_id")
                or not message.get("created_at") or not isinstance(message.get("content_parts"), list)
                or not value.get("actor")):
            raise MailboxConflict(f"缺少确切原消息或接收封装，不能恢复：{message_id or root_id}")
        seen.add(message_id)
        if (value.get("operation") or resident_execution_work.is_compact(value)
                or value.get("activation_policy") == "attach_to_next"
                or resident_input_scope.get(resident_id, message_id)):
            raise MailboxConflict(f"该输入不是可恢复的普通请求：{message_id}")
        previous = repository.input_state(resident_id, message_id)
        if message_id != root_id:
            location = resident_execution_work.input_location(resident_id, message_id, job)
            group = resident_mailbox.group_of(record, message_id)
            error_group = (previous.get("error") or {}).get("grouped_into_message_id")
            if location is not None or (group and group != root_id and error_group != root_id):
                raise MailboxConflict(f"成员已有执行位置或属于其他群体：{message_id}")
            if previous["state"] != "queued" and not (
                previous["state"] in REQUEUEABLE_STATES and error_group == root_id
            ):
                raise MailboxConflict(f"成员不能随此群体恢复：{message_id}")
        try:
            repository.validate_message_identity(resident_id, message)
        except KeyError:
            stored = repository.agent_data.group_source_message(resident_id, message_id)
            if stored is not None:
                public = resident_conversation_stream._message_from_storage(stored)
                fields = resident_conversation_stream._immutable_message_fields
                if fields(public) != fields(message):
                    raise MailboxConflict(f"原消息前像冲突：{message_id}")
        expected = [str(m.get("message_id") or "") for p in message["content_parts"]
                    if p.get("type") == "mailbox" for m in p.get("members") or []]
        actual = [str(m.get("user_message_id") or "") for m in value.get("mailbox_members") or []]
        if expected != actual or (expected and message.get("author_id") != "mailbox"):
            raise MailboxConflict(f"群体原成员或顺序不一致：{message_id}")


def _validate_recovery_infos(
    record: Mapping[str, Any], entry: Mapping[str, Any], job: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate all carried infos before reopening anything, under the retry locks/transaction."""
    from management_common.database_time import instant
    from management_portal.execution.execution_inputs import latest

    infos = entry.get("infos") or []
    if not isinstance(infos, list) or any(not isinstance(info, dict) for info in infos):
        raise MailboxConflict("恢复检查点的 info 引用不完整")
    resident_id = resident_lineage.resident_id(record)
    root_id = str(entry.get("message_id") or "")
    execution_id = str(entry.get("execution_id") or "")
    repository = resident_conversation_stream.get_repository()
    if not execution_id:
        # Accepted inputs may not have reached reservation. An existing round
        # cannot use a lost checkpoint reference to bypass its info-set check.
        if not infos and latest(repository.agent_data, resident_id, root_id) is None:
            return []
        raise MailboxConflict("恢复检查点缺少原 Execution 附带身份")
    execution = repository.agent_data.execution(execution_id) or {}
    inputs = {value["message_id"]: value for value in execution.get("inputs") or []}
    ids = [str(info.get("message_id") or "") for info in infos]
    if (execution.get("agent_id") != resident_id or inputs.get(root_id, {}).get("role") != "primary"
            or not all(ids) or len(set(ids)) != len(ids)
            or set(ids) != {mid for mid, value in inputs.items() if value["role"] == "info"}):
        raise MailboxConflict("恢复检查点的 info 与原 Resident/Execution 附带身份不一致")
    store = get_lineage_data_store()
    states = repository.input_states_by_id([(resident_id, mid) for mid in ids])
    receipts = store.terminal_input_receipts([(resident_id, mid) for mid in ids])
    fields = resident_conversation_stream._immutable_message_fields
    for info, message_id in zip(infos, ids):
        message = repository.message(resident_id, message_id)
        state = states.get((resident_id, message_id))
        if (message is None or state is None or message.get("role") != "user"
                or not message.get("created_at") or not isinstance(message.get("content_parts"), list)
                or not info.get("author_id") or message.get("author_id") != info["author_id"]):
            raise MailboxConflict(f"缺少同 Resident 的 info 原件、接收记录或身份冲突：{message_id}")
        try:
            held_at = instant(info.get("held_at") or entry.get("started_at"))
        except (TypeError, ValueError):
            held_at = None
        if held_at is None or held_at != instant(inputs[message_id]["accepted_at"]):
            raise MailboxConflict(f"info 的原附带时间冲突：{message_id}")
        owned = job if resident_execution_work.is_active(job) else {
            "queue": resident_execution_work.normalized_queue(job),
        }
        located = any(value.get("user_message_id") == message_id
                      for value in resident_execution_work.input_items(owned))
        original = latest(repository.agent_data, resident_id, message_id) or {}
        waiting = store.mailbox_entry(resident_id, message_id)
        if (located or original.get("execution_id") != execution_id
                or (waiting and (waiting["place"] != "info" or waiting["group_id"]))):
            raise MailboxConflict(f"info 已有其他执行位置或归属：{message_id}")
        if waiting:
            public = resident_conversation_stream._message_from_storage(waiting["message"])
            if fields(public) != fields(message) or waiting["author_id"] != message["author_id"]:
                raise MailboxConflict(f"info 原消息前像冲突：{message_id}")
        # Consumption completes the info independently of its carrier's outcome.
        # A projected terminal receipt of this exact round is also retryable;
        # an explicit terminal state (notably info_withdrawn) is not that receipt.
        receipt = receipts.get((resident_id, message_id), {})
        retryable = state["state"] == "queued" or (
            state["state"] == "completed" and message.get("released_with") == root_id
        ) or (state["state"] in REQUEUEABLE_STATES and receipt.get("execution_id") == execution_id)
        if not retryable or (state.get("error") or {}).get("code") == "info_withdrawn":
            raise MailboxConflict(f"info 已独立结束或撤回，不能随这一轮恢复：{message_id}")
    return copy.deepcopy(infos)


def requeue(
    resident_id: str, actor: str, message_id: str, *,
    portal_reason: str = "", automatic: bool = False,
) -> dict[str, Any]:
    """Restore a round; only background recovery spends the automatic retry budget.

    The caller's identity does not classify the operation. ``portal_reason``
    authorizes a Portal-internal callback; ``automatic`` identifies background
    recovery independently of that authorization.
    """

    from . import family_facade, invariant_locks
    from . import (
        resident_directory_domain,
        resident_intake_domain,
        resident_lifecycle_domain,
        resident_record_domain,
        resident_workspace_domain,
    )
    from . import resident_backend

    organizer = resident_mailbox.organizer_id(actor)
    target = resident_workspace_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=not bool(portal_reason))
    message_id = str(message_id or "").strip()
    if not message_id:
        raise ValueError("message_id required")
    repository = resident_conversation_stream.get_repository()
    now = resident_lifecycle_domain.now_iso()
    with (resident_lifecycle_domain.state_lock(target.resident_id),
          invariant_locks.resident_conversation_lock(target.resident_id),
          get_lineage_data_store().database.transaction()):
        record = resident_workspace_domain.find_resident(target.resident_id)
        if not record:
            raise KeyError("resident not found")
        if not portal_reason and not resident_directory_domain.can_manage(record, organizer):
            raise PermissionError("只有对该 Resident 有整理权限的主体可以把消息放回队列")
        if family_facade.is_facade(record):
            raise MailboxConflict("Family 门面不执行，没有可以放回的一轮")
        try:
            previous = repository.input_state(target.resident_id, message_id)
        except KeyError as exc:
            raise MailboxConflict("这一轮的输入记录已不在，不能放回队列") from exc
        job = resident_execution_work.read(record)
        active = resident_execution_work.is_active(job)
        if resident_execution_work.input_location(target.resident_id, message_id, job) is not None:
            if (active and job.get("user_message_id") == message_id) or any(
                item.get("user_message_id") == message_id for item in resident_execution_work.normalized_queue(job)
            ):
                return resident_intake_domain.resident_operation_result(target.resident_id, message_id=message_id, message_ids=[message_id])
            raise MailboxConflict("这条消息已在等待或正在处理")
        entry = next((item for item in checkpoints(record) if item.get("message_id") == message_id), None)
        if entry is None:
            group_id = resident_mailbox.group_of(record, message_id)
            if group_id:
                raise MailboxConflict(f"这条消息属于群体消息 {group_id}，请放回整个群体")
            if previous["state"] != "queued" or automatic:
                raise MailboxConflict("这一轮没有可恢复的检查点或未完成接收记录")
            entry = _accepted_checkpoint(record, message_id)
        status = str(previous.get("state") or "")
        if status not in REQUEUEABLE_STATES and status != "queued":
            raise MailboxConflict({
                "completed": "这一轮已经完成，不能放回队列；需要重做时请重新发送",
                "running": "这一轮正在进行，不能放回队列",
            }.get(status, f"这一轮的状态是 {status}，不能放回队列"))
        if automatic:
            from .resident_work_recovery import MAX_RECOVERY_ATTEMPTS, recovery_entry, set_recovery_entry
            attempts = int(recovery_entry(record, message_id).get("attempts") or 0)
            if attempts >= MAX_RECOVERY_ATTEMPTS:
                raise MailboxConflict("同一输入已自动恢复两次；如需继续，请显式恢复原消息")
        if not isinstance(entry.get("item"), dict):
            raise MailboxConflict("这一轮没有保留可放回的原始记录")
        item = queue_item(entry["item"])
        if str(item.get("user_message_id") or "") != message_id:
            raise MailboxConflict("恢复检查点的输入身份不一致")
        _validate_recovery_item(record, item, job)
        infos = _validate_recovery_infos(record, entry, job)
        # Only this recovery owner may mark a fresh reservation as an explicit
        # retry. Pre-reservation accepted orphans have no terminal predecessor.
        item.pop("retry_of_execution_id", None)
        predecessor = str(entry.get("execution_id") or "")
        if predecessor:
            original = repository.agent_data.execution(predecessor) or {}
            if original.get("status") in REQUEUEABLE_STATES:
                item["retry_of_execution_id"] = predecessor
        item["user_message_id"] = message_id
        item.pop("execution_id", None)
        item["enqueued_at"] = now
        item["input_media"] = _restored_media(record, message_id, item)
        if resident_backend.record_session_id(record):
            # The Resident has a session now; the round no longer creates one.
            item.pop("bootstrap_session", None)
        members = [str(value["user_message_id"]) for value in item.get("mailbox_members") or []]
        for value in resident_execution_work.input_items(item):
            repository.accept_user_message(target.resident_id, value["input_message"])
        for reopened in [message_id, *members]:
            repository.reopen_input(target.resident_id, reopened)
        for info in infos:
            repository.reopen_input(target.resident_id, info["message_id"])
        resident_held_infos.restore(record, infos)
        # Retire only the previous round's checkpoint. Reserving a new round
        # below captures its own original preparation in the same transaction.
        _forget(record, message_id, previous, organizer)
        if active:
            queue = resident_execution_work.normalized_queue(job)
            job["queue"] = [item, *queue]
            job["updated_at"] = now
            resident_execution_work.write(record, job)
        else:
            waiting = resident_execution_work.normalized_queue(job)
            if resident_lifecycle_domain.resident_intake_state(record) == resident_state.INTAKE_CLOSED:
                resident_execution_work.write(record, {"queue": [item, *waiting]})
            else:
                next_job = {**item, "started_at": now, "updated_at": now, "error": "", "queue": waiting}
                resident_execution_work.ensure_execution_id(next_job)
                resident_execution_work.write(record, next_job)
                record["session_state"] = resident_record_domain.record_backend_session_state(record)
                record["last_error"] = ""
        if automatic:
            set_recovery_entry(record, message_id, attempts=attempts + 1, recovered_by=organizer,
                               recovered_at=now, disabled=False)
        record["updated_at"] = now
        resident_workspace_domain.save_resident_record(record)
    return resident_intake_domain.resident_operation_result(
        target.resident_id,
        message_id=message_id,
        message_ids=[message_id, *[str(info["message_id"]) for info in infos]],
    )


def _preview(message: Mapping[str, Any], fallback: str = "") -> str:
    text = " ".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text" and part.get("text")
    )
    return " ".join((text or str(fallback or "")).split())[:PREVIEW_CHARS]


def _summary(message: Mapping[str, Any], message_id: str, *, fallback_author: str = "", fallback_time: str = "",
             preview: str = "") -> dict[str, Any]:
    summary = {
        "message_id": message_id,
        "author_id": str(message.get("author_id") or fallback_author or ""),
        "created_at": str(message.get("created_at") or fallback_time or ""),
        "preview": _preview(message, preview),
    }
    attachments = sum(
        1 for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") in {"image", "file"}
    )
    if attachments:
        summary["attachments"] = attachments
    if isinstance(message.get("origin"), Mapping):
        summary["origin"] = copy.deepcopy(dict(message["origin"]))
    return summary


def view(resident_id: str, actor: str) -> dict[str, Any]:
    """What the Mailbox tab shows: waiting items and infos, the round in progress, and the recent checkpoints."""

    from . import family_facade
    from . import resident_directory_domain, resident_workspace_domain

    target = resident_workspace_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    record = resident_workspace_domain.find_resident(target.resident_id) or {"resident_id": target.resident_id}
    try:
        in_lineage = bool(resident_lineage.lineage_id(record))
    except (KeyError, ValueError):
        in_lineage = False
    repository = resident_conversation_stream.get_repository()

    def message(message_id: str) -> dict[str, Any]:
        try:
            return dict(repository.message(target.resident_id, message_id) or {})
        except (KeyError, ValueError):
            return {}

    projection = repository.current_projection(target.resident_id)
    state = repository.current_state(target.resident_id)
    job = resident_execution_work.read(record) if in_lineage else {}
    groups = {
        str(item.get("user_message_id") or ""): list(item.get("mailbox_members") or [])
        for item in [job, *resident_execution_work.normalized_queue(job)] if resident_execution_work.is_active(job) and item
    }
    pending: list[dict[str, Any]] = []
    visible_queue = list((projection.get("queue") or {}).get("items") or [])
    from . import resident_initialization
    if resident_initialization.blocked(record):
        seen = {str(i.get('message_id') or '') for i in visible_queue}
        for waiting in resident_initialization.waiting_inputs(record, job):
            mid = str(waiting.get('user_message_id') or '')
            if mid and mid not in seen:
                visible_queue.append({'message_id': mid, 'author_id': waiting.get('actor', ''),
                                      'enqueued_at': waiting.get('enqueued_at', '')})
                seen.add(mid)
    for item in visible_queue:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("message_id") or "")
        stored = message(item_id)
        entry = {
            "kind": "group" if str(item.get("author_id") or "") == "mailbox" else "request",
            **_summary(stored, item_id, fallback_author=str(item.get("author_id") or ""),
                       fallback_time=str(item.get("enqueued_at") or ""), preview=str(item.get("content_preview") or "")),
            "enqueued_at": str(item.get("enqueued_at") or ""),
        }
        if entry["kind"] == "group":
            entry["preview"] = " ".join(str(item.get("content_preview") or "").split())[:PREVIEW_CHARS]
            entry["members"] = [
                _summary(dict(member.get("input_message") or {}), str(member.get("user_message_id") or ""),
                         fallback_author=str(member.get("actor") or ""), preview=str(member.get("message") or ""))
                for member in groups.get(item_id) or [] if isinstance(member, Mapping)
            ] or [
                _summary(message(member_id), member_id) for member_id in resident_mailbox.members_of(record, item_id)
            ]
        pending.append(entry)
    if not family_facade.is_facade(record):
        for held in resident_held_infos.peek(record) if in_lineage else []:
            info_id = str(held.get("message_id") or "")
            pending.append({
                "kind": "info",
                **_summary(message(info_id), info_id, fallback_author=str(held.get("author_id") or ""),
                           fallback_time=str(held.get("held_at") or "")),
                "held_at": str(held.get("held_at") or ""),
            })

    requeueable = {
        (str(item.get("message_id") or ""), str(item.get("execution_id") or "")): item.get("item")
        for item in checkpoints(record)
    }
    recorded = [
        {**entry, **({"item": requeueable[key]} if isinstance(requeueable.get(key), dict) else {})}
        for entry in recent_executions(target.resident_id)
        for key in [(str(entry.get("message_id") or ""), str(entry.get("execution_id") or ""))]
    ]

    def round_infos(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            _summary(message(str(info.get("message_id") or "")), str(info.get("message_id") or ""),
                     fallback_author=str(info.get("author_id") or ""), fallback_time=str(info.get("held_at") or ""))
            for info in entry.get("infos") or [] if isinstance(info, Mapping) and info.get("message_id")
        ]

    def round_view(message_id: str, entry: Mapping[str, Any]) -> dict[str, Any]:
        stored = message(message_id)
        kind = str(entry.get("kind") or ("group" if stored.get("author_id") == "mailbox" else "request"))
        result = {"kind": kind, **_summary(stored, message_id), "infos": round_infos(entry)}
        if kind == "group":
            members = resident_mailbox.members_of(record, message_id)
            result["preview"] = f"Mailbox · {len(members)} 条消息"
            result["members"] = [_summary(message(member_id), member_id) for member_id in members]
        return result

    active_id = str(state.get("active_message_id") or "")
    active = None
    if active_id:
        entry = next((item for item in recorded if item.get("message_id") == active_id), {})
        active = {**round_view(active_id, entry), "started_at": str(entry.get("started_at") or "")}
    recent: list[dict[str, Any]] = []
    for entry in reversed(recorded):
        round_id = str(entry.get("message_id") or "")
        if not round_id or round_id == active_id:
            continue
        try:
            input_state = repository.input_state(target.resident_id, round_id)
        except KeyError:
            continue
        status = str(input_state.get("state") or "")
        if status not in FINISHED_STATES:
            continue
        view_entry = {
            **round_view(round_id, entry),
            "state": status,
            "finished_at": str(input_state.get("updated_at") or ""),
            "started_at": str(entry.get("started_at") or ""),
            "can_requeue": status in REQUEUEABLE_STATES and isinstance(entry.get("item"), dict),
        }
        error = input_state.get("error") if isinstance(input_state.get("error"), Mapping) else {}
        if error.get("message") or error.get("code"):
            view_entry["error"] = str(error.get("message") or error.get("code") or "")[:PREVIEW_CHARS]
        recent.append(view_entry)
    return {
        "resident_id": target.resident_id,
        "pending": pending,
        "active": active,
        "recent": recent,
        "can_organize": bool(resident_directory_domain.can_manage(record, actor)),
    }
