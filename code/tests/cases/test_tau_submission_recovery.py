"""A lost submission receipt never grants ownership to cancel a guessed input."""
from __future__ import annotations

import json

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.resident_handoff import ResidentInputTerminated
from management_portal.test_framework import runner_adapters
from management_portal.test_framework.records import RunRecords
from management_portal.test_framework.runner_adapters import TauAdapter
from management_portal.test_framework.tau import TauConfig
from management_portal.test_framework.tau_campaign import TauCampaignExecutor
from tests.cases.test_test_framework import _tau_config, _verified_dev_target


PENDING = {"resident_id": "resident-original", "lineage_id": "lineage-original",
           "request_id": "request-before-trigger", "message_id": "",
           "event_source_id": "event-source", "effect_id": "effect-original",
           "submission_state": "pending"}
ACCEPTED = {**PENDING, "message_id": "message-actually-accepted", "submission_state": "submitted",
            "trigger_path_id": "trigger-original", "event_id": "event-original"}
PLANNED = {"case_id": "test_123456789abc", "case_title": "Recover the original input",
           "description": "Do not resend or release its environment.", "module": "platform.core",
           "implementation": {"id": "impl-recovery", "task_prompt": "Observe the page."},
           "runner": {"id": "tau"}}
VERDICT = {**ACCEPTED, "message": 'PORTAL_TAU_RESULT: {"status":"passed","summary":"completed",'
           '"issues":[],"evidence":[]}'}


@pytest.fixture
def config(tmp_path):
    storage = tmp_path / "state.json"
    storage.write_text("{}")
    storage.chmod(0o600)
    return _tau_config(storage)


def execute(adapter, tmp_path, **options):
    return adapter.execute(run_id="testrun_0123456789abcdef", planned=options.pop("planned", PLANNED),
                           run_dir=tmp_path / "evidence", actor="requester",
                           emit=lambda _text: None, cancelled=options.pop("cancelled", lambda: False), **options)


def lost_receipt(**kwargs):
    kwargs["on_assigned"](PENDING)
    raise InstructionError("instruction.failed", "interrupted", commit_state="unknown",
                           details={"reason": "interrupted", "instruction_id": "inst_lin_original"})


def test_interrupted_receipt_resumes_actual_input_without_resubmission(tmp_path, config):
    """提交回执丢失时 TAU 解析出实际输入继续等待结果，不重新提交"""
    sent, resolved, resumed, persisted = [], [], [], []

    def invoke(**kwargs):
        sent.append(True)
        lost_receipt(**kwargs)

    def resolve(**kwargs):
        resolved.append(kwargs["handle"])
        return ACCEPTED

    def resume(**kwargs):
        resumed.append(kwargs["message_id"])
        return VERDICT

    adapter = TauAdapter(config, invoke, lambda _: _verified_dev_target(), resume=resume,
                         resolve_submission=resolve,
                         cancel_invocation=lambda *args: pytest.fail("accepted work must continue"))
    outcome = execute(adapter, tmp_path, on_submitted=persisted.append)
    assert outcome.status == "passed"
    assert sent == [True]
    assert len(resolved) == 1
    assert all(resolved[0][key] == value for key, value in PENDING.items())
    assert resolved[0]["instruction_id"] == "inst_lin_original"
    assert resumed == [ACCEPTED["message_id"]]
    assert persisted[0]["request_id"] == PENDING["request_id"]
    assert persisted[0]["message_id"] == ""
    assert persisted[-1]["message_id"] == ACCEPTED["message_id"]
    assert adapter.cleanup_ready(run_dir=tmp_path / "evidence")


def test_unresolved_submission_preserves_helpers_and_checkpoint(tmp_path, config, monkeypatch):
    """提交状态未确认时保留浏览器辅助进程和检查点，清理被拒绝"""
    adapter = TauAdapter(config, lost_receipt, lambda _: _verified_dev_target(),
                         resolve_submission=lambda **_: {**PENDING, "reason": "no unique causal path yet"},
                         cancel_invocation=lambda *args: pytest.fail("request ID is not an input ID"))
    persisted = []
    outcome = execute(adapter, tmp_path, on_submitted=persisted.append)
    assert outcome.status == "pending"
    assert persisted[-1]["submission_state"] == "pending"
    monkeypatch.setattr(runner_adapters, "_terminate_artifact_helpers",
                        lambda _: pytest.fail("unresolved work still owns its browser"))
    with pytest.raises(RuntimeError, match="尚未确认终止"):
        adapter.cleanup(run_dir=tmp_path / "evidence", emit=lambda _: None)
    state = json.loads((tmp_path / "evidence" / "tau-invocation.json").read_text())
    assert state["handle"]["request_id"] == PENDING["request_id"]
    assert state["safe_to_cleanup"] is False


