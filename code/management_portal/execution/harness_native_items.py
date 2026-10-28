"""What a Provider's native tool item is: its reads, outputs, inline media and failure.

The native tool loop governor classifies each item the Provider reports
(app-server item shapes) with these functions; none of them keeps state.
"""
from __future__ import annotations

import os
import re
from typing import Any

from .harness_tool_semantics import inline_media_bytes, tool_call_reads


NATIVE_TOOL_ITEM_TYPES = frozenset({
    "commandExecution",
    "mcpToolCall",
    "dynamicToolCall",
    "webSearch",
    "imageView",
    "collabAgentToolCall",
})
_READ_COMMAND_ACTION_TYPES = frozenset({"read", "listFiles", "search"})

_TEST_COMMAND_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:[^\s]+/)?(?:pytest|playwright\s+test|"
    r"npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test)(?:\s|$)",
    re.IGNORECASE,
)
_PATH_CANDIDATE_RE = re.compile(
    r"(?<![\w.-])"
    r"((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,12})"
    r"(?=[:\s\"'()\[\],]|$)"
)
_SHELL_READ_COMMAND_RE = re.compile(
    r"(?:^|&&|\|\||;|\||\bdo\s+)\s*"
    r"(?:command\s+-v|pwd|ls|find|rg|grep|cat|head|tail|wc|stat|file|"
    r"readlink|sed\s+-n|git\s+(?:status|diff|show|log|ls-files))\b",
    re.IGNORECASE,
)
_SHELL_DISCOVERY_COMMAND_RE = re.compile(
    r"(?:^|&&|\|\||;|\||\bdo\s+)\s*"
    r"(?:ls\b|find\b|rg\b[^;&|]*\s--files\b|"
    r"git\s+ls-files\b|(?:rg|grep)\b[^;&|]+)",
    re.IGNORECASE,
)
_SHELL_MUTATION_RE = re.compile(
    r"(?:^|&&|\|\||;|\||\bdo\s+)\s*"
    r"(?:rm|mv|cp|install|mkdir|rmdir|touch|truncate|tee|chmod|chown|"
    r"ln|patch|apply_patch|git\s+(?:add|commit|checkout|reset|restore|"
    r"clean|merge|rebase|cherry-pick)|sed\s+-[^;&|]*i)\b|"
    r"(?<![0-9])>(?![>&])",
    re.IGNORECASE,
)


def native_tool_item(item: dict[str, Any]) -> bool:
    return str(item.get("type") or "") in NATIVE_TOOL_ITEM_TYPES


def item_identity(item: dict[str, Any]) -> str:
    return str(
        item.get("id")
        or item.get("callId")
        or item.get("call_id")
        or ""
    ).strip()


def runs_tests(item: dict[str, Any]) -> bool:
    return bool(_TEST_COMMAND_RE.search(_command_text(item)))


def tool_failed(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().casefold()
    if status in {"failed", "declined", "error"}:
        return True
    exit_code = item.get("exitCode", item.get("exit_code"))
    try:
        if exit_code is not None and int(exit_code) != 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(item.get("error"))


def approximate_output_bytes(item: dict[str, Any]) -> int:
    """Use bounded/shallow accounting on the shared app-server reader."""

    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        return len(str(
            item.get("aggregatedOutput")
            or item.get("aggregated_output")
            or item.get("formatted_output")
            or item.get("stdout")
            or ""
        ))
    if item_type == "mcpToolCall":
        result = item.get("result")
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                return sum(
                    len(str(part.get("text") or ""))
                    for part in content[:128]
                    if isinstance(part, dict)
                )
        return len(str(result or "")) if isinstance(result, str) else 0
    if item_type == "dynamicToolCall":
        content_items = item.get("contentItems")
        if isinstance(content_items, list):
            return sum(
                len(str(part.get("text") or part.get("imageUrl") or ""))
                for part in content_items[:128]
                if isinstance(part, dict)
            )
    return 0


def approximate_inline_media_bytes(item: dict[str, Any]) -> int:
    """Estimate model-facing image bytes from payloads or ImageView paths."""

    item_type = str(item.get("type") or "")
    if item_type == "imageView":
        path = str(item.get("path") or "").strip()
        if not path or not os.path.isabs(path):
            return 0
        try:
            source_bytes = os.path.getsize(path)
        except OSError:
            return 0
        # view_image returns a base64 data URL.  Include the base64 expansion;
        # the small MIME prefix is immaterial for pressure thresholds.
        return 4 * ((max(0, source_bytes) + 2) // 3)
    if item_type == "dynamicToolCall":
        content = item.get("contentItems")
    elif item_type == "mcpToolCall":
        result = item.get("result")
        result = result if isinstance(result, dict) else {}
        content = result.get("content")
    else:
        content = None
    if not isinstance(content, list):
        return 0
    return inline_media_bytes(content[:128])


def discovery_target_count(item: dict[str, Any]) -> int:
    return len(set(_PATH_CANDIDATE_RE.findall(_output_text(item))))


def read_profile(
    item: dict[str, Any],
    *,
    count_discovery_targets: bool = True,
) -> dict[str, Any]:
    item_type = str(item.get("type") or "")
    keys = semantic_read_keys(item)
    if not keys:
        return {
            "read_only": False,
            "read_operations": 0,
            "discovery": False,
            "discovery_targets": 0,
        }
    discovery = item_type in {"webSearch"}
    read_only = True
    if item_type == "commandExecution":
        actions = _command_actions(item)
        typed_actions = tuple(
            _command_action_type(action)
            for action in actions
        )
        if typed_actions and all(
            action_type in _READ_COMMAND_ACTION_TYPES
            for action_type in typed_actions
        ):
            read_only = all(
                action_type in _READ_COMMAND_ACTION_TYPES
                for action_type in typed_actions
            )
            discovery = any(
                action_type in {"listFiles", "search"}
                for action_type in typed_actions
            )
        else:
            shell_matches = _shell_read_matches(_command_text(item))
            read_only = bool(shell_matches)
            discovery = bool(
                read_only
                and _SHELL_DISCOVERY_COMMAND_RE.search(_command_text(item))
            )
    return {
        "read_only": read_only,
        "read_operations": len(keys) if read_only else 0,
        "discovery": discovery,
        "discovery_targets": (
            discovery_target_count(item)
            if discovery and read_only and count_discovery_targets
            else 0
        ),
    }


def semantic_read_keys(item: dict[str, Any]) -> tuple[str, ...]:
    item_type = str(item.get("type") or "")
    keys: list[str] = []
    if item_type == "commandExecution":
        cwd = str(item.get("cwd") or "")
        actions = _command_actions(item)
        for action in actions:
            action_type = _command_action_type(action)
            if action_type == "read":
                keys.append(
                    "command:read:"
                    f"{_path_key(action.get('path'), cwd)}"
                )
            elif action_type == "listFiles":
                keys.append(
                    "command:list:"
                    f"{_path_key(action.get('path'), cwd) or cwd}"
                )
            elif action_type == "search":
                keys.append(
                    "command:search:"
                    f"{_path_key(action.get('path'), cwd) or cwd}:"
                    f"{_normalized_text(action.get('query'))}"
                )
        if not keys:
            keys.extend(_shell_read_keys(item))
    elif item_type == "webSearch":
        keys.append(f"web:{_normalized_text(item.get('query'))}")
    elif item_type == "mcpToolCall" and item.get("readOnlyHint") is True:
        keys.append(
            "mcp:"
            f"{_normalized_text(item.get('server'))}:"
            f"{_normalized_text(item.get('tool'))}:"
            f"{_bounded_value_key(item.get('arguments'))}"
        )
    elif item_type == "dynamicToolCall":
        tool_name = str(item.get("tool") or "")
        if tool_call_reads(tool_name, item.get("arguments") or {}):
            keys.append(
                "dynamic:"
                f"{_normalized_text(tool_name)}:"
                f"{_bounded_value_key(item.get('arguments'))}"
            )
    return tuple(key for key in keys if key and not key.endswith(":{}"))


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "")[:2_048].split()).casefold()


