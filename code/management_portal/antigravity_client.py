"""ACP v1 stdio transport and an execution-scoped MCP endpoint.

No model loop lives here. Google owns the agent, native tools and durable sessions.
Only explicit login clients may let the native runtime start interactive OAuth.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import re
import secrets
import subprocess
import threading
import time
from urllib.parse import urlsplit

from management_common.process_runtime import spawn_process, terminate_process_tree


class AcpError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class AuthenticationRequired(AcpError):
    def __init__(self):
        super().__init__(-32000, "Antigravity requires Google login; open the account login in Portal")


class AcpClient:
    def __init__(self, command, *, env, cwd=None, on_update=None, on_request=None, on_login_url=None):
        self.on_update = on_update or (lambda params: None)
        self.on_request = on_request or (lambda method, params: None)
        self.on_login_url = on_login_url
        self._lock = threading.Lock()
        self._pending = {}
        self._next = 0
        self._failure = None
        self.process = spawn_process(command, env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._stderr = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr.start()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _fail(self, error):
        with self._lock:
            if self._failure is not None:
                return
            self._failure = error
            for pending in self._pending.values():
                pending.put(error)

    def _read_stderr(self):
        # Discard native diagnostics: they can include account and token data.
        for line in self.process.stderr:
            for url in re.findall(r"https://[^\s<>]+", line):
                parsed = urlsplit(url)
                if parsed.hostname == "accounts.google.com" and "oauth" in parsed.path:
                    if self.on_login_url is None:
                        self._fail(AuthenticationRequired())
                    else:
                        self.on_login_url(url)

    def _read(self):
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                if message.get("method"):
                    method, params = message["method"], message.get("params") or {}
                    if "id" in message:
                        try:
                            result = self.on_request(method, params)
                        except Exception as exc:
                            self._fail(exc)
                            return
                        reply = {"jsonrpc": "2.0", "id": message["id"]}
                        reply.update({"result": result} if result is not None else
                                     {"error": {"code": -32601, "message": "Client method not supported"}})
                        self._send(reply)
                    elif method == "session/update":
                        try:
                            self.on_update(params)
                        except Exception as exc:
                            # Execution ownership and service failures belong to
                            # the caller, not to Google's protocol/error recovery.
                            self._fail(exc)
                            return
                else:
                    with self._lock:
                        pending = self._pending.get(message.get("id"))
                    if pending is not None:
                        pending.put(message)
        except Exception as exc:
            self._fail(AcpError(-32603, f"Antigravity ACP stream failed ({type(exc).__name__})"))
        finally:
            self._fail(AcpError(-32001, "Antigravity ACP connection closed; input was not replayed"))

    def _send(self, message):
        with self._lock:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def call(self, method, params, *, timeout=60, cancelled=None, session_id=""):
        with self._lock:
            if self._failure:
                raise self._failure
            self._next += 1
            identifier = self._next
            pending = self._pending[identifier] = queue.Queue()
        deadline = time.monotonic() + timeout if timeout else None
        cancellation_sent = False
        try:
            self._send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
            while True:
                if cancelled and cancelled() and not cancellation_sent:
                    cancellation_sent = True
                    self.notify("session/cancel", {"sessionId": session_id})
                    # Bound cleanup only; there is no overall prompt timeout.
                    deadline = time.monotonic() + 10
                if deadline and time.monotonic() >= deadline:
                    if cancellation_sent:
                        raise InterruptedError("Antigravity turn cancelled")
                    raise TimeoutError(f"Antigravity ACP {method} timed out")
                try:
                    message = pending.get(timeout=.2)
                except queue.Empty:
                    continue
                if isinstance(message, Exception):
                    raise message
                if "error" in message:
                    error = message["error"]
                    # Native error bodies can include sensitive auth/transport data.
                    if error.get("code") == -32000:
                        raise AuthenticationRequired()
                    raise AcpError(error.get("code"), f"Antigravity ACP {method} failed (code {error.get('code')})")
                if cancellation_sent:
                    raise InterruptedError("Antigravity turn cancelled")
                return message.get("result") or {}
        finally:
            with self._lock:
                self._pending.pop(identifier, None)

    def initialize(self):
        result = self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                          "clientInfo": {"name": "tfi-portal", "version": "1.0"}})
        if result.get("protocolVersion") != 1:
            raise ValueError("Unsupported ACP protocol version")
        return result

    def close(self):
        terminate_process_tree(self.process, timeout=5)
        self._reader.join(2)
        self._stderr.join(2)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()


class ToolServer:
    """The current execution's tools; a new private endpoint for every invocation."""
    def __init__(self, catalog, dispatch):
        self.catalog, self.dispatch = catalog, dispatch
        self.token = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != "/mcp" or not secrets.compare_digest(
                        self.headers.get("Authorization", ""), f"Bearer {owner.token}"):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8 * 1024 * 1024:
                        raise ValueError("Invalid MCP request length")
                    request = json.loads(self.rfile.read(length))
                    if "id" not in request:
                        self.send_response(202)
                        self.end_headers()
                        return
                    method, params = request.get("method"), request.get("params") or {}
                    if method == "initialize":
                        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "portal", "version": "1.0"}}
                    elif method == "tools/list":
                        result = {"tools": owner.catalog}
                    elif method == "tools/call":
                        result = owner.dispatch({"tool": params.get("name"), "arguments": params.get("arguments", {}),
                                                 "call_id": f"acp-mcp-{request['id']}"})
                    elif method == "ping":
                        result = {}
                    else:
                        raise ValueError("Unsupported MCP method")
                    body = {"jsonrpc": "2.0", "id": request["id"], "result": result}
                except Exception:
                    body = {"jsonrpc": "2.0", "id": locals().get("request", {}).get("id"),
                            "error": {"code": -32603, "message": "Portal tool request failed"}}
                encoded = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def descriptor(self):
        return {"type": "http", "name": "portal", "url": f"http://127.0.0.1:{self.server.server_port}/mcp",
                "headers": [{"name": "Authorization", "value": f"Bearer {self.token}"}]}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
