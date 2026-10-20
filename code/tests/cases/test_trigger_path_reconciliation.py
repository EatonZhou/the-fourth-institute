"""Recover historical input facts without inventing new failures or execution dates."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    family_recovery, lineage_failure_notifications, lineage_program_runtime,
    resident_conversation_stream, trigger_path_storage as storage, trigger_paths,
)
from tests.cases.test_lineage_b3_regressions import complete_input
from tests.cases.canonical_domain_fixture import canonical_projection

LINEAGE = "lineage_111111111111"
RESIDENT = "resident_111111111111"
MESSAGE = "msg_111111111111"
SOURCE = "event_source_111111111111"
AT = "2026-10-02T00:00:00Z"
FINISHED = "2026-10-02T00:56:41Z"
TODAY = "2026-10-15T00:00:00Z"


@pytest.fixture
def recovery(monkeypatch):
    canonical_projection(RESIDENT, lineage_id=LINEAGE)
    monkeypatch.setattr(trigger_paths, "now", lambda: AT)
    event = {"id": "event-one", "type": "time.reached", "occurred_at": AT, "payload": {}}
    path = trigger_paths.begin_event(LINEAGE, SOURCE, event, cause_kind="timer", cause_id=SOURCE)
    with trigger_paths._store().database.transaction() as connection:
        step = storage.admit_step(connection, trigger_path_id=path, node_id=RESIDENT, kind="resident",
                                  activation_key=MESSAGE, input_message_id=MESSAGE, created_at=AT)
    trigger_paths.event_settled(path, SOURCE, event["id"])
    notices, routing_failures = [], []
    source = {}
    monkeypatch.setattr(lineage_program_runtime, "read_runtime", lambda _: {"event_sources": {SOURCE: source}})
    monkeypatch.setattr(lineage_failure_notifications, "notify", lambda *a, **kw: notices.append((a, kw)))
    monkeypatch.setattr(family_recovery, "routing_failed", lambda *a, **kw: routing_failures.append((a, kw)))
    monkeypatch.setattr(trigger_paths, "now", lambda: TODAY)
    return SimpleNamespace(path=path, step=step, notices=notices, source=source, routing_failures=routing_failures)


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_input_receipt_settles_without_execution_history(recovery, status):
    """没有执行历史时按输入回执收尾触发路径，失败只通知一次并带原始错误和时间"""
    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(RESIDENT, MESSAGE, status, updated_at=FINISHED,
                               error={"message": "original script failure"} if status == "failed" else {})
    for _ in range(2):
        trigger_paths.settle_input(LINEAGE, RESIDENT, MESSAGE, status=status, at=TODAY)
        trigger_paths.event_settled(recovery.path, SOURCE, "event-one")
    path = trigger_paths.get(LINEAGE, recovery.path)
    assert (path["status"], path["finished_at"]) == (status, FINISHED)
    assert path["executions"] == []
    assert len(recovery.notices) == (1 if status == "failed" else 0)
    if status == "failed":
        args, kwargs = recovery.notices[0]
        assert args[2]["finished_at"] == FINISHED
        assert "original script failure" in kwargs["message"]
        assert FINISHED in kwargs["message"] and TODAY not in kwargs["message"]
        assert recovery.path in kwargs["message"]


def test_paused_event_reconciliation_retains_failure_without_notifications(recovery):
    """事件源暂停期间收尾的失败保留在路径上，不发通知也不路由失败事件"""
    recovery.source.update(paused=True, paused_at="2026-10-11T00:00:00Z")
    resident_conversation_stream.get_repository().set_input_state(
        RESIDENT, MESSAGE, "failed", updated_at=FINISHED, error={"message": "old failure"})
    trigger_paths.settle_input(LINEAGE, RESIDENT, MESSAGE, status="failed", at=TODAY)
    assert trigger_paths.get(LINEAGE, recovery.path)["status"] == "failed"
    assert recovery.notices == recovery.routing_failures == []
    assert recovery.source["paused"] is True
    recovery.source["paused"] = False
    trigger_paths.event_settled(recovery.path, SOURCE, "event-one")
    assert recovery.notices == recovery.routing_failures == []


def test_completed_input_requires_positive_evidence(recovery):
    """没有终态输入回执时，不能把触发路径的输入记为完成"""
    with pytest.raises(ValueError, match="terminal input receipt"):
        trigger_paths.settle_input(LINEAGE, RESIDENT, MESSAGE, status="completed", at=TODAY)
    assert trigger_paths.get(LINEAGE, recovery.path)["status"] == "active"


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_missing_linked_execution_uses_receipt_and_unknown_stays_pending(recovery, status):
    """关联的执行记录已被清理时按输入回执收尾，回执未知时保持进行中"""
    database = trigger_paths._store().database
    with database.transaction() as connection:
        storage.link_execution(connection, recovery.step, "execution-pruned")
        storage.transition_step(connection, recovery.step, status="completed", at=AT)
    assert trigger_paths.settle(recovery.path) is False
    resident_conversation_stream.get_repository().set_input_state(
        RESIDENT, MESSAGE, status, updated_at=FINISHED)
    assert trigger_paths.settle(recovery.path) is True
    path = trigger_paths.get(LINEAGE, recovery.path)
    assert (path["status"], path["finished_at"]) == (status, FINISHED)


def test_delayed_execution_link_preserves_actual_terminal_time(recovery):
    """执行记录延迟关联时，触发路径的结束时间取实际完成时间"""
    execution_id = complete_input(MESSAGE)
    trigger_paths.link_input(LINEAGE, RESIDENT, MESSAGE, execution_id)
    path = trigger_paths.get(LINEAGE, recovery.path)
    assert path["status"] == "completed"
    assert path["finished_at"] == "2026-10-14T00:00:00Z"


def running_input(message_id):
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution import execution_inputs
    store = get_agent_data_store()
    store.ensure_agent(RESIDENT, actor_ref="sample_admin")
    execution_id = "execution-" + message_id
    store.begin_execution(execution_id=execution_id, agent_id=RESIDENT, actor_ref="sample_admin", provider="codex", started_at=AT)
    execution_inputs.attach(store, execution_id, [{"message_id": message_id, "role": "primary", "accepted_at": AT}])
    return store, execution_id


def test_a_handoff_stays_pending_until_its_execution_is_over(recovery):
    """交接被 Execution 接纳后步骤已完成，但直到这个 Execution 结束都还算待处理"""
    def pending():
        return [item["step_id"] for item in trigger_paths.resident_inputs(LINEAGE, pending=True)]
    assert pending() == [recovery.step]
    store, execution_id = running_input(MESSAGE)
    trigger_paths.link_input(LINEAGE, RESIDENT, MESSAGE, execution_id)
    assert pending() == [recovery.step]
    store.finish_execution(execution_id, status="completed", finished_at=FINISHED)
    assert pending() == []


def test_a_cancelled_run_is_recent_while_its_execution_finished_recently(recovery):
    """Run 早已取消、它接纳的 Execution 最近才结束时，这个 Run 仍在最近窗口里"""
    store, execution_id = running_input(MESSAGE)
    trigger_paths.link_input(LINEAGE, RESIDENT, MESSAGE, execution_id)
    with trigger_paths._store().database.transaction() as connection:
        storage.cancel(connection, recovery.path, at=AT)
    store.finish_execution(execution_id, status="completed", finished_at="2026-10-10T00:00:00Z")

    def recent(since):
        return [item["step_id"] for item in trigger_paths.resident_inputs(
            LINEAGE, finished_since=datetime.fromisoformat(since))]
    assert recent("2026-10-05T00:00:00+00:00") == [recovery.step]
    assert recent("2026-10-12T00:00:00+00:00") == []


def test_pending_and_recent_reads_are_planned_for_their_own_lineage(recovery):
    """待处理交接和最近窗口的读取每次按实际 Lineage 规划，不会被预备成按平均 Lineage 估算的通用计划"""
    with trigger_paths._store().database.read_current() as connection:
        for _ in range(8):
            trigger_paths.resident_inputs(LINEAGE, pending=True)
            trigger_paths.resident_inputs(LINEAGE, finished_since=datetime.fromisoformat("2026-10-05T00:00:00+00:00"))
        prepared = connection.execute("SELECT statement FROM pg_prepared_statements").fetchall()
    assert not [row for row in prepared if "SELECT s.*,p.input_json" in row["statement"]]


@pytest.mark.parametrize("occurred_at", [
    "2026-10-14T17:48:38.401086+00:00",
    "2026-10-14T21:18:06.548Z",
    "2026-10-15T05:18:06.548+08:00",
    "2026-10-14T21:18:06Z",
])
def test_event_redelivery_preserves_original_timestamp_text(occurred_at):
    """Event重放保留原始时间文本及业务身份，拒绝不同payload和身份，不重复创建路径或步骤。

    SQL time formatting must not change an accepted Event on retry."""
    from copy import deepcopy
    from management_common.database_time import instant, iso_time
    from management_common.principal import OperationIdentity

    canonical_projection(RESIDENT, lineage_id=LINEAGE)
    event = {"id": "event-retry", "type": "script.finished", "occurred_at": occurred_at,
             "payload": {"script_run_id": "script-original"},
             "identity": OperationIdentity.for_actor("sample_admin").to_dict()}
    path = trigger_paths.begin_event(LINEAGE, SOURCE, event, cause_kind="external", cause_id=event["id"])
    trigger_paths.event_settled(path, SOURCE, event["id"])
    before = trigger_paths.get(LINEAGE, path)
    for retry_at in (occurred_at, iso_time(instant(occurred_at)), TODAY):
        retry = deepcopy(event)
        retry["occurred_at"] = retry_at
        assert trigger_paths.begin_event(LINEAGE, SOURCE, retry, cause_kind="external", cause_id=event["id"]) == path
        assert retry["occurred_at"] == retry_at  # Caller-owned envelope is untouched.
    after = trigger_paths.get(LINEAGE, path)
    assert after == before
    assert after["input"] == event
    assert len(after["steps"]) == 1
    assert after["status"] == "completed"
    for field, value in (("payload", {"script_run_id": "different-run"}),
                         ("identity", OperationIdentity.for_actor("different-user").to_dict())):
        changed = {**event, field: value}
        with pytest.raises(ValueError, match="Conflicting delivery.*input_json"):
            trigger_paths.begin_event(LINEAGE, SOURCE, changed, cause_kind="external", cause_id=event["id"])
    assert trigger_paths.get(LINEAGE, path) == before


def test_observation_retry_uses_accepted_fact_and_never_repeats_terminal_work(monkeypatch):
    """观察事件恢复使用已接收事实与原身份；已终结路径不重跑业务。"""
    from contextlib import nullcontext
    from management_common.principal import OperationIdentity
    from management_portal.society_service import lineage_event_reconciliation as observations
    from management_portal.society_service import lineage_instruction_context, lineage_primitives

    canonical_projection(RESIDENT, lineage_id=LINEAGE)
    original = lineage_primitives.make_event(
        "resident.queue_pressure_exceeded", subject_resident_id=RESIDENT,
        occurred_at=AT, payload={"queue_count": 3, "message_id": MESSAGE},
        correlation="accepted-edge", identity=OperationIdentity.for_actor("sample_admin").to_dict())
    path = trigger_paths.begin_event(LINEAGE, SOURCE, original.to_dict(), cause_kind="external", cause_id="edge")
    candidate = lineage_primitives.normalize_event({**original.to_dict(), "occurred_at": TODAY,
                                                    "payload": {"queue_count": 9, "message_id": "new-message"}})
    item = {"event": candidate, "event_source_id": SOURCE, "cursor": "edge", "source_contract": "contract"}
    scopes, calls = [], []
    def scope(event, actor, **kwargs):
        scopes.append((event.to_dict(), actor, kwargs))
        return nullcontext()
    def dispatch(lineage, source, event, actor, **kwargs):
        calls.append((event.to_dict(), actor, kwargs))
        return {"event": event.to_dict()}
    monkeypatch.setattr(lineage_instruction_context, "event_scope", scope)
    observations._dispatch_observation(LINEAGE, item, "different-observer", dispatch)
    assert calls[0][0] == scopes[0][0] == original.to_dict()
    assert calls[0][1] == scopes[0][1] == "sample_admin"
    trigger_paths.event_settled(path, SOURCE, original.id)
    before = trigger_paths.get(LINEAGE, path)
    result = observations._dispatch_observation(LINEAGE, item, "different-observer", dispatch)
    assert result["duplicate"] and result["trigger_path_id"] == path
    assert result["event"] == original.to_dict()
    assert len(calls) == len(scopes) == 1
    assert trigger_paths.get(LINEAGE, path) == before


def test_rebuilt_observation_baselines_get_distinct_stable_occurrence_keys():
    """基线重建具有独立稳定身份，旧待确认发生身份在确认前保持。"""
    canonical_projection(RESIDENT, lineage_id=LINEAGE)
    def observe(value):
        return lineage_program_runtime.observe_event_source_value(
            LINEAGE, SOURCE, locator="queue", signature=value, value=value, subject=RESIDENT)
    first = observe("false")
    assert observe("true")["occurrence_key"] == first["occurrence_key"]
    lineage_program_runtime.fence_event_source_contract_preserving_fire_budget(LINEAGE, SOURCE)
    second = observe("false")
    assert second["occurrence_key"] != first["occurrence_key"]
    assert observe("true")["occurrence_key"] == second["occurrence_key"]
    # Upgrading a legacy baseline cannot rename an already accepted occurrence.
    with lineage_program_runtime._runtime_transaction(LINEAGE) as runtime:
        runtime["event_sources"][SOURCE]["subjects"][RESIDENT].pop("state_epoch")
    assert observe("true")["occurrence_key"] == "1"
    assert lineage_program_runtime.commit_event_source_value(
        LINEAGE, SOURCE, locator="queue", previous_signature="false", signature="true", value="true", subject=RESIDENT)
    following = observe("false")
    assert following["generation"] == 1
    assert following["occurrence_key"] != "2"
    assert observe("false")["occurrence_key"] == following["occurrence_key"]