def test_restarted_pending_submission_cancels_only_after_actual_identity(tmp_path, config):
    """重启后取消待定提交时先解析出实际消息再取消，不重新发送"""
    resumed = []

    def resume(**kwargs):
        assert kwargs["cancellation_requested"]()
        resumed.append(kwargs["message_id"])
        raise ResidentInputTerminated({"state": "cancelled", "message_id": ACCEPTED["message_id"]})

    adapter = TauAdapter(config, lambda **_: pytest.fail("must never send again"),
                         lambda _: _verified_dev_target(), resume=resume,
                         resolve_submission=lambda **_: ACCEPTED,
                         cancel_invocation=lambda *args: {"state": "cancelled"})
    outcome = execute(adapter, tmp_path, planned={**PLANNED, "_resume_invocation": PENDING},
                      cancelled=lambda: True)
    assert outcome.status == "cancelled"
    assert resumed == [ACCEPTED["message_id"]]
    assert adapter.cleanup_ready(run_dir=tmp_path / "evidence")


def test_cleanup_waits_for_actual_terminal_after_cancel_ack(tmp_path, config, monkeypatch):
    """取消请求被确认后，清理仍等到输入实际进入终态才允许"""
    cancelled, observed = [], []
    monkeypatch.setattr(runner_adapters.time, "sleep", lambda _: None)

    def invoke(**kwargs):
        kwargs["on_submitted"](ACCEPTED)
        raise ValueError("observer failed after acceptance")

    def cancel(resident, actor, message):
        cancelled.append((resident, actor, message))
        return {"state": "cancelling"}

    def read(**kwargs):
        assert not adapter.cleanup_ready(run_dir=tmp_path / "evidence")
        observed.append(kwargs["message_id"])
        return {"state": "running" if len(observed) == 1 else "cancelled"}

    adapter = TauAdapter(config, invoke, lambda _: _verified_dev_target(),
                         cancel_invocation=cancel, read_input_state=read)
    outcome = execute(adapter, tmp_path)
    assert outcome.status == "error"
    assert cancelled == [(ACCEPTED["resident_id"], "test-actor", ACCEPTED["message_id"])]
    assert observed == [ACCEPTED["message_id"]] * 2
    assert adapter.cleanup_ready(run_dir=tmp_path / "evidence")


def test_failed_cancellation_is_not_cleanup_permission(tmp_path, config):
    """取消调用失败时 TAU 结果保持待定，不允许清理"""
    def invoke(**kwargs):
        kwargs["on_submitted"](ACCEPTED)
        raise ValueError("observation unavailable")

    def cancel(*args):
        raise PermissionError("access revoked")

    adapter = TauAdapter(config, invoke, lambda _: _verified_dev_target(), cancel_invocation=cancel)
    outcome = execute(adapter, tmp_path)
    assert outcome.status == "pending"
    assert not adapter.cleanup_ready(run_dir=tmp_path / "evidence")
    assert outcome.details["conversation"]["message_id"] == ACCEPTED["message_id"]


def test_proven_unaccepted_request_ends_without_send_or_cancel(tmp_path, config):
    """确认请求未被受理时 TAU 结束为不支持，不重发也不取消"""
    adapter = TauAdapter(config, lost_receipt, lambda _: _verified_dev_target(),
                         resolve_submission=lambda **_: {**PENDING, "submission_state": "not_submitted"},
                         cancel_invocation=lambda *args: pytest.fail("no input was accepted"))
    assert execute(adapter, tmp_path).status == "unsupported"
    assert adapter.cleanup_ready(run_dir=tmp_path / "evidence")


def test_pre_submission_lifecycle_rejection_does_not_create_unknown_input(tmp_path, config, monkeypatch):
    """投递前资源归属检查拒绝时不发送输入，也不进入未知回执循环"""
    from management_common.resource_lifecycle import LifecycleStore

    def reject(_self, _scope, _actor):
        raise ValueError("Resident still belongs to an unfinished resource lifecycle")

    monkeypatch.setattr(LifecycleStore, "bind", reject)

    def invoke(**kwargs):
        kwargs["on_assigned"](PENDING)
        pytest.fail("submission must stop before the instruction is sent")

    adapter = TauAdapter(config, invoke, lambda _: _verified_dev_target(),
                         resolve_submission=lambda **_: pytest.fail("no submission to resolve"),
                         cancel_invocation=lambda *args: pytest.fail("no input to cancel"))
    persisted = []
    outcome = execute(adapter, tmp_path, on_submitted=persisted.append)
    assert outcome.status == "unsupported"
    assert "unfinished resource lifecycle" in outcome.details["summary"]
    assert persisted == []
    checkpoint = json.loads((tmp_path / "evidence" / "tau-invocation.json").read_text())
    assert checkpoint["handle"]["submission_state"] == "not_submitted"
    assert adapter.cleanup_ready(run_dir=tmp_path / "evidence")


