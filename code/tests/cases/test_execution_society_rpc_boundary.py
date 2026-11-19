"""Execution-to-Society transport boundary for input diagnostics.

:module: execution.service
:importance: P1
"""
from contextlib import contextmanager, nullcontext
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_common.rpc_auth import RpcAuthenticator
from management_common import society_rpc_client
from management_common.operation_errors import OperationError
from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
from management_common.service_shutdown import DrainingStreamServer
from management_portal.execution import provider_updates
from management_portal.execution.service_rpc import execution_input_diagnostics
from management_portal.society_service import resident_input_diagnostics
from management_portal.society_service.server import (
    LineageReadProcessPool,
    SocietyServiceServer,
    _LineageRequestHandler,
)


def _occupy_worker(entered, release):
    Path(entered).touch()
    deadline = monotonic() + 15
    while not Path(release).exists():
        if monotonic() >= deadline:
            raise TimeoutError("test did not release the Society read worker")
        sleep(0.01)


@contextmanager
def _society_socket(path, pool):
    # Exercise the real request handler and domain dispatch with a real process
    # pool, without starting unrelated background tasks.
    society = object.__new__(SocietyServiceServer)
    society.endpoint = service_endpoint(path)
    society.address_family = society.endpoint.family
    society.rpc_auth = RpcAuthenticator(path)
    society.read_pool = pool
    society._finish_changes = lambda changes: None
    DrainingStreamServer.__init__(society, society.endpoint.address, _LineageRequestHandler)
    thread = Thread(target=DrainingStreamServer.serve_forever, args=(society,), daemon=True)
    thread.start()
    try:
        yield
    finally:
        society.shutdown()
        LocalServiceSocketMixin.server_close(society)
        thread.join()


def test_signed_lookup_bypasses_a_saturated_society_read_pool(tmp_path, monkeypatch):
    from management_portal.society_service import execution_client, resident_protocol
    from management_portal.society_service.config import society_service_socket
    from management_portal.society_service.transport import SocietyServiceClient

    path = tmp_path / "society-service.sock"
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_SOCKET", str(path))
    expected = {"available": False, "reason": "execution_not_associated"}
    permission = Mock(side_effect=lambda actor, instruction, args: actor == "reader")
    monkeypatch.setattr(resident_input_diagnostics.permission_system, "allows", permission)
    state = Mock(return_value={"state": "completed"})
    monkeypatch.setattr(resident_protocol.resident_conversation_stream, "get_repository",
                        lambda: SimpleNamespace(input_state=state, agent_data=SimpleNamespace(message_groups=lambda *_: {})))
    monkeypatch.setattr(resident_protocol.resident_input_scope, "permits", lambda *args: True)
    monkeypatch.setattr(resident_input_diagnostics.resident_record_domain, "resolve_resident_runtime_target",
                        lambda resident_id, *args, **kwargs: SimpleNamespace(resident_id=resident_id))
    monkeypatch.setattr(resident_input_diagnostics.resident_record_domain, "find_resident_for_admission",
                        lambda rid: {"resident_id": rid})
    execution_port = Mock(side_effect=AssertionError("cannot call Execution from the association lookup"))
    monkeypatch.setattr(execution_client, "service", execution_port)
    pool = LineageReadProcessPool(1)
    entered, release = tmp_path / "entered", tmp_path / "release"
    occupied = pool._executor.submit(_occupy_worker, entered, release)
    identity = {"actor": "reader", "resident_id": "resident_target", "message_id": "input"}
    try:
        deadline = monotonic() + 8
        while not entered.exists() and monotonic() < deadline:
            sleep(0.01)
        assert entered.exists()
        with _society_socket(path, pool):
            client = SocietyServiceClient(society_service_socket(), timeout=1)
            assert client.resident.resolve_input_execution(**identity) == expected
            with pytest.raises(ValueError, match="execution_id"):
                client.resident.resolve_input_execution(**identity, execution_id="not caller-selectable")
            assert not occupied.done()
            read = Mock(side_effect=AssertionError("an unassociated input cannot read execution data"))
            execution = SimpleNamespace(agent_data=SimpleNamespace(input_diagnostics=read))
            assert execution_input_diagnostics(execution, identity) == {
                "schema": "tfi.execution-input-diagnostics.v1", **expected,
            }
            with pytest.raises(PermissionError):
                execution_input_diagnostics(execution, {**identity, "actor": "other_reader"})
            assert state.call_count == 2
            state.assert_called_with("resident_target", "input")
            assert permission.call_count == 3
            execution_port.assert_not_called()
            read.assert_not_called()
    finally:
        release.touch()
        try:
            occupied.result(timeout=5)
        finally:
            pool.close()


