"""A request to a restarting Portal service waits for it instead of failing.

Web used to answer 503 the moment SocietyService, ResourceService or
ExecutionService refused a connection during a publication's restart.

:importance: P1
:design: decision_96a887d83e8a
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common import service_socket
from management_common.service_socket import connect_service
from management_portal.execution.scheduler import SchedulerClient
from management_portal.execution.transport import ExecutionServiceClient, ExecutionServiceUnavailable
from management_portal.hatg import socket_rpc
from management_portal.society_service.errors import SocietyServiceUnavailable
from management_portal.society_service.transport import SocietyServiceClient
from management_portal.resources.errors import ResourceServiceUnavailable
from management_portal.resources.transport import ResourceServiceClient

pytestmark = pytest.mark.service_restart_wait
from tests.cases.rpc_test_support import socket_rpc_authenticator  # noqa: F401

RESTART_SECONDS = 0.6


def _stale_socket(path: Path) -> None:
    # The socket file of a service that stopped: connecting is refused.
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()


def _service_back_after(path: Path, delay: float, received: list[str]) -> threading.Thread:
    """A service that is listening again ``delay`` seconds later and answers one request."""

    def run() -> None:
        time.sleep(delay)
        path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(8)
        try:
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as stream:
                request = json.loads(stream.readline())
                received.append(request["method"])
                answer = {"id": request["id"], "ok": True, "result": {"answered": True}}
                connection.sendall(json.dumps(answer).encode("utf-8") + b"\n")
        finally:
            listener.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize("stopped", ["socket_absent", "socket_refusing"])
def test_a_lineage_request_waits_out_the_restart_and_is_sent_once(tmp_path, socket_rpc_authenticator, stopped):
    """SocietyService 重启期间的请求等它回来后只发送一次

    SocietyService 的 socket 不存在或拒绝连接时，Domain 请求等到服务重新监听再送达且只送达一次，不立即返回 503。
    """
    path = tmp_path / "lineage.sock"
    socket_rpc_authenticator(path)
    if stopped == "socket_refusing":
        _stale_socket(path)
    received: list[str] = []
    service = _service_back_after(path, RESTART_SECONDS, received)
    started = time.monotonic()
    result = SocietyServiceClient(path, timeout=5).resident.list_resident_conversation_activity()
    service.join(timeout=5)
    assert result == {"answered": True}
    assert received == ["domain.call"]
    assert time.monotonic() - started >= RESTART_SECONDS


def test_a_resource_request_waits_out_the_restart(tmp_path, socket_rpc_authenticator):
    """ResourceService 重启期间的请求等它回来后只发送一次

    ResourceService 的 socket 暂时不存在时，客户端请求等到服务重新监听再送达且只送达一次；status 探测仍立即报告服务不可用。
    """
    path = tmp_path / "resource.sock"
    socket_rpc_authenticator(path)
    received: list[str] = []
    service = _service_back_after(path, RESTART_SECONDS, received)
    client = ResourceServiceClient(path, timeout=5)
    assert client.get("article_000000000000") == {"answered": True}
    service.join(timeout=5)
    assert received == ["resource.get"]
    # The status probe reports an absent service at once.
    started = time.monotonic()
    with pytest.raises(ResourceServiceUnavailable):
        client.status()
    assert time.monotonic() - started < 1.0


def test_a_health_probe_reports_an_absent_service_at_once(tmp_path, socket_rpc_authenticator):
    """健康探测立即报告不在线的服务

    SocietyService 与 ExecutionService 的 health 探测不等待重启，服务不在线时一秒内报告不可用。

    :importance: P2
    """
    path = tmp_path / "lineage.sock"
    socket_rpc_authenticator(path)
    started = time.monotonic()
    with pytest.raises(SocietyServiceUnavailable):
        SocietyServiceClient(path).health()
    with pytest.raises(ExecutionServiceUnavailable):
        ExecutionServiceClient(tmp_path / "execution.sock").health()
    assert time.monotonic() - started < 1.0


def test_an_execution_request_waits_while_a_worker_fails_over_its_lease_at_once(tmp_path, socket_rpc_authenticator):
    """ExecutionService 请求等待重启，Worker 的调度调用立即失败

    Worker 的 SchedulerClient 在 ExecutionService 不在线时立即得到不可用，按租约截止时间自行处理；普通 ExecutionService 请求等服务回来后送达一次。
    """
    path = tmp_path / "execution.sock"
    socket_rpc_authenticator(path)
    # A Worker fences its claims by its lease deadline: it learns of the
    # absent service at once and retries on its own schedule.
    worker = SchedulerClient(worker_id="worker", build_id="build", capabilities=[])
    worker.service.socket_paths = (path,)
    started = time.monotonic()
    with pytest.raises(ExecutionServiceUnavailable):
        worker.register()
    assert time.monotonic() - started < 1.0

    received: list[str] = []
    service = _service_back_after(path, RESTART_SECONDS, received)
    assert ExecutionServiceClient(path, timeout=5).request("accounts.usage", {}) == {"answered": True}
    service.join(timeout=5)
    assert received == ["accounts.usage"]


def test_an_instruction_waits_for_its_restarting_owner(tmp_path, socket_rpc_authenticator, monkeypatch):
    """HATG 指令等待正在重启的所属服务

    指令所属服务的 socket 暂时不存在时，instruction.execute 等到服务重新监听再送达一次，不立即返回 instruction.owner_unavailable。
    """
    path = tmp_path / "resource.sock"
    monkeypatch.setattr(socket_rpc, "owner_socket", lambda owner: path)
    received: list[str] = []
    service = _service_back_after(path, RESTART_SECONDS, received)
    connection, stream = socket_rpc._open("resource", {"id": "isa-1", "method": socket_rpc.METHOD, "params": {}}, 5.0)
    try:
        assert json.loads(stream.readline())["id"] == "isa-1"
    finally:
        stream.close()
        connection.close()
    service.join(timeout=5)
    assert received == [socket_rpc.METHOD]


def test_a_waiting_request_backs_off_to_one_try_every_two_seconds(tmp_path, monkeypatch):
    """等待重启的连接按退避重试，最长每 2 秒一次

    服务 socket 一直不在时，重连间隔为 0.25、0.5、1 秒，之后每 2 秒一次，最后一次等待截到等待期限为止。

    :importance: P2
    """
    # sample_admin 10-13: a try every 0.25 s throughout the wait is too frequent.
    clock, pauses = [0.0], []

    def sleep(seconds: float) -> None:
        pauses.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(service_socket, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    with pytest.raises(FileNotFoundError):
        connect_service(tmp_path / "absent.sock", timeout=1, restart_wait=6)
    assert pauses == [0.25, 0.5, 1.0, 2.0, 2.0, 0.25]


def test_the_first_accepting_socket_answers_without_waiting(tmp_path):
    """第一个可连接的服务 socket 立即接手请求

    本实例的 socket 不存在而对端实例的 socket 在监听时，连接立即转到对端，不进入重启等待。

    :importance: P2
    """
    # ExecutionService fails over to the peer instance's socket at once.
    absent, peer = tmp_path / "absent.sock", tmp_path / "peer.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(peer))
    listener.listen(1)
    try:
        started = time.monotonic()
        connection, path = connect_service([absent, peer], timeout=1)
        connection.close()
        assert path == peer
        assert time.monotonic() - started < 0.2
    finally:
        listener.close()
