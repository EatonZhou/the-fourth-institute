"""Portal Web's ``instruction.execute`` socket (decision_96a887d83e8a).

Other services send the instructions Portal Web owns (users, discussion,
inbox, Portal services) to this socket.  The Web process also runs its own
kernel in process for calls arriving at ``/api/instructions/execute``.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
from pathlib import Path
from typing import Any


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        from management_portal.hatg.socket_rpc import METHOD, serve

        request_id = ""
        try:
            request = json.loads(self.rfile.readline() or b"{}")
            request_id = str(request.get("id") or "")
            if request.get("method") != METHOD:
                raise ValueError(f"unsupported Portal Web socket method: {request.get('method')}")
            params = request.get("params")
            if not isinstance(params, dict):
                raise ValueError("request params must be an object")
            actor = self.server.rpc_auth.authenticate(METHOD, params, request.get("auth"))
        except Exception as exc:  # noqa: BLE001
            code = "instruction.forbidden" if isinstance(exc, PermissionError) else "instruction.invalid_arguments"
            self._write({"id": request_id, "ok": False, "error": {"code": code, "message": str(exc)}})
            return
        try:
            serve(params, request_id, self._write, owner="web", verified_caller=actor)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _write(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        self.wfile.flush()


from management_common.service_endpoint import LocalServiceSocketMixin


class WebInstructionServer(LocalServiceSocketMixin, socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = socket.SOMAXCONN

    def __init__(self, path: Path) -> None:
        from management_portal.hatg.kernel import local_kernel
        from management_portal.hatg.maintenance import InstructionMaintenance

        self.instruction_maintenance = InstructionMaintenance(local_kernel("web"))
        self.path = path
        from management_common.service_endpoint import service_endpoint
        self.endpoint = service_endpoint(path)
        self.address_family = self.endpoint.family
        from management_common.rpc_auth import RpcAuthenticator
        self.rpc_auth = RpcAuthenticator(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.endpoint.is_unix:
            path.unlink(missing_ok=True)
        super().__init__(self.endpoint.address, _Handler)
        if self.endpoint.is_unix:
            os.chmod(path, 0o660)
        self._thread = threading.Thread(target=self.serve_forever, name="portal-web-instructions", daemon=True)

    def start(self) -> "WebInstructionServer":
        self.instruction_maintenance.start()
        self._thread.start()
        return self

    def stop(self) -> None:
        self.instruction_maintenance.stop()
        self.shutdown()
        self.server_close()
        if self.endpoint.is_unix:
            self.path.unlink(missing_ok=True)


def start_web_instruction_server() -> WebInstructionServer | None:
    """Register the Web kernel and open its socket; tests without an instance skip the socket."""

    from management_portal.hatg.kernel import register_kernel
    from management_portal.hatg.socket_rpc import web_service_socket
    from management_portal.web_isa import build_kernel

    register_kernel(build_kernel())
    if str(os.getenv("PORTAL_ISA_ROUTING") or "local").strip().lower() != "socket":
        return None
    return WebInstructionServer(web_service_socket()).start()
