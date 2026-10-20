"""Common local Agent control protocol, including Worker-owned tool callbacks."""
import json
import secrets
import socket
import select
import time
from pathlib import Path
from management_common.local_ipc import connect_local

# How often a Worker that may hand over a running turn checks whether to leave.
DETACH_POLL_SECONDS = 0.25


class NativeAgentError(RuntimeError):
    pass


class NativeAgentUnavailable(NativeAgentError):
    pass


class NativeAgentDetached(NativeAgentError):
    """The Worker left a running turn to the next Worker (a handover)."""


class ToolCallHandedOver(NativeAgentDetached):
    """A retiring Worker leaves a tool call it has not started to the next Worker.

    The client leaves without answering it; the Agent gives the call to the
    Worker that attaches next.
    """


class NativeAgentClient:
    error_type = NativeAgentError
    unavailable_type = NativeAgentUnavailable

    def __init__(self, socket_path):
        self.socket_path = Path(socket_path)

    def _request(self, method, params=None, *, event_callback=None, response_timeout=None, tool_callback=None,
                 detach_requested=None):
        """Send one request and handle its events and tool calls until the result.

        With ``detach_requested`` the client leaves the request when it turns
        true, raising ``NativeAgentDetached``; the Agent keeps running the
        turn for the next Worker.  It leaves only between messages, never
        during a tool call, so every call it took is answered.
        """
        request_id = secrets.token_hex(16)
        try:
            with connect_local(self.socket_path, timeout=15) as sock:
                sock.settimeout(DETACH_POLL_SECONDS if detach_requested else response_timeout)
                def send(message):
                    sock.sendall(json.dumps(message, ensure_ascii=False).encode() + b"\n")
                send({"id": request_id, "method": method, "params": params or {}})
                with sock.makefile("rb") as stream:
                    lines = (
                        _lines_until_detached(sock, detach_requested, response_timeout)
                        if detach_requested else stream
                    )
                    for raw in lines:
                        message = json.loads(raw)
                        if message.get("id") != request_id:
                            continue
                        kind = message.get("type")
                        if kind == "tool_call":
                            try:
                                if tool_callback is None:
                                    raise ValueError("No tool owner for this execution")
                                result = tool_callback(message)
                            except (InterruptedError, NativeAgentDetached):
                                raise
                            except Exception as exc:
                                result = {"isError": True, "content": [{"type": "text", "text": f"Tool rejected: {type(exc).__name__}: {str(exc)[:1000]}"}]}
                            send({"id": request_id, "type": "tool_result", "call_id": message.get("call_id"), "result": result})
                        elif kind == "event" and event_callback:
                            event_callback(message["event"])
                        elif kind == "error":
                            error = message.get("error") or {}
                            raise self.error_type(str(error.get("message") or "Native Agent request failed"))
                        elif kind == "result":
                            return message.get("result") or {}
                raise self.unavailable_type("Native Agent closed before returning a result")
        except InterruptedError:
            raise
        except (OSError, TimeoutError) as exc:
            raise self.unavailable_type(f"Native Agent unavailable: {self.socket_path}: {exc}") from exc


def _lines_until_detached(sock, detach_requested, response_timeout):
    """Read message lines, leaving when ``detach_requested`` turns true."""

    buffer = b""
    last_data = time.monotonic()
    while True:
        try:
            chunk = sock.recv(1 << 16)
        except socket.timeout:
            if detach_requested():
                try:
                    # A message already sent is still taken; leave only when
                    # nothing is waiting.
                    if not select.select([sock], [], [], 0)[0]:
                        raise BlockingIOError()
                    chunk = sock.recv(1 << 16)
                except (BlockingIOError, socket.timeout):
                    raise NativeAgentDetached("Worker left the running turn to the next Worker") from None
            elif response_timeout is not None and time.monotonic() - last_data >= response_timeout:
                raise TimeoutError("Native Agent response timed out")
            else:
                continue
        if not chunk:
            return
        last_data = time.monotonic()
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line.strip():
                yield line
