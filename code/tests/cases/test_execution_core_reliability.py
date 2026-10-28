"""Cross-instance scheduling, accepted-input receipts and transport failures."""
import socket
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from tests.cases.rpc_test_support import socket_rpc_authenticator

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.service import ExecutionServiceServer
from management_portal.execution.transport import ExecutionResponseUnknown, ExecutionServiceClient, ExecutionServiceUnavailable
from management_portal.execution.worker import ExecutionWorker, ExecutionWorkerDaemon


def scheduler():
    return Scheduler(quota_reserve_enabled=False, dispatch_interval_min_seconds=0,
                     dispatch_interval_max_seconds=0)


def request():
    return ExecutionRequest.new(actor="test", prompt="core reliability", provider="codex",
        service_type="agent_session", operation="turn",
        session=SessionOptions(mode="stateless", backend_route_id="one"))


@pytest.mark.parametrize("error_type", [ExecutionServiceUnavailable, ExecutionResponseUnknown])
def test_claim_transport_failure_keeps_running_worker_alive(error_type):
    """认领请求遇到服务不可用或回复未知时 worker 保留运行中的执行并在下次恢复"""
    from unittest.mock import Mock
    daemon = ExecutionWorkerDaemon.__new__(ExecutionWorkerDaemon)
    daemon._service_error = ""
    daemon._stopping = threading.Event()
    daemon._active = {"existing": {"execution_id": "existing"}}
    daemon._fill_slots = Mock(side_effect=[error_type("service replacing"), True])
    assert daemon._fill_slots_resilient() is False
    assert daemon._service_error == "service replacing"
    assert not daemon._stopping.is_set()
    assert "existing" in daemon._active
    assert daemon._fill_slots_resilient() is True
    assert daemon._service_error == ""


def test_lost_control_reply_does_not_cancel_active_provider():
    """控制等待的回复丢失时不取消也不交接正在运行的 Provider"""
    from unittest.mock import Mock
    renewed = threading.Event()
    control = Mock()
    def wait_for_control(*_args, **_kwargs):
        if control.wait_for_control.call_count == 1:
            raise ExecutionResponseUnknown("service replacing")
        renewed.wait(0.05)
        return {}
    control.wait_for_control.side_effect = wait_for_control
    control.heartbeat.side_effect = lambda *_: renewed.set() or {}
    control.finish.side_effect = lambda result, _token: {"status": result.status}
    runtime = Mock()
    def execute(_request, context):
        assert renewed.wait(3)
        assert not context.is_cancelled()
        assert not context.is_handover()
        return SimpleNamespace(output={}, backend_session_id="", backend_route_id="", usage={})
    runtime.execute.side_effect = execute
    worker = ExecutionWorker(build_id="test", worker_id="worker", scheduler=control, runtime=runtime)
    item = request()
    result = worker.execute_claim({"claim_token": "claim", "execution": {
        "execution_id": item.execution_id, "request": item.to_payload()}}, lease_seconds=30)
    assert result["status"] == "completed"
    runtime.interrupt.assert_not_called()


def register(store, worker="worker"):
    store.register_worker(worker_id=worker, build_id="test", capabilities=["agent_session:codex"])


def claim(store, worker="worker"):
    return store.claim(worker_id=worker, capabilities=["agent_session:codex"])


def test_capacity_changes_reach_an_already_running_peer():
    """一个调度器实例修改的容量上限立即作用于已在运行的另一实例"""
    first, peer = scheduler(), scheduler()
    register(peer)
    first.configure_capacity("codex:one", 1, 0, 0, actor="test")
    requests = [request(), request()]
    for item in requests:
        first.submit(item)
    assert claim(peer)["claimed"]
    assert claim(peer)["reason"] == "capacity"
    assert peer.health()["capacity_partitions"]["codex:one"]["limit"] == 1
    first.configure_capacity("codex:one", 2, 0, 0, actor="test")
    assert claim(peer)["claimed"]


