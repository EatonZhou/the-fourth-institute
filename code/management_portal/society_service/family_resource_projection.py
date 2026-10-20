"""Reconcile Family descriptors from the canonical domain; retain no roster cache."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict


class FamilyResourceProjectionWorker:
    def __init__(self, *, poll_seconds: float = 60):
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread = None

    def reconcile(self) -> int:
        from . import family_storage, resource_client
        from .state_store import get_lineage_data_store
        with get_lineage_data_store().database.snapshot() as connection:
            records = family_storage.resource_records(connection)
        groups = defaultdict(list)
        # Only live Families are repaired here. A deletion is published once,
        # with its Unfamilize or DeleteLineage commit, and retried there.
        # ResourceService refuses a later tombstone for the same deletion whose
        # content differs, so replaying them would fail forever once the
        # domain's facts about a deleted Family change.
        for record in records:
            if not record['deleted_at']:
                groups[record['lineage_id']].append(record)
        count = 0
        for records in groups.values():
            try:
                resource_client._sync_many([resource_client._family_resource_projection(record) for record in records])
                count += len(records)
            except Exception:
                logging.getLogger(__name__).exception('Family resource reconciliation failed for %s', records[0]['lineage_id'])
        return count

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='family-resource-projection', daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.reconcile()
            except Exception:
                logging.getLogger(__name__).exception('Family resource reconciliation unavailable')
            if self._stop.wait(self._poll_seconds):
                return

    def stop(self, timeout=15):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
