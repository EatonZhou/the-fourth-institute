"""Portal tools for Kimi Code.

Kimi Code shares one MCP server between the sessions of a workspace, lists
its tools once, and does not say which session makes a call.  Portal lists
one growing catalog, switches off per turn what the turn does not have, and
learns the calling session from a ``PreToolUse`` hook.  Found live: without
the hook, two sessions that made the same call got each other's identity.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import kimi_code_server, kimi_portal_tools
from management_portal.execution import backends
from management_portal.execution.harness import HarnessTool
from management_portal.execution.harness_capabilities import PORTAL_TOOLS
from management_portal.execution.kimi_tool_gateway import KimiToolGateway, publish_catalog
from management_portal.execution.provider_catalog import AGENT_PROVIDERS

PREFIX = kimi_portal_tools.TOOL_NAME_PREFIX


def _tool(name: str, description: str = "A tool.") -> dict:
    return {"name": name, "description": description, "inputSchema": {"type": "object", "properties": {}}}


@pytest.fixture
def root(monkeypatch):
    # A Unix socket path holds about a hundred bytes; pytest's tmp_path is longer.
    directory = Path(tempfile.mkdtemp(prefix="kpt", dir="/tmp"))
    monkeypatch.setattr(kimi_portal_tools, "GATEWAY_ROOT", directory / "gw")
    home = directory / "home"
    home.mkdir()
    yield SimpleNamespace(home=home, gateway=directory / "gw")
    shutil.rmtree(directory, ignore_errors=True)


def _event(session_id: str, tool: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {"hook_event_name": "PreToolUse", "session_id": session_id, "tool_name": f"{PREFIX}{tool}",
            "tool_input": arguments, "tool_call_id": call_id}


def _identity(label: str):
    return lambda call: {"isError": False, "content": [{"type": "text", "text": f"{label}:{call['tool']}:{call['call_id']}"}]}


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def test_kimi_declares_portal_tools() -> None:
    """Kimi 声明支持 Portal 工具

    Kimi Code Adapter 的能力声明中包含 Portal 工具。
    """
    assert PORTAL_TOOLS in AGENT_PROVIDERS["kimi"].harness_capabilities()


def test_the_catalog_only_grows_and_mcp_json_names_it(root) -> None:
    """工具目录只增不减并由 mcp.json 命名

    Kimi 的 Portal 工具目录只追加新工具，mcp.json 中登记该目录。
    """
    (root.home / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"transport": "http", "url": "http://x"}}}))
    assert kimi_portal_tools.install(root.home) is True      # the launcher of the Kimi Code service
    assert kimi_portal_tools.install(root.home) is False

    assert publish_catalog([_tool("b_tool"), _tool("a_tool")], root.home) is True
    first = json.loads((root.home / "mcp.json").read_text())["mcpServers"]
    assert first["other"] == {"transport": "http", "url": "http://x"}      # other servers stay
    assert first["portal"]["args"] == [str(Path(kimi_portal_tools.__file__).resolve())]
    assert [tool["name"] for tool in kimi_portal_tools.read_catalog()] == ["a_tool", "b_tool"]

    # A turn with fewer tools leaves the list alone: Kimi Code need not connect again.
    assert publish_catalog([_tool("a_tool")], root.home) is False
    assert json.loads((root.home / "mcp.json").read_text())["mcpServers"] == first

    # A new tool, or a changed definition, renames the catalog: Kimi Code connects again.
    assert publish_catalog([_tool("c_tool")], root.home) is True
    second = json.loads((root.home / "mcp.json").read_text())["mcpServers"]["portal"]
    assert second["env"] != first["portal"]["env"]
    assert publish_catalog([_tool("a_tool", "Changed.")], root.home) is True
    assert [tool["name"] for tool in kimi_portal_tools.read_catalog()] == ["a_tool", "b_tool", "c_tool"]


def test_a_call_runs_with_the_worker_of_the_session_that_made_it(root) -> None:
    """调用由发起会话所属的 Worker 执行

    Kimi 的 Portal 工具调用交给发起该调用的会话所在 Worker 处理。
    """
    with KimiToolGateway("session-a", _identity("A")), KimiToolGateway("session-b", _identity("B")):
        assert kimi_portal_tools.announce(_event("session-b", "whoami", {"n": 1}, "call-b")) == ""
        assert _text(kimi_portal_tools.call_portal_tool("whoami", {"n": 1})) == "B:whoami:call-b"
        assert kimi_portal_tools.announce(_event("session-a", "whoami", {"n": 1}, "call-a")) == ""
        assert _text(kimi_portal_tools.call_portal_tool("whoami", {"n": 1})) == "A:whoami:call-a"
    assert not list((root.gateway / "calls").iterdir())      # every note was picked up
    assert not list((root.gateway / "turns").iterdir())      # the turns ended


def test_identical_calls_of_two_sessions_are_let_through_one_after_the_other(root) -> None:
    """两个会话的相同调用依次放行

    两个会话发出完全相同的调用时按顺序逐个执行，不会合并。
    """
    second: dict[str, str] = {}
    with KimiToolGateway("session-a", _identity("A")), KimiToolGateway("session-b", _identity("B")):
        assert kimi_portal_tools.announce(_event("session-a", "whoami", {}, "call-a")) == ""

        def second_session() -> None:
            # Waits: the first session's note is still there.
            second["denied"] = kimi_portal_tools.announce(_event("session-b", "whoami", {}, "call-b"))
            second["result"] = _text(kimi_portal_tools.call_portal_tool("whoami", {}))

        waiting = threading.Thread(target=second_session)
        waiting.start()
        time.sleep(0.2)
        assert second == {}
        first = _text(kimi_portal_tools.call_portal_tool("whoami", {}))
        waiting.join(timeout=20)

    assert first == "A:whoami:call-a"
    assert second == {"denied": "", "result": "B:whoami:call-b"}


def test_an_identical_call_that_cannot_be_told_apart_is_held_back(root, monkeypatch) -> None:
    """无法区分的相同调用被扣留

    无法分辨归属的相同调用不会执行，没人认领的记录视为未发生的调用。
    """
    monkeypatch.setattr(kimi_portal_tools, "ANNOUNCE_WAIT_SECONDS", 0.2)
    assert kimi_portal_tools.announce(_event("session-a", "whoami", {})) == ""
    assert "again" in kimi_portal_tools.announce(_event("session-b", "whoami", {}))
    # A note nobody picked up belongs to a call that was never made.
    monkeypatch.setattr(kimi_portal_tools, "NOTE_STALE_SECONDS", 0.0)
    assert kimi_portal_tools.announce(_event("session-b", "whoami", {}, "call-b")) == ""
    with KimiToolGateway("session-b", _identity("B")):
        assert _text(kimi_portal_tools.call_portal_tool("whoami", {})) == "B:whoami:call-b"


def test_a_call_portal_cannot_attribute_does_not_run(root, monkeypatch) -> None:
    """Portal 无法确认调用者的调用不执行

    没有 hook 告诉 Portal 调用者时，Kimi 的 Portal 工具调用被拒绝。
    """
    monkeypatch.setattr(kimi_portal_tools, "NOTE_WAIT_SECONDS", 0.1)
    # This negative case has no worker to reconnect to. Exercise expiry without
    # waiting through the production restart allowance.
    monkeypatch.setattr(kimi_portal_tools, "GATEWAY_WAIT_SECONDS", 0.1)
    ran: list[dict] = []
    with KimiToolGateway("session-a", lambda call: ran.append(call) or {"isError": False, "content": []}):
        # No hook told Portal who calls.
        unknown = kimi_portal_tools.call_portal_tool("whoami", {})
        # A session Portal runs no turn for.
        kimi_portal_tools.announce(_event("session-outsider", "whoami", {}))
        outsider = kimi_portal_tools.call_portal_tool("whoami", {})

    assert unknown["isError"] and "which session" in _text(unknown)
    assert outsider["isError"] and "no Portal turn of this session" in _text(outsider)
    assert ran == []


def test_a_tool_the_turn_does_not_have_is_rejected_by_its_worker(root) -> None:
    """本轮没有的工具被 Worker 拒绝

    调用本轮未授予的工具时，Worker 返回拒绝结果。
    """
    def dispatch(call: dict) -> dict:
        raise ValueError("Unknown Agent Harness tool or invalid arguments")

    with KimiToolGateway("session-a", dispatch):
        kimi_portal_tools.announce(_event("session-a", "other_tool", {}))
        result = kimi_portal_tools.call_portal_tool("other_tool", {})
    assert result["isError"] and "Unknown Agent Harness tool" in _text(result)


def test_the_program_speaks_mcp_and_announces_as_a_hook(root) -> None:
    """工具程序以 MCP 通信并以 hook 身份报到

    Portal 工具程序使用 MCP 协议，并通过 hook 声明调用来源。
    """
    kimi_portal_tools.install(root.home)
    publish_catalog([_tool("whoami", "Who runs this turn.")], root.home)
    environment = {"PORTAL_KIMI_TOOLS_ROOT": str(root.gateway), "PATH": "/usr/bin:/bin"}
    script = str(Path(kimi_portal_tools.__file__).resolve())

    hook = subprocess.run(
        [sys.executable, script, "announce"], input=json.dumps(_event("session-a", "whoami", {"n": 1}, "call-9")),
        capture_output=True, text=True, env=environment, timeout=30, check=True,
    )
    assert hook.stdout == ""          # the call may run

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "whoami", "arguments": {"n": 1}}},
    ]
    with KimiToolGateway("session-a", _identity("A")):
        server = subprocess.Popen(
            [sys.executable, script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=environment,
        )
        assert server.stdin and server.stdout
        answers: dict[int, dict] = {}
        for request in requests:
            server.stdin.write(json.dumps(request) + "\n")
            server.stdin.flush()
        while len(answers) < 3:
            answer = json.loads(server.stdout.readline())
            answers[answer["id"]] = answer["result"]
        server.stdin.close()
        server.wait(timeout=10)

    assert answers[1]["protocolVersion"] == "2025-11-25" and "tools" in answers[1]["capabilities"]
    assert [tool["name"] for tool in answers[2]["tools"]] == ["whoami"]
    assert _text(answers[3]) == "A:whoami:call-9" and answers[3]["isError"] is False

    # An identical call whose note is still waiting is denied in the hook's own format.
    kimi_portal_tools.announce(_event("session-a", "whoami", {}))
    busy = subprocess.run(
        [sys.executable, "-c",
         "import management_portal.kimi_portal_tools as k; k.ANNOUNCE_WAIT_SECONDS = 0.1; "
         "raise SystemExit(k.main(['announce']))"],
        input=json.dumps(_event("session-b", "whoami", {})), capture_output=True, text=True, timeout=30,
        env={**environment, "PYTHONPATH": str(Path(kimi_portal_tools.__file__).resolve().parents[1])},
    )
    assert json.loads(busy.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_kimi_service_is_started_with_the_hook(root, tmp_path) -> None:
    """Kimi 服务带 hook 启动

    Kimi Code 服务启动时配置 Portal 的调用者识别 hook。
    """
    template = tmp_path / "template.toml"
    template.write_text(
        'default_model = __PORTAL_KIMI_DEFAULT_MODEL__\n[providers.p]\nbase_url = __PORTAL_KIMI_BASE_URL__\n'
        'api_key = __PORTAL_KIMI_API_KEY__\n[models."kimi-k3"]\nprovider = "p"\n', encoding="utf-8",
    )
    assert kimi_portal_tools.service_ready(root.home) is False
    path = kimi_code_server._write_runtime_config(
        root.home, template, {"base_url": "https://example.invalid/v1"}, "secret", "kimi-k3",
    )
    hooks = tomllib.loads(path.read_text(encoding="utf-8"))["hooks"]
    assert hooks == [{
        "event": "PreToolUse", "matcher": "mcp__portal__.*",
        "command": kimi_portal_tools.hook_command(), "timeout": kimi_portal_tools.HOOK_TIMEOUT_SECONDS,
    }]
    assert kimi_portal_tools.service_ready(root.home) is False     # the server is not named yet
    kimi_portal_tools.install(root.home)
    assert kimi_portal_tools.service_ready(root.home) is True


def test_a_worker_renames_the_catalog_but_never_installs_the_server(root) -> None:
    """Worker 只改目录名称，不安装 MCP 服务

    Kimi 服务启动时没有 Portal 服务条目，Worker 不会补加；服务名由启动器决定。
    """
    # A service started without Portal's server has no entry: a worker adds none.
    assert publish_catalog([_tool("a_tool")], root.home) is True
    assert not (root.home / "mcp.json").exists()
    # The launcher names the server; the worker keeps the launcher's program.
    kimi_portal_tools.install(root.home)
    servers = json.loads((root.home / "mcp.json").read_text())["mcpServers"]
    servers["portal"]["args"] = ["/elsewhere/kimi_portal_tools.py"]
    (root.home / "mcp.json").write_text(json.dumps({"mcpServers": servers}))
    assert publish_catalog([_tool("b_tool")], root.home) is True
    entry = json.loads((root.home / "mcp.json").read_text())["mcpServers"]["portal"]
    assert entry["args"] == ["/elsewhere/kimi_portal_tools.py"]
    assert entry["env"] == {kimi_portal_tools.CATALOG_ENVIRONMENT: kimi_portal_tools.catalog_signature()}


def _fake_client(registered: list[list[str]]) -> SimpleNamespace:
    """A Kimi client whose session shows ``registered`` tool lists, one per poll."""

    def session_tools(_session_id: str) -> list[dict]:
        names = registered.pop(0) if len(registered) > 1 else registered[0]
        return [{"name": name, "source": "mcp", "active": True} for name in names] + [{"name": "Read", "source": "builtin"}]

    return SimpleNamespace(session_tools=mock.Mock(side_effect=session_tools), update_session_profile=mock.Mock(return_value={}))


def _settle(client: SimpleNamespace, catalog: list[dict], *, cancelled: bool = False) -> list[str]:
    with mock.patch.object(backends, "shared_kimi_client", return_value=client), \
            mock.patch.object(backends.time, "sleep"):
        return backends.KimiBackend.settle_portal_tools(
            "session-a", catalog, profile={"tools": ["Read"], "permission_mode": "yolo", "model": "kimi-k3"},
            cancelled=lambda: cancelled,
        )


def test_a_turn_switches_off_the_portal_tools_it_does_not_have() -> None:
    """轮次关闭自己没有的 Portal 工具

    Kimi Code 加载会话后，本轮未授予的 Portal 工具被关闭。
    """
    client = _fake_client([[f"{PREFIX}hatg_instruction", f"{PREFIX}read_harness_artifact", f"{PREFIX}jev_decide"]])
    assert _settle(client, [_tool("hatg_instruction")]) == [f"{PREFIX}jev_decide", f"{PREFIX}read_harness_artifact"]
    # Kimi Code lists a session's tools only once it has loaded the session.
    client.update_session_profile.assert_called_once_with("session-a", tools=["Read"], permission_mode="yolo", model="kimi-k3")
    assert _settle(_fake_client([[f"{PREFIX}hatg_instruction"]]), []) == [f"{PREFIX}hatg_instruction"]


def test_a_turn_waits_for_kimi_code_to_register_a_tool_that_is_new_to_it() -> None:
    """轮次等待 Kimi Code 登记新工具

    工具第一次出现时，轮次等待 Kimi Code 完成登记后再开始。
    """
    client = _fake_client([[], [f"{PREFIX}old_tool"], [f"{PREFIX}old_tool", f"{PREFIX}new_tool"]])
    assert _settle(client, [_tool("new_tool")]) == [f"{PREFIX}old_tool"]
    assert client.session_tools.call_count == 3


def test_tools_portal_cannot_vouch_for_stay_off() -> None:
    """Portal 无法担保的工具保持关闭

    不能确认来源与授权的工具在 Kimi 会话中保持关闭。
    """
    client = SimpleNamespace(
        update_session_profile=mock.Mock(side_effect=backends.KimiCodeUnavailable("down")), session_tools=mock.Mock(),
    )
    assert _settle(client, []) == [f"{PREFIX}*"]
    assert _settle(client, [_tool("hatg_instruction")]) == []


def _invocation(tools: tuple[HarnessTool, ...]) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(runtime_options={"tools": ["Read"]}, permission_profile="yolo", cwd="/work",
                                operation="turn", execution_id="execution-1", input_media=(), metadata={},
                                session=SimpleNamespace(binding_id="resident:test")),
        session=SimpleNamespace(backend_session_id="session-a", backend_route_id=""),
        recovery_checkpoint={}, system_context="", user_prompt="use the tool",
        attach_system_context=False, is_new_backend_session=False,
        harness_tools=tools, tool_batch_processor=None, provider_event_processor=None, provider_error_recovery=None,
    )


def test_a_kimi_turn_serves_its_harness_tools_to_its_own_session(root) -> None:
    """Kimi 轮次只为自己的会话提供 Harness 工具

    一个 Kimi 轮次挂载的 Harness 工具只服务于它自己的会话。
    """
    (root.home / "config.toml").write_text(kimi_portal_tools.hook_config(), encoding="utf-8")
    kimi_portal_tools.install(root.home)
    seen: dict = {}

    def run_turn(**kwargs):
        seen["disabled_tools"] = kwargs["disabled_tools"]
        # Kimi Code: the hook, then the MCP call.
        kimi_portal_tools.announce(_event("session-a", "hatg_instruction", {"instruction": "Ping"}, "call-7"))
        seen["result"] = kimi_portal_tools.call_portal_tool("hatg_instruction", {"instruction": "Ping"})
        return SimpleNamespace(response="done", session_id="session-a", turn_id="prompt-1", usage={})

    client = _fake_client([[f"{PREFIX}hatg_instruction", f"{PREFIX}jev_decide"]])
    client.run_turn = run_turn
    tool = HarnessTool(
        name="hatg_instruction", description="Call an instruction.", input_schema={"type": "object"},
        handler=lambda arguments: f"pong:{arguments['instruction']}",
    )
    context = mock.Mock()
    context.is_cancelled.return_value = False
    with mock.patch.object(backends, "shared_kimi_client", return_value=client), \
            mock.patch.object(backends, "_kimi_home", return_value=str(root.home)), \
            mock.patch.object(backends.KimiBackend, "record_provider_compaction"), \
            mock.patch.object(backends.KimiBackend, "observe_turn_usage", return_value=None):
        result = backends.KimiBackend().execute(_invocation((tool,)), context)  # type: ignore[arg-type]

    assert _text(seen["result"]) == "pong:Ping" and seen["result"]["isError"] is False
    assert seen["disabled_tools"] == [f"{PREFIX}jev_decide"]
    assert result.available_harness_tool_names == frozenset({"hatg_instruction"})
    assert [entry["name"] for entry in kimi_portal_tools.read_catalog()] == ["hatg_instruction"]
    assert not list((root.gateway / "turns").iterdir())      # nothing listens once the turn has ended


def test_without_the_hook_a_kimi_turn_runs_without_portal_tools(root) -> None:
    """没有 hook 时 Kimi 轮次不带 Portal 工具运行

    Kimi 服务没有配置 hook 时，轮次照常运行但不提供 Portal 工具。
    """
    seen: dict = {}
    client = _fake_client([[f"{PREFIX}hatg_instruction"]])
    client.run_turn = lambda **kwargs: seen.update(kwargs) or SimpleNamespace(
        response="done", session_id="session-a", turn_id="prompt-1", usage={},
    )
    tool = HarnessTool(name="hatg_instruction", description="x", input_schema={"type": "object"}, handler=lambda _a: "")
    context = mock.Mock()
    context.is_cancelled.return_value = False
    with mock.patch.object(backends, "shared_kimi_client", return_value=client), \
            mock.patch.object(backends, "_kimi_home", return_value=str(root.home)), \
            mock.patch.object(backends.KimiBackend, "record_provider_compaction"), \
            mock.patch.object(backends.KimiBackend, "observe_turn_usage", return_value=None):
        result = backends.KimiBackend().execute(_invocation((tool,)), context)  # type: ignore[arg-type]

    assert result.output["message"] == "done"
    assert result.available_harness_tool_names == frozenset()
    assert seen["disabled_tools"] == [f"{PREFIX}hatg_instruction"]     # nobody may call what no worker serves
    assert kimi_portal_tools.read_catalog() == []