def test_started_event_survives_cross_instance_waiting_and_restart():
    """started 事件在其他调度器实例和重启后的实例中都只出现一次"""
    first, peer = scheduler(), scheduler()
    register(first)
    item = request()
    first.submit(item)
    owned = claim(first)
    first.start(item.execution_id, worker_id="worker", claim_token=owned["claim_token"])
    for reader in (peer, scheduler()):
        events = reader.snapshot(item.execution_id)["events"]
        assert len([event for event in events if event["type"] == "started"]) == 1


def test_submission_retry_uses_original_request_before_preparation():
    """重复提交同一执行按原始请求去重且不再次准备，内容不同的请求被拒绝"""
    store = scheduler()
    item = request()
    server = object.__new__(ExecutionServiceServer)
    server.scheduler = store
    server.publish_change = lambda value: value
    prepared = []
    def prepare(value):
        prepared.append(value)
        if len(prepared) > 1:
            raise ValueError("Agent session already exists")
        return replace(value, priority=7)
    server.preparation = SimpleNamespace(prepare=prepare)
    server.dispatch("execution.submit", item.to_payload())
    store.set_priority(item.execution_id, actor="test", priority=9)
    assert server.dispatch("execution.submit", item.to_payload())["execution_id"] == item.execution_id
    assert len(prepared) == 1
    with pytest.raises(ValueError, match="different request"):
        server.dispatch("execution.submit", replace(item, prompt="another input").to_payload())


@pytest.mark.parametrize("provider", ["codex", "claude", "dsh", "kimi"])
def test_cancel_after_crash_requeue_still_interrupts_provider(provider):
    """崩溃重排队后取消执行，新认领者收到放弃旧轮次的取消标记，旧 claim 不能再写检查点"""
    store = scheduler()
    register(store)
    item = request()
    store.submit(item)
    owned = claim(store)
    owner = {"worker_id": "worker", "claim_token": owned["claim_token"]}
    store.start(item.execution_id, **owner)
    store.checkpoint(item.execution_id, checkpoint={"provider": provider, "turn_id": "turn-1",
        "backend_session_id": "session-1", "resumable": True}, **owner)
    store.requeue_claim(item.execution_id, reason="lost connection", **owner)
    result = store.cancel(item.execution_id, actor="test")
    assert result["status"] == "queued"
    replacement = claim(store)
    assert replacement["execution"]["checkpoint"]["abandoned"]["status"] == "cancelled"
    with pytest.raises(PermissionError):
        store.checkpoint(item.execution_id, checkpoint={"resumable": True}, **owner)


def test_response_timeout_does_not_replay_a_claim_on_peer(tmp_path, socket_rpc_authenticator):
    """认领请求响应超时时报回复未知，不在另一实例上重放"""
    path = tmp_path / "execution.sock"
    receiver = socket_rpc_authenticator(path)
    accepted = threading.Event()
    stop = threading.Event()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()
    def serve():
        connection, _ = listener.accept()
        with connection:
            import json
            with connection.makefile("rb") as stream:
                payload = json.loads(stream.readline())
            receiver.authenticate(payload["method"], payload["params"], payload.get("auth"))
            accepted.set()
            stop.wait(2)
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        client = ExecutionServiceClient(path, timeout=0.1)
        client.socket_paths = (path, tmp_path / "peer.sock")
        with pytest.raises(ExecutionResponseUnknown):
            client.request("scheduler.claim.wait")
        assert accepted.is_set()
    finally:
        stop.set()
        thread.join(2)
        listener.close()


