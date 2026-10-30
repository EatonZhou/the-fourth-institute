"""The JSON-RPC connection to one Codex app-server.

The connection speaks WebSocket over the app-server's local socket: it sends
requests and notifications, matches responses to their requests on a reader
thread and hands the server's own requests and notifications to the client.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .codex_app_server_host import connect_transport
from .codex_app_server_protocol import (
    CodexAppServerError,
    CodexAppServerUnavailable,
    CodexRequestTimeouts,
    CodexThreadNotFound,
    CodexThreadWriterBusy,
    PendingRequest,
    fail_waiter,
    is_loaded_thread_writer_conflict,
    terminal_turn_error,
)
from .provider_websocket import WebSocketTransport


USE_DEFAULT_REQUEST_TIMEOUT = object()


@dataclass
class ReleasedTransport:
    """A transport the connection gave up, with the requests still waiting on it."""

    socket: socket.socket | None
    transport: WebSocketTransport | None
    pending: list[PendingRequest]

    def close(self, error: BaseException) -> None:
        try:
            if self.socket is not None:
                self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            if self.transport is not None:
                self.transport.close()
        except OSError:  # WebSocketTransport.close lets only its socket's close fail
            pass
        try:
            if self.socket is not None:
                self.socket.close()
        except OSError:
            pass
        for item in self.pending:
            fail_waiter(item, CodexAppServerUnavailable(str(error)))


class CodexRpcConnection:
    def __init__(
        self,
        socket_path: Path,
        *,
        state_lock: threading.RLock,
        timeouts: CodexRequestTimeouts,
        on_server_request: Callable[[object, str, object], None],
        on_notification: Callable[[str, object], None],
        on_connected: Callable[[], None],
        on_connection_lost: Callable[[BaseException], None],
        on_thread_missing: Callable[[str], None],
    ) -> None:
        self.socket_path = socket_path
        self.timeouts = timeouts
        self.closing = False
        self._state_lock = state_lock
        self._on_server_request = on_server_request
        self._on_notification = on_notification
        self._on_connected = on_connected
        self._on_connection_lost = on_connection_lost
        self._on_thread_missing = on_thread_missing
        self._write_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._transport: WebSocketTransport | None = None
        self._reader_thread: threading.Thread | None = None
        self._request_id = 0
        self._pending: dict[int, PendingRequest] = {}

    @property
    def connected(self) -> bool:
        return self._transport is not None

    def _next_request_id(self) -> int:
        with self._state_lock:
            self._request_id += 1
            return self._request_id

    def send(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            transport = self._transport
            if transport is None:
                raise CodexAppServerUnavailable("Codex app-server is not connected")
            try:
                transport.send_text(encoded)
            except (BrokenPipeError, OSError) as exc:
                self._on_connection_lost(exc)
                raise CodexAppServerUnavailable("Codex app-server connection failed") from exc

    def connect(
        self,
        *,
        timeout: float | None | object = USE_DEFAULT_REQUEST_TIMEOUT,
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
                self.closing = False
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
                self._on_connection_lost(CodexAppServerUnavailable("Codex app-server initialization failed"))
                raise
            self._on_connected()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None | object = USE_DEFAULT_REQUEST_TIMEOUT,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if self._transport is None:
            self.connect(
                timeout=timeout,
                cancellation_requested=cancellation_requested,
            )
        request_id = self._next_request_id()
        pending = PendingRequest()
        with self._state_lock:
            self._pending[request_id] = pending
        try:
            self.send({"method": method, "id": request_id, "params": params or {}})
            deadline = (
                time.monotonic() + self.timeouts.request
                if timeout is USE_DEFAULT_REQUEST_TIMEOUT
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
                    f"Codex app-server request failed: invalid paginated history lineage for {thread_id}: missing source rollout",
                }:
                    self._on_thread_missing(thread_id)
                    raise CodexThreadNotFound(str(pending.error)) from pending.error
                raise pending.error
            return pending.result or {}
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._transport is None:
            self.connect()
        self.send({"method": method, "params": params or {}})

    def release(self) -> ReleasedTransport:
        """Give up the transport; the caller closes it and fails its requests."""

        with self._state_lock:
            released = ReleasedTransport(self._socket, self._transport, list(self._pending.values()))
            self._socket = None
            self._transport = None
        return released

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
                except ValueError:  # recv_text returns text; a frame that is not JSON is skipped
                    logging.getLogger(__name__).warning(
                        "Codex app-server sent a frame that is not JSON (%d characters)", len(raw))
                    continue
                if not isinstance(message, dict):
                    continue
                self.dispatch_message(message)
        except Exception as exc:
            failure = exc
        finally:
            self._on_connection_lost(failure)

    def dispatch_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if request_id is not None and not method:
            self._resolve_response(request_id, message)
            return
        if request_id is not None and method:
            self._on_server_request(request_id, method, message.get("params"))
            return
        if method:
            self._on_notification(method, message.get("params"))

    def _resolve_response(self, request_id: object, message: dict[str, Any]) -> None:
        try:
            numeric_id = int(request_id)
        except (TypeError, ValueError):
            return
        with self._state_lock:
            pending = self._pending.get(numeric_id)
        if not pending:
            return
        if isinstance(message.get("error"), dict):
            pending.error = self._response_error(message["error"])
        else:
            pending.result = message.get("result") if isinstance(message.get("result"), dict) else {}
        pending.event.set()

    @staticmethod
    def _response_error(error: dict[str, Any]) -> BaseException:
        provider_error = terminal_turn_error("", {
            "status": "failed", "error": error,
        })
        error_message = (
            "Codex app-server request failed: "
            f"{error.get('message') or error}"
        )
        return (
            provider_error
            if getattr(provider_error, "error_details", {}).get("retryable") is False
            else CodexThreadWriterBusy(error_message)
            if is_loaded_thread_writer_conflict(error_message)
            else CodexAppServerError(error_message)
        )
