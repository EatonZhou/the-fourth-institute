""":importance: P2"""
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution.codex_quota import quota_sample, quota_state
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.account_monitor import AccountMonitor
from management_portal.execution.service import ExecutionService, ExecutionServiceServer


def sample(used, secondary=None):
    return quota_sample({"rateLimits": {"primary": {"usedPercent": used, "resetsAt": 1}, "secondary": secondary}}, observed_at=time.time())


@pytest.mark.parametrize("used,expected", [(97.9, "available"), (98, "reserved"), (99, "reserved"), (100, "reserved"), (None, "unknown"), (True, "unknown"), (float("nan"), "unknown"), (-1, "unknown")])
def test_quota_threshold(used, expected):
    """已用额度达到 98% 时进入保留，读数无效时状态为未知"""
    assert sample(used)["status"] == expected


def test_either_window_triggers_and_reset_timestamp_never_implies_recovery():
    """任一额度窗口达到保留线即暂停调度，重置时间已过也不视为已恢复"""
    observation = sample(20, {"usedPercent": 99, "resetsAt": 1})
    assert observation["remaining_percent"] == 1
    assert quota_state(observation, now=time.time() + 3600)["automatic_paused"]
    assert quota_state(sample(20), now=time.time() + 61)["status"] == "unknown"
    assert quota_sample({"rateLimits": {"primary": None}}, observed_at=time.time())["status"] == "unknown"


def test_multi_bucket_uses_account_bucket():
    """有多个额度桶时按 codex 账户自己的额度桶判断是否保留"""
    observation = quota_sample({"rateLimits": {"primary": {"usedPercent": 10}}, "rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": 98}}}}, observed_at=time.time())
    assert observation["status"] == "reserved"


@pytest.mark.parametrize("payload,exhausted", [
    ({"ordinaryUsageAllowed": False}, True),
    ({"ordinaryUsageAllowed": True, "rateLimitReachedType": "primary"}, True),
    ({"ordinaryUsageAllowed": True}, False),
    ({}, False),
])
def test_codex_reports_a_used_up_plan_and_its_credits(payload, exhausted):
    """Codex 套餐额度用完时样本标为已用完，并单独读出 credits 余额

    After the plan runs out Codex keeps working on paid credits, so the
    sample must pause the account even with a zero Close threshold.
    """
    from management_portal.execution.codex_quota import credits

    limits = {"limitId": "codex", "primary": {"usedPercent": 30, "resetsAt": 1},
              "rateLimitReachedType": payload.pop("rateLimitReachedType", None),
              "credits": {"hasCredits": True, "unlimited": False, "balance": "61988.9493440000"}}
    reading = {**payload, "rateLimits": limits, "rateLimitsByLimitId": {"codex": limits}}
    observation = quota_sample(reading, observed_at=time.time())
    assert observation["exhausted"] is exhausted
    assert observation["status"] == ("reserved" if exhausted else "available")
    assert credits(reading) == {"balance": 61988.949344, "has_credits": True, "unlimited": False}
    assert credits({"rateLimits": {"primary": {"usedPercent": 1}}}) is None
    assert credits({"rateLimits": {"credits": {"balance": "nan"}}})["balance"] is None


