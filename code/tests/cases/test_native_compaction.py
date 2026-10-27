"""Claude Code and DSH compact a session's history on demand (``/compact``).

Both Providers have their own manual compaction: Claude Code's ``/compact``
command and DSH's ``compactNow`` (what its ``/compact`` runs).  The daemon and
the bridge expose it as ``session/compact``; the Adapters run Portal's
``compact`` operation with it, so ``/compact``, the Compact Lineage component
and the chat page's compact button work as they do for Codex and Kimi Code.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import dsh_client
from management_portal.execution import backends
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.society_service.resident_session_backend_domain import (
    compact_portal_result as _compact_portal_result,
)
from tests.cases.claude_sdk_support import load_claude_module


def test_every_session_provider_compacts_on_demand() -> None:
    """每种会话型 Provider 都支持按需压缩会话历史"""
    assert all("compact" in provider.operations for provider in AGENT_PROVIDERS.values())


# --- the Adapters -------------------------------------------------------------------------------


def _invocation(session_id: str = "native-1"):
    return SimpleNamespace(
        request=SimpleNamespace(
            runtime_options={"provider_profile": "deepseek_primary", "session_key": "resident:resident_x",
                             "model": "m", "effort": "high", "is_owner": True, "config_dir": "/cfg"},
            operation="compact", input_media=(), execution_id="execution-c1", cwd="/work",
            permission_profile="danger-full-access", session=SimpleNamespace(binding_id="resident:resident_x"),
            metadata={},
        ),
        session=SimpleNamespace(backend_route_id="deepseek_primary", backend_session_id=session_id),
        user_prompt="", system_context="", attach_system_context=False, is_new_backend_session=False,
        recovery_checkpoint={}, provider_error_recovery=None, provider_event_processor=None,
        harness_tools=(), tool_batch_processor=None,
    )


def _compact(backend_class, outcome: dict, session_id: str = "native-1"):
    calls: list[dict] = []

    def compact_session(**kwargs):
        calls.append(kwargs)
        return outcome

    backend = backend_class()
    backend.client_factory = lambda: SimpleNamespace(compact_session=compact_session)  # type: ignore[method-assign]
    context = mock.Mock()
    context.is_cancelled.return_value = False
    with mock.patch.object(backend_class, "record_provider_compaction") as recorded:
        result = backend.execute(_invocation(session_id), context)
    return result, calls, recorded


@pytest.mark.parametrize("backend_class", [backends.ClaudeBackend, backends.DshBackend])
def test_the_adapter_compacts_and_records_the_compaction(backend_class) -> None:
    """适配器执行压缩并记录这次压缩"""
    result, calls, recorded = _compact(backend_class, {
        "compacted": True, "session_id": "native-1", "pre_tokens": 17278, "context_tokens": 3034,
    })

    assert calls[0]["session_id"] == "native-1" and calls[0]["session_key"] == "resident:resident_x"
    assert calls[0]["model"] == "m" and calls[0]["request_id"] == "execution-c1"
    recorded.assert_called_once()
    assert recorded.call_args.kwargs["backend_session_id"] == "native-1"
    assert result.output["operation"] == "compact" and result.output["compacted"] is True
    assert result.output["message"] == "上下文从约 17,278 token 压缩到约 3,034 token。"
    # Only the context size: the summary's request is not reported, so no token use is recorded.
    assert result.usage == {"context": {"inputTokens": 3034, "totalTokens": 3034}}


def test_a_dsh_compaction_reports_what_it_replaced() -> None:
    """DSH 压缩后报告替换了哪些历史"""
    result, _calls, _recorded = _compact(backends.DshBackend, {
        "compacted": True, "shadowed_items": 11, "shadowed_tokens": 1877, "context_tokens": 1377,
    })
    assert result.output["message"] == "较早的 11 条历史已换成一份摘要（约 1,877 token），压缩后上下文约 1,377 token。"


@pytest.mark.parametrize("reason", ["Not enough messages to compact.", "nothing_to_compact", "summary_not_smaller"])
def test_a_history_too_short_to_compact_is_left_as_it_is(reason) -> None:
    """历史太短无法压缩时保持原样"""
    result, _calls, recorded = _compact(backends.ClaudeBackend, {"compacted": False, "reason": reason})
    recorded.assert_not_called()
    assert result.output["compacted"] is False
    assert result.output["message"] == "没有足够的历史可以压缩，会话未改变。"
    assert result.usage == {}


def test_a_compaction_the_provider_could_not_run_fails() -> None:
    """Provider 无法执行的压缩以失败结束"""
    with pytest.raises(RuntimeError, match="did not compact: busy"):
        _compact(backends.DshBackend, {"compacted": False, "reason": "busy"})


def test_a_session_without_provider_history_has_nothing_to_compact() -> None:
    """没有 Provider 历史的会话没有可压缩的内容"""
    with pytest.raises(ValueError, match="no Provider history"):
        _compact(backends.ClaudeBackend, {"compacted": True}, session_id="")


def test_the_completed_message_says_what_the_provider_did() -> None:
    """压缩完成消息说明 Provider 实际做了什么"""
    done = _compact_portal_result("completed", "Resident Session", backend_label="DSH", detail="压缩已完成。")
    assert done["content"] == "DSH compact 已完成。压缩已完成。"
    assert _compact_portal_result("completed", "x", backend_label="Codex")["content"] == "Codex compact 已完成。"


# --- the DSH client -----------------------------------------------------------------------------


def test_the_dsh_client_compacts_the_session_in_its_own_process(monkeypatch) -> None:
    """DSH 客户端在会话自己的进程中压缩

    DshClient.compact_session 用校验后的模型与推理强度，向会话所在的 DSH 进程发一次 session/compact，并返回其结果，不重启进程。

    :importance: P2
    """
    requests: list[tuple[str, dict]] = []

    class Transport:
        def _request(self, method, params=None, **_kwargs):
            requests.append((method, params))
            return {"compacted": True}

    client = dsh_client.DshClient()
    monkeypatch.setattr(dsh_client.DshClient, "_settings", staticmethod(lambda params: ("deepseek-v4-pro", "high")))
    monkeypatch.setattr(client, "_start", lambda session_id, params: Transport())
    closed = mock.Mock()
    monkeypatch.setattr(client, "close_session", closed)

    outcome = client.compact_session(session_key="k", session_id="dsh-abc", provider_profile="deepseek_primary",
                                     cwd="/work", model="", effort="", resident_environment={}, is_owner=True)

    assert outcome == {"compacted": True}
    closed.assert_not_called()
    assert requests == [("session/compact", {
        "cwd": "/work", "provider_profile": "deepseek_primary", "model": "deepseek-v4-pro", "effort": "high",
        "resident_environment": {}, "session_id": "dsh-abc",
    })]


def test_the_dsh_bridge_compacts_only_an_idle_session() -> None:
    """DSH 桥接只压缩空闲会话"""
    from pathlib import Path

    bridge = (Path(backends.__file__).resolve().parents[1] / "dsh_runtime" / "bridge.mjs").read_text(encoding="utf-8")
    block = bridge[bridge.index("message.method === 'session/compact'"):bridge.index("message.method === 'session/close'")]
    assert "if (current || admitted || compacting) return {compacted: false, reason: 'busy'};" in block
    assert "ctx.compaction.compactNow(handle.agent," in block
    assert "'compaction'" in bridge[:bridge.index("export function apply")]
    # A turn does not start, and the process does not exit, while it compacts.
    assert "if (compacting) throw new Error('Session is compacting');" in bridge
    assert "if (!current && !compacting && Date.now() - lastUsed > 300_000)" in bridge
    assert "session_compact: true" in bridge


# --- the Claude daemon --------------------------------------------------------------------------

daemon_module = load_claude_module("claude_code_daemon")


class _Cli:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.sent: list[object] = []
        self.prompt_uuid = ""

    async def query(self, prompt):
        async for message in prompt:
            self.sent.append(message["message"]["content"])
            self.prompt_uuid = message["uuid"]

    async def receive_response(self):
        yield daemon_module.UserMessage(content="/compact", uuid=self.prompt_uuid)
        while self.messages:
            yield self.messages.pop(0)


def _daemon_compact(cli: _Cli, session_id: str = "native"):
    async def run():
        daemon = daemon_module.ClaudeCodeDaemon()
        handle = daemon_module.SessionHandle(key="resident:test", signature=(), client=cli, session_id="native")
        daemon._sessions["resident:test"] = handle
        result = await daemon.compact_session({"session_key": "resident:test", "session_id": session_id})
        return result, handle

    return asyncio.run(run())


def _result(text: str = "", *, is_error: bool = False):
    return daemon_module.ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=10, is_error=is_error, num_turns=0,
        session_id="native", result=text, errors=None, usage=None,
    )


def test_the_daemon_compacts_with_claude_codes_own_command() -> None:
    """守护进程用 Claude Code 自带命令压缩"""
    cli = _Cli(
        daemon_module.SystemMessage(subtype="status", data={"status": "compacting", "session_id": "native"}),
        daemon_module.SystemMessage(subtype="compact_boundary", data={"session_id": "native", "compact_metadata": {
            "trigger": "manual", "pre_tokens": 17278, "post_tokens": 3034, "duration_ms": 34453}}),
        _result(),
    )
    result, handle = _daemon_compact(cli)
    assert cli.sent == ["/compact"]
    assert result == {"session_id": "native", "compacted": True, "reason": "", "pre_tokens": 17278,
                      "context_tokens": 3034, "duration_ms": 34453}
    assert handle.active_turn_id == "" and not handle.lock.locked()


def test_the_daemon_reports_a_history_too_short_to_compact() -> None:
    """守护进程报告历史太短无法压缩"""
    cli = _Cli(
        daemon_module.SystemMessage(subtype="status", data={
            "status": None, "compact_result": "failed", "compact_error": "Not enough messages to compact."}),
        _result("Not enough messages to compact."),
    )
    result, _handle = _daemon_compact(cli)
    assert result["compacted"] is False and result["reason"] == "Not enough messages to compact."


def test_a_failed_compaction_fails_the_request() -> None:
    """压缩失败时请求以失败结束"""
    with pytest.raises(RuntimeError, match="compaction failed"):
        _daemon_compact(_Cli(_result("API error", is_error=True)))


def test_a_session_the_cli_never_started_has_nothing_to_compact() -> None:
    """CLI 从未启动的会话没有可压缩的内容"""
    cli = _Cli()
    result, _handle = _daemon_compact(cli, session_id="")
    assert result == {"compacted": False, "reason": "no_session"} and cli.sent == []


def test_the_daemon_advertises_compaction() -> None:
    """守护进程声明支持压缩"""
    daemon = daemon_module.ClaudeCodeDaemon()
    with mock.patch.object(daemon_module, "_provider_environment", return_value=({}, {"models": []})):
        health = asyncio.run(daemon.health())
    assert health["capabilities"]["session_compact"] is True


def test_compact_aligns_the_requested_session_before_calling_the_client():
    """claude 原生压缩先对齐请求的会话，再调用客户端压缩

    :module: platform.core
    :importance: P2
    """
    backend = backends.ClaudeBackend()
    order = []
    backend.prepare_native_session = lambda **kwargs: order.append(("align", kwargs["backend_session_id"]))
    backend.client_factory = lambda: SimpleNamespace(compact_session=lambda **kwargs: (
        order.append(("compact", kwargs["session_id"])), {"compacted": False, "reason": "nothing_to_compact"}
    )[1])
    context = mock.Mock()
    context.is_cancelled.return_value = False
    backend.execute(_invocation(), context)
    assert order == [("align", "native-1"), ("compact", "native-1")]


def test_kimi_compact_without_history_does_not_create_a_session():
    """没有 Provider 历史时 kimi 压缩报错，且不新建会话

    :module: platform.core
    :importance: P2
    """
    client = mock.Mock()
    context = mock.Mock()
    context.is_cancelled.return_value = False
    invocation = _invocation("")
    with mock.patch.object(backends, "shared_kimi_client", return_value=client):
        with pytest.raises(ValueError, match="no Provider history"):
            backends.KimiBackend().execute(invocation, context)
    client.create_session.assert_not_called()
    context.record_compaction.assert_not_called()


def test_kimi_compact_without_wire_replacement_records_no_checkpoint():
    """kimi 压缩后 wire 未被替换时返回无需压缩，不记录压缩检查点

    :module: platform.core
    :importance: P2
    """
    from management_portal.kimi_code_client import KimiCodeClient
    client = KimiCodeClient(poll_seconds=0.2)
    with mock.patch.object(client, "_request", return_value={"as_of_seq": 42}), \
            mock.patch.object(client, "get_status", return_value={"busy": False}), \
            mock.patch("management_portal.execution.native_session_compaction.kimi_wire_path", return_value=None), \
            mock.patch("management_portal.kimi_code_client.time.sleep"):
        result = client.compact_session("empty-wire")
    assert result["compacted"] is False and result["reason"] == "nothing_to_compact"
    backend_result, _, recorded = _compact(backends.ClaudeBackend, result)
    assert backend_result.output["compacted"] is False
    recorded.assert_not_called()


@pytest.mark.parametrize("control", ["cancelled", "detach_requested"])
def test_unavailable_kimi_compaction_obeys_worker_control(control):
    """kimi 不可用时压缩等待响应取消或分离指令，取消时中止会话

    :module: platform.core
    :importance: P2
    """
    from management_portal.kimi_code_client import KimiCodeClient, KimiCodeUnavailable, KimiCodeDetached
    client = KimiCodeClient(poll_seconds=0.2)
    check = mock.Mock(side_effect=[False, False, True])
    with mock.patch.object(client, "_request", return_value={}), \
            mock.patch.object(client, "get_status", side_effect=KimiCodeUnavailable("offline")), \
            mock.patch.object(client, "abort_session") as abort, \
            mock.patch("management_portal.execution.native_session_compaction.kimi_wire_path", return_value=None), \
            mock.patch("management_portal.kimi_code_client.time.sleep"):
        with pytest.raises(InterruptedError if control == "cancelled" else KimiCodeDetached):
            client.compact_session("s", **{control: check})
    assert abort.called is (control == "cancelled")


def test_compact_ignores_a_previous_query_result_until_its_own_echo():
    """claude 压缩忽略之前查询遗留的结果，等到自身的压缩边界才返回

    :module: platform.core
    :importance: P2
    """
    class Cli(_Cli):
        previous = True
        async def receive_response(self):
            if self.previous:
                self.previous = False
                yield _result("previous background answer")
                return
            async for message in super().receive_response():
                yield message
    result, _ = _daemon_compact(Cli(
        daemon_module.SystemMessage(subtype="compact_boundary", data={"compact_metadata": {"post_tokens": 24}}),
        _result(),
    ))
    assert result["compacted"] is True and result["context_tokens"] == 24


def test_usage_emission_failure_does_not_drop_later_provider_requests():
    """某次请求用量上报失败时，后续请求的用量照常上报

    :module: platform.core
    :importance: P2
    """
    context = mock.Mock()
    context.emit.side_effect = [RuntimeError("temporary ledger failure"), None]
    requests = tuple(SimpleNamespace(request_id=key, usage={"total_tokens": 1}, occurred_at="now") for key in ("first", "second"))
    backends.report_request_usage(context, SimpleNamespace(requests=requests))
    assert [call.args[1]["event_id"] for call in context.emit.call_args_list] == ["first", "second"]
