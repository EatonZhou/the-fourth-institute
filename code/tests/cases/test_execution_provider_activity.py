"""Provider activity: native events of the Claude and Kimi Adapters, and redaction of sensitive values
before they are stored.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.execution import (
    backend_contracts,
    claude_backend,
    kimi_backend,
    provider_activity_events,
)
from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import SchedulerClient
from management_portal.execution.session_manager import RuntimeSession
from tests.cases.execution_layer_test_support import _stack, _close


def test_provider_activity_is_redacted_before_scheduler_and_trace_persistence(
    tmp_path: Path,
) -> None:
    """Provider 活动中的令牌、Cookie 和密钥在写入调度器和活动记录前被遮蔽"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    worker = SchedulerClient(
        worker_id="activity-redaction-worker",
        build_id="test-build",
        capabilities=["agent_session:codex"],
        service_client=client,
    )
    synthetic_secret = "sentinel-activity-secret"
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="activity redaction",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        service.submit(request)
        claim = worker.claim(wait_seconds=0.1)

        with mock.patch(
            "management_portal.execution.service.append_execution_activity",
        ) as append_activity:
            client.request(
                "scheduler.event",
                {
                    "execution_id": request.execution_id,
                    "worker_id": "activity-redaction-worker",
                    "claim_token": claim["claim_token"],
                    "event_type": "provider_event",
                    "details": {
                        "method": "guardianWarning",
                        "params": {
                            "Authorization": f"Bearer {synthetic_secret}",
                            "headers": {
                                "Set-Cookie": f"session={synthetic_secret}",
                                "X-API-Key": synthetic_secret,
                                "X-Request-ID": "request-safe",
                            },
                            "items": [{"clientSecret": synthetic_secret}],
                            "error": {
                                "additionalDetails": (
                                    f"token={synthetic_secret}"
                                ),
                            },
                        },
                    },
                },
            )

        projected_events = append_activity.call_args.args[1]
        snapshot = service.status(request.execution_id, actor="sample_admin")
        persisted_json = json.dumps(projected_events, sort_keys=True)
        status_json = json.dumps(snapshot["events"], sort_keys=True)
        assert synthetic_secret not in persisted_json
        assert synthetic_secret not in status_json
        assert "[REDACTED]" in persisted_json
        assert "[REDACTED]" in status_json
        assert "request-safe" in persisted_json
        assert "request-safe" in status_json
    finally:
        _close(server, thread)


def test_claude_backend_projects_provider_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """claude 后端把原生事件作为 provider_event 上报，并传递权限配置"""
    raw_event = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "Read",
            "call_id": "tool-1",
            "arguments": {"path": "/tmp/example.md"},
        },
    }
    client = mock.Mock()

    def run_turn(**kwargs):
        kwargs["event_callback"](raw_event)
        return mock.Mock(
            response="done",
            session_id="claude-session-1",
            turn_id="claude-turn-1",
            usage={},
        )

    client.run_turn.side_effect = run_turn
    monkeypatch.setattr(
        claude_backend,
        "shared_claude_client",
        lambda: client,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="claude",
        operation="turn",
        cwd="/tmp",
        permission_profile="portal-closed-cwd",
        session=SessionOptions(
            mode="persistent",
        binding_id="provider:test-session",
        ),
    )
    events: list[tuple[str, dict]] = []

    claude_backend.ClaudeBackend().execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="claude-session-1",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda name, details: events.append((name, details)),
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert events == [("provider_event", raw_event)]
    assert (
        client.run_turn.call_args.kwargs["permission_profile"]
        == "portal-closed-cwd"
    )


def test_kimi_backend_projects_provider_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kimi 后端把原生事件作为 provider_event 上报"""
    raw_event = {
        "method": "item/completed",
        "params": {
            "threadId": "kimi-session-1",
            "turnId": "kimi-turn-1",
            "item": {"id": "kimi:turn:assistant:0", "type": "agentMessage", "text": "Inspecting the workspace"},
        },
    }
    client = mock.Mock()

    def run_turn(**kwargs):
        kwargs["event_callback"](raw_event)
        return mock.Mock(
            response="done",
            session_id="kimi-session-1",
            turn_id="kimi-turn-1",
            usage={},
        )

    client.run_turn.side_effect = run_turn
    monkeypatch.setattr(
        kimi_backend,
        "shared_kimi_client",
        lambda: client,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="kimi",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
        binding_id="provider:test-session",
        ),
    )
    events: list[tuple[str, dict]] = []

    kimi_backend.KimiBackend().execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="kimi-session-1",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda name, details: events.append((name, details)),
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert events == [("provider_event", raw_event)]


def test_provider_activity_recursively_redacts_sensitive_values() -> None:
    """Provider 活动的命令、URL、参数、警告和工具输出中的密钥被递归遮蔽"""
    synthetic_secret = "sentinel-provider-secret"
    compact = provider_activity_events._compact_provider_activity_event({
        "method": "item/completed",
        "params": {
            "item": {
                "id": "tool-1",
                "type": "functionCall",
                "status": "completed",
                "command": f"API_TOKEN={synthetic_secret} python3 safe.py",
                "display": {
                    "url": (
                        "https://user:password@example.test/run?"
                        f"token={synthetic_secret}&mode=safe"
                    ),
                },
                "arguments": {
                    "api_key": synthetic_secret,
                    "headers": {
                        "Authorization": f"Bearer {synthetic_secret}",
                    },
                    "query": "public status",
                },
            },
        },
    })
    warning = provider_activity_events._compact_provider_activity_event({
        "method": "guardianWarning",
        "params": {
            "warning": f"credential={synthetic_secret}",
            "error": {
                "message": f"password: {synthetic_secret}",
                "additionalDetails": (
                    f"authorization: Bearer {synthetic_secret}\n"
                    "https://example.test/failure?"
                    f"access_token={synthetic_secret}"
                ),
            },
        },
    })
    tool_output = provider_activity_events._compact_provider_activity_event({
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": "tool-1",
            "output": f"cookie={synthetic_secret} safe-output",
        },
    })

    serialized = json.dumps([compact, warning, tool_output], sort_keys=True)
    assert synthetic_secret not in serialized
    assert "password@example.test" not in serialized
    assert "[REDACTED]" in serialized
    assert "public status" in serialized
    assert "mode=safe" in serialized
    assert "safe-output" in serialized


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
