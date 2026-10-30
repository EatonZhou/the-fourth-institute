"""Portal's control bridge for one OpenCode session (the OpenCode Adapter's agent side).

``OpenCodeClient`` starts one bridge per Portal session.  The bridge runs
OpenCode's headless server (``opencode serve``) in the session's working
directory and speaks Portal's native agent protocol on a Unix socket: a turn
(``turn/run``) with its events and Portal tool calls, input during the turn
(``turn/steer``), interrupts, compaction and release.

OpenCode reaches the Portal tools as an MCP server the bridge serves on the
loopback interface; a call goes to the Worker that follows the turn, as the
other native Adapters' calls do.  OpenCode's events become the common item
events (``item/started``, ``item/completed``) under the turn's ID.

Standard library only: the bridge runs as its own process, outside Portal's
packages (``python3 -I``).
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# This entry point is intentionally launched with -I. Add only this checkout,
# never the caller's working directory or PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.local_ipc import LocalListener
from management_common.process_runtime import spawn_process, terminate_process_tree

PROTOCOL = "tfi.native-agent.v1"
# The route's key, from the client (``opencode_client.KEY_VARIABLE``); OpenCode
# gets it as its credential for the service, never in its environment.
KEY_VARIABLE = "PORTAL_OPENCODE_API_KEY"
# The name of Portal's MCP server in OpenCode; OpenCode prefixes the tool
# names with it (``portal_read_resource``).
MCP_SERVER = "portal"
TOOL_PREFIX = MCP_SERVER + "_"
# OpenCode's delegation tool: a subagent runs in a child session inside the call.
SUBAGENT_TOOL = "task"
# The Worker's word that one subagent was stopped (``provider_subagent_runs.STOPPING_METHOD``).
STOPPING_METHOD = "subagent/stopping"
# A turn whose Worker left (a handover) keeps running; its Portal tool calls
# wait this long for the next Worker.
DETACHED_TOOL_WAIT_SECONDS = 540
# A bridge without work exits after this long; the next operation starts it again.
IDLE_EXIT_SECONDS = 300
# A turn's run is also checked against OpenCode's session status this often,
# in case its idle event was missed.
STATUS_POLL_SECONDS = 5
# OpenCode's argument names, in the names the common tool events use.
ARGUMENT_NAMES = {"filePath": "file_path", "oldString": "old_string", "newString": "new_string",
                  "replaceAll": "replace_all"}


def _die_with_parent() -> None:
    """OpenCode's server stops when the bridge does, however the bridge ends."""

    import ctypes

    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def now_ms() -> int:
    return int(time.time() * 1000)


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), message, file=sys.stderr, flush=True)


class BridgeError(RuntimeError):
    pass


