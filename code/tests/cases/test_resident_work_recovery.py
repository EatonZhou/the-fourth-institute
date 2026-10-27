"""Provider failures recover original inputs even when supervision is offline.

:module: resident.session
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal.execution import provider_egress, provider_failures
from management_portal.society_service import (
    family_facade,
    resident_backend,
    resident_conversation_stream,
    resident_execution_work,
    resident_input_checkpoints,
    resident_input_recovery,
    resident_restart_domain,
    resident_work_recovery as recovery,
)
from management_portal.society_service.domain_ports import execution_port
from management_portal.society_service.resident_mailbox import MailboxConflict
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401
from tests.cases.test_resident_mailbox_requeue import _record, _round
from tests.cases.test_session_restart import _restart_domain
from management_portal.society_service import resident_input_queue_domain, resident_record_domain


def _failed(team, monkeypatch, error="connection error"):
    resident_id = team.target["resident_id"]
    message_id, _ = _round(resident_id, state="failed")
    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(resident_id, message_id, "failed", error={"message": error})
    record = _record(resident_id)
    record["last_error"] = error
    resident_execution_work.clear(record)
    resident_record_domain.save_resident_record(record)
    resident_input_queue_domain.commit_resident_intake_state(record, "open")
    monkeypatch.setattr(resident_backend, "session_record", lambda _r: {})
    monkeypatch.setattr(provider_egress, "provider_network_state", lambda *_args: "allowed")
    return resident_id, message_id


def test_network_recovery_preserves_input_and_does_not_duplicate_it(team, monkeypatch):
    """网络恢复后失败的输入以原发送者和正文重新排队一次，不重复入队

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    resident_id, message_id = _failed(team, monkeypatch)
    assert recovery.reconcile_failed_work([_record(resident_id)]) == 1
    job = resident_execution_work.read(_record(resident_id))
    assert job["user_message_id"] == message_id and job["actor"] == ACTOR
    assert job["message"] == "整理本周的测试报告"
    assert recovery.reconcile_failed_work([_record(resident_id)]) == 0
    assert recovery.recovery_entry(_record(resident_id), message_id)["attempts"] == 1


def test_network_outage_waits_without_spending_recovery_attempts(team, monkeypatch):
    """网络不可达时等待且不消耗恢复次数，十分钟内不再探测

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    resident_id, message_id = _failed(team, monkeypatch)
    monkeypatch.setattr(provider_egress, "provider_network_state", lambda *_: "unreachable")
    assert recovery.reconcile_failed_work([_record(resident_id)]) == 0
    entry = recovery.recovery_entry(_record(resident_id), message_id)
    assert entry.get("attempts", 0) == 0 and entry["checked_at"] > 0
    monkeypatch.setattr(provider_egress, "provider_network_state", lambda *_: pytest.fail("must wait ten minutes"))
    assert recovery.reconcile_failed_work([_record(resident_id)]) == 0


@pytest.mark.parametrize("boundary", ["closed", "cancelled", "disabled", "active"])
def test_recovery_respects_user_and_execution_boundaries(team, monkeypatch, boundary):
    """intake 关闭、输入已取消、恢复已禁用或已有执行时不自动恢复失败输入

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    resident_id, message_id = _failed(team, monkeypatch)
    record = _record(resident_id)
    if boundary == "closed":
        resident_input_queue_domain.commit_resident_intake_state(record, "closed")
    elif boundary == "cancelled":
        resident_conversation_stream.get_repository().reopen_input(resident_id, message_id)
        resident_conversation_stream.get_repository().set_input_state(resident_id, message_id, "cancelled")
    elif boundary == "disabled":
        recovery.set_recovery_entry(record, message_id, disabled=True)
        resident_record_domain.save_resident_record(record)
    else:
        resident_input_recovery.requeue(resident_id, ACTOR, message_id)
    monkeypatch.setattr(provider_egress, "provider_network_state", lambda *_: pytest.fail("boundary must precede probe"))
    assert recovery.reconcile_failed_work([_record(resident_id)]) == 0


