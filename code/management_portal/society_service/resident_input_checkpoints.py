"""Input retry checkpoints; Agent execution_inputs owns execution history.

``resident_execution_work.retain_input_checkpoint`` keeps an input's original
preparation at admission, before projection can fail.  The newest
``MAX_CHECKPOINTS`` rounds of a Resident keep theirs; ``resident_input_recovery``
puts a checkpointed round back in the queue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from . import resident_lineage
from .state_store import get_lineage_data_store

MAX_CHECKPOINTS = 10
FINISHED_STATES = frozenset({"completed", "failed", "cancelled"})


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def address(record: Mapping[str, Any]) -> tuple[str, str]:
    return resident_lineage.lineage_id(record), resident_lineage.resident_id(record)


def checkpoints(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The checkpoints that can still go back to the queue, oldest first."""

    try:
        lineage_id, resident_id = address(record)
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


def recent_executions(resident_id: str) -> list[dict[str, Any]]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.execution_inputs import recent
    return list(reversed(recent(get_agent_data_store(),resident_id,limit=MAX_CHECKPOINTS)))


def forget(record: Mapping[str, Any], message_id: str) -> None:
    """Drop the checkpoint of one input; requeue retires the previous round's this way."""
    lineage_id, resident_id = address(record)
    with get_lineage_data_store().resident_mailbox_progress.transaction(lineage_id, resident_id) as state:
        state["checkpoints"] = [item for item in state.get("checkpoints") or []
                                if isinstance(item, dict) and item.get("message_id") != message_id]
