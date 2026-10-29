"""Recovery supervision follows current task outcomes, not stale failure events."""
from types import SimpleNamespace
import uuid

import pytest

from management_portal.society_service import family_facade, family_supervision as supervision
from management_portal.society_service import (
    resident_conversation_stream,
    resident_protocol,
    resident_lifecycle_domain,
    resident_record_domain,
    resident_input_queue_domain,
)
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.society_service import lineage_aggregate
from management_portal.society_service import family_familize
from tests.cases.isa_test_support import ok
from tests.cases.test_family_familize import lineage, charters, future_creation_store, resident, graph  # noqa: F401


@pytest.fixture
def team(lineage, charters, monkeypatch):
    member = resident(lineage, "Worker")
    other = resident(lineage, "Replacement")
    created = ok("Familize", {"lineage_id": lineage, "resident_ids": [member, other], "title": "Recovery"})
    facade, steward = created["facade_resident_id"], created["steward_resident_id"]
    repo = resident_conversation_stream.get_repository()
    sent, alerts = [], []
    def state(rid, mid, status):
        try:
            previous = repo.input_state(rid, mid)["state"]
        except KeyError:
            previous = ""
        if previous in {"failed", "cancelled", "completed"} and previous != status:
            repo.reopen_input(rid, mid)
        repo.set_input_state(rid, mid, status)
    def message(rid=member, status="failed", ref="", *, enqueue=False):
        mid = "msg_" + uuid.uuid4().hex
        item = resident_input_queue_domain.build_message("user", "original task", "sample_admin", message_id=mid)
        if ref:
            item["business_ref"] = ref
        if enqueue:
            from management_portal.society_service import resident_domain
            resident_domain.deliver_resident_message_content(rid, "sample_admin", "original task", message_id=mid, business_ref=ref)
        else:
            resident_lifecycle_domain.append_conversation_message(rid, item)
        state(rid, mid, status)
        return mid
    def deliver(facade_id, steward_id, actor, text, **options):
        sent.append({"facade": facade_id, "resident_id": steward_id, "actor": actor, "text": text, **options})
        mid = options["message_id"]
        item = resident_input_queue_domain.build_message("user", text, facade_id, message_id=mid)
        item["business_ref"] = options["business_ref"]
        repo.accept_user_message(steward_id, item)
        state(steward_id, mid, "queued")
    monkeypatch.setattr(family_facade, "tell_steward", deliver)
    from management_portal.society_service import lineage_human_notifications
    monkeypatch.setattr(lineage_human_notifications, "create_notification", lambda lid, **kw: alerts.append(kw))
    def check(mid=None, rid=member, event_type="resident.work_failed", **kwargs):
        event = {"type": event_type, "payload": {"resident_id": rid, "message_id": mid}} if mid else {"type": "time.reached"}
        return supervision.check(lineage, created["family_id"], steward, event=event, **kwargs)
    def intake(rid, mode):
        resident_protocol.change_resident_state(rid, "sample_admin", state_domain="intake", transition=mode)
    for rid in (facade, member, other, steward):
        intake(rid, "open")
    return SimpleNamespace(lineage=lineage, family=created, facade=facade, steward=steward,
                           member=member, other=other, sent=sent, alerts=alerts,
                           state=state, message=message, check=check, intake=intake, repo=repo)


def test_repair_rounds_recheck_original_and_escalate_only_after_third_finishes(team):
    """第三轮修复完成后原请求仍失败才升级通知人类"""
    mid = team.message()
    for attempt in range(1, 4):
        result = team.check(mid)
        assert result["incidents"][0]["attempts"] == attempt
        assert len(team.sent) == attempt
        team.check(mid)  # Duplicate failed event and periodic checks cannot start another round.
        team.check()
        assert len(team.sent) == attempt and not team.alerts
        team.state(team.steward, team.sent[-1]["message_id"], "completed")
    result = team.check()
    assert result["incidents"][0]["status"] == "escalated"
    assert len(team.sent) == 3 and len(team.alerts) == 1
    assert team.alerts[0]["created_by"] == team.facade
    assert team.alerts[0]["recipients"] == ["sample_admin"]
    team.check(mid)
    assert len(team.alerts) == 1
    team.state(team.member, mid, "completed")
    assert team.check()["incidents"][0]["status"] == "resolved"


def test_delayed_failure_observes_completion_and_never_replays_historical_failed_inputs(team):
    """延迟失败事件读取当前完成状态且不重放历史失败输入"""
    old = team.message()
    mid = team.message(status="completed")
    result = team.check(mid)
    assert result["incidents"][0]["status"] == "resolved"
    assert not team.sent
    assert len(team.check()["incidents"]) == 1
    assert team.repo.input_state(team.member, old)["state"] == "failed"


