"""The Claude Code daemon's modules in the test environment.

The Claude Agent SDK is installed only in the daemon's own environment
(``claude-agent-sdk-current``), which has no pytest, so tests of the daemon
were skipped wherever they ran.  When the SDK is not importable, these helpers
load the daemon's modules on a synthetic SDK that has the same message types,
fields and defaults as the pinned SDK (0.2.165).  The tests drive the daemon
with their own fake client, so only these shapes matter.

The synthetic SDK is never left in ``sys.modules``: modules that import the
SDK are loaded under private names, so other tests still see the real import
behaviour.
"""
from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import re
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# name -> (required fields, optional fields), in the SDK's declaration order.
_MESSAGE_TYPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "AssistantMessage": (
        ("content", "model"),
        ("parent_tool_use_id", "error", "usage", "message_id", "stop_reason", "session_id", "uuid"),
    ),
    "ResultMessage": (
        ("subtype", "duration_ms", "duration_api_ms", "is_error", "num_turns", "session_id"),
        ("stop_reason", "total_cost_usd", "usage", "result", "structured_output", "model_usage",
         "permission_denials", "deferred_tool_use", "errors", "api_error_status", "uuid",
         "terminal_reason", "origin"),
    ),
    "UserMessage": (("content",), ("uuid", "parent_tool_use_id", "tool_use_result", "origin")),
    "SystemMessage": (("subtype", "data"), ()),
    "StreamEvent": (("uuid", "session_id", "event"), ("parent_tool_use_id",)),
    "RateLimitEvent": (("rate_limit_info", "uuid", "session_id"), ()),
    "RateLimitInfo": (
        ("status",),
        ("resets_at", "rate_limit_type", "utilization", "overage_status", "overage_resets_at",
         "overage_disabled_reason", "raw"),
    ),
    "TextBlock": (("text",), ()),
    "ThinkingBlock": (("thinking", "signature"), ()),
    "ToolResultBlock": (("tool_use_id",), ("content", "is_error")),
    "ServerToolUseBlock": (("id", "name", "input"), ()),
    "ServerToolResultBlock": (("tool_use_id", "content"), ()),
    "ToolUseBlock": (("id", "name", "input"), ()),
    "HookMatcher": ((), ("matcher", "hooks", "timeout")),
    "SdkMcpTool": (("name", "description", "input_schema", "handler"), ("annotations",)),
}


def _dataclass(name: str, required: tuple[str, ...], optional: tuple[str, ...]) -> type:
    fields: list[Any] = [*required, *((field, Any, dataclasses.field(default=None)) for field in optional)]
    return dataclasses.make_dataclass(name, fields)


class _Options:
    """ClaudeAgentOptions: keeps whatever the daemon configures."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _Client:
    """ClaudeSDKClient: tests replace the client a session uses."""

    def __init__(self, options: Any = None, *_args: Any, **_kwargs: Any) -> None:
        self.options = options


def synthetic_sdk() -> types.ModuleType:
    sdk = types.ModuleType("claude_agent_sdk")
    for name, (required, optional) in _MESSAGE_TYPES.items():
        setattr(sdk, name, _dataclass(name, required, optional))
    sdk.ClaudeAgentOptions = _Options
    sdk.ClaudeSDKClient = _Client
    sdk.ClaudeSDKError = type("ClaudeSDKError", (Exception,), {})
    sdk.project_key_for_directory = lambda directory=None: re.sub(
        r"[^A-Za-z0-9]", "-", str(Path(directory or ".").resolve()),
    )
    sdk.create_sdk_mcp_server = lambda name, version="1.0.0", tools=None: {
        "type": "sdk", "name": name, "instance": object(),
    }
    sdk.__version__ = "0.2.165-synthetic"
    return sdk


def sdk_of(module: types.ModuleType) -> types.ModuleType:
    """The SDK ``module`` was loaded on, real or synthetic."""
    return getattr(module, "_claude_sdk", None) or importlib.import_module("claude_agent_sdk")


def _pinned_sdk_importable() -> bool:
    """The importable SDK has the message fields of the pinned one.

    The Portal venv holds an older SDK (0.2.111): its ``UserMessage`` has no
    ``origin``, so the daemon's tests run on the synthetic SDK there too.
    """
    try:
        real = importlib.import_module("claude_agent_sdk")
    except ModuleNotFoundError as exc:
        if exc.name != "claude_agent_sdk":
            raise
        return False
    for name, (required, optional) in _MESSAGE_TYPES.items():
        kind = getattr(real, name, None)
        if kind is None:
            return False
        if dataclasses.is_dataclass(kind) and not {*required, *optional} <= {field.name for field in dataclasses.fields(kind)}:
            return False
    return True


def load_claude_module(name: str) -> types.ModuleType:
    """``management_portal.<name>``, on the synthetic SDK when the pinned one is not importable."""
    if _pinned_sdk_importable():
        return importlib.import_module(f"management_portal.{name}")
    before = dict(sys.modules)
    sdk = sys.modules["claude_agent_sdk"] = synthetic_sdk()
    try:
        spec = importlib.util.spec_from_file_location(
            f"_synthetic_sdk_{name}", ROOT / "management_portal" / f"{name}.py",
        )
        module = importlib.util.module_from_spec(spec)
        module._claude_sdk = sdk
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        # Modules that imported the synthetic SDK keep their reference to it,
        # but nothing else sees it: the real import still fails elsewhere.
        for loaded in set(sys.modules) - set(before):
            source = getattr(sys.modules[loaded], "__file__", "") or ""
            if loaded != spec.name and source.endswith(".py") and _IMPORTS_SDK.search(
                Path(source).read_text(errors="ignore"),
            ):
                sys.modules.pop(loaded, None)
        if "claude_agent_sdk" in before:
            sys.modules["claude_agent_sdk"] = before["claude_agent_sdk"]
        else:
            sys.modules.pop("claude_agent_sdk", None)


_IMPORTS_SDK = re.compile(r"^\s*(?:from claude_agent_sdk import|import claude_agent_sdk)", re.M)
