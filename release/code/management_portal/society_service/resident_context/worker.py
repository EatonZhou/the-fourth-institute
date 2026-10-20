"""Mechanical Resident discovery work inside SocietyService.

Dev and Release share this data, so a lease in it lets one SocietyService at
a time sync the catalog, detect conversation changes, prepare summary
evidence and compute embeddings; another takes over when it stops.  No AI
runs here: summaries are written by the ``resident-summary`` duty holder.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any

from .service import ResidentContext, resident_context

LEASE_TTL_SECONDS = 90.0
_LOG = logging.getLogger(__name__)


class ResidentContextWorker:
    def __init__(self, context: ResidentContext | None = None, *, holder: str = "") -> None:
        self._context = context
        instance = str(os.getenv("PORTAL_INSTANCE", "dev") or "dev")
        self.holder = holder or f"{instance}:{socket.gethostname()}:{os.getpid()}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def context(self) -> ResidentContext:
        if self._context is None:
            self._context = resident_context()
        return self._context

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="resident-context-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                return  # A running worker must not release its lease early.
            self._thread = None
        try:
            self.context.store.release_worker_lease(self.holder)
        except Exception:  # noqa: BLE001 - the lease expires by itself
            pass

    def _run(self) -> None:
        holding = False
        last_lease = last_catalog = last_reconcile = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if not holding or now - last_lease >= LEASE_TTL_SECONDS / 3:
                    was_holding = holding
                    holding = self.context.store.hold_worker_lease(self.holder, ttl_seconds=LEASE_TTL_SECONDS)
                    last_lease = now
                    if holding and not was_holding:
                        self._take_over()
                        last_catalog = last_reconcile = 0.0
                if not holding:
                    self._stop.wait(LEASE_TTL_SECONDS / 3)
                    continue
                busy = self.run_once(
                    sync_catalog=now - last_catalog >= self.context.settings.catalog_sync_seconds,
                    reconcile=now - last_reconcile >= self.context.settings.reconcile_seconds,
                )
                if busy.get("catalog"):
                    last_catalog = now
                if busy.get("reconcile"):
                    last_reconcile = now
                self._stop.wait(0.2 if busy.get("work") else 2.0)
            except Exception:  # noqa: BLE001 - the worker keeps running; PostgreSQL may come back
                _LOG.warning("Resident discovery worker step failed", exc_info=True)
                self._stop.wait(10.0)

    def _take_over(self) -> None:
        store = self.context.store
        store.recover_running_embeddings()
        store.recover_expired_leases()
        store.discard_inactive_jobs()
        from .evidence import PROMPT_VERSION

        store.enqueue_summary_backfill(PROMPT_VERSION)
        if self.context.embeddings_available:
            store.enqueue_embedding_backfill(self.context.embedder.model_version)

    def run_once(self, *, sync_catalog: bool = False, reconcile: bool = False) -> dict[str, Any]:
        """One pass; returns which periodic steps ran and whether queued work was done."""

        context = self.context
        ran: dict[str, Any] = {}
        if sync_catalog:
            try:
                context.sync_catalog()
            except Exception:  # noqa: BLE001 - keep the last known catalog
                _LOG.warning("Resident discovery catalog sync failed", exc_info=True)
            ran["catalog"] = True
        if reconcile:
            context.reconcile()
            context.store.recover_expired_leases()
            ran["reconcile"] = True
        prepared = context.prepare_due()
        embedded = context.process_one_embedding()
        ran["work"] = bool(prepared or embedded)
        return ran


_WORKER: ResidentContextWorker | None = None


def start_worker() -> ResidentContextWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = ResidentContextWorker()
        _WORKER.start()
    return _WORKER


def stop_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        _WORKER.stop()
        _WORKER = None
