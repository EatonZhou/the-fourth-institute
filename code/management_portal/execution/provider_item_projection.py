"""Runtime details of the items a Provider event reports.

An item is one unit of a turn's work: an Agent message, reasoning, a command,
a tool call, a file change, a web search.  App-server notifications
(``item/started``) and Codex exec records (``item.started``) carry the same
items, mostly in the app-server's names (``commandExecution``), a few also in
the exec stream's names (``command_execution``).  ``ITEMS`` maps each item
type to the handler of its card; an item of another type has none.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from management_common.async_questions import normalize_async_questions

from .runtime_details import EventContext, RuntimeDetails, arguments_text, native_command_identity

# The phases in which an item is reported, as its events name them.
ITEM_PHASES = ("started", "progress", "completed")


@dataclass(frozen=True)
class ItemEvent:
    """One report of an item: the phase it reached and the envelope's params."""

    item: dict[str, Any]
    phase: str
    params: dict[str, Any]
    context: EventContext

    @property
    def completed(self) -> bool:
        return self.phase == "completed"

    def status(self) -> str:
        return _item_status(self.item, "completed" if self.completed else "running")

    def turn_id(self) -> str:
        return str(self.params.get("turnId") or self.item.get("turnId") or self.item.get("turn_id") or "")


def _item_status(item: dict[str, Any], default: str) -> str:
    status = str(item.get("status") or "").strip()
    normalized = re.sub(r"[^a-z]", "", status.casefold())
    if normalized in {"inprogress", "queued", "running", "working"}:
        return "running"
    if normalized in {"cancelled", "canceled", "completed", "failed", "suspended"}:
        return normalized.replace("canceled", "cancelled")
    return status or default


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        if isinstance(part, str):
            chunks.append(part)
            continue
        if not isinstance(part, dict):
            continue
        text = part.get("text") or part.get("input_text") or part.get("output_text")
        if text:
            chunks.append(str(text))
    return "\n\n".join(chunks).strip()


def _item_text(item: dict[str, Any]) -> str:
    return str(
        item.get("text")
        or _content_text(item.get("content"))
        or _content_text(item.get("summary"))
        or ""
    ).strip()


def _item_progress(item: dict[str, Any]) -> str:
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
    text = str(progress.get("text") or "").strip()
    percent = progress.get("percent")
    if percent not in (None, ""):
        try:
            percent_text = f"{float(percent):g}%"
        except (TypeError, ValueError):
            percent_text = str(percent)
        return " · ".join(filter(None, (text, percent_text)))
    return text


def _tool_content(item: dict[str, Any]) -> str:
    progress = _item_progress(item)
    if progress:
        return progress
    description = str(item.get("description") or "").strip()
    if description:
        return description
    display = item.get("display") if isinstance(item.get("display"), dict) else {}
    for key in ("command", "path", "url", "skill_name"):
        value = str(display.get(key) or "").strip()
        if value:
            return value
    arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
    return arguments_text(arguments) if arguments else ""


def _file_change_content(item: dict[str, Any]) -> str:
    changes = item.get("changes") if isinstance(item.get("changes"), list) else []
    summaries: list[str] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        kind_value = change.get("kind")
        kind = str(kind_value.get("type") or "") if isinstance(kind_value, dict) else str(kind_value or "")
        path = str(change.get("path") or "").strip()
        summary = " ".join(part for part in (kind, path) if part)
        if summary:
            summaries.append(summary)
    return "\n".join(summaries)


def _web_search_content(item: dict[str, Any]) -> str:
    query = str(item.get("query") or "").strip()
    if query:
        return query
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    queries = action.get("queries") if isinstance(action.get("queries"), list) else []
    return "\n".join(str(value).strip() for value in queries if str(value).strip())


def _agent_message(event: ItemEvent, details: RuntimeDetails) -> None:
    if not details.include_updates:
        return
    item = event.item
    item_id = str(item.get("id") or "").strip()
    update_id = event.context.scoped(item_id)
    detail = event.context.detail(
        kind="update", title="Update",
        content=_item_text(item) or details.streamed_text.get(update_id, ""),
        status=event.status(),
    )
    detail.update(
        execution_id=event.context.execution_id,
        message_phase=str(item.get("phase") or ""),
        turn_id=event.turn_id(),
        provider_item_id=item_id,
    )
    questions = normalize_async_questions(item.get("questions"))
    if questions:
        detail["questions"] = questions
    details.add_update(detail, update_id, replace_previous=bool(item_id))


