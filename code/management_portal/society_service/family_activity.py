"""Family-wide display activity, independent of any facade request's lifecycle."""
from __future__ import annotations

from typing import Any, Mapping

from . import family_facade, family_storage, resident_conversation_stream, resident_lineage
from .state_store import get_lineage_data_store
from . import resident_conversation_records


class _FamilyRead:
    """One read's indexed rosters and records; no persistent or quota cache."""

    def __init__(self, resident_ids: list[str], *, records: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.store = get_lineage_data_store()
        self.records: dict[str, dict[str, Any]] = dict(records or {})
        self.rosters: dict[str, list[str]] = {}
        self.load_records(resident_ids)

    def load_records(self, resident_ids: list[str]) -> None:
        # The walk reads only whether a Resident is live and its Family role:
        # identity records, without resolving every member's Workspace paths.
        missing = [item for item in resident_ids if item not in self.records]
        if missing:
            self.records.update({item: {} for item in missing})
            self.records.update({resident_lineage.resident_id(record): record
                                 for record in self.store.resident_identity_records(missing)})

    def members(self, record: Mapping[str, Any]) -> list[str]:
        family_id = family_facade.facade_configuration(record).get("family_id")
        if not family_id:
            return []
        if family_id not in self.rosters:
            with self.store.database.read() as connection:
                roles = family_storage.roles_of(connection, family_id)
            self.rosters[family_id] = [*roles["member"], *roles["router"], *roles["steward"]]
        return self.rosters[family_id]

    def tree(self, facade_id: str) -> set[str]:
        found: set[str] = set()
        pending = [facade_id]
        while pending:
            self.load_records(pending)
            next_ids = []
            for resident_id in pending:
                if resident_id in found:
                    continue
                record = self.records[resident_id]
                if not record or record.get("deleted_at"):
                    continue
                found.add(resident_id)
                if family_facade.is_facade(record):
                    next_ids.extend(self.members(record))
            pending = list(set(next_ids) - found)
        return found


def project_states(
    snapshots: Mapping[str, dict[str, Any]],
    *, resident_records: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Attach display counts without changing work state, input IDs or controls.

    Read member states in one batch, reusing states the caller already read.
    Nested Family facades contribute their waiting requests, while executing
    Residents are counted once. Trashed Residents never contribute.
    """
    result = dict(snapshots)
    view = _FamilyRead(list(snapshots), records=resident_records)
    trees = {resident_id: view.tree(resident_id) for resident_id in snapshots
             if family_facade.is_facade(view.records.get(resident_id))}
    if not trees:
        return result
    missing = set().union(*trees.values()) - snapshots.keys()
    states = {**resident_conversation_stream.get_repository().state_snapshots(sorted(missing)), **snapshots}
    for facade_id, participants in trees.items():
        running = queued = 0
        for resident_id in participants:
            state = (states.get(resident_id) or {}).get("state") or {}
            work_state = state.get("work_state")
            if not family_facade.is_facade(view.records[resident_id]):
                running += work_state in {"running", "stopping"}
            queued += max(int(state.get("queued_count") or 0), int(work_state == "queued"))
        activity = {"work_state": "running" if running else "queued" if queued else "idle",
                    "running_count": running, "queued_count": queued}
        snapshot = snapshots[facade_id]
        result[facade_id] = {**snapshot, "state": {**(snapshot.get("state") or {}), "family_activity": activity}}
    return result


def projection_targets(event: Mapping[str, Any]) -> list[str]:
    """Wake facade viewers for any participant's work/queue transition."""
    if event.get("event_type") not in {resident_conversation_records.STATE_CHANGED,
                                      resident_conversation_records.QUEUE_CHANGED}:
        return []
    resident_id = str(event.get("resident_id") or "")
    if not resident_id:
        return []
    with get_lineage_data_store().database.read() as connection:
        return sorted({item["facade_id"] for item in family_storage.families_around(
            connection, resident_id, max_hops=family_facade.MAX_RELAY_HOPS,
        ) if item["facade_id"] != resident_id})
