"""Drain committed Effect recovery cleanup independently of graph activity."""
from __future__ import annotations

import threading

from . import lineage_effect_script_journal as journal
from .state_store import get_lineage_data_store


class EffectRecoveryWorker:
    def __init__(self, *, poll_seconds: float = 1.0):
        self.poll_seconds = max(0.05, poll_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""
        self._cursor: tuple[str, str, str] | None = None

    def drain_once(self) -> dict[str, bool]:
        try:
            tasks = get_lineage_data_store().effect_cleanup_tasks(after_key=self._cursor)
            result = {}
            for task in tasks:
                if self._stop.is_set():
                    break
                result[f"{task['lineage_id']}/{task['activation_key']}"] = journal.cleanup(task["lineage_id"], task["activation_key"])
                # Advance past failed jobs too; their durable tasks remain for
                # the next round instead of starving newer cleanup work.
                self._cursor = (task["created_at"], task["lineage_id"], task["activation_key"])
            if not tasks:
                self._cursor = None
            self.last_error = "" if all(result.values()) else "Effect recovery cleanup is pending"
            return result
        except Exception as exc:
            self.last_error = str(exc)
            return {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="effect-recovery-cleanup", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.drain_once()
            self._stop.wait(self.poll_seconds)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