class OpenCodeServer:
    """The ``opencode serve`` process of this session and its HTTP API."""

    def __init__(self, config: dict, mcp_url: str, mcp_token: str) -> None:
        self.password = secrets.token_hex(24)
        self.auth = "Basic " + base64.b64encode(f"opencode:{self.password}".encode()).decode()
        settings = {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            # Portal's workspaces are shared and large: no snapshot repository per step.
            "snapshot": False,
            # A Portal turn has no one to answer OpenCode's questions; the
            # execution is a full-access one (``OpenCodeClient.run_turn``).
            "permission": {"*": "allow", "external_directory": "allow", "doom_loop": "allow"},
            "mcp": {MCP_SERVER: {"type": "remote", "url": mcp_url, "oauth": False,
                                 "headers": {"Authorization": f"Bearer {mcp_token}"}}},
        }
        provider = config.get("provider") or {}
        if provider:
            # A custom route's Model API is the session's only provider, titles included.
            service = str(config["service"])
            settings.update(provider=provider, enabled_providers=[service],
                            small_model=f"{service}/{next(iter(provider[service]['models']))}")
        state = Path(config["state_dir"])
        # The client started the bridge with the Resident's environment; OpenCode's
        # tools inherit OpenCode's, so the key stays out of it.
        env = dict(os.environ)
        key = env.pop(KEY_VARIABLE, "")
        env.update(
            # OpenCode's own directories: Portal's configuration for OpenCode
            # (not the host user's rules, agents and plugins), and this
            # session's data and logs.
            XDG_CONFIG_HOME=str(config["config_home"]),
            XDG_DATA_HOME=str(state / "data"),
            XDG_STATE_HOME=str(state / "state"),
            OPENCODE_CONFIG_CONTENT=json.dumps(settings),
            OPENCODE_SERVER_PASSWORD=self.password,
            OPENCODE_DB=str(state / "opencode.db"),
            OPENCODE_DISABLE_AUTOUPDATE="1",
            OPENCODE_DISABLE_SHARE="1",
            OPENCODE_DISABLE_PROJECT_CONFIG="1",
            OPENCODE_DISABLE_CLAUDE_CODE="1",
            OPENCODE_DISABLE_LSP_DOWNLOAD="1",
        )
        if provider:
            # Its models are declared; there is no service listing to fetch.
            env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
        self.process = spawn_process(
            [config["binary"], "serve", "--hostname", "127.0.0.1", "--port", "0"],
            cwd=config["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            **({"preexec_fn": _die_with_parent} if sys.platform == "linux" else {}),
        )
        # The client gives the bridge a minute to start; OpenCode ends with it.
        self.host, self.port = "", 0
        for line in self.process.stdout:
            match = re.search(r"listening on http://([\d.]+):(\d+)", line)
            if match:
                self.host, self.port = match.group(1), int(match.group(2))
                break
        if not self.port:
            terminate_process_tree(self.process, force=True)
            raise BridgeError("OpenCode server did not start")
        threading.Thread(target=self._drain, daemon=True).start()
        self.service = str(config.get("service") or "opencode")
        if key:
            # Before anything else: OpenCode reads the services' credentials when it first serves a request.
            self.request("PUT", f"/auth/{self.service}", {"type": "api", "key": key})

    def _drain(self) -> None:
        for line in self.process.stdout:
            if line.strip() and "OPENCODE_SERVER_PASSWORD" not in line:
                log("opencode: " + line.rstrip()[:2000])

    def request(self, method: str, path: str, body=None, timeout: float = 180):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            payload = None if body is None else json.dumps(body).encode()
            connection.request(method, path, body=payload, headers={
                "authorization": self.auth, "content-type": "application/json"})
            response = connection.getresponse()
            raw = response.read()
            if response.status >= 400:
                raise BridgeError(f"OpenCode {method} {path} failed: {response.status} {raw[:500].decode(errors='replace')}")
            return json.loads(raw) if raw.strip() else None
        finally:
            connection.close()

    def events(self):
        """OpenCode's event stream; ends when the connection drops."""

        connection = http.client.HTTPConnection(self.host, self.port, timeout=None)
        connection.request("GET", "/event", headers={"authorization": self.auth, "accept": "text/event-stream"})
        response = connection.getresponse()
        for raw in response:
            line = raw.decode(errors="replace").strip()
            if line.startswith("data:"):
                try:
                    yield json.loads(line[5:])
                except ValueError:
                    continue

    def stop(self) -> None:
        terminate_process_tree(self.process, timeout=30)


class Turn:
    """One Portal operation: its events, its Worker and its outcome."""

    def __init__(self, turn_id: str, emit, owner: Peer | None) -> None:
        self.id = turn_id
        self.emit = emit
        # The Worker connection that follows the turn.
        self.owner = owner
        self.events: list[dict] = []
        self.sent = 0
        self.done = threading.Event()
        # The operation's outcome, for every Worker that follows it.
        self.finished = threading.Event()
        self.result: dict | None = None
        self.failure = ""
        self.posted = False
        # The turn's model, system instructions and tool switches: OpenCode
        # takes them from the newest user message, so input sent during the
        # turn carries them too.
        self.settings: dict = {}
        self.busy = False
        self.error: dict | None = None
        self.started_ms = now_ms()
        self.items_started: set[str] = set()
        self.items_completed: set[str] = set()
        self.steered: set[str] = set()
        self.detach_timer: threading.Timer | None = None
        # Subagents: a child session's task call and its items waiting for that link.
        self.children: dict[str, str] = {}
        self.waiting_children: dict[str, list[dict]] = {}
        self.models: dict[str, str] = {}
        # Each subagent's tokens so far, for its card.
        self.child_usage: dict[str, dict] = {}
        # The task calls asked to stop before their child session started.
        self.stopping: set[str] = set()


class Peer:
    """One Worker's connection to the bridge."""

    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self.closed = False
        self.write_lock = threading.Lock()

    def sender(self, request_id):
        def send(body: dict) -> None:
            with self.write_lock:
                if self.closed:
                    return
                try:
                    self.connection.sendall(json.dumps({"id": request_id, **body}, ensure_ascii=False).encode() + b"\n")
                except OSError:
                    self.closed = True
        return send


class Bridge:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.session_id = config["session_id"]
        self.root = Path(config["state_dir"])
        self.state_path = self.root / "state.json"
        try:
            self.state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("opencode_session_id", "")
        self.state.setdefault("turns", {})
        self.lock = threading.RLock()
        self.current: Turn | None = None
        # Admission of a turn: one start at a time; a Worker that comes for
        # the same turn attaches to it.
        self.start_lock = threading.Lock()
        self.compacting = False
        self.pending: dict[str, dict] = {}
        self.roles: dict[str, str] = {}
        self.message_sessions: dict[str, str] = {}
        self.tool_parts: dict[str, dict] = {}
        self.part_types: dict[str, str] = {}
        self.matched_calls: set[str] = set()
        self.tools: list[dict] = []
        self.tools_key = fingerprint([])
        self.last_used = time.monotonic()
        self.mcp_token = secrets.token_hex(24)
        self.mcp = ThreadingHTTPServer(("127.0.0.1", 0), self._mcp_handler())
        self.mcp.daemon_threads = True
        threading.Thread(target=self.mcp.serve_forever, daemon=True).start()
        self.server = OpenCodeServer(config, f"http://127.0.0.1:{self.mcp.server_address[1]}/mcp", self.mcp_token)
        self.version = str(config.get("version") or "")
        self.context_window = 0
        self.context_model = ""
        self.context_sample = None
        threading.Thread(target=self._follow_events, daemon=True).start()

    # -- state -------------------------------------------------------------

    def save(self) -> None:
        temporary = self.state_path.with_name(f".state.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(self.state))
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_path)

    @property
    def opencode_session(self) -> str:
        return str(self.state.get("opencode_session_id") or "")

    def ensure_session(self) -> str:
        if not self.opencode_session:
            created = self.server.request("POST", "/session", {"title": f"Portal {self.session_id}"})
            self.state["opencode_session_id"] = created["id"]
            self.save()
        return self.opencode_session

    # -- Portal tools as an MCP server ------------------------------------

    def ensure_tools(self, tools: list[dict]) -> None:
        """OpenCode lists an MCP server's tools when it connects: a changed set reconnects it."""

        key = fingerprint(tools)
        if key == self.tools_key:
            return
        self.tools = [dict(tool) for tool in tools]
        self.tools_key = key
        self.server.request("POST", f"/mcp/{MCP_SERVER}/disconnect")
        self.server.request("POST", f"/mcp/{MCP_SERVER}/connect")

    def _mcp_handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status: int, body=None) -> None:
                payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                if body is not None:
                    self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _authorized(self) -> bool:
                if secrets.compare_digest(self.headers.get("authorization", ""), f"Bearer {bridge.mcp_token}"):
                    return True
                self._reply(403)
                return False

            def do_GET(self):
                # No server-initiated stream: every answer is a JSON response.
                if self._authorized():
                    self._reply(405)

            def do_DELETE(self):
                if self._authorized():
                    self._reply(200)

            def do_POST(self):
                if not self._authorized():
                    return
                try:
                    message = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                except ValueError:
                    return self._reply(400)
                if not isinstance(message, dict) or "id" not in message:
                    return self._reply(202)
                method, params = message.get("method"), message.get("params") or {}
                try:
                    if method == "initialize":
                        result = {"protocolVersion": params.get("protocolVersion") or "2025-03-26",
                                  "capabilities": {"tools": {"listChanged": False}},
                                  "serverInfo": {"name": "tfi-portal", "version": "1"}}
                    elif method == "tools/list":
                        result = {"tools": [{"name": tool["name"], "description": tool.get("description") or "",
                                             "inputSchema": tool.get("inputSchema") or {"type": "object"}}
                                            for tool in bridge.tools]}
                    elif method == "tools/call":
                        result = bridge.call_tool(str(params.get("name") or ""), params.get("arguments") or {})
                    elif method == "ping":
                        result = {}
                    else:
                        return self._reply(200, {"jsonrpc": "2.0", "id": message["id"],
                                                 "error": {"code": -32601, "message": f"Unknown method {method}"}})
                except Exception as error:  # noqa: BLE001 - the model sees the failure as the tool's result
                    result = {"isError": True, "content": [{"type": "text", "text": f"Portal tool failed: {error}"}]}
                self._reply(200, {"jsonrpc": "2.0", "id": message["id"], "result": result})

        return Handler

    def _call_id(self, name: str, arguments: dict) -> str:
        """The OpenCode call this MCP request comes from (its tool part runs with the same input)."""

        deadline = time.monotonic() + 6
        while True:
            with self.lock:
                for call_id, part in reversed(list(self.tool_parts.items())):
                    state = part.get("state") or {}
                    if (call_id not in self.matched_calls and part.get("tool") == TOOL_PREFIX + name
                            and state.get("status") == "running" and (state.get("input") or {}) == arguments):
                        self.matched_calls.add(call_id)
                        return call_id
            if time.monotonic() > deadline:
                return "portal_" + secrets.token_hex(8)
            time.sleep(0.05)

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name not in {tool["name"] for tool in self.tools}:
            raise BridgeError(f"Unknown Portal tool {name}")
        call_id = self._call_id(name, arguments)
        done = threading.Event()
        call = {"call_id": call_id, "tool": name, "arguments": arguments}
        entry = {"call": call, "done": done, "result": None}
        with self.lock:
            turn = self.current
            if turn is None:
                raise BridgeError("No Portal turn is running")
            self.pending[call_id] = entry
            if turn.emit is not None:
                turn.emit({"type": "tool_call", **call})
        while not done.wait(1):
            with self.lock:
                if self.current is not turn:
                    self.pending.pop(call_id, None)
                    raise BridgeError("The Portal turn ended before the tool call was answered")
                waited_without_worker = turn.emit is None and turn.detach_timer is None
            if waited_without_worker:
                self.pending.pop(call_id, None)
                raise BridgeError("No Portal Worker took the tool call; outcome unknown")
        result = entry["result"] or {}
        return {"content": result.get("content") or [], "isError": bool(result.get("isError"))}

    def tool_result(self, call_id: str, result: dict) -> None:
        with self.lock:
            entry = self.pending.pop(str(call_id or ""), None)
        if entry is not None:
            entry["result"] = result if isinstance(result, dict) else {}
            entry["done"].set()

    # -- events ------------------------------------------------------------

    def _follow_events(self) -> None:
        while self.server.process.poll() is None:
            try:
                for event in self.server.events():
                    self.on_event(event)
            except Exception as error:  # noqa: BLE001 - reconnect the stream
                log(f"event stream: {error}")
            time.sleep(0.5)
        log("OpenCode server exited")
        os._exit(1)

    def notify(self, turn: Turn, event: dict) -> None:
        turn.events.append(event)
        if turn.emit is not None:
            turn.emit({"type": "event", "event": event})
            turn.sent = len(turn.events)

    def on_event(self, event: dict) -> None:
        kind = str(event.get("type") or "")
        props = event.get("properties") or {}
        with self.lock:
            turn = self.current
            ours = self.opencode_session
            session = str(props.get("sessionID") or (props.get("info") or {}).get("sessionID") or "")
            if kind == "message.updated":
                info = props.get("info") or {}
                self.roles[str(info.get("id"))] = str(info.get("role") or "")
                self.message_sessions[str(info.get("id"))] = str(info.get("sessionID") or "")
                if turn is not None and info.get("role") == "assistant" and info.get("modelID"):
                    turn.models[str(info.get("sessionID") or "")] = str(info["modelID"])
                    if session == ours and isinstance(info.get("tokens"), dict):
                        model = str(info["modelID"])
                        usage = canonical_usage(info["tokens"])
                        tokens = int(usage.get("input_tokens") or 0)
                        if tokens:
                            if model != self.context_model:
                                self.context_window = self.window(model)
                                self.context_model = model if self.context_window else ""
                            sample = (model, tokens, self.context_window)
                            if sample != self.context_sample:
                                self.context_sample = sample
                                self.notify(turn, {"method": "thread/tokenUsage/updated", "params": {
                                    "threadId": self.session_id, "turnId": turn.id,
                                    "tokenUsage": {"context": {"inputTokens": tokens}, "modelContextWindow": self.context_window},
                                }})
                return
            if kind in {"permission.asked", "permission.v2.asked"} and session:
                threading.Thread(target=self._allow, args=(str(props.get("id") or ""),), daemon=True).start()
                return
            if kind in {"question.asked", "question.v2.asked"} and session:
                threading.Thread(target=self._reject_question, args=(str(props.get("id") or ""),), daemon=True).start()
                return
            if turn is None or not ours:
                return
            if kind == "session.status" and session == ours:
                state = str((props.get("status") or {}).get("type") or "")
                if turn.posted and state in {"busy", "retry"}:
                    turn.busy = True
                return
            if kind == "session.error" and session == ours:
                turn.error = props.get("error") or {"name": "UnknownError"}
                return
            if kind == "session.idle" and session == ours:
                if turn.posted:
                    turn.done.set()
                return
            if kind == "message.part.delta" and session == ours:
                part = str(props.get("partID") or "")
                if (props.get("field") == "text" and self.part_types.get(part) == "text"
                        and self.roles.get(str(props.get("messageID"))) == "assistant"):
                    self.notify(turn, {"method": "item/agentMessage/delta", "params": {
                        "threadId": self.session_id, "turnId": turn.id, "itemId": part,
                        "delta": str(props.get("delta") or "")}})
                return
            if kind != "message.part.updated":
                return
            part = props.get("part") or {}
            session = session or str(part.get("sessionID") or "")
            self.part_types[str(part.get("id"))] = str(part.get("type") or "")
            if part.get("type") == "tool":
                call = str(part.get("callID") or part.get("id"))
                self.tool_parts[call] = part
                child = task_session(part)
                if call in turn.stopping and child:
                    turn.stopping.discard(call)
                    threading.Thread(target=self._abort_child, args=(child,), daemon=True).start()
            if session == ours:
                for item in self.translate(turn, part, session):
                    self.notify(turn, item)
                return
            self._child_part(turn, part, session)

    def _allow(self, request_id: str) -> None:
        try:
            self.server.request("POST", f"/permission/{request_id}/reply", {"reply": "always"})
        except Exception as error:  # noqa: BLE001
            log(f"permission {request_id}: {error}")

    def _reject_question(self, request_id: str) -> None:
        try:
            self.server.request("POST", f"/question/{request_id}/reject")
        except Exception as error:  # noqa: BLE001
            log(f"question {request_id}: {error}")

    @staticmethod
    def _arguments(value) -> dict:
        arguments = value if isinstance(value, dict) else {}
        return {ARGUMENT_NAMES.get(key, key): item for key, item in arguments.items()}

    def translate(self, turn: Turn, part: dict, session: str, *, child: bool = False) -> list[dict]:
        """The common item events of one OpenCode part (an assistant's text, reasoning or tool call)."""

        if self.roles.get(str(part.get("messageID"))) != "assistant":
            return []
        params = {"threadId": session if child else self.session_id, "turnId": turn.id}
        kind = part.get("type")
        if kind in {"text", "reasoning"}:
            key = f"{session}:{part.get('id')}"
            if part.get("synthetic") or not (part.get("time") or {}).get("end") or key in turn.items_completed:
                return []
            turn.items_completed.add(key)
            text = str(part.get("text") or "")
            if not text.strip():
                return []
            item = ({"id": part["id"], "type": "agentMessage", "text": text} if kind == "text"
                    else {"id": part["id"], "type": "reasoning", "content": [text]})
            return [{"method": "item/completed", "params": {**params, "item": item}}]
        if kind != "tool":
            return []
        state = part.get("state") or {}
        status = str(state.get("status") or "")
        call = str(part.get("callID") or part.get("id"))
        name = str(part.get("tool") or "")
        name = name[len(TOOL_PREFIX):] if name.startswith(TOOL_PREFIX) else name
        item = {"id": call, "type": "dynamicToolCall", "tool": name, "arguments": self._arguments(state.get("input"))}
        events = []
        if status in {"running", "completed", "error"} and call not in turn.items_started:
            turn.items_started.add(call)
            events.append({"method": "item/started", "params": {**params, "item": dict(item)}})
        if status in {"completed", "error"} and call not in turn.items_completed:
            turn.items_completed.add(call)
            failed = status == "error"
            output = state.get("error") if failed else state.get("output")
            events.append({"method": "item/completed", "params": {**params, "item": {
                **item, "status": "failed" if failed else "completed", "success": not failed,
                "output": [{"type": "text", "text": str(output or "")}]}}})
        return events

    def _child_part(self, turn: Turn, part: dict, session: str) -> None:
        """A subagent's items, under the task call that started it."""

        call = turn.children.get(session)
        if call is None:
            call = self._task_call(session)
            if call is None:
                turn.waiting_children.setdefault(session, []).append(part)
                return
            turn.children[session] = call
            for waiting in turn.waiting_children.pop(session, []):
                self._child_items(turn, waiting, session, call)
        self._child_items(turn, part, session, call)

    def _task_call(self, child_session: str) -> str | None:
        for call_id, part in self.tool_parts.items():
            if part.get("tool") != SUBAGENT_TOOL or self.message_sessions.get(str(part.get("messageID"))) != self.opencode_session:
                continue
            metadata = (part.get("state") or {}).get("metadata") or {}
            if metadata.get("sessionId") == child_session or metadata.get("sessionID") == child_session:
                return call_id
        return None

    def _child_items(self, turn: Turn, part: dict, session: str, call: str) -> None:
        events = self.translate(turn, part, session, child=True)
        key = f"{session}:{part.get('id')}"
        if part.get("type") == "step-finish" and key not in turn.items_completed:
            # The subagent's running total of tokens, one request (OpenCode step) at a time.
            turn.items_completed.add(key)
            turn.child_usage[session] = add_usage(turn.child_usage.get(session, {}), canonical_usage(part.get("tokens") or {}))
            events = [{"method": "usage/updated", "params": {"usage": dict(turn.child_usage[session])}}]
        for item in events:
            model = turn.models.get(session, "")
            self.notify(turn, {"method": "subagent/event", "params": {
                "traceKey": session, "agentId": session, "parentToolUseId": call, "ancestorTraceKeys": [],
                **({"model": model} if model else {}), "event": item}})

    # -- operations --------------------------------------------------------

    def attach(self, turn: Turn, emit, owner) -> None:
        if turn.detach_timer is not None:
            turn.detach_timer.cancel()
            turn.detach_timer = None
        turn.emit, turn.owner = emit, owner
        for event in turn.events[turn.sent:]:
            emit({"type": "event", "event": event})
        turn.sent = len(turn.events)
        for entry in self.pending.values():
            emit({"type": "tool_call", **entry["call"]})

    def detach(self, turn: Turn) -> None:
        turn.emit = turn.owner = None
        if turn.detach_timer is not None:
            turn.detach_timer.cancel()

        def expire() -> None:
            with self.lock:
                if turn.emit is None and self.current is turn:
                    turn.detach_timer = None
                    for entry in list(self.pending.values()):
                        entry["result"] = {"isError": True, "content": [{
                            "type": "text", "text": "No Portal Worker took the tool call; outcome unknown"}]}
                        entry["done"].set()
                    self.pending.clear()

        turn.detach_timer = threading.Timer(DETACHED_TOOL_WAIT_SECONDS, expire)
        turn.detach_timer.daemon = True
        turn.detach_timer.start()

    def check(self, params: dict) -> None:
        if params.get("session_id") and params["session_id"] != self.session_id:
            raise BridgeError("Session identity mismatch")
        if params.get("cwd") != self.config["cwd"] or params.get("provider_profile") != self.config["route"]:
            raise BridgeError("Session route/cwd changes require migration")
        if fingerprint(params.get("resident_environment") or {}) != self.config["identity_fingerprint"]:
            raise BridgeError("Resident identity mismatch")

    def model(self, params: dict) -> dict:
        return {"providerID": self.server.service, "modelID": str(params.get("model") or "")}

    def run(self, params: dict, emit, owner) -> dict:
        self.check(params)
        turn_id = str(params.get("turn_id") or "")
        if not turn_id:
            raise BridgeError("Stable turn ID is required")
        with self.start_lock:
            with self.lock:
                turn = self.current
                if turn is not None:
                    if turn.id != turn_id:
                        raise BridgeError("Session has another active operation")
                    # The next Worker of a running turn attaches to it under its turn ID.
                    self.attach(turn, emit, owner)
            if turn is None:
                record = self.state["turns"].get(turn_id)
                if record and record.get("status") == "completed" and record.get("result"):
                    return self._replay(turn_id, record["result"], emit)
                turn = self._start(params, emit, owner, record)
                starter = True
            else:
                starter = False
        if starter:
            try:
                turn.result = self._outcome(turn)
            except Exception as error:  # noqa: BLE001 - every Worker of the turn gets it
                turn.failure = str(error)
            finally:
                with self.lock:
                    if turn.detach_timer is not None:
                        turn.detach_timer.cancel()
                    self.current = None
                    self.last_used = time.monotonic()
                turn.finished.set()
        turn.finished.wait()
        if turn.failure:
            raise BridgeError(turn.failure)
        return turn.result or {}

    def _replay(self, turn_id: str, result: dict, emit) -> dict:
        """The turn completed while no Worker followed it: its answer and outcome."""

        def event(method: str, **params) -> None:
            emit({"type": "event", "event": {"method": method, "params": {"threadId": self.session_id, **params}}})

        event("turn/started", turn={"id": turn_id})
        if result.get("response"):
            event("item/completed", turnId=turn_id,
                  item={"id": f"{turn_id}:answer", "type": "agentMessage", "text": result["response"]})
        event("turn/completed", turn={"id": turn_id, "status": "completed"})
        return result

    def _start(self, params: dict, emit, owner, record: dict | None) -> Turn:
        text, recovered = str(params.get("prompt") or ""), None
        if record:
            # OpenCode has this turn's input already: continue, never send it twice.
            text = str(params.get("continue_prompt") or "continue")
            recovered = {"delivery": "continued"}
        elif params.get("continue_prompt"):
            recovered = {"delivery": "first_delivery"}
        session = self.ensure_session()
        self.ensure_tools(list(params.get("tools") or []))
        body = {"model": self.model(params), "parts": [{"type": "text", "text": text}],
                "tools": {SUBAGENT_TOOL: params.get("allow_subagents") is not False}}
        if params.get("system_prompt_append"):
            body["system"] = str(params["system_prompt_append"])
        if params.get("effort"):
            body["variant"] = str(params["effort"])
        turn = Turn(str(params["turn_id"]), emit, owner)
        turn.settings = {key: value for key, value in body.items() if key != "parts"}
        with self.lock:
            if self.compacting:
                raise BridgeError("Session is compacting")
            # What the bridge learned of earlier turns' messages is not needed again.
            for seen in (self.roles, self.message_sessions, self.tool_parts, self.part_types, self.matched_calls):
                seen.clear()
            self.current = turn
            if owner is not None and owner.closed:
                self.detach(turn)
        try:
            self.notify(turn, {"method": "turn/started", "params": {"threadId": self.session_id, "turn": {"id": turn.id}}})
            if recovered:
                self.notify(turn, {"method": "turn/recovered", "params": {
                    "threadId": self.session_id, "turnId": turn.id, **recovered, "prompt": text}})
            self.state["turns"][turn.id] = {"status": "submitted", "started_ms": turn.started_ms}
            self.save()
            turn.posted = True
            try:
                self.server.request("POST", f"/session/{session}/prompt_async", body)
            except Exception:
                # OpenCode did not take the input: the next attempt sends it.
                if not record:
                    self.state["turns"].pop(turn.id, None)
                    self.save()
                raise
        except Exception:
            with self.lock:
                self.current = None
            raise
        return turn

    def _settled(self, turn: Turn) -> bool:
        """Whether OpenCode finished the turn's run (also when its idle event was missed)."""

        if turn.done.wait(STATUS_POLL_SECONDS):
            return True
        try:
            status = self.server.request("GET", "/session/status") or {}
        except Exception as error:  # noqa: BLE001 - the event stream decides
            log(f"session status: {error}")
            return False
        state = str((status.get(self.opencode_session) or {}).get("type") or "idle")
        if state == "idle" and (turn.busy or now_ms() - turn.started_ms > 15000):
            turn.done.set()
            return True
        return False

    def _outcome(self, turn: Turn) -> dict:
        while not self._settled(turn):
            pass
        messages = self.server.request("GET", f"/session/{self.opencode_session}/message", timeout=360) or []
        mine = [message for message in messages
                if int(((message.get("info") or {}).get("time") or {}).get("created") or 0) >= turn.started_ms - 1000]
        answers = [message for message in mine if (message.get("info") or {}).get("role") == "assistant"]
        last = answers[-1] if answers else {}
        info = last.get("info") or {}
        error = turn.error or info.get("error")
        completed = bool(answers) and not error and info.get("finish") not in {None, "tool-calls", "error"}
        response = "\n".join(str(part.get("text") or "") for part in last.get("parts") or []
                             if part.get("type") == "text" and not part.get("synthetic"))
        usage, context = {}, {}
        for step in steps(answers, 0):
            usage, context = add_usage(usage, step["usage"]), step["usage"]
        for step in self.subagent_steps(answers, turn.started_ms - 1000):
            usage = add_usage(usage, step["usage"])
        model = str(info.get("modelID") or "")
        result = {"session_id": self.session_id, "turn_id": turn.id, "response": response,
                  "usage": usage, "context": context, "model": model, "context_window": self.window(model)}
        self.notify(turn, {"method": "turn/completed", "params": {
            "threadId": self.session_id, "turn": {"id": turn.id, "status": "completed" if completed else "failed"}}})
        if not completed:
            self.state["turns"][turn.id]["status"] = "failed"
            self.save()
            detail = describe_error(error) if error else "no answer"
            raise BridgeError(f"OpenCode operation {turn.id} did not complete: {detail}. It will not be resubmitted automatically.")
        self.state["turns"][turn.id] = {"status": "completed", "started_ms": turn.started_ms, "result": result}
        # Keep the outcome of recent turns only.
        for old in sorted(self.state["turns"], key=lambda key: self.state["turns"][key].get("started_ms") or 0)[:-50]:
            self.state["turns"].pop(old, None)
        self.save()
        return result

    def window(self, model: str) -> int:
        if not model:
            return 0
        try:
            providers = self.server.request("GET", "/config/providers") or {}
            for provider in providers.get("providers") or []:
                if provider.get("id") == self.server.service:
                    return int((((provider.get("models") or {}).get(model) or {}).get("limit") or {}).get("context") or 0)
        except Exception as error:  # noqa: BLE001 - the window is optional
            log(f"context window of {model}: {error}")
            return 0
        return 0

    def steer(self, params: dict) -> dict:
        with self.lock:
            turn = self.current
            active = turn is not None and turn.id == params.get("turn_id") and (
                not params.get("session_id") or params["session_id"] == self.session_id)
            if not active:
                return {"steered": False, "reason": "no_active_turn"}
            if turn.done.is_set():
                return {"steered": False, "reason": "turn_closing"}
            text = str(params.get("text") or "")
            if not text.strip():
                raise BridgeError("Steering needs text")
            key = fingerprint([turn.id, params.get("control_id") or text])
            if key in turn.steered:
                return {"steered": True, "duplicate": True}
            turn.steered.add(key)
            settings = dict(turn.settings)
        # OpenCode takes a message sent during a run at the run's next step.
        self.server.request("POST", f"/session/{self.opencode_session}/prompt_async", {
            **settings, "parts": [{"type": "text", "text": text}]})
        return {"steered": True}

    def interrupt(self, params: dict) -> dict:
        with self.lock:
            turn = self.current
            matches = turn is not None and turn.id == params.get("turn_id") and (
                not params.get("session_id") or params["session_id"] == self.session_id)
        if matches:
            self.server.request("POST", f"/session/{self.opencode_session}/abort")
        return {"interrupted": bool(matches)}

    def stop_subagent(self, params: dict) -> dict:
        """One subagent (a ``task`` call) stops while the turn goes on.

        Its child session is aborted: OpenCode's task call fails with the
        abort and the model goes on.  The Worker hears ``subagent/stopping``
        first, so the call's card ends stopped.
        """

        call = str(params.get("tool_use_id") or "")
        with self.lock:
            turn = self.current
            active = turn is not None and not turn.done.is_set() and (
                not params.get("session_id") or params["session_id"] == self.session_id)
            if not active:
                return {"stopped": False, "reason": "no_active_turn"}
            part = self.tool_parts.get(call) or {}
            if part.get("tool") != SUBAGENT_TOOL or self.message_sessions.get(str(part.get("messageID"))) != self.opencode_session:
                return {"stopped": False, "reason": "unknown_subagent"}
            if (part.get("state") or {}).get("status") in {"completed", "error"}:
                return {"stopped": False, "reason": "subagent_finished"}
            child = task_session(part)
            if not child:
                turn.stopping.add(call)
            self.notify(turn, {"method": STOPPING_METHOD, "params": {
                "threadId": self.session_id, "turnId": turn.id, "toolUseId": call}})
        if child:
            self._abort_child(child)
        return {"stopped": True}

    def _abort_child(self, child: str) -> None:
        try:
            self.server.request("POST", f"/session/{child}/abort")
        except Exception as error:  # noqa: BLE001
            log(f"subagent session {child} abort: {error}")

    def compact(self, params: dict) -> dict:
        self.check(params)
        with self.lock:
            if self.current is not None or self.compacting:
                return {"compacted": False, "reason": "busy"}
            if not self.opencode_session:
                return {"compacted": False, "reason": "no_session"}
            self.compacting = True
        try:
            before = self.server.request("GET", f"/session/{self.opencode_session}/message", timeout=360) or []
            if len([m for m in before if (m.get("info") or {}).get("role") == "user"]) < 2:
                return {"compacted": False, "reason": "nothing_to_compact", "session_id": self.session_id}
            pre = context_tokens(before)
            self.server.request("POST", f"/session/{self.opencode_session}/summarize",
                                self.model(params), timeout=1770)
            after = self.server.request("GET", f"/session/{self.opencode_session}/message", timeout=360) or []
            return {"compacted": True, "session_id": self.session_id, "pre_tokens": pre,
                    "context_tokens": summary_tokens(after)}
        finally:
            with self.lock:
                self.compacting = False
                self.last_used = time.monotonic()

    def requests(self, params: dict) -> dict:
        """Every model request of the session since ``since_ms``: one per OpenCode step."""

        since = int(params.get("since_ms") or 0)
        if not self.opencode_session:
            return {"requests": []}
        messages = self.server.request("GET", f"/session/{self.opencode_session}/message", timeout=360) or []
        rows = steps(messages, since)
        return {"requests": rows + self.subagent_steps(messages, since),
                "context_window": self.window(rows[-1]["model"]) if rows else 0}

    def subagent_steps(self, messages: list, since: int) -> list[dict]:
        """The requests of the subagents the messages' task calls started, and of their subagents.

        A subagent runs in a child session: its requests are the turn's usage,
        not the turn's context.
        """

        rows, pending, seen = [], task_sessions(messages), set()
        while pending:
            child = pending.pop()
            if child in seen:
                continue
            seen.add(child)
            found = self.server.request("GET", f"/session/{child}/message", timeout=360) or []
            rows.extend({**row, "context": False} for row in steps(found, since))
            pending.extend(task_sessions(found))
        return rows

    def compaction(self) -> dict:
        """The newest compaction of the session: OpenCode's summary and where the history it keeps starts."""

        if not self.opencode_session:
            return {}
        messages = self.server.request("GET", f"/session/{self.opencode_session}/message", timeout=360) or []
        for index in range(len(messages) - 1, -1, -1):
            info = messages[index].get("info") or {}
            if info.get("role") != "assistant" or not info.get("summary"):
                continue
            text = "\n".join(str(part.get("text") or "") for part in messages[index].get("parts") or []
                             if part.get("type") == "text")
            if not text.strip():
                return {}
            created = int((info.get("time") or {}).get("created") or 0)
            later = [int(((message.get("info") or {}).get("time") or {}).get("created") or 0) for message in messages[index + 1:]]
            return {"id": info.get("id"), "summary": text, "created_ms": created,
                    "retain_from_ms": next((moment for moment in later if moment), created)}
        return {}

    def close(self) -> dict:
        with self.lock:
            if self.current is not None or self.compacting:
                raise BridgeError("Cannot release an active session")
        threading.Timer(0.1, self.shutdown).start()
        return {"closed": True}

    def shutdown(self) -> None:
        self.server.stop()
        if getattr(self, "_listener", None) is not None:
            self._listener.close()
        os._exit(0)

    # -- the native agent socket ------------------------------------------

    def handle(self, request: dict, send, peer: Peer) -> dict:
        method, params = request.get("method"), request.get("params") or {}
        self.last_used = time.monotonic()
        if method == "health":
            return {"ready": True, "protocol": PROTOCOL, "version": self.version,
                    "capabilities": {"turn_steer": True, "session_compact": True, "turn_reattach": True,
                                     "turn_continue": True}}
        if method == "turn/run":
            return self.run(params, send, peer)
        if method == "turn/steer":
            return self.steer(params)
        if method == "turn/interrupt":
            return self.interrupt(params)
        if method == "subagent/stop":
            return self.stop_subagent(params)
        if method == "session/compact":
            return self.compact(params)
        if method == "usage/read":
            return self.requests(params)
        if method == "compaction/read":
            return self.compaction()
        if method == "session/close":
            return self.close()
        raise BridgeError(f"Unsupported native operation: {method}")

    def serve(self) -> None:
        path = self.config["socket_path"]
        listener = self._listener = LocalListener(path, backlog=16, replace=True)
        threading.Thread(target=self._idle_exit, daemon=True).start()
        while True:
            connection, _ = listener.accept()
            threading.Thread(target=self._connection, args=(connection,), daemon=True).start()

    def _idle_exit(self) -> None:
        while True:
            time.sleep(15)
            with self.lock:
                idle = self.current is None and not self.compacting and not self.start_lock.locked()
            if idle and time.monotonic() - self.last_used > IDLE_EXIT_SECONDS:
                self.shutdown()

    def _connection(self, connection: socket.socket) -> None:
        peer = Peer(connection)

        def answer(request: dict) -> None:
            send = peer.sender(request.get("id"))
            try:
                result = self.handle(request, send, peer)
                send({"type": "result", "result": result})
            except Exception as error:  # noqa: BLE001 - the Worker gets the message
                send({"type": "error", "error": {"message": str(error)}})

        buffer = b""
        try:
            while True:
                chunk = connection.recv(1 << 16)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 8 * 1024 * 1024:
                    break
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    message = json.loads(line)
                    if message.get("type") == "tool_result":
                        self.tool_result(message.get("call_id"), message.get("result"))
                        continue
                    threading.Thread(target=answer, args=(message,), daemon=True).start()
        except (OSError, ValueError):
            pass
        finally:
            peer.closed = True
            with self.lock:
                # A Worker that leaves (a handover) leaves the turn running.
                if self.current is not None and self.current.owner is peer:
                    self.detach(self.current)
            connection.close()


