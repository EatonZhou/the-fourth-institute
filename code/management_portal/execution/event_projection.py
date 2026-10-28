"""Project an Execution's events into its messages and its runtime activity.

An Execution's events are Portal's own (the Execution service's start and
end, the Agent Harness report, a recovery input, a stored activity record)
or a Provider event, which ``provider_event_projection`` hands to the
interpreter of its wire form.  The activity log keeps the projected activity
of each Execution, apart from any page's display limits.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from management_common.file_lock import lock_stream
from management_common.message_identity import derived_message_identity
from .config import execution_state_root
from .harness_activity_card import harness_activity_card
from .message_projection import model_message_id
from .provider_event_projection import project_provider_event
from .recovery_messages import RECOVERY_INPUT_EVENT, recovery_input_message
from .runtime_details import EventContext, RuntimeDetails, activity_identity, mapping, nonnegative_int


DEFAULT_RUNTIME_DETAIL_LIMIT = 14
MAX_ACTIVITY_TRACE_BYTES = 32 * 1024 * 1024
ACTIVITY_TRACE_TTL_SECONDS = 30 * 24 * 60 * 60
_EXECUTION_ID = re.compile(r"execution-[0-9a-f]{24}")
_RUNNING_STATUSES = frozenset({"running", "in_progress", "inprogress"})
# The text fields of a stored activity a projection carries over.
_STORED_TEXT_FIELDS = (
    "execution_id",
    "message_phase",
    "turn_id",
    "turn_start_reason",
    "interruption_reason",
    "provider_item_id",
)
# The fields of a detail its stored activity keeps.
_STORED_ACTIVITY_FIELDS = (
    "id",
    "kind",
    "title",
    "content",
    "status",
    "phase",
    *_STORED_TEXT_FIELDS,
    "provider_event_type",
    "questions",
    "content_parts",
    "native_plan",
    "subagent",
)
_ACTIVITY_PRUNE_LOCK = threading.Lock()
_ACTIVITY_PRUNE_SCANS: dict[Path, Any] = {}
_ACTIVITY_WRITE_LOCK = threading.Lock()
_ACTIVITY_WRITES: OrderedDict[Path, tuple[tuple[int, int, int, int], int, set[tuple[str, str]]]] = OrderedDict()


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sequenced(events: Iterable[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """Execution events with their sequence; an event without one takes its position."""

    return [
        (nonnegative_int(event.get("sequence"), index), event)
        for index, event in enumerate(events, 1)
        if isinstance(event, dict)
    ]


def _empty_projection(execution_id: str) -> dict[str, Any]:
    return {"execution_id": execution_id, "outputs": [], "runtime_details": []}


def _stored_activity(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    activity = mapping(event.get("activity"))
    kind = str(activity.get("kind") or "").strip()
    title = str(activity.get("title") or "").strip()
    if not kind or not title:
        return
    detail = context.detail(
        kind=kind,
        title=title,
        content=str(activity.get("content") or ""),
        status=str(activity.get("status") or ""),
        phase=str(activity.get("phase") or ""),
    )
    for key in _STORED_TEXT_FIELDS:
        if activity.get(key) not in (None, ""):
            detail[key] = str(activity[key])
    for key, value_type in (("questions", list), ("content_parts", list), ("native_plan", dict)):
        if isinstance(activity.get(key), value_type):
            detail[key] = activity[key]
    stored_id = str(activity.get("id") or "").strip()
    if not stored_id.startswith("runtime-update:"):
        if stored_id:
            detail["id"] = stored_id
        details.add_stored(detail, activity_identity(event))
        return
    update_id = stored_id.removeprefix("runtime-update:")
    if kind != "subagent":
        details.add_update(detail, update_id)
        return
    if isinstance(activity.get("subagent"), dict):
        detail["subagent"] = activity["subagent"]
    details.add_subagent(detail, update_id)


def _harness_input(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    # The input a Harness sent to recover the turn: a message of its own.
    report = mapping(event.get("details"))
    continued = str(report.get("input_delivery") or "") == "continued"
    detail = context.at(str(report.get("created_at") or context.created_at)).detail(
        kind="harness_input",
        title="Harness 让 Provider 继续这一轮" if continued else "Harness 把这一轮的输入交给 Provider",
        content=str(report.get("prompt") or ""),
        status="completed",
        turn_id=str(report.get("turn_id") or ""),
    )
    detail["execution_id"] = context.execution_id
    detail["harness_input"] = dict(report)
    details.add(detail)


def _provider_event(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    project_provider_event(mapping(event.get("details")), context, details)


def _service_started(title: str) -> Callable[[dict[str, Any], EventContext, RuntimeDetails], None]:
    def project(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
        details.add(context.detail(
            kind="started", title=title,
            content=str(mapping(event.get("details")).get("service_type") or ""), status="running",
        ))

    return project


def _service_completed(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    details.add(context.detail(kind="completed", title="Completed", status="completed"))


def _harness_completed(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    card = harness_activity_card(mapping(event.get("details")), context)
    if card is not None:
        details.add(card)


# Portal's own Execution events by type; an event of any other type is a
# Provider event as it stands.
_EXECUTION_EVENTS = {
    "runtime_activity": _stored_activity,
    RECOVERY_INPUT_EVENT: _harness_input,
    "provider_event": _provider_event,
    "runtime_started": _service_started("Runtime started"),
    "service_started": _service_started("Execution service started"),
    "service_completed": _service_completed,
    "agent_harness_completed": _harness_completed,
}


def _in_scope(
    line_number: int, event: dict[str, Any], *, since_line: int, until_line: int, since: datetime | None,
) -> bool:
    if since_line and line_number <= since_line:
        return False
    if until_line and line_number >= until_line:
        return False
    if since:
        created = _parse_iso(str(event.get("timestamp") or event.get("created_at") or ""))
        if created and created <= since:
            return False
    return True


def runtime_details_from_objects(
    objects: Iterable[tuple[int, dict[str, Any]]],
    *,
    include_updates: bool = False,
    execution_id: str = "",
    since_line: int = 0,
    until_line: int = 0,
    since_iso: str = "",
    limit: int = DEFAULT_RUNTIME_DETAIL_LIMIT,
) -> list[dict[str, Any]]:
    """Normalize known Provider/Execution events without presentation truncation.

    Unknown event types and methods are intentionally ignored. This function
    never invents a generic activity row for an unrecognized event.
    """

    since = _parse_iso(since_iso)
    details = RuntimeDetails(include_updates=include_updates)
    for line_number, event in objects:
        if not isinstance(event, dict) or not _in_scope(
            line_number, event, since_line=since_line, until_line=until_line, since=since,
        ):
            continue
        context = EventContext(
            line_number=line_number,
            created_at=str(event.get("timestamp") or event.get("created_at") or ""),
            execution_id=str(event.get("execution_id") or execution_id or "").strip(),
        )
        project = _EXECUTION_EVENTS.get(str(event.get("type") or ""), project_provider_event)
        project(event, context, details)
    shown = details.rows()
    return last_runtime_details(shown, limit) if limit and limit > 0 else shown


def _keeps_place(detail: dict[str, Any]) -> bool:
    """A running subagent or native plan stays in view outside the recent window."""

    if detail.get("kind") == "subagent":
        return detail.get("status") == "running"
    return (
        detail.get("kind") == "plan" and detail.get("status") == "running"
        and isinstance(detail.get("native_plan"), dict)
    )


def last_runtime_details(details: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep running cards in view even when tool activity fills the recent window."""

    return [detail for detail in details[:-limit] if _keeps_place(detail)] + details[-limit:]


