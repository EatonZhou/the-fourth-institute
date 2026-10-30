"""The OpenCode Adapter: the real client and bridge against a stand-in OpenCode server.

``fake_opencode`` answers the part of ``opencode serve``'s API the bridge
uses; everything on Portal's side (``OpenCodeBackend``, ``OpenCodeClient``,
``opencode_bridge``) runs as it does live.

:module: spec.design-governance
:importance: P2
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


from management_portal import opencode_client, opencode_provider_catalog
from management_portal.execution import provider_continuation
from management_portal.execution.harness import HarnessTool, ProviderErrorRecoveryPolicy
from management_portal.opencode_backend import OpenCodeBackend
from tests.cases import capability_contracts

FAKE = Path(__file__).with_name("fake_opencode.py")
ROUTE = opencode_provider_catalog.ROUTE


@pytest.fixture
def opencode(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    log = tmp_path / "opencode.log"
    wrapper = runtime / "bin/opencode"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} {log} \"$@\"\n")
    wrapper.chmod(0o755)
    (runtime / "models.json").write_text(json.dumps({"version": "9.9.9", "models": [
        {"id": "free-model", "context": 200000, "variants": ["high"]}], "openrouter": [
        {"id": "vendor/agent-model", "context": 100000, "variants": ["low", "high"]},
        {"id": "another/model", "context": 50000, "variants": []},
        {"id": "the/default", "context": 50000, "variants": []}]}))
    # A Unix socket path has room for about 100 bytes.
    sockets = Path(tempfile.mkdtemp(prefix="oc", dir="/tmp"))
    monkeypatch.setattr(opencode_provider_catalog, "RUNTIME", runtime)
    monkeypatch.setattr(opencode_client, "STATE", tmp_path / "state")
    monkeypatch.setattr(opencode_client, "SOCKETS", sockets)
    work = tmp_path / "work"
    work.mkdir()
    client = opencode_client.OpenCodeClient()

    def prompts() -> list[dict]:
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def servers() -> list[dict]:
        path = log.with_name(log.name + ".server")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    yield SimpleNamespace(client=client, cwd=str(work), prompts=prompts, servers=servers, runtime=runtime,
                          state=tmp_path / "state", monkeypatch=monkeypatch)
    for root in (tmp_path / "state").glob("opencode-*"):
        try:
            client.close_session("", root.name)
        except Exception:  # noqa: BLE001 - a test may leave a session running
            pass
    shutil.rmtree(sockets, ignore_errors=True)


def _context() -> mock.Mock:
    context = mock.Mock()
    context.is_cancelled.return_value = False
    context.is_handover.return_value = False
    context.has_turn_control_provider.return_value = False
    context.execution_started_at = datetime.now(timezone.utc).isoformat()
    return context


def _invocation(opencode, prompt: str, *, tools=(), policy=None, options=None, session_id: str = "",
                execution_id: str = "execution-1"):
    return SimpleNamespace(
        request=SimpleNamespace(
            runtime_options={"provider_profile": ROUTE, "model": "free-model", "is_owner": True,
                             "session_key": "resident:resident_x", "tools": [], **(options or {})},
            operation="turn", input_media=(), execution_id=execution_id, cwd=opencode.cwd,
            permission_profile="owner", provider="opencode", metadata={},
            session=SimpleNamespace(binding_id="resident:resident_x"),
        ),
        session=SimpleNamespace(backend_route_id=ROUTE, backend_session_id=session_id),
        user_prompt=prompt, system_context="Portal rules", attach_system_context=True, is_new_backend_session=not session_id,
        recovery_checkpoint={}, provider_error_recovery=policy, provider_event_processor=None,
        harness_tools=tuple(tools), tool_batch_processor=None,
    )


def _events(context: mock.Mock) -> list[dict]:
    return [call.args[0] for call in context.observe_provider_event.call_args_list]


def test_an_opencode_turn_serves_its_portal_tools_and_reports_its_own_tool_calls(opencode) -> None:
    """OpenCode 一轮对话提供 Portal 工具并以通用形状报告自身工具调用

    OpenCode Adapter 经真实 client 与 bridge 对接模拟 OpenCode 服务：Portal 工具经 MCP 调用并回到 Harness，bash/edit 调用以通用参数名报告，回答流式送达并标为最终回答，按 OpenCode 记录的每次请求计用量，模型、Portal 规则与 subagent 开关随提示发送。
    """
    seen = []
    tool = HarnessTool("read_resource", "Read a resource", {"type": "object", "properties": {"id": {"type": "string"}}},
                       lambda arguments: seen.append(arguments) or "resource text")
    context = _context()
    result = OpenCodeBackend().execute(_invocation(opencode, "use the tool, bash and edit", tools=[tool]), context)

    assert result.output == {"message": "answer to: use the tool, bash and edit", "provider": "opencode",
                             "turn_id": "execution-1"}
    assert seen == [{"id": "r1"}]
    assert result.backend_session_id == opencode_client.session_for_turn("execution-1")
    events = _events(context)
    capability_contracts.check_tool_events(events)
    calls = [event["params"]["item"] for event in events if event["method"] == "item/completed"
             and event["params"]["item"]["type"] == "dynamicToolCall"]
    # The Portal tool under its own name, OpenCode's tools with the common argument names.
    assert [(call["tool"], call["status"]) for call in calls] == [
        ("read_resource", "completed"), ("bash", "completed"), ("edit", "completed")]
    assert calls[0]["output"] == [{"type": "text", "text": "resource text"}]
    assert calls[2]["arguments"] == {"file_path": "notes.txt", "old_string": "hello", "new_string": "goodbye"}
    # The answer streamed, then came as the turn's final message.
    assert any(event["method"] == "item/agentMessage/delta" for event in events)
    answers = [event["params"]["item"] for event in events
               if event["method"] == "item/completed" and event["params"]["item"]["type"] == "agentMessage"]
    assert [answer["phase"] for answer in answers] == ["final_answer"]
    # Every request of the turn, from OpenCode's record: four steps.
    assert result.usage["last"] == {"inputTokens": 600, "cachedInputTokens": 200, "outputTokens": 100,
                                    "reasoningOutputTokens": 20, "totalTokens": 700}
    assert result.usage["modelContextWindow"] == 200000
    samples = [event["params"]["tokenUsage"] for event in events if event["method"] == "thread/tokenUsage/updated"]
    assert samples and samples[-1]["modelContextWindow"] == 200000
    assert samples[-1]["context"]["inputTokens"] == 150
    context.observe_context_usage.assert_any_call({"input_tokens": 150}, 200000)
    # The model, the Portal rules and the subagent switch went with the prompt.
    (prompt,) = [entry for entry in opencode.prompts() if entry["kind"] == "prompt"]
    assert prompt["model"] == {"providerID": "opencode", "modelID": "free-model"}
    assert prompt["system"] == "Portal rules" and prompt["tools"] == {"task": True}


def _run_turn(opencode, turn_id: str, prompt: str, **params):
    return opencode.client.run_turn(**{
        "session_key": "k", "session_id": "", "prompt": prompt, "cwd": opencode.cwd, "provider_profile": ROUTE,
        "model": "free-model", "effort": "", "is_owner": True, "permission_profile": "owner",
        "system_prompt_append": "rules", "turn_id": turn_id, "tools": [], "resident_environment": {},
        "inherit_portal_database": False, "allow_subagents": True, "event_callback": lambda event: None,
        "tool_callback": None, **params})


def test_an_opencode_session_runs_in_the_residents_environment_and_portals_own_directories(opencode, monkeypatch):
    """opencode 会话带 Resident 环境变量运行，配置和数据放在 Portal 自己的目录，数据库地址不落盘

    :module: platform.core"""
    monkeypatch.setenv("PORTAL_DATABASE_URL", "postgresql://test-actor")
    identity = {"PORTAL_RESIDENT_ID": "resident_0123456789ab", "PORTAL_LINEAGE_ID": "lineage_0123456789ab"}
    result = _run_turn(opencode, "turn-env", "plain", resident_environment=identity, inherit_portal_database=True)

    assert result.response == "answer to: plain"
    (server,) = opencode.servers()
    # The Resident's shell tools reach the Portal database; its address is never written down.
    assert server["PORTAL_DATABASE_URL"] == "postgresql://test-actor"
    assert server["PORTAL_RESIDENT_ID"] == "resident_0123456789ab"
    launch = (opencode.state / result.session_id / "launch.json").read_text()
    assert "test-actor" not in launch and "environment" not in json.loads(launch)
    # OpenCode reads Portal's configuration directory, not the host user's, and keeps its data in the session.
    assert server["XDG_CONFIG_HOME"] == str(opencode.runtime / "config")
    session_root = opencode.state / result.session_id
    assert all(Path(server[key]).is_relative_to(session_root) for key in ("XDG_DATA_HOME", "XDG_STATE_HOME", "OPENCODE_DB"))
    # A malformed identity starts nothing.
    with pytest.raises(ValueError, match="both IDs"):
        _run_turn(opencode, "turn-bad", "plain", resident_environment={"PORTAL_RESIDENT_ID": "resident_0123456789ab"})


class _Registry:
    """An LLM API Registry with OpenRouter entries and one entry of a service OpenCode is not routed to."""

    def __init__(self, key_file: Path) -> None:
        self.rows = [
            {"id": "openrouter_off", "provider": "openrouter", "enabled": False, "default_model": "off/model",
             "api_key_file": "/nonexistent", "models": []},
            {"id": "openrouter_test", "provider": "openrouter", "default_model": "the/default",
             "api_key_file": str(key_file), "models": [{"id": "vendor/agent-model"}, {"id": "not/staged"}]},
            {"id": "openrouter_spare", "provider": "openrouter", "default_model": "spare/model",
             "api_key_file": "/nonexistent", "models": []},
            {"id": "deepseek_test", "provider": "deepseek", "default_model": "x", "models": []},
        ]

def test_an_openrouter_route_reaches_the_service_with_the_registrys_key_kept_out_of_opencodes_tools(opencode, tmp_path):
    """opencode 的 openrouter 路由使用注册表中的密钥和模型，密钥不进入工具环境也不落盘

    :module: platform.core"""
    key_file = tmp_path / "openrouter.key"
    key_file.write_text("sk-or-test\n")
    from tests.cases.model_api_test_support import register_apis
    entries = [{**row, "kind": "openai_compatible_chat"} for row in _Registry(key_file).rows]
    entries[1]["routes"] = [{"backend": "opencode", "route_id": "openrouter"}]
    register_apis(*entries)

    # The service route explicitly names the registration that provides its
    # credential, models and billing account.
    routes = {row["backend_route_id"]: row for row in opencode_provider_catalog.routes()}
    assert set(routes) == {ROUTE, "openrouter"}
    # The free route has no billing account, so its account shows no balance
    # and no balance read can fail for it.
    assert routes[ROUTE]["provider_profile"] == ""
    openrouter = routes["openrouter"]
    assert (openrouter["label"], openrouter["provider_profile"]) == ("OpenRouter", "openrouter_test")
    # The registry's models first, its default the default; then every other model that can call tools.
    assert openrouter["models"] == ["the/default", "vendor/agent-model", "another/model"]
    assert openrouter["default_model"] == "the/default" and openrouter["upstream_provider"] == "openrouter"
    assert openrouter["efforts_by_model"]["vendor/agent-model"] == ["low", "high"]

    result = _run_turn(opencode, "turn-openrouter", "plain", provider_profile="openrouter",
                       model="vendor/agent-model", effort="high")
    assert result.response == "answer to: plain"
    auth, prompt = opencode.prompts()
    assert auth == {"kind": "auth", "service": "openrouter", "key": "sk-or-test", "first": True}
    assert prompt["model"] == {"providerID": "openrouter", "modelID": "vendor/agent-model"} and prompt["variant"] == "high"
    # OpenCode holds the key as its credential: not in its environment (its tools inherit that), not on disk.
    (server,) = opencode.servers()
    assert server["PORTAL_OPENCODE_API_KEY"] is None and server["OPENROUTER_API_KEY"] is None
    assert "sk-or-test" not in (opencode.state / result.session_id / "launch.json").read_text()
    # A model the route does not offer is refused before OpenCode starts.
    with pytest.raises(ValueError):
        _run_turn(opencode, "turn-unknown", "plain", provider_profile="openrouter", model="free-model")


def test_an_api_key_route_runs_on_its_own_openai_compatible_provider(opencode, tmp_path):
    """opencode 的自定义 API 路线以模型 API 作为唯一的 OpenAI 兼容 Provider 运行，密钥不进入工具环境也不落盘

    :module: platform.core"""
    from management_portal.execution import account_registrations
    from tests.cases.model_api_test_support import register_apis
    key_file = tmp_path / "gateway.key"
    key_file.write_text("sk-gateway\n")
    register_apis({"id": "gateway", "kind": "openai_compatible_chat", "provider": "example",
                   "base_url": "https://gateway.invalid/v1", "default_model": "gpt-test", "api_key_file": str(key_file),
                   "models": [{"id": "gpt-test", "context_window": 128000}]})
    account_registrations.create_registration({"account_id": "gateway-opencode", "provider": "opencode",
                                               "kind": "api_profile", "config": {"api_id": "gateway"}}, "test")
    result = _run_turn(opencode, "turn-gateway", "plain", provider_profile="gateway-opencode", model="gpt-test")
    assert result.response == "answer to: plain"
    auth, prompt = opencode.prompts()
    assert auth == {"kind": "auth", "service": "portal-gateway-opencode", "key": "sk-gateway", "first": True}
    assert prompt["model"] == {"providerID": "portal-gateway-opencode", "modelID": "gpt-test"}
    (server,) = opencode.servers()
    assert server["config"] == {
        "provider": {"portal-gateway-opencode": {
            "npm": "@ai-sdk/openai-compatible", "name": "gateway", "options": {"baseURL": "https://gateway.invalid/v1"},
            "models": {"gpt-test": {"name": "gpt-test", "limit": {"context": 128000, "output": 32768}}}}},
        "enabled_providers": ["portal-gateway-opencode"], "small_model": "portal-gateway-opencode/gpt-test"}
    assert server["OPENCODE_DISABLE_MODELS_FETCH"] == "1" and server["PORTAL_OPENCODE_API_KEY"] is None
    assert "sk-gateway" not in (opencode.state / result.session_id / "launch.json").read_text()


def test_an_opencode_turn_takes_send_now_while_it_runs(opencode) -> None:
    """OpenCode 运行中的一轮接受立即发送

    轮次运行时送入的输入在下一步被 OpenCode 采纳，并沿用本轮的模型、规则与工具开关；轮次结束后不再接受输入。
    """
    backend = OpenCodeBackend()
    outcome = {}
    session = opencode_client.session_for_turn("turn-slow")

    def run() -> None:
        outcome["result"] = _run_turn(opencode, "turn-slow", "slow work")

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 30
    taken = (False, "")
    while time.monotonic() < deadline and not taken[0]:
        time.sleep(0.2)
        taken = backend.deliver_turn_input(session_key="k", session_id=session, turn_id="turn-slow",
                                           items=[{"type": "text", "text": "also this"}], control_id="c1")
    worker.join(30)
    assert taken == (True, "")
    assert outcome["result"].response == "answer to: slow work +also this"
    # Input during the turn keeps the turn's model, rules and switches: OpenCode takes them from the newest message.
    (steer,) = [entry for entry in opencode.prompts() if entry["kind"] == "steer"]
    assert steer["model"]["modelID"] == "free-model" and steer["system"] == "rules" and steer["tools"] == {"task": True}
    # A turn that is no longer running takes no input.
    assert backend.deliver_turn_input(session_key="k", session_id=session, turn_id="turn-slow",
                                      items=[{"type": "text", "text": "late"}], control_id="c2") == (False, "no_active_turn")


def test_a_failed_opencode_turn_is_continued_in_its_session(opencode) -> None:
    """OpenCode 失败的一轮在同一会话中继续

    OpenCode 报 Provider 错误后，按 Harness 恢复策略在同一会话发送 continue，不重发原输入。

    :module: execution.providers
    :aspects: functional, behavior
    """
    context = _context()
    with mock.patch.object(provider_continuation.time, "sleep"):
        result = OpenCodeBackend().execute(
            _invocation(opencode, "fail once", policy=ProviderErrorRecoveryPolicy(1)), context)

    assert result.output["message"] == "answer to: continue"
    prompts = [entry["parts"][0]["text"] for entry in opencode.prompts() if entry["kind"] == "prompt"]
    assert prompts == ["fail once", "continue"]
    recovery = {call.args[0]: call.args[1] for call in context.emit.call_args_list}["recovery_input_sent"]
    assert (recovery["reason"], recovery["input_delivery"]) == ("provider_error", "continued")
    # Without a continuation the turn fails with OpenCode's own account of the error.
    with pytest.raises(Exception, match=r"APIError: Upstream request failed: Endpoint is unavailable \(HTTP 429\)\. "):
        OpenCodeBackend().execute(_invocation(opencode, "fail again", execution_id="execution-2"), _context())


def test_opencode_subagents_follow_the_residents_setting(opencode) -> None:
    """OpenCode subagent 跟随 Resident 的设置

    “允许开启 sub agent”关闭时 OpenCode 的 task 工具随提示关闭，开启时打开。
    """
    for allowed in (False, True):
        OpenCodeBackend().execute(_invocation(opencode, f"plain {allowed}", options={"allow_subagents": allowed},
                                              execution_id=f"execution-{allowed}"), _context())
    assert [entry["tools"] for entry in opencode.prompts()] == [{"task": False}, {"task": True}]


def test_an_opencode_subagents_steps_reach_the_worker_under_its_call_and_end_in_its_card(opencode) -> None:
    """OpenCode subagent 的步骤与卡片

    OpenCode 子会话的步骤以 subagent/event 归到发起它的 task 调用下，运行中卡片与完成卡片按通用形状送达。
    """
    context = _context()
    result = OpenCodeBackend().execute(_invocation(opencode, "subagent please"), context)
    events = _events(context)
    capability_contracts.check_subagent_events(events)
    # The subagent's two requests ran in its child session; they count toward the
    # turn's usage beside the turn's own two, not toward the turn's context.
    assert result.usage["last"]["totalTokens"] == 700 and result.usage["context"]["totalTokens"] == 175
    (card,) = [event["params"]["item"]["run"] for event in events
               if event["method"] == "item/completed" and event["params"]["item"]["type"] == "subagentRun"]
    assert card["status"] == "completed" and card["description"] == "look at notes"
    assert card["usage"] == {"total_tokens": 350, "input_tokens": 300, "output_tokens": 50, "cached_input_tokens": 100,
                             "reasoning_tokens": 10}
    # The card holds the subagent's answer without OpenCode's <task_result> wrapper.
    assert card["result"] == "child found hello"
    # It holds the task the call gave and names the child session's trace, not the call.
    (child,) = {event["params"]["traceKey"] for event in events if event["method"] == "subagent/event"}
    assert (card["prompt"], card["trace_ref"]) == ("read notes.txt", child) and child != card["tool_use_id"]


def test_opencode_stops_one_subagent_by_aborting_its_child_session_while_the_turn_goes_on(opencode) -> None:
    """OpenCode 单独停止一个子 agent，这一轮继续

    运行中卡片点停止时，OpenCode 桥接只 abort 这次 task 调用的子会话；task 调用以中止失败返回，模型继续给出这一轮的回答，卡片以已停止结束。subagent/stopping 只交给卡片，不进入这一轮的事件；未知调用返回 unknown_subagent，这一轮结束后返回 no_active_turn。

    :module: execution.providers
    :importance: P1
    """
    from management_common.provider_subagent_runs import STOPPING_METHOD

    backend = OpenCodeBackend()
    session = opencode_client.session_for_turn("turn-watch")
    events: list[dict] = []
    outcome = {}
    worker = threading.Thread(target=lambda: outcome.update(result=_run_turn(
        opencode, "turn-watch", "watch please", event_callback=events.append)))
    worker.start()
    deadline = time.monotonic() + 30
    calls: list[str] = []
    while time.monotonic() < deadline and not calls:
        time.sleep(0.1)
        calls = [event["params"]["parentToolUseId"] for event in list(events) if event["method"] == "subagent/event"]
    stop = {"type": "subagent_stop", "tool_use_id": calls[0]}
    assert backend.deliver_turn_input(session_key="k", session_id=session, turn_id="turn-watch",
                                      items=[{**stop, "tool_use_id": "call-unknown"}], control_id="s0") == (False, "unknown_subagent")
    assert backend.deliver_turn_input(session_key="k", session_id=session, turn_id="turn-watch",
                                      items=[stop], control_id="s1") == (True, "")
    worker.join(30)

    # Only the child session was aborted; the model went on to its answer.
    assert outcome["result"].response == "answer to: watch please"
    assert [entry["aborted"] for entry in opencode.prompts() if entry["kind"] == "child"] == [True]
    [(end, card)] = [(index, event["params"]["item"]["run"]) for index, event in enumerate(events)
                     if event["method"] == "item/completed" and event["params"]["item"]["type"] == "subagentRun"]
    assert (card["tool_use_id"], card["status"]) == (calls[0], "stopped")
    assert "aborted" in card["result"]
    # The child's step that settles after its call failed gives no new running card.
    assert [event for event in events[end:] if event["method"] == "subagent/event"]
    assert not [event for event in events[end:] if event["method"] == "subagent/progress"]
    # The bridge's word stays with the card; the turn's events do not carry it.
    assert not [event for event in events if event["method"] == STOPPING_METHOD]
    assert backend.deliver_turn_input(session_key="k", session_id=session, turn_id="turn-watch",
                                      items=[stop], control_id="s2") == (False, "no_active_turn")


def test_a_finished_turn_is_answered_again_without_sending_its_input_twice(opencode) -> None:
    """OpenCode 已完成的一轮再次接入时不重发输入

    同一轮次 ID 再次到来（Worker 交接后）时 bridge 返回已有结果，OpenCode 只收到一次输入。

    :module: execution.providers
    :aspects: behavior
    """
    first = OpenCodeBackend().execute(_invocation(opencode, "once only"), _context())
    # A Worker that comes for the same turn after it ended (a handover) gets its outcome.
    again = OpenCodeBackend().execute(_invocation(opencode, "once only", session_id=first.backend_session_id), _context())

    assert again.output["message"] == first.output["message"] == "answer to: once only"
    assert [entry["kind"] for entry in opencode.prompts()] == ["prompt"]


def test_an_opencode_session_compacts_on_demand_and_is_released(opencode) -> None:
    """OpenCode 会话按需压缩并可释放

    /compact 让 OpenCode 总结会话历史并报告压缩后上下文，释放结束 bridge 与 OpenCode 进程。
    """
    session = OpenCodeBackend().execute(_invocation(opencode, "first"), _context()).backend_session_id
    OpenCodeBackend().execute(_invocation(opencode, "second", session_id=session, execution_id="execution-2"), _context())
    outcome = opencode.client.compact_session(session_id=session, cwd=opencode.cwd, provider_profile=ROUTE,
                                              model="free-model", resident_environment={})

    assert outcome["compacted"] is True and outcome["context_tokens"] == 42
    assert opencode.prompts()[-1] == {"kind": "summarize", "providerID": "opencode", "modelID": "free-model"}
    # The compaction is recorded: OpenCode's summary replaces the history before it.
    context = _context()
    OpenCodeBackend().record_provider_compaction(context=context, backend_session_id=session, backend_route_id=ROUTE,
                                                 config_dir="")
    (record,) = context.record_compaction.call_args_list
    assert record.kwargs["provider"] == "opencode" and record.kwargs["provider_compaction_ref"].startswith("opencode:msg_")
    assert record.kwargs["content"]["replacement_history"][0]["content"][0]["text"] == "summary"
    assert record.kwargs["retain_from"] == record.kwargs["created_at"]
    assert opencode.client.close_session("k", session) == {"closed": True}


def test_the_opencode_runtime_is_staged_with_the_free_models_in_portals_own_directories(monkeypatch, tmp_path):
    """opencode 运行时安装时只记录可调用工具的免费模型，并在 Portal 自己的目录中运行

    :module: platform.core"""
    from management_portal.execution import provider_updates as updates
    from management_portal.opencode_runtime import OpenCodeRuntime

    monkeypatch.setattr(updates, "RUNTIMES", tmp_path)
    root = tmp_path / "opencode-9.9.9"
    (root / "bin").mkdir(parents=True)
    rows = [
        {"id": "free-tools", "name": "Free Tools", "cost": {"input": 0, "output": 0}, "capabilities": {"toolcall": True},
         "limit": {"context": 1000}, "variants": {"high": {}, "low": {}}},
        {"id": "paid", "cost": {"input": 1, "output": 2}, "capabilities": {"toolcall": True}},
        {"id": "free-chat", "cost": {}, "capabilities": {"toolcall": False}},
        {"id": "free-old", "status": "deprecated", "cost": {}, "capabilities": {"toolcall": True}},
    ]
    # ``opencode models --verbose`` prints each model's name, then its JSON.
    (root / "models.out").write_text("".join(f"opencode/{row['id']}\n{json.dumps(row, indent=2)}\n" for row in rows))
    (root / "openrouter.out").write_text("".join(f"openrouter/{row['id']}\n{json.dumps(row, indent=2)}\n" for row in rows))
    binary = root / "bin/opencode"
    binary.write_text(f"""#!/bin/sh