@pytest.mark.parametrize("caller", ["user", "resident"])
def test_automatic_limit_preserves_budget_but_allows_explicit_requeue(team, monkeypatch, caller):
    """自动恢复达到两次上限后被拒绝，用户或 Resident 仍可用 RequeueMessage 手动放回

    :module: resident.identity
    :importance: P2
    :aspects: functional
    """
    resident_id, message_id = _failed(team, monkeypatch)
    for _ in range(2):
        resident_input_recovery.requeue(resident_id, resident_id, message_id,
                                       portal_reason="environment_recovery", automatic=True)
        record = _record(resident_id)
        resident_input_checkpoints.retain_input_checkpoint(record, resident_execution_work.read(record), [])
        resident_execution_work.clear(record)
        resident_conversation_stream.get_repository().set_input_state(resident_id, message_id, "failed")
    with pytest.raises(MailboxConflict, match="两次"):
        resident_input_recovery.requeue(resident_id, resident_id, message_id,
                                       portal_reason="environment_recovery", automatic=True)
    actor = ACTOR if caller == "user" else team.source["resident_id"]
    result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": message_id}, actor=actor)
    assert result.status == "committed", result
    job = resident_execution_work.read(_record(resident_id))
    assert job["user_message_id"] == message_id and job["actor"] == ACTOR
    assert job["message"] == "整理本周的测试报告"
    assert recovery.recovery_entry(_record(resident_id), message_id)["attempts"] == 2


def test_explicit_resident_requeue_does_not_spend_automatic_budget(team, monkeypatch):
    """Resident 用 RequeueMessage 手动放回不消耗自动恢复次数

    :module: resident.identity
    :importance: P2
    :aspects: functional
    """
    resident_id, message_id = _failed(team, monkeypatch)
    result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": message_id},
                         actor=team.source["resident_id"])
    assert result.status == "committed", result
    assert recovery.recovery_entry(_record(resident_id), message_id).get("attempts", 0) == 0


def _account(provider, route, remaining, **quota):
    return {"account_id": provider + "_" + route, "provider": provider, "backend_route_id": route,
            "quota_reserve": {"status": "available", "fresh": True, "remaining": remaining,
                              "windows": [{"window": "seven_day", "remaining_percent": remaining}], **quota}}


def test_steward_quota_failure_migrates_without_calling_its_model(team, monkeypatch):
    """steward 额度耗尽时直接重启到有额度的账号并续跑失败输入，不调用其模型

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    resident_id, message_id = _failed(team, monkeypatch, "You've hit your weekly limit")
    record = _record(resident_id)
    record["backend_route_id"] = "one"
    resident_record_domain.save_resident_record(record)
    monkeypatch.setattr(family_facade, "is_operating_role", lambda r: r.get("resident_id") == resident_id)
    accounts = [_account("codex", "one", 0), _account("claude", "claude_one", 90), _account("codex", "two", 80)]
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(accounts=lambda: {"accounts": accounts}))
    calls = []
    def restart(target, actor, payload, **kwargs):
        calls.append((target, actor, payload, kwargs))
        return {"status": "accepted"}
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", restart)
    assert recovery.reconcile_failed_work([_record(resident_id)]) == 1
    target, actor, payload, kwargs = calls[0]
    assert target == actor == resident_id
    assert payload["target_provider"] == "codex" and payload["target_account"] == "two"
    assert payload["resume_failed_input"] is True and payload["context_strategy"] == "compact"
    assert kwargs["portal_reason"] == "steward_quota_recovery"
    assert recovery.recovery_entry(_record(resident_id), message_id)["switch_attempts"] == 1


def test_scheduler_quota_wait_is_not_recovered_as_a_failed_round(team, monkeypatch):
    """调度器因额度预留而排队等待的执行不当作失败轮次恢复

    :module: resident.identity
    :importance: P2
    :aspects: functional
    """
    resident_id, message_id = _failed(team, monkeypatch)
    resident_input_recovery.requeue(resident_id, ACTOR, message_id)
    record = _record(resident_id)
    before = resident_execution_work.read(record)
    monkeypatch.setattr(family_facade, "is_operating_role", lambda _: True)
    monkeypatch.setattr(resident_execution_work, "execution_snapshot", lambda _: {
        "status": "queued", "scheduler": {"queue_reason": "quota_reserve"},
    })
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session",
                        lambda *a, **k: pytest.fail("Scheduler owns an admitted waiting execution"))
    assert recovery.reconcile_failed_work([record]) == 0
    assert resident_execution_work.read(record) == before
    assert record.get("intake_open") is not False


def test_fallback_requires_fresh_positive_agent_quota_and_can_change_provider():
    """备用账号须有最新且为正的额度，可以换到其他 Provider，已试过的账号跳过

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    accounts = [_account("codex", "one", 0), _account("codex", "two", 99, fresh=False),
                _account("codex", "three", True), _account("claude", "claude_one", 50)]
    assert recovery.fallback_account(accounts, "codex", "one", [])["provider"] == "claude"
    assert recovery.fallback_account(accounts, "codex", "one", ["claude_claude_one"]) == {}


