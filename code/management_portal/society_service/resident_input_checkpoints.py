"""Input retry checkpoints; Agent execution_inputs owns execution history.

Original preparation is retained at admission, before projection can fail.
The newest ``MAX_CHECKPOINTS`` rounds of a Resident keep theirs;
``resident_input_recovery`` puts a checkpointed round back in the queue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from . import resident_conversation_stream, resident_execution_work, resident_lineage
from .state_store import get_lineage_data_store

MAX_CHECKPOINTS = 10
FINISHED_STATES = frozenset({"completed", "failed", "cancelled"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _address(record: Mapping[str, Any]) -> tuple[str, str]:
    return resident_lineage.lineage_id(record), resident_lineage.resident_id(record)


def checkpoints(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The checkpoints that can still go back to the queue, oldest first."""

    try:
        lineage_id, resident_id = _address(record)
    except (KeyError, ValueError):
        return []
    if not lineage_id:
        return []
    state = get_lineage_data_store().resident_mailbox_progress.get(lineage_id, resident_id) or {}
    return [dict(item) for item in state.get("checkpoints") or [] if isinstance(item, dict)]


def queue_item(job: Mapping[str, Any]) -> dict[str, Any]:
    """The original input, without Execution preparation fields."""

    from .resident_execution_storage import queue_input
    return queue_input(job)


def retain_input_checkpoint(record: Mapping[str, Any], job: Mapping[str, Any], carried: list[Mapping[str, Any]]) -> None:
    """Retain the input needed for requeue and attach its infos to the Execution.

    Only inputs that someone sent are checkpoints: a /compact, a Fork's preparation,
    a project creation and an isolated authorized call are not.
    """

    from . import resident_input_scope

    message_id = str(job.get("user_message_id") or "").strip()
    if not message_id or str(job.get("operation") or "") or resident_execution_work.is_compact(job):
        return
    lineage_id, resident_id = _address(record)
    if not lineage_id or resident_input_scope.get(resident_id, message_id):
        return
    expected_execution_id = str(job.get("execution_id") or "")
    durable = resident_execution_work.read(record)
    # Stored preparation keeps the item as it was queued; the argument may
    # carry a prompt rebuilt for this delivery.
    source = durable if expected_execution_id and str(durable.get("execution_id") or "") == expected_execution_id else job
    repository = resident_conversation_stream.get_repository()
    completed: set[str] = set()
    for entry in checkpoints(record):
        if "item" not in entry:
            continue
        try:
            if repository.input_state(resident_id, entry.get("message_id"))["state"] == "completed":
                completed.add(str(entry.get("message_id") or ""))
        except KeyError:
            completed.add(str(entry.get("message_id") or ""))
    entry = {
        "message_id": message_id,
        "execution_id": expected_execution_id,
        "kind": "group" if source.get("mailbox_members") else "request",
        "started_at": _now(),
        "infos": [
            {"message_id": str(item.get("message_id") or ""), "author_id": str(item.get("author_id") or ""),
             "held_at": str(item.get("held_at") or "")}
            for item in carried if item.get("message_id")
        ],
        "item": queue_item(source),
    }
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.execution_inputs import attach
    if carried:
        attach(get_agent_data_store(), expected_execution_id, [
            {"message_id":item["message_id"],"role":"info","accepted_at":item.get("held_at") or entry["started_at"]}
            for item in carried if item.get("message_id")
        ])
    store = get_lineage_data_store()
    with store.resident_mailbox_progress.transaction(lineage_id, resident_id) as state:
        kept = [item for item in state.get("checkpoints") or [] if isinstance(item, dict)]
        existing = next((item for item in kept if item.get("message_id") == message_id
                         and item.get("execution_id") == expected_execution_id), None)
        if existing is not None:
            # Reservation captured the original input before any projection or
            # prompt work. Later preparation may attach infos, not replace it.
            if carried:
                existing["infos"] = entry["infos"]
            return
        kept = [item for item in kept if item.get("message_id") != message_id]
        for item in kept:
            if item.get("message_id") in completed:
                item.pop("item", None)
        state["checkpoints"] = [*kept, entry][-MAX_CHECKPOINTS:]


def recent_executions(resident_id: str) -> list[dict[str, Any]]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.execution_inputs import recent
    return list(reversed(recent(get_agent_data_store(),resident_id,limit=MAX_CHECKPOINTS)))


def forget(record: Mapping[str, Any], message_id: str) -> None:
    """Drop the checkpoint of one input; requeue retires the previous round's this way."""
    lineage_id, resident_id = _address(record)
    with get_lineage_data_store().resident_mailbox_progress.transaction(lineage_id, resident_id) as state:
        state["checkpoints"] = [item for item in state.get("checkpoints") or []
                                if isinstance(item, dict) and item.get("message_id") != message_id]
