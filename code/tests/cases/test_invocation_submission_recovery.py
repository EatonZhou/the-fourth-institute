"""Recover business submissions by original ISA/path facts, never by resending."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal import isa_client, lineage_workflows as workflows, resident_handoff as handoff
from management_portal.hatg.contracts import InstructionError

ACTOR = "sample_admin"
BINDING = {"lineage_id": "lineage_aaaaaaaaaaaa", "resident_id": "resident_bbbbbbbbbbbb",
           "event_source_id": "event_source_request", "effect_id": "effect_request"}
REQUEST = "msg_prepared"
ACTUAL = "msg_accepted_different"
INSTRUCTION = "inst_lin_aaaaaaaaaaaaaaaaaaaa"
PATH = "trigger_path_aaaaaaaaaaaa"


def path_record(path_id=PATH):
    return {"trigger_path_id": path_id, "lineage_id": BINDING["lineage_id"], "root_kind": "event",
            "root_node_id": BINDING["event_source_id"], "occurrence_id": "event_request",
            "cause_kind": "instruction", "cause_id": INSTRUCTION, "status": "active"}


def path_page():
    return {"path": path_record(), "steps": {"items": [
        {"step_id": "effect_step", "kind": "effect", "node_id": BINDING["effect_id"], "status": "failed"},
        {"step_id": "input_step", "kind": "resident", "node_id": BINDING["resident_id"],
         "input_message_id": ACTUAL, "status": "completed"},
    ], "next_offset": None}, "edges": {"items": [
        {"source_step_id": "effect_step", "target_step_id": "input_step"},
    ], "next_offset": None}}


@pytest.fixture
def rpc(monkeypatch):
    env = SimpleNamespace(calls=[], paths=[path_record()], page=path_page(),
        state={"message_id": ACTUAL, "state": "running"},
        record={"instruction_id": INSTRUCTION, "instruction": "TriggerEvent", "status": "failed",
                "references": {}, "error": {"code": "instruction.interrupted", "commit_state": "unknown"}},
        submission_error=InstructionError("instruction.interrupted", "receipt lost", commit_state="unknown",
            details={"instruction_id": INSTRUCTION, "instruction_result": {"instruction_id": "inst_ent_wrong"}}))

    def call(actor, name, arguments=None, **options):
        assert actor == ACTOR
        env.calls.append((name, deepcopy(arguments), deepcopy(options)))
        if name == "TriggerEvent":
            raise env.submission_error
        if name == "GetInstructionExecution":
            assert arguments in ({"instruction_id": INSTRUCTION},
                                 {"instruction": "TriggerEvent", "idempotency_key": prepared()["idempotency_key"]})
            if isinstance(env.record, Exception):
                raise env.record
            return deepcopy(env.record)
        if name == "ListTriggerPaths":
            assert "resident_id" not in arguments  # includes paths still awaiting delivery
            return {"paths": deepcopy(env.paths)}
        if name == "GetTriggerPath":
            assert arguments["trigger_path_id"] == PATH
            return deepcopy(env.page)
        if name == "GetInputState":
            assert arguments == {"resident_id": BINDING["resident_id"], "message_id": ACTUAL}
            return {"input_state": deepcopy(env.state)}
        raise AssertionError(f"Unexpected mutation/read: {name}")

    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(handoff, "ensure_invocation_resident", lambda **_: dict(BINDING))
    return env


def prepared():
    return workflows.WorkflowBindings.prepare(BINDING, request_id=REQUEST)


def resolve():
    return handoff.resolve_invocation_submission(actor=ACTOR, handle={**prepared(), "instruction_id": INSTRUCTION})


def test_interrupted_submission_recovers_actual_input_and_persists_preparation(rpc):
    """提交回执丢失时按原指令和触发路径找回实际受理的输入，只提交一次"""
    assigned = []
    result = handoff.submit_invocation(actor=ACTOR, message="task", resident_scope="scope",
        message_id=REQUEST, workflow_binding=BINDING, on_assigned=assigned.append)
    assert assigned == [prepared()]
    assert assigned[0]["submission_state"] == "pending" and "message_id" not in assigned[0]
    assert result["submission_state"] == "submitted" and result["message_id"] == ACTUAL != REQUEST
    assert result["request_id"] == REQUEST and result["instruction_id"] == INSTRUCTION
    assert [name for name, _, _ in rpc.calls].count("TriggerEvent") == 1
    rpc.calls.clear()
    assert handoff.resolve_invocation_submission(actor=ACTOR, handle=result) == result
    assert all(name.startswith("Get") for name, _, _ in rpc.calls)


def test_unavailable_receipt_without_execution_locator_stays_pending(rpc):
    """没有执行定位信息且查不到回执时，提交状态保持待定"""
    rpc.submission_error = TimeoutError("connection lost before receiving instruction identity")
    rpc.record = InstructionError("instruction.not_found", "receipt absent or expired")
    result = handoff.submit_invocation(actor=ACTOR, message="task", resident_scope="scope",
        message_id=REQUEST, workflow_binding=BINDING)
    assert result["submission_state"] == "pending" and "message_id" not in result
    assert "未找到不代表未受理" in result["submission_reason"]
    assert [name for name, _, _ in rpc.calls] == ["TriggerEvent", "GetInstructionExecution"]


def test_prepared_key_resolves_lost_receipt_without_resubmitting(rpc):
    """用准备阶段的幂等键查回丢失的回执并找到实际输入，不重新提交"""
    result = handoff.resolve_invocation_submission(actor=ACTOR, handle=prepared())
    assert result["submission_state"] == "submitted" and result["message_id"] == ACTUAL != REQUEST
    assert result["instruction_id"] == INSTRUCTION
    assert [name for name, _, _ in rpc.calls] == [
        "GetInstructionExecution", "ListTriggerPaths", "GetTriggerPath", "GetInputState"]
    assert rpc.calls[0][1] == {"instruction": "TriggerEvent", "idempotency_key": prepared()["idempotency_key"]}


@pytest.mark.parametrize("fault", ["paths", "inputs", "unrelated_effect", "wrong_source", "missing_receipt"])
def test_incomplete_or_ambiguous_original_evidence_never_selects_input(rpc, fault):
    """原始证据不完整或有歧义时不选定输入，提交保持待定"""
    if fault == "paths":
        rpc.paths.append(path_record("trigger_path_bbbbbbbbbbbb"))
    elif fault == "inputs":
        rpc.page["steps"]["items"].append({"step_id": "other_input", "kind": "resident",
            "node_id": BINDING["resident_id"], "input_message_id": "msg_other"})
        rpc.page["edges"]["items"].append({"source_step_id": "effect_step", "target_step_id": "other_input"})
    elif fault == "unrelated_effect":
        rpc.page["steps"]["items"][0]["node_id"] = "effect_unrelated"
    elif fault == "wrong_source":
        rpc.page["path"]["root_node_id"] = "event_source_other"
    else:
        rpc.state = {}
    result = resolve()
    assert result["submission_state"] == "pending" and "message_id" not in result
    assert not any(name in {"TriggerEvent", "DeliverMessage", "ListResidentStates", "CancelInput"} for name, _, _ in rpc.calls)


@pytest.mark.parametrize("status,commit_state,code,expected", [
    ("failed", "not_committed", "instruction.invalid_arguments", "not_submitted"),
    ("failed", "unknown", "instruction.interrupted", "pending"),
    ("failed", "not_committed", "instruction.interrupted", "pending"),
    ("executing", "unknown", "", "pending"),
    ("committed", "committed", "", "pending"),
])
def test_only_proven_nonacceptance_returns_not_submitted(rpc, status, commit_state, code, expected):
    """只有确认未提交的指令失败才判为未提交，其余情况保持待定"""
    rpc.paths = []
    rpc.record.update(status=status, error={"code": code, "commit_state": commit_state})
    result = resolve()
    assert result["submission_state"] == expected and "message_id" not in result


def test_expired_instruction_record_is_not_proof_of_nonacceptance(rpc):
    """指令记录已过期不视为未受理，仍按触发路径找回输入"""
    rpc.record = InstructionError("instruction.not_found", "expired")
    rpc.paths = []
    assert resolve()["submission_state"] == "pending"
    rpc.paths = [path_record()]
    assert resolve()["message_id"] == ACTUAL


def test_failed_instruction_and_failed_business_input_are_still_submitted(rpc):
    """指令失败或业务输入失败时，已受理的输入仍判为已提交"""
    rpc.record["error"] = {"code": "instruction.failed", "commit_state": "not_committed"}
    rpc.page["path"]["status"] = "failed"
    rpc.state["state"] = "failed"
    assert resolve()["message_id"] == ACTUAL


def test_invoke_pending_does_not_wait_or_cancel_prepared_id(rpc, monkeypatch):
    """提交待定时调用方不等待结果，也不取消准备好的消息 ID"""
    rpc.paths = []
    assigned, submitted = [], []
    monkeypatch.setattr(handoff, "resume_and_wait", lambda **_: pytest.fail("must not wait on unconfirmed input"))
    monkeypatch.setattr(handoff, "cancel_resident_invocation", lambda *_: pytest.fail("must not cancel prepared ID"))
    with pytest.raises(workflows.WorkflowPending) as pending:
        handoff.invoke_and_wait(actor=ACTOR, message="task", resident_scope="scope", message_id=REQUEST,
            workflow_binding=BINDING, on_assigned=assigned.append, on_submitted=submitted.append)
    assert pending.value.handle["instruction_id"] == INSTRUCTION
    assert pending.value.handle["request_id"] == assigned[0]["request_id"] == REQUEST
    assert "message_id" not in pending.value.handle and submitted == []


def test_cancellation_after_preparation_is_proven_unsent(rpc):
    """准备完成后收到取消请求时不发送，判为未提交"""
    cancelled = False
    def assigned(handle):
        nonlocal cancelled
        assert "message_id" not in handle
        cancelled = True
    result = handoff.submit_invocation(actor=ACTOR, message="task", resident_scope="scope", message_id=REQUEST,
        workflow_binding=BINDING, on_assigned=assigned, cancellation_requested=lambda: cancelled)
    assert result["submission_state"] == "not_submitted" and rpc.calls == []


def test_assignment_failure_or_mutation_cannot_change_target(rpc):
    """准备信息保存失败时不提交，保存回调改动准备信息也不改变提交目标"""
    def reject(_):
        raise ValueError("journal failed")
    with pytest.raises(ValueError, match="journal failed"):
        handoff.submit_invocation(actor=ACTOR, message="task", resident_scope="scope", message_id=REQUEST,
            workflow_binding=BINDING, on_assigned=reject)
    assert rpc.calls == []
    result = handoff.submit_invocation(actor=ACTOR, message="task", resident_scope="scope", message_id=REQUEST,
        workflow_binding=BINDING, on_assigned=lambda h: h.update(resident_id="wrong", request_id="wrong"))
    assert result["resident_id"] == BINDING["resident_id"] and result["request_id"] == REQUEST


def test_direct_submission_lost_receipt_reads_requested_input_without_redelivery(monkeypatch):
    """直接投递的回执丢失时读取该输入状态确认受理，不重新投递"""
    monkeypatch.setattr(handoff, "ensure_invocation_resident", lambda **_: dict(BINDING))
    sent, reads = [], []
    def deliver(**kwargs):
        sent.append(kwargs)
        raise TimeoutError("acknowledgement lost")
    def read(actor, name, args, **_):
        assert actor == ACTOR
        if name == "GetInstructionExecution":
            assert args == {"instruction": "DeliverMessage", "idempotency_key": f"invocation:{REQUEST}"}
            raise InstructionError("instruction.not_found", "no persisted receipt")
        assert name == "GetInputState"
        reads.append(args)
        return {"input_state": {"message_id": REQUEST, "state": "queued"}}
    monkeypatch.setattr(handoff, "deliver_resident_message", deliver)
    monkeypatch.setattr(isa_client, "call", read)
    result = handoff.submit_invocation(actor=ACTOR, message="task", resident_scope="scope", message_id=REQUEST)
    assert result["submission_state"] == "submitted" and result["message_id"] == REQUEST
    assert len(sent) == len(reads) == 1


from tests.cases.test_lineage_generic_primitives import primitive_store  # noqa: F401,E402


@pytest.mark.parametrize("process_exit", [False, True])
def test_real_kernel_record_and_persisted_path_recover_after_delivery_interruption(primitive_store, monkeypatch, tmp_path, process_exit):
    """真实 ISA 内核下投递中断或进程退出后，按持久化的执行记录和触发路径找回输入

    Real ISA kernel/receipt store/path handlers, with only delivery/provider boundary isolated."""
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import current_instruction_id
    from management_portal.hatg import routing
    import json
    from management_portal.society_service import lineage_domain, trigger_paths, trigger_path_observation
    from management_portal.society_service.isa import build_kernel, execution

    graph = lineage_domain.create_lineage({"title": "Submission recovery fixture", "nodes": [], "edges": []}, ACTOR)
    binding = {**BINDING, "lineage_id": graph["lineage_id"]}
    kernel = build_kernel()
    calls, accepted = [], {}
    def emit(lineage_id, payload, actor):
        assert actor == ACTOR and payload["event_source_id"] == binding["event_source_id"]
        event = {"id": "event_submission_fixture", "type": "manual.triggered",
                 "occurred_at": "2026-10-16T00:00:00Z", "payload": {}}
        path_id = trigger_paths.begin_event(lineage_id, binding["event_source_id"], event,
            cause_kind="instruction", cause_id=current_instruction_id())
        _, step_id = trigger_paths.effect_started(lineage_id, "fixture_activation", {
            "trigger_path_id": path_id, "effect_id": binding["effect_id"], "started_at": event["occurred_at"]})
        event["payload"].update(trigger_path_id=path_id, trigger_step_id=step_id)
        accepted[ACTUAL] = {"message_id": ACTUAL, "state": "queued"}
        trigger_paths.admit_resident(lineage_id, resident_id=binding["resident_id"], message_id=ACTUAL,
            trigger_path_id=path_id, source_wire_id="wire_fixture", event=event)
        if process_exit:
            raise SystemExit("owner disappeared before returning any execution locator")
        raise InstructionError("instruction.failed", "owner stopped after durable delivery",
                               details={"reason": "interrupted"}, commit_state="unknown")
    monkeypatch.setattr(lineage_domain, "emit_program_manual_event", emit)
    monkeypatch.setattr(execution, "protocol", lambda: SimpleNamespace(
        get_resident_input_state=lambda rid, actor, mid, **_: deepcopy(accepted[mid])))
    def call(actor, name, arguments=None, **options):
        assert actor == ACTOR
        calls.append(name)
        context = InstructionContext(caller=actor, entry="web")
        return routing.execute_local(InstructionCall(name, arguments, idempotency_key=options.get("key", "")),
                                     context, owner="lineage").raise_for_error()
    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(routing, "kernel_for", lambda owner, **_: kernel if owner == "lineage" else pytest.fail(owner))
    monkeypatch.setattr(handoff, "ensure_invocation_resident", lambda **_: dict(binding))
    journal = tmp_path / "prepared.json"
    def submit():
        return handoff.submit_invocation(actor=ACTOR, message="task", resident_scope="fixture", message_id=REQUEST,
            workflow_binding=binding, on_assigned=lambda handle: journal.write_text(json.dumps(handle)))
    if process_exit:
        with pytest.raises(SystemExit):
            submit()
        saved = json.loads(journal.read_text())
        assert not any(key in saved for key in ("instruction_id", "event_id", "trigger_path_id", "message_id"))
        kernel = build_kernel()  # Fresh owner instance; only the durable preparation survives at the caller.
        # A newer event for the same source cannot be substituted for the original request.
        decoy = trigger_paths.begin_event(binding["lineage_id"], binding["event_source_id"], {
            "id": "event_later_unrelated", "type": "manual.triggered",
            "occurred_at": "2099-01-01T00:00:00Z", "payload": {},
        }, cause_kind="instruction", cause_id="inst_lin_other_request")
        result = handoff.resolve_invocation_submission(actor=ACTOR, handle=saved)
        assert result["trigger_path_id"] != decoy
    else:
        result = submit()
    assert result["submission_state"] == "submitted", result
    assert result["message_id"] == ACTUAL != result["request_id"]
    record = kernel.execution_record(result["instruction_id"], InstructionContext(caller=ACTOR, entry="web"))
    assert record["status"] == "failed" and record["error"]["details"]["reason"] == "interrupted", record
    persisted = trigger_path_observation.observe(binding["lineage_id"], {"trigger_path_id": result["trigger_path_id"]})
    assert persisted["path"]["cause_id"] == result["instruction_id"]
    assert any(step.get("input_message_id") == ACTUAL for step in persisted["steps"]["items"])
    assert calls.count("TriggerEvent") == 1 and calls.count("GetInputState") == 1


@pytest.mark.parametrize("ambiguous", [False, True])
def test_paginated_path_waits_for_all_evidence_before_selecting(rpc, monkeypatch, ambiguous):
    """触发路径分页时读完所有页再选定输入，后续页出现歧义则保持待定"""
    original = isa_client.call
    first = path_page()
    first["steps"]["next_offset"] = 200
    second = {"path": path_record(), "steps": {"items": [{"step_id": "late_input", "kind": "resident",
        "node_id": BINDING["resident_id"], "input_message_id": "msg_late"}], "next_offset": None},
        "edges": {"items": [{"source_step_id": "effect_step" if ambiguous else "unrelated_effect",
                             "target_step_id": "late_input"}], "next_offset": None}}
    offsets = []
    def read(actor, name, args=None, **options):
        if name == "GetTriggerPath":
            offsets.append(args["offset"])
            return deepcopy(second if args["offset"] else first)
        return original(actor, name, args, **options)
    monkeypatch.setattr(isa_client, "call", read)
    result = resolve()
    assert offsets == [0, 200]
    assert result["submission_state"] == ("pending" if ambiguous else "submitted")
    assert result.get("message_id") == (None if ambiguous else ACTUAL)


def test_instruction_interrupted_reason_is_unknown_even_with_inconsistent_commit_flag(rpc):
    """原因为 interrupted 的失败即使标为未提交，提交状态仍视为待定"""
    rpc.paths = []
    rpc.record["error"] = {"code": "instruction.failed", "commit_state": "not_committed",
                           "details": {"reason": "interrupted"}}
    assert resolve()["submission_state"] == "pending"