@pytest.mark.parametrize("automatic", [True, False])
def test_successful_restart_resumes_the_original_failed_round_once(team, monkeypatch, automatic):
    """重启成功后原失败轮次只续跑一次，仅自动恢复计入次数

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    resident_id, message_id = _failed(team, monkeypatch)
    state = {"resume_message_id": message_id}
    if automatic:
        state["reason"] = "steward_quota_recovery"
    recovery.resume_after_restart(_record(resident_id), state)
    assert state["resumed_message_id"] == message_id
    recovery.resume_after_restart(_record(resident_id), state)
    assert "resume_error" in state
    assert recovery.recovery_entry(_record(resident_id), message_id).get("attempts", 0) == int(automatic)


@pytest.mark.parametrize("automatic", [True, False])
def test_restart_recovery_at_automatic_limit_depends_on_origin(team, monkeypatch, automatic):
    """自动恢复已达上限时，自动重启不再续跑失败输入，用户发起的重启仍续跑

    :module: resident.identity
    :importance: P2
    :aspects: functional
    """
    resident_id, message_id = _failed(team, monkeypatch)
    record = _record(resident_id)
    recovery.set_recovery_entry(record, message_id, attempts=2)
    resident_record_domain.save_resident_record(record)
    state = {"resume_message_id": message_id}
    if automatic:
        state["reason"] = "steward_quota_recovery"
    recovery.resume_after_restart(_record(resident_id), state)
    if automatic:
        assert "两次" in state["resume_error"]
        assert not resident_execution_work.read(_record(resident_id))
    else:
        assert state["resumed_message_id"] == message_id
        job = resident_execution_work.read(_record(resident_id))
        assert job["user_message_id"] == message_id and job["actor"] == ACTOR
    assert recovery.recovery_entry(_record(resident_id), message_id)["attempts"] == 2


@pytest.mark.parametrize("message", ["You've hit your weekly limit · resets Oct 9", "You've hit your limit"])
def test_claude_subscription_limits_are_quota_failures(message):
    """claude 订阅的周限额和用量上限提示被识别为额度失败

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    assert provider_failures.is_usage_limit_failure({"message": message})


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("automatic", [True, False])
def test_restart_records_recovery_choice_and_only_resumes_after_promotion(monkeypatch, enabled, automatic):
    """会话重启记录是否续跑失败输入，新会话启用后才续跑

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    record, operations, _saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(recovery, "latest_failed_input", lambda _: {"message_id": "msg_original"})
    calls = []
    monkeypatch.setattr(recovery, "resume_after_restart", lambda current, state: calls.append(dict(state)))
    monkeypatch.setattr(recovery, "set_recovery_entry", lambda *args, **kwargs: None)
    state = resident_restart_domain.restart_resident_session(record["resident_id"], "alice", {
        "target_provider": "claude", "model": "deepseek-v4-pro",
        "context_strategy": "compact", "resume_failed_input": enabled,
    }, portal_reason="steward_quota_recovery" if automatic else "")
    assert calls == [] and state["status"] == "accepted"
    assert state["resume_message_id"] == ("msg_original" if enabled else "")
    resident_restart_domain.resume_resident_session_restart(operations[-1]["step"])
    assert record["backend"] == "claude" and record["session_restart"]["status"] == "completed"
    if enabled:
        assert len(calls) == 1 and calls[0]["resume_message_id"] == state["resume_message_id"]
        assert bool(calls[0].get("reason")) == automatic
    else:
        assert calls == []


@pytest.mark.parametrize("automatic", [True, False])
def test_configuration_only_restart_preserves_recovery_origin(monkeypatch, automatic):
    """只改配置的重启立即续跑失败输入，并保留是否自动恢复的来源

    :module: resident.identity
    :importance: P2
    :aspects: functional
    """
    record, operations, _saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(resident_backend, "session_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(recovery, "latest_failed_input", lambda _: {"message_id": "msg_original"})
    calls = []
    monkeypatch.setattr(recovery, "resume_after_restart", lambda current, state: calls.append(dict(state)))
    state = resident_restart_domain.restart_resident_session(record["resident_id"], "alice", {
        "target_provider": "claude", "context_strategy": "fresh", "resume_failed_input": True,
    }, portal_reason="steward_quota_recovery" if automatic else "")
    assert state["configuration_only"] and not operations
    assert len(calls) == 1 and calls[0]["resume_message_id"] == "msg_original"
    assert bool(calls[0].get("reason")) == automatic


def test_failed_session_initialization_preserves_input_and_original_provider(monkeypatch):
    """新会话初始化失败时保留原 Provider，不重新排队失败输入

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    record, operations, _saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(recovery, "latest_failed_input", lambda _: {"message_id": "msg_original"})
    monkeypatch.setattr(recovery, "resume_after_restart", lambda *_: pytest.fail("failed migration must not requeue"))
    resident_restart_domain.restart_resident_session(record["resident_id"], "alice", {
        "target_provider": "claude", "context_strategy": "compact",
    })
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(
        resume=lambda *args, **kwargs: {"status": "failed", "result": {"status": "failed", "error": "network outage"}},
    ))
    resident_restart_domain.resume_resident_session_restart(operations[-1]["step"])
    assert record["backend"] == "codex" and record["session_restart"]["status"] == "failed"