def _reasoning(event: ItemEvent, details: RuntimeDetails) -> None:
    item_id = str(event.item.get("id") or "").strip()
    details.add_update(
        event.context.detail(kind="reasoning", title="Thinking", content=_item_text(event.item), status=event.status()),
        item_id, replace_previous=bool(item_id),
    )


_COMMAND_TITLES = {"started": "Command started", "progress": "Command progress", "completed": "Command finished"}


def _command(event: ItemEvent, details: RuntimeDetails) -> None:
    item = event.item
    command = str(item.get("command") or "")
    exit_code = item.get("exit_code") if "exit_code" in item else item.get("exitCode")
    status = event.status()
    content = _item_progress(item) or (f"$ {command}" if command else "")
    if event.completed and exit_code not in (None, ""):
        status = "completed" if str(exit_code) == "0" else "failed"
        content = f"{content}\nexit {exit_code}".strip()
    detail = event.context.detail(
        kind="command", title=_COMMAND_TITLES[event.phase], content=content, status=status, turn_id=event.turn_id(),
    )
    detail.update(
        execution_id=event.context.execution_id,
        provider_item_id=str(item.get("id") or "").strip(),
        provider_event_type=f"item.{event.phase}",
    )
    detail["id"] = native_command_identity(detail) or detail["id"]
    details.add(detail)


def _tool_call(event: ItemEvent, details: RuntimeDetails) -> None:
    item = event.item
    name = str(item.get("name") or item.get("tool") or "Tool").strip() or "Tool"
    server = str(item.get("server") or "").strip()
    if server:
        name = f"{server}.{name}"
    title = {"started": "Tool", "progress": "Tool progress", "completed": "Tool finished"}[event.phase]
    details.add(event.context.detail(
        kind="tool_output" if event.completed else "tool_call",
        title=f"{title}: {name}", content=_tool_content(item), status=event.status(),
    ))


def _agent_execution(event: ItemEvent, details: RuntimeDetails) -> None:
    item = event.item
    name = str(item.get("name") or "Subagent").strip() or "Subagent"
    title = {"started": "Subagent", "progress": "Subagent update", "completed": "Subagent finished"}[event.phase]
    details.add(event.context.detail(
        kind="subagent", title=f"{title}: {name}",
        content=str(item.get("description") or item.get("command") or item.get("output") or "").strip(),
        status=event.status(),
    ))


def _card(
    kind: str, running_title: str, completed_title: str, content: Callable[[dict[str, Any]], str],
) -> Callable[[ItemEvent, RuntimeDetails], None]:
    """The handler of an item shown as one card, titled by whether it completed."""

    def project(event: ItemEvent, details: RuntimeDetails) -> None:
        details.add(event.context.detail(
            kind=kind, title=completed_title if event.completed else running_title,
            content=content(event.item), status=event.status(),
        ))

    return project


ITEMS: dict[str, Callable[[ItemEvent, RuntimeDetails], None]] = {
    "agentMessage": _agent_message,
    "reasoning": _reasoning,
    "commandExecution": _command,
    "command_execution": _command,
    "tool_call": _tool_call,
    "toolCall": _tool_call,
    "mcp_tool_call": _tool_call,
    "mcpToolCall": _tool_call,
    "dynamicToolCall": _tool_call,
    "fileChange": _card("file_change", "Changing files", "File changes completed", _file_change_content),
    "webSearch": _card("web_search", "Searching the web", "Web search completed", _web_search_content),
    "imageView": _card("image", "Inspecting image", "Image inspected", lambda item: str(item.get("path") or "")),
    "imageGeneration": _card(
        "image", "Generating image", "Image generated", lambda item: str(item.get("revisedPrompt") or ""),
    ),
    "contextCompaction": _card("compact", "Compacting context", "Context compacted", lambda item: ""),
    "agentExecution": _agent_execution,
}


def project_item(
    item: dict[str, Any], phase: str, params: dict[str, Any], context: EventContext, details: RuntimeDetails,
) -> None:
    """Add the card of one item report; ``phase`` is one of ``ITEM_PHASES``."""

    handler = ITEMS.get(str(item.get("type") or ""))
    if handler is not None:
        handler(ItemEvent(item, phase, params, context), details)