@pytest.mark.parametrize("crash", [False, True])
def test_campaign_preserves_unresolved_work_and_recovers_without_retrigger(tmp_path, config, monkeypatch, crash):
    """提交结果未知或执行器崩溃时 TAU 运行保持进行中，下次执行观察原请求而不重发"""
    monkeypatch.setattr(TauCampaignExecutor, "_verify_source", lambda _self, _item: None)
    plan = {"schema": "tfi.test-run-plan.v2", "executable": True, "campaign": "tau",
            "selected": [PLANNED], "execution": {"mode": "parallel-modules", "groups": [{
                "group_id": "module:platform.core", "module_id": "platform.core", "runner": "tau",
                "item_indexes": [0], "continue_on_failure": True}]}}
    records = RunRecords(tmp_path / "runs.json")
    run_id = records.create(plan, "requester")["run_id"]
    sent, cleaned, finalized = [], [], []

    def invoke(**kwargs):
        sent.append(True)
        kwargs["on_assigned"](PENDING)
        if crash:
            raise SystemExit("executor disappeared after acceptance")
        raise InstructionError("instruction.failed", "interrupted", commit_state="unknown")

    adapter = TauAdapter(config, invoke, lambda _: _verified_dev_target(),
                         resolve_submission=lambda **_: PENDING, resume=lambda **_: VERDICT)
    monkeypatch.setattr(adapter, "cleanup", lambda **_: cleaned.append(True))
    monkeypatch.setattr(adapter, "finalize_group", lambda **_: finalized.append(True) or {"status": "complete"})
    executor = TauCampaignExecutor(records, tmp_path / "artifacts", adapter)
    if crash:
        with pytest.raises(SystemExit):
            executor.run(run_id)
    else:
        assert executor.run(run_id) == "running", records.get(run_id).get("error")
    record = records.get(run_id)
    assert record["status"] == "running"
    assert not record.get("finished_at")
    assert not record["results"]
    assert record["groups"][0]["active_invocation"]["request_id"] == PENDING["request_id"]
    assert not cleaned and not finalized
    # The next executor observes the durable request, even if the plan source changed.
    monkeypatch.setattr(TauCampaignExecutor, "_verify_source", lambda _self, _: pytest.fail("do not prepare new work"))
    adapter.resolve_submission = lambda **_: ACCEPTED
    records.update(run_id, {"executor": {**record["executor"], "pid": 999_999_999}})
    assert executor.run(run_id) == "passed"
    assert sent == [True]
    assert cleaned == [True] and finalized == [True]
    assert records.get(run_id)["groups"][0]["active_invocation"] == {}


@pytest.mark.parametrize("rejected", [False, True])
def test_owner_exception_preserves_late_causal_locator(tmp_path, config, rejected):
    """业务方异常中带回的新定位信息被保存，请求被拒绝时结果为不支持"""
    from management_portal.lineage_workflows import WorkflowFailed, WorkflowPending

    late = {**PENDING, "instruction_id": "inst_lin_after_assignment", "event_id": "event-late"}
    persisted, observations = [], []

    def invoke(**kwargs):
        kwargs["on_assigned"](PENDING)
        error_type = WorkflowFailed if rejected else WorkflowPending
        raise error_type(late, {})

    def resolve(**kwargs):
        observations.append(kwargs["handle"])
        assert kwargs["handle"]["instruction_id"] == late["instruction_id"]
        return ACCEPTED

    adapter = TauAdapter(config, invoke, lambda _: _verified_dev_target(),
                         resolve_submission=resolve, resume=lambda **_: VERDICT,
                         cancel_invocation=lambda *args: pytest.fail("no unrelated cancellation"))
    outcome = execute(adapter, tmp_path, on_submitted=persisted.append)
    assert outcome.status == ("unsupported" if rejected else "passed")
    assert any(handle.get("instruction_id") == late["instruction_id"] for handle in persisted)
    assert bool(observations) is not rejected
    assert adapter.cleanup_ready(run_dir=tmp_path / "evidence")


