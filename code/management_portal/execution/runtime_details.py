"""Runtime details: the activity cards an Execution projection produces.

A runtime detail is one card of an Execution's live activity: a turn's start
or end, a command, a tool call, a plan, a streamed Agent message.  The
interpreters of Provider events (``provider_event_projection``) build details
through ``EventContext.detail`` and add them to ``RuntimeDetails``, which keeps
each streamed update, stored activity and subagent run in one place.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from .contracts import TOKEN_USAGE_KEYS


_TURN_START_TITLES = {
    "initial": "开始执行",
    "context_compaction": "压缩后继续",
    "backend_service_update": "后端服务更新后恢复运行",
    "provider_recovery": "恢复运行",
    "continuation": "继续执行",
}
_TURN_OUTCOMES = {
    "completed": ("completed", "Completed"),
    "interrupted": ("interrupted", "已中断"),
    "failed": ("error", "执行失败"),
    "cancelled": ("cancelled", "已取消"),
}


def nonnegative_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def arguments_text(arguments: object) -> str:
    """A tool call's arguments as the text of its card."""

    if isinstance(arguments, str):
        return arguments.strip()
    return json.dumps(arguments, ensure_ascii=False) if arguments is not None else ""


def _runtime_detail(
    *,
    line_number: int,
    created_at: str,
    kind: str,
    title: str,
    content: str = "",
    status: str = "",
    phase: str = "",
    turn_id: str = "",
    turn_start_reason: str = "",
    interruption_reason: str = "",
) -> dict[str, Any]:
    detail = {
        "id": f"runtime:{line_number}:{kind}",
        "event_line": line_number,
        "created_at": created_at,
        "kind": kind,
        "title": title,
        "content": str(content or "").strip(),
        "status": status,
    }
    if phase:
        detail["phase"] = phase
    if turn_id:
        detail["turn_id"] = turn_id
    if turn_start_reason:
        detail["turn_start_reason"] = turn_start_reason
    if interruption_reason:
        detail["interruption_reason"] = interruption_reason
    return detail


