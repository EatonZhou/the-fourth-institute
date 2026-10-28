"""Compacting a Codex thread on request (``thread/compact/start``).

The compaction is followed until Codex commits the compacted context and
its technical compaction turn has ended, within a total deadline and a
deadline for progress.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from .codex_app_server_protocol import CodexAppServerError, CodexRequestTimeouts, CompactWaiter

if TYPE_CHECKING:
    from .codex_app_server_events import CodexActiveWork
    from .codex_app_server_rpc import CodexRpcConnection
    from .codex_app_server_threads import CodexLoadedThreads, CodexThreadReads


DEFAULT_COMPACT_TOTAL_TIMEOUT_SECONDS = 1800.0
DEFAULT_COMPACT_NO_PROGRESS_TIMEOUT_SECONDS = 900.0
DEFAULT_COMPACT_STATUS_POLL_INTERVAL_SECONDS = 5.0


def _bounded(value: float | None, default: float) -> float:
    return max(0.01, float(default if value is None else value))


class CompactionProgress:
    """The deadlines of one compaction and the Provider state it last saw."""

    def __init__(
        self,
        waiter: CompactWaiter,
        reads: CodexThreadReads,
        *,
        total_timeout: float,
        no_progress_timeout: float,
        cancellation_requested: Callable[[], bool] | None,
    ) -> None:
        self.waiter = waiter
        self._reads = reads
        self.total_timeout = total_timeout
        self.no_progress_timeout = no_progress_timeout
        self._cancellation_requested = cancellation_requested
        self.total_deadline = time.monotonic() + total_timeout
        self._last_state = ("", "")

    def check_cancelled(self) -> None:
        if self._cancellation_requested and self._cancellation_requested():
            raise InterruptedError(
                f"Codex compact cancelled for thread {self.waiter.thread_id}"
            )

    def check_total_deadline(self) -> None:
        now = time.monotonic()
        if now >= self.total_deadline:
            raise TimeoutError(
                f"Codex compact did not finish within "
                f"{self.total_timeout:g} seconds for thread {self.waiter.thread_id}"
            )

    def check_no_progress_deadline(self) -> None:
        now = time.monotonic()
        if now - self.waiter.last_progress_at >= self.no_progress_timeout:
            raise TimeoutError(
                f"Codex compact made no progress for "
                f"{self.no_progress_timeout:g} seconds for thread {self.waiter.thread_id}"
            )

    def observe_provider_state(self) -> dict[str, Any] | None:
        """The thread's active turn; a changed turn or status is progress."""

        active_turn = self._reads.read_active_turn(self.waiter.thread_id)
        state = (
            str((active_turn or {}).get("id") or ""),
            str((active_turn or {}).get("status") or "").casefold(),
        )
        if state != self._last_state:
            self._last_state = state
            self.waiter.last_progress_at = time.monotonic()
        return active_turn


class CodexCompactionRunner:
    def __init__(
        self,
        rpc: CodexRpcConnection,
        work: CodexActiveWork,
        threads: CodexLoadedThreads,
        reads: CodexThreadReads,
        timeouts: CodexRequestTimeouts,
    ) -> None:
        self._rpc = rpc
        self._work = work
        self._threads = threads
        self._reads = reads
        self._state_lock = work.state_lock
        self.timeouts = timeouts

    def compact_thread(
        self,
        thread_id: str,
        *,
        config: dict[str, Any] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
        no_progress_timeout_seconds: float | None = None,
        status_poll_interval_seconds: float | None = None,
    ) -> dict[str, Any]:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        if not self._threads.is_loaded(safe_thread_id):
            self._rpc.request(
                "thread/resume",
                {"threadId": safe_thread_id, "excludeTurns": True, **({"config": config} if config is not None else {})},
                timeout=self.timeouts.lifecycle,
                cancellation_requested=cancellation_requested,
            )
            self._threads.mark_loaded(safe_thread_id)
        waiter = CompactWaiter(thread_id=safe_thread_id)
        with self._state_lock:
            if safe_thread_id in self._work.compactions:
                raise CodexAppServerError(f"Codex thread {safe_thread_id} is already compacting")
            self._work.compactions[safe_thread_id] = waiter
            self._threads.set_busy(safe_thread_id)
        try:
            total_timeout = _bounded(timeout_seconds, DEFAULT_COMPACT_TOTAL_TIMEOUT_SECONDS)
            progress = CompactionProgress(
                waiter, self._reads,
                total_timeout=total_timeout,
                no_progress_timeout=_bounded(
                    no_progress_timeout_seconds, DEFAULT_COMPACT_NO_PROGRESS_TIMEOUT_SECONDS,
                ),
                cancellation_requested=cancellation_requested,
            )
            poll_interval = _bounded(status_poll_interval_seconds, DEFAULT_COMPACT_STATUS_POLL_INTERVAL_SECONDS)
            self._rpc.request(
                "thread/compact/start",
                {"threadId": safe_thread_id},
                timeout=total_timeout,
                cancellation_requested=cancellation_requested,
            )
            # The accepted RPC is itself concrete progress. Without this
            # reset, time spent establishing a long-running compact would be
            # incorrectly charged to the following no-progress window.
            waiter.last_progress_at = time.monotonic()
            while not waiter.event.wait(poll_interval):
                progress.check_cancelled()
                progress.check_total_deadline()
                progress.observe_provider_state()
                progress.check_no_progress_deadline()
            if waiter.error:
                raise waiter.error
            # `thread/compacted` reports that the compacted context has been
            # committed, but the provider's technical compaction turn can
            # remain active briefly afterwards.  Do not expose the thread to
            # the next turn until that turn is terminal; otherwise its delayed
            # turn notifications can be consumed by the next turn's waiter.
            while progress.observe_provider_state() is not None:
                progress.check_cancelled()
                progress.check_total_deadline()
                progress.check_no_progress_deadline()
                waiter.event.wait(poll_interval)
            return dict(waiter.token_usage)
        finally:
            with self._state_lock:
                self._work.compactions.pop(safe_thread_id, None)
                self._threads.clear_busy(safe_thread_id)
                self._threads.touch(safe_thread_id)
