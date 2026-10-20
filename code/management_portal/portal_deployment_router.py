#!/usr/bin/env python3
from __future__ import annotations
from management_common.site_compatibility import compatibility_value

import argparse
import base64
import hashlib
import http.client
import ipaddress
import json
import os
import select
import socket
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from management_common import portal_network
from typing import Callable
from urllib.parse import urlsplit

from .portal_deployment_registry import (
    DEFAULT_DEPLOYMENT_REGISTRY_PATH,
    PortalDeploymentRegistry,
)
from management_common.instance_paths import instance_root
from management_common.service_socket import retry_delays
from .portal_session_auth import (
    PortalSessionAuthority,
    cookie_value as _cookie_value,
)
from .runtime_status import portal_runtime_status

DEPLOYMENT_PREFERENCE_COOKIE = "db_portal_instance"
DEPLOYMENT_PREFERENCE_VERSION = 1
DEPLOYMENT_PREFERENCE_MAX_AGE = 180 * 24 * 60 * 60
DEPLOYMENT_PREFERENCE_PATH = "/__portal_router/preference"
RUNTIME_STATUS_PATH = "/__portal_router/status"
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "expect",
}
# A backend that refuses the connection is being restarted: systemd replaces
# its Web process in about 3 seconds, a crashed one comes back after
# RestartSec.  The request has not reached it yet, so the router waits for the
# backend this long instead of answering 502 at once; nothing is sent twice.
# It retries with the services' backoff (``retry_delays``).
BACKEND_RESTART_WAIT_SECONDS = 30.0
# A page the browser asked for while its backend stayed away: it tries again
# by itself, so a person does not have to refresh.
BACKEND_UNAVAILABLE_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Portal 暂时无法连接</title>
<style>body{font:15px/1.6 system-ui,sans-serif;margin:18vh auto;max-width:28em;padding:0 1.5em;color:#333}</style>
</head><body><h1>Portal 暂时无法连接</h1><p>服务可能正在重启。页面会在 5 秒后自动重试。</p>
<p lang="en">Portal is unavailable for the moment, probably restarting. This page retries in 5 seconds.</p></body></html>
"""
# The repository source files the running router has loaded, with their
# digests (``{"pid", "sources"}``).  A publication restarts the router only
# when one of them changed: a restart cuts every connection it carries.
SOURCE_MANIFEST_NAME = "user-router-source.json"
REPOSITORY = Path(__file__).resolve().parents[1]
# The unit the router runs under counts as its source too.
UNIT_TEMPLATE = "systemd/portal-user-router.service"
PUBLIC_RUNTIME_STATUS_FIELDS = (
    "schema",
    "ok",
    "ready",
    "state",
    "code",
    "terminal",
    "retryable",
    "title",
    "message",
)


def _b64url_decode(raw: str) -> bytes:
    return base64.urlsafe_b64decode((raw + "=" * (-len(raw) % 4)).encode("ascii"))


def _forwarded_metadata(client_ip: str, headers: object) -> tuple[str, str]:
    peer = ipaddress.ip_address(str(client_ip or ""))
    get_header = getattr(headers, "get", lambda _name, _default="": _default)
    if not peer.is_loopback:
        return str(peer), "http"
    raw_chain = str(get_header("X-Forwarded-For", "") or "").strip()
    try:
        chain = ", ".join(
            str(ipaddress.ip_address(item.strip()))
            for item in raw_chain.split(",")
            if item.strip()
        )
    except ValueError:
        chain = ""
    raw_proto = str(get_header("X-Forwarded-Proto", "") or "").split(",", 1)[0].strip().lower()
    return chain or str(peer), raw_proto if raw_proto in {"http", "https"} else "http"


def _public_health_payload() -> dict:
    return {"ok": True}


def _public_runtime_status(payload: object) -> dict:
    source = payload if isinstance(payload, dict) else {}
    return {field: source[field] for field in PUBLIC_RUNTIME_STATUS_FIELDS if field in source}


def source_manifest_path() -> Path:
    return instance_root() / SOURCE_MANIFEST_NAME


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def loaded_sources() -> dict[str, str]:
    """The repository source files of the modules this process loaded (and its unit), by content digest."""

    found = {UNIT_TEMPLATE: _digest(REPOSITORY / UNIT_TEMPLATE)}
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        source = Path(path).resolve()
        if source.suffix == ".py" and REPOSITORY in source.parents:
            found[str(source.relative_to(REPOSITORY))] = _digest(source)
    return found


def changed_sources(manifest: object, pid: int) -> list[str] | None:
    """The router sources that differ from what the running router loaded.

    ``None`` when the manifest is not the running router's (``pid`` is its
    process): what it loaded is unknown.
    """

    if not isinstance(manifest, dict) or not pid or manifest.get("pid") != pid:
        return None
    sources = manifest.get("sources")
    if not isinstance(sources, dict):
        return None
    return sorted(name for name, digest in sources.items() if _digest(REPOSITORY / name) != digest)


def _public_backend_error() -> dict:
    return {
        "error": "Portal backend unavailable",
        "code": "backend_unavailable",
    }


def _encode_deployment_preference(username: str, backend: str) -> str:
    payload = {
        "v": DEPLOYMENT_PREFERENCE_VERSION,
        "u": str(username or "").strip(),
        "b": str(backend or "").strip().lower(),
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")


def _decode_deployment_preference(raw: str) -> tuple[str, str]:
    try:
        payload = json.loads(_b64url_decode(str(raw or "").strip()).decode("utf-8"))
        username = str(payload.get("u") or "").strip()
        backend = str(payload.get("b") or "").strip().lower()
        if int(payload.get("v") or 0) != DEPLOYMENT_PREFERENCE_VERSION:
            return "", ""
        if not username or not backend:
            return "", ""
        return username, backend
    except Exception:
        return "", ""


@dataclass(frozen=True)
class Backend:
    name: str
    host: str
    port: int

    @classmethod
    def parse(cls, name: str, origin: str) -> "Backend":
        parsed = urlsplit(str(origin or "").strip())
        if parsed.scheme != "http" or not parsed.hostname or parsed.path not in {"", "/"}:
            raise ValueError(f"{name} backend must be an http origin without a path")
        return cls(name=name, host=parsed.hostname, port=int(parsed.port or 80))


class RouterPolicy:
    def __init__(self, verifier: PortalSessionAuthority, dev_users: set[str]):
        self.verifier = verifier
        self.dev_users = {str(item).strip() for item in dev_users if str(item).strip()}

    def default_backend(self, actor: str, valid_backends: set[str] | None = None) -> str:
        desired = "dev" if str(actor or "").strip() in self.dev_users else "release"
        valid = set(valid_backends or {"dev", "release"})
        if desired in valid:
            return desired
        if "release" in valid:
            return "release"
        if "dev" in valid:
            return "dev"
        return sorted(valid)[0] if valid else ""

    def preference(
        self,
        actor: str,
        cookie_header: str,
        valid_backends: set[str] | None = None,
    ) -> str:
        cookie_actor, backend = _decode_deployment_preference(
            _cookie_value(cookie_header, DEPLOYMENT_PREFERENCE_COOKIE)
        )
        valid = set(valid_backends or {"dev", "release"})
        return backend if cookie_actor == str(actor or "").strip() and backend in valid else ""

    def select(
        self,
        path: str,
        cookie_header: str,
        valid_backends: set[str] | None = None,
    ) -> str:
        valid = set(valid_backends or {"dev", "release"})
        clean_path = urlsplit(str(path or "/")).path
        if (
            clean_path.startswith("/api/auth/passkeys/")
            or clean_path.startswith("/api/auth/password-reset/")
            or clean_path.startswith("/api/account/")
            or clean_path in {
                "/forgot-password",
                "/reset-password",
                "/settings.html",
                "/static/portal_passkeys.js",
                "/static/portal_ui_primitives.js",
                "/static/settings.css",
                "/static/settings.js",
            }
        ) and "dev" in valid:
            return "dev"
        actor = self.verifier.verify_cookie(cookie_header)
        if actor:
            return self.preference(actor, cookie_header, valid) or self.default_backend(actor, valid)
        if clean_path in {"/login", "/login/verify"} and "dev" in valid:
            return "dev"
        return self.default_backend("", valid)


class PortalRouterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(
        self,
        address: tuple[str, int],
        policy: RouterPolicy,
        backends: dict[str, Backend],
        allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
        timeout: float,
        deployment_registry: PortalDeploymentRegistry | None = None,
        runtime_status_reader: Callable[[], dict] = portal_runtime_status,
        restart_wait: float = BACKEND_RESTART_WAIT_SECONDS,
        source_manifest: Path | None = None,
        network_settings_path: Path | None = None,
    ):
        self.policy = policy
        self.restart_wait = restart_wait
        self.source_manifest = source_manifest
        self._recorded_modules = -1
        self._record_lock = threading.Lock()
        self.backends = backends
        self.deployment_registry = deployment_registry
        self.runtime_status_reader = runtime_status_reader
        self.allowed_networks = allowed_networks
        self.network_settings_path = network_settings_path
        self.backend_timeout = timeout
        super().__init__(address, PortalRouterHandler)

    def current_backends(self) -> dict[str, Backend]:
        if self.deployment_registry is None:
            return dict(self.backends)
        return {
            item.instance_id: Backend.parse(item.instance_id, item.origin)
            for item in self.deployment_registry.deployments()
        }

    def public_instances(self) -> list[dict]:
        if self.deployment_registry is not None:
            return self.deployment_registry.public_instances()
        return [
            {
                "id": name,
                "label": "Dev" if name == "dev" else "Release" if name == "release" else name,
                "kind": name if name in {"dev", "release"} else "exploration",
                "build_id": "",
            }
            for name in self.backends
        ]

    def record_sources(self) -> None:
        """Write the source manifest again when this process loaded more modules."""

        if self.source_manifest is None or len(sys.modules) == self._recorded_modules:
            return
        with self._record_lock:
            count = len(sys.modules)
            if count == self._recorded_modules:
                return
            payload = {"pid": os.getpid(), "sources": loaded_sources()}
            temporary = self.source_manifest.with_name(f".{self.source_manifest.name}.{os.getpid()}")
            try:
                temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
                os.replace(temporary, self.source_manifest)
                self._recorded_modules = count
            except OSError as exc:
                print(f"Portal router source manifest not written: {exc}", file=sys.stderr, flush=True)

    def runtime_status(self) -> dict:
        payload = self.runtime_status_reader()
        if not isinstance(payload, dict):
            raise TypeError("Portal runtime status reader must return an object")
        return dict(payload)

    def verify_request(self, request, client_address) -> bool:
        try:
            client_ip = ipaddress.ip_address(client_address[0])
        except ValueError:
            return False
        networks = self.allowed_networks
        if self.network_settings_path is not None:
            try:
                networks = _parse_networks(portal_network.allowed_networks(self.network_settings_path))
            except (OSError, ValueError):
                # Keep local administration available without widening failed policy.
                networks = _parse_networks(list(portal_network.LOOPBACK_NETWORKS))
        return any(client_ip in network for network in networks)


class PortalRouterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # Only failed requests are logged; successful ones carry nothing to act on.
        if isinstance(code, int) and code < 400:
            return
        super().log_request(code, size)

    def _json(self, status: int, payload: dict, headers: list[tuple[str, str]] | None = None) -> None:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers or []:
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _backend_unavailable(self) -> None:
        accept = str(self.headers.get("Accept") or "")
        if self.command not in {"GET", "HEAD"} or "text/html" not in accept:
            return self._json(502, _public_backend_error())
        body = BACKEND_UNAVAILABLE_PAGE.encode("utf-8")
        self.send_response(502)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Retry-After", "5")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _client_gone(self) -> bool:
        """Whether the client closed its connection (a refresh, a closed tab)."""

        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _connect(self, backend: Backend) -> http.client.HTTPConnection | None:
        """A connection to the backend, waiting out a restart that refuses connections.

        ``None`` when the client left meanwhile.  Raises the refusal when the
        backend stays away longer than the restart wait.
        """

        deadline = time.monotonic() + self.server.restart_wait  # type: ignore[attr-defined]
        delays = retry_delays()
        while True:
            connection = http.client.HTTPConnection(backend.host, backend.port, timeout=self.server.backend_timeout)  # type: ignore[attr-defined]
            try:
                connection.connect()
                # Connecting again and again to a closed port in the ephemeral
                # range can meet itself (TCP simultaneous open): no backend.
                if connection.sock.getsockname() == connection.sock.getpeername():
                    raise ConnectionRefusedError("backend port connected to itself")
                return connection
            except ConnectionRefusedError:
                connection.close()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
            if self._client_gone():
                return None
            time.sleep(min(next(delays), remaining))

    def _content_length(self) -> int:
        values = self.headers.get_all("Content-Length", [])
        if not values:
            return 0
        normalized = {str(item).strip() for item in values}
        if len(normalized) != 1:
            raise ValueError("conflicting Content-Length headers")
        value = int(normalized.pop())
        if value < 0:
            raise ValueError("invalid Content-Length")
        return value

    def _proxy(self) -> None:
        if not self.path.startswith("/") or urlsplit(self.path).scheme:
            return self._json(400, {"error": "invalid request target"})
        transfer_encoding = str(self.headers.get("Transfer-Encoding") or "").strip().lower()
        if transfer_encoding and transfer_encoding != "identity":
            return self._json(501, {"error": "chunked request bodies are not supported"})
        try:
            body_length = self._content_length()
        except (TypeError, ValueError) as exc:
            return self._json(400, {"error": str(exc)})

        backends = self.server.current_backends()  # type: ignore[attr-defined]
        backend_name = self.server.policy.select(  # type: ignore[attr-defined]
            self.path,
            self.headers.get("Cookie", ""),
            set(backends),
        )
        backend = backends[backend_name]
        try:
            connection = self._connect(backend)
        except OSError as exc:
            self.log_error("Portal backend unavailable: %s", exc)
            return self._backend_unavailable()
        if connection is None:
            self.close_connection = True
            return
        try:
            connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            forwarded_for, forwarded_proto = _forwarded_metadata(
                self.client_address[0],
                self.headers,
            )
            for key, value in self.headers.raw_items():
                lower = key.lower()
                if lower in HOP_HEADERS or lower == "x-portal-selected-backend":
                    continue
                if lower in {"x-forwarded-for", "x-forwarded-proto"}:
                    continue
                connection.putheader(key, value)
            connection.putheader("X-Forwarded-For", forwarded_for)
            connection.putheader("X-Forwarded-Proto", forwarded_proto)
            connection.putheader("X-Portal-Selected-Backend", backend_name)
            connection.endheaders()

            remaining = body_length
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ConnectionError("request body ended before Content-Length")
                connection.send(chunk)
                remaining -= len(chunk)

            response = connection.getresponse()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                lower = key.lower()
                if lower in HOP_HEADERS or lower in {"server", "date", "x-portal-backend"}:
                    continue
                self.send_header(key, value)
            self.send_header("X-Portal-Backend", backend_name)
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                read_chunk = getattr(response, "read1", response.read)
                while True:
                    chunk = read_chunk(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            if not self.wfile.closed:
                try:
                    self.log_error("Portal backend unavailable: %s", exc)
                    self._json(502, _public_backend_error())
                except Exception:
                    pass
        finally:
            connection.close()
            self.close_connection = True

    def _preference_cookie(self, actor: str, backend: str) -> str:
        parts = [
            f"{DEPLOYMENT_PREFERENCE_COOKIE}={_encode_deployment_preference(actor, backend)}",
            "Path=/",
            f"Max-Age={DEPLOYMENT_PREFERENCE_MAX_AGE}",
            "HttpOnly",
            "SameSite=Lax",
        ]
        _forwarded_for, forwarded_proto = _forwarded_metadata(
            self.client_address[0],
            self.headers,
        )
        if forwarded_proto == "https":
            parts.append("Secure")
        return "; ".join(parts)

    def _preference_payload(self, actor: str, preference: str) -> dict:
        backends = self.server.current_backends()  # type: ignore[attr-defined]
        default_backend = self.server.policy.default_backend(actor, set(backends))  # type: ignore[attr-defined]
        return {
            "ok": True,
            "username": actor,
            "default_instance": default_backend,
            "preference": preference or None,
            "effective_instance": preference or default_backend,
            "instances": self.server.public_instances(),  # type: ignore[attr-defined]
        }

    def _deployment_preference(self) -> None:
        cookie_header = self.headers.get("Cookie", "")
        actor = self.server.policy.verifier.verify_cookie(cookie_header)  # type: ignore[attr-defined]
        if not actor:
            return self._json(401, {"error": "login required"})

        if self.command in {"GET", "HEAD"}:
            preference = self.server.policy.preference(  # type: ignore[attr-defined]
                actor,
                cookie_header,
                set(self.server.current_backends()),  # type: ignore[attr-defined]
            )
            return self._json(200, self._preference_payload(actor, preference))
        if self.command != "POST":
            return self._json(405, {"error": "method not allowed"}, [("Allow", "GET, HEAD, POST")])

        _client, scheme = _forwarded_metadata(self.client_address[0], self.headers)
        origin = str(self.headers.get("Origin") or "").rstrip("/").casefold()
        if origin and origin != f"{scheme}://{self.headers.get('Host', '')}".casefold():
            return self._json(403, {"error": "cross-origin request refused"})

        transfer_encoding = str(self.headers.get("Transfer-Encoding") or "").strip().lower()
        if transfer_encoding and transfer_encoding != "identity":
            return self._json(501, {"error": "chunked request bodies are not supported"})
        try:
            body_length = self._content_length()
        except (TypeError, ValueError) as exc:
            return self._json(400, {"error": str(exc)})
        if body_length > 4096:
            return self._json(413, {"error": "request body is too large"})
        try:
            payload = json.loads(self.rfile.read(body_length).decode("utf-8")) if body_length else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"error": "request body must be valid JSON"})
        backend = str(payload.get("instance") or "").strip().lower() if isinstance(payload, dict) else ""
        if backend not in self.server.current_backends():  # type: ignore[attr-defined]
            return self._json(400, {"error": "unknown or unavailable Portal instance"})
        return self._json(
            200,
            self._preference_payload(actor, backend),
            [("Set-Cookie", self._preference_cookie(actor, backend))],
        )

    def _dispatch(self) -> None:
        self.server.record_sources()  # type: ignore[attr-defined]
        clean_path = urlsplit(self.path).path
        if clean_path == "/__portal_router/health":
            return self._json(200, _public_health_payload())
        if clean_path == RUNTIME_STATUS_PATH:
            if self.command not in {"GET", "HEAD"}:
                return self._json(
                    405,
                    {"error": "method not allowed"},
                    [("Allow", "GET, HEAD")],
                )
            try:
                payload = self.server.runtime_status()  # type: ignore[attr-defined]
            except Exception as exc:
                self.log_error("Portal runtime status unavailable: %s", exc)
                payload = {
                    "schema": "tfi.portal-runtime-status.v1",
                    "ok": False,
                    "ready": False,
                    "state": "blocked",
                    "code": "runtime_status_unavailable",
                    "terminal": True,
                    "retryable": False,
                    "title": "服务状态无法确认",
                    "message": "无法读取独立服务状态。自动重连已停止。",
                }
            return self._json(200, _public_runtime_status(payload))
        if clean_path == DEPLOYMENT_PREFERENCE_PATH:
            return self._deployment_preference()
        return self._proxy()

    do_GET = _dispatch
    do_HEAD = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch
    do_OPTIONS = _dispatch


def _parse_networks(values: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return [ipaddress.ip_network(value, strict=False) for value in values]


def _listener_settings(args) -> tuple[list[str], int, list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    access = portal_network.router_access()
    if args.allow_network and not access["explicit_mode"]:
        portal_network.initialize_allowed_networks(args.allow_network)
        access = portal_network.router_access()
    binds = (["127.0.0.1"] if access["mode"] == "local" else ["0.0.0.0"]) if access["explicit_mode"] else (
        args.bind or (["0.0.0.0"] if access["mode"] == "remote" else ["127.0.0.1"])
    )
    port = access["port"] if access["explicit_port"] else (args.port or access["port"])
    return binds, port, _parse_networks(portal_network.allowed_networks())


def main() -> int:
    parser = argparse.ArgumentParser(description="Route Portal users between registered Portal instances.")
    parser.add_argument("--bind", action="append", default=[], help="Bind address. Can be repeated.")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--registry-path",
        type=Path,
        default=Path(os.getenv("PORTAL_DEPLOYMENT_REGISTRY_PATH", str(DEFAULT_DEPLOYMENT_REGISTRY_PATH))),
    )
    parser.add_argument("--dev-users", default=os.getenv(
        "PORTAL_ROUTER_DEV_USERS", ",".join(compatibility_value("router_dev_users", []))))
    parser.add_argument(
        "--session-signing-key-path",
        type=Path,
        default=None,
    )
    parser.add_argument("--allow-network", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=float(os.getenv("PORTAL_ROUTER_BACKEND_TIMEOUT", "3600")))
    args = parser.parse_args()

    binds, port, networks = _listener_settings(args)
    policy = RouterPolicy(
        PortalSessionAuthority(args.session_signing_key_path),
        set(args.dev_users.split(",")),
    )
    registry = PortalDeploymentRegistry(args.registry_path)
    backends = {
        item.instance_id: Backend.parse(item.instance_id, item.origin)
        for item in registry.deployments()
    }
    servers = [
        PortalRouterServer(
            (host, port),
            policy,
            backends,
            networks,
            max(1.0, args.timeout),
            registry,
            source_manifest=source_manifest_path(),
            network_settings_path=portal_network.config_path(),
        )
        for host in binds
    ]
    servers[0].record_sources()
    threads = [threading.Thread(target=server.serve_forever, name=f"portal-router-{server.server_address[0]}", daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    print(
        f"Portal deployment router listening on {', '.join(f'{server.server_address[0]}:{server.server_address[1]}' for server in servers)}",
        flush=True,
    )
    try:
        threads[0].join()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
