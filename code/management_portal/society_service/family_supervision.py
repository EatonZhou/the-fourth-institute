"""Durable supervision of Family work, separate from execution recovery.

The original request is authoritative: a reassignment changes what we observe,
and a retry is progress, not completion. Only the steward decides how to repair;
this module checks outcomes, schedules follow-up and escalates to the familizer.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timezone

from . import family_facade, family_recovery, resident_conversation_stream
from .state_store import get_lineage_data_store

MAX_REPAIR_ROUNDS = 3
REF = "duty/family-recovery"
TERMINAL = {"resolved", "cancelled", "escalated"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(value):
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _input(resident_id, message_id):
    try:
        return resident_conversation_stream.get_repository().input_state(resident_id, message_id)
    except KeyError:
        return {"state": "missing"}


def _request(facade, resident_id, message_id):
    """Keep the original facade reference even after its old copy was reassigned."""
    repository = resident_conversation_stream.get_repository()
    if resident_id == facade["resident_id"]:
        return message_id
    ref = repository.message_business_ref(resident_id, message_id) or ""
    source = family_facade.parse_relay_mark(ref.removeprefix("family-route/"))
    if source and source[0] == facade["resident_id"] and repository.message(*source):
        return source[1]
    # Nested Family relays retain a chain of original requests.
    for source in family_facade.relay_sources_of(resident_id, message_id):
        if source[0] == facade["resident_id"]:
            return source[1]
    return ""


def _candidate(facade, resident_id, message_id):
    root = _request(facade, resident_id, message_id)
    return {"key": "request:" + root if root else f"input:{resident_id}:{message_id}",
            "resident_id": resident_id, "message_id": message_id, "request_message_id": root}


def _event_input(lineage_id, event):
    from . import trigger_paths
    seen = set()
    for _ in range(8):
        payload = event.get("payload") or {}
        ref = str(payload.get("input_business_ref") or "")
        if ref.startswith("family-route/"):
            source = family_facade.parse_relay_mark(ref.removeprefix("family-route/"))
            if source:
                return source
        if payload.get("resident_id") and payload.get("message_id"):
            return str(payload["resident_id"]), str(payload["message_id"])
        source = str(payload.get("source_event_id") or "")
        if not source or source in seen:
            break
        seen.add(source)
        event = trigger_paths.event_input(lineage_id, source) or {}
    return None


def _observe(facade, incident):
    from . import resident_record_domain
    root = incident.get("request_message_id")
    if root:
        if _input(facade["resident_id"], root).get("state") == "cancelled":
            return "cancelled", {"state": "cancelled", "resident_id": facade["resident_id"], "message_id": root}
        state = family_recovery.request_status(facade, root)
    else:
        state = _input(incident["resident_id"], incident["message_id"])
    state = {**state, "resident_id": state.get("resident_id") or incident["resident_id"],
             "message_id": state.get("member_message_id") or incident["message_id"]}
    status = state.get("state")
    if status == "completed":
        return "resolved", state
    if status == "cancelled":
        return "cancelled", state
    record = resident_record_domain.find_resident(state["resident_id"])
    if record and resident_record_domain.resident_intake_state(record) != "open":
        from . import resident_execution_work
        held = status == "running" and resident_execution_work.is_intake_held(resident_execution_work.read(record))
        if status in {"queued", "failed"} or held:
            from . import resident_record_domain
            if resident_record_domain.lifecycle_operations().pending(record["resident_id"]):
                state.update(intake_state="closed", blocked_reason="session_restart")
                return "waiting", state
            state.update(intake_state="closed", blocked_reason="intake_closed")
            return "open", state
    if status in {"queued", "running", "routing"}:
        return "waiting", state
    return "open", state


def _blocked_queue_candidates(facade, resident_ids):
    """A closed member's live queue is work, unlike a historical failed copy.

    One representative input per member is enough to request reopening. Observe
    its current relay holder before registering it, including nested Families.
    """
    from .resident_catalog import get_catalog
    from . import resident_record_domain
    closed = [r["resident_id"] for r in get_catalog().get_many(sorted(resident_ids))
              if not r.get("deleted_at") and resident_record_domain.resident_intake_state(r) != "open"]
    for rid, queue in resident_conversation_stream.get_repository().queues(closed).items():
        for item in queue["items"]:
            candidate = _candidate(facade, rid, item["message_id"])
            _, observed = _observe(facade, candidate)
            if observed.get("blocked_reason") == "intake_closed":
                yield candidate
                break


def _escalate(lineage_id, family, incidents, reason):
    from .lineage_human_notifications import create_notification
    creator = str(family.get("human_familizer_id") or "")
    for incident in incidents:
        incident["escalation_reason"] = reason
        if not creator:
            incident["status"] = "missing_creator"
            continue
        notification_id = "family-recovery:" + _id(family["facade"] + ":" + incident["key"])
        observed = incident.get("observed") or {}
        create_notification(lineage_id, recipients=[creator], created_by=family["facade"], severity="error",
            notification_id=notification_id, title=f"Family「{family['title']}」需要恢复协助",
            message=f"{reason}。原请求：{incident.get('request_message_id') or incident['message_id']}；"
                    f"当前接手者：{observed.get('resident_id') or incident['resident_id']}；"
                    f"状态：{observed.get('state') or 'unknown'}；错误：{str(observed.get('error') or '')[:1500]}。"
                    "任务和故障记录已保留，请处理阻塞后恢复原任务。")
        incident.update(status="escalated", notification_id=notification_id)
        from .family_owners import message_creator
        from . import resident_record_domain
        message_creator(resident_record_domain.find_resident(family["facade"]), identity=notification_id,
                        text=f"Family「{family['title']}」需要恢复协助：{reason}。原请求 {incident.get('request_message_id') or incident['message_id']} 已保留。")


def check(lineage_id, family_id, actor, *, event=None, escalate_reason=""):
    from .templates.familize import read_family
    from . import lineage_domain
    from . import resident_record_domain
    lineage, block = read_family(lineage_id, family_id, actor)
    family = block["family"]
    if actor not in {family["facade"], family["steward"]}:
        lineage_domain._ensure_manager(lineage, actor)
    facade = resident_record_domain.find_resident(family["facade"])
    if facade.get("deleted_at") or resident_record_domain.resident_intake_state(facade) != "open":
        # Pausing the Family is explicit; closing one member is a repairable fault.
        ledger = get_lineage_data_store().family_relays.get(lineage_id, family["facade"]).get("supervision") or {}
        return {"family_id": block["id"], "paused": True,
                "incidents": list(ledger.get("incidents", {}).values()), "notice": ledger.get("notice") or {}}
    # Recovery can race the independent intake Event. Restore its recipient
    # before deciding delivery is impossible, without scanning role accounts.
    from .family_quota import restore_role_intakes
    restore_role_intakes([resident_record_domain.find_resident(family["steward"])])
    roles = set(family["members"]) | {family[r] for r in ("router", "steward", "facade")}
    event = event or {}
    event_type = str(event.get("type") or "")
    candidates = []
    if event_type in {"resident.work_failed", "effect.failed", "event.delivery_failed"}:
        subject = _event_input(lineage_id, event)
        if subject and subject[0] in roles:
            candidates.append(_candidate(facade, *subject))
    # Arrival events give prompt detection; the timer also catches a member
    # closed after arrival or a missed event. Never scan historical failures.
    candidates.extend(_blocked_queue_candidates(facade, roles - {family["facade"]}))
    repository = get_lineage_data_store().family_relays
    now = _now()
    pending = None
    with repository.transaction(lineage_id, family["facade"]) as relay:
        ledger = relay.setdefault("supervision", {"incidents": {}})
        incidents = ledger["incidents"]
        for candidate in candidates:
            # A failed repair round belongs to its existing incidents. Do not
            # turn the reminder itself into a new user task (or notify twice).
            if (candidate["resident_id"] == family["steward"] and
                    candidate["message_id"] == ledger.get("notice", {}).get("message_id")):
                continue
            incidents.setdefault(candidate["key"], {**candidate, "status": "open", "attempts": 0, "created_at": now})
        for incident in incidents.values():
            if incident["status"] in {"resolved", "cancelled"}:
                continue
            status, observed = _observe(facade, incident)
            incident.update(observed=observed, checked_at=now)
            if incident["status"] not in {"escalated", "missing_creator"} or status in {"resolved", "cancelled"}:
                incident["status"] = status
        missing_creator = [i for i in incidents.values() if i["status"] == "missing_creator"]
        for incident in missing_creator:
            _escalate(lineage_id, family, [incident], incident["escalation_reason"])
        active = [i for i in incidents.values() if i["status"] not in TERMINAL]
        steward_failure = any(i["resident_id"] == family["steward"] and
                              i.get("observed", {}).get("state") in {"failed", "missing"} for i in active)
        if active and (steward_failure or escalate_reason):
            _escalate(lineage_id, family, active, escalate_reason or "steward 运行失败，无法继续监督恢复")
        notice = ledger.get("notice") or {}
        notice_state = _input(family["steward"], notice["message_id"]).get("state") if notice else ""
        if active and notice_state in {"failed", "cancelled"}:
            _escalate(lineage_id, family, [i for i in active if i["status"] not in TERMINAL], "steward 的恢复轮次失败或已停止")
        open_incidents = [i for i in active if i["status"] == "open"]
        repair_pending = bool(notice and (not notice.get("delivered") or notice_state in {"queued", "running"}))
        exhausted = [i for i in open_incidents if i["attempts"] >= MAX_REPAIR_ROUNDS and not repair_pending]
        if exhausted:
            _escalate(lineage_id, family, exhausted, f"steward 已跟进 {MAX_REPAIR_ROUNDS} 轮，原任务仍未恢复完成")
        open_incidents = [i for i in open_incidents if i["status"] == "open"]
        # One outstanding repair message per Family, including a durable unsent outbox.
        if notice and not notice.get("delivered"):
            if any(incidents[key]["status"] == "open" for key in notice["incident_keys"]):
                pending = deepcopy(notice)
            else:
                notice.update(delivered=True, withdrawn=True)
        elif open_incidents and notice_state not in {"queued", "running"}:
            from . import resident_record_domain
            steward = resident_record_domain.find_resident(family["steward"])
            if steward and resident_record_domain.resident_intake_state(steward) != "open" and resident_record_domain.lifecycle_operations().pending(family["steward"]):
                pass  # Its lifecycle operation will restore intake; do not bypass it.
            elif not steward or resident_record_domain.resident_intake_state(steward) != "open":
                _escalate(lineage_id, family, open_incidents, "steward 不可接单，无法继续监督恢复")
            else:
                for incident in open_incidents:
                    incident["attempts"] += 1
                identity = ";".join(f"{i['key']}:{i['attempts']}" for i in sorted(open_incidents, key=lambda i: i['key']))
                pending = {"message_id": "msg_" + _id(family["facade"] + identity),
                           "incident_keys": [i["key"] for i in open_incidents], "delivered": False}
                ledger["notice"] = pending
        closed = sorted((i for i in incidents.values() if i["status"] in {"resolved", "cancelled"}), key=lambda i: i["created_at"])
        for incident in closed[:-500]:
            del incidents[incident["key"]]
    if pending:
        try:
            family_facade.tell_steward(family["facade"], family["steward"], family["facade"], "",
                                      message_id=pending["message_id"], business_ref=REF, task_trigger="recovery",
                                      task_facts={"family_id": block["id"], "lineage_id": lineage_id,
                                                  "incident_keys": pending["incident_keys"]})
        except Exception as exc:
            with repository.transaction(lineage_id, family["facade"]) as relay:
                ledger = relay["supervision"]
                _escalate(lineage_id, family, [ledger["incidents"][key] for key in pending["incident_keys"]
                          if ledger["incidents"][key]["status"] not in TERMINAL], f"无法通知 steward：{str(exc)[:500]}")
                ledger["notice"]["error"] = str(exc)[:1000]
                ledger["notice"]["delivered"] = True
        else:
            with repository.transaction(lineage_id, family["facade"]) as relay:
                if relay["supervision"].get("notice", {}).get("message_id") == pending["message_id"]:
                    relay["supervision"]["notice"]["delivered"] = True
    ledger = repository.get(lineage_id, family["facade"])["supervision"]
    return {"family_id": block["id"], "incidents": list(ledger["incidents"].values()), "notice": ledger.get("notice") or {}}
