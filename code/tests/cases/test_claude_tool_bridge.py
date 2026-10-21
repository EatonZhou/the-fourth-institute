"""Portal's tools in Claude Code sessions.

Found in the Harness experiment: Claude Code puts MCP tools behind its tool
search, so before every Portal tool (hatg_instruction, read_harness_artifact,
...) the model first had to search for it.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parents[2] / "management_portal/claude_tool_bridge.py"


@pytest.fixture
def ClaudeToolBridge(monkeypatch):
    """The bridge, on the Claude Agent SDK when installed, else on its server shape.

    The SDK is installed in the Claude Code daemon's own environment, not in
    the test environment, so a plain import failed and this file never ran.
    The SDK's server config is ``{"type": "sdk", "name": ..., "instance": ...}``.
    """
    try:
        import claude_agent_sdk  # noqa: F401
    except ModuleNotFoundError:
        sdk = types.ModuleType("claude_agent_sdk")
        sdk.SdkMcpTool = lambda name, description, input_schema, handler: types.SimpleNamespace(name=name)
        sdk.create_sdk_mcp_server = lambda name, tools=None, version="1.0.0": {
            "type": "sdk", "name": name, "instance": object(),
        }
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    spec = importlib.util.spec_from_file_location("claude_tool_bridge_under_test", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ClaudeToolBridge


def test_portal_tools_are_always_in_the_prompt(ClaudeToolBridge) -> None:
    """Claude Code 会话中 Portal 工具始终在提示词中

    Portal 的进程内 MCP 服务标记为 alwaysLoad，模型无需先搜索即可调用 Portal 工具，允许的工具名带 mcp__portal__ 前缀。
    """
    bridge = ClaudeToolBridge([
        {"name": "hatg_instruction", "description": "Call an instruction.", "inputSchema": {"type": "object"}},
        {"name": "read_harness_artifact", "description": "Read a slice.", "inputSchema": {"type": "object"}},
    ])
    server = bridge.servers["portal"]
    assert server["type"] == "sdk" and server["name"] == "portal"
    assert server["alwaysLoad"] is True
    # What the SDK hands to Claude Code: everything but the in-process instance.
    sent = {key: value for key, value in server.items() if key != "instance"}
    assert json.loads(json.dumps(sent)) == {"type": "sdk", "name": "portal", "alwaysLoad": True}
    assert bridge.allowed_tools == ["mcp__portal__hatg_instruction", "mcp__portal__read_harness_artifact"]


def test_a_turn_without_portal_tools_has_no_server(ClaudeToolBridge) -> None:
    """没有 Portal 工具的轮次不挂载 MCP 服务

    本轮没有可用 Portal 工具时，Claude 工具桥不创建任何 MCP 服务。
    """
    assert ClaudeToolBridge([]).servers == {}
