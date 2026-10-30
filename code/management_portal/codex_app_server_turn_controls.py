"""Requests that act on a running Codex turn.

Interrupting, steering and changing the settings of a turn, stopping one of
its subagents, and delivering the controls Portal queues for a turn while it
runs.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable

from .codex_app_server_protocol import CodexRequestTimeouts, PendingRequest, TurnWaiter

if TYPE_CHECKING:
    from .codex_app_server_events import CodexActiveWork
    from .codex_app_server_rpc import CodexRpcConnection
    from .codex_app_server_subagents import CodexSubagentRouter
    from .codex_app_server_threads import CodexThreadReads
    from .codex_user_input import CodexUserInputRequests


TurnControlProvider = Callable[[], "dict[str, Any] | None"]
TurnControlResultCallback = Callable[[dict[str, Any], bool, str], None]


class CodexTurnControls:
    def __init__(
        self,
        rpc: CodexRpcConnection,
        work: CodexActiveWork,
        reads: CodexThreadReads,
        subagents: CodexSubagentRouter,
        user_inputs: CodexUserInputRequests,
        timeouts: CodexRequestTimeouts,
    ) -> None:
        self._rpc = rpc
        self._work = work
        self._reads = reads
        self._subagents = subagents
        self._user_inputs = user_inputs
        self._state_lock: threading.RLock = work.state_lock
        self.timeouts = timeouts

    def interrupt_turn(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        safe_thread_id = str(thread_id or "").strip()
        safe_turn_id = str(turn_id or "").strip()
        if not safe_thread_id or not safe_turn_id:
            raise ValueError("thread_id and turn_id are required")
        params = {"threadId": safe_thread_id, "turnId": safe_turn_id}
        pending = None
        owns_request = False
        with self._state_lock:
            waiter = self._work.turns.get(safe_thread_id)
            if waiter is not None and waiter.turn_id == safe_turn_id:
                pending = waiter.interrupt_requests.get(safe_turn_id)
                if pending is None:
                    if _turn_ended(waiter, safe_thread_id, safe_turn_id):
                        return {}
                    pending = PendingRequest()
                    waiter.interrupt_requests[safe_turn_id] = pending
                    owns_request = True
        if pending is None:
            return self._rpc.request("turn/interrupt", params)
        if owns_request:
            try:
                pending.result = self._rpc.request("turn/interrupt", params)
            except BaseException as exc:
                pending.error = exc
            finally:
                pending.event.set()
        else:
            # Control and turn cleanup share one native request. Even after a
            # terminal notification, let that request return before releasing
            # the old Worker claim; never hold the cross-thread state lock.
            pending.event.wait()
        if pending.error is not None:
            # Reconciliation/recovery owns an ambiguous result, not repeated
            # interrupts from cleanup within this same accepted-turn waiter.
            raise pending.error
        return dict(pending.result or {})

    def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        turn_input: list[dict[str, Any]],
        *,
        additional_context: dict[str, Any] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        safe_thread_id = str(thread_id or "").strip()
        safe_turn_id = str(turn_id or "").strip()
        if not safe_thread_id or not safe_turn_id:
            raise ValueError("thread_id and turn_id are required")
        if not isinstance(turn_input, list) or not turn_input:
            raise ValueError("turn steer input required")
        params: dict[str, Any] = {
            "threadId": safe_thread_id,
            "expectedTurnId": safe_turn_id,
            "input": list(turn_input),
        }
        if additional_context:
            params["additionalContext"] = dict(additional_context)
        return self._rpc.request(
            "turn/steer",
            params,
            timeout=self.timeouts.lifecycle,
            cancellation_requested=cancellation_requested,
        )

    def update_turn_settings(
        self,
        thread_id: str,
        turn_id: str,
        *,
        service_tier: str,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Publish a Provider-native speed change to one running turn."""

        safe_thread_id = str(thread_id or "").strip()
        safe_turn_id = str(turn_id or "").strip()
        if not safe_thread_id or not safe_turn_id:
            raise ValueError("thread_id and turn_id are required")
        safe_service_tier = str(service_tier or "").strip()
        if safe_service_tier not in {"", "priority"}:
            raise ValueError("unsupported turn service tier")
        return self._rpc.request(
            "turn/settings/update",
            {
                "threadId": safe_thread_id,
                "turnId": safe_turn_id,
                "serviceTier": safe_service_tier or None,
            },
            timeout=self.timeouts.lifecycle,
            cancellation_requested=cancellation_requested,
        )

    def stop_subagent(self, thread_id: str, child: str) -> tuple[bool, str]:
        """Interrupt the turn of one subagent thread of ``thread_id``; the thread's own turn goes on.

        A Codex subagent's card names its thread (``codex_subagent_cards``).
        """

        known, turn_id = self._subagents.running_turn(thread_id, child)
        if not known:
            return False, "unknown_subagent"
        turn_id = turn_id or self._reads.active_turn_id(child)
        if not turn_id:
            return False, "subagent_not_running"
        self.interrupt_turn(child, turn_id)
        return True, ""

    def deliver_turn_control(
        self,
        *,
        thread_id: str,
        waiter: TurnWaiter,
        turn_control_provider: TurnControlProvider,
        turn_control_result_callback: TurnControlResultCallback | None,
        cancellation_requested: Callable[[], bool] | None,
    ) -> None:
        if waiter.final_answer_seen or waiter.completed is not None:
            return None
        control: dict[str, Any] | None = None
        try:
            candidate = turn_control_provider()
            control = candidate if isinstance(candidate, dict) else None
        except Exception as exc:
            if turn_control_result_callback is not None:
                try:
                    turn_control_result_callback(
                        {},
                        False,
                        type(exc).__name__,
                    )
                except Exception:
                    logging.getLogger(__name__).exception("Codex turn control result callback failed")
            return None
        if control is None:
            return None
        if waiter.final_answer_seen or waiter.completed is not None:
            return None
        applied = False
        error = ""
        try:
            applied, error = self._apply_control(thread_id, waiter, control, cancellation_requested)
        except Exception as exc:
            error = type(exc).__name__
        if turn_control_result_callback is not None:
            try:
                turn_control_result_callback(
                    control,
                    applied,
                    error,
                )
            except Exception:
                logging.getLogger(__name__).exception("Codex turn control result callback failed")

    def _apply_control(
        self,
        thread_id: str,
        waiter: TurnWaiter,
        control: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None,
    ) -> tuple[bool, str]:
        """Whether the turn took the control, and why not."""

        expected_turn_id = str(control.get("expected_turn_id") or "")
        if expected_turn_id and expected_turn_id != waiter.turn_id:
            raise ValueError("active provider turn changed")
        action = str(control.get("action") or "steer").strip()
        response_input = control.get("input") or []
        if action == "update_settings":
            settings = control.get("settings")
            if not isinstance(settings, dict):
                raise ValueError("turn settings control requires settings")
            response = self.update_turn_settings(
                thread_id,
                waiter.turn_id,
                service_tier=str(settings.get("service_tier") or ""),
                cancellation_requested=cancellation_requested,
            )
            status = str(response.get("status") or "").strip()
            applied = status == "applied"
            return applied, "" if applied else status or "targetUnavailable"
        if len(response_input) == 1 and response_input[0].get("type") == "user_input_response":
            self._user_inputs.respond(thread_id, waiter.turn_id, response_input[0])
            return True, ""
        if len(response_input) == 1 and response_input[0].get("type") == "subagent_stop":
            return self.stop_subagent(thread_id, str(response_input[0].get("tool_use_id") or ""))
        if action != "steer":
            raise ValueError("unsupported turn control action")
        control_input = control.get("input")
        if not isinstance(control_input, list):
            raise ValueError("turn control input must be an array")
        additional_context = control.get("additional_context")
        self.steer_turn(
            thread_id,
            waiter.turn_id,
            control_input,
            additional_context=(
                additional_context
                if isinstance(additional_context, dict)
                else None
            ),
            cancellation_requested=cancellation_requested,
        )
        return True, ""


def _turn_ended(waiter: TurnWaiter, thread_id: str, turn_id: str) -> bool:
    """Whether the waiter already saw the turn's terminal notification."""

    completed = waiter.completed if isinstance(waiter.completed, dict) else {}
    turn = completed.get("turn")
    return (
        isinstance(turn, dict)
        and str(completed.get("threadId") or thread_id) == thread_id
        and str(turn.get("id") or "") == turn_id
        and str(turn.get("status") or "").strip().casefold()
        in {"completed", "interrupted", "failed"}
    )
