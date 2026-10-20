"""The one instruction address hands the call to routing unchanged and returns its result."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from management_portal import asgi
from management_portal.hatg import routing
from management_portal.hatg.contracts import InstructionResult


def _post(payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "method": "POST", "path": "/api/instructions/execute", "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    response = asyncio.run(asgi.instruction_execute(Request(scope, receive)))
    return response.status_code, json.loads(response.body)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list:
    monkeypatch.setattr(asgi, "_instruction_principal", lambda _request: ("sample_admin", "web"))
    monkeypatch.setattr(asgi, "_same_origin", lambda _request: True)
    return []


def test_instruction_endpoint_delegates_the_unmodified_call(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """指令接口把调用原样交给路由执行并返回其结果"""
    arguments = {"resident_id": "resident_aaaaaaaaaaaa", "before_message_id": "message_before", "limit": 50}

    def execute(call, context):
        calls.append((call, context))
        return InstructionResult(instruction_id="instruction_000000000001", instruction=call.instruction,
                                 status="committed", result={"messages": [{"message_id": "message_older"}]})

    monkeypatch.setattr(routing, "execute", execute)
    status, body = _post({"instruction": "ListMessages", "arguments": arguments, "idempotency_key": "page-1"})

    assert status == 200
    call, context = calls[0]
    assert (call.instruction, dict(call.arguments), call.idempotency_key) == ("ListMessages", arguments, "page-1")
    assert (context.caller, context.entry) == ("sample_admin", "web")
    assert body["status"] == "committed" and body["result"] == {"messages": [{"message_id": "message_older"}]}


def test_instruction_endpoint_preserves_executor_errors(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """指令接口原样返回执行器的错误，并给出 400 状态码"""
    error = {"code": "instruction.invalid_arguments", "message": "invalid page token", "retryable": False,
             "commit_state": "not_committed", "details": {"argument": "before_message_id"}}
    monkeypatch.setattr(routing, "execute", lambda call, context: InstructionResult(
        instruction_id="instruction_000000000002", instruction=call.instruction, status="failed", error=error,
    ))
    status, body = _post({"instruction": "ListMessages", "arguments": {}})

    assert status == 400
    assert (body["status"], body["error"]) == ("failed", error)


def test_instruction_endpoint_accepts_arguments_larger_than_64_kib(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """指令接口接受超过 64 KiB 的参数"""
    def execute(call, context):
        return InstructionResult(instruction_id="instruction_000000000003", instruction=call.instruction,
                                 status="committed", result={"bytes": len(call.arguments["content"])})

    monkeypatch.setattr(routing, "execute", execute)
    status, body = _post({"instruction": "UpdateWorkspaceFile", "arguments": {"content": "x" * 70_000}})
    assert (status, body["result"]) == (200, {"bytes": 70_000})


@pytest.mark.parametrize("entry,status", [("web", 403), ("cli", 200)])
def test_foreign_origin_blocks_page_sessions_but_not_bearer_callers(monkeypatch: pytest.MonkeyPatch, entry, status) -> None:
    """跨源请求不能使用页面会话调用指令，Bearer 令牌调用者不受影响"""
    monkeypatch.setattr(asgi, "_instruction_principal", lambda _request: ("sample_admin", entry))
    monkeypatch.setattr(asgi, "_same_origin", lambda _request: False)
    monkeypatch.setattr(routing, "execute", lambda call, context: InstructionResult(
        instruction_id="instruction_000000000004", instruction=call.instruction, status="committed", result={},
    ))
    assert _post({"instruction": "GetUser", "arguments": {}})[0] == status