@pytest.mark.parametrize("status", ["queued", "running", "completed", "cancelled"])
def test_original_progress_does_not_mean_another_repair_round(team, status):
    """原请求已有进展时等待结果且不启动新修复轮次"""
    mid = team.message()
    team.check(mid)
    team.state(team.member, mid, status)
    team.state(team.steward, team.sent[0]["message_id"], "completed")
    result = team.check()
    expected = {"completed": "resolved", "cancelled": "cancelled"}.get(status, "waiting")
    assert result["incidents"][0]["status"] == expected
    assert len(team.sent) == 1 and not team.alerts


def test_closed_member_requests_steward_repair_and_preserves_original_input(team):
    """关闭成员不免除修复责任，恢复后继续监督原请求"""
    mid = team.message()
    team.intake(team.member, "close")
    incident = team.check(mid)["incidents"][0]
    assert incident["status"] == "open"
    assert incident["observed"]["blocked_reason"] == "intake_closed"
    assert len(team.sent) == 1
    team.intake(team.member, "open")
    assert team.check()["incidents"][0]["status"] == "open"
    assert len(team.sent) == 1


def test_reassignment_follows_current_holder_even_when_old_copy_fails(team):
    """旧副本失败时监督跟随请求当前承接成员"""
    root = team.message(team.facade, "queued")
    old = team.message(ref=family_facade.relay_mark(team.facade, root))
    replacement = team.message(team.other, "running")
    family_facade.complete_relay((team.facade, root), team.other, replacement)
    result = team.check(old)
    incident = result["incidents"][0]
    assert incident["request_message_id"] == root
    assert incident["status"] == "waiting" and incident["observed"]["resident_id"] == team.other
    team.state(team.other, replacement, "completed")
    assert team.check(old)["incidents"][0]["status"] == "resolved"
    assert not team.sent


@pytest.mark.parametrize("cause", ["failed", "cancelled", "explicit", "delivery"])
def test_steward_unavailable_escalates_with_facade_identity(team, monkeypatch, cause):
    """steward 不可用时以门面身份通知人类一次"""
    mid = team.message()
    if cause == "delivery":
        def fail(*args, **kwargs):
            raise RuntimeError("delivery unavailable")
        monkeypatch.setattr(family_facade, "tell_steward", fail)
    team.check(mid)
    if cause in {"failed", "cancelled"}:
        team.state(team.steward, team.sent[0]["message_id"], cause)
        team.check()
    if cause == "explicit":
        team.check(escalate_reason="上游账号需要创立者恢复")
    assert len(team.alerts) == 1
    assert team.alerts[0]["created_by"] == team.facade and team.alerts[0]["recipients"] == ["sample_admin"]
    team.check()
    assert len(team.alerts) == 1


@pytest.mark.parametrize("arrival", [True, False])
def test_live_closed_queue_discovered_without_failure_and_deduplicated(team, arrival):
    team.intake(team.member, "close")
    mid = team.message(status="queued", enqueue=True)
    team.message(status="queued", enqueue=True)
    result = team.check(mid, event_type="resident.message_received") if arrival else team.check()
    assert len(result["incidents"]) == len(team.sent) == 1
    assert result["incidents"][0]["message_id"] == mid
    assert team.repo.input_state(team.member, mid)["state"] == "queued"
    team.check()
    assert len(team.sent) == 1
    # Steward reopens intake; the existing queued request continues, no requeue.
    team.intake(team.member, "open")
    assert team.check()["incidents"][0]["status"] == "waiting"
    team.state(team.member, mid, "completed")
    assert team.check()["incidents"][0]["status"] == "resolved"
    assert not team.alerts


def test_closed_cancelled_or_admitted_running_input_is_not_revived(team):
    team.intake(team.member, "close")
    cancelled = team.message(status="cancelled")
    assert team.check(cancelled)["incidents"][0]["status"] == "cancelled"
    running = team.message(status="running")
    result = team.check(running)
    assert next(i for i in result["incidents"] if i["message_id"] == running)["status"] == "waiting"
    assert not team.sent


def test_closed_steward_is_reopened_before_repair_delivery(team):
    team.intake(team.steward, "close")
    team.intake(team.member, "close")
    mid = team.message(status="queued", enqueue=True)
    result = team.check(mid, event_type="resident.message_received")
    from management_portal.society_service.resident_record_domain import find_resident as _find_resident
    assert _find_resident(team.steward)["intake_open"] is True
    assert result["incidents"][0]["status"] == "open" and len(team.sent) == 1
    assert not team.alerts


