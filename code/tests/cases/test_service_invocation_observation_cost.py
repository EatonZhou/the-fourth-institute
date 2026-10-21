"""
:module: lineage.service
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from management_portal.society_service import service_invocation_domain as domain
from management_portal.society_service.service_invocation_store import ServiceInvocationStore
from management_portal.service_invocation_contract import ServiceInvocationConflict, batch_manifest
from tests.cases.test_shared_search_invocation import job, runtime


def seeded_plan(runtime, states, selected=None):
    selected = selected or job()
    runtime.owner.prepare_batches(job=selected, surface="knowledge",
        batches=batch_manifest(selected, [f"input-{index}" for index in range(len(states))]))
    store = domain._store()
    with store.database.transaction() as connection:
        key = store.key(selected, "knowledge")
        record = store._read(connection, key)
        for batch, state in zip(record["batches"], states):
            batch["status"] = state
        store._aggregate(record)
        store._write(connection, key, record)
    return record


def count_operations(monkeypatch):
    counts = {"read": 0, "write": 0, "project": 0}
    for name in ("read", "project"):
        original = getattr(ServiceInvocationStore, "_" + name)

        def counted(self, *args, _name=name, _original=original):
            counts[_name] += 1
            return _original(self, *args)

        monkeypatch.setattr(ServiceInvocationStore, "_" + name, counted)
    write = ServiceInvocationStore._write

    def counted_write(*args):
        counts["write"] += 1
        return write(*args)

    monkeypatch.setattr(ServiceInvocationStore, "_write", staticmethod(counted_write))
    return counts


@pytest.mark.parametrize("batch_count", [3, 96])
def test_terminal_parent_replay_has_constant_plan_operations_and_no_execution_reads(runtime, monkeypatch, batch_count):
    """终态父计划重放操作次数不随批次增长且不读取执行输出"""
    states = ["completed", "failed", "cancelled"] * (batch_count // 3)
    seeded_plan(runtime, states)
    counts = count_operations(monkeypatch)
    status = Mock(side_effect=AssertionError("terminal parent must not fetch batch outputs"))
    monkeypatch.setattr(domain.resident_protocol, "get_resident_input_state", status)
    for _ in range(3):
        result = runtime.owner.get_invocation(job=job(), surface="knowledge")
        assert result["input_state"] == {}
        assert [batch["status"] for batch in result["receipt"]["batches"]] == states
    assert counts == {"read": 9, "write": 0, "project": 6}
    assert status.call_count == 0
    assert not runtime.executions.submitted and not runtime.executions.cancelled


def test_parent_observes_only_live_batches_and_writes_only_changed_status(runtime, monkeypatch):
    """父计划仅观察运行批次且仅写入实际状态变化"""
    states = ["completed"] * 96
    states[2], states[3], states[4] = "running", "queued", "prepared"
    plan = seeded_plan(runtime, states)
    running = plan["batches"][2]["message_id"]
    completing = plan["batches"][3]["message_id"]

    def snapshot(resident_id, actor, message_id):
        execution_id = message_id
        assert actor == "alice"
        if execution_id == running:
            return {"message_id": execution_id, "state": "running"}
        assert execution_id == completing
        return {"message_id": execution_id, "state": "completed", "reply_message_ids": ["reply"]}

    status = Mock(side_effect=snapshot)
    monkeypatch.setattr(domain.resident_protocol, "get_resident_input_state", status)
    counts = count_operations(monkeypatch)
    result = runtime.owner.get_invocation(job=job(), surface="knowledge")
    assert status.call_count == 2
    assert counts == {"read": 5, "write": 1, "project": 2}
    assert result["receipt"]["status"] == "running"
    assert result["receipt"]["batches"][2]["status"] == "running"
    assert result["receipt"]["batches"][3]["status"] == "completed"
    assert result["receipt"]["batches"][4]["status"] == "prepared"
    assert not runtime.executions.submitted


@pytest.mark.parametrize("persisted,observed", [
    ("running", "running"), ("completed", "completed"), ("completed", "cancelled"),
    ("failed", "running"), ("cancelled", "running"),
])
def test_noop_observation_and_batch_read_never_write_or_project_whole_plan(runtime, monkeypatch, persisted, observed):
    """无变化观察与单批读取不写入或投影整份计划"""
    plan = seeded_plan(runtime, [persisted] + ["prepared"] * 95)
    batch = plan["batches"][0]
    store = domain._store()
    monkeypatch.setattr(ServiceInvocationStore, "_write", Mock(side_effect=AssertionError("unchanged receipt write")))
    monkeypatch.setattr(ServiceInvocationStore, "_project", Mock(side_effect=AssertionError("whole-plan projection")))
    current = store.get_batch(job(), "knowledge", batch["batch_id"], plan["plan_fingerprint"])
    result = store.observe(job(), "knowledge", batch["batch_id"], plan["plan_fingerprint"], observed)
    assert result == current and result["status"] == persisted
    with pytest.raises(ServiceInvocationConflict):
        store.observe(job(), "knowledge", batch["batch_id"], "wrong", observed)


@pytest.mark.parametrize("api", ["single_parent", "batch"])
@pytest.mark.parametrize("valid_result", [False, True])
def test_single_output_apis_still_fetch_and_validate_persisted_completion(runtime, monkeypatch, api, valid_result):
    """单批输出接口仍读取并校验已持久成功的执行结果"""
    plan = seeded_plan(runtime, ["completed"] * (1 if api == "single_parent" else 96))
    batch = plan["batches"][0]
    snapshot = {"message_id": batch["message_id"] if valid_result else "wrong-message",
                "state": "completed", "reply_message_ids": ["reply"]}
    status = Mock(return_value=snapshot)
    monkeypatch.setattr(domain.resident_protocol, "get_resident_input_state", status)
    monkeypatch.setattr(ServiceInvocationStore, "_write", Mock(side_effect=AssertionError("unchanged completion write")))

    def read():
        if api == "single_parent":
            return runtime.owner.get_invocation(job=job(), surface="knowledge")
        return runtime.owner.get_batch(job=job(), surface="knowledge", batch_id=batch["batch_id"],
                                       plan_fingerprint=plan["plan_fingerprint"])

    if valid_result:
        assert read()["input_state"] == snapshot
    else:
        with pytest.raises(ServiceInvocationConflict, match="another message"):
            read()
    assert status.call_count == 1
    assert not runtime.executions.submitted


@pytest.mark.parametrize("api", ["single_parent", "batch"])
def test_unchanged_completion_rechecks_fence_before_exposing_execution(runtime, monkeypatch, api):
    """无变化成功观察返回前复核封禁且不泄露执行输出"""
    plan = seeded_plan(runtime, ["completed"] * (1 if api == "single_parent" else 3))
    batch = plan["batches"][0]

    def status(resident_id, actor, execution_id):
        domain._store().cancel(job(), "knowledge")
        return {"message_id": execution_id, "state": "completed", "reply_message_ids": ["reply"]}

    monkeypatch.setattr(domain.resident_protocol, "get_resident_input_state", status)
    if api == "single_parent":
        result = runtime.owner.get_invocation(job=job(), surface="knowledge")
        selected = result["receipt"]["batches"][0]
    else:
        result = runtime.owner.get_batch(job=job(), surface="knowledge", batch_id=batch["batch_id"],
                                         plan_fingerprint=plan["plan_fingerprint"])
        selected = result["receipt"]
    assert result["input_state"] == {}
    assert selected["status"] == "completed" and selected["fence_reason"] == "cancelled"


@pytest.mark.parametrize("control", ["cancel", "delete", "refresh"])
def test_terminal_parent_rechecks_generation_and_fence_without_execution_reads(runtime, monkeypatch, control):
    """终态父计划返回前复核最新代次与封禁而不重复读取输出"""
    seeded_plan(runtime, ["completed"] * 3)
    original = ServiceInvocationStore.get
    changed = False

    def get(self, selected, surface):
        nonlocal changed
        result = original(self, selected, surface)
        if not changed:
            changed = True
            if control == "refresh":
                seeded_plan(runtime, ["prepared"] * 3, job(generation=2))
            else:
                self.cancel(selected, surface, deleted=control == "delete")
        return result

    monkeypatch.setattr(ServiceInvocationStore, "get", get)
    monkeypatch.setattr(runtime.executions, "status", Mock(side_effect=AssertionError("terminal output read")))
    result = runtime.owner.get_invocation(job=job(), surface="knowledge")
    assert result["receipt"]["is_current_generation"] == (control == "cancel")
    assert result["receipt"]["fenced"] == (control != "refresh")
    assert all(batch["status"] == "completed" for batch in result["receipt"]["batches"])
    assert result["input_state"] == {}


def test_fast_terminal_parent_read_does_not_bypass_active_creator_check(runtime, monkeypatch):
    """终态父计划快速读取不绕过原创建者有效性检查"""
    seeded_plan(runtime, ["completed"] * 3)
    runtime.access.is_active.side_effect = lambda actor: actor == "bob"
    monkeypatch.setattr(ServiceInvocationStore, "_read", Mock(side_effect=AssertionError("read before authorization")))
    with pytest.raises(PermissionError):
        runtime.owner.get_invocation(job=job(), surface="knowledge")
