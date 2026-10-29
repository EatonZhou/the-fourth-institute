"""Runtime details of Codex rollout records.

A Codex rollout (its session file) records the model's tool calls as
``response_item`` records whose payload is a ``function_call`` or
``custom_tool_call`` and, later, the call's ``function_call_output``.  The
Claude Code Adapter reports Claude's ``tool_use`` blocks other than Bash, and
every ``tool_result`` block, in the same records (``claude_code_daemon``).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .runtime_details import EventContext, RuntimeDetails, arguments_text, mapping

Handler = Callable[[dict[str, Any], EventContext, RuntimeDetails], None]


def _tool_identity(payload: dict[str, Any]) -> tuple[str, str, str]:
    """The tool's kind, namespace and name; an MCP tool is named ``mcp__<server>__<tool>``."""

    name = str(payload.get("name") or "tool").strip() or "tool"
    namespace = str(payload.get("namespace") or "").strip()
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            return "mcp", parts[1], "__".join(parts[2:])
    return "tool", namespace, name


def _output_status(payload: dict[str, Any], output: str) -> str:
    if payload.get("is_error") is True:
        return "failed"
    # Codex's shell tools end their output with the process exit code.
    code = re.search(r"Process exited with code (-?\d+)", output)
    return "failed" if code and code.group(1) != "0" else "completed"


def _call(payload: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    kind, namespace, name = _tool_identity(payload)
    details.tool_names[str(payload.get("call_id") or "")] = name
    label = "MCP" if kind == "mcp" else "Tool"
    details.add(context.detail(
        kind="tool_call",
        title=f"{label}: {namespace + '.' if namespace else ''}{name}",
        content=arguments_text(payload.get("arguments")),
        status="running",
    ))


def _output(payload: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    name = details.tool_names.get(str(payload.get("call_id") or ""), "tool")
    content = str(payload.get("output") or "")
    details.add(context.detail(
        kind="tool_output", title=f"Tool finished: {name}", content=content, status=_output_status(payload, content),
    ))


_PAYLOADS: dict[str, Handler] = {
    "function_call": _call,
    "custom_tool_call": _call,
    "function_call_output": _output,
}


def _response_item(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    payload = mapping(event.get("payload"))
    handler = _PAYLOADS.get(str(payload.get("type") or ""))
    if handler is not None:
        handler(payload, context, details)


RECORDS: dict[str, Handler] = {
    "response_item": _response_item,
}
