"""Page reads cannot start native discovery or wait through service restarts.

:module: execution
:design: decision_25eb93d803a4, decision_44f8f67d7390
"""
import json
import threading
import time
from unittest.mock import Mock

import pytest

from management_portal import antigravity_accounts as accounts
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution.service import ExecutionService, ExecutionServiceServer
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.transport import ExecutionServiceClient, ExecutionServiceUnavailable, ExecutionResponseUnknown
from management_portal.society_service import resident_execution_provider, resident_access, resident_protocol, resident_backend
from tests.cases.test_lineage_backend import lineage_store, sample_lineage_payload  # noqa: F401


@pytest.mark.parametrize("observation,expected", [
    ({"capabilities": {"media": True}, "prompt_capabilities": {"image": True}, "observed_at": 1}, True),
    ({"capabilities": {"media": False}, "prompt_capabilities": {"image": False}, "observed_at": 1}, False),
    ({"capabilities": {"media": True}, "observed_at": 1}, False),
    (None, False), ("broken", False),
])
def test_expired_or_unknown_capability_reads_never_probe(tmp_path, monkeypatch, observation, expected):
    monkeypatch.setattr(accounts, "account_for_route", lambda route: {"account_id": "antigravity_one"})
    monkeypatch.setattr(accounts, "account_runtime", lambda *_: tmp_path)
    if observation is not None:
        (tmp_path / "catalog.json").write_text(observation if isinstance(observation, str) else json.dumps(observation))
    native = Mock(side_effect=AssertionError("page read must not spawn ACP"))
    monkeypatch.setattr(accounts, "AcpClient", native)
    record = {"backend": "antigravity", "backend_route_id": "one"}
    assert resident_execution_provider.provider_for(record).capabilities.supports_media is expected
    assert resident_execution_provider.capability_projection(record)["image_input"] is expected
    assert AGENT_PROVIDERS["antigravity"]._route_takes_images("one") is expected
    record["resident_id"] = "resident_111111111111"
    monkeypatch.setattr(resident_backend, "execution_account", lambda _: {"backend_route_id": "one"})
    monkeypatch.setattr(resident_access, "visible_resident_records", lambda *_: [record])
    monkeypatch.setattr(resident_access, "actor_can_manage_residents", lambda *_: True)
    monkeypatch.setattr(resident_protocol, "read_resident_state_snapshots", lambda *a, **k: {
        record["resident_id"]: {"state": {"work_state": "idle"}}})
    items = resident_protocol.get_resident_states([record["resident_id"]], "test")["items"]
    assert items[0]["controls"]["accepts_images"] is expected
    native.assert_not_called()


def test_states_fail_promptly_during_restart_without_fabricating_state(tmp_path):
    service = ExecutionService(client=ExecutionServiceClient(tmp_path / "missing.sock", restart_wait=25))
    start = time.monotonic()
    with pytest.raises(ExecutionServiceUnavailable):
        service.states({"execution-test": "test"})
    assert time.monotonic() - start < 1.5


def test_blocked_maintenance_does_not_block_rpc_or_shutdown(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    server = ExecutionServiceServer(tmp_path / "execution.sock", Scheduler())
    def maintain():
        entered.set()
        release.wait(5)
    monkeypatch.setattr(server, "run_maintenance", maintain)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.started.wait(2) and entered.wait(2)
        assert ExecutionServiceClient(server.socket_path).request("execution.states", {"executions": {}}, timeout=1) == {}
        start = time.monotonic()
        server.shutdown()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert time.monotonic() - start < 1
        assert not release.is_set()  # Shutdown did not wait for maintenance.
    finally:
        release.set()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("failure", [ExecutionServiceUnavailable, ExecutionResponseUnknown])
def test_get_lineage_preserves_definition_during_execution_restart(lineage_store, monkeypatch, warm, failure):
    from management_portal.society_service import lineage_lifecycle, lineage_reads, resident_execution_storage
    from management_portal.society_service.domain_ports import execution_port
    from types import SimpleNamespace
    created = lineage_lifecycle.create_lineage(sample_lineage_payload(), "sample_admin")
    identifier = created["lineage_id"]
    job = {"execution_id": "execution-test", "actor": "sample_admin", "submitted_at": "2026-11-02T00:00:00Z"}
    monkeypatch.setattr(resident_execution_storage, "read", lambda connection, ids, **kwargs: {
        rid: (1, dict(job)) for rid in ids})
    unavailable = False
    calls = []
    def states(executions):
        calls.append(executions)
        if unavailable:
            raise failure("restarting")
        return {}
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(states=states, binding_quota_states=lambda batch: {}))
    if warm:
        lineage_reads.get_lineage(identifier, "sample_admin")
    unavailable = True
    view = lineage_reads.get_lineage(identifier, "sample_admin")
    assert calls  # Real ConversationProjection.states reached the Execution port.
    assert view["lineage_id"] == identifier
    assert [n["id"] for n in view["nodes"]] == [n["id"] for n in created["nodes"]]
    assert [(e["id"], e["source"], e["target"], e["label"]) for e in view["edges"]] == [
        (e["id"], e["source"], e["target"], e["label"]) for e in created["edges"]]
    assert view["resident_runtime_observation"] == {"status": "unavailable", "source": "execution"}
    assert all(n["status"] == "unknown" for n in view["nodes"])
    definition = lineage_reads.get_lineage_definition_for_analysis(identifier, "sample_admin")
    assert "resident_runtime_observation" not in definition
    assert all(n.get("status") != "unknown" for n in definition["nodes"])
    unavailable = False
    recovered = lineage_reads.get_lineage(identifier, "sample_admin")
    assert "resident_runtime_observation" not in recovered
    assert all(n.get("status") != "unknown" for n in recovered["nodes"])
