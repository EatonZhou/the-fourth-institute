"""Transport address for one Portal RPC owner, separate from its key path."""
from __future__ import annotations

import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from management_common.local_ipc import local_transport


_SERVICE_NAMES = {f"{owner}-service.sock" for owner in ("resource", "execution", "society", "web")}


@dataclass(frozen=True)
class ServiceEndpoint:
    logical_path: Path
    family: int
    address: str | tuple[str, int]
    private_local: bool = False

    @property
    def is_unix(self) -> bool:
        return self.family == getattr(socket, "AF_UNIX", None)


def service_endpoint(path: Path | str, *, platform: str | None = None) -> ServiceEndpoint:
    """Resolve an instance's RPC address without changing its signing-key path.

    Windows and long POSIX paths discover an ephemeral loopback port through
    the authenticated local transport. Identity is the entire installation path.
    """
    logical = Path(path)
    selected = platform or sys.platform
    if selected in {"linux", "darwin"} and local_transport(logical, platform=selected) == "unix":
        return ServiceEndpoint(logical, socket.AF_UNIX, str(logical))
    if selected not in {"linux", "darwin", "win32"}:
        raise NotImplementedError(f"Unsupported RPC platform: {selected}")
    if selected == "win32" and logical.name not in _SERVICE_NAMES:
        raise ValueError(f"No Windows RPC endpoint for {logical}")
    return ServiceEndpoint(logical, socket.AF_INET, ("127.0.0.1", 0), private_local=True)


class LocalServiceSocketMixin:
    """socketserver integration; Unix generation handover stays with its owner."""

    def server_bind(self):
        if not self.endpoint.private_local:
            return super().server_bind()
        from management_common.local_ipc import LocalListener
        listener = LocalListener(self.endpoint.logical_path, transport="tcp",
                                 backlog=self.request_queue_size, replace=True)
        self.socket.close()
        self._local_listener = listener
        self.socket = listener.socket
        self.server_address = self.socket.getsockname()

    def server_activate(self):
        if not self.endpoint.private_local:
            return super().server_activate()

    def get_request(self):
        listener = getattr(self, "_local_listener", None)
        connection, address = listener.accept() if listener else super().get_request()
        # Darwin can inherit O_NONBLOCK from the listening socket. Handlers
        # read complete request lines; an empty buffer must not become EOF.
        # Keep the listener nonblocking for generation handover and shutdown.
        connection.setblocking(True)
        return connection, address

    def server_close(self):
        try:
            super().server_close()
        finally:
            listener = getattr(self, "_local_listener", None)
            if listener:
                listener.close()