def _execution_output(detail: dict[str, Any], *, execution_id: str = "") -> dict[str, Any] | None:
    """The message a runtime detail carries; ``None`` for activity."""

    execution_id = str(detail.get("execution_id") or execution_id or "")
    kind = str(detail.get("kind") or "").strip().lower()
    if kind == "harness_input":
        message = recovery_input_message(detail.get("harness_input") or {}, execution_id=execution_id)
        return {
            "output_id": message["message_id"],
            "role": "system",
            "created_at": message["created_at"],
            "text": detail["content"],
            "message": message,
            "execution_id": str(message.get("execution_id") or ""),
        } if message else None
    if kind != "update":
        return None
    content = str(detail.get("content") or "").strip()
    status = str(detail.get("status") or "").strip().lower()
    questions = detail.get("questions") or []
    parts = detail.get("content_parts") or []
    if (not content and not questions and not parts) or status in _RUNNING_STATUSES:
        return None
    detail_id = str(detail.get("id") or "").strip()
    phase = str(detail.get("message_phase") or "").strip()
    if not phase and status in {"commentary", "final_answer"}:
        phase = status
    provider_item = str(detail.get("provider_item_id") or "")
    if not provider_item and detail_id.startswith("runtime-update:"):
        provider_item = detail_id.removeprefix("runtime-update:")
    output_id = model_message_id(execution_id, provider_item, phase) if provider_item else derived_message_identity(
        "resident-execution-message",
        execution_id or "unknown",
        detail_id or str(nonnegative_int(detail.get("event_line"), 0)),
    )
    return {
        "output_id": output_id,
        "created_at": str(detail.get("created_at") or ""),
        "phase": phase or "commentary",
        "text": content,
        **({"questions": questions} if questions else {}),
        **({"content_parts": parts} if parts else {}),
        "execution_id": execution_id,
    }


