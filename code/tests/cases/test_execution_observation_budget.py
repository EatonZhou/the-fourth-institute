from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.accounts import ExecutionAccounts
from management_portal.execution.contracts import ExecutionRequest, ExecutionResult
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.scheduler_views import SchedulerViews
from management_portal.execution.service import ExecutionService, ExecutionServiceServer, ExecutionWaitTimeout
from management_portal.execution.transport import ExecutionServiceClient, ExecutionServiceUnavailable


@pytest.fixture
def control_plane(tmp_path):
    scheduler = Scheduler(dispatch_interval_min_seconds=0,
                          dispatch_interval_max_seconds=0)
    preparation = Mock()
    preparation.prepare.side_effect = lambda request: request
    preparation.accounts = ExecutionAccounts(())  # the server reads the execution accounts from preparation
    server = ExecutionServiceServer(
        tmp_path / "execution.sock", scheduler, instance="test", build_id="test",
        preparation=preparation, agent_data=AgentDataStore(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(1)
    service = ExecutionService(client=ExecutionServiceClient(tmp_path / "execution.sock"))
    worker = "test:observation"
    scheduler.register_worker(worker_id=worker, build_id="test", capabilities=["chat_completion"])
    try:
        yield SimpleNamespace(scheduler=scheduler, service=service, worker=worker)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def start(plane):
    claim = plane.scheduler.claim(worker_id=plane.worker, capabilities=["chat_completion"])
    assert claim["claimed"]
    execution_id = claim["execution"]["execution_id"]
    plane.scheduler.start(execution_id, worker_id=plane.worker, claim_token=claim["claim_token"])
    return execution_id, claim["claim_token"]


def finish(plane, execution_id, token, terminal):
    plane.scheduler.finish(
        ExecutionResult(execution_id=execution_id, status=terminal,
                        output={"message": "completed original batch"} if terminal == "completed" else {},
                        error="provider read timed out" if terminal == "failed" else ""),
        worker_id=plane.worker, claim_token=token,
    )


@pytest.mark.parametrize("phase", ["queued", "running"])
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_observation_timeout_preserves_execution_then_reconnects(control_plane, phase, terminal):
    """等待执行超时只结束观察，不取消执行，之后重新等待可拿到终态结果"""
    plane = control_plane
    request = ExecutionRequest.new(actor="alice", prompt="durable original request",
                                   service_type="chat_completion", provider="openai", operation="generate")
    plane.scheduler.submit(request)
    if phase == "running":
        execution_id, token = start(plane)
    with pytest.raises(ExecutionWaitTimeout) as caught:
        plane.service.wait(request.execution_id, actor="alice", timeout_seconds=0.1)
    assert caught.value.execution_id == request.execution_id
    assert caught.value.snapshot["status"] == phase
    current = plane.scheduler.snapshot(request.execution_id)
    assert current["status"] == phase
    assert not current["scheduler"]["cancel_requested"]
    assert not current["result"]
    if phase == "queued":
        execution_id, token = start(plane)
    if terminal == "cancelled":
        plane.service.cancel(execution_id, actor="alice", reason="explicit Stop after observation timeout")
    finish(plane, execution_id, token, "completed" if terminal == "cancelled" else terminal)
    observed = plane.service.wait(execution_id, actor="alice", timeout_seconds=0.5)
    assert observed["status"] == terminal
    assert observed["result"]["execution_id"] == request.execution_id
    assert observed["scheduler"]["attempts"] == 1
    if terminal == "failed":
        assert observed["result"]["error"] == "provider read timed out"


@pytest.mark.parametrize("response_unknown", [False, True])
def test_unavailable_control_plane_exhausts_observation_without_cancel(response_unknown):
    """控制面不可用时等待到时限后报超时而不取消，恢复后按事件序号续等，提交按原请求重发"""
    from management_portal.execution.transport import ExecutionResponseUnknown
    transport = Mock()
    error = ExecutionResponseUnknown if response_unknown else ExecutionServiceUnavailable
    transport.request.side_effect = error("control socket replaced")
    with pytest.raises(ExecutionWaitTimeout) as caught:
        ExecutionService(client=transport).wait("original-execution", actor="alice", timeout_seconds=0.1)
    assert caught.value.execution_id == "original-execution"
    assert caught.value.snapshot == {}
    assert caught.value.control_plane_error == "control socket replaced"
    assert all(call.args[0] == "execution.wait" for call in transport.request.call_args_list)

    transport.reset_mock()
    transport.request.side_effect = [
        {"status": "running", "events": [{"sequence": 7, "type": "provider_output"}]},
        error("reply lost during replacement"),
        {"status": "completed", "result": {"status": "completed"}},
    ]
    result = ExecutionService(client=transport).wait("original-execution", actor="alice", timeout_seconds=2)
    assert result["status"] == "completed"
    calls = transport.request.call_args_list
    assert [call.args[1]["after_sequence"] for call in calls] == [0, 7, 7]
    assert {call.args[1]["execution_id"] for call in calls} == {"original-execution"}

    transport.reset_mock()
    request = ExecutionRequest.new(actor="alice", prompt="original request",
                                   service_type="chat_completion", provider="openai", operation="generate")
    transport.request.side_effect = [error("submit receipt lost"), {"execution_id": request.execution_id, "status": "queued"}]
    submitted = ExecutionService(client=transport).submit(request)
    assert submitted["execution_id"] == request.execution_id
    assert transport.request.call_args_list[0] == transport.request.call_args_list[1]




def test_quiet_polls_of_a_running_turn_do_not_read_the_whole_execution(control_plane, monkeypatch):
    """运行中轮次的静默轮询不再读取整条执行记录

    Resident 轮次每 0.25 秒轮询一次以便响应 Stop；没有变化的轮询只返回 unchanged，不再整行读取、解析并回传执行请求，也不重复投影未变化的快照；首次观察、结束与非提交者的拒绝保持不变。

    A running turn's waiter polls every 0.25 s for Stop; each poll read and
    parsed the whole execution row twice and sent it back, about 85 KB."""
    plane = control_plane
    request = ExecutionRequest.new(actor="alice", prompt="durable original request",
                                   service_type="chat_completion", provider="openai", operation="generate")
    plane.scheduler.submit(request)
    execution_id, token = start(plane)
    full_reads = [0]
    original_snapshot = SchedulerViews.snapshot

    def counted_snapshot(self, *args, **kwargs):
        full_reads[0] += 1
        return original_snapshot(self, *args, **kwargs)

    monkeypatch.setattr(SchedulerViews, "snapshot", counted_snapshot)
    projected: list[dict] = []
    with pytest.raises(ExecutionWaitTimeout) as caught:
        plane.service.wait(
            execution_id, actor="alice", timeout_seconds=1.2,
            cancellation_requested=lambda: False,
            on_snapshot=lambda snapshot: projected.append(snapshot),
        )
    # The first observation is the only full read; the quiet polls after it
    # neither read the row again nor project an unchanged snapshot.
    assert full_reads[0] == 1
    assert len(projected) == 1 and projected[0]["status"] == "running"
    assert caught.value.snapshot["status"] == "running"
    assert caught.value.snapshot["request"]["actor"] == "alice"
    finish(plane, execution_id, token, "completed")
    observed = plane.service.wait(execution_id, actor="alice", timeout_seconds=1, cancellation_requested=lambda: False)
    assert observed["status"] == "completed"
    with pytest.raises(KeyError):
        plane.service.wait(execution_id, actor="mallory", timeout_seconds=0.3)