def _path_key(value: object, cwd: str) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)
    return os.path.normpath(path)


def _command_text(item: dict[str, Any]) -> str:
    command = item.get("command")
    if isinstance(command, list):
        return str(command[-1] if command else "")
    return str(command or "")


def _command_actions(item: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    actions = item.get("commandActions")
    if not isinstance(actions, list):
        actions = item.get("parsed_cmd")
    return tuple(
        action for action in (actions if isinstance(actions, list) else ())
        if isinstance(action, dict)
    )


def _command_action_type(action: dict[str, Any]) -> str:
    clean = re.sub(
        r"[^a-z]",
        "",
        str(action.get("type") or "").casefold(),
    )
    return {
        "read": "read",
        "listfiles": "listFiles",
        "search": "search",
    }.get(clean, clean)


def _shell_read_matches(command: str) -> tuple[str, ...]:
    if (
        not command
        or _TEST_COMMAND_RE.search(command)
        or _SHELL_MUTATION_RE.search(command)
    ):
        return ()
    return tuple(
        match.group(0).strip()
        for match in _SHELL_READ_COMMAND_RE.finditer(command)
    )


def _shell_read_keys(item: dict[str, Any]) -> tuple[str, ...]:
    command = _command_text(item)
    matches = _shell_read_matches(command)
    normalized = _normalized_text(command)
    return tuple(
        f"command:shell-read:{normalized}:{index}:{_normalized_text(match)}"
        for index, match in enumerate(matches, start=1)
    )


def _bounded_value_key(value: object, *, budget: int = 2_048) -> str:
    if budget <= 0:
        return "…"
    if value is None or isinstance(value, (bool, int, float)):
        return str(value)
    if isinstance(value, str):
        return value[:budget]
    if isinstance(value, dict):
        parts: list[str] = []
        remaining = budget
        for key in sorted(value, key=str)[:32]:
            prefix = f"{str(key)[:80]}:"
            rendered = _bounded_value_key(
                value[key],
                budget=max(0, remaining - len(prefix)),
            )
            part = prefix + rendered
            parts.append(part)
            remaining -= len(part)
            if remaining <= 0:
                break
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        parts = []
        remaining = budget
        for item in value[:32]:
            rendered = _bounded_value_key(item, budget=remaining)
            parts.append(rendered)
            remaining -= len(rendered)
            if remaining <= 0:
                break
        return "[" + ",".join(parts) + "]"
    return str(value)[:budget]


def _output_text(item: dict[str, Any], *, limit: int = 64 * 1024) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        return str(
            item.get("aggregatedOutput")
            or item.get("aggregated_output")
            or item.get("formatted_output")
            or item.get("stdout")
            or ""
        )[:limit]
    if item_type == "mcpToolCall":
        result = item.get("result")
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                return "\n".join(
                    str(part.get("text") or "")
                    for part in content[:128]
                    if isinstance(part, dict)
                )[:limit]
        return str(result or "")[:limit] if isinstance(result, str) else ""
    if item_type == "dynamicToolCall":
        content_items = item.get("contentItems")
        if isinstance(content_items, list):
            return "\n".join(
                str(part.get("text") or "")
                for part in content_items[:128]
                if isinstance(part, dict)
            )[:limit]
    return ""
