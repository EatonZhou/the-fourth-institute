"""Starting a Codex turn, or attaching to one an earlier follower accepted.

``run_turn`` loads the thread with the turn's settings, adopts a turn already
running with this input, starts the turn (waiting out a compaction that
holds the thread) and follows it.  ``resume_turn`` follows an accepted turn
again after a reconnect or on another Worker.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from .codex_app_server_protocol import (
    CodexAppServerError,
    CodexAppServerUnavailable,
    CodexCompactionInProgress,
    CodexRequestTimeouts,
    CodexThreadNotFound,
    CodexThreadWriterBusy,
    CodexTurnConnectionLost,
    CodexTurnResult,
    TurnWaiter,
    is_active_compaction_conflict,
    notification_turn_id,
    sandbox_policy as sandbox_policy_for,
    thread_sandbox_from_policy,
)

if TYPE_CHECKING:
    from .codex_app_server_events import CodexActiveWork, CodexEventRouter, DynamicToolHandler
    from .codex_app_server_rpc import CodexRpcConnection
    from .codex_app_server_threads import CodexLoadedThreads, CodexThreadReads
    from .codex_app_server_turn_controls import CodexTurnControls, TurnControlProvider, TurnControlResultCallback
    from .codex_app_server_turn_follow import CodexTurnFollower
    from .codex_user_input import CodexUserInputRequests


# Provider-owned compact turns normally release quickly. A long compact must
# not hold a Portal worker or become a terminal user-visible failure; after
# this short reconciliation window the execution returns to the scheduler.
TURN_COMPACTION_CONFLICT_WAIT_SECONDS = 30.0
COMPACTION_RELEASE_POLL_INTERVAL_SECONDS = 0.25
IMAGE_DETAILS = frozenset({"", "auto", "high", "original"})


def turn_input(
    prompt: str,
    *,
    images: list[str] | None,
    image_urls: list[str] | None,
    image_detail: str | None,
) -> list[dict[str, Any]]:
    """The input items of a turn: its prompt, then its local and remote images."""

    items: list[dict[str, Any]] = [
        {"type": "text", "text": prompt}
    ]
    safe_image_detail = str(image_detail or "").strip().casefold()
    if safe_image_detail not in IMAGE_DETAILS:
        raise ValueError("unsupported Codex input image detail")
    for image in images or []:
        if not str(image or "").strip():
            continue
        image_input = {
            "type": "localImage",
            "path": str(image),
        }
        if safe_image_detail:
            image_input["detail"] = safe_image_detail
        items.append(image_input)
    for image_url in image_urls or []:
        if not str(image_url or "").strip():
            continue
        image_input = {
            "type": "image",
            "url": str(image_url),
        }
        if safe_image_detail:
            image_input["detail"] = safe_image_detail
        items.append(image_input)
    return items


def turn_start_params(
    thread_id: str,
    items: list[dict[str, Any]],
    *,
    cwd: str,
    model: str,
    effort: str,
    service_tier: str,
    collaboration_mode: str,
    permissions: str,
    sandbox: str,
    sandbox_policy: dict[str, Any] | None,
    approval_policy: str,
    approvals_reviewer: str,
    client_user_message_id: str,
) -> dict[str, Any]:
    """The ``turn/start`` parameters of a turn."""

    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": items,
        "cwd": cwd,
    }
    safe_collaboration_mode = str(
        collaboration_mode or ""
    ).strip()
    if safe_collaboration_mode in {"default", "plan"}:
        if not str(model or "").strip():
            raise ValueError(
                "collaboration mode requires an explicit model"
            )
        collaboration_settings: dict[str, Any] = {
            "model": str(model or "").strip(),
            "developer_instructions": None,
        }
        if effort:
            collaboration_settings["reasoning_effort"] = effort
        params["collaborationMode"] = {
            "mode": safe_collaboration_mode,
            "settings": collaboration_settings,
        }
    for key, value in (
        ("model", model),
        ("effort", effort),
        ("serviceTier", service_tier),
        ("permissions", permissions),
        ("approvalsReviewer", approvals_reviewer),
        ("clientUserMessageId", client_user_message_id),
        ("approvalPolicy", approval_policy),
    ):
        if value:
            params[key] = value
    if sandbox_policy is not None and not permissions:
        params["sandboxPolicy"] = dict(sandbox_policy)
    elif sandbox and not permissions:
        params["sandboxPolicy"] = sandbox_policy_for(sandbox)
    return params


class CodexTurnRunner:
    def __init__(
        self,
        rpc: CodexRpcConnection,
        work: CodexActiveWork,
        threads: CodexLoadedThreads,
        reads: CodexThreadReads,
        events: CodexEventRouter,
        controls: CodexTurnControls,
        follower: CodexTurnFollower,
        user_inputs: CodexUserInputRequests,
        timeouts: CodexRequestTimeouts,
    ) -> None:
        self._rpc = rpc
        self._work = work
        self._threads = threads
        self._reads = reads
        self._events = events
        self._controls = controls
        self._follower = follower
        self._user_inputs = user_inputs
        self._state_lock = work.state_lock
        self.timeouts = timeouts

    def run_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        cwd: str,
        history_initializer: Callable[[str], None] | None = None,
        model: str = "",
        effort: str = "",
        service_tier: str = "",
        collaboration_mode: str = "",
        review_target: dict[str, Any] | None = None,
        permissions: str = "",
        sandbox: str = "",
        sandbox_policy: dict[str, Any] | None = None,
        approval_policy: str = "",
        approvals_reviewer: str = "",
        client_user_message_id: str = "",
        images: list[str] | None = None,
        image_urls: list[str] | None = None,
        image_detail: str | None = None,
        config: dict[str, Any] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        turn_snapshot_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        turn_started_callback: Callable[[str], None] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        detach_requested: Callable[[], bool] | None = None,
        dynamic_tool_handler: DynamicToolHandler | None = None,
        turn_control_provider: TurnControlProvider | None = None,
        turn_control_result_callback: TurnControlResultCallback | None = None,
    ) -> CodexTurnResult:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        if cancellation_requested and cancellation_requested():
            raise InterruptedError(
                f"Codex turn cancelled before start for {safe_thread_id}"
            )
        with self._threads.locked(
            safe_thread_id,
            cancellation_requested=cancellation_requested,
        ):
            self._threads.set_busy(safe_thread_id)
            try:
                load = self._threads.prepare_reload(
                    safe_thread_id, config, cancellation_requested=cancellation_requested,
                )
                unmaterialized = load.unmaterialized
                resumed_thread_id = safe_thread_id
                if not load.loaded:
                    resumed_thread_id, missing = self._resume_for_turn(
                        safe_thread_id,
                        cwd=cwd, config=config, model=model, service_tier=service_tier,
                        permissions=permissions, sandbox=sandbox, sandbox_policy=sandbox_policy,
                        approvals_reviewer=approvals_reviewer, approval_policy=approval_policy,
                        cancellation_requested=cancellation_requested,
                    )
                    unmaterialized = unmaterialized or missing
                self._threads.mark_loaded(
                    resumed_thread_id,
                    config=None if load.config_reload_deferred else load.requested_config,
                )
                # The input is checked before the turn is registered: a
                # rejected input leaves no waiter or tool handler behind.
                items = turn_input(prompt, images=images, image_urls=image_urls, image_detail=image_detail)
                turn_params = turn_start_params(
                    resumed_thread_id, items,
                    cwd=cwd, model=model, effort=effort, service_tier=service_tier,
                    collaboration_mode=collaboration_mode, permissions=permissions,
                    sandbox=sandbox, sandbox_policy=sandbox_policy,
                    approval_policy=approval_policy, approvals_reviewer=approvals_reviewer,
                    client_user_message_id=client_user_message_id,
                )
                waiter = TurnWaiter(thread_id=resumed_thread_id, event_callback=event_callback,
                                    turn_snapshot_transform=turn_snapshot_transform)
                self._register(waiter, dynamic_tool_handler)
                if not (unmaterialized or load.loaded):
                    self._adopt_running_turn(waiter, client_user_message_id)
                start_method = "review/start" if review_target else "turn/start"
                if review_target:
                    turn_params = {"threadId": resumed_thread_id, "target": review_target, "delivery": "inline"}
                turn_start_attempted = False
                try:
                    if not waiter.turn_id:
                        if history_initializer is not None:
                            history_initializer(resumed_thread_id)
                        turn_start_attempted = True
                        try:
                            started = self._request_start(
                                start_method, turn_params, cancellation_requested=cancellation_requested,
                            )
                        except CodexAppServerError as exc:
                            if not is_active_compaction_conflict(exc):
                                raise
                            waiter = self._replace_after_compaction(
                                waiter, cancellation_requested=cancellation_requested,
                            )
                            started = self._request_start(
                                start_method, turn_params, cancellation_requested=cancellation_requested,
                            )
                        self._bind_started(waiter, started, start_method)
                    self._threads.mark_materialized(resumed_thread_id)
                    if waiter.turn_id and turn_started_callback:
                        turn_started_callback(waiter.turn_id)
                    self._follower.schedule_reconcile(waiter)
                    return self._follower.follow(
                        waiter, turn_started_callback=turn_started_callback,
                        cancellation_requested=cancellation_requested,
                        detach_requested=detach_requested,
                        turn_control_provider=turn_control_provider,
                        turn_control_result_callback=turn_control_result_callback,
                    )
                except (InterruptedError, TimeoutError):
                    self._interrupt_accepted(waiter)
                    raise
                except CodexAppServerUnavailable as exc:
                    if turn_start_attempted:
                        raise CodexTurnConnectionLost(resumed_thread_id, waiter.turn_id) from exc
                    raise
                finally:
                    self._finish(resumed_thread_id, dynamic_tool_handler)
            finally:
                self._threads.clear_busy(safe_thread_id)

    def _resume_for_turn(
        self,
        thread_id: str,
        *,
        cwd: str,
        config: dict[str, Any] | None,
        model: str,
        service_tier: str,
        permissions: str,
        sandbox: str,
        sandbox_policy: dict[str, Any] | None,
        approvals_reviewer: str,
        approval_policy: str,
        cancellation_requested: Callable[[], bool] | None,
    ) -> tuple[str, bool]:
        """Load the thread with the turn's settings: the thread it resumed, and whether none exists yet."""

        resume_params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "excludeTurns": True,
        }
        if config is not None:
            resume_params["config"] = dict(config)
        for key, value in (
            ("model", model),
            ("serviceTier", service_tier),
            ("permissions", permissions),
            (
                "sandbox",
                (
                    ""
                    if permissions
                    else (
                        sandbox
                        or thread_sandbox_from_policy(
                            sandbox_policy
                        )
                    )
                ),
            ),
            ("approvalsReviewer", approvals_reviewer),
            ("approvalPolicy", approval_policy),
        ):
            if value:
                resume_params[key] = value
        try:
            resumed = self._rpc.request(
                "thread/resume",
                resume_params,
                timeout=self.timeouts.lifecycle,
                cancellation_requested=cancellation_requested,
            )
        except CodexAppServerError as exc:
            # A fork is not persisted until its first turn starts. A
            # different execution worker therefore cannot resume it,
            # even though the returned thread id is valid for
            # turn/start. Keep the same id and let turn/start
            # materialize the fork. Any genuinely missing id will be
            # rejected by turn/start itself.
            if not isinstance(exc, CodexThreadNotFound):
                raise
            self._threads.mark_unmaterialized(thread_id)
            return thread_id, True
        resumed_thread = (
            resumed.get("thread")
            if isinstance(resumed.get("thread"), dict)
            else {}
        )
        return str(resumed_thread.get("id") or thread_id), False

    def _register(self, waiter: TurnWaiter, dynamic_tool_handler: DynamicToolHandler | None) -> None:
        with self._state_lock:
            self._work.turns[waiter.thread_id] = waiter
            if dynamic_tool_handler is not None:
                self._work.dynamic_tool_handlers[waiter.thread_id] = (
                    dynamic_tool_handler
                )

    def _finish(self, thread_id: str, dynamic_tool_handler: DynamicToolHandler | None) -> None:
        self._user_inputs.expire(thread_id)
        with self._state_lock:
            self._work.detaching_threads.discard(thread_id)
            self._work.turns.pop(thread_id, None)
            if dynamic_tool_handler is not None:
                self._work.dynamic_tool_handlers.pop(
                    thread_id,
                    None,
                )
            self._threads.touch(thread_id)

    def _adopt_running_turn(self, waiter: TurnWaiter, client_user_message_id: str) -> None:
        """Follow the thread's running turn when it carries this input; refuse another input's."""

        active_turn = self._reads.read_active_turn(waiter.thread_id, include_items=True)
        if active_turn is None:
            return
        owns_input = bool(client_user_message_id) and any(
            isinstance(item, dict) and item.get("type") == "userMessage"
            and item.get("clientId") == client_user_message_id
            for item in active_turn.get("items") or []
        )
        if not owns_input:
            with self._state_lock:
                self._work.turns.pop(waiter.thread_id, None)
                self._work.dynamic_tool_handlers.pop(waiter.thread_id, None)
            raise CodexThreadWriterBusy("Codex thread has another active input")
        self.bind_turn_waiter(waiter, str(active_turn.get("id") or "").strip())

    def _request_start(
        self,
        start_method: str,
        turn_params: dict[str, Any],
        *,
        cancellation_requested: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        return self._rpc.request(
            start_method,
            turn_params,
            timeout=self.timeouts.turn_start,
            cancellation_requested=cancellation_requested,
        )

    def _replace_after_compaction(
        self,
        waiter: TurnWaiter,
        *,
        cancellation_requested: Callable[[], bool] | None,
    ) -> TurnWaiter:
        """Wait out the compaction that rejected the start; the waiter for the second start.

        Automatic context compaction can start after the active-turn check
        but before turn/start.  The rejected request was not accepted, so it
        is safe to wait for that technical turn and retry exactly once.  Its
        notifications and state do not belong to the user's turn.
        """

        thread_id = waiter.thread_id
        with self._state_lock:
            if self._work.turns.get(thread_id) is waiter:
                self._work.detaching_threads.discard(thread_id)
                self._work.turns.pop(thread_id, None)
        self.wait_for_compaction_release(
            thread_id,
            cancellation_requested=cancellation_requested,
        )
        replacement = TurnWaiter(
            thread_id=thread_id,
            event_callback=waiter.event_callback,
            turn_snapshot_transform=waiter.turn_snapshot_transform,
        )
        with self._state_lock:
            self._work.turns[thread_id] = replacement
        return replacement

    def _bind_started(self, waiter: TurnWaiter, started: dict[str, Any], start_method: str) -> None:
        turn = (
            started.get("turn")
            if isinstance(started.get("turn"), dict)
            else {}
        )
        self.bind_turn_waiter(waiter, str(turn.get("id") or "").strip())
        if waiter.turn_id and waiter.event_callback:
            # This is a local receipt of the actual successful
            # RPC, not a native item or an inference on replay.
            waiter.event_callback({
                "method": "portal/provider_request_accepted",
                "params": {"threadId": waiter.thread_id,
                           "turnId": waiter.turn_id,
                           "rpcMethod": start_method},
            })

    def _interrupt_accepted(self, waiter: TurnWaiter) -> None:
        """Interrupt the turn a cancelled or timed-out start may have started."""

        if not waiter.turn_id:
            for pending_method, pending_params in waiter.pending_notifications:
                if pending_method == "turn/started":
                    accepted = pending_params.get("turn") or {}
                    waiter.turn_id = str(accepted.get("id") or pending_params.get("turnId") or "")
                    if waiter.turn_id:
                        break
        if waiter.turn_id:
            try:
                self._controls.interrupt_turn(
                    waiter.thread_id,
                    waiter.turn_id,
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "Codex turn %s of thread %s was not interrupted", waiter.turn_id, waiter.thread_id, exc_info=True)

    def bind_turn_waiter(self, waiter: TurnWaiter, turn_id: str) -> None:
        if not turn_id:
            raise CodexAppServerError("Codex did not identify the accepted turn")
        with self._state_lock:
            waiter.turn_id = turn_id
            pending = waiter.pending_notifications
            waiter.pending_notifications = []
        for method, params in pending:
            event_turn_id = notification_turn_id(params)
            if event_turn_id and event_turn_id != turn_id:
                continue
            self._events.dispatch_notification(method, params)

    def wait_for_compaction_release(
        self,
        thread_id: str,
        *,
        cancellation_requested: Callable[[], bool] | None,
    ) -> None:
        deadline = time.monotonic() + TURN_COMPACTION_CONFLICT_WAIT_SECONDS
        while True:
            if cancellation_requested and cancellation_requested():
                raise InterruptedError(
                    f"Codex turn cancelled while waiting for compaction on {thread_id}"
                )
            if self._reads.read_active_turn(thread_id) is None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexCompactionInProgress(
                    "Codex context compaction is still active; "
                    "execution will retry after the thread becomes writable"
                )
            time.sleep(min(COMPACTION_RELEASE_POLL_INTERVAL_SECONDS, remaining))

    def resume_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        cwd: str,
        config: dict[str, Any] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        turn_snapshot_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        turn_started_callback: Callable[[str], None] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        detach_requested: Callable[[], bool] | None = None,
        dynamic_tool_handler: DynamicToolHandler | None = None,
        turn_control_provider: TurnControlProvider | None = None,
        turn_control_result_callback: TurnControlResultCallback | None = None,
        timeout_seconds: float | None = None,
    ) -> CodexTurnResult:
        safe_thread_id = str(thread_id or "").strip()
        safe_turn_id = str(turn_id or "").strip()
        if not safe_thread_id or not safe_turn_id:
            raise ValueError("thread_id and turn_id are required")
        deadline = (
            time.monotonic() + max(0.1, float(timeout_seconds))
            if timeout_seconds is not None
            else None
        )
        with self._threads.locked(
            safe_thread_id,
            cancellation_requested=cancellation_requested,
        ):
            self._threads.set_busy(safe_thread_id)
            waiter = TurnWaiter(
                thread_id=safe_thread_id,
                turn_id=safe_turn_id,
                event_callback=event_callback,
                turn_snapshot_transform=turn_snapshot_transform,
            )
            try:
                with self._state_lock:
                    self._threads.mark_loaded(safe_thread_id)
                    self._register(waiter, dynamic_tool_handler)
                resume_params: dict[str, Any] = {
                    "threadId": safe_thread_id,
                    "cwd": cwd,
                    "excludeTurns": True,
                }
                if config is not None:
                    resume_params["config"] = dict(config)
                self._rpc.request(
                    "thread/resume",
                    resume_params,
                    timeout=self.timeouts.lifecycle,
                    cancellation_requested=cancellation_requested,
                )
                active_turn = self._reads.read_active_turn(safe_thread_id)
                if (active_turn is not None
                        and str(active_turn.get("id") or "") != waiter.turn_id
                        and self._reads.read_turn(safe_thread_id, waiter.turn_id) is None):
                    # Only an absent checkpoint may realign to another turn.
                    # A known parent remains authoritative during native review.
                    waiter.turn_id = str(
                        active_turn.get("id") or waiter.turn_id
                    ).strip()
                if waiter.turn_id and turn_started_callback:
                    turn_started_callback(waiter.turn_id)
                return self._follower.follow(
                    waiter, turn_started_callback=turn_started_callback,
                    cancellation_requested=cancellation_requested,
                    detach_requested=detach_requested,
                    turn_control_provider=turn_control_provider,
                    turn_control_result_callback=turn_control_result_callback,
                    deadline=deadline,
                )
            finally:
                self._finish(safe_thread_id, dynamic_tool_handler)
                self._threads.clear_busy(safe_thread_id)
