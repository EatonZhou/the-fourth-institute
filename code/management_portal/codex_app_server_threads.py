"""The threads of a Codex app-server connection.

App-server keeps a thread loaded for every client that started, forked or
resumed it.  ``CodexLoadedThreads`` remembers which threads this connection
loaded and with which configuration, serializes the work on each thread and
unloads threads that stay idle.  ``CodexThreadReads`` reads a thread's turns,
status and goal.  ``codex_thread_execution_lock`` serializes one thread
across Portal processes.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from management_common.file_lock import try_lock_descriptor, unlock_descriptor
from management_common.portal_paths import path as portal_path

from .codex_app_server_protocol import (
    TASK_COMPLETE_GOAL_STATUSES,
    CodexAppServerError,
    CodexRequestTimeouts,
    config_fingerprint,
)

if TYPE_CHECKING:
    from .codex_app_server_events import CodexActiveWork
    from .codex_app_server_rpc import CodexRpcConnection


DEFAULT_THREAD_LOCK_ROOT = (portal_path("staging_root") / "portal_locks" / "codex-app-server-threads")
DEFAULT_IDLE_UNSUBSCRIBE_SECONDS = 5 * 60 * 60
DEFAULT_MAX_LOADED_THREADS = 30
THREAD_LOCK_POLL_INTERVAL_SECONDS = 0.25


@contextmanager
def codex_thread_execution_lock(
    thread_id: str,
    *,
    cancellation_requested: Callable[[], bool] | None = None,
    poll_interval_seconds: float = 0.1,
    blocking: bool = True,
):
    safe_thread_id = str(thread_id or "").strip()
    if not safe_thread_id:
        raise ValueError("thread_id required")
    root = Path(os.getenv("PORTAL_CODEX_APP_SERVER_THREAD_LOCK_ROOT", str(DEFAULT_THREAD_LOCK_ROOT)))
    root.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(safe_thread_id.encode("utf-8")).hexdigest() + ".lock"
    with (root / lock_name).open("a+", encoding="utf-8") as stream:
        acquired = False
        try:
            while not acquired:
                if cancellation_requested and cancellation_requested():
                    raise InterruptedError(
                        f"Codex thread lock cancelled for {safe_thread_id}"
                    )
                acquired = try_lock_descriptor(stream.fileno())
                if not acquired:
                    if not blocking:
                        yield False
                        return
                    time.sleep(max(0.01, float(poll_interval_seconds)))
            yield True
        finally:
            if acquired:
                unlock_descriptor(stream.fileno())


@dataclass(frozen=True)
class ThreadLoad:
    """What the connection knew of a thread when a run on it began."""

    loaded: bool
    unmaterialized: bool
    requested_config: str | None
    # A zero-turn thread keeps its configuration until its first turn.
    config_reload_deferred: bool


def _result_thread_id(result: dict[str, Any]) -> str:
    thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
    return str(thread.get("id") or "")


class CodexLoadedThreads:
    def __init__(
        self,
        rpc: CodexRpcConnection,
        work: CodexActiveWork,
        *,
        state_lock: threading.RLock,
        timeouts: CodexRequestTimeouts,
        idle_unsubscribe_seconds: int,
        max_loaded_threads: int,
    ) -> None:
        self._rpc = rpc
        self._work = work
        self._state_lock = state_lock
        self.timeouts = timeouts
        self.idle_unsubscribe_seconds = max(60, int(idle_unsubscribe_seconds))
        self.max_loaded_threads = max(1, int(max_loaded_threads))
        self._maintenance_thread: threading.Thread | None = None
        self._busy: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        self._activity: dict[str, float] = {}
        self._configs: dict[str, str] = {}
        # thread/start returns before a zero-turn thread has a durable rollout.
        # Reloading that thread through unsubscribe/resume would remove its only
        # in-memory representation before the first turn can materialize it.
        self._unmaterialized: set[str] = set()

    def counts(self) -> tuple[int, int]:
        """The numbers of loaded and of busy threads."""

        with self._state_lock:
            return len(self._activity), len(self._busy)

    def is_loaded(self, thread_id: str) -> bool:
        with self._state_lock:
            return thread_id in self._activity

    def mark_loaded(self, thread_id: str, *, config: str | None = None) -> None:
        with self._state_lock:
            self._activity[thread_id] = time.monotonic()
            if config is not None:
                self._configs[thread_id] = config

    def touch(self, thread_id: str) -> None:
        """Record activity on a thread that is still loaded."""

        with self._state_lock:
            if thread_id in self._activity:
                self._activity[thread_id] = time.monotonic()

    def mark_unmaterialized(self, thread_id: str) -> None:
        with self._state_lock:
            self._unmaterialized.add(thread_id)

    def mark_materialized(self, thread_id: str) -> None:
        with self._state_lock:
            self._unmaterialized.discard(thread_id)

    def set_busy(self, thread_id: str) -> None:
        with self._state_lock:
            self._busy.add(thread_id)

    def clear_busy(self, thread_id: str) -> None:
        with self._state_lock:
            self._busy.discard(thread_id)

    def forget(self, thread_id: str) -> None:
        with self._state_lock:
            self._activity.pop(thread_id, None)
            self._configs.pop(thread_id, None)
            self._unmaterialized.discard(thread_id)

    def forget_all(self) -> None:
        """The connection ended: app-server no longer holds any thread for it."""

        with self._state_lock:
            self._activity.clear()
            self._configs.clear()
            self._unmaterialized.clear()

    def lock_for(self, thread_id: str) -> threading.Lock:
        with self._state_lock:
            return self._locks.setdefault(thread_id, threading.Lock())

    @contextmanager
    def locked(
        self,
        thread_id: str,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ):
        """Serialize one thread without hiding cancellation behind a lock wait."""

        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        lock = self.lock_for(safe_thread_id)
        acquired = False
        try:
            while not acquired:
                if cancellation_requested and cancellation_requested():
                    raise InterruptedError(
                        f"Codex thread lock cancelled for {safe_thread_id}"
                    )
                acquired = lock.acquire(timeout=THREAD_LOCK_POLL_INTERVAL_SECONDS)
            yield
        finally:
            if acquired:
                lock.release()

    def start(
        self,
        params: dict[str, Any],
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        result = self._rpc.request(
            "thread/start",
            params,
            timeout=self.timeouts.lifecycle,
            cancellation_requested=cancellation_requested,
        )
        thread_id = _result_thread_id(result)
        if thread_id:
            with self._state_lock:
                self._activity[thread_id] = time.monotonic()
                self._unmaterialized.add(thread_id)
                if "config" in params:
                    self._configs[thread_id] = config_fingerprint(params.get("config"))
        return result

    def fork(
        self,
        params: dict[str, Any],
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        result = self._rpc.request(
            "thread/fork",
            params,
            timeout=self.timeouts.lifecycle,
            cancellation_requested=cancellation_requested,
        )
        thread_id = _result_thread_id(result)
        if thread_id:
            with self._state_lock:
                self._activity[thread_id] = time.monotonic()
                if "config" in params:
                    self._configs[thread_id] = config_fingerprint(params.get("config"))
        return result

    def prepare_reload(
        self,
        thread_id: str,
        config: dict[str, Any] | None,
        *,
        cancellation_requested: Callable[[], bool] | None,
    ) -> ThreadLoad:
        """Unload a loaded thread whose configuration the run changes.

        A thread no turn has materialized yet stays loaded: it has no
        rollout to resume from.
        """

        with self._state_lock:
            loaded = thread_id in self._activity
            loaded_config = self._configs.get(thread_id)
            unmaterialized = thread_id in self._unmaterialized
        requested_config = config_fingerprint(config) if config is not None else None
        reconfigured = bool(loaded and requested_config is not None and loaded_config != requested_config)
        if reconfigured and not unmaterialized:
            self._rpc.request(
                "thread/unsubscribe",
                {"threadId": thread_id},
                timeout=self.timeouts.lifecycle,
                cancellation_requested=cancellation_requested,
            )
            with self._state_lock:
                self._activity.pop(thread_id, None)
                self._configs.pop(thread_id, None)
            loaded = False
        return ThreadLoad(
            loaded=loaded,
            unmaterialized=unmaterialized,
            requested_config=requested_config,
            config_reload_deferred=reconfigured and unmaterialized,
        )

    def unsubscribe(self, thread_id: str) -> None:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            return
        with self.locked(safe_thread_id):
            self._rpc.request("thread/unsubscribe", {"threadId": safe_thread_id}, timeout=45.0)
            self.forget(safe_thread_id)

    def ensure_maintenance(self) -> None:
        with self._state_lock:
            if self._maintenance_thread and self._maintenance_thread.is_alive():
                return
            self._maintenance_thread = threading.Thread(
                target=self._maintenance_loop,
                name="portal-codex-app-server-maintenance",
                daemon=True,
            )
            self._maintenance_thread.start()

    def _maintenance_loop(self) -> None:
        while not self._rpc.closing:
            time.sleep(60)
            try:
                self.unsubscribe_idle()
            except Exception:
                continue

    def _in_use(self, thread_id: str) -> bool:
        return (
            thread_id in self._busy
            or self._work.follows_any(thread_id)
            or thread_id in self._unmaterialized
        )

    def unsubscribe_idle(self) -> None:
        now = time.monotonic()
        with self._state_lock:
            # A zero-turn thread only exists inside app-server memory.  It
            # cannot be resumed until its first turn creates a rollout, so
            # idle/LRU maintenance must treat it like an active turn.
            activity = dict(self._activity)
            idle = sorted(
                ((last_active, thread_id) for thread_id, last_active in activity.items()
                 if not self._in_use(thread_id)),
                key=lambda item: item[0],
            )
        over_limit = max(0, len(activity) - self.max_loaded_threads)
        expired = {thread_id for last_active, thread_id in idle if now - last_active >= self.idle_unsubscribe_seconds}
        targets = set(thread_id for _last_active, thread_id in idle[:over_limit]) | expired
        for thread_id in targets:
            lock = self.lock_for(thread_id)
            if not lock.acquire(blocking=False):
                continue
            try:
                with self._state_lock:
                    if self._in_use(thread_id) or self._activity.get(thread_id) != activity[thread_id]:
                        continue
                self._rpc.request("thread/unsubscribe", {"threadId": thread_id}, timeout=45.0)
                self.forget(thread_id)
            except Exception:
                continue
            finally:
                lock.release()


class CodexThreadReads:
    """Snapshots of a thread's state that start or resume no work."""

    def __init__(self, rpc: CodexRpcConnection, timeouts: CodexRequestTimeouts) -> None:
        self._rpc = rpc
        self.timeouts = timeouts

    def read_turn(self, thread_id: str, turn_id: str) -> dict[str, Any] | None:
        """Read one exact turn snapshot without starting or resuming work."""
        params: dict[str, Any] = {
            "threadId": thread_id, "limit": 5,
            "sortDirection": "desc", "itemsView": "full",
        }
        deadline = time.monotonic() + min(45.0, self.timeouts.request)
        cursors: set[str] = set()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Codex exact turn lookup timed out: {turn_id}")
            result = self._rpc.request("thread/turns/list", params, timeout=remaining)
            turns = result.get("data") if isinstance(result.get("data"), list) else []
            for item in turns:
                if isinstance(item, dict) and str(item.get("id") or "") == turn_id:
                    return item
            cursor = str(result.get("nextCursor") or "")
            if not cursor:
                return None
            if cursor in cursors:
                raise CodexAppServerError("Codex turn pagination did not advance")
            cursors.add(cursor)
            params = {**params, "cursor": cursor}

    def read_recent_turns(self, thread_id: str) -> list[Any]:
        """The thread's five newest turns with their items, newest first."""

        result = self._rpc.request(
            "thread/turns/list",
            {
                "threadId": thread_id,
                "limit": 5,
                "sortDirection": "desc",
                "itemsView": "full",
            },
            timeout=min(45.0, self.timeouts.request),
        )
        return result.get("data") if isinstance(result.get("data"), list) else []

    def read_thread_status(self, thread_id: str) -> dict[str, Any]:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        result = self._rpc.request(
            "thread/read",
            {"threadId": safe_thread_id, "includeTurns": False},
            timeout=min(45.0, self.timeouts.request),
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        status = thread.get("status") if isinstance(thread, dict) else None
        return dict(status) if isinstance(status, dict) else {}

    def active_turn_id(self, thread_id: str) -> str:
        """The id of the thread's turn in progress, or ``""``."""

        turn = self.read_active_turn(thread_id)
        return str((turn or {}).get("id") or "")

    def read_active_turn(
        self, thread_id: str, *, include_items: bool = False,
    ) -> dict[str, Any] | None:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        result = self._rpc.request(
            "thread/turns/list",
            {
                "threadId": safe_thread_id,
                "limit": 1,
                "sortDirection": "desc",
                "itemsView": "full" if include_items else "summary",
            },
            timeout=min(45.0, self.timeouts.request),
        )
        turns = result.get("data") if isinstance(result.get("data"), list) else []
        turn = turns[0] if turns and isinstance(turns[0], dict) else None
        if not isinstance(turn, dict):
            return None
        status = str(turn.get("status") or "").strip().casefold()
        return turn if status in {"inprogress"} else None

    def read_task_complete_goal(self, thread_id: str) -> dict[str, Any] | None:
        result = self._rpc.request(
            "thread/goal/get",
            {"threadId": thread_id},
            timeout=min(45.0, self.timeouts.request),
        )
        goal = result.get("goal") if isinstance(result, dict) else None
        if not isinstance(goal, dict):
            return None
        status = str(goal.get("status") or "").strip().lower()
        return dict(goal) if status in TASK_COMPLETE_GOAL_STATUSES else None