echo "$XDG_CONFIG_HOME $XDG_DATA_HOME" >> {root}/calls
case "$1 $2" in
  --version*) echo 9.9.9 ;;
  serve*) echo "  --port  port to listen on" >&2 ;;
  "models opencode") cat {root}/models.out ;;
  "models openrouter") [ "$OPENROUTER_API_KEY" = listing-only ] && cat {root}/openrouter.out ;;
esac
""")
    binary.chmod(0o755)
    installs = []
    real = updates.run_command
    monkeypatch.setattr(updates, "run_command", lambda *command, **options: (
        installs.append(command) or "") if command[:2] == ("npm", "install") else real(*command, **options))

    assert OpenCodeRuntime().stage(("9.9.9",)) == root
    staged = json.loads((root / "models.json").read_text())
    assert staged == {"version": "9.9.9", "models": [
        {"id": "free-tools", "name": "Free Tools", "context": 1000, "variants": ["high", "low"]}], "openrouter": [
        {"id": "free-tools", "name": "Free Tools", "context": 1000, "variants": ["high", "low"]},
        {"id": "paid", "name": "paid", "context": 0, "variants": []}]}
    assert len(installs) == 1 and installs[0][-1] == "opencode-ai@9.9.9"
    # OpenCode ran with the runtime's configuration directory and data outside the host user's home.
    for line in (root / "calls").read_text().splitlines():
        config, data = line.split()
        assert config == str(root / "config") and not data.startswith(str(Path.home()))
    OpenCodeRuntime().stage(("9.9.9",))
    assert len(installs) == 1


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
