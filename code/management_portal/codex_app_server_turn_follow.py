"""Following an accepted Codex turn to its answer.

The follower waits for the turn's notifications, reconciles them with the
thread's durable turn snapshots, replays the items it missed, delivers the
controls Portal queues for the running turn and moves on to a successor turn
that Codex starts in the same thread.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .codex_app_server_protocol import (
    CodexAppServerError,
    CodexTurnConnectionLost,
    CodexTurnContinuationRequired,
    CodexTurnResult,
    TurnWaiter,
    codex_final_agent_response,
    collect_agent_message,
    collect_final_answer,
    is_final_answer,
    item_identity,
    terminal_turn_error,
)

if TYPE_CHECKING:
    from .codex_app_server_events import CodexActiveWork
    from .codex_app_server_subagents import CodexSubagentRouter
    from .codex_app_server_threads import CodexThreadReads
    from .codex_app_server_turn_controls import CodexTurnControls, TurnControlProvider, TurnControlResultCallback
    from .codex_user_input import CodexUserInputRequests


TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS = 2.0
TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS = 10.0
TURN_SUCCESSOR_RECONCILE_GRACE_SECONDS = 6.0
TURN_ACCEPTED_MISSING_GRACE_SECONDS = 15.0
TURN_CONTROL_POLL_INTERVAL_SECONDS = 0.25
# Item types a running snapshot reports before they carry a status.
UNSTATUSED_RUNNING_ITEM_TYPES = frozenset({"subAgentActivity", "webSearch", "imageView"})


class CodexTurnFollower:
    def __init__(
        self,
        work: CodexActiveWork,
        reads: CodexThreadReads,
        controls: CodexTurnControls,
        subagents: CodexSubagentRouter,
        user_inputs: CodexUserInputRequests,
    ) -> None:
        self._work = work
        self._reads = reads
        self._controls = controls
        self._subagents = subagents
        self._user_inputs = user_inputs
        self._state_lock = work.state_lock

    def follow(
        self,
        waiter: TurnWaiter,
        *,
        turn_started_callback: Callable[[str], None] | None,
        cancellation_requested: Callable[[], bool] | None,
        detach_requested: Callable[[], bool] | None,
        turn_control_provider: TurnControlProvider | None,
        turn_control_result_callback: TurnControlResultCallback | None,
        deadline: float | None = None,
    ) -> CodexTurnResult:
        """Observe an accepted turn, including native successor turns."""
        thread_id = waiter.thread_id
        has_controls = turn_control_provider is not None
        missing_since: float | None = None
        while True:
            if waiter.error:
                raise waiter.error
            self._work.detach_if_requested(thread_id, waiter.turn_id, detach_requested)
            if cancellation_requested and cancellation_requested():
                try:
                    self._controls.interrupt_turn(thread_id, waiter.turn_id)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Cancelled Codex turn %s of thread %s was not interrupted", waiter.turn_id, thread_id,
                        exc_info=True)
                raise InterruptedError(f"Codex turn cancelled for {thread_id}")
            if turn_control_provider is not None and waiter.turn_id:
                self._controls.deliver_turn_control(
                    thread_id=thread_id, waiter=waiter,
                    turn_control_provider=turn_control_provider,
                    turn_control_result_callback=turn_control_result_callback,
                    cancellation_requested=cancellation_requested,
                )
            if self._user_inputs.blocking(thread_id):
                started = time.monotonic()
                self.wait_for_turn_signal(waiter, has_controls)
                if deadline is not None:
                    deadline += time.monotonic() - started
                continue
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"Codex turn reconciliation timed out: {waiter.turn_id}")
            terminal = (waiter.completed or {}).get("turn")
            if not isinstance(terminal, dict):
                terminal = None
            if terminal is None and self.turn_reconcile_due(waiter):
                terminal, missing_since = self._reconcile(waiter, missing_since)
            if waiter.error:
                raise waiter.error
            if terminal is None:
                self.wait_for_turn_signal(waiter, has_controls)
                continue
            if str(terminal.get("status") or "") != "completed" or terminal.get("error"):
                raise terminal_turn_error(thread_id, terminal)
            if waiter.completion_deferred:
                terminal = self.canonical_turn(waiter, terminal)
                self.replay_completed_items(waiter, terminal)
                if waiter.unphased_items:
                    # Failed projection remains retryable by its native identity.
                    waiter.event.clear()
                    self.wait_for_turn_signal(waiter, has_controls)
                    continue
                waiter.completed = None
                waiter.completion_deferred = False
            terminal, response = self._final_response(waiter, terminal)
            if waiter.error:
                raise waiter.error
            self._work.detach_if_requested(thread_id, waiter.turn_id, detach_requested)
            if cancellation_requested and cancellation_requested():
                raise InterruptedError(f"Codex turn cancelled for {thread_id}")
            if not waiter.completed:
                self._report_completed(waiter, terminal)
            if response:
                return CodexTurnResult(
                    thread_id=thread_id, turn_id=str(terminal.get("id") or waiter.turn_id),
                    response=response, turn=terminal, token_usage=waiter.token_usage,
                )
            self._await_successor(
                waiter, str(terminal.get("id") or waiter.turn_id),
                cancellation_requested=cancellation_requested,
                detach_requested=detach_requested,
            )
            missing_since = None
            if turn_started_callback:
                turn_started_callback(waiter.turn_id)

    def _reconcile(
        self, waiter: TurnWaiter, missing_since: float | None,
    ) -> tuple[dict[str, Any] | None, float | None]:
        """The terminal turn the durable snapshots show, and since when the turn is missing.

        A turn that neither ends nor runs for ``TURN_ACCEPTED_MISSING_GRACE_SECONDS``
        lost its connection.
        """

        thread_id = waiter.thread_id
        terminal = None
        try:
            terminal = self.read_terminal_turn(waiter)
            if terminal is None:
                if waiter.terminal_goal is None:
                    waiter.terminal_goal = self._reads.read_task_complete_goal(thread_id)
                if waiter.terminal_goal is not None:
                    terminal = self.read_terminal_goal_successor(waiter)
            if terminal is None:
                active = self._reads.read_active_turn(thread_id)
                if active is not None and str(active.get("id") or "") == waiter.turn_id:
                    missing_since = None
                else:
                    self.raise_thread_system_error(
                        thread_id, waiter.turn_id, self._reads.read_thread_status(thread_id),
                    )
                    now = time.monotonic()
                    if missing_since is None:
                        missing_since = now
                    elif now - missing_since >= TURN_ACCEPTED_MISSING_GRACE_SECONDS:
                        raise CodexTurnConnectionLost(thread_id, waiter.turn_id)
        except (CodexTurnContinuationRequired, CodexTurnConnectionLost):
            raise
        except (CodexAppServerError, TimeoutError):
            if waiter.error:
                raise waiter.error
        return terminal, missing_since

    def _final_response(
        self, waiter: TurnWaiter, terminal: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """The completed turn and its answer, read again when the notification preceded it."""

        response = codex_final_agent_response(terminal, waiter.agent_messages)
        if not response and waiter.completed:
            # Completion notifications may precede the final item snapshot.
            snapshot = self.read_terminal_turn(waiter)
            if snapshot is not None:
                if str(snapshot.get("id") or "") != waiter.turn_id:
                    raise CodexAppServerError("Codex terminal snapshot does not match the requested turn")
                terminal = snapshot
                if str(terminal.get("status") or "") != "completed" or terminal.get("error"):
                    raise terminal_turn_error(waiter.thread_id, terminal)
                response = codex_final_agent_response(terminal, waiter.agent_messages)
        return terminal, response

    @staticmethod
    def _report_completed(waiter: TurnWaiter, terminal: dict[str, Any]) -> None:
        """Tell the observer of a completion that only the snapshot showed."""

        waiter.completed = {"threadId": waiter.thread_id, "turn": terminal}
        if waiter.event_callback:
            try:
                waiter.event_callback({"method": "turn/completed", "params": waiter.completed})
            except Exception:
                logging.getLogger(__name__).exception("Codex completed turn observation failed")

    def _await_successor(
        self,
        waiter: TurnWaiter,
        terminal_id: str,
        *,
        cancellation_requested: Callable[[], bool] | None,
        detach_requested: Callable[[], bool] | None,
    ) -> None:
        """Follow the turn Codex starts after a turn that ended without an answer."""

        thread_id = waiter.thread_id
        successor_deadline = time.monotonic() + TURN_SUCCESSOR_RECONCILE_GRACE_SECONDS
        successor = None
        while time.monotonic() < successor_deadline:
            self._work.detach_if_requested(thread_id, waiter.turn_id, detach_requested)
            if cancellation_requested and cancellation_requested():
                raise InterruptedError(f"Codex turn cancelled for {thread_id}")
            candidate = self._reads.read_active_turn(thread_id)
            if candidate and str(candidate.get("id") or "") not in {"", terminal_id}:
                successor = candidate
                break
            time.sleep(0.05)
        if successor is None:
            raise CodexTurnContinuationRequired(thread_id, terminal_id, "missing_final_response")
        waiter.turn_id = str(successor["id"])
        waiter.completed = None
        waiter.terminal_goal = None
        waiter.next_reconcile_at = 0
        waiter.event.clear()

    @staticmethod
    def wait_for_turn_signal(waiter: TurnWaiter, has_controls: bool) -> bool:
        return waiter.event.wait(
            TURN_CONTROL_POLL_INTERVAL_SECONDS if has_controls
            else TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS
        )

    @staticmethod
    def schedule_reconcile(waiter: TurnWaiter) -> None:
        """Let a just accepted turn run a reconciliation interval before the first snapshot read."""

        waiter.next_reconcile_at = time.monotonic() + TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS

    @staticmethod
    def turn_reconcile_due(waiter: TurnWaiter) -> bool:
        now = time.monotonic()
        if not waiter.event.is_set() and now < waiter.next_reconcile_at:
            return False
        waiter.next_reconcile_at = now + TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS
        return True

    @staticmethod
    def raise_thread_system_error(
        thread_id: str,
        turn_id: str,
        status: Mapping[str, Any],
    ) -> None:
        if (
            str(status.get("type") or "").strip().casefold()
            != "systemerror"
        ):
            return
        raise CodexTurnContinuationRequired(
            thread_id,
            turn_id,
            "provider_system_error",
        )

    def read_terminal_turn(
        self,
        waiter: TurnWaiter,
    ) -> dict[str, Any] | None:
        if not waiter.turn_id:
            return None
        turn = self._reads.read_turn(waiter.thread_id, waiter.turn_id)
        if turn is None:
            return None
        # Active snapshots can contain reconstructed display IDs too. Normalize
        # message identity before replay so polling and live notifications share
        # the same deduplication key throughout the turn.
        turn = self.canonical_turn(waiter, turn)
        self.replay_completed_items(waiter, turn)
        if str(turn.get("status") or "") == "inProgress":
            return None
        return turn

    def canonical_turn(self, waiter: TurnWaiter, turn: dict[str, Any]) -> dict[str, Any]:
        if waiter.turn_snapshot_transform is None:
            return turn
        # Completion notifications sometimes omit items. Retain original pending
        # identities as candidates for the existing exact-turn history resolver.
        with self._state_lock:
            pending = list(waiter.unphased_items.values())
        if pending:
            items = list(turn.get("items") or [])
            # Snapshot message IDs may be reconstructed display IDs. Do not
            # count those and the pending native item as two expected replies.
            if not any(isinstance(item, dict) and item.get("type") == "agentMessage" for item in items):
                items.extend(pending)
            turn = {**turn, "items": items}
        return waiter.turn_snapshot_transform(turn)

    def read_terminal_goal_successor(
        self,
        waiter: TurnWaiter,
    ) -> dict[str, Any] | None:
        if not waiter.turn_id or waiter.terminal_goal is None:
            return None
        turns = self._reads.read_recent_turns(waiter.thread_id)
        try:
            expected_identity = uuid.UUID(waiter.turn_id).int
        except (AttributeError, TypeError, ValueError):
            return None
        for item in turns:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") != "completed":
                continue
            try:
                candidate_identity = uuid.UUID(str(item.get("id") or "")).int
            except (AttributeError, TypeError, ValueError):
                continue
            if candidate_identity > expected_identity:
                return (waiter.turn_snapshot_transform(item)
                        if waiter.turn_snapshot_transform is not None else item)
        return None

    def replay_completed_items(
        self,
        waiter: TurnWaiter,
        turn: Mapping[str, Any],
    ) -> None:
        running = str(turn.get("status") or "").casefold() in {"inprogress"}
        for item in turn.get("items") or []:
            if not isinstance(item, dict):
                continue
            if is_final_answer(item):
                collect_final_answer(waiter, item)
            if waiter.event_callback is None:
                continue
            if running and not _finished_in_running_turn(item):
                continue
            item_id = item_identity(item)
            with self._state_lock:
                if item_id in waiter.completed_item_ids:
                    waiter.unphased_items.pop(item_id, None)
                    continue
                waiter.completed_item_ids.add(item_id)
            event = {
                "method": "item/completed",
                "origin": "turn_snapshot",
                "params": {
                    "threadId": waiter.thread_id,
                    "turnId": waiter.turn_id,
                    "item": dict(item),
                },
            }
            try:
                self._subagents.route(event["method"], event["params"])
                waiter.event_callback(event)
            except Exception as error:
                with self._state_lock:
                    waiter.completed_item_ids.discard(item_id)
                logging.getLogger(__name__).warning(
                    "Provider item replay failed: thread=%s turn=%s item=%s error_type=%s",
                    waiter.thread_id, waiter.turn_id, item_id, type(error).__name__,
                )
                continue
            with self._state_lock:
                waiter.unphased_items.pop(item_id, None)
            if collect_agent_message(waiter, item) and is_final_answer(item):
                collect_final_answer(waiter, item)


def _finished_in_running_turn(item: Mapping[str, Any]) -> bool:
    """Whether an item of a running turn's snapshot is complete enough to deliver."""

    item_status = str(item.get("status") or "").casefold()
    # Native agentMessage snapshots have no status. Started items
    # have empty text; completed messages carry their full text.
    # Do not mark an empty placeholder or explicitly running item
    # as delivered, or its later completed text would be deduped.
    completed_message = (
        item.get("type") == "agentMessage"
        and not item_status
        and bool(str(item.get("text") or "").strip())
    )
    return (
        completed_message
        or item.get("type") in UNSTATUSED_RUNNING_ITEM_TYPES
        or item_status in {"completed", "failed", "interrupted"}
    )