def test_cancelled_facade_request_does_not_revive_its_closed_member_copy(team):
    root = team.message(team.facade, "cancelled")
    team.intake(team.member, "close")
    copy = team.message(status="queued", ref="family-route/" + family_facade.relay_mark(team.facade, root), enqueue=True)
    family_facade.complete_relay((team.facade, root), team.member, copy)
    assert not team.check()["incidents"]
    assert not team.sent


def test_family_pause_suppresses_repairs_until_facade_reopens(team):
    team.intake(team.facade, "close")
    team.intake(team.steward, "close")
    team.intake(team.member, "close")
    team.message(status="queued", enqueue=True)
    assert team.check()["paused"] is True
    assert not team.sent
    from management_portal.society_service.resident_record_domain import find_resident as _find_resident
    assert _find_resident(team.steward)["intake_open"] is False
    team.intake(team.facade, "open")
    assert team.check()["incidents"][0]["status"] == "open"
    assert len(team.sent) == 1


def test_unrecoverable_steward_intake_escalates_once(team, monkeypatch):
    from management_portal.society_service import family_quota
    team.intake(team.steward, "close")
    monkeypatch.setattr(family_quota, "restore_role_intakes", lambda records: records)
    team.check(team.message())
    team.check()
    assert len(team.alerts) == 1 and not team.sent


def test_session_migration_intake_hold_is_not_overridden_or_escalated(team, monkeypatch):
    team.intake(team.steward, "close")
    team.intake(team.member, "close")
    mid = team.message(status="queued", enqueue=True)
    pending = {team.member, team.steward}
    monkeypatch.setattr(resident_record_domain, "lifecycle_operations", lambda: SimpleNamespace(
        pending=lambda rid: {"status": "waiting"} if rid in pending else None))
    assert not team.check()["incidents"]
    pending.remove(team.member)
    assert team.check()["incidents"][0]["message_id"] == mid
    from management_portal.society_service.resident_record_domain import find_resident as _find_resident
    assert _find_resident(team.steward)["intake_open"] is False
    assert not team.sent and not team.alerts
    pending.clear()
    team.check()
    assert _find_resident(team.steward)["intake_open"] is True
    assert len(team.sent) == 1


def test_unsent_notice_is_durable_but_withdrawn_after_task_recovers(team):
    """未送达修复通知持久重试且任务恢复后撤回"""
    mid = team.message()
    first = team.check(mid)
    store = get_lineage_data_store().family_relays
    with store.transaction(team.lineage, team.facade) as data:
        data["supervision"]["notice"]["delivered"] = False
    team.check()
    assert team.sent[-1]["message_id"] == first["notice"]["message_id"]
    with store.transaction(team.lineage, team.facade) as data:
        data["supervision"]["notice"]["delivered"] = False
    team.state(team.member, mid, "completed")
    team.check()
    assert len(team.sent) == 2


def test_generated_supervision_graph_compiles_and_uses_own_family_context(team):
    """Family 监督图可编译且内部通知使用本 Family 门面"""
    diagnostics = ok("GetLineageDiagnostics", {"lineage_id": team.lineage})["diagnostics"]
    assert not [d for d in diagnostics if d["level"] == "error"]
    view = graph(team.lineage)
    check = next(c for c in view["program"]["components"] if c["kind"] == "effect" and c["config"]["script"].startswith("CheckFamilyRecovery"))
    from management_portal.society_service.lineage_program_effect_execution import _family_note_context
    from management_portal.hatg.contracts import InstructionContext
    context = InstructionContext(caller=team.member, entry="lineage")
    args = {"lineage_id": team.lineage, "family_id": team.family["family_id"]}
    assert _family_note_context(view, check["id"], "CheckFamilyRecovery", args, context, lineage_id=team.lineage).caller == team.facade


def test_trigger_failure_resolves_original_event_and_ignores_delayed_completed_input(team):
    """触发失败追溯原事件且已完成输入不再修复"""
    from management_portal.society_service import trigger_paths
    root = team.message(team.facade, "completed")
    source = {"id": "event_" + uuid.uuid4().hex[:16], "type": "resident.message_received",
              "occurred_at": "2026-10-14T17:28:47Z", "payload": {"resident_id": team.facade, "message_id": root}}
    trigger_paths.begin_event(team.lineage, "event_source_original", source, cause_kind="input", cause_id=root)
    failed = {"id": "event_failure", "type": "effect.failed", "payload": {"source_event_id": source["id"]}}
    result = supervision.check(team.lineage, team.family["family_id"], team.steward, event=failed)
    assert result["incidents"][0]["status"] == "resolved" and not team.sent