def canonical_usage(tokens: dict) -> dict:
    """One OpenCode step's tokens in Portal's usage keys; input counts the cached prefix."""

    cache = tokens.get("cache") or {}
    cached = int(cache.get("read") or 0)
    written = int(cache.get("write") or 0)
    output = int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0)
    usage = {"input_tokens": int(tokens.get("input") or 0) + cached + written, "cached_input_tokens": cached,
             "output_tokens": output, "reasoning_output_tokens": int(tokens.get("reasoning") or 0)}
    usage["total_tokens"] = usage["input_tokens"] + output
    return {key: value for key, value in usage.items() if value > 0}


def add_usage(total: dict, more: dict) -> dict:
    return {key: total.get(key, 0) + more.get(key, 0) for key in {*total, *more}}


def steps(messages: list, since: int) -> list[dict]:
    """The model requests (OpenCode steps) of the assistant messages created since ``since`` (ms)."""

    rows = []
    for message in messages:
        info = message.get("info") or {}
        if info.get("role") != "assistant" or int((info.get("time") or {}).get("created") or 0) < since:
            continue
        for part in message.get("parts") or []:
            if part.get("type") == "step-finish":
                rows.append({"id": part.get("id"), "at_ms": int((info.get("time") or {}).get("created") or 0),
                             "model": str(info.get("modelID") or ""),
                             "usage": canonical_usage(part.get("tokens") or {}),
                             # A compaction's summary request is not in the context that follows.
                             "context": not info.get("summary")})
    return rows


