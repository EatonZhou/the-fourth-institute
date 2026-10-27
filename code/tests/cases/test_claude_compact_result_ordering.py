"""Request-scoped Claude /compact outcomes across SDK response boundaries."""

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.cases.claude_sdk_support import load_claude_module


daemon_module = load_claude_module("claude_code_daemon")
ECHO = object()


def _result(text="", *, is_error=False):
    return daemon_module.ResultMessage(
        subtype="error_during_execution" if is_error else "success",
        duration_ms=10,
        duration_api_ms=10,
        is_error=is_error,
        num_turns=0,
        session_id="native",
        result=text,
        errors=[text] if is_error else None,
        usage=None,
    )


def _boundary(*, pre=255681, post=9843):
    return daemon_module.SystemMessage(
        subtype="compact_boundary",
        data={
            "session_id": "native",
            "compact_metadata": {
                "trigger": "manual",
                "pre_tokens": pre,
                "post_tokens": post,
                "duration_ms": 62000,
            },
        },
    )


class _OrderedCli:
    """Match receive_response stopping at each Result, including a stale one."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.sent = []
        self.prompt_uuid = ""

    async def query(self, prompt):
        async for message in prompt:
            self.sent.append(message["message"]["content"])
            self.prompt_uuid = message["uuid"]

    async def receive_response(self):
        while self.messages:
            message = self.messages.pop(0)
            if message is ECHO:
                message = daemon_module.UserMessage(
                    content="/compact", uuid=self.prompt_uuid
                )
            yield message
            if isinstance(message, daemon_module.ResultMessage):
                return


def _compact(cli, *, session_id="native"):
    async def run():
        daemon = daemon_module.ClaudeCodeDaemon()
        handle = daemon_module.SessionHandle(
            key="resident:test", signature=(), client=cli, session_id="native"
        )
        daemon._sessions[handle.key] = handle
        try:
            return await daemon.compact_session(
                {"session_key": handle.key, "session_id": session_id}
            )
        finally:
            assert handle.active_turn_id == ""
            assert not handle.lock.locked()

    return asyncio.run(run())


def test_manual_boundary_before_request_echo_is_preserved():
    cli = _OrderedCli(_boundary(), ECHO, _result())
    outcome = _compact(cli)
    assert outcome == {
        "session_id": "native",
        "compacted": True,
        "reason": "",
        "pre_tokens": 255681,
        "context_tokens": 9843,
        "duration_ms": 62000,
    }
    assert cli.sent == ["/compact"]
    assert not cli.messages


def _failed_status(reason="compact service unavailable"):
    return daemon_module.SystemMessage(
        subtype="status",
        data={"compact_result": "failed", "compact_error": reason},
    )


@pytest.mark.parametrize("before_echo", [True, False], ids=["before_echo", "after_echo"])
def test_previous_result_cannot_complete_current_request(before_echo):
    current = [_boundary(), ECHO] if before_echo else [ECHO, _boundary()]
    cli = _OrderedCli(_result("previous background answer"), *current, _result())
    outcome = _compact(cli)
    assert outcome["compacted"] is True
    assert outcome["context_tokens"] == 9843
    assert not cli.messages


@pytest.mark.parametrize("old_evidence", ["boundary", "failure"])
def test_evidence_before_previous_result_cannot_leak_into_current(old_evidence):
    old = _boundary(pre=1000, post=100) if old_evidence == "boundary" else _failed_status("old error")
    cli = _OrderedCli(old, _result(), ECHO, _result())
    assert _compact(cli) == {
        "session_id": "native", "compacted": False, "reason": "not_compacted"
    }
    assert not cli.messages


def test_current_boundary_replaces_old_metadata_after_previous_result():
    cli = _OrderedCli(
        _boundary(pre=1000, post=100), _result(), _boundary(), ECHO, _result()
    )
    outcome = _compact(cli)
    assert outcome["pre_tokens"] == 255681
    assert outcome["context_tokens"] == 9843
    assert outcome["compacted"] is True
    assert not cli.messages


def test_wrong_echo_does_not_make_previous_result_current():
    cli = _OrderedCli(
        daemon_module.UserMessage(content="old prompt", uuid="other-input"),
        _boundary(pre=1000, post=100),
        _result(),
        _boundary(), ECHO, _result(),
    )
    outcome = _compact(cli)
    assert outcome["context_tokens"] == 9843
    assert not cli.messages


@pytest.mark.parametrize("reason", ["compact service unavailable", "Not enough messages to compact."])
def test_failure_status_before_echo_preserves_reason(reason):
    cli = _OrderedCli(_failed_status(reason), ECHO, _result())
    assert _compact(cli) == {"session_id": "native", "compacted": False, "reason": reason}


def test_successful_result_without_boundary_is_not_compaction():
    assert _compact(_OrderedCli(ECHO, _result())) == {
        "session_id": "native", "compacted": False, "reason": "not_compacted"
    }


@pytest.mark.parametrize("before_echo", [True, False], ids=["before_echo", "after_echo"])
def test_current_error_result_still_raises(before_echo):
    messages = [] if before_echo else [_boundary(), ECHO]
    cli = _OrderedCli(*messages, _result("API error", is_error=True))
    with pytest.raises(RuntimeError, match="compaction failed: API error"):
        _compact(cli)


def test_boundary_without_current_result_does_not_claim_success():
    with pytest.raises(RuntimeError, match="ended without a result"):
        _compact(_OrderedCli(_boundary(), ECHO))


def test_session_mismatch_rejects_before_sending_compact():
    cli = _OrderedCli(_boundary(), ECHO, _result())
    with pytest.raises(ValueError, match="session identity changed"):
        _compact(cli, session_id="other-native")
    assert cli.sent == []


def _adapter_compact(cli, *, recorded=None):
    from management_portal.execution.backends import ClaudeBackend

    invocation = SimpleNamespace(
        request=SimpleNamespace(
            runtime_options={"provider_profile": "test", "session_key": "resident:test", "model": "m"},
            operation="compact", input_media=(), execution_id="execution-test", cwd="/work",
            permission_profile="danger-full-access", session=SimpleNamespace(binding_id="resident:test"),
            metadata={},
        ),
        session=SimpleNamespace(backend_route_id="test", backend_session_id="native"),
        user_prompt="", system_context="", attach_system_context=False, is_new_backend_session=False,
        recovery_checkpoint={}, provider_error_recovery=None, provider_event_processor=None,
        harness_tools=(), tool_batch_processor=None,
    )
    backend = ClaudeBackend()
    backend.client_factory = lambda: SimpleNamespace(
        session_status=lambda *_args: {"session_id": "native", "loaded": True, "active": False},
        compact_session=lambda **_kwargs: _compact(cli),
    )
    context = mock.Mock()
    context.is_cancelled.return_value = False
    if recorded is None:
        recorded = mock.Mock()
    with mock.patch.object(ClaudeBackend, "record_provider_compaction", new=recorded):
        result = backend.execute(invocation, context)
    return result, recorded


def test_adapter_records_confirmed_pre_echo_boundary_once():
    result, recorded = _adapter_compact(_OrderedCli(_boundary(), ECHO, _result()))
    recorded.assert_called_once()
    assert recorded.call_args.kwargs["backend_session_id"] == "native"
    assert result.output["compacted"] is True
    assert result.usage == {"context": {"inputTokens": 9843, "totalTokens": 9843}}


def test_adapter_history_too_short_does_not_record_compaction():
    result, recorded = _adapter_compact(_OrderedCli(
        _failed_status("Not enough messages to compact."), ECHO, _result()
    ))
    recorded.assert_not_called()
    assert result.output["compacted"] is False
    assert result.output["message"] == "没有足够的历史可以压缩，会话未改变。"


def test_adapter_does_not_record_failed_result_even_with_boundary():
    recorded = mock.Mock()
    with pytest.raises(RuntimeError, match="compaction failed: API error"):
        _adapter_compact(
            _OrderedCli(_boundary(), ECHO, _result("API error", is_error=True)),
            recorded=recorded,
        )
    recorded.assert_not_called()


def test_failure_after_boundary_clears_success_and_token_metadata():
    cli = _OrderedCli(_boundary(), _failed_status("compact failed"), ECHO, _result())
    assert _compact(cli) == {
        "session_id": "native", "compacted": False, "reason": "compact failed"
    }


@pytest.mark.parametrize("mismatch_in", ["boundary", "result"])
def test_current_stream_session_mismatch_never_reports_compacted(mismatch_in):
    boundary, result = _boundary(), _result()
    if mismatch_in == "boundary":
        boundary.data["session_id"] = "other-native"
    else:
        result.session_id = "other-native"
    cli = _OrderedCli(boundary, ECHO, result)

    async def run():
        daemon = daemon_module.ClaudeCodeDaemon()
        handle = daemon_module.SessionHandle(
            key="resident:test", signature=(), client=cli, session_id="native"
        )
        daemon._sessions[handle.key] = handle
        daemon._session_ids["native"] = handle.key
        with pytest.raises(ValueError, match="session identity changed"):
            await daemon.compact_session(
                {"session_key": handle.key, "session_id": "native"}
            )
        assert handle.session_id == "native"
        assert daemon._sessions == {handle.key: handle}
        assert daemon._session_ids == {"native": handle.key}
        assert handle.active_turn_id == ""
        assert not handle.lock.locked()

    asyncio.run(run())
