"""Private, process-owned model protocol conversion for native harnesses.

Only loopback is bound. An unguessable capability selects one registered account,
API and client protocol; a request cannot supply a target URL or another account.
Native endpoints remain direct unless their declared transport hints require
projection. No Agent loop or history is kept here.
"""
from __future__ import annotations

import atexit
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import select
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry
from . import model_api_stream as stream
from . import model_api_wire as wire

MAX_BODY = 64 * 1024 * 1024


class AccountUnavailable(wire.WireError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A configured endpoint cannot redirect a credential elsewhere.


def open_upstream(entry, protocol, payload):
    base, auth = AIProviderRegistry.harness_endpoint(entry, protocol)
    key = AIProviderRegistry.read_secret(AIProviderRegistry.secret_path(entry))
    headers = AIProviderRegistry.headers(str(entry.get("provider") or ""), key, entry)
    if protocol == wire.MESSAGES:
        path = "/v1/messages"
        headers["anthropic-version"] = "2023-06-01"
        if auth == "x-api-key":
            headers.pop("Authorization", None)
            headers["x-api-key"] = key
    else:
        path = "/responses" if protocol == wire.RESPONSES else "/chat/completions"
    headers.update({"Content-Type": "application/json", "Accept": "text/event-stream"})
    context = ssl.create_default_context()
    if sys.platform == "win32":
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers, method="POST")
    return opener.open(request, timeout=max(1, float(entry.get("timeout_s") or 360)))


class Bridge:
    """One native host's conversion server. Its capability expires with the host."""
    def __init__(self, resolve, target):
        self.resolve, self.target = resolve, target
        self.token = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(32)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.bridge = self
        self.thread = threading.Thread(target=self.server.serve_forever, name="model-protocol-bridge", daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        return origin if self.target == wire.MESSAGES else origin + "/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)
        self.streaming = False

    def log_message(self, *_):
        pass  # Neither native prompts nor credentials belong in access logs.

    def _json(self, status, body, **headers):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, message):
        kind = ("authentication_error" if status in (401, 403) else "rate_limit_error" if status == 429
                else "invalid_request_error" if status < 500 else "api_error")
        value = {"type": "error", "error": {"type": kind, "message": message}}
        try:
            if self.streaming:
                self.wfile.write(stream.frame(value))
                self.wfile.flush()
            else:
                self._json(status, value if self.server.bridge.target == wire.MESSAGES else {"error": value["error"]})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def _authorize(self):
        bridge = self.server.bridge
        value = self.headers.get("Authorization", "")
        key = value[7:] if value.startswith("Bearer ") else self.headers.get("x-api-key", "")
        if not hmac.compare_digest(key.encode(), bridge.token.encode()):
            self._error(401, "Invalid model adapter capability")
            return None
        entry = bridge.resolve()
        if not AIProviderRegistry.is_enabled(entry):
            raise AccountUnavailable("The selected Model API is disabled")
        return entry

    def do_GET(self):
        try:
            entry = self._authorize()
            if entry is None:
                return
            if urlsplit(self.path).path != "/v1/models":
                self._error(404, "Unknown model adapter endpoint")
                return
            self._json(200, {"data": [{"id": model, "type": "model", "display_name": model} for model in AIProviderRegistry.harness_models(entry)], "has_more": False})
        except (ValueError, OSError):
            self._error(503, "The selected model account is unavailable")

    def do_POST(self):
        bridge = self.server.bridge
        acquired = relaying = False
        try:
            entry = self._authorize()
            if entry is None:
                return
            path = urlsplit(self.path).path
            expected = {wire.MESSAGES: "/v1/messages", wire.RESPONSES: "/v1/responses", wire.CHAT: "/v1/chat/completions"}[bridge.target]
            if path not in (expected, expected + "/count_tokens") or (path.endswith("count_tokens") and bridge.target != wire.MESSAGES):
                self._error(404, "Unknown model adapter endpoint")
                return
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get("Transfer-Encoding") or not 0 < size <= MAX_BODY:
                self._error(413, "Model adapter requires a bounded JSON request")
                self.close_connection = True
                return
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise wire.WireError("Incomplete model request")
            payload = json.loads(raw)
            if payload.get("model") not in AIProviderRegistry.harness_models(entry):
                raise wire.WireError("The model is not enabled for the selected API")
            if bridge.target == wire.CHAT:
                # Same-protocol projection preserves the complete business
                # payload and upstream response, including vendor extensions.
                # Only the publicly declared, non-semantic routing hint may
                # be removed; this is not a generic field-dropping mechanism.
                contract = AIProviderRegistry.harness_parameter_contracts(entry)[wire.CHAT]['omitted_advisory_fields']
                omitted = []
                if 'prompt_cache_key' in payload and contract:
                    if payload['prompt_cache_key'] is not None and not isinstance(payload['prompt_cache_key'], str):
                        raise wire.WireError('prompt_cache_key must be text or null')
                    payload = {k: v for k, v in payload.items() if k != 'prompt_cache_key'}
                    omitted = contract
                acquired = bridge.slots.acquire(blocking=False)
                if not acquired:
                    self._error(429, 'Model adapter concurrency limit reached')
                    return
                relaying = True
                self._relay_chat(entry, payload, omitted)
                return
            decoded = wire.request(payload, bridge.target)
            protocol = wire.upstream(entry, bridge.target)
            if path.endswith("count_tokens"):
                # No common cross-vendor tokenizer exists. This is a documented
                # local context estimate, never an inference usage/billing record.
                self._json(200, {"input_tokens": len(json.dumps(decoded, ensure_ascii=False).encode())}, **{"X-TFI-Token-Count": "estimated-utf8-bytes"})
                return
            converted = wire.encode(decoded, protocol)
            acquired = bridge.slots.acquire(blocking=False)
            if not acquired:
                self._error(429, "Model adapter concurrency limit reached")
                return
            relaying = True
            self._relay(entry, protocol, converted, payload.get("stream") is True, decoded["tool_map"])
        except urllib.error.HTTPError as error:
            # Provider bodies can echo authorization or request data. Return the
            # status without exposing credentials/prompts or retrying a model call.
            self._error(error.code if 400 <= error.code <= 599 else 502, f"Selected Model API returned HTTP {error.code}")
            error.close()
        except AccountUnavailable as error:
            self._error(503, str(error))
        except (wire.WireError, KeyError, TypeError, ValueError, StopIteration) as error:
            self._error(502 if relaying else 400, str(error) if isinstance(error, wire.WireError) else "Invalid model protocol payload")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (OSError, TimeoutError):
            self._error(502, "Selected Model API connection failed or timed out")
        finally:
            if acquired:
                bridge.slots.release()
            self.close_connection = True

    def _relay_chat(self, entry, payload, omitted):
        """Keep native Chat SSE/JSON bytes; the native SDK owns decoding."""
        with open_upstream(entry, wire.CHAT, payload) as response:
            content_type = response.headers.get('Content-Type', '')
            if not any(kind in content_type for kind in ('text/event-stream', 'json')):
                raise wire.WireError('Model API returned neither JSON nor an event stream')
            raw = None
            if 'text/event-stream' not in content_type:
                raw = response.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise wire.WireError('Model response exceeds 64 MiB')
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            if omitted:
                self.send_header('X-TFI-Omitted-Advisory-Fields', ','.join(omitted))
            self.end_headers()
            self.streaming = 'text/event-stream' in content_type
            if raw is not None:
                self.wfile.write(raw)
                return
            stopped = threading.Event()
            watcher = threading.Thread(target=self._disconnect_watch, args=(response, stopped), daemon=True)
            watcher.start()
            try:
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            finally:
                stopped.set()
                watcher.join(1)

    def _relay(self, entry, protocol, payload, streaming, tool_map):
        with open_upstream(entry, protocol, payload) as response:
            writer = stream.Writer(self.server.bridge.target, payload["model"], tool_map)
            stopped = threading.Event()
            watcher = threading.Thread(target=self._disconnect_watch, args=(response, stopped), daemon=True)
            watcher.start()
            try:
                content_type = response.headers.get("Content-Type", "")
                if "text/event-stream" in content_type:
                    events = stream.sse(response)
                elif "json" in content_type:
                    raw = response.read(MAX_BODY + 1)
                    if len(raw) > MAX_BODY:
                        raise wire.WireError("Model response exceeds 64 MiB")
                    events = stream.json_events(json.loads(raw), protocol)
                else:
                    raise wire.WireError("Model API returned neither JSON nor an event stream")
                # Validate the beginning before committing HTTP 200.
                decoded = iter(stream.decode(events, protocol))
                first = next(decoded)
                if streaming:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.streaming = True
                def emit(event):
                    for frame in writer.take(*event):
                        if streaming:
                            self.wfile.write(stream.frame(frame))
                            self.wfile.flush()
                emit(first)
                for event in decoded:
                    emit(event)
                if not streaming:
                    self._json(200, writer.result)
            finally:
                stopped.set()
                watcher.join(1)

    def _disconnect_watch(self, response, stopped):
        while not stopped.wait(.2):
            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable and self.connection.recv(1, socket.MSG_PEEK) == b"":
                    # HTTPResponse.close alone can block behind its reader lock.
                    upstream_socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                    if upstream_socket is not None:
                        upstream_socket.shutdown(socket.SHUT_RDWR)
                    return
            except OSError:
                return


