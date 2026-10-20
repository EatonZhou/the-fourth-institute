"""The worker side of Portal tools for Kimi Code.

Kimi Code reaches Portal tools through ``management_portal.kimi_portal_tools``:
an MCP server shared by every session of a workspace, and a hook that tells
that server which session makes each call.

A worker adds the tools of its turn to the catalog the server lists, and
while the turn runs it listens on a socket named after its Kimi session.
The server passes it the calls of that session only, so the handlers, the
caller's identity and the Harness tool-batch hook stay with the session
that made the call.
"""
from __future__ import annotations

from management_common.file_lock import lock_stream
from management_common.local_ipc import LocalListener
import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from management_portal import kimi_portal_tools
from management_portal.native_agent_transport import ToolCallHandedOver

LOGGER = logging.getLogger(__name__)
ToolDispatch = Callable[[dict[str, Any]], dict[str, Any]]


def publish_catalog(catalog: list[dict[str, Any]], kimi_home: str | os.PathLike[str]) -> bool:
    """Add a turn's tools to the catalog Kimi Code lists; whether it changed.

    The catalog only grows: a tool another turn offered stays listed, and
    each turn switches off what it does not have.  A definition that
    differs from the listed one replaces it.  The catalog is then named in
    Kimi Code's ``mcp.json``, which makes Kimi Code connect again, and list
    the tools again, when the catalog changed.
    """

    path = kimi_portal_tools.catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (path.parent / "catalog.lock").open("a+") as lock, lock_stream(lock):
        # One worker at a time, so mcp.json always names the catalog on disk.
        listed = {str(tool["name"]): tool for tool in kimi_portal_tools.read_catalog()}
        merged = {**listed, **{str(tool["name"]): tool for tool in catalog}}
        changed = merged != listed
        if changed:
            temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps({"tools": [merged[name] for name in sorted(merged)]}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        kimi_portal_tools.name_catalog(kimi_home)
        return changed


class KimiToolGateway:
    """Serve one turn's Portal tools to the calls of its Kimi session."""

    # A retiring Worker answers the calls it has taken before it leaves.
    DRAIN_SECONDS = 990.0

    def __init__(self, session_id: str, dispatch: ToolDispatch | None) -> None:
        self.dispatch = dispatch
        self.path: Path = kimi_portal_tools.gateway_socket_path(session_id)
        self._server: LocalListener | None = None
        self._calls: set[threading.Thread] = set()
        self._calls_lock = threading.Lock()

    def __enter__(self) -> KimiToolGateway:
        if self.dispatch is None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        server = LocalListener(self.path, backlog=32, replace=True)
        self._server = server
        threading.Thread(target=self._accept, args=(server,), name="kimi-portal-tools", daemon=True).start()
        return self

    def __exit__(self, *_exc: object) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        # Calls taken before the gateway closed still get their answers.
        with self._calls_lock:
            calls = list(self._calls)
        deadline = time.monotonic() + self.DRAIN_SECONDS
        for call in calls:
            call.join(max(0.0, deadline - time.monotonic()))

    def _accept(self, server: LocalListener) -> None:
        while True:
            try:
                connection, _address = server.accept()
            except OSError:
                return  # the turn ended
            call = threading.Thread(target=self._serve, args=(connection,), daemon=True)
            with self._calls_lock:
                self._calls.add(call)
            call.start()

    def _serve(self, connection: socket.socket) -> None:
        try:
            self._answer_connection(connection)
        finally:
            with self._calls_lock:
                self._calls.discard(threading.current_thread())

    def _answer_connection(self, connection: socket.socket) -> None:
        with connection:
            try:
                with connection.makefile("rb") as stream:
                    request = json.loads(stream.readline() or b"{}")
                answer = self.answer(request if isinstance(request, dict) else {})
                connection.sendall(json.dumps(answer, ensure_ascii=False).encode("utf-8") + b"\n")
            except (OSError, ValueError):
                LOGGER.debug("Kimi Portal tool request failed", exc_info=True)

    def answer(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run one call of this turn's session."""

        arguments = request.get("arguments")
        if request.get("op") != "call" or self.dispatch is None:
            return {"error": "Unsupported Portal tool request"}
        try:
            return {"result": self.dispatch({
                "tool": str(request.get("tool") or ""),
                "arguments": arguments if isinstance(arguments, dict) else {},
                "call_id": str(request.get("call_id") or ""),
            })}
        except ToolCallHandedOver:
            # A Worker handover: the caller asks the next Worker's gateway.
            return {"retry": True}
        except InterruptedError:
            return {"error": "The Portal turn was cancelled"}
        except Exception as exc:  # noqa: BLE001 - the model gets the reason, the turn goes on
            return {"error": f"Portal tool rejected: {type(exc).__name__}: {str(exc)[:1000]}"}
