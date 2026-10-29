"""Failures reach Effect recipients or automatic Resident failures' human admins.

A Lineage's admins are the active Users whose own entries let UpdateLineage
pass on it (with the permission system off, its creator).

A Lineage can name its own failure handler: an Event Source watching the
failed object for its failure Event and passing it on.  The handler looks
into the failure and tells the admins itself, so the platform adds nothing.
Otherwise the platform writes the failure to the selected users in the Leader's
name, without starting the Leader's work; an admin answering the message
reaches the Leader.  Admins who cannot view the Leader's Conversation, and
Lineages without a Leader, fall back to a notification.

An Effect activation whose call outcome no retry can settle is not a
failure: ``ask_resolution`` writes a Lineage notification asking for
ResolveEffectActivation.
"""
import logging

from management_common.database_time import instant, iso_time
from management_common.json_columns import json_value
from management_common.message_identity import derived_message_identity
from management_portal import permission_system

from . import lineage_human_notifications, trigger_path_storage, trigger_paths
from . import lineage_aggregate

LOGGER = logging.getLogger(__name__)


def creating_user(lineage_id: str) -> list[str]:
    """The User who created the Lineage, from its registered descriptor ([] when a Resident or nobody did)."""
    from management_common.principal import actor_principal
    from management_portal.resources.collaboration_registry import CollaborationDescriptorStore

    try:
        record = CollaborationDescriptorStore().get(lineage_id)
        principal = actor_principal(str((record.get("attributes") or {}).get("created_by") or "").strip())
    except (KeyError, ValueError):
        return []
    return [principal.id] if principal.kind == "user" else []


def admins(lineage_id: str) -> list[str]:
    """Who can fix the Lineage: the Users whose entries let UpdateLineage pass on it; with no permission system, its creator."""
    recipients = permission_system.users_permitted("UpdateLineage", "lineage_id", lineage_id)
    return creating_user(lineage_id) if recipients is None else recipients


def ask_resolution(lineage_id: str, wire_id: str, hold: dict) -> None:
    """Ask people to resolve an Effect activation that no retry can settle.

    It reaches people the way a failure does, once per activation, but
    resolving is required work: the Effect's own failure recipients are
    asked, or the Lineage's admins when it names none. No failure Event is
    emitted, so a failure handler never sees it and the platform always asks.
    """
    from .effect_failure_notifications import recipients as effect_recipients

    effect_id, key = hold["effect_id"], hold["activation_key"]
    recipients = effect_recipients(lineage_id, effect_id) or admins(lineage_id)
    if not recipients:
        return
    title = "Effect 结果未知，等待处置"
    instruction = hold.get("instruction") or "调用"
    message = (
        f"Effect {effect_id} 的一次 {instruction} 调用结果未知。重试只会读回执行方保存的同一张回执，"
        f"已经停止重试。这次 Event 留在连线 {wire_id} 上等待处置，连线后面的 Event 照常投递。\n\n"
        f"请先核对 {instruction} 要改的对象是否已经改变，再在运行面板打开 Trigger Path "
        f"{hold.get('trigger_path_id') or '（未记录）'}，在这个 Effect 步骤上选“标记成功”或“标记失败”；"
        f"也可以调用 ResolveEffectActivation（lineage_id={lineage_id}，activation_key={key}）。"
        f"处置后这次激活结束，不再执行剩下的调用，也不产生结果 Event。\n\n"
        f"执行方回执：{hold.get('receipt') or hold.get('error') or '未记录'}\n"
        f"指令 ID：{hold.get('instruction_id') or '未记录'}"
    )[:4000]
    subject = f"effect-resolution:{key}"
    try:
        # One request per activation: a retry on a later day, after asking
        # succeeded but holding did not, writes the same message again.
        recipients = _tell_as_leader(lineage_id, recipients, subject=subject, period="activation",
                                     title=title, message=message)
    except Exception:  # noqa: BLE001 - the request must still reach its people
        LOGGER.exception("resolution request for %s in the Leader's name failed; sending a notification", lineage_id)
    if recipients:
        lineage_human_notifications.create_notification(
            lineage_id, notification_id=subject, title=title, message=message, recipients=recipients,
            severity="warning", trigger_path_id=hold.get("trigger_path_id") or "", created_by="portal",
        )


def automatic(lineage_id: str, trigger_path_id: str, record: dict) -> bool:
    if record.get("trigger_type") in {"time.reached", "file.changed"}:
        return True
    if (record.get("event") or {}).get("type") in {"time.reached", "file.changed"}:
        return True
    if not trigger_path_id:
        return False
    from .trigger_paths import get
    path = get(lineage_id, trigger_path_id)
    return bool(path and path["input"].get("type") in {"time.reached", "file.changed"})


def _watches_failure(source: dict, watched: tuple[str, str]) -> bool:
    from .lineage_program import event_source_subject_ids

    kind, subject_id = watched
    event_type = str(source.get("event_type") or "")
    if kind == "resident":
        return event_type == "resident.work_failed" and subject_id in event_source_subject_ids(source)
    effects = {str(item) for item in source.get("subject_effect_ids") or []}
    effects.add(str(source.get("subject_effect_id") or ""))
    if subject_id not in effects:
        return False
    return event_type == "effect.failed" or (
        kind == "script" and event_type == "script.finished"
        and str(source.get("outcome") or "any") in {"any", "failed"}
    )