def native_command_identity(activity: dict[str, Any]) -> str:
    """The identity of one Provider command event; empty without the Provider's own identities.

    Recovery may assign a new local sequence and observation time to the same
    native event. Keep independent calls/turns and distinct progress or
    terminal payloads, not a body-text deduplication.
    """

    if not (
        activity.get("kind") == "command"
        and activity.get("provider_item_id")
        and activity.get("turn_id")
        and activity.get("provider_event_type")
    ):
        return ""
    native_event = [activity.get(key, "") for key in (
        "execution_id", "turn_id", "provider_item_id",
        "provider_event_type", "status", "content",
    )]
    digest = hashlib.sha256(
        json.dumps(native_event, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"native-command:{digest}"


def activity_identity(value: object) -> tuple[str, str] | None:
    """Return the durable identity of one projected activity record.

    Execution event sequences make ``activity.id`` stable across waiter
    recovery. ``created_at`` keeps separately emitted, idless-source fallback
    events distinct when their local line-based ids happen to collide.
    """

    record = mapping(value)
    if str(record.get("type") or "") != "runtime_activity":
        return None
    activity = mapping(record.get("activity"))
    activity_id = str(activity.get("id") or "").strip()
    if not activity_id:
        return None
    native = native_command_identity(activity)
    if native:
        return native, ""
    return activity_id, str(record.get("created_at") or "")


def token_usage_brief(usage: dict[str, Any]) -> str:
    data = {
        key: number
        for key in TOKEN_USAGE_KEYS
        if (number := nonnegative_int(usage.get(key), 0)) > 0
    }
    labels = (
        ("total_tokens", "total"),
        ("input_tokens", "in"),
        ("output_tokens", "out"),
        ("reasoning_output_tokens", "reason"),
    )
    return " · ".join(f"{label} {data[key]}" for key, label in labels if data.get(key))


def plan_update_id(thread_id: str, turn_id: str) -> str:
    """The update identity of a turn's transient plan card."""

    return f"plan:{thread_id}:{turn_id}" if thread_id and turn_id else f"plan:{turn_id or 'current'}"


@dataclass(frozen=True)
class EventContext:
    """Where one event sits: its line, its time and its Execution."""

    line_number: int
    created_at: str
    execution_id: str

    def detail(self, **fields: Any) -> dict[str, Any]:
        return _runtime_detail(line_number=self.line_number, created_at=self.created_at, **fields)

    def scoped(self, update_id: str) -> str:
        """An update identity that stays apart from the same identity in another Execution."""

        if not update_id:
            return ""
        return f"{self.execution_id}:{update_id}" if self.execution_id else update_id

    def at(self, created_at: str) -> EventContext:
        return replace(self, created_at=created_at)

    def turn_started(self, *, reason: str, thread_id: str, turn_id: str) -> dict[str, Any]:
        """A turn started; ``reason`` names why (``_TURN_START_TITLES``)."""

        return self.detail(
            kind="started", title=_TURN_START_TITLES.get(reason, "继续执行"), content=thread_id,
            status="running", turn_id=turn_id, turn_start_reason=reason,
        )

    def thread_attached(self, *, thread_id: str, turn_id: str) -> dict[str, Any]:
        return self.detail(
            kind="started", title="Thread attached", content=thread_id, status="running", turn_id=turn_id,
        )

    def turn_ended(
        self, *, turn_id: str, status: str, usage: dict[str, Any], error: object,
        interruption_reason: str,
    ) -> dict[str, Any]:
        """A turn ended with the Provider's own outcome; an unknown outcome shows as ended."""

        normalized = str(status or "").strip().casefold()
        if normalized == "canceled":
            normalized = "cancelled"
        if normalized not in _TURN_OUTCOMES:
            normalized = "unknown"
        kind, title = _TURN_OUTCOMES.get(normalized, ("ended", "执行轮已结束"))
        if normalized == "interrupted" and interruption_reason == "worker_handover":
            title = "Worker 换代旧 turn 中断"
        message = str(error.get("message") or "") if isinstance(error, dict) else str(error or "")
        return self.detail(
            kind=kind, title=title, status=normalized, turn_id=turn_id,
            interruption_reason=interruption_reason if normalized == "interrupted" else "",
            content="\n".join(value for value in (token_usage_brief(usage), message) if value),
        )


class RuntimeDetails:
    """The details of one projection in order, each update kept in one place."""

    def __init__(self, *, include_updates: bool) -> None:
        # Streamed Agent messages are projected only when asked for.
        self.include_updates = include_updates
        # The text each streamed update has received so far.
        self.streamed_text: dict[str, str] = {}
        # Tool names by call identity, for the card of the call's output.
        self.tool_names: dict[str, str] = {}
        self._rows: list[dict[str, Any] | None] = []
        self._updates: dict[str, int] = {}
        self._subagents: dict[str, int] = {}
        self._stored: dict[tuple[str, str], int] = {}

    def add(self, detail: dict[str, Any]) -> None:
        self._rows.append(detail)

    def add_update(self, detail: dict[str, Any], update_id: str, *, replace_previous: bool = True) -> None:
        """Add one state of a streamed update, in the place of its previous state when it replaces it."""

        update_id = update_id.strip()
        if update_id:
            detail["id"] = f"runtime-update:{update_id}"
        if update_id and replace_previous and update_id in self._updates:
            self._rows[self._updates[update_id]] = detail
            return
        self._rows.append(detail)
        if update_id:
            self._updates[update_id] = len(self._rows) - 1

    def add_subagent(self, detail: dict[str, Any], update_id: str) -> None:
        # One activity per subagent run, at the end when it changes: it is the
        # newest activity, which a page showing the last details keeps.
        detail["id"] = f"runtime-update:{update_id}"
        if update_id in self._subagents:
            self._rows[self._subagents[update_id]] = None
        self._rows.append(detail)
        self._subagents[update_id] = len(self._rows) - 1

    def add_stored(self, detail: dict[str, Any], identity: tuple[str, str] | None) -> None:
        """Add a stored activity; a replay of one already added takes its place."""

        if identity is not None and identity in self._stored:
            self._rows[self._stored[identity]] = detail
            return
        if identity is not None:
            self._stored[identity] = len(self._rows)
        self._rows.append(detail)

    def end_plan(self, context: EventContext, thread_id: str, turn_id: str) -> None:
        """End the turn's transient plan card."""

        self.add_update(
            context.detail(kind="plan", title="Plan ended", content="", status="completed"),
            context.scoped(plan_update_id(thread_id, turn_id)),
        )

    def rows(self) -> list[dict[str, Any]]:
        return [detail for detail in self._rows if detail is not None]
