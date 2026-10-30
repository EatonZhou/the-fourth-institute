"""The adapter layer: queues adapter refreshes, finds the sources that changed and
applies the adapters' scans to the store."""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from management_common.scan_cancellation import ScanCancelled, cancellable_scan, check_scan_cancelled
from management_portal.resources import timing
from management_common.text_values import stripped_text
from management_portal.resources.registry_store import parse_registry_change_key
from management_portal.notifications.file_watch import RecursiveFileChangeWatcher

from management_portal.resource_index_adapters import (
    _routing_paths, _known_paths, ResourceAdapter, _adapter_projection_code_fingerprint, _watch_directories,
)
from management_portal.resource_index_records import _now_iso, _dedupe, ChangeSet
from management_portal.resource_index_store import IndexStore


RESOURCE_RECONCILE_SECONDS = max(
    300,
    int(
        os.getenv("PORTAL_RESOURCE_INDEX_RECONCILE_SECONDS", "300")
        or "300"
    ),
)
WATCH_FILESYSTEM = os.getenv("PORTAL_RESOURCE_INDEX_WATCH", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
LOGGER = logging.getLogger(__name__)
SLOW_REFRESH_SECONDS = 5.0
# A change is looked for from this long before the previous check started, so
# a file written just as that check began is not missed.
SOURCE_CHANGE_MARGIN_SECONDS = 2.0
SOURCE_CHANGE_TIMEOUT_SECONDS = 900.0
_SOURCE_CHANGE_FINDER = Path(__file__).with_name("resource_source_changes.py")


def _find_source_changes(request: dict[str, Any]) -> dict[str, Any]:
    """Run resource_source_changes.py on ``request`` in a child process."""
    check_scan_cancelled()
    command = [sys.executable, "-I", str(_SOURCE_CHANGE_FINDER)]
    # The request lists every indexed source (4,000 papers is about 300 KB),
    # more than a pipe holds. communicate() writes input only on its first
    # call, so a child that started reading after that call's 0.1 s never got
    # the rest and the scan hung until the timeout. A file needs no writer.
    with tempfile.TemporaryFile() as request_file:
        request_file.write(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        request_file.seek(0)
        with subprocess.Popen(
            command, stdin=request_file, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            deadline = time.monotonic() + SOURCE_CHANGE_TIMEOUT_SECONDS
            try:
                while True:
                    check_scan_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, SOURCE_CHANGE_TIMEOUT_SECONDS)
                    try:
                        stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                process.kill()
                process.communicate()
                raise
    check_scan_cancelled()
    if process.returncode != 0:
        raise RuntimeError(
            "resource source change finder failed: "
            + stderr.decode("utf-8", errors="replace")[-1000:]
        )
    return json.loads(stdout)


@dataclass
class _RefreshCompletion:
    done: bool = False
    ok: bool = False


@dataclass
class _PendingRefresh:
    force: bool = False
    # A full refresh someone forced (overflow, the refresh API), as opposed to
    # a changed path that joined a pending check.
    rescan: bool = False
    full: bool = False
    paths: set[str] = field(default_factory=set)
    queued_at: float = 0.0
    completion: _RefreshCompletion = field(default_factory=_RefreshCompletion)


class AdapterLayer:
    """Apply explicit source changes to the persisted resource projection."""

    def __init__(
        self,
        store: IndexStore,
        adapters: Iterable[ResourceAdapter],
        *,
        start_worker: bool = True,
        on_projection_changed: Callable[[str, dict[str, Any]], None] | None = None,
        reconcile_seconds: float = RESOURCE_RECONCILE_SECONDS,
        merge_seconds: float = 0.1,
    ) -> None:
        self.store = store
        self.adapters = {adapter.name: adapter for adapter in adapters}
        self._on_projection_changed = on_projection_changed
        self._reconcile_seconds = max(0.05, float(reconcile_seconds))
        self._merge_seconds = max(0.0, float(merge_seconds))
        self._queue: queue.Queue[str] = queue.Queue()
        self._condition = threading.Condition(threading.RLock())
        self._pending: dict[str, _PendingRefresh] = {}
        self._latest_refresh: dict[str, _RefreshCompletion] = {}
        self._running: set[str] = set()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._file_detector: RecursiveFileChangeWatcher | None = None
        # Real paths of each adapter's sources from its last change check;
        # a changed-file update reuses them instead of resolving every record.
        self._source_resolutions: dict[str, dict[str, Path]] = {}
        if start_worker:
            if WATCH_FILESYSTEM:
                directories = _watch_directories({
                    path
                    for adapter in self.adapters.values()
                    for path in (
                        adapter.watch_directories()
                        if callable(getattr(adapter, "watch_directories", None))
                        else []
                    )
                })
                self._file_detector = RecursiveFileChangeWatcher(
                    directories,
                    self._handle_file_change,
                    self._handle_file_overflow,
                    thread_name="portal-resource-index-files",
                    recursive=False,
                    track_new_directories=True,
                )
                self._file_detector.start()
            self._worker = threading.Thread(
                target=self._run,
                name="portal-resource-index",
                daemon=True,
            )
            self._worker.start()

    def _handle_file_change(self, path: str) -> None:
        if not self._stop_event.is_set():
            self.notify_change(path=path)

    def _handle_file_overflow(self) -> None:
        if not self._stop_event.is_set():
            self.request_refresh(force=True)

    def _completed_refresh(
        self,
        adapter: str,
        result: dict[str, Any],
        *,
        forced: bool,
        emit: bool,
    ) -> dict[str, Any]:
        if self._stop_event.is_set():
            return result
        if result.get("scanned") and not result.get("incremental"):
            self._sync_file_watches()
        callback = self._on_projection_changed
        if emit and callback is not None and (forced or bool(result.get("changed"))):
            try:
                callback(adapter, dict(result))
            except Exception:
                LOGGER.exception(
                    "resource projection committed but notification failed: %s",
                    adapter,
                )
        return result

    def _sync_file_watches(self) -> None:
        detector = self._file_detector
        if detector is None:
            return
        directories = _watch_directories({
            path
            for adapter in self.adapters.values()
            for path in (
                adapter.watch_directories()
                if callable(getattr(adapter, "watch_directories", None))
                else []
            )
        })
        detector.add_directories(directories)

    def stop(self, *, timeout: float = 180.0) -> None:
        with self._condition:
            self._stop_event.set()
            self._pending.clear()
            self._condition.notify_all()
        self._queue.put("")
        if self._file_detector is not None:
            self._file_detector.stop()
        if self._worker is not None:
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                raise RuntimeError("resource index worker did not stop before store flush")
        self.store.flush()

    def request_refresh(
        self,
        adapters: Iterable[str] = (),
        *,
        force: bool = False,
        paths: Iterable[str] = (),
    ) -> list[str]:
        names = list(adapters) or list(self.adapters)
        unknown = sorted(set(names) - set(self.adapters))
        if unknown:
            raise ValueError(f"unknown resource index adapter: {', '.join(unknown)}")
        clean_paths = {stripped_text(path) for path in paths if stripped_text(path)}
        rescan = bool(force) and not clean_paths
        queued: list[str] = []
        with self._condition:
            if self._stop_event.is_set():
                return []
            for name in names:
                previous = self._pending.get(name)
                if previous is None:
                    self._pending[name] = _PendingRefresh(
                        force=bool(force),
                        rescan=rescan,
                        full=not clean_paths,
                        paths=set(clean_paths),
                        queued_at=time.monotonic(),
                    )
                    self._latest_refresh[name] = self._pending[name].completion
                    self._queue.put(name)
                    queued.append(name)
                else:
                    previous.force = previous.force or bool(force)
                    previous.rescan = previous.rescan or rescan
                    previous.full = previous.full or not clean_paths
                    previous.paths.update(clean_paths)
            self._condition.notify_all()
        return queued

    def matching_adapters(self, *, path: str = "", adapters: Iterable[str] = ()) -> list[str]:
        names = [stripped_text(value) for value in adapters if stripped_text(value)]
        registry_change = parse_registry_change_key(path)
        if registry_change is not None:
            names.extend(
                name for name, adapter in self.adapters.items()
                if registry_change[0] in getattr(adapter, "registry_collections", ())
            )
        elif path:
            resolved = Path(path).expanduser().resolve(strict=False)
            token = _routing_paths.set({str(path): resolved, str(resolved): resolved})
            try:
                names.extend(
                    name for name, adapter in self.adapters.items() if adapter.owns_path(resolved)
                )
            finally:
                _routing_paths.reset(token)
        return _dedupe(names)

    def notify_change(self, *, path: str = "", adapters: Iterable[str] = ()) -> list[str]:
        matched = self.matching_adapters(path=path, adapters=adapters)
        return self.request_refresh(
            matched,
            force=True,
            paths=[path] if path else [],
        ) if matched else []

    def project_registered(
        self, resource_type: str, record: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish one committed registration without waiting for the scan queue."""
        resource_id = stripped_text(record.get("resource_id"))
        owners = [adapter for adapter in self.adapters.values()
                  if resource_type in adapter.resource_types]
        if len(owners) != 1:
            raise ValueError(f"registration has no unique index adapter: {resource_type}")
        adapter = owners[0]
        projector = getattr(adapter, "project_record", None)
        if callable(projector):
            summary = projector(resource_type, record)
        else:
            path = stripped_text(record.get("path"))
            scan_changes = getattr(adapter, "scan_changes", None)
            if not path or not callable(scan_changes):
                raise ValueError(f"adapter {adapter.name} cannot project one registration")
            changes = scan_changes(
                (path,), self.store.records_for_adapter(adapter.name),
                self.store.adapter_source_state(adapter.name),
            )
            matches = [item for item in changes.upserts
                       if stripped_text(item.get("resource_id")) == resource_id]
            if len(matches) != 1:
                raise RuntimeError(f"adapter {adapter.name} did not find registered {resource_id}")
            summary = matches[0]
        if stripped_text(summary.get("resource_id")) != resource_id:
            raise ValueError("registration projection changed resource identity")
        result = self.store.apply(
            ChangeSet(adapter.name, upserts=(summary,),
                      source_state=self.store.adapter_source_state(adapter.name)),
            direct=True,
        )
        if resource_id not in result["upserted_ids"]:
            raise RuntimeError(f"registration projection rejected {resource_id}")
        self._completed_refresh(adapter.name, {
            **result, "changed": True, "scanned": False, "incremental": True,
        }, forced=True, emit=True)
        return result

    def project_unregistered(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        """Remove a committed identity before callers query the derived list.

        The direct revision also fences scans begun before the unregistration,
        including a scan whose first observation of this ID is still in flight.
        """
        owners = [adapter for adapter in self.adapters.values()
                  if resource_type in adapter.resource_types]
        if not owners:
            raise ValueError(f"unregistration has no index adapter: {resource_type}")
        try:
            record = self.store.internal_get(resource_id)
        except KeyError:
            record = None
        if record is not None:
            owner = record["source"]["adapter"]
            if record["resource_type"] != resource_type or owner not in {a.name for a in owners}:
                raise ValueError(f"unregistration index ownership mismatch: {resource_id}")
            # A type may have several sources (Knowledge and Paper reviews).
            # Delete through the recorded owner before fencing the other sources;
            # adapters must never delete a record belonging to another adapter.
            owners.sort(key=lambda adapter: adapter.name != owner)
        results = []
        for adapter in owners:
            result = self.store.apply(ChangeSet(
                adapter.name, deletes=(resource_id,),
                source_state=self.store.adapter_source_state(adapter.name),
            ), direct=True)
            self._completed_refresh(adapter.name, {
                **result, "changed": True, "scanned": False, "incremental": True,
            }, forced=True, emit=True)
            results.append(result)
        return {
            **results[0],
            "adapters": [adapter.name for adapter in owners],
            "deleted": sum(result["deleted"] for result in results),
            "deleted_ids": sorted({rid for result in results for rid in result["deleted_ids"]}),
        }

    def refresh_now(
        self,
        adapter_name: str,
        *,
        force: bool = False,
        paths: Iterable[str] = (),
        rescan: bool | None = None,
        joined_paths: Iterable[str] = (),
        _claimed: bool = False,
    ) -> dict[str, Any]:
        """Refresh one adapter.

        ``paths`` asks for an incremental update of those sources.  Without
        them the adapter is checked as a whole.  An adapter that tracks its
        source changes re-reads only the changed sources, unless ``rescan``
        (by default ``force``) asks for a full scan; ``joined_paths`` are
        changed paths that arrived while the check waited.  Other adapters
        scan when ``force`` is set or their fingerprint changed.
        """
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            raise ValueError(f"unknown resource index adapter: {adapter_name}")
        if not _claimed:
            direct_completion = self._claim_direct_refresh(adapter_name)
        succeeded = True
        started = time.perf_counter()
        scan_started_generation = self.store.generation()
        rescan = force if rescan is None else bool(rescan)
        joined = tuple(sorted({stripped_text(path) for path in joined_paths if stripped_text(path)}))
        try:
            check_scan_cancelled()
            clean_paths = tuple(sorted({stripped_text(path) for path in paths if stripped_text(path)}))
            incremental_scan = getattr(adapter, "scan_changes", None)
            if clean_paths and callable(incremental_scan):
                applied = self._refresh_paths(
                    adapter_name,
                    incremental_scan,
                    clean_paths,
                    started=started,
                    force=force,
                    claimed=_claimed,
                    scan_started_generation=scan_started_generation,
                )
                if applied is not None:
                    return applied
                # The adapter cannot apply these paths alone.
                incremental_failed = True
            else:
                incremental_failed = False
            previous_state = self.store.adapter_source_state(adapter_name)
            source_scope = getattr(adapter, "source_scope", None)
            tracks_changes = callable(source_scope) and callable(incremental_scan)
            check_started = time.time()
            if tracks_changes:
                if (
                    not rescan
                    and not incremental_failed
                    and self.store.adapter_has_snapshot(adapter_name)
                ):
                    checked = self._refresh_changed_sources(
                        adapter_name,
                        adapter,
                        previous_state,
                        check_started,
                        joined,
                        started=started,
                        force=force,
                        claimed=_claimed,
                        scan_started_generation=scan_started_generation,
                    )
                    if checked is not None:
                        return checked
                scan_state = self._source_tracking_state(adapter, source_scope(), check_started)
            else:
                fingerprint = adapter.fingerprint()
                check_scan_cancelled()
                if (
                    not force
                    and self.store.adapter_has_snapshot(adapter_name)
                    and stripped_text(previous_state.get("fingerprint")) == fingerprint
                ):
                    self.store.mark_checked(
                        adapter_name,
                        {
                            **previous_state,
                            "fingerprint": fingerprint,
                            "projection_code_fingerprint": _adapter_projection_code_fingerprint(adapter),
                            "checked_at": _now_iso(),
                        },
                        persist=not _claimed,
                    )
                    return self._unchanged_refresh(adapter_name, started, force=force, claimed=_claimed)
                scan_state = {"fingerprint": fingerprint}
            return self._scan_and_publish(
                adapter_name,
                adapter,
                previous_state,
                scan_state,
                started=started,
                force=force,
                claimed=_claimed,
                scan_started_generation=scan_started_generation,
            )
        except Exception as exc:
            succeeded = False
            self.store.mark_error(
                adapter_name,
                exc,
                persist=not _claimed,
            )
            raise
        finally:
            if not _claimed:
                self._release_direct_refresh(adapter_name, direct_completion, succeeded)

    def _claim_direct_refresh(self, adapter_name: str) -> _RefreshCompletion:
        """Mark a refresh called outside the queue as running; its completion answers waiters."""
        with self._condition:
            if adapter_name in self._running:
                raise RuntimeError(f"adapter refresh already running: {adapter_name}")
            self._running.add(adapter_name)
            direct_completion = _RefreshCompletion()
            if adapter_name not in self._pending:
                self._latest_refresh[adapter_name] = direct_completion
        return direct_completion

    def _release_direct_refresh(
        self, adapter_name: str, direct_completion: _RefreshCompletion, succeeded: bool,
    ) -> None:
        with self._condition:
            direct_completion.ok = succeeded
            direct_completion.done = True
            self._running.discard(adapter_name)
            self._condition.notify_all()

    def _refresh_paths(
        self,
        adapter_name: str,
        incremental_scan: Callable[..., ChangeSet | None],
        paths: tuple[str, ...],
        *,
        started: float,
        force: bool,
        claimed: bool,
        scan_started_generation: int,
    ) -> dict[str, Any] | None:
        """Apply the changes of ``paths`` alone, or None when the adapter cannot."""
        previous_records = self.store.records_for_adapter(adapter_name)
        previous_state = self.store.adapter_source_state(adapter_name)
        token = _known_paths.set(self._source_resolutions.get(adapter_name))
        try:
            changes = self._scan_paths(
                adapter_name,
                incremental_scan,
                paths,
                previous_records,
                previous_state,
            )
        finally:
            _known_paths.reset(token)
        if changes is None:
            return None
        return self._apply_incremental(
            adapter_name,
            changes,
            paths,
            previous_records,
            previous_state,
            {"incremental_checked_at": _now_iso()},
            started=started,
            force=force,
            claimed=claimed,
            scan_started_generation=scan_started_generation,
        )

    def _scan_and_publish(
        self,
        adapter_name: str,
        adapter: ResourceAdapter,
        previous_state: dict[str, Any],
        scan_state: dict[str, Any],
        *,
        started: float,
        force: bool,
        claimed: bool,
        scan_started_generation: int,
    ) -> dict[str, Any]:
        """Scan the adapter as a whole and publish its records as its snapshot."""
        check_scan_cancelled()
        records, source_state = adapter.scan()
        check_scan_cancelled()
        projection_changed = bool(source_state.get("projection_signature")) and (
            stripped_text(source_state.get("projection_signature"))
            != stripped_text(previous_state.get("projection_signature"))
        )
        recover = getattr(adapter, "recover_snapshot", None)
        if callable(recover):
            records, source_state = recover(
                self.store.records_for_adapter(adapter_name),
                records,
                source_state,
            )
        check_scan_cancelled()
        published = self.store.publish_snapshot(
            adapter_name,
            records,
            {
                **source_state,
                **scan_state,
                "projection_code_fingerprint": _adapter_projection_code_fingerprint(adapter),
                "checked_at": _now_iso(),
            },
            persist=not claimed,
            scan_started_generation=scan_started_generation,
        )
        return self._completed_refresh(adapter_name, {
            **published,
            "changed": bool(
                published.get("upserted")
                or published.get("deleted")
                or projection_changed
            ),
            "scanned": True,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }, forced=force, emit=not claimed)

    def _unchanged_refresh(
        self,
        adapter_name: str,
        started: float,
        *,
        force: bool,
        claimed: bool,
    ) -> dict[str, Any]:
        return self._completed_refresh(adapter_name, {
            "adapter": adapter_name,
            "changed": False,
            "resource_count": int(
                (self.store.status().get("adapters", {}).get(adapter_name) or {}).get("resource_count") or 0
            ),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }, forced=force, emit=not claimed)

    def _scan_paths(
        self,
        adapter_name: str,
        incremental_scan: Callable[..., ChangeSet | None],
        paths: tuple[str, ...],
        previous_records: list[dict[str, Any]],
        previous_state: dict[str, Any],
    ) -> ChangeSet | None:
        try:
            return incremental_scan(paths, previous_records, previous_state)
        except Exception:
            # Each path is retried alone and a path that fails again becomes a
            # resource error; this says why the paths together failed.
            LOGGER.warning(
                "resource index %s: incremental scan of %d paths failed; retrying them one by one",
                adapter_name, len(paths), exc_info=True,
            )
            return self._retry_incremental_paths(
                adapter_name,
                incremental_scan,
                paths,
                previous_records,
                previous_state,
            )

    def _apply_incremental(
        self,
        adapter_name: str,
        changes: ChangeSet,
        paths: tuple[str, ...],
        previous_records: list[dict[str, Any]],
        previous_state: dict[str, Any],
        state: dict[str, Any],
        *,
        started: float,
        force: bool,
        claimed: bool,
        scan_started_generation: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(changes, ChangeSet) or changes.adapter != adapter_name:
            raise ValueError(f"adapter {adapter_name} returned an invalid incremental ChangeSet")
        previous_by_id = {
            record["resource_id"]: record for record in previous_records
        }
        changed_upserts = tuple(
            record
            for record in changes.upserts
            if previous_by_id.get(stripped_text(record.get("resource_id"))) != record
        )
        changed_deletes = tuple(
            resource_id
            for resource_id in changes.deletes
            if resource_id in previous_by_id
        )
        incremental_errors = self._incremental_resource_errors(
            previous_state,
            changes.source_state,
            paths,
        )
        check_scan_cancelled()
        published = self.store.apply(
            ChangeSet(
                adapter=adapter_name,
                upserts=changed_upserts,
                deletes=changed_deletes,
                source_state={
                    **previous_state,
                    **changes.source_state,
                    "resource_errors": incremental_errors,
                    **state,
                },
            ),
            persist=not claimed,
            scan_started_generation=scan_started_generation,
        )
        return self._completed_refresh(adapter_name, {
            **published,
            "changed": bool(changed_upserts or changed_deletes),
            "scanned": True,
            "incremental": True,
            "path_count": len(paths),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }, forced=force, emit=not claimed)

    @staticmethod
    def _source_tracking_state(
        adapter: ResourceAdapter,
        scope: dict[str, Any],
        check_started: float,
    ) -> dict[str, Any]:
        """What a change check compares against next time, taken before scanning."""
        return {
            "sources_since": check_started,
            "projection_code_fingerprint": _adapter_projection_code_fingerprint(adapter),
            "registry_fingerprint": (
                adapter._registry_fingerprint()
                if getattr(adapter, "registry_collections", ())
                else ""
            ),
            "present_files": sorted(
                path for path in scope.get("files") or ()
                if os.path.exists(path)
            ),
        }

    def _refresh_changed_sources(
        self,
        adapter_name: str,
        adapter: ResourceAdapter,
        previous_state: dict[str, Any],
        check_started: float,
        joined_paths: tuple[str, ...],
        *,
        started: float,
        force: bool,
        claimed: bool,
        scan_started_generation: int,
    ) -> dict[str, Any] | None:
        """Apply only the sources changed since the previous check.

        Returns None when the adapter has to be scanned as a whole: it was
        never checked this way, the index code or the registries it reads
        changed, or the adapter cannot apply the changed paths alone.
        """
        since = previous_state.get("sources_since")
        scope = adapter.source_scope()
        tracking = self._source_tracking_state(adapter, scope, check_started)
        if (
            not isinstance(since, (int, float))
            or stripped_text(previous_state.get("projection_code_fingerprint")) != tracking["projection_code_fingerprint"]
            or stripped_text(previous_state.get("registry_fingerprint")) != tracking["registry_fingerprint"]
        ):
            return None
        previous_records = self.store.records_for_adapter(adapter_name)
        sources = {
            source_path
            for record in previous_records
            if (source_path := stripped_text((record.get("source") or {}).get("path")))
        }
        resolution_paths = set(sources)
        extra_paths = getattr(adapter, "source_resolution_paths", None)
        if callable(extra_paths):
            resolution_paths.update(extra_paths(previous_records))
        found = _find_source_changes({
            **scope,
            "since": float(since) - SOURCE_CHANGE_MARGIN_SECONDS,
            "paths": sorted(resolution_paths),
        })
        known = {raw: Path(real) for raw, real in (found.get("resolved") or {}).items()}
        self._source_resolutions[adapter_name] = known
        vanished = set(previous_state.get("present_files") or ()) - set(tracking["present_files"])
        changed_paths = tuple(sorted(
            set(found.get("changed") or ())
            | (set(found.get("missing") or ()) & sources)
            | vanished
            | set(joined_paths)
        ))
        state = {**tracking, "checked_at": _now_iso()}
        if not changed_paths:
            self.store.mark_checked(
                adapter_name,
                {**previous_state, **state},
                persist=not claimed,
            )
            return self._unchanged_refresh(adapter_name, started, force=force, claimed=claimed)
        token = _known_paths.set(known)
        try:
            changes = self._scan_paths(
                adapter_name,
                adapter.scan_changes,
                changed_paths,
                previous_records,
                previous_state,
            )
            if changes is None:
                return None
            return self._apply_incremental(
                adapter_name,
                changes,
                changed_paths,
                previous_records,
                previous_state,
                state,
                started=started,
                force=force,
                claimed=claimed,
                scan_started_generation=scan_started_generation,
            )
        finally:
            _known_paths.reset(token)

    @staticmethod
    def _incremental_resource_errors(
        previous_state: dict[str, Any],
        current_state: dict[str, Any],
        changed_paths: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        changed = [
            Path(path).expanduser().resolve(strict=False)
            for path in changed_paths
        ]

        def affected(error: dict[str, Any]) -> bool:
            raw_path = stripped_text(error.get("path"))
            if not raw_path:
                return False
            path = Path(raw_path).expanduser().resolve(strict=False)
            return any(
                path == candidate
                or path in candidate.parents
                or candidate in path.parents
                for candidate in changed
            )

        errors = [
            dict(item)
            for item in previous_state.get("resource_errors") or []
            if isinstance(item, dict) and not affected(item)
        ]
        errors.extend(
            dict(item)
            for item in current_state.get("resource_errors") or []
            if isinstance(item, dict)
        )
        deduplicated: list[dict[str, Any]] = []
        seen: set[str] = set()
        for error in errors:
            key = json.dumps(error, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(error)
        return deduplicated

    @staticmethod
    def _retry_incremental_paths(
        adapter_name: str,
        incremental_scan: Callable[..., ChangeSet | None],
        paths: tuple[str, ...],
        previous_records: list[dict[str, Any]],
        previous_state: dict[str, Any],
    ) -> ChangeSet | None:
        previous_by_path = {
            stripped_text((record.get("source") or {}).get("path")): stripped_text(
                record.get("resource_id")
            )
            for record in previous_records
            if stripped_text((record.get("source") or {}).get("path"))
        }
        upserts: dict[str, dict[str, Any]] = {}
        deletes: set[str] = set()
        source_state: dict[str, Any] = {}
        resource_errors: list[dict[str, str]] = []
        for path in paths:
            try:
                changes = incremental_scan(
                    (path,),
                    previous_records,
                    previous_state,
                )
            except Exception as exc:
                resource_errors.append(
                    {
                        "resource_id": previous_by_path.get(path, ""),
                        "path": path,
                        "error": str(exc)[:1000],
                    }
                )
                continue
            if changes is None:
                return None
            if not isinstance(changes, ChangeSet) or changes.adapter != adapter_name:
                raise ValueError(
                    f"adapter {adapter_name} returned an invalid incremental ChangeSet"
                )
            resource_errors.extend(
                dict(item)
                for item in changes.source_state.get("resource_errors") or []
                if isinstance(item, dict)
            )
            source_state.update(
                {
                    key: value
                    for key, value in changes.source_state.items()
                    if key != "resource_errors"
                }
            )
            for resource_id in changes.deletes:
                clean_id = stripped_text(resource_id)
                if clean_id:
                    deletes.add(clean_id)
                    upserts.pop(clean_id, None)
            for record in changes.upserts:
                resource_id = stripped_text(record.get("resource_id"))
                if resource_id:
                    upserts[resource_id] = record
                    deletes.discard(resource_id)
        return ChangeSet(
            adapter=adapter_name,
            upserts=tuple(upserts[key] for key in sorted(upserts)),
            deletes=tuple(sorted(deletes)),
            source_state={
                **source_state,
                "last_incremental_path_count": len(paths),
                "resource_errors": resource_errors,
            },
        )

    def _run(self) -> None:
        # Every process generation checks source fingerprints once. Existing
        # snapshots avoid parsing when unchanged, while edits made during
        # downtime are recovered immediately instead of waiting for the
        # periodic reconcile.
        self.request_refresh(self.adapters, force=False)
        # The reconcile runs on a clock, not only after an idle spell: while
        # agents keep editing knowledge, the queue is never empty, and a change
        # the file watch missed (new Residents, 2026-10-03) stayed unindexed
        # for as long as the edits went on.
        next_reconcile = time.monotonic() + self._reconcile_seconds
        while not self._stop_event.is_set():
            try:
                name = self._queue.get(
                    timeout=max(0.0, next_reconcile - time.monotonic())
                )
            except queue.Empty:
                name = None
            if time.monotonic() >= next_reconcile:
                next_reconcile = time.monotonic() + self._reconcile_seconds
                with self._condition:
                    idle = [
                        adapter for adapter in self.adapters
                        if adapter not in self._pending and adapter not in self._running
                    ]
                if idle:
                    self.request_refresh(idle, force=False)
            if name is None:
                continue
            if not name and self._stop_event.is_set():
                self._queue.task_done()
                break
            with self._condition:
                # A fixed window from the first change merges filesystem and
                # explicit notifications without starvation under steady writes.
                while not self._stop_event.is_set() and name in self._pending:
                    remaining = self._pending[name].queued_at + self._merge_seconds - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
                if self._stop_event.is_set():
                    self._queue.task_done()
                    break
                pending = self._pending.pop(name, _PendingRefresh(full=True))
                self._running.add(name)
            started = time.monotonic()
            result: dict[str, Any] | None = None
            try:
                with cancellable_scan(self._stop_event):
                    result = self.refresh_now(
                        name,
                        force=pending.force,
                        rescan=pending.rescan,
                        paths=() if pending.full else pending.paths,
                        joined_paths=pending.paths if pending.full else (),
                        _claimed=True,
                    )
            except ScanCancelled:
                pass
            except Exception:
                # refresh_now already recorded the error in the adapter status.
                LOGGER.exception("resource index refresh failed: %s", name)
            finally:
                # One worker serves every adapter, so a slow refresh delays the
                # rest: say how long this one waited in the queue and ran.
                waited = started - pending.queued_at if pending.queued_at else 0.0
                ran = time.monotonic() - started
                timing.report_slow(
                    "resource index refresh",
                    max(waited, ran),
                    threshold=SLOW_REFRESH_SECONDS,
                    adapter=name,
                    waited=round(waited, 3),
                    ran=round(ran, 3),
                    paths=0 if pending.full else len(pending.paths),
                    queued=self._queue.qsize(),
                )
                # Worker refreshes batch writes in memory. Persist them before
                # announcing the new projection to Portal.
                ok = False
                try:
                    self.store.flush()
                    if result is not None:
                        self._completed_refresh(name, result, forced=pending.force, emit=True)
                        ok = not stripped_text((self.store.status().get("adapters", {}).get(name) or {}).get("last_error"))
                except Exception:
                    LOGGER.exception("resource index persistence failed: %s", name)
                finally:
                    self._queue.task_done()
                    with self._condition:
                        pending.completion.ok = ok
                        pending.completion.done = True
                        self._running.discard(name)
                        self._condition.notify_all()

    def wait(self, adapters: Iterable[str] = (), timeout: float = 90.0) -> bool:
        names = set(adapters) or set(self.adapters)
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            # Capture the batches visible at entry. Later changes must not
            # extend this caller's barrier indefinitely.
            completions = [self._latest_refresh[name] for name in names if name in self._latest_refresh]
            while any(not item.done for item in completions):
                if self._stop_event.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        if self._stop_event.is_set():
            return False
        if not all(item.ok for item in completions):
            return False
        status = self.store.status().get("adapters") or {}
        return all(
            not stripped_text((status.get(name) or {}).get("last_error"))
            for name in names if name not in self._latest_refresh
        )

    def status(self) -> dict[str, Any]:
        with self._condition:
            status = self.store.status(self._running, self._pending)
        status["file_detector"] = (
            self._file_detector.status()
            if self._file_detector is not None
            else {
                "enabled": False,
                "ready": False,
                "watch_count": 0,
                "last_event_at": "",
                "last_error": "",
            }
        )
        return status