def handled(lineage_id: str, watched: tuple[str, str] | None) -> bool:
    """Whether an Event Source watching ``watched`` for its failure passes the Event on."""

    if watched is None:
        return False
    from . import lineage_catalog, lineage_program_queries

    observers: dict[str, list[str]] = {}
    if watched[0] == "resident":
        # A Resident is also watched by scope: every member of a Lineage, or all of Portal.
        observers = lineage_catalog.get_catalog().program_resident_observers(watched[1], "resident.work_failed")
    for observing in sorted(set(observers) | {lineage_id}):
        try:
            plan = lineage_program_queries._compiled_program(lineage_aggregate.read_lineage_state(observing))
        except (KeyError, FileNotFoundError):
            continue
        for source_id, source in (plan.get("event_sources") or {}).items():
            if isinstance(source, dict) and source.get("subscriber_routes") and (
                _watches_failure(source, watched) or source_id in (observers.get(observing) or [])
            ):
                return True
    return False


def _leader(lineage_id: str) -> str:
    try:
        return str(lineage_aggregate.read_lineage_state(lineage_id).get("leader_resident_id") or "")
    except (KeyError, FileNotFoundError):
        return ""


def _tell_as_leader(lineage_id: str, recipients: list[str], *, subject: str, period: str, title: str, message: str) -> list[str]:
    """Write the failure in the Leader's name, one message per subject and period; return the admins it could not reach."""

    from . import outward_messages

    leader = _leader(lineage_id)
    if not leader:
        return recipients
    written = outward_messages.post_platform_notice(
        leader, recipients, f"{title}\n\n{message}",
        message_id=derived_message_identity("lineage-failure", lineage_id, subject, period),
        business_ref=f"lineage-failure:{lineage_id}",
    )
    return list(written["unreached"])


def notify(
    lineage_id: str,
    trigger_path_id: str,
    record: dict,
    *,
    subject: str,
    title: str,
    message: str,
    watched: tuple[str, str] | None = None,
) -> None:
    """Tell the configured Effect users, or the admins of an automatic Resident failure."""

    effect = watched is not None and watched[0] in {"effect", "script"}
    if record.get("status") not in {"failed", "timed_out", "lost"} or (
        not effect and not automatic(lineage_id, trigger_path_id, record)
    ):
        return
    if effect:
        from .effect_failure_notifications import recipients as effect_recipients
        recipients = effect_recipients(lineage_id, watched[1])
        # Editing the list must take effect even when this Effect already failed today.
        from hashlib import sha256
        subject += ":recipients:" + sha256("\n".join(sorted(recipients)).encode()).hexdigest()[:16]
    else:
        recipients = admins(lineage_id)
    if not recipients:
        return
    timestamp = str(record.get("finished_at") or record.get("completed_at") or "")
    try:
        if handled(lineage_id, watched):
            return
        recipients = _tell_as_leader(
            lineage_id, recipients, subject=subject, period=timestamp[:10], title=title, message=message,
        )
    except Exception:  # noqa: BLE001 - a failure must still reach its admins
        LOGGER.exception("failure message for %s in the Leader's name failed; sending a notification", lineage_id)
    if recipients:
        lineage_human_notifications.create_notification(
            lineage_id, notification_id=f"{subject}:{timestamp[:10]}",
            title=title, message=message, recipients=recipients, severity="error",
            trigger_path_id=trigger_path_id, created_by="portal",
        )


def notify_failed_path(identity: str) -> None:
    from . import lineage_program_runtime
    store = trigger_paths.data_store()
    with store.database.read() as connection:
        path = trigger_path_storage.read(connection, identity)
        if not path or path["status"] != "failed":
            return
        steps = [dict(row) for row in connection.execute(
            "SELECT s.*, (SELECT max(e.finished_at) "
            f"FROM trigger_path_execution_links l JOIN {trigger_paths.agent_executions_table()} e USING(execution_id) "
            "WHERE l.step_id=s.step_id AND e.status='failed') AS execution_failed_at "
            "FROM trigger_path_steps s WHERE s.trigger_path_id=%s", (identity,),
        ).fetchall()]
    if path["root_kind"] == "event":
        source = lineage_program_runtime.read_runtime(path["lineage_id"])["event_sources"].get(path["root_node_id"], {})
        if source.get("paused"):
            return  # Reconciliation of a disabled source does not start new alerts.
    inputs = trigger_paths.input_receipts(s for s in steps if s["kind"] == "resident")
    for step in steps:
        receipt = inputs.get(step["step_id"], {})
        if step["status"] != "failed" and not step["execution_failed_at"] and receipt.get("state") != "failed":
            continue
        result = json_value(step["result_json"] or "{}")
        if result.get("disposition"):
            # An administrator already handled this occurrence. Preserve its
            # terminal evidence without emitting another generic failure alert.
            continue
        failure = (receipt.get("error") if receipt.get("state") == "failed" else None) or result.get("failure") or result
        if not isinstance(failure, dict):
            failure = {"message": str(failure)}
        failed_at = (receipt.get("updated_at") if receipt.get("state") == "failed" else None) or step["execution_failed_at"] or step["finished_at"]
        failed_at = iso_time(instant(failed_at)) or path["finished_at"]
        reason = str(failure.get("message") or failure.get("error") or failure.get("diagnostic") or
                     ("节点 " + step["node_id"] + " 执行失败，未记录具体原因。"))
        from . import family_recovery
        family_recovery.routing_failed(path["input"], reason, node_id=step["node_id"])
        notify(
            path["lineage_id"], identity,
            {"status": "failed", "finished_at": failed_at, "event": path["input"]},
            subject=step["kind"] + ":" + step["node_id"], title="Lineage 自动执行失败",
            message=f"{reason}\n\n失败发生时间：{failed_at}\n节点：{step['node_id']}\nTrigger Path：{identity}",
            watched=(step["kind"], step["node_id"]) if step["kind"] in {"resident", "effect"} else None,
        )