def test_worker_retries_accepted_input_receipt_before_finishing():
    """worker 结束执行前重试送达已接受输入的回执，回执丢失时以相同内容重发"""
    done = threading.Event()
    receipts = []
    class Control:
        def start(self, *args):
            return {}
        def wait_for_control(self, *args, **kwargs):
            done.wait(0.02)
            return {}
        def heartbeat(self, *args):
            return {}
        def complete_input_control(self, *args, **kwargs):
            receipts.append((args, kwargs))
            if len(receipts) == 1:
                raise ExecutionResponseUnknown("reply lost")
        def finish(self, result, token):
            assert len(receipts) == 2
            done.set()
            return {"status": result.status}
    class Runtime:
        def execute(self, item, context):
            context.turn_control_result_callback({"control_id": "accepted"}, True, "")
            return SimpleNamespace(output={}, backend_session_id="", backend_route_id="", usage={})
        def interrupt(self, *args):
            pass
    item = request()
    worker = ExecutionWorker(build_id="test", worker_id="worker", scheduler=Control(), runtime=Runtime())
    result = worker.execute_claim({"claim_token": "claim", "execution": {
        "execution_id": item.execution_id, "request": item.to_payload()}})
    assert result["status"] == "completed"
    assert receipts[0] == receipts[1]


@pytest.mark.parametrize("reply_lost", [False, True])
def test_cancelled_worker_renews_lease_until_provider_stops(reply_lost):
    """已取消的 worker 在 Provider 停止前持续续租，心跳回执丢失也不中断续租"""
    renewed = threading.Event()
    done = threading.Event()
    interrupted = threading.Event()
    class Control:
        heartbeat_attempts = 0
        def start(self, *args):
            return {}
        def wait_for_control(self, *args, **kwargs):
            return {"cancel_requested": True}
        def heartbeat(self, *args):
            assert interrupted.is_set()
            self.heartbeat_attempts += 1
            if reply_lost and self.heartbeat_attempts == 1:
                raise ExecutionResponseUnknown("heartbeat receipt lost")
            renewed.set()
            return {"cancel_requested": True}
        def finish(self, result, token):
            done.set()
            return {"status": result.status}
    class Runtime:
        def execute(self, item, context):
            assert renewed.wait(8)
            raise InterruptedError("stopped")
        def interrupt(self, *args):
            interrupted.set()
    item = request()
    worker = ExecutionWorker(build_id="test", worker_id="worker", scheduler=Control(), runtime=Runtime())
    result = worker.execute_claim({"claim_token": "claim", "execution": {
        "execution_id": item.execution_id, "request": item.to_payload()}}, lease_seconds=15)
    assert result["status"] == "cancelled"


@pytest.mark.parametrize("first_delivery", ["not_started", "reply_lost", "accepted"])
def test_cancel_retries_until_provider_has_actually_stopped(first_delivery):
    """取消请求未送达、回复丢失或已接受时 worker 都重试中断，直到 Provider 真正停止"""
    stopped = threading.Event()
    calls = []

    class Control:
        heartbeats = 0

        def start(self, *args):
            return {}

        def wait_for_control(self, *args, **kwargs):
            return {"cancel_requested": True}

        def heartbeat(self, *args):
            self.heartbeats += 1
            return {"cancel_requested": True}

        def finish(self, result, token):
            assert stopped.is_set()
            return {"status": result.status}

    class Runtime:
        def execute(self, item, context):
            assert stopped.wait(8), "cancellation intent was never retried"
            assert context.is_cancelled()
            raise InterruptedError("provider stopped")

        def interrupt(self, execution_id):
            calls.append(execution_id)
            if len(calls) == 1:
                if first_delivery == "reply_lost":
                    raise TimeoutError("interrupt reply lost")
                return first_delivery == "accepted"
            stopped.set()
            return True

    item = request()
    control = Control()
    worker = ExecutionWorker(build_id="test", worker_id="worker", scheduler=control, runtime=Runtime())
    result = worker.execute_claim({"claim_token": "claim", "execution": {
        "execution_id": item.execution_id, "request": item.to_payload()}}, lease_seconds=15)
    assert result["status"] == "cancelled"
    assert calls == [item.execution_id, item.execution_id]
    assert control.heartbeats >= 2


def test_execution_payload_requires_matching_identity():
    """执行请求载荷缺少 identity 时反序列化被拒绝"""
    item = request()
    payload = item.to_payload()
    assert ExecutionRequest.from_payload(payload).identity == item.identity
    payload.pop("identity")
    with pytest.raises(ValueError, match="identity required"):
        ExecutionRequest.from_payload(payload)
