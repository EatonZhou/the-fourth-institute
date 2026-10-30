"""Stalled Trigger Paths are closed by an administrator; finished paths expire (decision_71aa039e3211).

ResolveTriggerPath fails the open steps of a path that cannot finish by
itself and records who decided and why; work whose outcome is unknown
refuses it. Housekeeping deletes paths that finished more than 30 days ago,
keeping each Effect's latest finished activation.

:module: lineage.service
:design: decision_71aa039e3211, decision_68215b3a825b
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.society_service import (
    effect_disposition, lineage_effect_script_journal as journal, lineage_failure_notifications,
    trigger_path_storage as storage, trigger_paths,
)
from management_portal.society_service.isa import build_kernel
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_isa_audit_g4 import make_lineage, source
from tests.cases.test_lineage_generic_primitives import ACTOR, WORK_A, primitive_store  # noqa: F401

EFFECT = "effect_aaaaaaaaaaaa"
SOURCE = "event_source_start"


def _lineage() -> str:
    return make_lineage([source(), {"id": EFFECT, "kind": "effect", "config": {"script": "GetLineage"}}])["lineage_id"]


def _stalled(lineage_id: str, event_id: str) -> str:
    """An Event admitted but never settled, with an Effect activation that never finished."""
    event = {"id": event_id, "type": "manual.triggered", "occurred_at": "2026-10-16T02:00:00.000Z", "payload": {}}
    identity = trigger_paths.begin_event(lineage_id, SOURCE, event, cause_kind="instruction", cause_id="inst_stalled")
    journal.begin(lineage_id, "effect:" + event_id, effect_id=EFFECT, trigger_path_id=identity, script_sha="sha",
                  source={"event_id": event_id})
    return identity


def test_a_stalled_path_is_closed_as_failed_with_its_disposition(primitive_store, monkeypatch):
    """管理员把无法自行结束的触发流程按失败关闭

    ResolveTriggerPath 把从未派发的事件步骤和没有结果未知调用的 Effect 激活都记为失败，写入处置人、原因和范围，
    Effect 的执行记录随之清除；流程结束为失败，不再发失败通知；再次调用返回同一结果。
    """
    lineage_id = _lineage()
    identity = _stalled(lineage_id, "event_stalled01")
    notified: list[tuple] = []
    monkeypatch.setattr(lineage_failure_notifications, "notify", lambda *args, **kwargs: notified.append(args))

    def call(note: str):
        return build_kernel().execute(InstructionCall.from_payload({
            "instruction": "ResolveTriggerPath",
            "arguments": {"lineage_id": lineage_id, "trigger_path_id": identity, "note": note},
        }), InstructionContext(caller=ACTOR, entry="web"))

    result = call("10-16 起停住，按失败收尾")
    assert result.status == "committed", result.error
    path = result.result["path"]
    assert path["status"] == "failed"
    assert {step["kind"]: step["status"] for step in path["steps"]} == {"event": "failed", "effect": "failed"}
    for step in path["steps"]:
        disposition = step["result"]["disposition"]
        assert (disposition["actor"], disposition["scope"], disposition["note"]) == (ACTOR, "trigger_path", "10-16 起停住，按失败收尾")
    assert not get_lineage_data_store().effect_work.get(lineage_id, "effect:event_stalled01")
    lineage_failure_notifications.notify_failed_path(identity)
    assert notified == []
    again = call("再次关闭")
    assert again.status == "committed" and again.result["path"]["status"] == "failed"


def test_an_unknown_effect_outcome_refuses_closing_the_path(primitive_store):
    """流程中有结果未知的 Effect 调用时拒绝关闭，也必须写明原因

    拒绝时提示先用 ResolveEffectActivation，流程和步骤保持原状。
    """
    lineage_id = _lineage()
    identity = _stalled(lineage_id, "event_unknown01")
    journal.record_call_started(lineage_id, "effect:event_unknown01", "c1",
                                {"instruction": "GetLineage", "caller": ACTOR, "index": 1, "line": 1})
    with pytest.raises(InstructionError, match="ResolveEffectActivation"):
        effect_disposition.resolve_path(lineage_id, identity, ACTOR, note="关闭")
    with pytest.raises(InstructionError):
        effect_disposition.resolve_path(lineage_id, identity, ACTOR, note="  ")
    path = trigger_paths.get(lineage_id, identity)
    assert path["status"] == "active"
    assert {step["status"] for step in path["steps"]} == {"pending", "running"}


def test_a_resident_step_records_only_a_disposition(primitive_store):
    """Resident 步骤只能记录管理员处置，不能保存 Resident 输出

    Resident 的输出属于 Agent 执行历史；处置是流程证据，可以写入 Resident 步骤。
    """
    at = "2026-10-16T02:00:00.000Z"
    with get_lineage_data_store().database.transaction() as connection:
        identity = storage.begin(connection, lineage_id="lineage_aaaaaaaaaaaa", root_kind="event", occurrence_id="event_r",
                                 root_node_id=SOURCE, cause_kind="instruction", cause_id="inst_r", occurred_at=at,
                                 input={"id": "event_r"})
        step = storage.admit_step(connection, trigger_path_id=identity, node_id=WORK_A, kind="resident",
                                  activation_key="event_r", created_at=at, input_message_id="msg_r")
        with pytest.raises(ValueError):
            storage.transition_step(connection, step, status="failed", at=at, result={"text": "reply"})
        storage.transition_step(connection, step, status="failed", at=at, result={"disposition": {"actor": ACTOR}})
        assert connection.execute("SELECT status FROM trigger_path_steps WHERE step_id=%s", (step,)).fetchone()["status"] == "failed"


def test_finished_paths_expire_after_thirty_days_except_each_effects_latest(primitive_store):
    """已结束的触发流程保留 30 天，到期由后台清理

    清理删除流程及其步骤、连线和执行关联；进行中的流程和每个 Effect 最近一次结束的激活所在流程保留。
    """
    lineage_id = "lineage_aaaaaaaaaaaa"
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    database = get_lineage_data_store().database

    def path(event_id: str, at: datetime, *, effect: bool = False, resident: bool = False, finish: bool = True) -> str:
        when = at.isoformat().replace("+00:00", "Z")
        with database.transaction() as connection:
            identity = storage.begin(connection, lineage_id=lineage_id, root_kind="event", occurrence_id=event_id,
                                     root_node_id=SOURCE, cause_kind="timer", cause_id="timer", occurred_at=when,
                                     input={"id": event_id})
            root = storage.admit_step(connection, trigger_path_id=identity, node_id=SOURCE, kind="event",
                                      activation_key=event_id, created_at=when)
            if not finish:
                return identity
            storage.transition_step(connection, root, status="completed", at=when)
            statuses = {}
            if effect:
                step = storage.admit_step(connection, trigger_path_id=identity, node_id=EFFECT, kind="effect",
                                          activation_key="effect:" + event_id, created_at=when, predecessors=[(root, "wire_a")])
                storage.transition_step(connection, step, status="completed", at=when)
            if resident:
                step = storage.admit_step(connection, trigger_path_id=identity, node_id=WORK_A, kind="resident",
                                          activation_key=event_id, created_at=when, input_message_id="msg_" + event_id,
                                          predecessors=[(root, "wire_b")])
                storage.link_execution(connection, step, "execution_" + event_id)
                storage.transition_step(connection, step, status="completed", at=when)
                statuses["execution_" + event_id] = "completed"
            assert storage.finish(connection, identity, at=when, execution_statuses=statuses) == "completed"
        return identity

    old = now - timedelta(days=40)
    expired = path("event_old_plain", old, resident=True)
    superseded = path("event_old_effect1", old - timedelta(days=1), effect=True)
    latest = path("event_old_effect2", old, effect=True)
    running = path("event_old_active", old, finish=False)
    recent = path("event_recent", now - timedelta(days=5))

    assert trigger_paths.prune_finished(now=now) == 2
    with database.read() as connection:
        remaining = {row["trigger_path_id"] for row in connection.execute(
            "SELECT trigger_path_id FROM trigger_paths WHERE lineage_id=%s", (lineage_id,)).fetchall()}
        assert remaining == {latest, running, recent}
        assert not connection.execute("SELECT 1 FROM trigger_path_steps WHERE trigger_path_id=ANY(%s)",
                                      ([expired, superseded],)).fetchone()
        assert not connection.execute("SELECT 1 FROM trigger_path_execution_links WHERE execution_id=%s",
                                      ("execution_event_old_plain",)).fetchone()
    assert trigger_paths.prune_finished(now=now) == 0
