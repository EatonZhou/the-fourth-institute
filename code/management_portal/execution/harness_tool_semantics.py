"""Shared classification of tool calls and inline media payloads."""
from __future__ import annotations
import logging
import re
import shlex
from typing import Any

LOGGER = logging.getLogger(__name__)


def inline_media_bytes(value: object) -> int:
    """Count inline media once per block, including MCP base64 blocks."""
    total = 0
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            own = []
            for key, item in current.items():
                if str(key).replace("_", "").casefold() in {"imageurl", "audiourl"} and isinstance(item, str):
                    if item.lstrip().startswith("data:"):
                        own.append(len(item.encode("utf-8")))
                elif key == "data" and current.get("type") in {"image", "audio"} and isinstance(item, str):
                    own.append(len(item.encode("utf-8")))
                else:
                    stack.append(item)
            total += max(own, default=0)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return total


DEFAULT_READ_TOOL_NAMES = frozenset({
    "read_harness_artifact", "search_harness_artifact",
    "read_portal_resource", "search_portal_resource",
    "find",
    "find_text",
    "get_file",
    "list_directory",
    "list_files",
    "open",
    "open_file",
    "query",
    "read",
    "read_content",
    "read_file",
    "read_mcp_resource",
    "read_text_file",
    "search",
    "search_query",
    "view_file",
    "view_image",
})
_SHELL_CONTROL = re.compile(r"(?:\n|\r|;|&&|\|\||`|\$\(|>|<)")
_SIMPLE_READ_COMMANDS = frozenset({
    "cat", "grep", "head", "ls", "rg", "tail", "wc",
})
_SAFE_GIT_SUBCOMMANDS = frozenset({"diff", "log", "show", "status"})
_MUTATING_FIND_EXPRESSIONS = frozenset({
    "-delete", "-exec", "-execdir", "-fls", "-fprint", "-fprintf",
    "-ok", "-okdir",
})


def declared_tool_reads(tool: Any, arguments: dict[str, Any]) -> bool:
    read_only = getattr(tool, "read_only", False)
    if callable(read_only):
        try:
            return bool(read_only(arguments))
        except Exception:  # noqa: BLE001 - a tool that cannot tell is not a read
            LOGGER.warning("Harness tool %s could not tell whether a call only reads",
                           getattr(tool, "name", ""), exc_info=True)
            return False
    return read_only is True


def tool_call_reads(
    tool_name: str,
    arguments: dict[str, Any],
    read_tool_names: frozenset[str] = DEFAULT_READ_TOOL_NAMES,
) -> bool:
    name = str(tool_name or "").strip().rsplit("__", 1)[-1].rsplit(".", 1)[-1].casefold()
    if name in read_tool_names:
        return True
    if name not in {"exec", "exec_command", "shell", "terminal"}:
        return False
    command = arguments.get("cmd")
    if command is None:
        command = arguments.get("command")
    if isinstance(command, list):
        command = " ".join(str(value) for value in command)
    text = str(command or "").strip()
    if not text or _SHELL_CONTROL.search(text):
        return False
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    command = tokens[0].rsplit("/", 1)[-1]
    if command in _SIMPLE_READ_COMMANDS:
        return True
    if command == "git":
        return len(tokens) > 1 and tokens[1] in _SAFE_GIT_SUBCOMMANDS
    if command == "sed":
        return (
            "-n" in tokens[1:]
            and not any(
                token == "-i"
                or token.startswith("--in-place")
                or (
                    token.startswith("-")
                    and not token.startswith("--")
                    and "i" in token[1:]
                )
                for token in tokens[1:]
            )
        )
    if command == "find":
        return not any(
            token in _MUTATING_FIND_EXPRESSIONS
            for token in tokens[1:]
        )
    return False
