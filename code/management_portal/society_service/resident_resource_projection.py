from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any

# A publication or confirmation that takes longer is reported with its timings.
SLOW_PROJECTION_SECONDS = 0.5


def _report_slow(label: str, seconds: float, **details: Any) -> None:
    if seconds < SLOW_PROJECTION_SECONDS:
        return
    print(
        f"SocietyService slow Resident Resource {label}: "
        + json.dumps({**details, "seconds": round(seconds, 3)}, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


class ResidentResourceProjectionWorker:
    """Deliver transactional Resident outbox snapshots to ResourceService."""

    def __init__(
        self,
        state_store: Any,
        resource_client: Any,
        *,
        poll_seconds: float = 0.2,
        batch_size: int = 50,
        lease_seconds: float = 90.0,
    ) -> None:
        self._state_store = state_store
        self._resource_client = resource_client
        self._poll_seconds = max(0.05, float(poll_seconds))
        self._batch_size = max(1, int(batch_size))
        self._lease_seconds = max(1.0, float(lease_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._projected_count = 0
        self._last_error = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="resident-resource-projection",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))

    def _record_error(self, error: BaseException | str) -> None:
        message = (
            str(error)
            if isinstance(error, str)
            else f"{error.__class__.__name__}: {error}"
        )
        with self._lock:
            self._last_error = message

    def _publish(self, claims: list[dict[str, Any]], *, origin: str = "worker") -> int:
        started = time.perf_counter()
        try:
            from .resident_lifecycle_domain import ensure_resident_agent
            for claim in claims:
                ensure_resident_agent(claim["payload"])
            self._resource_client.sync_resident_resource_bundles(
                [item["payload"] for item in claims],
                updated_by="society-service",
            )
            synced = time.perf_counter()
            receipt = self._state_store.complete_resident_resource_projections(
                claims
            )
            _report_slow(
                "publication", time.perf_counter() - started, origin=origin, bundles=len(claims),
                sync_seconds=round(synced - started, 3),
                complete_seconds=round(time.perf_counter() - synced, 3),
            )
        except Exception as exc:
            try:
                self._state_store.fail_resident_resource_projections(claims, exc)
            except Exception as release_exc:
                self._record_error(
                    f"{exc.__class__.__name__}: {exc}; "
                    f"claim release failed: {release_exc.__class__.__name__}: {release_exc}"
                )
            else:
                self._record_error(exc)
            raise
        from .changes import resident_changed

        for resident_id in receipt["ready_resident_ids"]:
            resident_changed(resident_id, scope="identity")
        completed = receipt["completed"]
        with self._lock:
            self._projected_count += completed
            self._last_error = ""
        return completed

    def drain_once(self) -> int:
        try:
            claims = self._state_store.claim_resident_resource_projections(
                limit=self._batch_size,
                lease_seconds=self._lease_seconds,
            )
            if claims:
                return self._publish(claims)
            with self._lock:
                self._last_error = ""
        except Exception as exc:
            self._record_error(exc)
        return 0

    def confirm(self, resident_id: str, *, timeout_seconds: float = 15.0) -> dict[str, Any]:
        """Confirm the existing identity's current outbox without recreating it."""
        from . import invariant_locks

        if invariant_locks.lock_held():
            raise RuntimeError("Resident Resource confirmation cannot hold state locks")
        started = time.monotonic()
        deadline = started + timeout_seconds
        waits = 0
        published = False
        while True:
            record = self._state_store.resident_record(resident_id)
            if record.get("deleted_at"):
                raise KeyError(resident_id)
            if not self._state_store.resident_resource_projection_pending(resident_id):
                # Recheck deletion after observing the publication acknowledgement.
                current = self._state_store.resident_record(resident_id)
                if current.get("deleted_at"):
                    raise KeyError(resident_id)
                if current.get("registration_state") == "resource_pending":
                    raise RuntimeError("Resident registration has no publication acknowledgement")
                from .resident_lifecycle_domain import ensure_resident_agent
                ensure_resident_agent(current)
                _report_slow(
                    "confirmation", time.monotonic() - started, resident_id=resident_id,
                    published_itself=published, lease_waits=waits,
                )
                return current
            if time.monotonic() >= deadline:
                _report_slow(
                    "confirmation timeout", time.monotonic() - started, resident_id=resident_id,
                    published_itself=published, lease_waits=waits,
                )
                raise TimeoutError("Resident Resource bundle confirmation is pending")
            claims = self._state_store.claim_resident_resource_projections(
                resident_id=resident_id, limit=1, lease_seconds=self._lease_seconds,
            )
            if claims:
                self._publish(claims, origin="confirm")
                published = True
            else:
                # An existing publisher owns the lease; do not send a second bundle.
                waits += 1
                time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))

    def confirm_tombstone(
        self,
        resident_id: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        """Confirm publication of one committed Resident deletion."""
        from . import invariant_locks

        if invariant_locks.lock_held():
            raise RuntimeError("Resident Resource confirmation cannot hold state locks")
        deadline = time.monotonic() + timeout_seconds
        while self._state_store.resident_resource_projection_pending(resident_id):
            if time.monotonic() >= deadline:
                raise TimeoutError("Resident Resource tombstone confirmation is pending")
            claims = self._state_store.claim_resident_resource_projections(
                resident_id=resident_id,
                limit=1,
                lease_seconds=self._lease_seconds,
            )
            if claims:
                if any(not item["payload"].get("deleted_at") for item in claims):
                    raise RuntimeError(
                        "Resident Resource deletion points to a non-tombstone projection"
                    )
                self._publish(claims)
            else:
                # An existing publisher owns the lease; wait for its acknowledgement.
                time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))

    def health(self) -> dict[str, Any]:
        pending = self._state_store.resident_resource_projection_health()
        with self._lock:
            projected_count = self._projected_count
            last_error = self._last_error or str(pending.get("last_error") or "")
        thread = self._thread
        return {
            **pending,
            "running": bool(thread and thread.is_alive()),
            "projected_count": projected_count,
            "last_error": last_error,
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                completed = self.drain_once()
            except Exception as exc:
                # A background projection is an eventually-consistent retry
                # loop. One transient store/client failure must not permanently
                # remove the worker from the SocietyService health contract.
                self._record_error(exc)
                completed = 0
            if not completed:
                self._stop_event.wait(self._poll_seconds)
