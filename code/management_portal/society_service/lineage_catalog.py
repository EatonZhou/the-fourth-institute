from __future__ import annotations

import copy
import threading
from typing import Any, Mapping

from . import lineage_definition_contract, lineage_state_domains
from .state_store import LineageDataStore, get_lineage_data_store
_CATALOGS: dict[str, "LineageCatalog"] = {}
_CATALOGS_LOCK = threading.Lock()


class LineageCatalog:
    """PostgreSQL-backed authority for canonical Lineage definitions."""

    def __init__(
        self,
        state_store: LineageDataStore,
    ) -> None:
        self.state_store = state_store

    @property
    def storage_identity(self) -> str:
        return self.state_store.database_identity

    def _normalize(
        self,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        return lineage_definition_contract.validate_definition(
            raw,
            path=self.state_store.database_identity,
        )

    def snapshot(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.state_store.all_lineages()]

    def listing_records(self) -> list[dict[str, Any]]:
        return self.state_store.lineage_listing_records()

    def listing_graph_summaries(
        self,
        lineage_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        return self.state_store.lineage_listing_graph_summaries(lineage_ids)

    # One Lineage's definition is rebuilt from its rows on every read and
    # shares nothing, so the caller already owns what it gets back.
    def get(self, lineage_id: str) -> dict[str, Any]:
        return self.state_store.lineage_definition(str(lineage_id or "").strip())


    def get_access_records(
        self,
        lineage_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return copy.deepcopy(
            self.state_store.lineage_access_records(lineage_ids)
        )


    def get_state(
        self,
        lineage_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return self.state_store.lineage_snapshot(str(lineage_id or "").strip())


    def lineage_ids_for_resident(self, resident_id: str) -> list[str]:
        target_id = str(resident_id or "").strip()
        if not target_id:
            return []
        return self.state_store.lineage_ids_for_resident(target_id)

    def program_resident_observers(
        self,
        resident_id: str,
        event_type: str,
    ) -> dict[str, list[str]]:
        """Event Sources, by Lineage, that observe ``resident_id``'s ``event_type``.

        A source observes the Resident through a Watch wire, or by covering
        all of Portal or its own Lineage (``config.resident_scope``) when the
        Resident is a member.
        """

        return self.state_store.program_resident_observers(resident_id, event_type)

    def upsert(self, definition: Mapping[str, Any]) -> None:
        normalized = self._normalize(definition)
        lineage_id = str(normalized["lineage_id"])
        self.state_store.put_lineage_definition(
            lineage_id,
            normalized,
            lineage_state_domains.persisted_runtime_state(normalized),
        )

    def remove(self, lineage_id: str, *, deleted_by: str) -> None:
        clean_id = str(lineage_id or "").strip()
        if clean_id:
            from . import lineage_effect_script_journal as journal
            from datetime import datetime, timezone
            def remove() -> None:
                journal.cancel_removed_effects(clean_id, None, reason="Lineage deleted",
                    occurred_at=datetime.now(timezone.utc).isoformat())
                self.state_store.remove_lineage(clean_id, deleted_by=deleted_by)
            self.state_store.transaction(remove)


    def put_runtime(self, lineage_id: str, value: Mapping[str, Any]) -> None:
        self.state_store.put_lineage_runtime(lineage_id, value)

    def put_runtime_cas(
        self,
        lineage_id: str,
        expected: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.state_store.put_lineage_runtime_cas(
            lineage_id,
            expected,
            candidate,
        )

    def update_layout(
        self,
        lineage_id: str,
        positions: list[Mapping[str, Any]],
        *,
        updated_at: str,
    ) -> dict[str, Any]:
        return self.state_store.upsert_layout_positions(
            lineage_id,
            positions,
            updated_at=updated_at,
        )

def get_catalog() -> LineageCatalog:
    state_store = get_lineage_data_store()
    database = state_store.database_identity
    key = database
    with _CATALOGS_LOCK:
        catalog = _CATALOGS.get(key)
        if catalog is None:
            catalog = LineageCatalog(state_store)
            _CATALOGS[key] = catalog
        return catalog
