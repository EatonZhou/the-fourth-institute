"""Durable no-progress recovery budget for one Codex Execution."""

from __future__ import annotations

import threading
from typing import Any, Mapping

from .contracts import ExecutionControlSignal


MAX_NO_PROGRESS_RECOVERY_ATTEMPTS = 3


def _attempt_count(value: Any) -> int:
    try:
        count = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return MAX_NO_PROGRESS_RECOVERY_ATTEMPTS
    if count < 0:
        return MAX_NO_PROGRESS_RECOVERY_ATTEMPTS
    return min(MAX_NO_PROGRESS_RECOVERY_ATTEMPTS, count)


class CodexRecoveryExhausted(ExecutionControlSignal):
    error_code = "provider_recovery_exhausted"

    def __init__(self, reason: object) -> None:
        super().__init__(
            f"Codex recovery exhausted after {MAX_NO_PROGRESS_RECOVERY_ATTEMPTS} "
            f"attempts without new work: {str(reason)[:1000]}"
        )
        self.error_details = {
            "provider": "codex",
            "max_recovery_attempts": MAX_NO_PROGRESS_RECOVERY_ATTEMPTS,
        }


class CodexRecoveryBudget:
    """Queue claims and new turns never replenish this budget.

    Only new completed work on the currently accepted turn replenishes it.
    Retain that turn's item identities to reject replayed progress after a
    reconnect. No prompts, model text or tool results enter the checkpoint.
    """

    def __init__(self, checkpoint: Mapping[str, Any]) -> None:
        state = checkpoint.get("recovery_budget")
        if not isinstance(state, Mapping):
            state = {}
        self._attempts = _attempt_count(state.get("attempts"))
        self._turn_id = str(state.get("turn_id") or "")
        completed_items = state.get("completed_item_ids")
        self._completed_items = (
            {item for item in completed_items if isinstance(item, str) and item}
            if isinstance(completed_items, list) else set()
        )
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "attempts": self._attempts,
                "turn_id": self._turn_id,
                "completed_item_ids": sorted(self._completed_items),
            }

    def turn_started(self, turn_id: str) -> None:
        with self._lock:
            if turn_id and turn_id != self._turn_id:
                self._turn_id = turn_id
                self._completed_items.clear()

    def reserve_retry(self, reason: object) -> None:
        with self._lock:
            if self._attempts >= MAX_NO_PROGRESS_RECOVERY_ATTEMPTS:
                raise CodexRecoveryExhausted(reason)
            self._attempts += 1

    def observe(self, event: Mapping[str, Any]) -> bool:
        """Record completed work; return whether a spent budget was reset."""
        if event.get("method") != "item/completed":
            return False
        params = event.get("params")
        item = params.get("item") if isinstance(params, Mapping) else None
        if not isinstance(item, Mapping):
            return False
        item_id = item.get("id")
        turn_id = params.get("turnId")
        if (
            not isinstance(item_id, str) or not item_id
            or not isinstance(turn_id, str) or not turn_id
        ):
            return False
        kind = item.get("type")
        if item.get("error") or item.get("status") in (
            "failed", "cancelled", "interrupted",
        ):
            return False
        if kind == "agentMessage":
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                return False
        elif kind in (
            "commandExecution", "fileChange", "mcpToolCall",
            "dynamicToolCall", "imageGeneration",
        ):
            result = item.get("result")
            if (
                item.get("status") != "completed"
                or item.get("exitCode") not in (None, 0)
                or item.get("success") is False
                or (isinstance(result, Mapping) and result.get("isError") is True)
            ):
                return False
        elif kind not in ("webSearch", "imageView"):
            return False
        with self._lock:
            if turn_id != self._turn_id or item_id in self._completed_items:
                return False
            self._completed_items.add(item_id)
            replenished = self._attempts > 0
            self._attempts = 0
            # Healthy tool loops need no extra checkpoint per result. Before
            # any retry, reserve_retry's caller persists a snapshot including
            # all seen IDs, so reconnect replay still cannot renew the budget.
            return replenished
