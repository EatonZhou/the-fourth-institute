"""New Paper inputs run fresh; canonical inputs and their recovery remain stable."""
from copy import deepcopy

import pytest

from management_portal.society_service import (
    lineage_domain, lineage_reads, lineage_program_backlog, lineage_program_execution,
    lineage_program_resident_inputs, resident_execution_domain, resident_protocol,
)
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401
from tests.cases.test_lineage_invocation_prompt_budget import _graph, trigger
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401


@pytest.mark.parametrize("queued", [False, True])
def test_fresh_effect_persists_real_input_before_delivery_and_replays_once(isolated_instruction_store, monkeypatch, queued):
    """真实 Event/Effect/ISA 链在受理前绑定上下文；排队和重复请求不产生第二个输入"""
    sent = []
    busy = {"value": queued}
    availability = lineage_program_execution.program_event_route_can_consume
    monkeypatch.setattr(lineage_program_execution, "program_event_route_can_consume",
                        lambda *a, **k: (False, "resident_running") if busy["value"] else availability(*a, **k))
    monkeypatch.setattr(lineage_domain, "_node_has_active_execution", lambda *a, **k: False)
    lid = _graph()
    route = next(w for w in lineage_reads.get_lineage(lid, ACTOR)["program"]["wires"] if w["id"] == "wire_input")
    ok("UpdateWire", {"lineage_id": lid, "wire_id": "wire_input", "changes": {
        "config": {**route["config"], "context_policy": "fresh"}}})

    def receive(payload, actor, *, delivery_context):
        mid = payload["message"]["message_id"]
        assert lineage_program_resident_inputs.delivery_profile(lid, mid) == {"isolated": True}
        assert actor == ACTOR and delivery_context.lineage_id == lid
        sent.append(deepcopy(payload))
        return {"message_id": mid, "message": payload["message"], "accepted": True}

    monkeypatch.setattr(resident_protocol, "deliver_resident_input", receive)
    first = trigger(lid, "Only paper A", key="fresh-A").raise_for_error()
    if queued:
        assert sent == []
        busy["value"] = False
        drained = lineage_program_backlog.drain_event_backlog(lid, ACTOR, authority=lineage_domain._program_control_authority())
        assert drained["delivered_count"] == 1
    replay = trigger(lid, "Only paper A", key="fresh-A")
    assert replay.replayed and replay.result["event_id"] == first["event_id"]
    trigger(lid, "Only paper B", key="fresh-B").raise_for_error()
    assert len(sent) == 2
    assert sent[0]["message"]["message_id"] != sent[1]["message"]["message_id"]
    assert [p["message"]["content_parts"][0]["text"] for p in sent] == ["FIXED\n\nOnly paper A", "FIXED\n\nOnly paper B"]


@pytest.mark.parametrize("policy", ["inject_active_turn", "attach_to_next"])
def test_fresh_cannot_join_an_existing_turn(isolated_instruction_store, monkeypatch, policy):
    """独立任务不能注入当前任务或附到下一轮"""
    from tests.cases.test_lineage_invocation_prompt_budget import WORK_A
    _graph()
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", lambda *a, **k: pytest.fail("must not deliver"))
    result = run("DeliverMessage", {"resident_id": WORK_A, "message": {"content_parts": [{"type": "text", "text": "new"}]},
                                    "context_policy": "fresh", "activation_policy": policy})
    assert result.status == "failed" and "fresh" in result.error["message"]


def test_profile_read_failure_never_falls_back_to_shared_history(monkeypatch):
    """读取隔离策略失败时停止，而不是借用共享历史"""
    def unavailable(*a):
        raise RuntimeError("profile unavailable")
    monkeypatch.setattr(lineage_program_resident_inputs, "delivery_profile", unavailable)
    with pytest.raises(RuntimeError, match="profile unavailable"):
        resident_execution_domain._lineage_delivery_profile({"lineage_id": "lineage_111111111111", "user_message_id": "msg_test"})


