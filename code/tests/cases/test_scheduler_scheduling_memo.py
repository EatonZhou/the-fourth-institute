"""Claims and queue positions read only the scheduling fields of active executions.

request_json carries the whole prompt, often hundreds of KB.  The scheduler keeps
the route and binding of each active execution and re-reads request_json only when
(updated_at, request size) changes.

:module: execution.service
:aspects: functional, performance
"""

from management_common.json_columns import json_value
from management_portal.execution import scheduler_capacity
from management_portal.execution.contracts import ExecutionRequest, SessionOptions, execution_timestamp
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.scheduler_rows import compact_json

import pytest


@pytest.fixture
def scheduler():
    result = Scheduler(
        capacity_limits={"codex:one": 8, "codex:two": 8},
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    result.register_worker(worker_id="memo-worker", build_id="test", capabilities=["agent_session:codex"])
    return result


def submit(scheduler, *, account="one", binding_id="", prompt="test"):
    session = SessionOptions(
        mode="persistent" if binding_id else "stateless",
        binding_id=binding_id,
        backend_route_id=account,
    )
    request = ExecutionRequest.new(
        actor="alice", prompt=prompt, service_type="agent_session", provider="codex",
        operation="turn", session=session,
    )
    scheduler.submit(request)
    return request


def claim(scheduler):
    return scheduler.claim(worker_id="memo-worker", capabilities=["agent_session:codex"])


def test_scheduling_request_keeps_only_route_and_binding():
    """调度只保留执行请求中的路由与绑定字段

    验证 ExecutionService 调度器：调度只保留执行请求中的路由与绑定字段。
    """
    slim = scheduler_capacity._scheduling_request({
        "service_type": "agent_session", "operation": "turn", "provider": "codex",
        "prompt": "x" * 100_000, "runtime_options": {"project_id": "p"},
        "session": {"backend_route_id": "one", "binding_id": "b1", "backend_session_id": "s", "context": ["y" * 1000]},
    })
    assert slim == {
        "service_type": "agent_session", "operation": "turn", "provider": "codex",
        "runtime_options": {"project_id": "p"},
        "session": {"backend_route_id": "one", "binding_id": "b1"},
    }


def test_active_requests_are_parsed_once_until_they_change(scheduler, monkeypatch):
    """活跃执行的请求在变化前只解析一次

    验证 ExecutionService 调度器：活跃执行的请求在变化前只解析一次。
    """
    submit(scheduler, account="one", binding_id="binding-a", prompt="p" * 200_000)
    submit(scheduler, account="one", binding_id="binding-b", prompt="q" * 200_000)
    assert claim(scheduler)["claimed"] and claim(scheduler)["claimed"]

    # Claiming the second execution already remembered the first one.
    scheduler.active_requests._scheduling_memo.clear()
    parsed = []
    original = scheduler_capacity._scheduling_request

    def counting(request):
        parsed.append(request.get("session", {}).get("binding_id"))
        return original(request)

    monkeypatch.setattr(scheduler_capacity, "_scheduling_request", counting)
    with scheduler.changes.connect() as connection:
        first = scheduler.active_requests.scheduling_requests(connection)
        second = scheduler.active_requests.scheduling_requests(connection)
    assert sorted(request["session"]["binding_id"] for _, request in first) == ["binding-a", "binding-b"]
    assert second == first
    assert sorted(parsed) == ["binding-a", "binding-b"]


def test_a_same_length_route_change_is_seen(scheduler):
    """长度相同的路由变更也会刷新调度缓存

    验证 ExecutionService 调度器：长度相同的路由变更也会刷新调度缓存。
    """
    request = submit(scheduler, account="one", binding_id="binding-a")
    assert claim(scheduler)["claimed"]
    with scheduler.changes.connect() as connection:
        [(_, before)] = scheduler.active_requests.scheduling_requests(connection)
        assert scheduler.capacity.capacity_key(before) == "codex:one"
        # One UPDATE path moves an execution to another route of the same length.
        row = connection.execute(
            "SELECT request_json FROM executions WHERE execution_id = %s", (request.execution_id,),
        ).fetchone()
        stored = compact_json(json_value(row["request_json"]))
        rerouted = stored.replace('"backend_route_id":"one"', '"backend_route_id":"two"')
        assert rerouted != stored and len(rerouted) == len(stored)
        connection.execute(
            "UPDATE executions SET request_json = %s, updated_at = %s WHERE execution_id = %s",
            (rerouted, execution_timestamp(), request.execution_id),
        )
        connection.commit()
        [(_, after)] = scheduler.active_requests.scheduling_requests(connection)
    assert scheduler.capacity.capacity_key(after) == "codex:two"


def test_finished_executions_leave_the_memo(scheduler):
    """结束的执行移出调度缓存

    验证 ExecutionService 调度器：结束的执行移出调度缓存。
    """
    submit(scheduler, account="one", binding_id="binding-a")
    claimed = claim(scheduler)
    with scheduler.changes.connect() as connection:
        assert len(scheduler.active_requests.scheduling_requests(connection)) == 1
        connection.execute(
            "UPDATE executions SET status = 'completed', updated_at = %s WHERE execution_id = %s",
            (execution_timestamp(), claimed["execution"]["execution_id"]),
        )
        connection.commit()
        assert scheduler.active_requests.scheduling_requests(connection) == []
    assert scheduler.active_requests._scheduling_memo == {}
