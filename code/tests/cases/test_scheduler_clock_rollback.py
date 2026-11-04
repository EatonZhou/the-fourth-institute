"""墙钟回拨后的派发节奏与公平记录恢复。

:module: execution.service
:design: decision_8ac0b5a6c0e5
:profiles: local.fixture
:risk: no-external-side-effects
"""
import pytest

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler

pytestmark = pytest.mark.usefixtures('registered_execution_defaults')


def prepare(monkeypatch, interval):
    clock = [1000.0]
    monkeypatch.setattr('time.time', lambda: clock[0])
    scheduler = Scheduler(capacity_limits={'codex:zero': 10},
                          dispatch_interval_min_seconds=interval, dispatch_interval_max_seconds=interval)
    scheduler.register_worker(worker_id='clock-worker', build_id='clock-build', capabilities=['agent_session:codex'])
    requests = []
    for i in range(2):
        r = ExecutionRequest.new(actor=f'clock-actor-{i}', prompt='clock probe', service_type='agent_session',
            provider='codex', operation='turn', session=SessionOptions(mode='persistent', binding_id=f'clock:{i}', backend_route_id='zero'))
        scheduler.submit(r);requests.append(r)
    assert scheduler.claim(worker_id='clock-worker', capabilities=['agent_session:codex'])['claimed']
    return clock, scheduler, requests


def test_maintenance_rebases_future_dispatch_once_and_preserves_normal_interval(monkeypatch):
    """回拨后保留原输入和完整限速等待，读取不恢复，维护只重定位一次。"""
    clock, scheduler, requests = prepare(monkeypatch, 4)
    clock[0] = 100.0
    assert scheduler.health()['dispatch_interval']['partitions']['codex:zero']['remaining_seconds'] == 904
    assert scheduler.claim(worker_id='clock-worker', capabilities=['agent_session:codex']) == {'claimed':False, 'reason':'dispatch_interval'}
    recovered = scheduler.maintain()
    assert recovered['rebased_dispatch_windows'] == 1
    assert recovered['rebased_actor_dispatches'] == 1
    assert scheduler.health()['dispatch_interval']['partitions']['codex:zero']['remaining_seconds'] == 4
    clock[0] = 102.0
    assert scheduler.maintain()['rebased_dispatch_windows'] == 0
    assert scheduler.health()['dispatch_interval']['partitions']['codex:zero']['remaining_seconds'] == 2
    assert not scheduler.claim(worker_id='clock-worker', capabilities=['agent_session:codex'])['claimed']
    clock[0] = 104.1
    claimed = scheduler.claim(worker_id='clock-worker', capabilities=['agent_session:codex'])
    assert claimed['claimed'] and claimed['execution']['execution_id'] == requests[1].execution_id
    assert scheduler.health()['dispatch_interval']['partitions']['codex:zero']['remaining_seconds'] == 4


def test_legitimate_long_interval_and_small_sampling_skew_are_not_relaxed(monkeypatch):
    """合法长间隔与一秒内的并发采样偏差保持原窗口。"""
    clock, scheduler, _requests = prepare(monkeypatch, 3600)
    clock[0] = 999.5
    assert scheduler.maintain()['rebased_dispatch_windows'] == 0
    assert scheduler.health()['dispatch_interval']['partitions']['codex:zero']['remaining_seconds'] == 3600.5
    clock[0] = 1001.0
    assert scheduler.maintain()['rebased_dispatch_windows'] == 0
    assert not scheduler.claim(worker_id='clock-worker', capabilities=['agent_session:codex'])['claimed']
