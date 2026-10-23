from __future__ import annotations
from management_common.portal_paths import path as portal_path

from management_common.file_lock import try_lock_descriptor, unlock_descriptor
import hashlib
import json
import logging
import os
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from collections import deque
from .provider_websocket import WebSocketTransport
from .codex_app_server_host import connect_transport
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_SOCKET_PATH = (portal_path("staging_root") / "portal_locks" / "codex-app-server.sock")
DEFAULT_THREAD_LOCK_ROOT = (portal_path("staging_root") / "portal_locks" / "codex-app-server-threads")
DEFAULT_IDLE_UNSUBSCRIBE_SECONDS = 5 * 60 * 60
DEFAULT_MAX_LOADED_THREADS = 30
DEFAULT_REQUEST_TIMEOUT_SECONDS = 90.0
DEFAULT_LIFECYCLE_REQUEST_TIMEOUT_SECONDS = 180.0
DEFAULT_TURN_START_REQUEST_TIMEOUT_SECONDS = 360.0
DEFAULT_COMPACT_TOTAL_TIMEOUT_SECONDS = 1800.0
DEFAULT_COMPACT_NO_PROGRESS_TIMEOUT_SECONDS = 900.0
DEFAULT_COMPACT_STATUS_POLL_INTERVAL_SECONDS = 5.0
TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS = 2.0
TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS = 10.0
TURN_SUCCESSOR_RECONCILE_GRACE_SECONDS = 6.0
TURN_ACCEPTED_MISSING_GRACE_SECONDS = 15.0
# Provider-owned compact turns normally release quickly. A long compact must
# not hold a Portal worker or become a terminal user-visible failure; after
# this short reconciliation window the execution returns to the scheduler.
TURN_COMPACTION_CONFLICT_WAIT_SECONDS = 30.0
TASK_COMPLETE_GOAL_STATUSES = frozenset({"blocked", "complete", "usagelimited", "budgetlimited"})
TURN_CONTROL_POLL_INTERVAL_SECONDS = 0.25
_USE_DEFAULT_REQUEST_TIMEOUT = object()


class CodexAppServerError(RuntimeError):
    pass


class CodexTurnDetached(CodexAppServerError):
    """A retiring follower leaves the accepted native turn running."""

    def __init__(self, thread_id: str, turn_id: str) -> None:
        self.thread_id, self.turn_id = thread_id, turn_id
        super().__init__(f"Codex follower detached: {thread_id}/{turn_id}")


class CodexTurnUsageLimitExceeded(CodexAppServerError):
    """The account needs an external quota change, not another input."""

    error_code = "provider_usage_limit"

    def __init__(self, thread_id: str, turn_id: str, reason: str) -> None:
        super().__init__(f"Codex turn failed: {reason}")
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.error_details = {"provider": "codex", "retryable": False}


class CodexTurnAuthenticationError(CodexAppServerError):
    """The account requires renewed authentication before work can resume."""

    error_code = "provider_authentication_failed"

    def __init__(self, thread_id: str, turn_id: str, reason: str) -> None:
        detail = str(reason or "Codex authentication failed; please sign in again").strip()
        super().__init__(f"Codex authentication failed: {detail}")
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.error_details = {
            "provider": "codex",
            "retryable": False,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "provider_reason": detail,
        }


class CodexTurnSafetyBlocked(CodexAppServerError):
    """The provider safety system rejected the request as terminal."""

    error_code = "provider_safety_blocked"

    def __init__(self, thread_id: str, turn_id: str, reason: str) -> None:
        detail = str(reason or "provider safety system blocked the request").strip()
        super().__init__(f"Codex turn blocked by safety systems: {detail}")
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.error_details = {
            "provider": "codex",
            "retryable": False,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }
        if detail:
            self.error_details["provider_reason"] = detail


class CodexAppServerUnavailable(CodexAppServerError):
    pass


class CodexThreadWriterBusy(CodexAppServerError):
    """The durable thread is temporarily owned by another Codex process."""


class CodexCompactionInProgress(CodexThreadWriterBusy):
    """The Provider is still compacting a thread that rejected turn/start."""

    retry_after_seconds = 5.0


class CodexTurnConnectionLost(CodexAppServerError):
    def __init__(self, thread_id: str, turn_id: str = "") -> None:
        super().__init__(f"Codex app-server connection lost during accepted turn {turn_id or '(unknown)'}")
        self.thread_id = thread_id
        self.turn_id = turn_id


class CodexTurnInterrupted(CodexAppServerError):
    error_code = "provider_turn_interrupted"

    def __init__(
        self,
        thread_id: str,
        turn_id: str,
        provider_reason: str = "",
    ) -> None:
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.provider_reason = str(provider_reason or "").strip()
        self.error_details = {
            "provider": "codex",
            "provider_status": "interrupted",
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }
        if self.provider_reason:
            self.error_details["provider_reason"] = self.provider_reason
        message = (
            f"Codex turn interrupted: {self.provider_reason}"
            if self.provider_reason
            else "Codex turn interrupted without a provider reason"
        )
        super().__init__(message)


class CodexTurnRetryableFailure(CodexAppServerError):
    """A terminal Provider failure that is safe to retry once in-place."""

    error_code = "provider_turn_retryable_failure"

    def __init__(
        self,
        thread_id: str,
        turn_id: str,
        provider_reason: str,
        failure_kind: str,
    ) -> None:
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.provider_reason = str(provider_reason or "").strip()
        self.failure_kind = str(failure_kind or "transient").strip()
        self.error_details = {
            "provider": "codex",
            "provider_status": "failed",
            "provider_failure_kind": self.failure_kind,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }
        if self.provider_reason:
            self.error_details["provider_reason"] = self.provider_reason
        detail = self.provider_reason or "provider returned no error details"
        super().__init__(f"Codex turn failed: {detail}")




class CodexThreadNotFound(CodexAppServerError):
    """The requested thread is absent from the native server and rollout store."""


class CodexTurnContinuationRequired(CodexAppServerError):
    """A provider turn ended before the logical execution produced an answer."""

    def __init__(self, thread_id: str, turn_id: str, reason: str) -> None:
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.reason = str(reason or "missing_final_response")
        super().__init__(
            "Codex turn ended before the logical execution produced a final response"
        )


def _is_active_compaction_conflict(exc: BaseException) -> bool:
    message = str(exc or "").casefold()
    return "activeturnnotsteerable" in message and "compact" in message


def _is_loaded_thread_writer_conflict(error: object) -> bool:
    message = str(error or "").casefold()
    return "thread" in message and "already has an active writer" in message


def is_context_compaction_item(item: object) -> bool:
    """A thread item that compacted the thread's context (ThreadItem ``contextCompaction``)."""

    return isinstance(item, Mapping) and item.get("type") == "contextCompaction"


def _terminal_turn_error(
    thread_id: str,
    turn: dict[str, Any],
) -> CodexAppServerError:
    status = str(turn.get("status") or "").strip()
    raw_error = turn.get("error")
    from .execution.provider_failures import failure_message, is_usage_limit_failure

    reason = failure_message(raw_error)
    turn_id = str(turn.get("id") or "")
    normalized_reason = reason.casefold()
    error_info = raw_error.get("codexErrorInfo") if isinstance(raw_error, dict) else None
    if isinstance(error_info, dict):
        error_info = next(iter(error_info), "")
    if error_info == "unauthorized" or any(marker in normalized_reason for marker in (
        "your access token could not be refreshed", "provided authentication token is expired",
    )):
        return CodexTurnAuthenticationError(thread_id, turn_id, reason)
    if error_info in {"cyberPolicy", "misalignmentPolicyViolation"} or any(marker in normalized_reason for marker in (
        "blocked by our safety systems", "potentially unintended activity", "cyber_policy",
    )):
        return CodexTurnSafetyBlocked(thread_id, turn_id, reason)
    if error_info == "usageLimitExceeded" or is_usage_limit_failure(reason):
        return CodexTurnUsageLimitExceeded(thread_id, turn_id, reason)
    if status == "interrupted":
        return CodexTurnInterrupted(thread_id, turn_id, reason)
    if error_info == "serverOverloaded" or "selected model is at capacity" in normalized_reason:
        return CodexTurnRetryableFailure(thread_id, turn_id, reason, "capacity")
    detail = reason or "provider returned no error details"
    return CodexAppServerError(
        f"Codex turn {status or 'failed'}: {detail}"
    )


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


@dataclass
class CodexTurnResult:
    thread_id: str
    turn_id: str
    response: str
    turn: dict[str, Any]
    token_usage: dict[str, Any]


@dataclass
class CodexGoalResult:
    thread_id: str
    turn_id: str
    response: str
    goal: dict[str, Any] | None
    token_usage: dict[str, Any]


@dataclass
class _PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None


@dataclass
class _TurnWaiter:
    thread_id: str
    next_reconcile_at: float = 0.0
    event: threading.Event = field(default_factory=threading.Event)
    turn_id: str = ""
    pending_notifications: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    completed: dict[str, Any] | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    token_usage_events: set[tuple[Any, ...]] = field(default_factory=set)
    agent_messages: list[str] = field(default_factory=list)
    error: BaseException | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None
    thread_idle: bool = False
    terminal_goal: dict[str, Any] | None = None
    final_answer_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    final_answer_seen: bool = False
    completed_item_ids: set[str] = field(default_factory=set)
    turn_snapshot_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    interrupt_requests: dict[str, _PendingRequest] = field(default_factory=dict)




@dataclass
class _GoalWaiter:
    thread_id: str
    event: threading.Event = field(default_factory=threading.Event)
    turn_id: str = ""
    goal: dict[str, Any] | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    token_usage_events: set[tuple[Any, ...]] = field(default_factory=set)
    agent_messages: list[str] = field(default_factory=list)
    error: BaseException | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None


@dataclass
class _CompactWaiter:
    thread_id: str
    event: threading.Event = field(default_factory=threading.Event)
    token_usage: dict[str, Any] = field(default_factory=dict)
    token_usage_events: set[tuple[Any, ...]] = field(default_factory=set)
    error: BaseException | None = None
    thread_idle: bool = False
    last_progress_at: float = field(default_factory=time.monotonic)


