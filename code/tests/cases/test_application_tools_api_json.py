"""HTTP JSON decoding at the application tool boundary.

:module: execution.harness
:importance: P2
"""
import pytest
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.routing import Route
from starlette.testclient import TestClient

from management_portal import application_tools, asgi


PATH = "/api/application-tools/execute"


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setattr(asgi, "_instruction_principal", lambda _: ("alice", "web"))
    monkeypatch.setattr(asgi, "_same_origin", lambda _: True)
    calls = []

    def execute(payload, context):
        calls.append((payload, context))
        return {"status": "committed", "result": {"value": "hello"}}

    monkeypatch.setattr(application_tools, "execute", execute)
    with TestClient(Starlette(routes=[Route(PATH, asgi.application_tools_api, methods=["POST"])])) as client:
        yield client, calls


@pytest.mark.parametrize("body", [b'{"tool":', b'{"tool":"\xff"}'], ids=["syntax", "encoding"])
def test_bad_json_returns_invalid_arguments_without_dispatch(endpoint, body):
    client, calls = endpoint
    response = client.post(PATH, content=body, headers={"Content-Type": "application/json"})
    assert calls == []
    assert response.status_code == 400
    payload = response.json()
    assert payload["schema"] == "tfi.application-tool-result.v1"
    assert payload["status"] == "failed"
    assert payload["tool"] == "" and payload["call_id"] and payload["replayed"] is False
    assert payload["error"]["code"] == "instruction.invalid_arguments"
    assert payload["error"]["commit_state"] == "not_committed"


@pytest.mark.parametrize("error_type", [ClientDisconnect, OSError, RuntimeError])
def test_unexpected_body_read_failure_propagates(endpoint, monkeypatch, error_type):
    client, calls = endpoint
    failure = error_type("body read failed")

    async def broken_json(self):
        raise failure

    monkeypatch.setattr(Request, "json", broken_json)
    with pytest.raises(error_type) as raised:
        client.post(PATH, json={})
    assert raised.value is failure
    assert calls == []


def test_valid_json_preserves_authenticated_call(endpoint):
    client, calls = endpoint
    payload = {"schema": "tfi.application-tool-call.v1", "tool": "Example", "arguments": {"value": 1}}
    response = client.post(PATH, json=payload, headers={"X-Portal-Resource-Lifecycle": "lifecycle-test"})
    assert response.status_code == 200
    assert response.json() == {"status": "committed", "result": {"value": "hello"}}
    [(forwarded, context)] = calls
    assert forwarded == payload
    assert context.caller == "alice" and context.entry == "web"
    assert context.resource_lifecycle_id == "lifecycle-test"
