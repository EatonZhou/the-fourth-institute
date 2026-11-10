from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from management_portal.execution.service_rpc import execution_generation_source


@pytest.mark.parametrize("reported", [False, True])
def test_source_uses_original_request_and_actual_account(monkeypatch, reported):
    from management_portal import permission_system
    monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, args: actor == "caller" and args == {"resident_id": "resident_original"})
    server = SimpleNamespace(agent_data=SimpleNamespace(execution=lambda _: {"agent_id": "resident_original"}),
        scheduler=SimpleNamespace(snapshot=Mock(return_value={"request": {"agent_id": "resident_original", "provider": "kimi", "runtime_options": {"model": "original-model"}, "session": {"backend_route_id": "requested-account"}, "prompt": "private"},
            "result": {"backend_route_id": "actual-original-account", "usage": {"model": "reported-model"} if reported else {}, "output": {"private": True}}, "scheduler": {"created_at": "2026-11-10T06:00:00Z"}})))
    result = execution_generation_source(server, {"actor": "caller", "execution_id": "original-execution"})
    assert result["model"] == ("reported-model" if reported else "original-model")
    assert result["account_id"] == "actual-original-account"
    assert "prompt" not in result and "request" not in result and "output" not in result
    with pytest.raises(PermissionError):
        execution_generation_source(server, {"actor": "denied", "execution_id": "original-execution"})