_bridges, _lock = {}, threading.Lock()


def resolve_account(api_id, account_id=""):
    if account_id:
        from .execution.account_registrations import registration
        row = registration(account_id)
        if not row or not row["enabled"] or row["kind"] != "api_profile" or row["config"].get("api_id") != api_id:
            raise AccountUnavailable("The selected model account is unavailable")
    try:
        return shared_ai_provider_registry.get(api_id)
    except ValueError:
        raise AccountUnavailable("The selected Model API is unavailable") from None


def connection(entry, target, *, account_id=""):
    """Native routes are direct unless the public parameter contract needs projection."""
    omitted = AIProviderRegistry.harness_parameter_contracts(entry).get(target, {}).get('omitted_advisory_fields', [])
    if wire.upstream(entry, target) == target and not omitted:
        base, auth = AIProviderRegistry.harness_endpoint(entry, target)
        key = AIProviderRegistry.read_secret(AIProviderRegistry.secret_path(entry))
        return base, auth, key
    api_id = entry.get("api_id") or entry["id"]
    identity = (api_id, account_id, target)
    with _lock:
        bridge = _bridges.get(identity)
        if bridge is None:
            bridge = Bridge(lambda: resolve_account(api_id, account_id), target)
            _bridges[identity] = bridge
    return bridge.base_url, "bearer", bridge.token


def close_bridges():
    with _lock:
        bridges = list(_bridges.values())
        _bridges.clear()
    for bridge in bridges:
        bridge.close()


def local_proxy_bypass(environment):
    """A harness reaches its private adapter directly; upstream egress is unchanged."""
    result = dict(environment)
    for name in ("NO_PROXY", "no_proxy"):
        existing = environment.get(name) or os.environ.get(name, "")
        result[name] = ",".join(filter(None, (existing, "127.0.0.1", "localhost")))
    return result


atexit.register(close_bridges)
