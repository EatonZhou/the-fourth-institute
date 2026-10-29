from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable

from management_common.service_shutdown import stopping_with
from .lineage_automation_identity import AutomationConfigurationError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LineageCoordinator:
    """Restore at startup and coalesce explicit runtime notifications."""

    def __init__(
        self,
        *,
        resume_residents: Callable[[], dict[str, Any]],
        resume_resident: Callable[[str], dict[str, Any]],
        reconcile_all: Callable[[], dict[str, Any]],
        reconcile_lineage: Callable[[str], dict[str, Any]],
        recover_program: Callable[[], dict[str, Any]] | None = None,
        consume_terminal: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        recover_terminals: Callable[[str], list[dict[str, Any]]] | None = None,
        housekeeping: Callable[[], Any] | None = None,
        housekeeping_seconds: float = 300.0,
        recovery_seconds: float = 30.0,
        lifecycle_ready: Callable[[], list[str]] | None = None,
        lifecycle_run: Callable[[str], Any] | None = None,
        cancel_execution: Callable[[str, str], Any] | None = None,
        dispatch_arrival: Callable[[str, str, str], Any] | None = None,
        wake_for_due_sources: bool = True,
        timer_lease: Callable[[], bool] | None = None,
        timer_lease_seconds: float = 30.0,
        sync_templates: Callable[[], dict[str, Any]] | None = None,
        migrate_runtime: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        # Run once per start: template-owned structure and stored Program
        # runtime follow the running code.
        self._sync_templates = sync_templates
        self._migrate_runtime = migrate_runtime
        self._wake_for_due_sources = bool(wake_for_due_sources)
        # With a lease, only its holder wakes Lineages for due sources; the
        # lease is renewed every ``timer_lease_seconds``.
        self._timer_lease = timer_lease
        self._timer_lease_seconds = max(0.05, float(timer_lease_seconds))
        self._timer_owner = timer_lease is None
        self._resume_residents = resume_residents
        self._resume_resident = resume_resident
        self._reconcile_all = reconcile_all
        self._reconcile_lineage = reconcile_lineage
        self._recover_program = recover_program
        self._consume_terminal = consume_terminal
        # Terminal facts live in memory until consumed; once per start, after
        # the first recovery interval, facts a stopped process dropped are
        # read back from their committed turns (lost_terminal_facts).
        self._recover_terminals = recover_terminals
        self._terminals_recovered = False
        self._housekeeping = housekeeping
        self._housekeeping_seconds = max(
            60.0,
            float(housekeeping_seconds),
        )
        self._recovery_seconds = max(0.05, float(recovery_seconds))
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._lifecycle_executor: ThreadPoolExecutor | None = None
        self._cancellation_executor: ThreadPoolExecutor | None = None
        self._cancel_execution = cancel_execution
        # Message arrivals dispatch their Events on their own lane, one worker
        # per message, so a Family Router starts at once and routes in parallel.
        self._dispatch_arrival = dispatch_arrival
        self._arrival_executor: ThreadPoolExecutor | None = None
        self._pending_arrivals: dict[tuple[str, str, str], None] = {}
        self._pending_cancellations: dict[str, str] = {}
        self._work: dict[tuple[str, str], Future] = {}
        self._work_local = threading.local()
        self._worker_count = 8
        self._lifecycle_ready = lifecycle_ready
        self._lifecycle_run = lifecycle_run
        self._pending_residents: set[str] = set()
        self._reconcile_failures: dict[str, int] = {}
        self._blocked_lineages: dict[str, str] = {}
        self._pending_lineages: set[str] = set()
        self._pending_terminals: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._terminal_retries: dict[tuple[str, str, str, str], float] = {}
        # Due times of the next look at a Lineage: retries after a failure or a
        # requested delay, and wakes for its next time or file source.
        self._lineage_retries: dict[str, float] = {}
        self._lineage_wakes: dict[str, float] = {}
        self._active_lineages: set[str] = set()
        self._running_full_reconcile = False
        self._traced_errors: set[str] = set()
        self._state: dict[str, Any] = {
            "started_at": "",
            "last_recovery_at": "",
            "last_reconcile_at": "",
            "last_recovery": {},
            "last_reconcile": {},
            "last_housekeeping_at": "",
            "last_housekeeping_error": "",
            "last_error": "",
            "reconcile_count": 0,
            "recovery_count": 0,
            "coalesced_lineage_notifications": 0,
            "suppressed_self_notifications": 0,
            "terminal_consumption_count": 0,
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._work:
                raise RuntimeError("Lineage coordinator is still draining")
        self._executor = ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="lineage-coordination",
        )
        self._lifecycle_executor = ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="resident-lifecycle-effects",
        )
        self._cancellation_executor = ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="resident-execution-cancellation",
        )
        self._arrival_executor = ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="lineage-message-arrival",
        )
        self._stop.clear()
        with self._lock:
            self._state["started_at"] = _now_iso()
            self._terminals_recovered = False
        self._thread = threading.Thread(
            target=self._run,
            name="society-service-coordinator",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        self._stop.set()
        self._wake.set()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        if self._lifecycle_executor is not None:
            self._lifecycle_executor.shutdown(wait=False, cancel_futures=True)
            self._lifecycle_executor = None
        if self._cancellation_executor is not None:
            self._cancellation_executor.shutdown(wait=False, cancel_futures=True)
            self._cancellation_executor = None
        if self._arrival_executor is not None:
            self._arrival_executor.shutdown(wait=False, cancel_futures=True)
            self._arrival_executor = None

        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        if thread is None or not thread.is_alive():
            self._thread = None
        with self._lock:
            # Work the shutdowns above cancelled before it ran never reaches
            # run(), so nothing removes it, and wait() would sit out the whole
            # timeout: a future cancelled by shutdown(cancel_futures=True) is
            # never marked as notified.
            for key, future in list(self._work.items()):
                if future.cancelled():
                    del self._work[key]
            running = list(self._work.values())
        if running:
            wait(running, timeout=max(0.0, deadline - time.monotonic()))

    def _submit(self, key: tuple[str, str], callback: Callable[[], Any]) -> bool:
        """Bound concurrency and coalesce by object, never wait for a worker."""

        def run() -> None:
            self._work_local.key = key
            try:
                with stopping_with(self._stop):
                    callback()
            except Exception as exc:
                self._record_error(exc)
            finally:
                self._work_local.key = None
                with self._lock:
                    self._work.pop(key, None)
                self._wake.set()

        with self._lock:
            executors = {
                "lifecycle": self._lifecycle_executor, "cancel": self._cancellation_executor,
                "arrival": self._arrival_executor,
            }
            executor = executors.get(key[0], self._executor)
            active_in_lane = sum(executors.get(item[0], self._executor) is executor for item in self._work)
            if (
                self._stop.is_set()
                or executor is None
                or key in self._work
                or active_in_lane >= self._worker_count
            ):
                return False
            self._work[key] = executor.submit(run)
        return True

    def notify_execution_cancellation(self, execution_id: str, actor: str) -> None:
        if not execution_id or not actor or self._cancel_execution is None:
            return
        with self._lock:
            if self._stop.is_set() or ("cancel", execution_id) in self._work:
                return
            self._pending_cancellations[execution_id] = actor
        self._wake.set()

    def notify_message_arrival(self, lineage_id: str, resident_id: str, message_id: str) -> None:
        """Dispatch one accepted message's Event on its own worker (decision_44cf587c1721)."""

        key = (str(lineage_id or "").strip(), str(resident_id or "").strip(), str(message_id or "").strip())
        if not all(key):
            return
        if self._dispatch_arrival is None:
            self.notify_lineage(key[0])
            return
        with self._lock:
            if self._stop.is_set():
                return
            self._pending_arrivals[key] = None
        self._wake.set()

    def _dispatch_one_arrival(self, lineage_id: str, resident_id: str, message_id: str) -> None:
        try:
            self._dispatch_arrival(lineage_id, resident_id, message_id)
        except AutomationConfigurationError as exc:
            self._block_lineage(lineage_id, str(exc))
        finally:
            # The pass moves its baseline past the pushed Event, or produces it if the push failed.
            self.notify_lineage(lineage_id)

    def notify_resident(self, resident_id: str) -> None:
        clean_id = str(resident_id or "").strip()
        if not clean_id:
            return
        with self._lock:
            self._pending_residents.add(clean_id)
        self._wake.set()

    def notify_lineage(self, lineage_id: str) -> None:
        clean_id = str(lineage_id or "").strip()
        if not clean_id:
            return
        with self._lock:
            work_key = getattr(self._work_local, "key", None)
            called_by_coordinator = work_key in {
                ("lineage", clean_id), ("maintenance", "reconcile"),
            }
            if called_by_coordinator and (
                self._running_full_reconcile
                or clean_id in self._active_lineages
            ):
                self._state["suppressed_self_notifications"] = int(
                    self._state.get("suppressed_self_notifications") or 0
                ) + 1
                return
            if clean_id in self._pending_lineages:
                self._state["coalesced_lineage_notifications"] = int(
                    self._state.get("coalesced_lineage_notifications") or 0
                ) + 1
            self._lineage_retries.pop(clean_id, None)
            self._lineage_wakes.pop(clean_id, None)
            self._pending_lineages.add(clean_id)
        self._wake.set()

    def _schedule_lineage_retry(
        self,
        lineage_id: str,
        delay_seconds: float,
        *,
        wake: bool = False,
    ) -> None:
        clean_id = str(lineage_id or "").strip()
        if not clean_id:
            return
        due_at = time.monotonic() + max(0.05, float(delay_seconds))
        queue = self._lineage_wakes if wake else self._lineage_retries
        with self._lock:
            if clean_id in self._blocked_lineages:
                return
            previous_due = queue.get(clean_id)
            if previous_due is None or due_at < previous_due:
                queue[clean_id] = due_at
        self._wake.set()

    def _schedule_lineage_wake(self, lineage_id: str, wake_at: object) -> None:
        """Look at a Lineage again when its next time or file source is due.

        Time sources fire only when their Lineage is reconciled, so without
        this wake a recurring source would wait for an unrelated change or a
        service restart.
        """

        text = str(wake_at or "").strip()
        if not text or not self._wake_for_due_sources or not self._timer_owner:
            return
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        delay = (moment - datetime.now(timezone.utc)).total_seconds()
        # A past or immediate due time is looked at shortly, never in a tight loop.
        self._schedule_lineage_retry(lineage_id, max(5.0, delay), wake=True)

    def _renew_timer_lease(self) -> None:
        try:
            owner = bool(self._timer_lease()) if self._timer_lease is not None else True
        except Exception as exc:  # noqa: BLE001 - without the lease no timers run here
            self._record_error(exc)
            owner = False
        gained = owner and not self._timer_owner
        self._timer_owner = owner
        with self._lock:
            self._state["timer_owner"] = owner
        if gained:
            # The previous holder's wakes are lost with it; find every due source.
            self._submit(("maintenance", "reconcile"), self._reconcile)

    def _take_due_lineage_retries(self, now: float) -> list[str]:
        due: set[str] = set()
        with self._lock:
            for queue in (self._lineage_retries, self._lineage_wakes):
                for lineage_id in [key for key, due_at in queue.items() if due_at <= now]:
                    queue.pop(lineage_id)
                    due.add(lineage_id)
        return sorted(due)

    def _next_lineage_retry_at(self) -> float | None:
        with self._lock:
            return min((*self._lineage_retries.values(), *self._lineage_wakes.values()), default=None)

    def notify_terminal(self, fact: dict[str, Any]) -> None:
        resident_id = str(fact.get("resident_id") or "").strip()
        message_id = str(fact.get("message_id") or "").strip()
        terminal_status = str(fact.get("terminal_status") or "completed").strip()
        lineage_id = str(fact.get("lineage_id") or "").strip()
        if not resident_id or not message_id:
            return
        payload = dict(fact)
        key = (resident_id, message_id, terminal_status, lineage_id)
        with self._lock:
            self._pending_terminals[key] = payload
        self._wake.set()

    def _retry_terminal(self, fact: dict[str, Any], *, delayed: bool = True) -> None:
        key = (
            str(fact.get("resident_id") or "").strip(),
            str(fact.get("message_id") or "").strip(),
            str(fact.get("terminal_status") or "completed").strip(),
            str(fact.get("lineage_id") or "").strip(),
        )
        with self._lock:
            self._pending_terminals[key] = dict(fact)
            if delayed:
                self._terminal_retries[key] = time.monotonic() + self._recovery_seconds

    def _take_pending(
        self,
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        with self._lock:
            residents = sorted(self._pending_residents)
            lineages = sorted(self._pending_lineages)
            now = time.monotonic()
            ready = [key for key in self._pending_terminals if self._terminal_retries.get(key, 0) <= now]
            terminals = [self._pending_terminals.pop(key) for key in ready]
            for key in ready:
                self._terminal_retries.pop(key, None)
            self._pending_residents.clear()
            self._pending_lineages.clear()
        return residents, lineages, terminals

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = {
                key: dict(value) if isinstance(value, dict) else value
                for key, value in self._state.items()
            }
            snapshot["active_lineage_ids"] = sorted(self._active_lineages)
            snapshot["pending_resident_ids"] = sorted(self._pending_residents)
            snapshot["pending_lineage_ids"] = sorted(self._pending_lineages)
            snapshot["pending_terminal_count"] = len(self._pending_terminals)
            snapshot["pending_cancellation_count"] = len(self._pending_cancellations)
            snapshot["active_work"] = [list(key) for key in sorted(self._work)]
            snapshot["worker_limit"] = self._worker_count
            snapshot["cancellation_worker_limit"] = self._worker_count
            snapshot["retry_lineage_ids"] = sorted(self._lineage_retries)
            snapshot["wake_lineage_ids"] = sorted(self._lineage_wakes)
            snapshot["blocked_lineages"] = dict(self._blocked_lineages)
            snapshot["thread_alive"] = bool(
                self._thread is not None and self._thread.is_alive()
            )
            return snapshot

    def _record_error(self, exc: BaseException, scope: str = "") -> None:
        message = f"{exc.__class__.__name__}: {exc}"
        if exc.__cause__ is not None:
            # A domain error wraps the store's reason, such as which object conflicted.
            message += f" (caused by {exc.__cause__.__class__.__name__}: {exc.__cause__})"
        # Name the Lineage or Resident: one Lineage's reconcile failed every
        # five minutes for three days as a bare "KeyError: 'resident not found'".
        message = (f"{scope}: {message}" if scope else message)[:2000]
        with self._lock:
            changed = message != self._state.get("last_error")
            self._state["last_error"] = message
            first = message not in self._traced_errors
            if first and len(self._traced_errors) < 200:
                self._traced_errors.add(message)
        if changed:
            print(f"SocietyService coordinator failed: {message}", file=sys.stderr)
            if first:
                traceback.print_exception(exc, file=sys.stderr)

    def _recover(self) -> None:
        try:
            result = dict(self._resume_residents() or {})
        except Exception as exc:
            self._record_error(exc)
            return
        with self._lock:
            self._state["last_recovery"] = result
            self._state["last_recovery_at"] = _now_iso()
            self._state["last_error"] = ""
            self._state["recovery_count"] = int(
                self._state.get("recovery_count") or 0
            ) + 1

    def _recover_pending_program(self) -> None:
        try:
            result = dict(self._recover_program() or {})
        except Exception as exc:
            self._record_error(exc)
            return
        with self._lock:
            self._state["last_program_recovery"] = result
            self._state["last_program_recovery_at"] = _now_iso()

    def _recover_lost_terminals(self) -> None:
        with self._lock:
            started_at = str(self._state.get("started_at") or "")
        try:
            facts = list(self._recover_terminals(started_at) or [])
        except Exception as exc:
            self._record_error(exc)
            with self._lock:
                self._terminals_recovered = False
            return
        for fact in facts:
            self.notify_terminal(fact)
        with self._lock:
            self._state["recovered_terminal_count"] = len(facts)
        if facts:
            names = ", ".join(f"{fact['resident_id']}:{fact['message_id']}" for fact in facts)
            print(f"SocietyService requeued {len(facts)} dropped terminal fact(s): {names}", file=sys.stderr)

    def _sync_template_structure(self) -> None:
        try:
            result = dict(self._sync_templates() or {})
        except Exception as exc:
            self._record_error(exc)
            return
        with self._lock:
            self._state["last_template_sync"] = result
            self._state["last_template_sync_at"] = _now_iso()
        if result.get("synced") or result.get("failed"):
            print(f"Template structure sync: {json.dumps(result, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)

    def _migrate_stored_runtime(self) -> None:
        try:
            result = dict(self._migrate_runtime() or {})
        except Exception as exc:
            self._record_error(exc)
            return
        with self._lock:
            self._state["last_runtime_migration"] = result
            self._state["last_runtime_migration_at"] = _now_iso()
        if result.get("lineages"):
            print(f"Program runtime migration: {json.dumps(result, sort_keys=True)}", file=sys.stderr)

    def _recover_resident(self, resident_id: str) -> None:
        try:
            result = dict(self._resume_resident(resident_id) or {})
        except Exception as exc:
            self._record_error(exc, resident_id)
            return
        with self._lock:
            self._state["last_recovery"] = result
            self._state["last_recovery_at"] = _now_iso()
            self._state["last_error"] = ""

    def _reconcile(self) -> None:
        with self._lock:
            self._running_full_reconcile = True
        try:
            result = dict(self._reconcile_all() or {})
        except Exception as exc:
            self._record_error(exc)
            return
        finally:
            with self._lock:
                self._running_full_reconcile = False
        with self._lock:
            self._state["last_reconcile"] = result
            self._state["last_reconcile_at"] = _now_iso()
            self._state["last_error"] = ""
            self._state["reconcile_count"] = int(
                self._state.get("reconcile_count") or 0
            ) + 1
        for lineage_id, reason in (result.get("blocked_lineages") or {}).items():
            self._block_lineage(lineage_id, reason)
        for lineage_id in result.get("retry_lineage_ids") or []:
            with self._lock:
                self._blocked_lineages.pop(str(lineage_id), None)
            self._schedule_lineage_retry(str(lineage_id), 5.0)
        wakes = result.get("wake_lineages") if isinstance(result.get("wake_lineages"), dict) else {}
        for lineage_id, wake_at in wakes.items():
            with self._lock:
                self._blocked_lineages.pop(str(lineage_id), None)
            self._schedule_lineage_wake(str(lineage_id), wake_at)

    def _block_lineage(self, lineage_id: str, reason: str) -> None:
        with self._lock:
            self._blocked_lineages[lineage_id] = reason[:500]
            self._lineage_retries.pop(lineage_id, None)
            self._lineage_wakes.pop(lineage_id, None)
            self._reconcile_failures.pop(lineage_id, None)

    def _reconcile_one(self, lineage_id: str) -> None:
        with self._lock:
            self._active_lineages.add(lineage_id)
            self._blocked_lineages.pop(lineage_id, None)
        try:
            result = dict(self._reconcile_lineage(lineage_id) or {})
        except AutomationConfigurationError as exc:
            self._block_lineage(lineage_id, str(exc))
            return
        except Exception as exc:
            self._record_error(exc, lineage_id)
            with self._lock:
                failures = min(self._reconcile_failures.get(lineage_id, 0) + 1, 7)
                self._reconcile_failures[lineage_id] = failures
            self._schedule_lineage_retry(lineage_id, min(300.0, 5.0 * 2 ** (failures - 1)))
            return
        finally:
            with self._lock:
                self._active_lineages.discard(lineage_id)
        with self._lock:
            self._reconcile_failures.pop(lineage_id, None)
            self._state["last_reconcile"] = result
            self._state["last_reconcile_at"] = _now_iso()
            self._state["last_error"] = ""
            self._state["reconcile_count"] = int(
                self._state.get("reconcile_count") or 0
            ) + 1
        retry_after = float(result.get("retry_after_seconds") or 0)
        if result.get("status") == "blocked":
            self._block_lineage(lineage_id, str(result.get("error") or "Configuration blocked"))
            return
        if retry_after > 0:
            self._schedule_lineage_retry(lineage_id, retry_after)
        self._schedule_lineage_wake(lineage_id, result.get("next_wake_at"))

    def _consume_one_terminal(self, fact: dict[str, Any]) -> None:
        if self._consume_terminal is None:
            return
        try:
            self._consume_terminal(fact)
        except Exception as exc:
            self._record_error(exc)
            self._retry_terminal(fact)
            return
        with self._lock:
            self._state["terminal_consumption_count"] = int(
                self._state.get("terminal_consumption_count") or 0
            ) + 1
            self._state["last_error"] = ""

    def _run_housekeeping(self) -> None:
        if self._housekeeping is None:
            return
        try:
            self._housekeeping()
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"[:2000]
            with self._lock:
                changed = message != self._state.get("last_housekeeping_error")
                self._state["last_housekeeping_error"] = message
            if changed:
                print(
                    f"SocietyService housekeeping failed: {message}",
                    file=sys.stderr,
                )
            return
        with self._lock:
            self._state["last_housekeeping_at"] = _now_iso()
            self._state["last_housekeeping_error"] = ""

    def _run(self) -> None:
        self._submit(("maintenance", "recover"), self._recover)
        self._submit(("maintenance", "reconcile"), self._reconcile)
        self._submit(("maintenance", "housekeeping"), self._run_housekeeping)
        if self._sync_templates is not None:
            self._submit(("maintenance", "template-sync"), self._sync_template_structure)
        if self._migrate_runtime is not None:
            self._submit(("maintenance", "runtime-migration"), self._migrate_stored_runtime)
        next_housekeeping = (
            time.monotonic() + self._housekeeping_seconds
        )
        next_recovery = time.monotonic() + self._recovery_seconds
        next_lifecycle = time.monotonic()
        next_timer_lease = time.monotonic() if self._timer_lease is not None else float("inf")
        while not self._stop.is_set():
            next_retry = self._next_lineage_retry_at()
            with self._lock:
                next_terminal_retry = min(self._terminal_retries.values(), default=float("inf"))
            wait_seconds = max(
                0.0,
                min(
                    next_housekeeping,
                    next_recovery,
                    next_terminal_retry,
                    next_lifecycle,
                    next_timer_lease,
                    next_retry if next_retry is not None else float("inf"),
                )
                - time.monotonic(),
            )
            self._wake.wait(wait_seconds)
            self._wake.clear()
            if self._stop.is_set():
                return
            residents, lineages, terminals = self._take_pending()
            with self._lock:
                cancellations = self._pending_cancellations
                self._pending_cancellations = {}
            with self._lock:
                arrivals = list(self._pending_arrivals)
                self._pending_arrivals = {}
            for lineage_id, resident_id, message_id in arrivals:
                if not self._submit(
                    ("arrival", f"{lineage_id}:{resident_id}:{message_id}"),
                    lambda lid=lineage_id, rid=resident_id, mid=message_id: self._dispatch_one_arrival(lid, rid, mid),
                ):
                    with self._lock:
                        self._pending_arrivals.setdefault((lineage_id, resident_id, message_id), None)
            for execution_id, actor in cancellations.items():
                if not self._submit(
                    ("cancel", execution_id),
                    lambda eid=execution_id, owner=actor: self._cancel_execution(eid, owner),
                ):
                    with self._lock:
                        self._pending_cancellations.setdefault(execution_id, actor)
            for resident_id in residents:
                if not self._submit(
                    ("resident", resident_id),
                    lambda rid=resident_id: self._recover_resident(rid),
                ):
                    with self._lock:
                        self._pending_residents.add(resident_id)
            for fact in terminals:
                if not self._submit(
                    ("terminal", str(fact.get("resident_id") or "")),
                    lambda item=fact: self._consume_one_terminal(item),
                ):
                    self._retry_terminal(fact, delayed=False)
            lineages.extend(self._take_due_lineage_retries(time.monotonic()))
            for lineage_id in lineages:
                if not self._submit(
                    ("lineage", lineage_id),
                    lambda lid=lineage_id: self._reconcile_one(lid),
                ):
                    with self._lock:
                        self._pending_lineages.add(lineage_id)
            now = time.monotonic()
            if now >= next_timer_lease:
                next_timer_lease = now + self._timer_lease_seconds
                self._renew_timer_lease()
            if now >= next_lifecycle:
                if self._lifecycle_ready is not None and self._lifecycle_run is not None:
                    try:
                        for operation_id in self._lifecycle_ready():
                            self._submit(
                                ("lifecycle", operation_id),
                                lambda oid=operation_id: self._lifecycle_run(oid),
                            )
                    except Exception as exc:
                        self._record_error(exc)
                next_lifecycle = now + 1.0
            if now >= next_recovery:
                self._submit(("maintenance", "recover"), self._recover)
                if self._recover_program is not None:
                    self._submit(("maintenance", "program-recovery"), self._recover_pending_program)
                # The stopping process may still finish a consumption while
                # this one starts; looking one interval later lets it finish.
                with self._lock:
                    recover_terminals = self._recover_terminals is not None and not self._terminals_recovered
                    self._terminals_recovered = True
                if recover_terminals and not self._submit(
                    ("maintenance", "terminal-recovery"), self._recover_lost_terminals,
                ):
                    with self._lock:
                        self._terminals_recovered = False
                next_recovery = time.monotonic() + self._recovery_seconds
            if now >= next_housekeeping:
                self._submit(("maintenance", "housekeeping"), self._run_housekeeping)
                next_housekeeping = now + self._housekeeping_seconds
