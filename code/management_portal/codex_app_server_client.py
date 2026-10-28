"""The Codex app-server client Portal's Codex backend runs turns through.

One client per app-server socket.  It composes the JSON-RPC connection, the
bookkeeping of loaded threads, the routing of the server's messages to the
work that waits for them, and the runners of turns, goals and compactions.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from management_common.portal_paths import path as portal_path
from management_common.provider_turn_artifacts import TurnArtifacts

from .codex_app_server_compaction import CodexCompactionRunner
from .codex_app_server_events import CodexActiveWork, CodexEventRouter
from .codex_app_server_goals import CodexGoalRunner
from .codex_app_server_protocol import (
    CodexAppServerUnavailable,
    CodexGoalResult,
    CodexRequestTimeouts,
    CodexTurnConnectionLost,
    CodexTurnResult,
    consumed_token_usage_part,
    fail_waiter,
    token_usage_event_fingerprint,
)
from .codex_app_server_rpc import USE_DEFAULT_REQUEST_TIMEOUT, CodexRpcConnection
from .codex_app_server_subagents import CodexSubagentRouter
from .codex_app_server_threads import (
    DEFAULT_IDLE_UNSUBSCRIBE_SECONDS,
    DEFAULT_MAX_LOADED_THREADS,
    CodexLoadedThreads,
    CodexThreadReads,
)
from .codex_app_server_turn_controls import CodexTurnControls
from .codex_app_server_turn_follow import CodexTurnFollower
from .codex_app_server_turns import CodexTurnRunner
from .codex_user_input import CodexUserInputRequests


DEFAULT_SOCKET_PATH = (portal_path("staging_root") / "portal_locks" / "codex-app-server.sock")
DEFAULT_REQUEST_TIMEOUT_SECONDS = 90.0
DEFAULT_LIFECYCLE_REQUEST_TIMEOUT_SECONDS = 180.0
DEFAULT_TURN_START_REQUEST_TIMEOUT_SECONDS = 360.0


def codex_token_usage_delta(value: object) -> tuple[str, dict[str, Any]]:
    """Return the durable identity and incremental part of one Codex update."""

    payload = value if isinstance(value, dict) else {}
    last = consumed_token_usage_part(payload.get("last"))
    if not last:
        return "", {}
    fingerprint = token_usage_event_fingerprint(payload)
    encoded = json.dumps(
        fingerprint,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), {"last": last}


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
        self.timeouts = CodexRequestTimeouts(
            request=max(1.0, float(request_timeout_seconds)),
            lifecycle=max(0.01, float(lifecycle_request_timeout_seconds)),
            turn_start=max(0.01, float(turn_start_request_timeout_seconds)),
        )
        # One lock guards the state every component keeps of this connection.
        self.state_lock = threading.RLock()
        self.work = CodexActiveWork(self.state_lock)
        self.rpc = CodexRpcConnection(
            self.socket_path,
            state_lock=self.state_lock,
            timeouts=self.timeouts,
            on_server_request=lambda request_id, method, params: self.events.answer_server_request(
                request_id, method, params,
            ),
            on_notification=lambda method, params: self.events.dispatch_notification(method, params),
            on_connected=lambda: self.threads.ensure_maintenance(),
            on_connection_lost=self.disconnect,
            on_thread_missing=lambda thread_id: self.threads.forget(thread_id),
        )
        self.threads = CodexLoadedThreads(
            self.rpc, self.work,
            state_lock=self.state_lock,
            timeouts=self.timeouts,
            idle_unsubscribe_seconds=idle_unsubscribe_seconds,
            max_loaded_threads=max_loaded_threads,
        )
        self.reads = CodexThreadReads(self.rpc, self.timeouts)
        self.subagents = CodexSubagentRouter(self.rpc, self.work, self.timeouts)
        self.user_inputs = CodexUserInputRequests(
            self.rpc.send, lambda method, params: self.events.dispatch_notification(method, params),
        )
        self.turn_artifacts = TurnArtifacts()
        self.events = CodexEventRouter(
            self.rpc, self.work, self.threads, self.subagents, self.user_inputs, self.turn_artifacts,
        )
        self.controls = CodexTurnControls(
            self.rpc, self.work, self.reads, self.subagents, self.user_inputs, self.timeouts,
        )
        self.follower = CodexTurnFollower(
            self.work, self.reads, self.controls, self.subagents, self.user_inputs,
        )
        self.turns = CodexTurnRunner(
            self.rpc, self.work, self.threads, self.reads, self.events, self.controls,
            self.follower, self.user_inputs, self.timeouts,
        )
        self.goals = CodexGoalRunner(
            self.rpc, self.work, self.threads, self.controls, self.timeouts,
        )
        self.compactions = CodexCompactionRunner(
            self.rpc, self.work, self.threads, self.reads, self.timeouts,
        )

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None | object = USE_DEFAULT_REQUEST_TIMEOUT,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self.rpc.request(
            method, params, timeout=timeout, cancellation_requested=cancellation_requested,
        )

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.rpc.notify(method, params)

    def start_thread(
        self,
        params: dict[str, Any],
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self.threads.start(params, cancellation_requested=cancellation_requested)

    def fork_thread(
        self,
        params: dict[str, Any],
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self.threads.fork(params, cancellation_requested=cancellation_requested)

    def unsubscribe_thread(self, thread_id: str) -> None:
        self.threads.unsubscribe(thread_id)

    def execute_command(
        self,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.rpc.request(
            "command/exec",
            params,
            timeout=timeout,
        )

    def compact_thread(self, thread_id: str, **options: Any) -> dict[str, Any]:
        return self.compactions.compact_thread(thread_id, **options)

    def run_goal(self, thread_id: str, **options: Any) -> CodexGoalResult:
        return self.goals.run_goal(thread_id, **options)

    def run_turn(self, thread_id: str, prompt: str, **options: Any) -> CodexTurnResult:
        return self.turns.run_turn(thread_id, prompt, **options)

    def resume_turn(self, thread_id: str, turn_id: str, **options: Any) -> CodexTurnResult:
        return self.turns.resume_turn(thread_id, turn_id, **options)

    def interrupt_turn(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        return self.controls.interrupt_turn(thread_id, turn_id)

    def steer_turn(self, thread_id: str, turn_id: str, turn_input: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        return self.controls.steer_turn(thread_id, turn_id, turn_input, **options)

    def update_turn_settings(self, thread_id: str, turn_id: str, **options: Any) -> dict[str, Any]:
        return self.controls.update_turn_settings(thread_id, turn_id, **options)

    def read_turn(self, thread_id: str, turn_id: str) -> dict[str, Any] | None:
        return self.reads.read_turn(thread_id, turn_id)

    def active_turn_id(self, thread_id: str) -> str:
        return self.reads.active_turn_id(thread_id)

    def snapshot(self) -> dict[str, Any]:
        with self.state_lock:
            loaded_threads, busy_threads = self.threads.counts()
            return {
                "connected": self.rpc.connected,
                "loaded_threads": loaded_threads,
                "busy_threads": busy_threads,
                "active_turns": len(self.work.turns),
                "compacting_threads": len(self.work.compactions),
            }

    def disconnect(self, error: BaseException) -> None:
        """End the connection: every request and every followed work fails."""

        self.turn_artifacts.clear()
        self.user_inputs.expire()
        with self.state_lock:
            released = self.rpc.release()
            turns, goals, compactions = self.work.waiters()
            self.threads.forget_all()
            self.subagents.forget_all()
        released.close(error)
        for waiter in turns:
            fail_waiter(waiter, CodexTurnConnectionLost(waiter.thread_id, waiter.turn_id))
        for waiter in goals:
            fail_waiter(waiter, CodexTurnConnectionLost(waiter.thread_id, waiter.turn_id))
        for waiter in compactions:
            fail_waiter(waiter, CodexAppServerUnavailable(str(error)))

    def close(self) -> None:
        self.rpc.closing = True
        self.disconnect(CodexAppServerUnavailable("client closed"))


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