def test_missing_creator_keeps_escalation_pending_without_another_ai_round(team, monkeypatch):
    """缺少人类创建者时保持待升级且不追加修复轮次"""
    original = family_familize.read_family
    creator = [""]
    def read(*args):
        lineage, block = original(*args)
        block["family"]["human_familizer_id"] = creator[0]
        return lineage, block
    monkeypatch.setattr(family_familize, "read_family", read)
    mid = team.message()
    team.check(mid)
    result = team.check(escalate_reason="需要创立者提供账号")
    assert result["incidents"][0]["status"] == "missing_creator"
    team.state(team.steward, team.sent[0]["message_id"], "completed")
    team.check()
    assert len(team.sent) == 1 and not team.alerts
    creator[0] = "sample_admin"
    assert team.check()["incidents"][0]["status"] == "escalated"
    assert team.alerts[0]["recipients"] == ["sample_admin"]


def test_steward_failure_event_escalates_without_sending_to_failed_steward(team):
    """steward 自身失败直接升级且不向其发送修复请求"""
    mid = team.message(team.steward)
    assert team.check(mid, rid=team.steward)["incidents"][0]["status"] == "escalated"
    assert len(team.alerts) == 1 and not team.sent


def test_role_availability_has_independent_effect_and_respects_family_pause(team, monkeypatch):
    """接单 Effect 不查询额度或迁移，门面暂停仍优先。"""
    from management_portal.society_service import family_quota
    checked = []
    restore = family_quota.restore_role_intakes
    service = family_quota.execution_port.service
    monkeypatch.setattr(family_quota, "restore_role_intakes", lambda records: checked.extend(r["resident_id"] for r in records) or restore(records))
    monkeypatch.setattr(family_quota.execution_port, "service", lambda: pytest.fail("intake effect must not query accounts"))
    args = {"lineage_id": team.lineage, "family_id": team.family["family_id"]}
    result = ok("CheckFamilyRoleAvailability", args, actor=team.steward)
    assert result["family_id"] == team.family["family_id"]
    assert set(checked) == {team.family["router_resident_id"], team.steward}
    checked.clear()
    team.check()
    checked.clear()  # Recovery may restore its recipient's intake, without quota IO.
    team.intake(team.facade, "close")
    assert ok("CheckFamilyRoleAvailability", args, actor=team.steward)["paused"]
    assert not checked
    monkeypatch.setattr(family_quota.execution_port, "service", service)
    view = graph(team.lineage)
    block = next(b for b in view["function_blocks"] if b["id"] == team.family["family_id"])
    keys = {e["key"]: e["id"] for e in block["elements"]}
    components = {c["id"]: c for c in view["program"]["components"]}
    assert "role_account_quota" not in keys and "check_role_quota" not in keys
    targets = {w["target"] for w in view["program"]["wires"] if w["kind"] == "watch" and w["source"] == keys["role_intake"]}
    assert targets == {team.family["router_resident_id"], team.steward}
    routes = {w["source"] for w in view["program"]["wires"] if w["kind"] == "route" and w["target"] == keys["role_availability"]}
    assert routes == {keys[k] for k in ("role_intake", "recovery_timer")}


def test_supervision_isa_checks_management_for_non_role_callers(team, monkeypatch):
    """监督 ISA 对非内部角色调用者检查管理权限"""
    from tests.cases.isa_test_support import run
    result = ok("CheckFamilyRecovery", {"lineage_id": team.lineage, "family_id": team.family["family_id"]}, actor=team.steward)
    assert result["family_id"] == team.family["family_id"]
    outsider = resident(team.lineage, "Outsider")
    checked = []
    def require_owner(lineage, actor):
        checked.append(actor)
        raise PermissionError("no management permission")
    monkeypatch.setattr(lineage_aggregate, "ensure_manager", require_owner)
    denied = run("CheckFamilyRecovery", {"lineage_id": team.lineage, "family_id": team.family["family_id"]}, actor=outsider)
    assert denied.error and checked == [outsider]


def test_failed_repair_event_escalates_original_once_and_not_after_recovery(team):
    """恢复轮次失败按原任务升级，原任务已完成则不重复通知。"""
    mid = team.message()
    team.check(mid)
    notice = team.sent[0]["message_id"]
    team.state(team.steward, notice, "failed")
    result = team.check(notice, rid=team.steward)
    assert len(result["incidents"]) == 1 and len(team.alerts) == 1
    team.state(team.member, mid, "completed")
    assert team.check(notice, rid=team.steward)["incidents"][0]["status"] == "resolved"
    assert len(team.alerts) == 1
