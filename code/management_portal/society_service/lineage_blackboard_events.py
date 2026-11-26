"""Recognize ``blackboard.entry_created``: one Event per entry written to a watched blackboard.

A blackboard keeps entries with a strictly increasing ``seq``.  An Event Source of this type
remembers the last ``seq`` it handled as its scalar runtime baseline.  The first look takes the
board's latest ``seq`` as the baseline (entries written before the source existed do not count);
each later pass reads the entries after it and turns every one into one Event whose payload names
the entry without its text: ``entry_id``, ``seq``, ``author_id``, ``kind``.

Entries are read outside the Lineage graph lock, like the other facts a pass gathers.  A source with
no subscribers produces no Event; it only keeps its baseline at the board's latest ``seq``, so a
subscriber connected later sees what is written from then on.  A write reaches the pass at once:
``CreateBoardEntry`` reports the Lineage as changed when one of its Event Sources observes boards.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Mapping

from . import blackboard_storage, lineage_primitives, lineage_program_observations

LOGGER = logging.getLogger(__name__)
EVENT = "blackboard.entry_created"
MAX_ENTRIES_PER_PASS = 200
_CURSOR_PREFIX = "blackboard_seq:"


def locator(board_id: str) -> str:
    return "blackboard_locator_" + hashlib.sha256(board_id.encode("utf-8")).hexdigest()


def cursor_value(seq: int) -> str:
    return f"{_CURSOR_PREFIX}{int(seq)}"


def cursor_signature(seq: int) -> str:
    return "sha256:" + hashlib.sha256(cursor_value(seq).encode("utf-8")).hexdigest()


def parse_cursor_value(value: object) -> int | None:
    text = str(value or "")
    if not text.startswith(_CURSOR_PREFIX):
        return None
    try:
        return max(0, int(text[len(_CURSOR_PREFIX):]))
    except ValueError:
        return None


def entry_sources(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(source_id): source
        for source_id, source in (plan.get("event_sources") or {}).items()
        if isinstance(source, dict)
        and str(source.get("event_type") or "") == EVENT
        and str(source.get("subject_blackboard_id") or "")
    }


def prefetch(lineage_id: str, plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per source: the baseline the reads started from, the latest ``seq`` and the entries after the baseline."""

    sources = entry_sources(plan)
    if not sources:
        return {}
    from . import lineage_program_runtime
    from .state_store import get_lineage_data_store

    runtime_sources = lineage_program_runtime.read_runtime(lineage_id).get("event_sources") or {}
    result: dict[str, dict[str, Any]] = {}
    with get_lineage_data_store().database.read() as connection:
        for source_id, source in sources.items():
            board_id = str(source["subject_blackboard_id"])
            board_locator = locator(board_id)
            stored = lineage_program_observations.observation_state(runtime_sources, source_id, "")
            base = (parse_cursor_value(stored.get("state_value"))
                    if str(stored.get("state_locator") or "") == board_locator else None)
            latest = blackboard_storage.latest_seq(connection, board_id)
            if base is None:
                result[source_id] = {"board_id": board_id, "locator": board_locator, "base_signature": None,
                                     "base_cursor": None, "cursor": latest, "entries": []}
                continue
            entries = blackboard_storage.entries_after(connection, board_id, base, MAX_ENTRIES_PER_PASS)
            result[source_id] = {
                "board_id": board_id, "locator": board_locator,
                "base_signature": str(stored.get("state_signature") or ""), "base_cursor": base,
                # With nothing left after the baseline (all cleaned up), it moves to the latest ``seq``.
                "cursor": entries[-1]["seq"] if entries else max(base, latest),
                "latest": latest, "entries": entries,
            }
    return result


def entry_event(source_id: str, board_id: str, entry: Mapping[str, Any]) -> lineage_primitives.LineageEvent:
    return lineage_primitives.make_event(
        EVENT,
        occurred_at=str(entry.get("created_at") or ""),
        payload={"board_id": board_id, "entry_id": entry["entry_id"], "seq": entry["seq"],
                 "author_id": entry["author_id"], "kind": entry.get("kind") or ""},
        correlation=f"{source_id}:{board_id}:{entry['seq']}",
    )


def has_entry_observers(connection: Any, lineage_id: str) -> bool:
    """Whether some Event Source of the Lineage observes blackboard entries."""

    row = connection.execute(
        "SELECT 1 AS present FROM lineage_nodes n JOIN lineage_components c USING(node_id) "
        "WHERE n.lineage_id=%s AND n.node_type='event_source' AND n.deleted_at IS NULL "
        "AND c.config_json::jsonb->>'event_type'=%s LIMIT 1", (lineage_id, EVENT)).fetchone()
    return row is not None


def notify_written(connection: Any, lineage_id: str) -> None:
    """Report the Lineage changed so its pass reads the new entry, when a source observes entries."""

    try:
        if not has_entry_observers(connection, lineage_id):
            return
        from .changes import lineage_changed

        lineage_changed(lineage_id, runtime=True)
    except Exception:  # the entry is committed; the periodic pass still finds it
        LOGGER.exception("Blackboard write notification failed for %s", lineage_id)
