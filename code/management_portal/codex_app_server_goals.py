"""Native Codex thread goals.

A goal command sets, resumes, pauses, clears or observes the thread's goal;
an active goal is followed until Codex reports it done, blocked or limited.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from .codex_app_server_protocol import CodexAppServerError, CodexGoalResult, CodexRequestTimeouts, GoalWaiter

if TYPE_CHECKING:
    from .codex_app_server_events import CodexActiveWork
    from .codex_app_server_rpc import CodexRpcConnection
    from .codex_app_server_threads import CodexLoadedThreads
    from .codex_app_server_turn_controls import CodexTurnControls


GOAL_ACTIONS = frozenset({"set", "resume", "pause", "clear", "observe"})
GOAL_RECONCILE_INTERVAL_SECONDS = 5.0


class CodexGoalRunner:
    def __init__(
        self,
        rpc: CodexRpcConnection,
        work: CodexActiveWork,
        threads: CodexLoadedThreads,
        controls: CodexTurnControls,
        timeouts: CodexRequestTimeouts,
    ) -> None:
        self._rpc = rpc
        self._work = work
        self._threads = threads
        self._controls = controls
        self._state_lock = work.state_lock
        self.timeouts = timeouts

    def run_goal(
        self,
        thread_id: str,
        *,
        action: str,
        objective: str = "",
        token_budget: int | None = None,
        cwd: str = "",
        config: dict[str, Any] | None = None,
        dynamic_tool_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        detach_requested: Callable[[], bool] | None = None,
    ) -> CodexGoalResult:
        """Apply a native thread goal command and follow active goals to a terminal state."""

        safe_thread_id = str(thread_id or "").strip()
        safe_action = str(action or "").strip().casefold()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        if safe_action not in GOAL_ACTIONS:
            raise ValueError("unsupported Codex goal action")
        safe_objective = str(objective or "").strip()
        if safe_action == "set" and not safe_objective:
            raise ValueError("Codex goal objective required")

        with self._threads.locked(
            safe_thread_id,
            cancellation_requested=cancellation_requested,
        ):
            self._threads.set_busy(safe_thread_id)
            waiter = GoalWaiter(
                thread_id=safe_thread_id,
                event_callback=event_callback,
            )
            try:
                with self._state_lock:
                    self._work.goals[safe_thread_id] = waiter
                self._load_thread(
                    safe_thread_id, cwd=cwd, config=config,
                    dynamic_tool_handler=dynamic_tool_handler,
                    cancellation_requested=cancellation_requested,
                )
                current = self._goal_request(
                    "thread/goal/get", {"threadId": safe_thread_id}, cancellation_requested,
                )
                current_goal = current.get("goal") if isinstance(current, dict) else None
                if safe_action == "clear":
                    self._goal_request("thread/goal/clear", {"threadId": safe_thread_id}, cancellation_requested)
                    waiter.goal = None
                    return CodexGoalResult(safe_thread_id, "", "Goal cleared.", None, {})
                current_goal = self._apply_command(
                    safe_thread_id, safe_action, safe_objective, token_budget, current_goal,
                    cancellation_requested=cancellation_requested,
                )
                self._start_following(waiter, current_goal)
                self._follow(
                    waiter,
                    cancellation_requested=cancellation_requested,
                    detach_requested=detach_requested,
                )
                goal = waiter.goal
                response = waiter.agent_messages[-1] if waiter.agent_messages else ""
                if not response:
                    response = f"Goal {str((goal or {}).get('status') or 'finished')}."
                return CodexGoalResult(
                    safe_thread_id,
                    waiter.turn_id,
                    response,
                    goal,
                    waiter.token_usage,
                )
            finally:
                with self._state_lock:
                    self._work.detaching_threads.discard(safe_thread_id)
                    self._work.goals.pop(safe_thread_id, None)
                    self._work.dynamic_tool_handlers.pop(safe_thread_id, None)
                    self._threads.clear_busy(safe_thread_id)
                    self._threads.touch(safe_thread_id)

    def _goal_request(
        self,
        method: str,
        params: dict[str, Any],
        cancellation_requested: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        return self._rpc.request(
            method,
            params,
            timeout=self.timeouts.lifecycle,
            cancellation_requested=cancellation_requested,
        )

    def _load_thread(
        self,
        thread_id: str,
        *,
        cwd: str,
        config: dict[str, Any] | None,
        dynamic_tool_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None,
        cancellation_requested: Callable[[], bool] | None,
    ) -> None:
        load = self._threads.prepare_reload(
            thread_id, config, cancellation_requested=cancellation_requested,
        )
        if not load.loaded:
            resume_params: dict[str, Any] = {
                "threadId": thread_id,
                "excludeTurns": True,
            }
            if str(cwd or "").strip():
                resume_params["cwd"] = str(cwd)
            if config is not None:
                resume_params["config"] = dict(config)
            self._goal_request("thread/resume", resume_params, cancellation_requested)
        with self._state_lock:
            self._threads.mark_loaded(
                thread_id,
                config=None if load.unmaterialized else load.requested_config,
            )
            if dynamic_tool_handler is not None:
                self._work.dynamic_tool_handlers[thread_id] = (
                    dynamic_tool_handler
                )

    def _apply_command(
        self,
        thread_id: str,
        action: str,
        objective: str,
        token_budget: int | None,
        current_goal: object,
        *,
        cancellation_requested: Callable[[], bool] | None,
    ) -> object:
        """Set, resume or pause the goal; the goal the command leaves."""

        if action == "set":
            if (
                isinstance(current_goal, dict)
                and str(current_goal.get("objective") or "").strip() == objective
                and str(current_goal.get("status") or "") == "active"
            ):
                return current_goal
            params: dict[str, Any] = {
                "threadId": thread_id,
                "objective": objective,
                "status": "active",
            }
            if token_budget is not None:
                params["tokenBudget"] = max(1, int(token_budget))
        elif action in {"resume", "pause"}:
            params = {
                "threadId": thread_id,
                "status": "active" if action == "resume" else "paused",
            }
        else:
            return current_goal
        updated = self._goal_request("thread/goal/set", params, cancellation_requested)
        return updated.get("goal") if isinstance(updated, dict) else None

    @staticmethod
    def _start_following(waiter: GoalWaiter, current_goal: object) -> None:
        """Adopt the goal the command left, unless a notification already ended it."""

        notified_terminal_goal = bool(
            isinstance(waiter.goal, dict)
            and str(waiter.goal.get("status") or "") != "active"
        )
        if not notified_terminal_goal:
            waiter.goal = (
                dict(current_goal)
                if isinstance(current_goal, dict)
                else None
            )
        if not isinstance(waiter.goal, dict):
            raise CodexAppServerError("Codex thread has no goal")
        if str(waiter.goal.get("status") or "") != "active":
            waiter.event.set()

    def _follow(
        self,
        waiter: GoalWaiter,
        *,
        cancellation_requested: Callable[[], bool] | None,
        detach_requested: Callable[[], bool] | None,
    ) -> None:
        thread_id = waiter.thread_id
        last_reconcile = time.monotonic()
        while not waiter.event.wait(0.25):
            if waiter.error:
                raise waiter.error
            self._work.detach_if_requested(thread_id, waiter.turn_id, detach_requested)
            if cancellation_requested and cancellation_requested():
                self._pause(waiter)
            if time.monotonic() - last_reconcile < GOAL_RECONCILE_INTERVAL_SECONDS:
                continue
            last_reconcile = time.monotonic()
            observed = self._rpc.request(
                "thread/goal/get",
                {"threadId": thread_id},
                timeout=45.0,
            )
            observed_goal = observed.get("goal") if isinstance(observed, dict) else None
            waiter.goal = dict(observed_goal) if isinstance(observed_goal, dict) else None
            if not isinstance(waiter.goal, dict) or str(waiter.goal.get("status") or "") != "active":
                waiter.event.set()
        if waiter.error:
            raise waiter.error

    def _pause(self, waiter: GoalWaiter) -> None:
        """A cancelled goal pauses and its running turn stops."""

        thread_id = waiter.thread_id
        try:
            self._rpc.request(
                "thread/goal/set",
                {"threadId": thread_id, "status": "paused"},
                timeout=30.0,
            )
        finally:
            if waiter.turn_id:
                try:
                    self._controls.interrupt_turn(thread_id, waiter.turn_id)
                except Exception:
                    pass
        raise InterruptedError(f"Codex goal paused for {thread_id}")