def task_session(part: dict) -> str:
    """The child session a ``task`` call runs its subagent in; empty before it starts."""

    metadata = (part.get("state") or {}).get("metadata") or {}
    return str(metadata.get("sessionId") or metadata.get("sessionID") or "") if part.get("tool") == SUBAGENT_TOOL else ""


def task_sessions(messages: list) -> list[str]:
    """The child sessions the messages' task calls ran their subagents in."""

    found = []
    for message in messages:
        for part in message.get("parts") or []:
            if part.get("type") == "tool" and part.get("tool") == SUBAGENT_TOOL:
                metadata = (part.get("state") or {}).get("metadata") or {}
                child = str(metadata.get("sessionId") or metadata.get("sessionID") or "")
                if child:
                    found.append(child)
    return found


def context_tokens(messages: list) -> int:
    for message in reversed(messages):
        for part in reversed(message.get("parts") or []):
            if part.get("type") == "step-finish":
                usage = canonical_usage(part.get("tokens") or {})
                return usage.get("total_tokens", 0)
    return 0


def summary_tokens(messages: list) -> int:
    for message in reversed(messages):
        info = message.get("info") or {}
        if info.get("role") == "assistant" and info.get("summary"):
            return int((info.get("tokens") or {}).get("output") or 0)
    return 0


def describe_error(error: dict) -> str:
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    message = str(data.get("message") or error.get("message") or "").strip().rstrip(".")
    status = f" (HTTP {data['statusCode']})" if data.get("statusCode") else ""
    return f"{error.get('name') or 'error'}: {message}{status}".strip(": ")[:1000]


def main() -> None:
    config = json.loads(Path(sys.argv[1]).read_text())
    bridge = Bridge(config)
    signal.signal(signal.SIGTERM, lambda *_: bridge.shutdown())
    bridge.serve()


if __name__ == "__main__":
    main()
