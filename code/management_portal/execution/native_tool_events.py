"""The common shape in which an Adapter reports a Provider's own tool calls.

Every Provider names and reports its tools differently.  The Harness reads
one shape: ``item/started`` and ``item/completed`` events whose item is a
``commandExecution`` (a shell command, or a read, search or listing with its
``commandActions``) or a ``dynamicToolCall`` (any other tool).  An Adapter
that declares ``native_tool_events`` translates its Provider's events here;
the meaning of a Provider event stays with its Adapter.

Any other Provider output (text, reasoning, status) an Adapter reports as a
``provider/activity`` event, so that the Harness does not take a Provider
that works without calling tools for a silent one.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_TOOL_ITEM_TYPES = frozenset({"commandExecution", "dynamicToolCall", "toolCall", "mcpToolCall"})
_SHELL_TOOLS = frozenset({"bash", "shell", "exec", "run_command", "execute_command", "terminal"})
_READ_TOOLS = frozenset({"read", "read_file", "readfile", "view", "view_file", "readmediafile", "notebookread"})
_SEARCH_TOOLS = frozenset({"grep", "search", "rg", "search_files"})
_LIST_TOOLS = frozenset({"glob", "ls", "list", "list_files", "listfiles", "list_directory"})
_PATH_KEYS = ("file_path", "path", "filename", "file", "target_file", "absolute_path", "notebook_path")
_QUERY_KEYS = ("pattern", "query", "regex")
_FAILED_STATUSES = frozenset({"failed", "error", "declined", "cancelled"})
OUTPUT_TEXT_LIMIT = 4 * 1024 * 1024
PROVIDER_ACTIVITY_EVENT = "provider/activity"


def provider_activity() -> dict[str, Any]:
    """The Provider produced output that is not a common tool event."""

    return {"method": PROVIDER_ACTIVITY_EVENT, "params": {}}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def normalize_native_tool_event(event: Mapping[str, Any]) -> Mapping[str, Any]:
    """Represent Codex's interruptible sleep item as an observed tool call.

    durationMs is the Provider's observation, not proof that a requested wait
    elapsed. A started item never supplies a result; only item/completed does.
    Keep native identities and envelope fields for replay and turn attribution.
    """
    method = event.get("method")
    params = _mapping(event.get("params"))
    item = _mapping(params.get("item"))
    if method not in {"item/started", "item/completed"} or item.get("type") != "sleep":
        return event
    duration = item.get("durationMs")
    if not isinstance(item.get("id"), str) or not item["id"] or type(duration) is not int or duration < 0:
        raise ValueError("Native sleep requires its item identity and nonnegative durationMs")
    completed = method == "item/completed"
    normalized = {"type": "dynamicToolCall", "id": item["id"], "tool": "clock.sleep",
                  "arguments": {"duration_ms": duration},
                  "status": "completed" if completed else "inProgress"}
    if completed:
        normalized["contentItems"] = [{"type": "inputText", "text": json.dumps({"duration_ms": duration})}]
    return {**event, "params": {**params, "item": normalized}}


def _arguments(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _first(arguments: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _read_target(arguments: Mapping[str, Any]) -> str:
    """The file a read names, with its range, so a later part is another read."""

    path = _first(arguments, _PATH_KEYS)
    if not path:
        return ""
    parts = [
        f"{key}={arguments[key]}"
        for key in ("offset", "start_line", "line_start", "limit", "end_line", "line_end", "pages")
        if arguments.get(key) not in (None, "")
    ]
    return f"{path}#{','.join(parts)}" if parts else path


def _item(call_id: str, name: str, arguments: dict[str, Any], cwd: str) -> dict[str, Any]:
    kind = name.strip().casefold()
    command = _first(arguments, ("command", "cmd"))
    if kind in _SHELL_TOOLS or (command and kind in {"", "tool"}):
        return {"type": "commandExecution", "id": call_id, "command": command, "cwd": cwd}
    if kind in _READ_TOOLS:
        target = _read_target(arguments)
        return {
            "type": "commandExecution", "id": call_id, "command": f"{name} {target}".strip(), "cwd": cwd,
            "commandActions": [{"type": "read", "path": target}] if target else [],
        }
    if kind in _SEARCH_TOOLS:
        return {
            "type": "commandExecution", "id": call_id, "cwd": cwd,
            "command": f"{name} {_first(arguments, _QUERY_KEYS)}".strip(),
            "commandActions": [{
                "type": "search",
                "query": _first(arguments, _QUERY_KEYS),
                "path": _first(arguments, _PATH_KEYS),
            }],
        }
    if kind in _LIST_TOOLS:
        target = _first(arguments, _PATH_KEYS) or _first(arguments, _QUERY_KEYS)
        return {
            "type": "commandExecution", "id": call_id, "command": f"{name} {target}".strip(), "cwd": cwd,
            "commandActions": [{"type": "listFiles", "path": "/".join(
                part for part in (_first(arguments, _PATH_KEYS), _first(arguments, _QUERY_KEYS)) if part
            )}],
        }
    return {"type": "dynamicToolCall", "id": call_id, "tool": name, "arguments": arguments}


def _output_parts(value: object) -> tuple[str, list[str]]:
    """Text and inline image data of a tool result in any Provider's form."""

    if isinstance(value, str):
        return value, []
    texts: list[str] = []
    images: list[str] = []
    for part in value if isinstance(value, (list, tuple)) else ():
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, Mapping):
            continue
        kind = str(part.get("type") or "").casefold()
        if "image" in kind:
            source = _mapping(part.get("source"))
            data = str(
                part.get("imageUrl") or part.get("image_url") or part.get("data") or source.get("data") or ""
            )
            if data:
                images.append(data if data.startswith("data:") else f"data:image/unknown;base64,{data}")
        elif isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n".join(texts), images


