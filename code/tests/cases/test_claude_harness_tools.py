from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution import claude_backend, dsh_backend
from management_portal.execution.native_harness_tools import prepare_tools
from management_portal.execution.harness import HarnessTool, AgentLoopToolBatch
from tests.cases.test_turn_input_during_turn import _native_context, _native_invocation


def test_hidden_harness_tool_schema_is_not_advertised():
    """设为隐藏的 harness 工具不出现在工具目录中，调用时返回错误"""
    def disabled(arguments):
        raise PermissionError("Disabled for this turn")

    invocation = SimpleNamespace(harness_tools=(HarnessTool(
        "disabled", "Hidden", {"type": "object"}, disabled, expose_schema=False),),
        tool_batch_processor=None)
    catalog, dispatch = prepare_tools(invocation, SimpleNamespace(is_cancelled=lambda: False))
    assert catalog == []
    assert dispatch({"tool": "disabled", "arguments": {}})["isError"] is True


def test_claude_tools_keep_worker_authority_and_process_results():
    """Claude 只能调用已授予的 harness 工具，结果经批处理返回，取消后拒绝调用"""
    handler = mock.Mock(return_value="authorized result")
    processor = mock.Mock()
    processor.process.side_effect = lambda batch: batch
    invocation = SimpleNamespace(harness_tools=(HarnessTool(
        "read_resource", "Read", {"type": "object"}, handler),), tool_batch_processor=processor)
    context = SimpleNamespace(is_cancelled=lambda: False)
    catalog, dispatch = prepare_tools(invocation, context)
    assert catalog == [{"name": "read_resource", "description": "Read", "inputSchema": {"type": "object"}}]
    assert dispatch({"tool": "read_resource", "arguments": {"id": "r"}, "call_id": "c"}) == {
        "isError": False, "content": [{"type": "text", "text": "authorized result"}]}
    handler.assert_called_once_with({"id": "r"})
    assert isinstance(processor.process.call_args.args[0], AgentLoopToolBatch)
    with pytest.raises(ValueError, match="Unknown"):
        dispatch({"tool": "not_granted", "arguments": {}})
    context.is_cancelled = lambda: True
    with pytest.raises(InterruptedError):
        dispatch({"tool": "read_resource", "arguments": {}})


@pytest.mark.parametrize("backend_class", [dsh_backend.DshBackend, claude_backend.ClaudeBackend])
def test_a_native_socket_turn_offers_portal_tools_and_hands_their_results_to_the_harness(backend_class):
    """Claude Code 与 DSH 的回合提供 Portal 工具并把结果交给 Harness

    经本地 socket 运行的回合把 Harness 工具目录交给 Provider；Provider 调用工具时由 Worker 执行处理函数，结果按通用形状返回，并经过工具批次处理器。

    :importance: P1
    :design: decision_5c62deb8253a
    """
    handler = mock.Mock(return_value="resource text")
    processor = mock.Mock()
    processor.process.side_effect = lambda batch: batch
    results = []

    def run_turn(**kwargs):
        assert [tool["name"] for tool in kwargs["tools"]] == ["read_resource"]
        results.append(kwargs["tool_callback"]({"tool": "read_resource", "arguments": {"id": "r"}, "call_id": "c1"}))
        return SimpleNamespace(response="done", session_id="dsh-session-1", turn_id=kwargs["turn_id"], usage={})

    invocation = _native_invocation()
    invocation.harness_tools = (HarnessTool("read_resource", "Read", {"type": "object"}, handler),)
    invocation.tool_batch_processor = processor
    backend = backend_class()
    backend.client_factory = lambda: SimpleNamespace(run_turn=run_turn, steer=None, close_session=lambda *a: {})
    with mock.patch.object(backend_class, "record_provider_compaction"), \
            mock.patch.object(backend_class, "observe_turn_usage", return_value=None), \
            mock.patch.object(backend_class, "prepare_native_session"):
        backend.execute(invocation, _native_context())

    handler.assert_called_once_with({"id": "r"})
    assert results == [{"isError": False, "content": [{"type": "text", "text": "resource text"}]}]
    assert isinstance(processor.process.call_args.args[0], AgentLoopToolBatch)


def test_claude_tool_failure_remains_an_error_result():
    """Claude 工具调用抛出异常时返回 isError 结果"""
    invocation = SimpleNamespace(harness_tools=(HarnessTool(
        "read_resource", "Read", {"type": "object"}, mock.Mock(side_effect=PermissionError("denied"))),),
        tool_batch_processor=None)
    _, dispatch = prepare_tools(invocation, SimpleNamespace(is_cancelled=lambda: False))
    assert dispatch({"tool": "read_resource", "arguments": {}})["isError"]


def test_claude_home_status_uses_daemon_not_api_credentials(monkeypatch):
    """首页 Claude 账户状态通过 daemon 读取用量，不使用 API 凭据"""
    from management_portal.execution.account_adapters import ClaudeAccounts
    from management_portal.execution.accounts import ExecutionAccount
    from management_portal import claude_code_client
    client = mock.Mock()
    client.account_usage.return_value = {"quota_status": "ok", "rate_limits": []}
    monkeypatch.setattr(claude_code_client, "shared_client", lambda: client)
    account = ExecutionAccount("claude_one", "claude", "claude_one", "account:claude_one", quota_policy="reserve")
    adapter = object.__new__(ClaudeAccounts)
    payload = adapter.observe(account)
    assert payload["status"] == "ok"
    client.account_usage.assert_called_once_with("claude_one", force_refresh=False)