def test_fresh_does_not_change_an_existing_session_input(isolated_instruction_store, monkeypatch):
    """不能通过新的 ISA 调用改变已受理输入的上下文方式"""
    from management_portal.society_service import resident_conversation_stream
    from tests.cases.test_lineage_invocation_prompt_budget import WORK_A
    lid = _graph()
    mid = "msg_existing_session"
    repo = resident_conversation_stream.get_repository()
    repo.accept_user_message(WORK_A, {"message_id": mid, "role": "user", "author_id": ACTOR,
                                     "content_parts": [{"type": "text", "text": "old task"}], "created_at": "2026-11-10T00:00:00Z"})
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", lambda *a, **k: pytest.fail("must not deliver"))
    result = run("DeliverMessage", {"resident_id": WORK_A, "message": {"message_id": mid, "content_parts": [{"type": "text", "text": "new"}]},
                                    "context_policy": "fresh"})
    assert result.status == "failed" and "已接收" in result.error["message"]
    assert lineage_program_resident_inputs.delivery_profile(lid, mid) == {}


def test_native_fresh_inputs_do_not_use_session_or_portable_history(resident_record, monkeypatch):
    """两个新任务不复用模型 session、历史 seed；正常聊天仍保留原会话"""
    from management_portal.society_service import resident_backend, resident_execution_engine_domain
    from management_portal.society_service.domain_ports import execution_port
    record = resident_record
    monkeypatch.setattr(resident_execution_domain, "_lineage_delivery_profile", lambda job: {"isolated": True})
    monkeypatch.setattr(resident_execution_engine_domain, "resident_execution_runtime_options",
                        lambda *a, **k: {"session_key": "shared-session"})
    monkeypatch.setattr(resident_backend, "execution_binding_id", lambda record: "binding-shared")
    monkeypatch.setattr(execution_port, "agent_context_snapshot", lambda *a, **k: pytest.fail("must not read previous tasks"))
    for i in (1, 2):
        job = {"actor": "CASE-A", "user_message_id": f"msg_fresh_{i}", "raw_message": f"paper {i}",
               "actual_sent_prompt": f"paper {i}", "execution_id": f"execution-{i:024x}"}
        assert resident_execution_domain._portable_context_execution(record, job) == job
        request = resident_execution_domain._build_resident_execution_request(record, job)
        assert request.session.binding_id == "" and request.session.mode == "stateless"
        assert request.runtime_options["session_key"] == ""
        assert request.prompt == f"paper {i}"
    monkeypatch.setattr(resident_execution_domain, "_lineage_delivery_profile", lambda job: {})
    request = resident_execution_domain._build_resident_execution_request(record, job)
    assert request.session.binding_id == "binding-shared" and request.runtime_options["session_key"] == "shared-session"


def test_fresh_task_does_not_consume_shared_mailbox_infos(resident_record, monkeypatch):
    """独立任务不夹带或消费共享 Resident 为普通聊天暂存的消息"""
    from management_portal.society_service import resident_execution_engine_domain, resident_held_infos
    monkeypatch.setattr(resident_execution_domain, "_lineage_delivery_profile", lambda job: {"isolated": True})
    monkeypatch.setattr(resident_held_infos, "attach", lambda *a: pytest.fail("must not attach old mailbox infos"))
    class Built(Exception):
        pass
    def capture(record, job, builder):
        request = builder()
        assert request.prompt == "only this task" and not request.metadata.get("resident_carried_infos")
        raise Built
    monkeypatch.setattr(resident_execution_engine_domain, "durable_resident_execution_request", capture)
    with pytest.raises(Built):
        resident_execution_domain._send_resident_execution(resident_record, {
            "actor": "CASE-A", "user_message_id": "msg_fresh_mailbox", "actual_sent_prompt": "only this task",
        })