@pytest.fixture
def scheduler(tmp_path):
    result = Scheduler(quota_reserve_enabled=True, capacity_limits={"codex:one": 8, "codex:two": 8}, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    with result._connect() as connection:
        connection.execute("DELETE FROM execution_store_meta WHERE key LIKE 'quota:%%' OR key LIKE 'quota_override:%%'")
        connection.commit()
    result.register_worker(worker_id="quota-worker", build_id="test", capabilities=["agent_session:codex"])
    return result


def submit(scheduler, *, account="one", human=False, operation="turn", binding_id="", context_source_binding_id="", metadata=None):
    session = SessionOptions(
        mode="persistent" if binding_id else "stateless", binding_id=binding_id,
        backend_route_id=account, context_source_binding_id=context_source_binding_id,
    )
    request = ExecutionRequest.new(actor="alice", prompt="test", service_type="agent_session", provider="codex", operation=operation, session=session, metadata=metadata or {"human_initiated": human})
    scheduler.submit(request)
    return request


def claim(scheduler):
    return scheduler.claim(worker_id="quota-worker", capabilities=["agent_session:codex"])


def test_unknown_blocks_even_requests_claiming_human_origin(scheduler):
    """额度未知时连标为人工发起的请求也不调度"""
    automatic = submit(scheduler)
    assert claim(scheduler)["reason"] == "quota_reserve"
    assert scheduler.snapshot(automatic.execution_id)["scheduler"]["queue_reason"] == "quota_reserve"
    human = submit(scheduler, human=True)
    assert claim(scheduler)["reason"] == "quota_reserve"
    assert scheduler.snapshot(human.execution_id)["status"] == "queued"


def test_other_accounts_continue_and_fresh_recovery_releases_original_queue(scheduler):
    """一个账户进入额度保留时其他账户照常调度，额度恢复后放行原排队请求"""
    scheduler.update_account_quota("one", sample(98))
    scheduler.update_account_quota("two", sample(10))
    paused = submit(scheduler)
    healthy = submit(scheduler, account="two")
    assert claim(scheduler)["execution"]["execution_id"] == healthy.execution_id
    assert claim(scheduler)["reason"] == "quota_reserve"
    scheduler.update_account_quota("one", sample(50))
    assert claim(scheduler)["execution"]["execution_id"] == paused.execution_id


def test_running_requests_stop_regardless_of_actor_and_cancellation_is_idempotent(scheduler):
    """进入额度保留时停止所有进行中的执行，不论发起者，取消只记录一次"""
    scheduler.update_account_quota("one", sample(10))
    automatic = submit(scheduler)
    claim(scheduler)
    human = submit(scheduler, human=True)
    claim(scheduler)
    scheduler.update_account_quota("one", sample(98))
    scheduler.update_account_quota("one", sample(99))
    result = scheduler.snapshot(automatic.execution_id)
    assert result["scheduler"]["cancel_requested"]
    assert scheduler.snapshot(human.execution_id)["scheduler"]["cancel_requested"]
    events = [event for event in result["events"] if event["type"] == "cancellation_requested"]
    assert len(events) == 1


def test_quota_survives_service_restart_and_unknown_refresh(scheduler):
    """服务重启或刷新得到未知额度后，仍保持之前的额度保留"""
    scheduler.update_account_quota("one", sample(99))
    scheduler.update_account_quota("one", sample(None))
    restored = Scheduler(quota_reserve_enabled=True)
    submit(restored)
    assert claim(restored)["reason"] == "quota_reserve"


def test_fallback_cannot_escape_into_reserved_account(scheduler):
    """执行改派不能转到已进入额度保留的账户"""
    scheduler.update_account_quota("one", sample(10))
    scheduler.update_account_quota("two", sample(98))
    request = submit(scheduler)
    claimed = claim(scheduler)
    result = scheduler.reassign_capacity(request.execution_id, worker_id="quota-worker", claim_token=claimed["claim_token"], backend_route_id="two")
    assert result == {"accepted": False, "capacity_key": "codex:one", "changed": False, "reason": "quota_reserve"}
    assert scheduler.snapshot(request.execution_id)["request"]["session"]["backend_route_id"] == "one"


def test_fallback_rejects_full_account_without_moving_the_execution(scheduler):
    """目标账户容量已满时拒绝改派，执行留在原账户"""
    scheduler.update_account_quota("one", sample(10))
    scheduler.update_account_quota("two", sample(10))
    request = submit(scheduler)
    claimed = claim(scheduler)
    for _ in range(8):
        submit(scheduler, account="two")
        assert claim(scheduler)["claimed"]
    result = scheduler.reassign_capacity(request.execution_id, worker_id="quota-worker",
                                        claim_token=claimed["claim_token"], backend_route_id="two")
    assert result == {"accepted": False, "capacity_key": "codex:one", "changed": False, "reason": "capacity_full"}
    assert scheduler.snapshot(request.execution_id)["request"]["session"]["backend_route_id"] == "one"


def test_monitor_keeps_quota_independent_of_usage_and_retains_reserve_on_failure(scheduler):
    """账户监控分开读取额度和用量，读取失败时维持额度保留"""
    calls = []
    def read(method, params, **kwargs):
        calls.append(method)
        return {"rateLimits": {"primary": {"usedPercent": 99, "windowDurationMins": 10080, "resetsAt": 2000000000}}}
    monitor = AccountMonitor(scheduler.accounts, scheduler)
    account = scheduler.accounts.get("codex_one")
    with mock.patch("management_portal.codex_app_server_client.client_for_socket", return_value=SimpleNamespace(request=read)):
        monitor.refresh_account(account)
    assert calls == ["account/rateLimits/read", "account/usage/read"]
    window = scheduler.health()["capacity_partitions"]["codex:one"]["quota_reserve"]["windows"][0]
    assert window["window_duration_mins"] == 10080
    assert window["resets_at"] == 2000000000
    with mock.patch("management_portal.codex_app_server_client.client_for_socket", side_effect=TimeoutError()):
        monitor.refresh_account(account)
    submit(scheduler)
    assert claim(scheduler)["reason"] == "quota_reserve"


def test_health_exposes_scheduler_owned_quota_state(scheduler):
    """调度器健康状态显示各账户的额度保留状态和剩余百分比"""
    scheduler.update_account_quota("one", sample(98))
    state = scheduler.health()["capacity_partitions"]["codex:one"]["quota_reserve"]
    assert state["automatic_paused"]
    assert state["remaining_percent"] == 2


def quota_override(binding_id="resident:manual", *, revision=1, enabled=True):
    return {"binding_id": binding_id, "revision": revision, "enabled": enabled}


def test_quota_override_releases_existing_queue_without_granting_other_bindings(scheduler):
    """额度例外只放行该绑定的请求，其他绑定、fork 子请求和自带例外的请求仍排队"""
    scheduler.update_account_quota("one", sample(100))
    request = submit(scheduler, binding_id="resident:manual")
    other = submit(scheduler, binding_id="resident:other")
    child = submit(scheduler, binding_id="resident:child", context_source_binding_id="resident:manual", operation="fork")
    spoofed = submit(scheduler, binding_id="resident:spoofed", metadata={
        "human_initiated": True, "quota_reserve_override": quota_override(),
    })
    assert claim(scheduler)["reason"] == "quota_reserve"
    assert scheduler.configure_binding_quota_overrides([quota_override()]) == {"updated": 1}
    assert claim(scheduler)["execution"]["execution_id"] == request.execution_id
    for blocked in (other, child, spoofed):
        assert scheduler.snapshot(blocked.execution_id)["scheduler"]["queue_reason"] == "quota_reserve"


def test_quota_override_persists_for_future_messages_and_service_restarts(scheduler):
    """绑定的额度例外对后续消息和服务重启后的调度仍然有效"""
    scheduler.configure_binding_quota_overrides([quota_override()])
    scheduler.update_account_quota("one", sample(50))
    scheduler.update_account_quota("one", sample(100))
    scheduler.update_account_quota("one", sample(None))
    restored = Scheduler(quota_reserve_enabled=True, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    request = submit(restored, binding_id="resident:manual")
    assert claim(restored)["execution"]["execution_id"] == request.execution_id
    restored.update_account_quota("one", sample(100))
    assert not restored.snapshot(request.execution_id)["scheduler"]["cancel_requested"]


def test_quota_override_revocation_stops_active_work_once_and_cannot_replay_it(scheduler):
    """撤销额度例外时只取消一次进行中的执行，旧版本设置不能重新启用"""
    scheduler.configure_binding_quota_overrides([quota_override()])
    scheduler.update_account_quota("one", sample(100))
    request = submit(scheduler, binding_id="resident:manual")
    assert claim(scheduler)["claimed"]
    scheduler.update_account_quota("one", sample(100))
    assert not scheduler.snapshot(request.execution_id)["scheduler"]["cancel_requested"]
    revoked = quota_override(revision=2, enabled=False)
    assert scheduler.configure_binding_quota_overrides([revoked]) == {"updated": 1}
    assert scheduler.configure_binding_quota_overrides([revoked]) == {"updated": 0}
    assert scheduler.configure_binding_quota_overrides([quota_override()]) == {"updated": 0}
    scheduler.configure_binding_quota_overrides([quota_override(revision=3)])
    result = scheduler.snapshot(request.execution_id)
    assert result["scheduler"]["cancel_requested"]
    assert len([event for event in result["events"] if event["type"] == "cancellation_requested"]) == 1


def test_quota_override_revision_conflicts_are_atomic(scheduler):
    """额度例外版本冲突时整批设置都不生效"""
    scheduler.configure_binding_quota_overrides([quota_override(revision=2, enabled=False)])
    with pytest.raises(ValueError, match="revision conflict"):
        scheduler.configure_binding_quota_overrides([
            quota_override("resident:other"), quota_override(revision=2),
        ])
    for binding_id in ("resident:other", "resident:manual"):
        request = submit(scheduler, binding_id=binding_id)
        assert scheduler.snapshot(request.execution_id)["scheduler"]["queue_reason"] == "quota_reserve"


@pytest.mark.parametrize("invalid", [None, {}, [None], [quota_override(revision=True)], [quota_override(enabled="true")], [quota_override(), quota_override()], [{**quota_override(), "updated_by": "alice"}]])
def test_quota_override_rejects_invalid_control_messages(scheduler, invalid):
    """格式错误、重复或带伪造字段的额度例外设置被拒绝"""
    with pytest.raises(ValueError):
        scheduler.configure_binding_quota_overrides(invalid)


def test_quota_override_control_plane_dispatch_releases_queued_request(scheduler, tmp_path):
    """经控制面设置额度例外后立即放行已排队的请求"""
    request = submit(scheduler, binding_id="resident:manual")
    with ExecutionServiceServer(tmp_path / "s", scheduler) as server:
        transport = mock.Mock()
        transport.request.side_effect = lambda method, params, **kwargs: server.dispatch(method, params)
        service = ExecutionService(client=transport)
        assert service.configure_binding_quota_overrides([quota_override()]) == {"updated": 1}
        assert claim(scheduler)["execution"]["execution_id"] == request.execution_id


def test_deleted_bindings_quota_overrides_are_forgotten_through_the_control_plane(scheduler, tmp_path):
    """删除绑定后经控制面清除其额度例外，该绑定的请求重新受额度保留限制"""
    scheduler.update_account_quota("one", sample(100))
    scheduler.configure_binding_quota_overrides([quota_override()])
    with ExecutionServiceServer(tmp_path / "s", scheduler) as server:
        transport = mock.Mock()
        transport.request.side_effect = lambda method, params, **kwargs: server.dispatch(method, params)
        service = ExecutionService(client=transport)
        assert service.forget_binding_quota_overrides(["resident:manual"]) == {"removed": 1}
        assert service.forget_binding_quota_overrides(["resident:manual"]) == {"removed": 0}
    request = submit(scheduler, binding_id="resident:manual")
    assert claim(scheduler)["reason"] == "quota_reserve"
    assert scheduler.snapshot(request.execution_id)["scheduler"]["queue_reason"] == "quota_reserve"
    with pytest.raises(ValueError):
        scheduler.forget_binding_quota_overrides(["not a binding"])


def test_batch_control_states_match_scheduler_reasons_without_body_or_actor_leak(scheduler):
    """批量查询执行状态的排队原因与调度器一致，只返回所选用户的执行且不含请求内容

    :design: decision_6ad0384e020d
    :design-point:
    """
    scheduler.update_account_quota('one', sample(99))
    scheduler.update_account_quota('two', sample(10))
    reserved = submit(scheduler, binding_id='resident:reserved')
    healthy = submit(scheduler, account='two', binding_id='resident:healthy')
    hidden = submit(scheduler, account='two')
    selected = {reserved.execution_id: 'alice', healthy.execution_id: 'alice', hidden.execution_id: 'bob'}
    batch = scheduler.states(selected)
    assert set(batch) == {reserved.execution_id, healthy.execution_id}
    for key in batch:
        assert set(batch[key]) == {'execution_id', 'status', 'scheduler'}
        assert batch[key]['scheduler']['queue_reason'] == scheduler.snapshot(key)['scheduler']['queue_reason']
    assert batch[reserved.execution_id]['scheduler']['queue_reason'] == 'quota_reserve'
    assert batch[healthy.execution_id]['scheduler']['queue_reason'] == 'ready'
    claim(scheduler)
    assert scheduler.states(selected)[healthy.execution_id]['status'] == 'claimed'
    scheduler.cancel(healthy.execution_id, actor="alice")
    assert scheduler.states(selected)[healthy.execution_id]['scheduler']['cancel_requested']