def _fail_waiter(
    waiter: _PendingRequest | _TurnWaiter | _GoalWaiter | _CompactWaiter,
    error: BaseException,
) -> None:
    # Preserve the causal failure; an explicit rejection supersedes recovery.
    if waiter.error is None or (
        getattr(error, "error_details", {}).get("retryable") is False
        and getattr(waiter.error, "error_details", {}).get("retryable") is not False
    ):
        waiter.error = error
    waiter.event.set()


_TOKEN_USAGE_KEYS = (
    "inputTokens",
    "cachedInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
    "totalTokens",
)


def _nonnegative_int(value: object) -> int:
    try:
        number = int(value or 0)
    except Exception:
        return 0
    return number if number > 0 else 0


def _clean_token_usage_part(value: object) -> dict[str, int]:
    data = value if isinstance(value, dict) else {}
    return {
        key: number
        for key in _TOKEN_USAGE_KEYS
        for number in [_nonnegative_int(data.get(key))]
        if number > 0
    }


def _sum_token_usage_parts(left: object, right: object) -> dict[str, int]:
    left_data = _clean_token_usage_part(left)
    right_data = _clean_token_usage_part(right)
    out: dict[str, int] = {}
    for key in _TOKEN_USAGE_KEYS:
        value = _nonnegative_int(left_data.get(key)) + _nonnegative_int(right_data.get(key))
        if value > 0:
            out[key] = value
    return out


