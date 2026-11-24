"""Deployment-selected egress for server-side HTTPS transfer jobs."""
from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit

from management_common.portal_network import PROXY_KEYS
from management_common.site_config import read_site_config


ROUTES = ("direct", "vps", "inherited")
GOOGLE_DRIVE_HOSTS = ("googleapis.com", "googleusercontent.com", "drive.google.com")


def resolve_route(requested: str | None = None) -> str:
    config = _settings()
    route = requested or config.get("default_route", "inherited")
    if route not in ROUTES:
        raise ValueError("Unknown transfer network route")
    if route == "direct" and not config.get("direct_interface"):
        raise ValueError("Direct transfer interface is not configured")
    if route == "vps" and not config.get("vps_proxy"):
        raise ValueError("VPS transfer proxy is not configured")
    return route


def _settings() -> dict:
    return read_site_config().get("deployment", {}).get("transfer_network", {})


def _proxy_environment(env: dict, proxy: str) -> dict:
    clean = {key: value for key, value in env.items() if key not in PROXY_KEYS}
    clean.update({"HTTPS_PROXY": proxy, "https_proxy": proxy})
    return clean


def _connect(host: str, interface: str) -> socket.socket:
    for family, kind, protocol, _, address in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM):
        if not ipaddress.ip_address(address[0]).is_global:
            continue
        connection = socket.socket(family, kind, protocol)
        try:
            connection.settimeout(8)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b"\0")
            connection.connect(address)
            connection.settimeout(60)
            return connection
        except OSError:
            connection.close()
    raise OSError("Direct connection failed")


def _bridge(client: socket.socket, upstream: socket.socket) -> None:
    peers = {client: upstream, upstream: client}
    while peers:
        ready, _, _ = select.select(list(peers), [], [], 120)
        if not ready:
            return
        for source in ready:
            data = source.recv(64 * 1024)
            target = peers[source]
            if data:
                target.sendall(data)
            else:
                target.shutdown(socket.SHUT_WR)
                del peers[source]


@contextmanager
def transfer_environment(env: dict, route: str, allowed_hosts: tuple[str, ...]):
    """Scope egress to this child environment. Direct sockets bind to the interface."""
    config = _settings()
    if route == "inherited":
        yield env
        return
    if route == "vps":
        proxy = config.get("vps_proxy", "")
        parsed = urlsplit(proxy)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid deployment VPS transfer proxy")
        yield _proxy_environment(env, proxy)
        return
    if route != "direct":
        raise ValueError("Unknown transfer network route")
    interface = config.get("direct_interface", "")
    if not interface or not socket.if_nametoindex(interface):
        raise ValueError("Direct transfer interface is not available")

    class ConnectHandler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(10)
            try:
                method, authority, version = self.rfile.readline(4096).decode("ascii").split()
                host, _, port = authority.lower().rpartition(":")
                if method != "CONNECT" or not version.startswith("HTTP/") or port != "443" or not any(
                    host == suffix or host.endswith("." + suffix) for suffix in allowed_hosts
                ):
                    self.wfile.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                    return
                while True:
                    header = self.rfile.readline(4096)
                    if header == b"\r\n":
                        break
                    if not header or not header.endswith(b"\r\n"):
                        return
                try:
                    upstream = _connect(host, interface)
                except OSError:
                    self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                with upstream:
                    self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    self.connection.settimeout(60)
                    _bridge(self.connection, upstream)
            except (OSError, ValueError):
                return

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), ConnectHandler) as proxy:
        proxy.daemon_threads = True
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            yield _proxy_environment(env, f"http://127.0.0.1:{proxy.server_address[1]}")
        finally:
            proxy.shutdown()
            thread.join()
