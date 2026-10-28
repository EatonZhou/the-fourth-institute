"""What a Codex app-server connection does with the server's messages.

``CodexActiveWork`` lists the turns, goals and compactions the connection
follows.  ``CodexEventRouter`` hands each notification to the one that owns
its thread and answers the server's requests: dynamic tool calls, user input
and approvals.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from .codex_app_server_protocol import (
    TASK_COMPLETE_GOAL_STATUSES,
    CodexTurnContinuationRequired,
    CodexTurnDetached,
    CompactWaiter,
    GoalWaiter,
    TurnWaiter,
    collect_agent_message,
    collect_final_answer,
    collect_token_usage,
    fail_waiter,
    is_context_compaction_item,
    is_final_answer,
    notification_turn_id,
    terminal_turn_error,
)

if TYPE_CHECKING:
    from management_common.provider_turn_artifacts import TurnArtifacts

    from .codex_app_server_rpc import CodexRpcConnection
    from .codex_app_server_subagents import CodexSubagentRouter
    from .codex_app_server_threads import CodexLoadedThreads
    from .codex_user_input import CodexUserInputRequests


DynamicToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

USER_INPUT_REQUEST_METHODS = frozenset({
    "item/tool/requestUserInput",
    "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
    "item/permissions/requestApproval", "mcpServer/elicitation/request",
})


class CodexActiveWork:
    """The turns, goals and compactions this connection follows, by thread.

    Every collection is guarded by the client's state lock.
    """

    def __init__(self, state_lock: threading.RLock) -> None:
        self.state_lock = state_lock
        self.turns: dict[str, TurnWaiter] = {}
        self.goals: dict[str, GoalWaiter] = {}
        self.compactions: dict[str, CompactWaiter] = {}
        self.dynamic_tool_handlers: dict[str, DynamicToolHandler] = {}
        self.active_dynamic_calls: dict[str, int] = {}
        # Threads whose follower is leaving the accepted turn to the next one.
        self.detaching_threads: set[str] = set()

    def runs_turn(self, thread_id: str) -> bool:
        """Whether a turn or a goal of this connection runs on the thread."""

        with self.state_lock:
            return thread_id in self.turns or thread_id in self.goals

    def follows_any(self, thread_id: str) -> bool:
        with self.state_lock:
            return thread_id in self.turns or thread_id in self.goals or thread_id in self.compactions

    def roots(self) -> dict[str, TurnWaiter | GoalWaiter]:
        with self.state_lock:
            return {**self.goals, **self.turns}

    def waiters(self) -> tuple[list[TurnWaiter], list[GoalWaiter], list[CompactWaiter]]:
        with self.state_lock:
            return list(self.turns.values()), list(self.goals.values()), list(self.compactions.values())

    def detach_if_requested(
        self, thread_id: str, turn_id: str,
        detach_requested: Callable[[], bool] | None,
    ) -> None:
        if detach_requested is None or not detach_requested():
            return
        with self.state_lock:
            self.detaching_threads.add(thread_id)
            if self.active_dynamic_calls.get(thread_id, 0):
                return  # A call already started here must reply before release.
        raise CodexTurnDetached(thread_id, turn_id)


class CodexEventRouter:
    def __init__(
        self,
        rpc: CodexRpcConnection,
        work: CodexActiveWork,
        threads: CodexLoadedThreads,
        subagents: CodexSubagentRouter,
        user_inputs: CodexUserInputRequests,
        turn_artifacts: TurnArtifacts,
    ) -> None:
        self._rpc = rpc
        self._work = work
        self._threads = threads
        self._subagents = subagents
        self._user_inputs = user_inputs
        self._turn_artifacts = turn_artifacts
        self._state_lock = work.state_lock

    def answer_server_request(
        self,
        request_id: object,
        method: str,
        raw_params: object = None,
    ) -> None:
        params = raw_params if isinstance(raw_params, dict) else {}
        # App-server sends a thread's requests to every client subscribed to
        # it and takes the first answer.  A client that keeps a thread loaded
        # from an earlier turn while another Worker runs the current turn must
        # leave the request to that Worker: answering it with an error would
        # fail the running turn's tool call.
        thread_id = str(params.get("threadId") or "")
        runs_turn = self._work.runs_turn(thread_id)
        if method == "item/tool/call":
            self._answer_tool_call(request_id, thread_id, params, runs_turn)
        elif method in USER_INPUT_REQUEST_METHODS:
            if runs_turn:
                self._accept_user_input(request_id, method, thread_id, params)
        else:
            self._rpc.send({"id": request_id, "error": {"code": -32601, "message": f"Unsupported client request: {method}"}})

    def _answer_tool_call(
        self, request_id: object, thread_id: str, params: dict[str, Any], runs_turn: bool,
    ) -> None:
        with self._state_lock:
            if thread_id in self._work.detaching_threads:
                # App-server owns the unanswered request. The next follower
                # must receive it; never send an invented cancellation result.
                return
            handler = self._work.dynamic_tool_handlers.get(thread_id)
            if handler is not None:
                self._work.active_dynamic_calls[thread_id] = self._work.active_dynamic_calls.get(thread_id, 0) + 1
        if handler is None and not runs_turn:
            return
        if handler is None:
            self._rpc.send({
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": (
                        "No dynamic tool dispatcher is registered for "
                        f"thread {thread_id or '(unknown)'}"
                    ),
                },
            })
            return
        threading.Thread(
            target=self.run_dynamic_tool_request,
            args=(request_id, handler, params),
            name="portal-codex-dynamic-tool",
            daemon=True,
        ).start()

    def _accept_user_input(
        self, request_id: object, method: str, thread_id: str, params: dict[str, Any],
    ) -> None:
        try:
            waiter = self._work.turns.get(thread_id) or self._work.goals.get(thread_id)
            if waiter is None or waiter.event_callback is None:
                raise ValueError("this client has no user input surface for the requested thread")
            native_input = method == "item/tool/requestUserInput"
            if method == "mcpServer/elicitation/request":
                params = {**params, "turnId": params.get("turnId") or getattr(waiter, "turn_id", ""),
                          "itemId": "mcp-elicitation:" + str(request_id)}
            self._user_inputs.accept(request_id, params, "" if native_input else method)
        except ValueError as exc:
            self._rpc.send({"id": request_id, "error": {"code": -32602, "message": str(exc)}})

    def run_dynamic_tool_request(
        self,
        request_id: object,
        handler: DynamicToolHandler,
        params: dict[str, Any],
    ) -> None:
        try:
            result = handler(dict(params))
            if not isinstance(result, dict):
                raise TypeError("Dynamic tool dispatcher must return an object")
            self._rpc.send({"id": request_id, "result": result})
        except Exception as exc:
            self._rpc.send({
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": f"Dynamic tool dispatch failed: {exc}",
                },
            })
        finally:
            thread_id = str(params.get("threadId") or "")
            with self._state_lock:
                remaining = self._work.active_dynamic_calls.get(thread_id, 0) - 1
                if remaining > 0:
                    self._work.active_dynamic_calls[thread_id] = remaining
                else:
                    self._work.active_dynamic_calls.pop(thread_id, None)

    def dispatch_notification(self, method: str, raw_params: object) -> None:
        params = raw_params if isinstance(raw_params, dict) else {}
        if self._subagents.route(method, params):
            return
        thread_id = str(params.get("threadId") or "")
        if not thread_id:
            return
        status = params.get("status") if isinstance(params.get("status"), dict) else {}
        thread_unloaded = method == "thread/closed" or (
            method == "thread/status/changed" and status.get("type") == "notLoaded"
        )
        with self._state_lock:
            pending_waiter = self._work.turns.get(thread_id)
            if pending_waiter is not None and not pending_waiter.turn_id and not thread_unloaded:
                pending_waiter.pending_notifications.append((method, params))
                return
        if thread_id in self._work.turns:
            artifacts = self._turn_artifacts.observe(method, params)
            if artifacts is not None:
                self.dispatch_notification("item/completed", artifacts)
        if method == "serverRequest/resolved":
            if params.get("requestId") is not None:
                self._user_inputs.expire(thread_id, wire_id=params["requestId"])
            return
        if method == "turn/completed":
            turn = params.get("turn") or {}
            self._user_inputs.expire(thread_id, turn_id=str(turn.get("id") or params.get("turnId") or ""))
        if thread_unloaded:
            self._user_inputs.expire(thread_id)
            self._threads.forget(thread_id)
        notification_error = self._notification_error(method, thread_id, params)
        with self._state_lock:
            waiter = self._work.turns.get(thread_id)
            goal_waiter = self._work.goals.get(thread_id)
            compact_waiter = self._work.compactions.get(thread_id)
            self._threads.touch(thread_id)
        if method == "item/started" and (
            goal_waiter is not None or (
                waiter is not None and (not waiter.turn_id or waiter.turn_id == params.get("turnId"))
            )
        ):
            self._user_inputs.observe_file_change(params)
        if compact_waiter is not None:
            self._observe_compaction(compact_waiter, method, params, notification_error)
        if goal_waiter is not None:
            self._observe_goal(goal_waiter, method, params, notification_error)
        if waiter is not None:
            self._observe_turn(thread_id, waiter, method, params, notification_error)

    @staticmethod
    def _notification_error(method: str, thread_id: str, params: dict[str, Any]) -> BaseException | None:
        """The failure an ``error`` notification reports, when it ends the work."""

        if method != "error":
            return None
        error = terminal_turn_error(thread_id, {
            "id": params.get("turnId") or "",
            "status": "failed",
            "error": params.get("error"),
        })
        # Explicit rejection takes precedence over transport retry hints.
        if (
            params.get("willRetry") is False
            or getattr(error, "error_details", {}).get("retryable") is False
        ):
            return error
        return None

    @staticmethod
    def _observe_compaction(
        waiter: CompactWaiter, method: str, params: dict[str, Any], error: BaseException | None,
    ) -> None:
        if error is not None:
            fail_waiter(waiter, error)
        waiter.last_progress_at = time.monotonic()
        if method == "thread/tokenUsage/updated":
            collect_token_usage(waiter, params.get("tokenUsage"))
        compact_completed = method == "thread/compacted"
        if method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            compact_completed = is_context_compaction_item(item)
        if compact_completed:
            waiter.event.set()

    @staticmethod
    def _observe_goal(
        waiter: GoalWaiter, method: str, params: dict[str, Any], error: BaseException | None,
    ) -> None:
        if error is not None:
            fail_waiter(waiter, error)
        turn_id = notification_turn_id(params)
        if turn_id:
            waiter.turn_id = turn_id
        if waiter.event_callback:
            try:
                waiter.event_callback({"method": method, "params": params})
            except Exception:
                logging.getLogger(__name__).exception("Codex goal event observation failed")
        if method == "thread/tokenUsage/updated":
            collect_token_usage(waiter, params.get("tokenUsage"))
        elif method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            collect_agent_message(waiter, item)
        elif method == "thread/goal/updated":
            goal = params.get("goal")
            if isinstance(goal, dict):
                waiter.goal = dict(goal)
                if str(goal.get("status") or "") != "active":
                    waiter.event.set()
        elif method == "thread/goal/cleared":
            waiter.goal = None
            waiter.event.set()

    def _observe_turn(
        self, thread_id: str, waiter: TurnWaiter, method: str, params: dict[str, Any],
        error: BaseException | None,
    ) -> None:
        turn_id = notification_turn_id(params)
        if (
            waiter.turn_id
            and turn_id
            and waiter.turn_id != turn_id
            and method != "thread/goal/updated"
        ):
            return
        item_id = ""
        if method in {"item/started", "item/completed"}:
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if is_final_answer(item):
                waiter.final_answer_seen = True
        if method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            item_id = str(item.get("id") or "").strip()
            # Inline review can complete an outer AgentMessage without phase.
            # Resolve it from the same durable turn before immutable fan-out;
            # persisting it now as commentary creates a second synthetic Final.
            if (waiter.turn_snapshot_transform is not None and waiter.event_callback is not None
                    and item.get("type") == "agentMessage"
                    and not str(item.get("phase") or "").strip()
                    and (str(item.get("text") or "").strip() or item.get("questions"))):
                with self._state_lock:
                    if item_id not in waiter.completed_item_ids:
                        waiter.unphased_items[item_id] = dict(item)
                        waiter.next_reconcile_at = 0
                return
            if item_id:
                with self._state_lock:
                    if item_id in waiter.completed_item_ids:
                        return
                    waiter.completed_item_ids.add(item_id)
        if method == "turn/completed" and waiter.unphased_items:
            # The reader must not block on history or acknowledge an unverified
            # final response. The accepted-turn follower performs reconciliation.
            waiter.completion_deferred = True
            waiter.completed = params
            waiter.event.set()
            return
        if method == "turn/completed" and waiter.final_answer_items:
            params = self._with_final_answers(waiter, params)
        if waiter.event_callback:
            try:
                waiter.event_callback({"method": method, "params": params})
            except Exception as callback_error:
                logging.getLogger(__name__).warning(
                    "Provider event projection failed: thread=%s turn=%s method=%s error_type=%s",
                    thread_id, turn_id, method, type(callback_error).__name__,
                )
                if method == "item/completed":
                    with self._state_lock:
                        waiter.completed_item_ids.discard(item_id)
                    return
        self._record_turn_notification(waiter, method, params, error)

    @staticmethod
    def _with_final_answers(waiter: TurnWaiter, params: dict[str, Any]) -> dict[str, Any]:
        """A completion that omits the turn's final answers carries the ones streamed."""

        completed_turn = params.get("turn") or {}
        if any(isinstance(item, dict) and item.get("phase") == "final_answer"
               for item in completed_turn.get("items") or []):
            return params
        return {**params, "turn": {**completed_turn, "items": [
            *(completed_turn.get("items") or []), *waiter.final_answer_items.values(),
        ]}}

    @staticmethod
    def _record_turn_notification(
        waiter: TurnWaiter, method: str, params: dict[str, Any], error: BaseException | None,
    ) -> None:
        if method == "error":
            if error is not None:
                fail_waiter(waiter, error)
        elif method == "thread/status/changed":
            status = params.get("status") if isinstance(params.get("status"), dict) else {}
            waiter.thread_idle = str(status.get("type") or "") == "idle"
            if (
                str(status.get("type") or "").strip().casefold()
                == "systemerror"
                and waiter.error is None
            ):
                waiter.error = CodexTurnContinuationRequired(
                    waiter.thread_id,
                    waiter.turn_id,
                    "provider_system_error",
                )
                waiter.event.set()
        elif method == "thread/goal/updated":
            goal = params.get("goal")
            if (
                isinstance(goal, dict)
                and str(goal.get("status") or "").strip().lower()
                in TASK_COMPLETE_GOAL_STATUSES
            ):
                waiter.terminal_goal = dict(goal)
        elif method == "thread/tokenUsage/updated":
            collect_token_usage(waiter, params.get("tokenUsage"))
        elif method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if collect_agent_message(waiter, item) and is_final_answer(item):
                collect_final_answer(waiter, item)
        elif method == "turn/completed":
            waiter.completed = params
            waiter.event.set()