def _collect_token_usage(waiter: _TurnWaiter | _GoalWaiter | _CompactWaiter, usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    fingerprint = _token_usage_event_fingerprint(usage)
    if fingerprint not in waiter.token_usage_events:
        waiter.token_usage_events.add(fingerprint)
        waiter.token_usage = _merge_turn_token_usage(waiter.token_usage, usage)


def _collect_agent_message(waiter: _TurnWaiter | _GoalWaiter, item: dict[str, Any]) -> bool:
    text = str(item.get("text") or "").strip()
    if item.get("type") != "agentMessage" or not text:
        return False
    waiter.agent_messages.append(text)
    return True


def _token_usage_event_fingerprint(value: object) -> tuple[Any, ...]:
    payload = value if isinstance(value, dict) else {}
    last = _clean_token_usage_part(payload.get("last"))
    total = _clean_token_usage_part(payload.get("total"))
    return (
        tuple((key, last.get(key, 0)) for key in _TOKEN_USAGE_KEYS),
        tuple((key, total.get(key, 0)) for key in _TOKEN_USAGE_KEYS),
        _nonnegative_int(payload.get("modelContextWindow")),
    )


def _consumed_token_usage_part(value: object) -> dict[str, int]:
    """The ``last`` part of an update when it reports a model request.

    After a context compaction Codex reports the estimated size of the
    compacted history as a ``last`` part holding only ``totalTokens``; its
    cumulative total does not change.  That is a context size, not usage.
    """

    part = _clean_token_usage_part(value)
    if not part.get("inputTokens") and not part.get("outputTokens"):
        return {}
    return part


def codex_token_usage_delta(value: object) -> tuple[str, dict[str, Any]]:
    """Return the durable identity and incremental part of one Codex update."""

    payload = value if isinstance(value, dict) else {}
    last = _consumed_token_usage_part(payload.get("last"))
    if not last:
        return "", {}
    fingerprint = _token_usage_event_fingerprint(payload)
    encoded = json.dumps(
        fingerprint,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), {"last": last}


def _merge_turn_token_usage(current: object, update: object) -> dict[str, Any]:
    current_payload = current if isinstance(current, dict) else {}
    update_payload = update if isinstance(update, dict) else {}
    merged: dict[str, Any] = {}
    last_total = _sum_token_usage_parts(
        current_payload.get("last"), _consumed_token_usage_part(update_payload.get("last")),
    )
    if last_total:
        merged["last"] = last_total
    context = _clean_token_usage_part(update_payload.get("last"))
    if context:
        merged["context"] = context
    total = _clean_token_usage_part(update_payload.get("total")) or _clean_token_usage_part(current_payload.get("total"))
    if total:
        merged["total"] = total
    context_window = _nonnegative_int(update_payload.get("modelContextWindow")) or _nonnegative_int(current_payload.get("modelContextWindow"))
    if context_window:
        merged["modelContextWindow"] = context_window
    return merged


class CodexAppServerClient:
    def __init__(
        self,
        socket_path: Path | str = DEFAULT_SOCKET_PATH,
        *,
        idle_unsubscribe_seconds: int = DEFAULT_IDLE_UNSUBSCRIBE_SECONDS,
        max_loaded_threads: int = DEFAULT_MAX_LOADED_THREADS,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        lifecycle_request_timeout_seconds: float = (
            DEFAULT_LIFECYCLE_REQUEST_TIMEOUT_SECONDS
        ),
        turn_start_request_timeout_seconds: float = (
            DEFAULT_TURN_START_REQUEST_TIMEOUT_SECONDS
        ),
    ) -> None:
        self.socket_path = Path(socket_path)
        self.idle_unsubscribe_seconds = max(60, int(idle_unsubscribe_seconds))
        self.max_loaded_threads = max(1, int(max_loaded_threads))
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self.lifecycle_request_timeout_seconds = max(
            0.01,
            float(lifecycle_request_timeout_seconds),
        )
        self.turn_start_request_timeout_seconds = max(
            0.01,
            float(turn_start_request_timeout_seconds),
        )
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._transport: WebSocketTransport | None = None
        self._reader_thread: threading.Thread | None = None
        self._maintenance_thread: threading.Thread | None = None
        self._closing = False
        self._request_id = 0
        self._pending: dict[int, _PendingRequest] = {}
        self._turn_waiters: dict[str, _TurnWaiter] = {}
        self._goal_waiters: dict[str, _GoalWaiter] = {}
        self._child_threads: dict[str, tuple[str, str]] = {}
        self._unclaimed_child_events: deque[tuple[str, dict[str, Any]]] = deque(maxlen=256)
        # Subagent threads this client joined to follow their steps.
        self._followed_children: set[str] = set()
        # The turn each subagent thread last ran, to stop that subagent alone.
        self._child_turns: dict[str, str] = {}
        self._compact_waiters: dict[str, _CompactWaiter] = {}
        self._busy_threads: set[str] = set()
        self._detaching_threads: set[str] = set()
        self._active_dynamic_calls: dict[str, int] = {}
        self._thread_locks: dict[str, threading.Lock] = {}
        self._thread_activity: dict[str, float] = {}
        self._thread_configs: dict[str, str] = {}
        # thread/start returns before a zero-turn thread has a durable rollout.
        # Reloading that thread through unsubscribe/resume would remove its only
        # in-memory representation before the first turn can materialize it.
        self._unmaterialized_threads: set[str] = set()
        self._dynamic_tool_handlers: dict[
            str,
            Callable[[dict[str, Any]], dict[str, Any]],
        ] = {}
        from .codex_user_input import CodexUserInputRequests

        self.user_inputs = CodexUserInputRequests(self._send, self._dispatch_notification)
        from management_common.provider_turn_artifacts import TurnArtifacts
        self.turn_artifacts = TurnArtifacts()

    def _next_request_id(self) -> int:
        with self._state_lock:
            self._request_id += 1
            return self._request_id

    def _send(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            transport = self._transport
            if transport is None:
                raise CodexAppServerUnavailable("Codex app-server is not connected")
            try:
                transport.send_text(encoded)
            except (BrokenPipeError, OSError) as exc:
                self._disconnect(exc)
                raise CodexAppServerUnavailable("Codex app-server connection failed") from exc

    def _connect(
        self,
        *,
        timeout: float | None | object = _USE_DEFAULT_REQUEST_TIMEOUT,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> None:
        if self._transport is not None:
            return
        with self._connect_lock:
            if self._transport is not None:
                return
            try:
                transport = connect_transport(self.socket_path)
                sock = transport.sock
            except (OSError, ValueError, CodexAppServerUnavailable) as exc:
                raise CodexAppServerUnavailable(
                    f"Codex app-server socket unavailable: {self.socket_path}: {exc}"
                ) from exc
            with self._state_lock:
                self._socket = sock
                self._transport = transport
                self._closing = False
                self._reader_thread = threading.Thread(
                    target=self._reader_loop,
                    name="portal-codex-app-server-reader",
                    daemon=True,
                )
                self._reader_thread.start()
            try:
                self.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "tfi-portal-execution-worker",
                            "title": "TFI Portal",
                            "version": "1",
                        },
                        "capabilities": {
                            "experimentalApi": True,
                            # Full Access still needs real user answers to MCP
                            # forms. Advertise the input UI implemented by
                            # user_inputs; this does not grant tool approval.
                            "extensions": {"openai/standard-form-input": {}},
                        },
                    },
                    timeout=timeout,
                    cancellation_requested=cancellation_requested,
                )
                self.notify("initialized", {})
            except Exception:
                self._disconnect(CodexAppServerUnavailable("Codex app-server initialization failed"))
                raise
            self._ensure_maintenance_thread()

    def _ensure_maintenance_thread(self) -> None:
        with self._state_lock:
            if self._maintenance_thread and self._maintenance_thread.is_alive():
                return
            self._maintenance_thread = threading.Thread(
                target=self._maintenance_loop,
                name="portal-codex-app-server-maintenance",
                daemon=True,
            )
            self._maintenance_thread.start()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None | object = _USE_DEFAULT_REQUEST_TIMEOUT,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if self._transport is None:
            self._connect(
                timeout=timeout,
                cancellation_requested=cancellation_requested,
            )
        request_id = self._next_request_id()
        pending = _PendingRequest()
        with self._state_lock:
            self._pending[request_id] = pending
        try:
            self._send({"method": method, "id": request_id, "params": params or {}})
            deadline = (
                time.monotonic() + self.request_timeout_seconds
                if timeout is _USE_DEFAULT_REQUEST_TIMEOUT
                else (
                    None
                    if timeout is None
                    else time.monotonic() + max(0.0, float(timeout))
                )
            )
            while not pending.event.wait(
                0.25
                if deadline is None
                else min(0.25, max(0.0, deadline - time.monotonic()))
            ):
                if cancellation_requested and cancellation_requested():
                    raise InterruptedError(
                        f"Codex app-server {method} cancelled"
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Codex app-server {method} timed out"
                    )
            if pending.error:
                thread_id = str((params or {}).get("threadId") or "")
                if thread_id and str(pending.error) in {
                    f"Codex app-server request failed: thread not found: {thread_id}",
                    f"Codex app-server request failed: no rollout found for thread id {thread_id}",
                }:
                    self._forget_loaded_thread(thread_id)
                    raise CodexThreadNotFound(str(pending.error)) from pending.error
                raise pending.error
            return pending.result or {}
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._transport is None:
            self._connect()
        self._send({"method": method, "params": params or {}})

    def start_thread(
        self,
        params: dict[str, Any],
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        result = self.request(
            "thread/start",
            params,
            timeout=self.lifecycle_request_timeout_seconds,
            cancellation_requested=cancellation_requested,
        )
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        thread_id = str(thread.get("id") or "")
        if thread_id:
            with self._state_lock:
                self._thread_activity[thread_id] = time.monotonic()
                self._unmaterialized_threads.add(thread_id)
                if "config" in params:
                    self._thread_configs[thread_id] = _config_fingerprint(
                        params.get("config")
                    )
        return result


    def fork_thread(
        self,
        params: dict[str, Any],
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        result = self.request(
            "thread/fork",
            params,
            timeout=self.lifecycle_request_timeout_seconds,
            cancellation_requested=cancellation_requested,
        )
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        thread_id = str(thread.get("id") or "")
        if thread_id:
            with self._state_lock:
                self._thread_activity[thread_id] = time.monotonic()
                if "config" in params:
                    self._thread_configs[thread_id] = _config_fingerprint(
                        params.get("config")
                    )
        return result

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
        with self._state_lock:
            already_loaded = safe_thread_id in self._thread_activity
        if not already_loaded:
            self.request(
                "thread/resume",
                {"threadId": safe_thread_id, "excludeTurns": True, **({"config": config} if config is not None else {})},
                timeout=self.lifecycle_request_timeout_seconds,
                cancellation_requested=cancellation_requested,
            )
            with self._state_lock:
                self._thread_activity[safe_thread_id] = time.monotonic()
        waiter = _CompactWaiter(thread_id=safe_thread_id)
        with self._state_lock:
            if safe_thread_id in self._compact_waiters:
                raise CodexAppServerError(f"Codex thread {safe_thread_id} is already compacting")
            self._compact_waiters[safe_thread_id] = waiter
            self._busy_threads.add(safe_thread_id)
        try:
            total_timeout = max(
                0.01,
                float(
                    DEFAULT_COMPACT_TOTAL_TIMEOUT_SECONDS
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            )
            no_progress_timeout = max(
                0.01,
                float(
                    DEFAULT_COMPACT_NO_PROGRESS_TIMEOUT_SECONDS
                    if no_progress_timeout_seconds is None
                    else no_progress_timeout_seconds
                ),
            )
            poll_interval = max(
                0.01,
                float(
                    DEFAULT_COMPACT_STATUS_POLL_INTERVAL_SECONDS
                    if status_poll_interval_seconds is None
                    else status_poll_interval_seconds
                ),
            )
            started_at = time.monotonic()
            total_deadline = started_at + total_timeout
            last_state = ("", "")

            def check_total_deadline() -> None:
                now = time.monotonic()
                if now >= total_deadline:
                    raise TimeoutError(
                        f"Codex compact did not finish within "
                        f"{total_timeout:g} seconds for thread {safe_thread_id}"
                    )

            def check_no_progress_deadline() -> None:
                now = time.monotonic()
                if now - waiter.last_progress_at >= no_progress_timeout:
                    raise TimeoutError(
                        f"Codex compact made no progress for "
                        f"{no_progress_timeout:g} seconds for thread {safe_thread_id}"
                    )

            def observe_provider_state() -> dict[str, Any] | None:
                nonlocal last_state
                active_turn = self._read_active_turn(safe_thread_id)
                state = (
                    str((active_turn or {}).get("id") or ""),
                    str((active_turn or {}).get("status") or "").casefold(),
                )
                if state != last_state:
                    last_state = state
                    waiter.last_progress_at = time.monotonic()
                return active_turn

            self.request(
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
                if cancellation_requested and cancellation_requested():
                    raise InterruptedError(
                        f"Codex compact cancelled for thread {safe_thread_id}"
                    )
                check_total_deadline()
                observe_provider_state()
                check_no_progress_deadline()
            if waiter.error:
                raise waiter.error
            # `thread/compacted` reports that the compacted context has been
            # committed, but the provider's technical compaction turn can
            # remain active briefly afterwards.  Do not expose the thread to
            # the next turn until that turn is terminal; otherwise its delayed
            # turn notifications can be consumed by the next turn's waiter.
            while observe_provider_state() is not None:
                if cancellation_requested and cancellation_requested():
                    raise InterruptedError(
                        f"Codex compact cancelled for thread {safe_thread_id}"
                    )
                check_total_deadline()
                check_no_progress_deadline()
                waiter.event.wait(poll_interval)
            return dict(waiter.token_usage)
        finally:
            with self._state_lock:
                self._compact_waiters.pop(safe_thread_id, None)
                self._busy_threads.discard(safe_thread_id)
                if safe_thread_id in self._thread_activity:
                    self._thread_activity[safe_thread_id] = time.monotonic()

    def _forget_loaded_thread(self, thread_id: str) -> None:
        with self._state_lock:
            self._thread_activity.pop(thread_id, None)
            self._thread_configs.pop(thread_id, None)
            self._unmaterialized_threads.discard(thread_id)

    def unsubscribe_thread(self, thread_id: str) -> None:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            return
        with self._locked_thread(safe_thread_id):
            self.request("thread/unsubscribe", {"threadId": safe_thread_id}, timeout=45.0)
            self._forget_loaded_thread(safe_thread_id)

    def interrupt_turn(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        safe_thread_id = str(thread_id or "").strip()
        safe_turn_id = str(turn_id or "").strip()
        if not safe_thread_id or not safe_turn_id:
            raise ValueError("thread_id and turn_id are required")
        params = {"threadId": safe_thread_id, "turnId": safe_turn_id}
        pending = None
        owns_request = False
        with self._state_lock:
            waiter = self._turn_waiters.get(safe_thread_id)
            if waiter is not None and waiter.turn_id == safe_turn_id:
                pending = waiter.interrupt_requests.get(safe_turn_id)
                if pending is None:
                    completed = waiter.completed if isinstance(waiter.completed, dict) else {}
                    turn = completed.get("turn")
                    if (
                        isinstance(turn, dict)
                        and str(completed.get("threadId") or safe_thread_id) == safe_thread_id
                        and str(turn.get("id") or "") == safe_turn_id
                        and str(turn.get("status") or "").strip().casefold()
                        in {"completed", "interrupted", "failed"}
                    ):
                        return {}
                    pending = _PendingRequest()
                    waiter.interrupt_requests[safe_turn_id] = pending
                    owns_request = True
        if pending is None:
            return self.request("turn/interrupt", params)
        if owns_request:
            try:
                pending.result = self.request("turn/interrupt", params)
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
        return self.request(
            "turn/steer",
            params,
            timeout=self.lifecycle_request_timeout_seconds,
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
        return self.request(
            "turn/settings/update",
            {
                "threadId": safe_thread_id,
                "turnId": safe_turn_id,
                "serviceTier": safe_service_tier or None,
            },
            timeout=self.lifecycle_request_timeout_seconds,
            cancellation_requested=cancellation_requested,
        )

    def execute_command(
        self,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "command/exec",
            params,
            timeout=timeout,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "connected": self._transport is not None,
                "loaded_threads": len(self._thread_activity),
                "busy_threads": len(self._busy_threads),
                "active_turns": len(self._turn_waiters),
                "compacting_threads": len(self._compact_waiters),
            }

    def _reader_loop(self) -> None:
        transport = self._transport
        if transport is None:
            return
        failure: BaseException = CodexAppServerUnavailable("Codex app-server connection closed")
        try:
            while True:
                raw = transport.recv_text()
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(message, dict):
                    continue
                self._dispatch_message(message)
        except Exception as exc:
            failure = exc
        finally:
            self._disconnect(failure)

    def _dispatch_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if request_id is not None and not method:
            try:
                numeric_id = int(request_id)
            except (TypeError, ValueError):
                return
            with self._state_lock:
                pending = self._pending.get(numeric_id)
            if pending:
                if isinstance(message.get("error"), dict):
                    error = message["error"]
                    provider_error = _terminal_turn_error("", {
                        "status": "failed", "error": error,
                    })
                    error_message = (
                        "Codex app-server request failed: "
                        f"{error.get('message') or error}"
                    )
                    pending.error = (
                        provider_error
                        if getattr(provider_error, "error_details", {}).get("retryable") is False
                        else CodexThreadWriterBusy(error_message)
                        if _is_loaded_thread_writer_conflict(error_message)
                        else CodexAppServerError(error_message)
                    )
                else:
                    pending.result = message.get("result") if isinstance(message.get("result"), dict) else {}
                pending.event.set()
            return
        if request_id is not None and method:
            self._answer_server_request(
                request_id,
                method,
                message.get("params"),
            )
            return
        if method:
            self._dispatch_notification(method, message.get("params"))

    def _answer_server_request(
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
        with self._state_lock:
            runs_turn = thread_id in self._turn_waiters or thread_id in self._goal_waiters
        if method == "item/tool/call":
            with self._state_lock:
                if thread_id in self._detaching_threads:
                    # App-server owns the unanswered request. The next follower
                    # must receive it; never send an invented cancellation result.
                    return
                handler = self._dynamic_tool_handlers.get(thread_id)
                if handler is not None:
                    self._active_dynamic_calls[thread_id] = self._active_dynamic_calls.get(thread_id, 0) + 1
            if handler is None and not runs_turn:
                return
            if handler is None:
                self._send({
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
                target=self._run_dynamic_tool_request,
                args=(request_id, handler, params),
                name="portal-codex-dynamic-tool",
                daemon=True,
            ).start()
            return
        if method in {"item/tool/requestUserInput",
                        "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                        "item/permissions/requestApproval", "mcpServer/elicitation/request"}:
            if not runs_turn:
                return
            try:
                waiter = self._turn_waiters.get(thread_id) or self._goal_waiters.get(thread_id)
                if waiter is None or waiter.event_callback is None:
                    raise ValueError("this client has no user input surface for the requested thread")
                native_input = method == "item/tool/requestUserInput"
                if method == "mcpServer/elicitation/request":
                    params = {**params, "turnId": params.get("turnId") or getattr(waiter, "turn_id", ""),
                              "itemId": "mcp-elicitation:" + str(request_id)}
                self.user_inputs.accept(request_id, params, "" if native_input else method)
            except ValueError as exc:
                self._send({"id": request_id, "error": {"code": -32602, "message": str(exc)}})
            return
        else:
            self._send({"id": request_id, "error": {"code": -32601, "message": f"Unsupported client request: {method}"}})
            return

    def _run_dynamic_tool_request(
        self,
        request_id: object,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        params: dict[str, Any],
    ) -> None:
        try:
            result = handler(dict(params))
            if not isinstance(result, dict):
                raise TypeError("Dynamic tool dispatcher must return an object")
            self._send({"id": request_id, "result": result})
        except Exception as exc:
            self._send({
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": f"Dynamic tool dispatch failed: {exc}",
                },
            })
        finally:
            thread_id = str(params.get("threadId") or "")
            with self._state_lock:
                remaining = self._active_dynamic_calls.get(thread_id, 0) - 1
                if remaining > 0:
                    self._active_dynamic_calls[thread_id] = remaining
                else:
                    self._active_dynamic_calls.pop(thread_id, None)

    def _detach_if_requested(
        self, thread_id: str, turn_id: str,
        detach_requested: Callable[[], bool] | None,
    ) -> None:
        if detach_requested is None or not detach_requested():
            return
        with self._state_lock:
            self._detaching_threads.add(thread_id)
            if self._active_dynamic_calls.get(thread_id, 0):
                return  # A call already started here must reply before release.
        raise CodexTurnDetached(thread_id, turn_id)

    def _route_child_notification(self, method: str, params: dict[str, Any]) -> bool:
        """Deliver descendants to their active root, never to another thread's turn.

        A child can publish before spawn completion. Keep a bounded transport
        buffer until an explicit parent edge is known; body equality is irrelevant.
        """
        thread = params.get("thread") or {}
        thread_id = str(params.get("threadId") or thread.get("id") or "")
        item = params.get("item") or {}
        edges: list[tuple[str, str, str]] = []
        source = thread.get("source") or {}
        sub = source.get("subAgent", {}) if isinstance(source, dict) else {}
        spawn = sub.get("thread_spawn", {}) if isinstance(sub, dict) else {}
        if method == "thread/started" and spawn.get("parent_thread_id"):
            edges.append((thread_id, str(spawn["parent_thread_id"]), ""))
        if item.get("type") == "collabAgentToolCall" and item.get("tool") == "spawnAgent":
            edges.extend((str(child), thread_id, str(item.get("id") or ""))
                         for child in item.get("receiverThreadIds") or [] if child)
        # Codex 0.162 tells the parent a subagent started or ended; nothing of
        # the child's own thread reaches a client that did not join it.
        activity_child = str(item.get("agentThreadId") or "") if item.get("type") == "subAgentActivity" else ""
        # A finished child the parent gives more work (``followup_task``) is ``interacted``.
        activity_starts = activity_child and item.get("kind") in {"started", "interacted"}
        if activity_starts:
            edges.append((activity_child, thread_id, str(item.get("id") or "")))
        follow: list[str] = []
        release: list[str] = []
        replay: list[tuple[str, dict[str, Any]]] = []
        with self._state_lock:
            roots = {**self._goal_waiters, **self._turn_waiters}
            # Parent chains belong to the connection, but only active executions consume them.
            def root_of(child: str) -> str:
                seen: set[str] = set()
                while child in self._child_threads and child not in seen:
                    seen.add(child)
                    child = self._child_threads[child][0]
                return child
            for child, parent, call in edges:
                # A grandchild can be announced before its parent's edge.
                # Record the explicit relationship now; route only after the
                # entire chain reaches an active root below.
                if child and parent and child != parent:
                    self._child_threads[child] = (parent, call or child)
            if edges:
                pending = list(self._unclaimed_child_events)
                self._unclaimed_child_events.clear()
                for entry in pending:
                    child = str(entry[1].get("threadId") or "")
                    if child in self._child_threads and root_of(child) in roots:
                        replay.append(entry)
                    else:
                        self._unclaimed_child_events.append(entry)
            is_root = thread_id in roots
            root = root_of(thread_id)
            waiter = roots.get(root) if thread_id in self._child_threads else None
            if thread_id in self._child_threads:
                child_turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                child_turn_id = str(child_turn.get("id") or params.get("turnId") or "")
                # The turn a stop interrupts: an ended turn's late items do not bring it back.
                if method == "turn/completed":
                    self._child_turns.pop(thread_id, None)
                elif child_turn_id and method in {"turn/started", "item/started"}:
                    self._child_turns[thread_id] = child_turn_id
            ancestors = []
            parent = self._child_threads.get(thread_id, ("", ""))[0]
            while parent in self._child_threads and parent not in ancestors:
                ancestors.append(parent)
                parent = self._child_threads[parent][0]
            if waiter is None and not is_root and roots and method in {"item/started", "item/completed"}:
                self._unclaimed_child_events.append((method, params))
            if activity_starts:
                if root_of(activity_child) in roots and activity_child not in self._followed_children:
                    self._followed_children.add(activity_child)
                    follow.append(activity_child)
            elif activity_child and item.get("kind") in {"completed", "interrupted"}:
                self._child_turns.pop(activity_child, None)
                if activity_child in self._followed_children:
                    self._followed_children.discard(activity_child)
                    release.append(activity_child)
            # Forget edges with no active owner. The source may announce them again on resume.
            if len(self._child_threads) > 1024:
                self._child_threads = {child: edge for child, edge in self._child_threads.items()
                                       if root_of(child) in roots}
                self._child_turns = {child: turn for child, turn in self._child_turns.items()
                                     if child in self._child_threads}
        for replay_method, replay_params in replay:
            self._route_child_notification(replay_method, replay_params)
        for child in follow + release:
            # A request waits for this reader thread's next message: never block it.
            threading.Thread(
                target=self._follow_child if child in follow else self._release_child,
                args=(child,), name="portal-codex-subagent-follow", daemon=True,
            ).start()
        if waiter is not None and waiter.event_callback:
            try:
                waiter.event_callback({"method": "subagent/event", "params": {
                    "traceKey": thread_id, "agentId": thread_id,
                    "ancestorTraceKeys": list(reversed(ancestors)),
                    "parentToolUseId": self._child_threads[thread_id][1],
                    "event": {"method": method, "params": params},
                }})
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Child event projection failed: thread=%s method=%s error_type=%s",
                    thread_id, method, type(error).__name__,
                )
            return True
        return not is_root and method == "thread/started"

    def _follow_child(self, child: str) -> None:
        """Join a running subagent thread; its own notifications then reach this client."""

        try:
            result = self.request("thread/resume", {"threadId": child, "excludeTurns": True},
                                  timeout=self.lifecycle_request_timeout_seconds)
        except Exception as error:
            with self._state_lock:
                self._followed_children.discard(child)
            logging.getLogger(__name__).warning(
                "Subagent thread could not be followed: thread=%s error_type=%s", child, type(error).__name__)
            return
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        if thread:
            # Who the subagent is (its nickname and role) comes with the thread.
            self._route_child_notification("thread/started", {"thread": {**thread, "id": child}})

    def _release_child(self, child: str) -> None:
        try:
            self.request("thread/unsubscribe", {"threadId": child}, timeout=45.0)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Subagent thread could not be released: thread=%s error_type=%s", child, type(error).__name__)

    def _dispatch_notification(self, method: str, raw_params: object) -> None:
        params = raw_params if isinstance(raw_params, dict) else {}
        if self._route_child_notification(method, params):
            return
        thread_id = str(params.get("threadId") or "")
        if not thread_id:
            return
        status = params.get("status") if isinstance(params.get("status"), dict) else {}
        thread_unloaded = method == "thread/closed" or (
            method == "thread/status/changed" and status.get("type") == "notLoaded"
        )
        with self._state_lock:
            pending_waiter = self._turn_waiters.get(thread_id)
            if pending_waiter is not None and not pending_waiter.turn_id and not thread_unloaded:
                pending_waiter.pending_notifications.append((method, params))
                return
        if thread_id in self._turn_waiters:
            artifacts = self.turn_artifacts.observe(method, params)
            if artifacts is not None:
                self._dispatch_notification("item/completed", artifacts)
        if method == "serverRequest/resolved":
            if params.get("requestId") is not None:
                self.user_inputs.expire(thread_id, wire_id=params["requestId"])
            return
        if method == "turn/completed":
            turn = params.get("turn") or {}
            self.user_inputs.expire(thread_id, turn_id=str(turn.get("id") or params.get("turnId") or ""))
        if thread_unloaded:
            self.user_inputs.expire(thread_id)
            self._forget_loaded_thread(thread_id)
        notification_error = None
        if method == "error":
            error = _terminal_turn_error(thread_id, {
                "id": params.get("turnId") or "",
                "status": "failed",
                "error": params.get("error"),
            })
            # Explicit rejection takes precedence over transport retry hints.
            if (
                params.get("willRetry") is False
                or getattr(error, "error_details", {}).get("retryable") is False
            ):
                notification_error = error
        with self._state_lock:
            waiter = self._turn_waiters.get(thread_id)
            goal_waiter = self._goal_waiters.get(thread_id)
            compact_waiter = self._compact_waiters.get(thread_id)
            if thread_id in self._thread_activity:
                self._thread_activity[thread_id] = time.monotonic()
        if compact_waiter is not None:
            if notification_error is not None:
                _fail_waiter(compact_waiter, notification_error)
            compact_waiter.last_progress_at = time.monotonic()
            if method == "thread/tokenUsage/updated":
                _collect_token_usage(compact_waiter, params.get("tokenUsage"))
            compact_completed = method == "thread/compacted"
            if method == "item/completed":
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                compact_completed = is_context_compaction_item(item)
            if compact_completed:
                compact_waiter.event.set()
        if goal_waiter is not None:
            if notification_error is not None:
                _fail_waiter(goal_waiter, notification_error)
            turn_id = str(
                params.get("turnId")
                or (params.get("turn") or {}).get("id")
                or ""
            )
            if turn_id:
                goal_waiter.turn_id = turn_id
            if goal_waiter.event_callback:
                try:
                    goal_waiter.event_callback({"method": method, "params": params})
                except Exception:
                    logging.getLogger(__name__).exception("Codex goal event observation failed")
            if method == "thread/tokenUsage/updated":
                _collect_token_usage(goal_waiter, params.get("tokenUsage"))
            elif method == "item/completed":
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                _collect_agent_message(goal_waiter, item)
            elif method == "thread/goal/updated":
                goal = params.get("goal")
                if isinstance(goal, dict):
                    goal_waiter.goal = dict(goal)
                    if str(goal.get("status") or "") != "active":
                        goal_waiter.event.set()
            elif method == "thread/goal/cleared":
                goal_waiter.goal = None
                goal_waiter.event.set()
        if waiter is None:
            return
        turn_id = str(params.get("turnId") or (params.get("turn") or {}).get("id") or "")
        if (
            waiter.turn_id
            and turn_id
            and waiter.turn_id != turn_id
            and method != "thread/goal/updated"
        ):
            return
        if method in {"item/started", "item/completed"}:
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if (
                item.get("type") == "agentMessage"
                and str(item.get("phase") or "").strip().casefold() == "final_answer"
            ):
                waiter.final_answer_seen = True
        if method == "item/completed":
            item = (
                params.get("item")
                if isinstance(params.get("item"), dict)
                else {}
            )
            item_id = str(item.get("id") or "").strip()
            if item_id:
                with self._state_lock:
                    if item_id in waiter.completed_item_ids:
                        return
                    waiter.completed_item_ids.add(item_id)
        if method == "turn/completed" and waiter.final_answer_items:
            completed_turn = params.get("turn") or {}
            if not any(isinstance(item, dict) and item.get("phase") == "final_answer"
                       for item in completed_turn.get("items") or []):
                params = {**params, "turn": {**completed_turn, "items": [
                    *(completed_turn.get("items") or []), *waiter.final_answer_items.values(),
                ]}}
        if waiter.event_callback:
            try:
                waiter.event_callback({"method": method, "params": params})
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Provider event projection failed: thread=%s turn=%s method=%s error_type=%s",
                    thread_id, turn_id, method, type(error).__name__,
                )
                if method == "item/completed":
                    with self._state_lock:
                        waiter.completed_item_ids.discard(item_id)
                    return
        if method == "error":
            if notification_error is not None:
                _fail_waiter(waiter, notification_error)
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
            _collect_token_usage(waiter, params.get("tokenUsage"))
        elif method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if _collect_agent_message(waiter, item):
                if (
                    str(item.get("phase") or "").strip().casefold()
                    == "final_answer"
                ):
                    waiter.final_answer_seen = True
                    waiter.final_answer_items[str(item.get("id") or item.get("text"))] = dict(item)
        elif method == "turn/completed":
            waiter.completed = params
            waiter.event.set()

    def _bind_turn_waiter(self, waiter: _TurnWaiter, turn_id: str) -> None:
        if not turn_id:
            raise CodexAppServerError("Codex did not identify the accepted turn")
        with self._state_lock:
            waiter.turn_id = turn_id
            pending = waiter.pending_notifications
            waiter.pending_notifications = []
        for method, params in pending:
            event_turn_id = str(params.get("turnId") or (params.get("turn") or {}).get("id") or "")
            if event_turn_id and event_turn_id != turn_id:
                continue
            self._dispatch_notification(method, params)

    def _replay_completed_items(
        self,
        waiter: _TurnWaiter,
        turn: Mapping[str, Any],
    ) -> None:
        for item in turn.get("items") or []:
            if not isinstance(item, dict):
                continue
            if (
                item.get("type") == "agentMessage"
                and str(item.get("phase") or "").strip().casefold() == "final_answer"
            ):
                waiter.final_answer_seen = True
                waiter.final_answer_items[str(item.get("id") or item.get("text"))] = dict(item)
            if waiter.event_callback is None:
                continue
            if str(turn.get("status") or "").casefold() in {"inprogress"}:
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
                if (
                    not completed_message
                    and item.get("type") not in {"subAgentActivity", "webSearch", "imageView"}
                    and item_status not in {"completed", "failed", "interrupted"}
                ):
                    continue
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                item_id = hashlib.sha256(
                    json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            with self._state_lock:
                if item_id in waiter.completed_item_ids:
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
                self._route_child_notification(event["method"], event["params"])
                waiter.event_callback(event)
            except Exception as error:
                with self._state_lock:
                    waiter.completed_item_ids.discard(item_id)
                logging.getLogger(__name__).warning(
                    "Provider item replay failed: thread=%s turn=%s item=%s error_type=%s",
                    waiter.thread_id, waiter.turn_id, item_id, type(error).__name__,
                )
                continue
            if _collect_agent_message(waiter, item):
                if (
                    str(item.get("phase") or "").strip().casefold()
                    == "final_answer"
                ):
                    waiter.final_answer_seen = True
                    waiter.final_answer_items[str(item.get("id") or item.get("text"))] = dict(item)

    @staticmethod
    def _wait_for_turn_signal(waiter: _TurnWaiter, has_controls: bool) -> bool:
        return waiter.event.wait(
            TURN_CONTROL_POLL_INTERVAL_SECONDS if has_controls
            else TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS
        )

    @staticmethod
    def _turn_reconcile_due(waiter: _TurnWaiter) -> bool:
        now = time.monotonic()
        if not waiter.event.is_set() and now < waiter.next_reconcile_at:
            return False
        waiter.next_reconcile_at = now + TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS
        return True

    def _read_terminal_turn(
        self,
        waiter: _TurnWaiter,
    ) -> dict[str, Any] | None:
        if not waiter.turn_id:
            return None
        turn = self.read_turn(waiter.thread_id, waiter.turn_id)
        if turn is None:
            return None
        # Active snapshots can contain reconstructed display IDs too. Normalize
        # message identity before replay so polling and live notifications share
        # the same deduplication key throughout the turn.
        if waiter.turn_snapshot_transform is not None:
            turn = waiter.turn_snapshot_transform(turn)
        self._replay_completed_items(waiter, turn)
        if str(turn.get("status") or "") == "inProgress":
            return None
        return turn

    def read_turn(self, thread_id: str, turn_id: str) -> dict[str, Any] | None:
        """Read one exact turn snapshot without starting or resuming work."""
        params: dict[str, Any] = {
            "threadId": thread_id, "limit": 5,
            "sortDirection": "desc", "itemsView": "full",
        }
        deadline = time.monotonic() + min(45.0, self.request_timeout_seconds)
        cursors: set[str] = set()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Codex exact turn lookup timed out: {turn_id}")
            result = self.request("thread/turns/list", params, timeout=remaining)
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

    def _read_terminal_goal_successor(
        self,
        waiter: _TurnWaiter,
    ) -> dict[str, Any] | None:
        if not waiter.turn_id or waiter.terminal_goal is None:
            return None
        result = self.request(
            "thread/turns/list",
            {
                "threadId": waiter.thread_id,
                "limit": 5,
                "sortDirection": "desc",
                "itemsView": "full",
            },
            timeout=min(45.0, self.request_timeout_seconds),
        )
        turns = result.get("data") if isinstance(result.get("data"), list) else []
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

    def _read_thread_status(self, thread_id: str) -> dict[str, Any]:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        result = self.request(
            "thread/read",
            {"threadId": safe_thread_id, "includeTurns": False},
            timeout=min(45.0, self.request_timeout_seconds),
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        status = thread.get("status") if isinstance(thread, dict) else None
        return dict(status) if isinstance(status, dict) else {}

    @staticmethod
    def _raise_thread_system_error(
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

    def active_turn_id(self, thread_id: str) -> str:
        """The id of the thread's turn in progress, or ``""``."""

        turn = self._read_active_turn(thread_id)
        return str((turn or {}).get("id") or "")

    def _read_active_turn(
        self, thread_id: str, *, include_items: bool = False,
    ) -> dict[str, Any] | None:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        result = self.request(
            "thread/turns/list",
            {
                "threadId": safe_thread_id,
                "limit": 1,
                "sortDirection": "desc",
                "itemsView": "full" if include_items else "summary",
            },
            timeout=min(45.0, self.request_timeout_seconds),
        )
        turns = result.get("data") if isinstance(result.get("data"), list) else []
        turn = turns[0] if turns and isinstance(turns[0], dict) else None
        if not isinstance(turn, dict):
            return None
        status = str(turn.get("status") or "").strip().casefold()
        return turn if status in {"inprogress"} else None

    def _wait_for_compaction_release(
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
            if self._read_active_turn(thread_id) is None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexCompactionInProgress(
                    "Codex context compaction is still active; "
                    "execution will retry after the thread becomes writable"
                )
            time.sleep(min(TURN_CONTROL_POLL_INTERVAL_SECONDS, remaining))

    def _deliver_turn_control(
        self,
        *,
        thread_id: str,
        waiter: _TurnWaiter,
        turn_control_provider: Callable[[], dict[str, Any] | None],
        turn_control_result_callback: (
            Callable[[dict[str, Any], bool, str], None] | None
        ),
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
                    pass
            return None
        if control is None:
            return None
        if waiter.final_answer_seen or waiter.completed is not None:
            return None
        applied = False
        error = ""
        try:
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
                if not applied:
                    error = status or "targetUnavailable"
            elif len(response_input) == 1 and response_input[0].get("type") == "user_input_response":
                self.user_inputs.respond(thread_id, waiter.turn_id, response_input[0])
                applied = True
            elif len(response_input) == 1 and response_input[0].get("type") == "subagent_stop":
                applied, error = self._stop_subagent(thread_id, str(response_input[0].get("tool_use_id") or ""))
            elif action != "steer":
                raise ValueError("unsupported turn control action")
            else:
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
                applied = True
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

    def _stop_subagent(self, thread_id: str, child: str) -> tuple[bool, str]:
        """Interrupt the turn of one subagent thread of ``thread_id``; the thread's own turn goes on.

        A Codex subagent's card names its thread (``codex_subagent_cards``).
        """

        with self._state_lock:
            known = bool(child) and self._child_threads.get(child, ("", ""))[0] == thread_id
            turn_id = self._child_turns.get(child, "")
        if not known:
            return False, "unknown_subagent"
        turn_id = turn_id or self.active_turn_id(child)
        if not turn_id:
            return False, "subagent_not_running"
        self.interrupt_turn(child, turn_id)
        return True, ""

    def _read_task_complete_goal(self, thread_id: str) -> dict[str, Any] | None:
        result = self.request(
            "thread/goal/get",
            {"threadId": thread_id},
            timeout=min(45.0, self.request_timeout_seconds),
        )
        goal = result.get("goal") if isinstance(result, dict) else None
        if not isinstance(goal, dict):
            return None
        status = str(goal.get("status") or "").strip().lower()
        return dict(goal) if status in TASK_COMPLETE_GOAL_STATUSES else None

    def _disconnect(self, error: BaseException) -> None:
        self.turn_artifacts.clear()
        self.user_inputs.expire()
        with self._state_lock:
            sock, transport = self._socket, self._transport
            self._socket = None
            self._transport = None
            pending = list(self._pending.values())
            waiters = list(self._turn_waiters.values())
            goal_waiters = list(self._goal_waiters.values())
            compact_waiters = list(self._compact_waiters.values())
            self._thread_activity.clear()
            self._thread_configs.clear()
            self._unmaterialized_threads.clear()
            self._followed_children.clear()
            self._child_threads.clear()
            self._child_turns.clear()
            self._unclaimed_child_events.clear()
        try:
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            if transport is not None:
                transport.close()
        except Exception:
            pass
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
        for item in pending:
            _fail_waiter(item, CodexAppServerUnavailable(str(error)))
        for waiter in waiters:
            _fail_waiter(waiter, CodexTurnConnectionLost(waiter.thread_id, waiter.turn_id))
        for waiter in goal_waiters:
            _fail_waiter(waiter, CodexTurnConnectionLost(waiter.thread_id, waiter.turn_id))
        for waiter in compact_waiters:
            _fail_waiter(waiter, CodexAppServerUnavailable(str(error)))

    def _thread_lock(self, thread_id: str) -> threading.Lock:
        with self._state_lock:
            return self._thread_locks.setdefault(thread_id, threading.Lock())

    @contextmanager
    def _locked_thread(
        self,
        thread_id: str,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ):
        """Serialize one thread without hiding cancellation behind a lock wait."""

        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        lock = self._thread_lock(safe_thread_id)
        acquired = False
        try:
            while not acquired:
                if cancellation_requested and cancellation_requested():
                    raise InterruptedError(
                        f"Codex thread lock cancelled for {safe_thread_id}"
                    )
                acquired = lock.acquire(timeout=TURN_CONTROL_POLL_INTERVAL_SECONDS)
            yield
        finally:
            if acquired:
                lock.release()

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
        if safe_action not in {"set", "resume", "pause", "clear", "observe"}:
            raise ValueError("unsupported Codex goal action")
        safe_objective = str(objective or "").strip()
        if safe_action == "set" and not safe_objective:
            raise ValueError("Codex goal objective required")

        with self._locked_thread(
            safe_thread_id,
            cancellation_requested=cancellation_requested,
        ):
            with self._state_lock:
                self._busy_threads.add(safe_thread_id)
            waiter = _GoalWaiter(
                thread_id=safe_thread_id,
                event_callback=event_callback,
            )
            try:
                with self._state_lock:
                    self._goal_waiters[safe_thread_id] = waiter
                    already_loaded = safe_thread_id in self._thread_activity
                    loaded_config = self._thread_configs.get(safe_thread_id)
                    unmaterialized = safe_thread_id in self._unmaterialized_threads
                requested_config = (
                    _config_fingerprint(config)
                    if config is not None
                    else None
                )
                if (
                    already_loaded
                    and requested_config is not None
                    and loaded_config != requested_config
                    and not unmaterialized
                ):
                    self.request(
                        "thread/unsubscribe",
                        {"threadId": safe_thread_id},
                        timeout=self.lifecycle_request_timeout_seconds,
                        cancellation_requested=cancellation_requested,
                    )
                    with self._state_lock:
                        self._thread_activity.pop(safe_thread_id, None)
                        self._thread_configs.pop(safe_thread_id, None)
                    already_loaded = False
                if not already_loaded:
                    resume_params: dict[str, Any] = {
                        "threadId": safe_thread_id,
                        "excludeTurns": True,
                    }
                    if str(cwd or "").strip():
                        resume_params["cwd"] = str(cwd)
                    if config is not None:
                        resume_params["config"] = dict(config)
                    self.request(
                        "thread/resume",
                        resume_params,
                        timeout=self.lifecycle_request_timeout_seconds,
                        cancellation_requested=cancellation_requested,
                    )
                with self._state_lock:
                    self._thread_activity[safe_thread_id] = time.monotonic()
                    if requested_config is not None and not unmaterialized:
                        self._thread_configs[safe_thread_id] = requested_config
                    if dynamic_tool_handler is not None:
                        self._dynamic_tool_handlers[safe_thread_id] = (
                            dynamic_tool_handler
                        )

                current = self.request(
                    "thread/goal/get",
                    {"threadId": safe_thread_id},
                    timeout=self.lifecycle_request_timeout_seconds,
                    cancellation_requested=cancellation_requested,
                )
                current_goal = current.get("goal") if isinstance(current, dict) else None
                if safe_action == "clear":
                    self.request(
                        "thread/goal/clear",
                        {"threadId": safe_thread_id},
                        timeout=self.lifecycle_request_timeout_seconds,
                        cancellation_requested=cancellation_requested,
                    )
                    waiter.goal = None
                    return CodexGoalResult(safe_thread_id, "", "Goal cleared.", None, {})

                if safe_action == "set":
                    if not (
                        isinstance(current_goal, dict)
                        and str(current_goal.get("objective") or "").strip() == safe_objective
                        and str(current_goal.get("status") or "") == "active"
                    ):
                        params: dict[str, Any] = {
                            "threadId": safe_thread_id,
                            "objective": safe_objective,
                            "status": "active",
                        }
                        if token_budget is not None:
                            params["tokenBudget"] = max(1, int(token_budget))
                        updated = self.request(
                            "thread/goal/set",
                            params,
                            timeout=self.lifecycle_request_timeout_seconds,
                            cancellation_requested=cancellation_requested,
                        )
                        current_goal = updated.get("goal") if isinstance(updated, dict) else None
                elif safe_action in {"resume", "pause"}:
                    updated = self.request(
                        "thread/goal/set",
                        {
                            "threadId": safe_thread_id,
                            "status": "active" if safe_action == "resume" else "paused",
                        },
                        timeout=self.lifecycle_request_timeout_seconds,
                        cancellation_requested=cancellation_requested,
                    )
                    current_goal = updated.get("goal") if isinstance(updated, dict) else None

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

                last_reconcile = time.monotonic()
                while not waiter.event.wait(0.25):
                    if waiter.error:
                        raise waiter.error
                    self._detach_if_requested(safe_thread_id, waiter.turn_id, detach_requested)
                    if cancellation_requested and cancellation_requested():
                        try:
                            self.request(
                                "thread/goal/set",
                                {"threadId": safe_thread_id, "status": "paused"},
                                timeout=30.0,
                            )
                        finally:
                            if waiter.turn_id:
                                try:
                                    self.interrupt_turn(safe_thread_id, waiter.turn_id)
                                except Exception:
                                    pass
                        raise InterruptedError(f"Codex goal paused for {safe_thread_id}")
                    if time.monotonic() - last_reconcile < 5.0:
                        continue
                    last_reconcile = time.monotonic()
                    observed = self.request(
                        "thread/goal/get",
                        {"threadId": safe_thread_id},
                        timeout=45.0,
                    )
                    observed_goal = observed.get("goal") if isinstance(observed, dict) else None
                    waiter.goal = dict(observed_goal) if isinstance(observed_goal, dict) else None
                    if not isinstance(waiter.goal, dict) or str(waiter.goal.get("status") or "") != "active":
                        waiter.event.set()
                if waiter.error:
                    raise waiter.error
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
                    self._detaching_threads.discard(safe_thread_id)
                    self._goal_waiters.pop(safe_thread_id, None)
                    self._dynamic_tool_handlers.pop(safe_thread_id, None)
                    self._busy_threads.discard(safe_thread_id)
                    if safe_thread_id in self._thread_activity:
                        self._thread_activity[safe_thread_id] = time.monotonic()

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
        dynamic_tool_handler: (
            Callable[[dict[str, Any]], dict[str, Any]] | None
        ) = None,
        turn_control_provider: (
            Callable[[], dict[str, Any] | None] | None
        ) = None,
        turn_control_result_callback: (
            Callable[[dict[str, Any], bool, str], None] | None
        ) = None,
    ) -> CodexTurnResult:
        safe_thread_id = str(thread_id or "").strip()
        if not safe_thread_id:
            raise ValueError("thread_id required")
        if cancellation_requested and cancellation_requested():
            raise InterruptedError(
                f"Codex turn cancelled before start for {safe_thread_id}"
            )
        with self._locked_thread(
            safe_thread_id,
            cancellation_requested=cancellation_requested,
        ):
            with self._state_lock:
                self._busy_threads.add(safe_thread_id)
            try:
                with self._state_lock:
                    already_loaded = safe_thread_id in self._thread_activity
                    loaded_config = self._thread_configs.get(safe_thread_id)
                    unmaterialized = (
                        safe_thread_id in self._unmaterialized_threads
                    )
                requested_config = (
                    _config_fingerprint(config)
                    if config is not None
                    else None
                )
                defer_config_reload = bool(
                    already_loaded
                    and requested_config is not None
                    and loaded_config != requested_config
                    and unmaterialized
                )
                if (
                    already_loaded
                    and requested_config is not None
                    and loaded_config != requested_config
                    and not defer_config_reload
                ):
                    self.request(
                        "thread/unsubscribe",
                        {"threadId": safe_thread_id},
                        timeout=self.lifecycle_request_timeout_seconds,
                        cancellation_requested=cancellation_requested,
                    )
                    with self._state_lock:
                        self._thread_activity.pop(safe_thread_id, None)
                        self._thread_configs.pop(safe_thread_id, None)
                    already_loaded = False
                resumed_thread_id = safe_thread_id
                if not already_loaded:
                    resume_params: dict[str, Any] = {
                        "threadId": safe_thread_id,
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
                        resumed = self.request(
                            "thread/resume",
                            resume_params,
                            timeout=self.lifecycle_request_timeout_seconds,
                            cancellation_requested=cancellation_requested,
                        )
                    except CodexAppServerError as exc:
                        # A fork is not persisted until its first turn starts. A
                        # different execution worker therefore cannot resume it,
                        # even though the returned thread id is valid for
                        # turn/start. Keep the same id and let turn/start
                        # materialize the fork. Any genuinely missing id will be
                        # rejected by turn/start itself.
                        if isinstance(exc, CodexThreadNotFound):
                            unmaterialized = True
                            with self._state_lock:
                                self._unmaterialized_threads.add(safe_thread_id)
                        else:
                            raise
                    else:
                        resumed_thread = (
                            resumed.get("thread")
                            if isinstance(resumed.get("thread"), dict)
                            else {}
                        )
                        resumed_thread_id = str(
                            resumed_thread.get("id") or safe_thread_id
                        )
                waiter = _TurnWaiter(thread_id=resumed_thread_id, event_callback=event_callback,
                                     turn_snapshot_transform=turn_snapshot_transform)
                with self._state_lock:
                    self._thread_activity[resumed_thread_id] = time.monotonic()
                    if requested_config is not None and not defer_config_reload:
                        self._thread_configs[resumed_thread_id] = requested_config
                    self._turn_waiters[resumed_thread_id] = waiter
                    if dynamic_tool_handler is not None:
                        self._dynamic_tool_handlers[resumed_thread_id] = (
                            dynamic_tool_handler
                        )
                active_turn = (
                    None
                    if unmaterialized or already_loaded
                    else self._read_active_turn(resumed_thread_id, include_items=True)
                )
                if active_turn is not None:
                    owns_input = bool(client_user_message_id) and any(
                        isinstance(item, dict) and item.get("type") == "userMessage"
                        and item.get("clientId") == client_user_message_id
                        for item in active_turn.get("items") or []
                    )
                    if not owns_input:
                        with self._state_lock:
                            self._turn_waiters.pop(resumed_thread_id, None)
                            self._dynamic_tool_handlers.pop(resumed_thread_id, None)
                        raise CodexThreadWriterBusy("Codex thread has another active input")
                    self._bind_turn_waiter(waiter, str(active_turn.get("id") or "").strip())
                turn_input: list[dict[str, Any]] = [
                    {"type": "text", "text": prompt}
                ]
                safe_image_detail = str(image_detail or "").strip().casefold()
                if safe_image_detail not in {"", "auto", "high", "original"}:
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
                    turn_input.append(image_input)
                for image_url in image_urls or []:
                    if not str(image_url or "").strip():
                        continue
                    image_input = {
                        "type": "image",
                        "url": str(image_url),
                    }
                    if safe_image_detail:
                        image_input["detail"] = safe_image_detail
                    turn_input.append(image_input)
                turn_params: dict[str, Any] = {
                    "threadId": resumed_thread_id,
                    "input": turn_input,
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
                    turn_params["collaborationMode"] = {
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
                        turn_params[key] = value
                if sandbox_policy is not None and not permissions:
                    turn_params["sandboxPolicy"] = dict(sandbox_policy)
                elif sandbox and not permissions:
                    turn_params["sandboxPolicy"] = _sandbox_policy(sandbox)
                turn_start_attempted = False
                start_method = "review/start" if review_target else "turn/start"
                if review_target:
                    turn_params = {"threadId": resumed_thread_id, "target": review_target, "delivery": "inline"}
                try:
                    if not waiter.turn_id:
                        if history_initializer is not None:
                            history_initializer(resumed_thread_id)
                        turn_start_attempted = True
                        try:
                            started = self.request(
                                start_method,
                                turn_params,
                                timeout=self.turn_start_request_timeout_seconds,
                                cancellation_requested=cancellation_requested,
                            )
                        except CodexAppServerError as exc:
                            if not _is_active_compaction_conflict(exc):
                                raise
                            # Automatic context compaction can start after the
                            # active-turn check above but before turn/start.
                            # The rejected request was not accepted, so it is
                            # safe to wait for that technical turn and retry
                            # exactly once. Ignore its notifications and state;
                            # they do not belong to the user's turn.
                            with self._state_lock:
                                if self._turn_waiters.get(resumed_thread_id) is waiter:
                                    self._detaching_threads.discard(resumed_thread_id)
                                    self._turn_waiters.pop(resumed_thread_id, None)
                            self._wait_for_compaction_release(
                                resumed_thread_id,
                                cancellation_requested=cancellation_requested,
                            )
                            waiter = _TurnWaiter(
                                thread_id=resumed_thread_id,
                                event_callback=event_callback,
                                turn_snapshot_transform=turn_snapshot_transform,
                            )
                            with self._state_lock:
                                self._turn_waiters[resumed_thread_id] = waiter
                            started = self.request(
                                start_method,
                                turn_params,
                                timeout=self.turn_start_request_timeout_seconds,
                                cancellation_requested=cancellation_requested,
                            )
                        turn = (
                            started.get("turn")
                            if isinstance(started.get("turn"), dict)
                            else {}
                        )
                        self._bind_turn_waiter(waiter, str(turn.get("id") or "").strip())
                        if waiter.turn_id and waiter.event_callback:
                            # This is a local receipt of the actual successful
                            # RPC, not a native item or an inference on replay.
                            waiter.event_callback({
                                "method": "portal/provider_request_accepted",
                                "params": {"threadId": resumed_thread_id,
                                           "turnId": waiter.turn_id,
                                           "rpcMethod": start_method},
                            })
                    with self._state_lock:
                        self._unmaterialized_threads.discard(
                            resumed_thread_id
                        )
                    if waiter.turn_id and turn_started_callback:
                        turn_started_callback(waiter.turn_id)
                    waiter.next_reconcile_at = (
                        time.monotonic()
                        + TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS
                    )
                    return self._follow_turn(
                        waiter, turn_started_callback=turn_started_callback,
                        cancellation_requested=cancellation_requested,
                        detach_requested=detach_requested,
                        turn_control_provider=turn_control_provider,
                        turn_control_result_callback=turn_control_result_callback,
                    )
                except (InterruptedError, TimeoutError):
                    if not waiter.turn_id:
                        for pending_method, pending_params in waiter.pending_notifications:
                            if pending_method == "turn/started":
                                accepted = pending_params.get("turn") or {}
                                waiter.turn_id = str(accepted.get("id") or pending_params.get("turnId") or "")
                                if waiter.turn_id:
                                    break
                    if waiter.turn_id:
                        try:
                            self.interrupt_turn(
                                resumed_thread_id,
                                waiter.turn_id,
                            )
                        except Exception:
                            pass
                    raise
                except CodexAppServerUnavailable as exc:
                    if turn_start_attempted:
                        raise CodexTurnConnectionLost(resumed_thread_id, waiter.turn_id) from exc
                    raise
                finally:
                    self.user_inputs.expire(resumed_thread_id)
                    with self._state_lock:
                        self._detaching_threads.discard(resumed_thread_id)
                        self._turn_waiters.pop(resumed_thread_id, None)
                        if dynamic_tool_handler is not None:
                            self._dynamic_tool_handlers.pop(
                                resumed_thread_id,
                                None,
                            )
                        if resumed_thread_id in self._thread_activity:
                            self._thread_activity[resumed_thread_id] = time.monotonic()
            finally:
                with self._state_lock:
                    self._busy_threads.discard(safe_thread_id)

    def _follow_turn(
        self,
        waiter: _TurnWaiter,
        *,
        turn_started_callback: Callable[[str], None] | None,
        cancellation_requested: Callable[[], bool] | None,
        detach_requested: Callable[[], bool] | None,
        turn_control_provider: Callable[[], dict[str, Any] | None] | None,
        turn_control_result_callback: Callable[[dict[str, Any], bool, str], None] | None,
        deadline: float | None = None,
    ) -> CodexTurnResult:
        """Observe an accepted turn, including native successor turns."""
        thread_id = waiter.thread_id
        missing_since: float | None = None
        while True:
            if waiter.error:
                raise waiter.error
            self._detach_if_requested(thread_id, waiter.turn_id, detach_requested)
            if cancellation_requested and cancellation_requested():
                try:
                    self.interrupt_turn(thread_id, waiter.turn_id)
                except Exception:
                    pass
                raise InterruptedError(f"Codex turn cancelled for {thread_id}")
            if turn_control_provider is not None and waiter.turn_id:
                self._deliver_turn_control(
                    thread_id=thread_id, waiter=waiter,
                    turn_control_provider=turn_control_provider,
                    turn_control_result_callback=turn_control_result_callback,
                    cancellation_requested=cancellation_requested,
                )
            if self.user_inputs.blocking(thread_id):
                started = time.monotonic()
                self._wait_for_turn_signal(waiter, turn_control_provider is not None)
                if deadline is not None:
                    deadline += time.monotonic() - started
                continue
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"Codex turn reconciliation timed out: {waiter.turn_id}")
            terminal = (waiter.completed or {}).get("turn")
            if not isinstance(terminal, dict):
                terminal = None
            if terminal is None and self._turn_reconcile_due(waiter):
                try:
                    terminal = self._read_terminal_turn(waiter)
                    if terminal is None:
                        if waiter.terminal_goal is None:
                            waiter.terminal_goal = self._read_task_complete_goal(thread_id)
                        if waiter.terminal_goal is not None:
                            terminal = self._read_terminal_goal_successor(waiter)
                    if terminal is None:
                        active = self._read_active_turn(thread_id)
                        if active is not None and str(active.get("id") or "") == waiter.turn_id:
                            missing_since = None
                        else:
                            self._raise_thread_system_error(
                                thread_id, waiter.turn_id, self._read_thread_status(thread_id),
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
            if waiter.error:
                raise waiter.error
            if terminal is None:
                self._wait_for_turn_signal(waiter, turn_control_provider is not None)
                continue
            if str(terminal.get("status") or "") != "completed" or terminal.get("error"):
                raise _terminal_turn_error(thread_id, terminal)
            response = codex_final_agent_response(terminal, waiter.agent_messages)
            if not response and waiter.completed:
                # Completion notifications may precede the final item snapshot.
                snapshot = self._read_terminal_turn(waiter)
                if snapshot is not None:
                    if str(snapshot.get("id") or "") != waiter.turn_id:
                        raise CodexAppServerError("Codex terminal snapshot does not match the requested turn")
                    terminal = snapshot
                    if str(terminal.get("status") or "") != "completed" or terminal.get("error"):
                        raise _terminal_turn_error(thread_id, terminal)
                    response = codex_final_agent_response(terminal, waiter.agent_messages)
            if waiter.error:
                raise waiter.error
            self._detach_if_requested(thread_id, waiter.turn_id, detach_requested)
            if cancellation_requested and cancellation_requested():
                raise InterruptedError(f"Codex turn cancelled for {thread_id}")
            if not waiter.completed:
                waiter.completed = {"threadId": thread_id, "turn": terminal}
                if waiter.event_callback:
                    try:
                        waiter.event_callback({"method": "turn/completed", "params": waiter.completed})
                    except Exception:
                        logging.getLogger(__name__).exception("Codex completed turn observation failed")
            if response:
                return CodexTurnResult(
                    thread_id=thread_id, turn_id=str(terminal.get("id") or waiter.turn_id),
                    response=response, turn=terminal, token_usage=waiter.token_usage,
                )
            terminal_id = str(terminal.get("id") or waiter.turn_id)
            successor_deadline = time.monotonic() + TURN_SUCCESSOR_RECONCILE_GRACE_SECONDS
            successor = None
            while time.monotonic() < successor_deadline:
                self._detach_if_requested(thread_id, waiter.turn_id, detach_requested)
                if cancellation_requested and cancellation_requested():
                    raise InterruptedError(f"Codex turn cancelled for {thread_id}")
                candidate = self._read_active_turn(thread_id)
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
            missing_since = None
            if turn_started_callback:
                turn_started_callback(waiter.turn_id)

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
        dynamic_tool_handler: (
            Callable[[dict[str, Any]], dict[str, Any]] | None
        ) = None,
        turn_control_provider: (
            Callable[[], dict[str, Any] | None] | None
        ) = None,
        turn_control_result_callback: (
            Callable[[dict[str, Any], bool, str], None] | None
        ) = None,
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
        with self._locked_thread(
            safe_thread_id,
            cancellation_requested=cancellation_requested,
        ):
            with self._state_lock:
                self._busy_threads.add(safe_thread_id)
            waiter = _TurnWaiter(
                thread_id=safe_thread_id,
                turn_id=safe_turn_id,
                event_callback=event_callback,
                turn_snapshot_transform=turn_snapshot_transform,
            )
            try:
                with self._state_lock:
                    self._thread_activity[safe_thread_id] = time.monotonic()
                    self._turn_waiters[safe_thread_id] = waiter
                    if dynamic_tool_handler is not None:
                        self._dynamic_tool_handlers[safe_thread_id] = (
                            dynamic_tool_handler
                        )
                resume_params: dict[str, Any] = {
                    "threadId": safe_thread_id,
                    "cwd": cwd,
                    "excludeTurns": True,
                }
                if config is not None:
                    resume_params["config"] = dict(config)
                self.request(
                    "thread/resume",
                    resume_params,
                    timeout=self.lifecycle_request_timeout_seconds,
                    cancellation_requested=cancellation_requested,
                )
                active_turn = self._read_active_turn(safe_thread_id)
                if (active_turn is not None
                        and str(active_turn.get("id") or "") != waiter.turn_id
                        and self.read_turn(safe_thread_id, waiter.turn_id) is None):
                    # Only an absent checkpoint may realign to another turn.
                    # A known parent remains authoritative during native review.
                    waiter.turn_id = str(
                        active_turn.get("id") or waiter.turn_id
                    ).strip()
                if waiter.turn_id and turn_started_callback:
                    turn_started_callback(waiter.turn_id)
                return self._follow_turn(
                    waiter, turn_started_callback=turn_started_callback,
                    cancellation_requested=cancellation_requested,
                    detach_requested=detach_requested,
                    turn_control_provider=turn_control_provider,
                    turn_control_result_callback=turn_control_result_callback,
                    deadline=deadline,
                )
            finally:
                self.user_inputs.expire(safe_thread_id)
                with self._state_lock:
                    self._detaching_threads.discard(safe_thread_id)
                    self._turn_waiters.pop(safe_thread_id, None)
                    if dynamic_tool_handler is not None:
                        self._dynamic_tool_handlers.pop(safe_thread_id, None)
                    if safe_thread_id in self._thread_activity:
                        self._thread_activity[safe_thread_id] = time.monotonic()
                    self._busy_threads.discard(safe_thread_id)

    def _maintenance_loop(self) -> None:
        while not self._closing:
            time.sleep(60)
            try:
                self._unsubscribe_idle_threads()
            except Exception:
                continue

    def _unsubscribe_idle_threads(self) -> None:
        now = time.monotonic()
        with self._state_lock:
            # A zero-turn thread only exists inside app-server memory.  It
            # cannot be resumed until its first turn creates a rollout, so
            # idle/LRU maintenance must treat it like an active turn.
            active = (
                set(self._turn_waiters)
                | set(self._goal_waiters)
                | set(self._compact_waiters)
                | set(self._busy_threads)
                | set(self._unmaterialized_threads)
            )
            activity = dict(self._thread_activity)
        idle = sorted(
            ((last_active, thread_id) for thread_id, last_active in activity.items() if thread_id not in active),
            key=lambda item: item[0],
        )
        over_limit = max(0, len(activity) - self.max_loaded_threads)
        expired = {thread_id for last_active, thread_id in idle if now - last_active >= self.idle_unsubscribe_seconds}
        targets = set(thread_id for _last_active, thread_id in idle[:over_limit]) | expired
        for thread_id in targets:
            lock = self._thread_lock(thread_id)
            if not lock.acquire(blocking=False):
                continue
            try:
                with self._state_lock:
                    if (
                        thread_id in self._busy_threads
                        or thread_id in self._turn_waiters
                        or thread_id in self._goal_waiters
                        or thread_id in self._compact_waiters
                        or thread_id in self._unmaterialized_threads
                        or self._thread_activity.get(thread_id) != activity[thread_id]
                    ):
                        continue
                self.request("thread/unsubscribe", {"threadId": thread_id}, timeout=45.0)
                self._forget_loaded_thread(thread_id)
            except Exception:
                continue
            finally:
                lock.release()

    def close(self) -> None:
        self._closing = True
        self._disconnect(CodexAppServerUnavailable("client closed"))


def _sandbox_policy(sandbox: str) -> dict[str, Any]:
    value = str(sandbox or "").strip().lower()
    if value == "danger-full-access":
        return {"type": "dangerFullAccess"}
    if value == "workspace-write":
        return {"type": "workspaceWrite"}
    return {"type": "readOnly"}


def thread_sandbox_from_policy(
    sandbox_policy: dict[str, Any] | None,
) -> str:
    policy_type = str(
        (sandbox_policy or {}).get("type") or ""
    ).strip()
    return {
        "readOnly": "read-only",
        "workspaceWrite": "workspace-write",
        "dangerFullAccess": "danger-full-access",
    }.get(policy_type, "")


def _config_fingerprint(config: object) -> str:
    payload = config if isinstance(config, dict) else {}
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def codex_final_agent_response(
    turn: dict[str, Any],
    streamed_messages: list[str],
) -> str:
    final_messages: list[str] = []
    other_messages: list[str] = []
    for item in turn.get("items") or []:
        if not isinstance(item, dict) or item.get("type") != "agentMessage":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if str(item.get("phase") or "") == "final_answer":
            final_messages.append(text)
        else:
            other_messages.append(text)
    if final_messages:
        # Runtime controls can race with the provider's terminal event and
        # elicit a second acknowledgement after the actual answer.  The first
        # final answer is the canonical response for the user's turn.
        return final_messages[0]
    if other_messages:
        return other_messages[-1]
    return streamed_messages[-1] if streamed_messages else ""





_CLIENTS: dict[str, CodexAppServerClient] = {}
_CLIENTS_LOCK = threading.Lock()


def client_for_socket(
    socket_path: Path | str,
) -> CodexAppServerClient:
    clean_path = str(Path(socket_path))
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(clean_path)
        if client is None:
            client = CodexAppServerClient(
                clean_path,
                idle_unsubscribe_seconds=int(
                    os.getenv("PORTAL_CODEX_APP_SERVER_IDLE_UNSUBSCRIBE_SECONDS", str(DEFAULT_IDLE_UNSUBSCRIBE_SECONDS))
                ),
                max_loaded_threads=int(
                    os.getenv("PORTAL_CODEX_APP_SERVER_MAX_LOADED_THREADS", str(DEFAULT_MAX_LOADED_THREADS))
                ),
            )
            _CLIENTS[clean_path] = client
        return client