def test_campaign_process_exit_recovers_through_real_invocation_resolver(tmp_path, config, monkeypatch):
    """TriggerEvent 受理后执行器退出，恢复时经触发路径找到实际消息且只触发一次"""
    from management_portal import isa_client, resident_handoff

    binding = {key: PENDING[key] for key in ("resident_id", "lineage_id", "event_source_id", "effect_id")}
    instruction_id, path_id, actual_id = "inst_lin_original_accepted", "path-original", ACCEPTED["message_id"]
    path = {"trigger_path_id": path_id, "lineage_id": binding["lineage_id"], "root_kind": "event",
            "root_node_id": binding["event_source_id"], "occurrence_id": "event-original",
            "cause_kind": "instruction", "cause_id": instruction_id}
    calls, prepared, cleaned = [], [], []
    original_key = None

    def call(actor, name, arguments=None, **options):
        nonlocal original_key
        assert actor == "test-actor"
        calls.append(name)
        if name == "TriggerEvent":
            assert original_key is None, "a recovered request must never trigger twice"
            original_key = options["key"]
            raise SystemExit("process exits after acceptance, before any receipt")
        if name == "GetInstructionExecution":
            if "instruction_id" in arguments:
                assert arguments["instruction_id"] == instruction_id
            else:
                assert arguments["instruction"] == "TriggerEvent"
                assert arguments["idempotency_key"] == original_key
            return {"instruction_id": instruction_id, "instruction": "TriggerEvent", "status": "failed",
                    "references": {}, "error": {"commit_state": "unknown", "code": "instruction.failed"}}
        if name == "ListTriggerPaths":
            return {"paths": [path]}
        if name == "GetTriggerPath":
            assert arguments["trigger_path_id"] == path_id
            return {"path": path, "steps": {"items": [
                {"step_id": "effect-step", "kind": "effect", "node_id": binding["effect_id"]},
                {"step_id": "resident-step", "kind": "resident", "node_id": binding["resident_id"],
                 "input_message_id": actual_id}], "next_offset": None},
                "edges": {"items": [{"source_step_id": "effect-step", "target_step_id": "resident-step"}],
                          "next_offset": None}}
        if name == "GetInputState":
            assert arguments["message_id"] == actual_id
            return {"input_state": {"message_id": actual_id, "state": "completed"}}
        pytest.fail(f"unexpected instruction: {name}")

    def resume(**kwargs):
        assert kwargs["message_id"] == actual_id
        return VERDICT

    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(resident_handoff, "ensure_invocation_resident", lambda **_: binding)
    monkeypatch.setattr(resident_handoff, "resume_and_wait", resume)
    monkeypatch.setattr(TauCampaignExecutor, "_verify_source", lambda _self, _item: None)
    adapter = TauAdapter(config,
        lambda **kwargs: resident_handoff.run_resident_turn(**kwargs, workflow_binding=binding, resident_binding=binding),
        lambda _: _verified_dev_target(), resume=resume)
    monkeypatch.setattr(adapter, "cleanup", lambda **_: cleaned.append(True))
    monkeypatch.setattr(adapter, "finalize_group", lambda **_: {"status": "complete"})
    plan = {"schema": "tfi.test-run-plan.v2", "executable": True, "campaign": "tau",
            "selected": [PLANNED], "execution": {"mode": "parallel-modules", "groups": [{
                "group_id": "module:platform.core", "module_id": "platform.core", "runner": "tau",
                "item_indexes": [0], "continue_on_failure": True}]}}
    records = RunRecords(tmp_path / "runs.json")
    run_id = records.create(plan, "requester")["run_id"]
    executor = TauCampaignExecutor(records, tmp_path / "artifacts", adapter)
    with pytest.raises(SystemExit):
        executor.run(run_id)
    record = records.get(run_id)
    original = record["groups"][0]["active_invocation"]
    assert original["submission_state"] == "pending" and not original.get("message_id")
    assert original["request_id"] and original["idempotency_key"] == original_key
    assert not cleaned and record["status"] == "running"
    records.update(run_id, {"executor": {**record["executor"], "pid": 999_999_999}})
    assert executor.run(run_id) == "passed", records.get(run_id).get("error")
    assert calls.count("TriggerEvent") == 1 and calls.count("GetInstructionExecution") >= 1
    assert cleaned == [True]
    assert not records.get(run_id)["groups"][0]["active_invocation"]