@pytest.mark.parametrize(("failure", "reason"), [
    (FileNotFoundError("private path"), "society_unavailable"),
    (TimeoutError("private response"), "society_timeout"),
])
def test_lookup_transport_failure_never_reads_execution(tmp_path, monkeypatch, failure, reason):
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_SOCKET", str(tmp_path / "absent.sock"))
    if isinstance(failure, FileNotFoundError):
        connect = Mock(side_effect=failure)
    else:
        connection = Mock()
        connection.makefile.side_effect = failure
        connect = Mock(return_value=(connection, tmp_path / "absent.sock"))
        monkeypatch.setattr(society_rpc_client, "request_auth", Mock(return_value={}))
    monkeypatch.setattr(society_rpc_client, "connect_service", connect)
    execution = SimpleNamespace(agent_data=Mock(), scheduler=Mock())
    assert execution_input_diagnostics(execution, {
        "actor": "reader", "resident_id": "resident_target", "message_id": "input",
    }) == {"schema": "tfi.execution-input-diagnostics.v1", "available": False, "reason": reason}
    assert connect.call_args.kwargs["restart_wait"] == 0
    assert execution.agent_data.mock_calls == execution.scheduler.mock_calls == []


@pytest.mark.parametrize("mode", ["success", "owner_error", "invalid_receipt", "window_closed", "codex"])
def test_runtime_activation_uses_only_the_signed_maintenance_write(tmp_path, monkeypatch, mode):
    from management_portal.society_service import resident_work_recovery
    from management_portal.society_service.transport import SocietyServiceClient

    path = tmp_path / "society-service.sock"
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_SOCKET", str(path))
    provider = "codex" if mode == "codex" else "kimi"
    monkeypatch.setattr(provider_updates, "activation_window", lambda _: nullcontext(mode != "window_closed"))
    monkeypatch.setattr(provider_updates, "_active_executions", lambda _: 1)
    events = []

    def prepare(*, provider, update_id):
        events.append(("prepare", provider, update_id))
        if mode == "owner_error":
            raise RuntimeError("cannot persist recovery")
        return {} if mode == "invalid_receipt" else {"prepared": 1}

    monkeypatch.setattr(resident_work_recovery, "prepare_provider_runtime_update", prepare)
    monkeypatch.setattr(provider_updates, "_switch_runtime", lambda *args, **kwargs: events.append(("switch",)) or True)
    runtime = SimpleNamespace(activation_ready=lambda _: True)
    pool = LineageReadProcessPool(0)
    with _society_socket(path, pool):
        client = SocietyServiceClient(path, timeout=1)
        with pytest.raises(ValueError, match="resident_id"):
            client.resident.prepare_provider_runtime_update(provider=provider, update_id="collision", resident_id="other")
        if mode in {"owner_error", "invalid_receipt"}:
            with pytest.raises(provider_updates.PreparationError) as error:
                provider_updates._activate(provider, runtime, tmp_path)
            assert error.value.phase == "service_activation"
            assert isinstance(error.value.__cause__, OperationError)
        else:
            assert provider_updates._activate(provider, runtime, tmp_path) == (mode != "window_closed")

    if mode in {"window_closed", "codex"}:
        assert events == ([] if mode == "window_closed" else [("switch",)])
    else:
        assert events[0][:2] == ("prepare", provider)
        assert events[0][2]
        assert events[1:] == ([("switch",)] if mode == "success" else [])
