"""A lost Worker's Provider work is interrupted before its execution ends.

Providers run a turn outside the Worker (Codex app server, Claude Code
daemon, DSH process, Kimi server).  When a Worker dies and its execution
cannot be resumed (cancelled, not yet confirmed by the Provider, or not a
turn), the execution goes back to the queue marked ``abandoned``.  The next
Worker only interrupts the Provider work and then ends the execution, so the
Resident shows its work as active until the Provider has been stopped.

A Provider that keeps a turn for the next Worker keeps it whenever a Worker's
connection closes; an execution that fails in its Worker interrupts that turn,
so it does not work on unseen and hold the session.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

from tests.cases.execution_test_support import run_worker_once

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from management_portal.execution import backends
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.scheduler import SchedulerClient
from management_portal.execution.worker import ExecutionWorker
from tests.cases.database_test_support import connect
from tests.cases.model_api_test_support import claude_subscription, register_apis
from tests.cases.test_execution_layer import _close, _stack


# One Worker slot is active at a time; the restarted Worker takes it over.
_WORKER = "explore-1:worker"


class _Provider:
    provider_id = "codex"

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.interrupts: list[dict] = []

    def execute(self, invocation, context):
        self.executed.append(invocation.request.execution_id)
        raise AssertionError("an abandoned execution must not run again")

    def interrupt(self, **values):
        self.interrupts.append(values)
        return True


def _lost_worker_execution(
    scheduler, client, service, *, checkpoint: dict, cancel: bool = False, operation: str = "turn",
    expire: bool = True,
) -> str:
    lost = SchedulerClient(
        worker_id=_WORKER, build_id="test-build",
        capabilities=["agent_session:codex"], service_client=client,
    )
    lost.register()
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="work the lost Worker started",
        service_type="agent_session", provider="codex", operation=operation,
        session=SessionOptions(mode="persistent", binding_id="resident:resident_lostworker01", backend_route_id="one"),
        runtime_options={"session_key": "resident:resident_lostworker01"},
    )
    service.submit(request)
    claim = lost.claim()
    lost.start(request.execution_id, claim["claim_token"])
    if checkpoint:
        lost.checkpoint(request.execution_id, claim["claim_token"], checkpoint)
    if cancel:
        service.cancel(request.execution_id, actor="sample_admin", reason="stop it")
    if expire:
        with connect(scheduler.database) as connection:
            connection.execute("UPDATE executions SET lease_expires_at = to_timestamp(1) WHERE execution_id = %s",
                               (request.execution_id,))
        scheduler.maintain()
    return request.execution_id


def _worker(tmp_path: Path, client, provider: _Provider, worker_id: str = _WORKER) -> ExecutionWorker:
    runtime = ExecutionRuntime(agent_providers={provider.provider_id: provider}, state_root=tmp_path / "execution-state")
    return ExecutionWorker(
        build_id="test-build", worker_id=worker_id,
        scheduler=SchedulerClient(worker_id=worker_id, build_id="test-build",
                                  capabilities=runtime.capabilities(), service_client=client),
        runtime=runtime, state_root=tmp_path / "execution-state",
    )


_UNCONFIRMED_TURN = {  # the Provider had the thread, the turn id was not reported yet
    "provider": "codex", "operation": "turn", "backend_session_id": "thread-1",
    "backend_route_id": "one", "turn_id": "", "resumable": False,
}


def test_a_lost_workers_unconfirmed_turn_is_interrupted_before_it_fails(tmp_path: Path) -> None:
    """Worker 丢失后，Provider 尚未确认的轮次先被中断再判失败

    Worker 丢失、执行无法续接时，先中断 Provider 中还在进行的工作，再结束这次执行：Worker 丢失后，Provider 尚未确认的轮次先被中断再判失败。
    """
    scheduler, server, thread, client, service = _stack(tmp_path)
    provider = _Provider()
    try:
        execution_id = _lost_worker_execution(scheduler, client, service, checkpoint=_UNCONFIRMED_TURN)

        waiting = service.status(execution_id)
        assert waiting["status"] == "queued"  # still active: the Resident is not idle yet
        assert waiting["events"][-1]["type"] == "requeued"
        assert waiting["events"][-1]["details"]["abandoned"]["status"] == "failed"

        worker = _worker(tmp_path, client, provider)
        worker.register()
        assert run_worker_once(worker)

        ended = service.status(execution_id)
        assert ended["status"] == "failed"
        assert "lease expired after backend execution started" in ended["error"]
        assert provider.executed == []
        assert provider.interrupts == [{
            "backend_session_id": "thread-1", "turn_id": "", "backend_route_id": "one",
            "runtime_options": {"session_key": "resident:resident_lostworker01", "interrupt_active_turn": True,
                                "abandoned_execution_id": execution_id},
        }]
        assert any(event["type"] == "provider_interrupt" and event["details"]["interrupted"]
                   for event in ended["events"])
    finally:
        _close(server, thread)


def test_a_cancelled_turn_of_a_lost_worker_is_interrupted_before_it_is_cancelled(tmp_path: Path) -> None:
    """Worker 丢失后，已取消的轮次先在 Provider 中断再记为取消

    Worker 丢失、执行无法续接时，先中断 Provider 中还在进行的工作，再结束这次执行：Worker 丢失后，已取消的轮次先在 Provider 中断再记为取消。
    """
    scheduler, server, thread, client, service = _stack(tmp_path)
    provider = _Provider()
    try:
        execution_id = _lost_worker_execution(
            scheduler, client, service, cancel=True,
            checkpoint={**_UNCONFIRMED_TURN, "turn_id": "turn-1", "resumable": True},
        )
        stopping = service.status(execution_id)
        assert stopping["status"] == "queued"
        assert stopping["scheduler"]["cancel_requested"] is True  # the Resident shows it stopping

        worker = _worker(tmp_path, client, provider)
        worker.register()
        assert run_worker_once(worker)

        assert service.status(execution_id)["status"] == "cancelled"
        assert [call["turn_id"] for call in provider.interrupts] == ["turn-1"]
    finally:
        _close(server, thread)


def test_stopping_work_that_waits_to_be_interrupted_still_interrupts_it(tmp_path: Path) -> None:
    """停止正在等待中断的工作时仍会中断 Provider 中的轮次

    Worker 丢失、执行无法续接时，先中断 Provider 中还在进行的工作，再结束这次执行：停止正在等待中断的工作时仍会中断 Provider 中的轮次。
    """
    scheduler, server, thread, client, service = _stack(tmp_path)
    provider = _Provider()
    try:
        execution_id = _lost_worker_execution(scheduler, client, service, checkpoint=_UNCONFIRMED_TURN)
        service.cancel(execution_id, actor="sample_admin", reason="stop it")
        assert service.status(execution_id)["status"] == "queued"

        worker = _worker(tmp_path, client, provider)
        worker.register()
        assert run_worker_once(worker)

        assert service.status(execution_id)["status"] == "cancelled"
        assert len(provider.interrupts) == 1
    finally:
        _close(server, thread)


def test_a_cancelled_turn_handed_over_from_an_exited_worker_is_interrupted_first(tmp_path: Path) -> None:
    """从已退出 Worker 交接来的已取消轮次先被中断

    Worker 丢失、执行无法续接时，先中断 Provider 中还在进行的工作，再结束这次执行：从已退出 Worker 交接来的已取消轮次先被中断。
    """
    scheduler, server, thread, client, service = _stack(tmp_path)
    provider = _Provider()
    try:
        execution_id = _lost_worker_execution(
            scheduler, client, service, cancel=True, expire=False,
            checkpoint={**_UNCONFIRMED_TURN, "turn_id": "turn-1", "resumable": True},
        )
        with connect(scheduler.database) as connection:
            connection.execute("UPDATE execution_workers SET state = 'offline' WHERE worker_id = %s", (_WORKER,))
        assert scheduler.handover_worker(_WORKER)["requeued_execution_ids"] == [execution_id]
        waiting = service.status(execution_id)
        assert waiting["status"] == "queued"
        assert waiting["events"][-1]["details"]["abandoned"]["error"] == "execution cancelled during worker handover"

        worker = _worker(tmp_path, client, provider, worker_id="explore-1:replacement-worker")
        worker.register()
        assert run_worker_once(worker)

        assert service.status(execution_id)["status"] == "cancelled"
        assert [call["turn_id"] for call in provider.interrupts] == ["turn-1"]
    finally:
        _close(server, thread)


def test_a_turn_lost_before_its_session_was_reported_interrupts_the_bound_session(tmp_path: Path) -> None:
    """会话尚未上报就丢失的轮次会中断已绑定的会话

    Worker 丢失、执行无法续接时，先中断 Provider 中还在进行的工作，再结束这次执行：会话尚未上报就丢失的轮次会中断已绑定的会话。
    """
    scheduler, server, thread, client, service = _stack(tmp_path)
    provider = _Provider()
    try:
        execution_id = _lost_worker_execution(scheduler, client, service, checkpoint={})
        assert service.status(execution_id)["status"] == "queued"

        worker = _worker(tmp_path, client, provider)
        worker.register()
        bound = mock.Mock(backend_session_id="thread-bound", backend_route_id="two")
        with mock.patch.object(worker.runtime.sessions, "prepare", return_value=bound):
            assert run_worker_once(worker)

        assert service.status(execution_id)["status"] == "failed"
        assert [(call["backend_session_id"], call["backend_route_id"]) for call in provider.interrupts] == [
            ("thread-bound", "two"),
        ]
        assert provider.interrupts[0]["runtime_options"]["abandoned_execution_id"] == execution_id
    finally:
        _close(server, thread)


class _TurnFailingInTheWorker(_Provider):
    """The Provider took the turn; the Worker failed while recording it."""

    def __init__(self, provider_id: str) -> None:
        super().__init__()
        self.provider_id = provider_id

    def execute(self, invocation, context):
        self.executed.append(invocation.request.execution_id)
        context.set_interrupt_target("session-1", "turn-1")
        raise ValueError("Segment identity refers to different immutable content: segment_1")


@pytest.fixture
def short_socket_root():
    # AF_UNIX limits include every parent directory, including xdist's worker path.
    with TemporaryDirectory(prefix="apw-", dir="/tmp") as directory:
        yield Path(directory)


def test_a_failed_execution_interrupts_the_turn_its_provider_keeps_running(
    tmp_path: Path, short_socket_root: Path,
) -> None:
    """执行在 Worker 中失败时中断 Provider 留着的轮次

    Claude 等为下一个 Worker 留着轮次的 Provider，在执行于 Worker 中失败（如记录轨迹出错）时中断这一轮，轮次不会在看不见的地方继续占着会话、让 Resident 的下一条输入排在后面；Codex 等不留轮次的 Provider 不另行中断。
    """
    # Every Provider keeps its turn running when a Worker's connection closes (handover_keeps_turn).
    register_apis(claude_subscription("claude_one", str(tmp_path / "claude-one")))
    for provider_id, route in (("claude", "claude_one"), ("codex", "one")):
        scheduler, server, thread, client, service = _stack(short_socket_root / provider_id)
        provider = _TurnFailingInTheWorker(provider_id)
        try:
            request = ExecutionRequest.new(
                actor="sample_admin", prompt="work the Worker fails to record",
                service_type="agent_session", provider=provider_id, operation="turn",
                session=SessionOptions(mode="persistent", binding_id=f"resident:resident_failed{provider_id}",
                                       backend_route_id=route),
                runtime_options={"session_key": f"resident:resident_failed{provider_id}"},
            )
            service.submit(request)
            worker = _worker(tmp_path / provider_id, client, provider)
            worker.register()
            assert run_worker_once(worker)

            ended = service.status(request.execution_id)
            assert ended["status"] == "failed"
            assert "Segment identity" in ended["error"]
            assert provider.executed == [request.execution_id]
            interrupts = [event for event in ended["events"] if event["type"] == "provider_interrupt"]
            assert [(call["backend_session_id"], call["turn_id"]) for call in provider.interrupts] == [
                ("session-1", "turn-1"),
            ]
            assert [event["details"]["reason"] for event in interrupts] == ["execution failed"]
        finally:
            _close(server, thread)


def test_a_lost_session_creation_still_fails_at_once(tmp_path: Path) -> None:
    """丢失的会话创建仍立即失败

    Worker 丢失、执行无法续接时，先中断 Provider 中还在进行的工作，再结束这次执行：丢失的会话创建仍立即失败。
    """
    scheduler, server, thread, client, service = _stack(tmp_path)
    try:
        execution_id = _lost_worker_execution(scheduler, client, service, checkpoint={}, operation="create")
        assert service.status(execution_id)["status"] == "failed"
    finally:
        _close(server, thread)


def test_dsh_names_the_turn_and_new_session_a_lost_worker_did_not_report() -> None:
    """DSH 能指出丢失的 Worker 未上报的轮次和新会话

    Worker 丢失、执行无法续接时，先中断 Provider 中还在进行的工作，再结束这次执行：DSH 能指出丢失的 Worker 未上报的轮次和新会话。
    """
    from management_portal.dsh_client import _digest

    client = mock.Mock()
    client.interrupt.return_value = {"interrupted": True}
    backend = backends.DshBackend.__new__(backends.DshBackend)
    backend.client_factory = lambda: client

    assert backend.interrupt(
        backend_session_id="", turn_id="", backend_route_id="",
        runtime_options={"session_key": "resident:r", "interrupt_active_turn": True,
                         "abandoned_execution_id": "exec-1"},
    )
    client.interrupt.assert_called_once_with("resident:r", "dsh-" + _digest("exec-1"), turn_id="exec-1")


def test_codex_interrupts_the_threads_turn_in_progress_when_the_turn_id_is_unknown() -> None:
    """不知道轮次 ID 时，Codex 中断线程上正在进行的轮次

    Worker 丢失、执行无法续接时，先中断 Provider 中还在进行的工作，再结束这次执行：不知道轮次 ID 时，Codex 中断线程上正在进行的轮次。
    """
    client = mock.Mock()
    client.active_turn_id.return_value = "turn-in-progress"
    backend = backends.CodexBackend.__new__(backends.CodexBackend)
    backend.account_registry = mock.Mock()
    backend.client_factory = lambda _socket: client

    assert backend.interrupt(
        backend_session_id="thread-1", turn_id="", backend_route_id="one",
        runtime_options={"interrupt_active_turn": True},
    )
    client.interrupt_turn.assert_called_once_with("thread-1", "turn-in-progress")

    client.reset_mock()
    assert not backend.interrupt(
        backend_session_id="thread-1", turn_id="", backend_route_id="one", runtime_options={},
    )
    client.interrupt_turn.assert_not_called()