def tool_started(call_id: object, name: object, arguments: object, *, cwd: str = "") -> dict[str, Any]:
    item = _item(str(call_id or ""), str(name or ""), _arguments(arguments), cwd)
    return {"method": "item/started", "params": {"item": {**item, "status": "inProgress"}}}


def tool_completed(
    call_id: object,
    name: object,
    arguments: object,
    output: object = "",
    *,
    failed: bool = False,
    cwd: str = "",
) -> dict[str, Any]:
    item = _item(str(call_id or ""), str(name or ""), _arguments(arguments), cwd)
    text, images = _output_parts(output)
    text = text[:OUTPUT_TEXT_LIMIT]
    item["status"] = "failed" if failed else "completed"
    if item["type"] == "commandExecution" and not images:
        item["aggregatedOutput"] = text
        item["exitCode"] = 1 if failed else 0
    else:
        # Images are counted from ``contentItems``, which only a tool item carries.
        if item["type"] == "commandExecution":
            item = {
                "type": "dynamicToolCall", "id": item["id"], "tool": str(name or ""),
                "arguments": _arguments(arguments), "status": item["status"],
            }
        item["success"] = not failed
        item["contentItems"] = [
            *([{"type": "inputText", "text": text}] if text else []),
            *({"type": "inputImage", "imageUrl": image} for image in images),
        ]
    return {"method": "item/completed", "params": {"item": item}}


class NativeToolEventTranslator:
    """Turn one turn's Provider events into the common tool events.

    It understands the event forms Portal's Adapters emit: ``item/started``
    and ``item/completed`` with a tool item, and ``response_item`` function
    calls with their outputs.  Other events yield nothing, except
    ``turn/completed``, which passes through so an observer knows the turn
    has ended.
    """

    def __init__(self, cwd: str = "") -> None:
        self.cwd = str(cwd or "")
        self._calls: dict[str, tuple[str, dict[str, Any]]] = {}

    def translate(self, event: object) -> list[dict[str, Any]]:
        if not isinstance(event, Mapping):
            return []
        event = normalize_native_tool_event(event)
        method = str(event.get("method") or "")
        if method == "turn/completed":
            return [dict(event)]
        item = _mapping(_mapping(event.get("params")).get("item"))
        if method in {"item/started", "item/completed"} and str(item.get("type") or "") in _TOOL_ITEM_TYPES:
            call_id = str(item.get("id") or item.get("callId") or "")
            name = str(
                item.get("tool") or item.get("name")
                or ("Bash" if item.get("type") == "commandExecution" else "")
            )
            # Claude's items carry ``arguments``, Kimi's the tool's ``input``.
            arguments = _arguments(item["arguments"] if item.get("arguments") is not None else item.get("input"))
            if item.get("command") and "command" not in arguments:
                arguments["command"] = str(item.get("command"))
            cwd = str(item.get("cwd") or self.cwd)
            if method == "item/started":
                self._calls[call_id] = (name, arguments)
                return [tool_started(call_id, name, arguments, cwd=cwd)]
            known_name, known_arguments = self._calls.pop(call_id, (name, arguments))
            output = (
                item.get("contentItems") if isinstance(item.get("contentItems"), list)
                else item.get("output") or item.get("aggregatedOutput") or ""
            )
            failed = (
                str(item.get("status") or "").casefold() in _FAILED_STATUSES
                or item.get("success") is False
            )
            return [tool_completed(
                call_id, name or known_name, arguments or known_arguments, output, failed=failed, cwd=cwd,
            )]
        payload = _mapping(event.get("payload"))
        if str(event.get("type") or "") != "response_item":
            return []
        call_id = str(payload.get("call_id") or "")
        if payload.get("type") == "function_call":
            name = str(payload.get("name") or "")
            arguments = _arguments(payload.get("arguments"))
            self._calls[call_id] = (name, arguments)
            return [tool_started(call_id, name, arguments, cwd=self.cwd)]
        if payload.get("type") == "function_call_output":
            name, arguments = self._calls.pop(call_id, ("", {}))
            return [tool_completed(
                call_id, name, arguments, payload.get("output"),
                failed=bool(payload.get("is_error")), cwd=self.cwd,
            )]
        return []

