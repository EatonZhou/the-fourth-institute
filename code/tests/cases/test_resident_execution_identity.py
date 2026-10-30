""":importance: P1"""
import pytest

from tests.cases.resident_execution_test_support import resident_record
from management_portal.society_service import resident_execution_work as work, trigger_paths
from management_portal.execution import execution_inputs
from management_portal.execution.agent_data_store import get_agent_data_store

AT = "2026-10-14T00:00:00Z"


def preparation(message="msg_primary"):
    return {**work.new_item({"message_id": message, "role": "user", "author_id": "sample_admin",
                            "content_parts": [{"type": "text", "text": "inspect"}]}, "inspect", "inspect", AT),
            "actor": "sample_admin"}


def test_prepare_cancel_retains_one_execution_and_input(resident_record):
    """准备阶段取消的执行保留同一执行记录与输入关联，状态为 cancelled

    :design: decision_71aa039e3211"""
    value = preparation()
    work.write(resident_record, value)
    identity = value["execution_id"]
    store = get_agent_data_store()
    assert store.execution(identity)["status"] == "preparing"
    assert work.read(resident_record)["execution_id"] == identity
    value.update(outcome="cancelled", finished_at=AT)
    work.write(resident_record, value)
    assert store.execution(identity)["status"] == "cancelled"
    assert execution_inputs.latest(store, resident_record["resident_id"], "msg_primary")["execution_id"] == identity


def test_two_event_inputs_share_independent_execution(resident_record):
    """两个事件触发的输入合并为一次执行，执行结束后两条触发路径都完成

    :design: decision_71aa039e3211"""
    lineage = resident_record["lineage_id"]
    resident = resident_record["resident_id"]
    paths = []
    for index in range(2):
        event = {"id": f"event_{index}", "type": "manual.triggered", "occurred_at": AT, "payload": {}}
        path = trigger_paths.begin_event(lineage, "event-source", event, cause_kind="input", cause_id=f"msg_source_{index}")
        event["payload"]["trigger_path_id"] = path
        trigger_paths.admit_resident(lineage, resident_id=resident, message_id=f"msg_input_{index}",
                                    trigger_path_id=path, source_wire_id="wire", event=event)
        trigger_paths.event_settled(path, "event-source", event["id"])
        paths.append(path)
    value = preparation("msg_input_0")
    value["mailbox_members"] = [{"message_id": "msg_input_1"}]
    work.write(resident_record, value)
    store = get_agent_data_store()
    identity = value["execution_id"]
    assert "trigger_path_id" not in store.execution(identity)
    for path in paths:
        data = trigger_paths.get(lineage, path)
        assert [e["execution_id"] for e in data["executions"]] == [identity]
        assert data["status"] == "active"
    store.begin_execution(execution_id=identity, agent_id=resident, provider="codex", actor_ref="sample_admin", started_at=AT)
    store.finish_execution(identity, status="completed", finished_at=AT)
    for path in paths:
        trigger_paths.settle(path)
        assert trigger_paths.get(lineage, path)["status"] == "completed"
    event = trigger_paths.begin_resident_output(lineage, resident, "msg_input_0", at=AT)
    assert event.payload["trigger_path_id"] not in paths
    assert event.payload["cause"] == {"kind": "execution", "id": identity}


def test_input_replay_preserves_terminal_execution(resident_record):
    """已结束的执行不能再挂上新输入，重放原输入不改变它

    :design: decision_71aa039e3211"""
    value = preparation()
    work.write(resident_record, value)
    store = get_agent_data_store()
    identity = value["execution_id"]
    store.finish_execution(identity, status="failed", finished_at=AT)
    execution_inputs.attach(store, identity, [{"message_id": "msg_primary", "role": "primary", "accepted_at": AT}])
    with pytest.raises(ValueError, match="terminal"):
        execution_inputs.attach(store, identity, [{"message_id": "msg_other", "role": "info", "accepted_at": AT}])
    assert execution_inputs.latest(store, resident_record["resident_id"], "msg_other") is None


def test_router_completion_preserves_request_reference_through_event_storage(resident_record):
    """Router 执行完成后输出事件保留请求的 business_ref，转交脚本据此把消息交给成员

    :importance: P2"""
    from management_portal.society_service import family_definition
    from tests.cases.test_family_router_script import _run, FACADE, MESSAGE_ID, MEMBERS, STEWARD

    resident = resident_record["resident_id"]
    lineage = resident_record["lineage_id"]
    message_id = "msg_" + "3" * 32
    business_ref = f"family-route/{FACADE}/message/{MESSAGE_ID}"
    value = preparation(message_id)
    value["input_message"]["business_ref"] = business_ref
    work.write(resident_record, value)
    store = get_agent_data_store()
    store.finish_execution(value["execution_id"], status="completed", finished_at=AT)
    event = trigger_paths.begin_resident_output(lineage, resident, message_id, at=AT)
    path = trigger_paths.get(lineage, event.payload["trigger_path_id"])
    assert event.payload["input_business_ref"] == business_ref
    assert path["input"]["payload"]["input_business_ref"] == business_ref
    outcome, calls = _run(family_definition.FAMILY_ROUTE_RELAY_SCRIPT, {
        "event": path["input"], "arguments": {"members": MEMBERS, "steward": STEWARD, "threshold": 60},
        "packet": {"key": "route", "message": {"text": "arithmetic", "data": {
            "target": MEMBERS[0], "confidence": 99, "scope": "in"}}}, "endpoint": {},
    }, {"RelayFamilyMessage": {"status": "relayed"}})
    assert outcome.status == "succeeded"
    assert calls == [("RelayFamilyMessage", {
        "resident_id": FACADE, "message_id": MESSAGE_ID, "member_id": MEMBERS[0]})]


def test_invalid_preparation_rolls_back_execution_and_inputs(resident_record):
    """含未知字段的执行准备被拒绝，执行与输入记录一起回滚

    :design: decision_71aa039e3211"""
    value = preparation()
    value["unowned_field"] = "rejected"
    with pytest.raises(ValueError, match="Unknown"):
        work.write(resident_record, value)
    store = get_agent_data_store()
    assert store.execution(value["execution_id"]) is None
    assert execution_inputs.latest(store, resident_record["resident_id"], "msg_primary") is None