def test_steward_migration_intent_survives_interruption_without_new_request(team, monkeypatch):
    """steward 迁移账号被中断后，下次按相同请求重试，切换次数只计一次

    :design: decision_44cf587c1721, decision_c47a13e8b962
    :design-point:
    """
    resident_id, message_id = _failed(team, monkeypatch, "You've hit your weekly limit")
    monkeypatch.setattr(family_facade, "is_operating_role", lambda r: r.get("resident_id") == resident_id)
    accounts = [_account("codex", "one", 0), _account("codex", "two", 80)]
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(accounts=lambda: {"accounts": accounts}))
    calls = []
    def restart(target, actor, payload, **kwargs):
        calls.append(dict(payload))
        if len(calls) == 1:
            raise RuntimeError("simulated service interruption")
        return {"status": "accepted"}
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", restart)
    assert recovery.reconcile_failed_work([_record(resident_id)]) == 0
    record = _record(resident_id)
    assert recovery.recovery_entry(record, message_id)["pending_restart"]
    recovery.set_recovery_entry(record, message_id, checked_at=0)
    resident_record_domain.save_resident_record(record)
    assert recovery.reconcile_failed_work([_record(resident_id)]) == 1
    assert calls[0] == calls[1]
    assert recovery.recovery_entry(_record(resident_id), message_id)["switch_attempts"] == 1