def _messages_and_activity(
    details: list[dict[str, Any]], execution_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split runtime details into the messages they carry and the activity left."""

    outputs: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    for detail in details:
        output = _execution_output(detail, execution_id=execution_id)
        if output is None:
            activity.append(detail)
        else:
            outputs.append(output)
    return outputs, activity


def execution_outputs_from_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return completed agent text outputs carried by Execution events."""

    objects = _sequenced(events)
    if not objects:
        return []
    details = runtime_details_from_objects(objects, include_updates=True, limit=0)
    return _messages_and_activity(details, "")[0]


def execution_projection_from_path(
    path: Path,
    *,
    runtime_limit: int = DEFAULT_RUNTIME_DETAIL_LIMIT,
    message_limit: int = 0,
) -> dict[str, Any]:
    """Project one Execution transcript into independent message/activity lanes.

    Completed Agent messages remain available for the whole Execution and are
    never evicted by tool or reasoning activity. Runtime details retain only
    the bounded activity window plus any currently streaming Agent message.
    """

    execution_id = path.stem if _EXECUTION_ID.fullmatch(path.stem) else ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            details = runtime_details_from_objects(
                (
                    (line_number, obj)
                    for line_number, line in enumerate(stream, 1)
                    if (obj := _parse_json_object(line))
                ),
                include_updates=True,
                execution_id=execution_id,
                limit=0,
            )
    except OSError:
        return _empty_projection(execution_id)

    outputs, runtime_details = _messages_and_activity(details, execution_id)
    if message_limit and message_limit > 0:
        outputs = outputs[-message_limit:]
    if runtime_limit and runtime_limit > 0:
        runtime_details = last_runtime_details(runtime_details, runtime_limit)
    return {
        "execution_id": execution_id,
        "outputs": outputs,
        "runtime_details": runtime_details,
    }


class ExecutionActivityProjection:
    """Incrementally consume the normalized, append-only Execution activity log.

    One completion waiter owns this reader. A read is acknowledged only after
    its messages have been committed; failed batches remain available for retry.
    Recreating a waiter safely replays the durable file through Message identity
    validation, independently of the Scheduler's transient event window.
    """

    def __init__(
        self,
        execution_id: str,
        *,
        runtime_limit: int = DEFAULT_RUNTIME_DETAIL_LIMIT,
    ) -> None:
        self.execution_id = execution_id
        self.path = execution_activity_path(execution_id)
        self.runtime_limit = max(1, runtime_limit)
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._line_number = 0
        self._runtime_details: list[dict[str, Any]] = []
        self._pending: dict[str, Any] | None = None
        self._pending_position: tuple[tuple[int, int], int, int] | None = None

    def read(self) -> dict[str, Any]:
        if self._pending is not None:
            return self._pending
        empty = {
            "execution_id": self.execution_id,
            "outputs": [],
            "runtime_details": self._runtime_details,
        }
        if self.path is None:
            return empty
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                reset = identity != self._identity or stat.st_size < self._offset
                offset = 0 if reset else self._offset
                line_number = 0 if reset else self._line_number
                stream.seek(offset)
                objects = []
                while stream.tell() < stat.st_size:
                    line = stream.readline(stat.st_size - stream.tell())
                    if not line.endswith(b"\n"):
                        # An append in progress is retried from its start.
                        break
                    offset = stream.tell()
                    line_number += 1
                    obj = _parse_json_object(line.decode("utf-8", errors="replace"))
                    if obj:
                        objects.append((line_number, obj))
        except OSError:
            return empty

        details = runtime_details_from_objects(
            objects, include_updates=True, execution_id=self.execution_id, limit=0,
        ) if objects else []
        outputs, activity = _messages_and_activity(details, self.execution_id)
        runtime = {} if reset else {
            self._runtime_identity(detail): detail for detail in self._runtime_details
        }
        for detail in activity:
            key = self._runtime_identity(detail)
            if detail.get("kind") == "subagent":
                # A subagent's latest activity is the newest one.
                runtime.pop(key, None)
            runtime[key] = detail
        self._pending_position = (identity, offset, line_number)
        self._pending = {
            "execution_id": self.execution_id,
            "outputs": outputs,
            "runtime_details": last_runtime_details(list(runtime.values()), self.runtime_limit),
        }
        return self._pending

    @staticmethod
    def _runtime_identity(detail: dict[str, Any]) -> tuple[str, str]:
        identity = str(detail.get("id") or "")
        return identity, (
            "" if identity.startswith("runtime-update:")
            else str(detail.get("created_at") or "")
        )

    def acknowledge(self) -> None:
        if self._pending is None or self._pending_position is None:
            return
        self._identity, self._offset, self._line_number = self._pending_position
        self._runtime_details = self._pending["runtime_details"]
        self._pending = None
        self._pending_position = None


def execution_projection_from_execution(
    execution_id: str,
    *,
    runtime_limit: int = DEFAULT_RUNTIME_DETAIL_LIMIT,
    message_limit: int = 0,
) -> dict[str, Any]:
    path = execution_activity_path(execution_id)
    if path is None:
        return _empty_projection(str(execution_id or ""))
    return execution_projection_from_path(
        path,
        runtime_limit=runtime_limit,
        message_limit=message_limit,
    )


def _activity_record(sequence: int, detail: dict[str, Any]) -> dict[str, Any] | None:
    """The stored record of one detail; ``None`` for a token snapshot or an unfinished update."""

    kind = str(detail.get("kind") or "").strip()
    status = str(detail.get("status") or "").strip().lower()
    if kind == "tokens" or (kind in {"update", "reasoning"} and (not status or status in _RUNNING_STATUSES)):
        return None
    return {
        "type": "runtime_activity",
        "sequence": sequence,
        "created_at": str(detail.get("created_at") or ""),
        "activity": {
            key: detail[key]
            for key in _STORED_ACTIVITY_FIELDS
            if detail.get(key) not in (None, "")
        },
    }


def _written_activity(stream: Any) -> tuple[int, set[tuple[str, str]]]:
    """The last sequence and the identities of the records an activity log holds."""

    last_sequence = 0
    identities: set[tuple[str, str]] = set()
    stream.seek(0)
    for line in stream:
        try:
            existing = json.loads(line)
        except (TypeError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(existing, dict):
            last_sequence = max(last_sequence, nonnegative_int(existing.get("sequence")))
        identity = activity_identity(existing)
        if identity is not None:
            identities.add(identity)
    return last_sequence, identities


def append_execution_activity(
    path: Path,
    events: Iterable[dict[str, Any]],
) -> int:
    """Persist semantic activity with retention independent of page display limits."""

    objects = _sequenced(events)
    if not objects:
        return 0
    # Scheduler sequences are stable across repeated delivery. Keep each
    # record's source sequence so a partial batch can be resumed safely.
    details = [
        (number if "sequence" in event else 0, detail)
        for number, event in objects
        for detail in runtime_details_from_objects([(number, event)], include_updates=True, limit=0)
    ]
    records: list[tuple[int, tuple[str, str] | None, bytes]] = []
    pending_identities: set[tuple[str, str]] = set()
    for sequence, detail in details:
        record = _activity_record(sequence, detail)
        if record is None:
            continue
        identity = activity_identity(record)
        if identity is not None:
            if identity in pending_identities:
                continue
            pending_identities.add(identity)
        records.append((
            sequence, identity,
            (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"),
        ))
    if not records:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a+b") as stream:
        with lock_stream(stream):
            stat = os.fstat(stream.fileno())
            signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
            with _ACTIVITY_WRITE_LOCK:
                cached = _ACTIVITY_WRITES.pop(path, None)
            if cached is not None and cached[0] == signature:
                _, last_sequence, existing_identities = cached
            else:
                last_sequence, existing_identities = _written_activity(stream)
            stream.seek(0, 2)
            size = stream.tell()
            batch_start_sequence = last_sequence
            for sequence, identity, record in records:
                if sequence and sequence <= batch_start_sequence:
                    continue
                if identity is not None and identity in existing_identities:
                    continue
                if size + len(record) > MAX_ACTIVITY_TRACE_BYTES:
                    break
                stream.write(record)
                size += len(record)
                written += 1
                last_sequence = max(last_sequence, sequence)
                if identity is not None:
                    existing_identities.add(identity)
            stream.flush()
            stat = os.fstat(stream.fileno())
            with _ACTIVITY_WRITE_LOCK:
                _ACTIVITY_WRITES[path] = (
                    (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size),
                    last_sequence, existing_identities,
                )
                while len(_ACTIVITY_WRITES) > 128:
                    _ACTIVITY_WRITES.popitem(last=False)
    return written


def execution_activity_path(
    execution_id: str,
) -> Path | None:
    clean_execution_id = str(execution_id or "").strip()
    if not _EXECUTION_ID.fullmatch(clean_execution_id):
        return None
    return (
        execution_state_root()
        / "activity"
        / f"{clean_execution_id}.jsonl"
    )


def prune_execution_activity(
    *,
    older_than_epoch: float,
    time_budget_seconds: float = 2.0,
) -> int:
    probe = execution_activity_path("execution-" + "0" * 24)
    if probe is None:
        raise RuntimeError("execution activity root is unavailable")
    root = probe.parent
    removed = 0
    deadline = time.monotonic() + max(0.01, float(time_budget_seconds))
    with _ACTIVITY_PRUNE_LOCK:
        entries = _ACTIVITY_PRUNE_SCANS.get(root)
        if entries is None:
            try:
                entries = os.scandir(root)
            except FileNotFoundError:
                return 0
            _ACTIVITY_PRUNE_SCANS[root] = entries
        while time.monotonic() < deadline:
            try:
                entry = next(entries)
            except StopIteration:
                entries.close()
                _ACTIVITY_PRUNE_SCANS.pop(root, None)
                break
            if not re.fullmatch(r"execution-[0-9a-f]{24}\.jsonl", entry.name):
                continue
            try:
                if entry.is_file(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < older_than_epoch:
                    os.unlink(entry.path)
                    removed += 1
            except FileNotFoundError:
                continue
    return removed
