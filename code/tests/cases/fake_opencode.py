"""A stand-in for ``opencode serve``: the part of OpenCode's HTTP API the OpenCode bridge uses.

The OpenCode Adapter tests run the real client and bridge against it.  Each
prompt plays a short script chosen by words in its text: ``tool`` calls the
first Portal tool through the bridge's MCP server, ``bash`` and ``edit`` call
OpenCode's own tools, ``subagent`` runs a ``task`` call in a child session,
``watch`` runs one whose child works until its session is aborted,
``slow`` waits for input sent during the run, ``fail`` ends the first such run
with a Provider error.  Every prompt body is appended to the log file the
wrapper names (``argv[1]``); the environment each server starts with, to
``<log>.server``.
"""
from __future__ import annotations

import base64
import copy
import itertools
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = sys.argv[1]
CONFIG = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
MCP = CONFIG["mcp"]["portal"]
AUTH = "Basic " + base64.b64encode(f"opencode:{os.environ['OPENCODE_SERVER_PASSWORD']}".encode()).decode()
LOCK = threading.Condition()
EVENTS: list[dict] = []
SESSIONS: dict[str, dict] = {}
TOOLS: list[dict] = []
FAILED: set[str] = set()
COUNTER = itertools.count(1)
TOKENS = {"total": 175, "input": 100, "output": 20, "reasoning": 5, "cache": {"read": 50, "write": 0}}


def record(entry: dict) -> None:
    with open(LOG, "a") as log:
        log.write(json.dumps(entry) + "\n")


def emit(kind: str, **properties) -> None:
    with LOCK:
        EVENTS.append({"type": kind, "properties": properties})
        LOCK.notify_all()


def ident(prefix: str) -> str:
    return f"{prefix}_{next(COUNTER):06d}"


def mcp(method: str, params: dict | None = None) -> dict:
    body = {"jsonrpc": "2.0", "id": next(COUNTER), "method": method, "params": params or {}}
    request = urllib.request.Request(MCP["url"], data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", **MCP["headers"]})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())["result"]


def connect() -> None:
    mcp("initialize", {"protocolVersion": "2025-03-26"})
    TOOLS[:] = mcp("tools/list")["tools"]


def message(session: str, role: str, **info) -> dict:
    entry = {"info": {"id": ident("msg"), "sessionID": session, "role": role,
                      "time": {"created": int(time.time() * 1000)}, **info}, "parts": []}
    SESSIONS[session]["messages"].append(entry)
    emit("message.updated", sessionID=session, info=copy.deepcopy(entry["info"]))
    return entry


def part(entry: dict, **fields) -> dict:
    piece = {"id": fields.pop("id", None) or ident("prt"), "sessionID": entry["info"]["sessionID"],
             "messageID": entry["info"]["id"], **fields}
    parts = entry["parts"]
    for index, existing in enumerate(parts):
        if existing["id"] == piece["id"]:
            parts[index] = piece
            break
    else:
        parts.append(piece)
    emit("message.part.updated", sessionID=piece["sessionID"], part=copy.deepcopy(piece))
    return piece


def tool(entry: dict, name: str, arguments: dict, run, metadata: dict | None = None) -> None:
    call = ident("call")
    piece = part(entry, type="tool", callID=call, tool=name, state={"status": "pending", "input": {}})
    part(entry, id=piece["id"], type="tool", callID=call, tool=name,
         state={"status": "running", "input": arguments, "metadata": metadata or {}})
    output, failed = run()
    state = {"status": "error", "input": arguments, "error": output} if failed else {
        "status": "completed", "input": arguments, "output": output, "metadata": metadata or {}}
    part(entry, id=piece["id"], type="tool", callID=call, tool=name, state=state)


def step(session: str, model: str, body, finish: str) -> dict:
    entry = message(session, "assistant", modelID=model, providerID="opencode")
    body(entry)
    part(entry, type="step-finish", reason=finish, tokens=TOKENS)
    entry["info"].update(finish=finish, tokens=TOKENS)
    emit("message.updated", sessionID=session, info=copy.deepcopy(entry["info"]))
    return entry


def run(session: str, body: dict) -> None:
    state = SESSIONS[session]
    text = body["parts"][0]["text"]
    model = body.get("model", {}).get("modelID", "")
    message(session, "user")
    emit("session.status", sessionID=session, status={"type": "busy"})
    try:
        if "tool" in text and TOOLS:
            def portal():
                result = mcp("tools/call", {"name": TOOLS[0]["name"], "arguments": {"id": "r1"}})
                return "".join(item.get("text", "") for item in result["content"]), result.get("isError", False)
            step(session, model, lambda entry: tool(entry, "portal_" + TOOLS[0]["name"], {"id": "r1"}, portal),
                 "tool-calls")
        if "bash" in text:
            step(session, model, lambda entry: tool(entry, "bash", {"command": "ls"}, lambda: ("notes.txt\n", False)),
                 "tool-calls")
        if "edit" in text:
            step(session, model, lambda entry: tool(
                entry, "edit", {"filePath": "notes.txt", "oldString": "hello", "newString": "goodbye"},
                lambda: ("Edit applied successfully.", False)), "tool-calls")
        if "subagent" in text:
            child = ident("ses")
            SESSIONS[child] = {"messages": [], "queue": []}

            def delegate():
                emit("session.created", sessionID=child, info={"id": child, "parentID": session})
                inner = step(child, model, lambda entry: tool(entry, "read", {"filePath": "notes.txt"},
                                                              lambda: ("hello", False)), "tool-calls")
                step(child, model, lambda entry: part(entry, type="text", text="child found hello",
                                                      time={"start": 1, "end": 2}), "stop")
                return f"task_id: {inner['info']['sessionID']}\n<task_result>child found hello</task_result>", False
            step(session, model, lambda entry: tool(
                entry, "task", {"description": "look at notes", "prompt": "read notes.txt", "subagent_type": "general"},
                delegate, metadata={"sessionId": child}), "tool-calls")
        if "watch" in text:
            child = ident("ses")
            SESSIONS[child] = {"messages": [], "queue": [], "busy": True, "aborted": False}
            late = []

            def watch():
                emit("session.created", sessionID=child, info={"id": child, "parentID": session})
                entry = message(child, "assistant", modelID=model, providerID="opencode")
                tail = {"callID": ident("call"), "tool": "bash", "type": "tool"}
                running = part(entry, **tail, state={"status": "running", "input": {"command": "tail -f log"}})
                late.append(lambda: part(entry, id=running["id"], **tail, state={
                    "status": "error", "input": {"command": "tail -f log"}, "error": "Tool execution aborted"}))
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not SESSIONS[child]["aborted"]:
                    time.sleep(0.05)
                record({"kind": "child", "session": child, "aborted": SESSIONS[child]["aborted"]})
                # As OpenCode's task tool: the child's aborted answer fails the call.
                return f"Subagent failed (task_id: {child}): The operation was aborted.", True
            step(session, model, lambda entry: tool(
                entry, "task", {"description": "watch the log", "prompt": "tail the log", "subagent_type": "general"},
                watch, metadata={"sessionId": child}), "tool-calls")
            # OpenCode settles the aborted child's running step after the call failed.
            for settle in late:
                settle()
        steered = []
        if "slow" in text:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not state["queue"] and not state["aborted"]:
                time.sleep(0.05)
            for queued in state.pop("queue"):
                record({"kind": "steer", **queued})
                message(session, "user")
                steered.append(queued["parts"][0]["text"])
            state["queue"] = []
        if state["aborted"]:
            raise InterruptedError
        if "fail" in text and session not in FAILED:
            FAILED.add(session)
            error = {"name": "APIError", "data": {"message": "Upstream request failed: Endpoint is unavailable.",
                                                  "statusCode": 429, "isRetryable": True}}
            entry = message(session, "assistant", modelID=model, providerID="opencode", error=error)
            emit("session.error", sessionID=session, error=error)
            return
        answer = f"answer to: {text[:40]}" + "".join(f" +{item}" for item in steered)
        step(session, model, lambda entry: (
            part(entry, type="reasoning", text="thinking", time={"start": 1, "end": 2}),
            part(entry, type="text", text="", time={"start": 1}),
            emit("message.part.delta", sessionID=session, messageID=entry["info"]["id"],
                 partID=entry["parts"][-1]["id"], field="text", delta=answer),
            part(entry, id=entry["parts"][-1]["id"], type="text", text=answer, time={"start": 1, "end": 2}),
        ), "stop")
    except InterruptedError:
        error = {"name": "MessageAbortedError", "data": {"message": "Aborted"}}
        message(session, "assistant", modelID=model, providerID="opencode", error=error)
        emit("session.error", sessionID=session, error=error)
    finally:
        state["busy"] = False
        state["aborted"] = False
        emit("session.status", sessionID=session, status={"type": "idle"})
        emit("session.idle", sessionID=session)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, body) -> None:
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self) -> bool:
        if self.headers.get("authorization") == AUTH:
            return True
        self.send_response(401)
        self.send_header("content-length", "0")
        self.end_headers()
        return False

    def body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def do_GET(self):
        if not self.authorized():
            return
        path = self.path.split("?")[0]
        if path == "/event":
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            with LOCK:
                position = len(EVENTS)
            self.wfile.write(b'data: {"type": "server.connected", "properties": {}}\n\n')
            self.wfile.flush()
            while True:
                with LOCK:
                    LOCK.wait_for(lambda: len(EVENTS) > position, timeout=10)
                    fresh, position = EVENTS[position:], len(EVENTS)
                for event in fresh:
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.flush()
        elif path.startswith("/session/") and path.endswith("/message"):
            self.reply(copy.deepcopy(SESSIONS[path.split("/")[2]]["messages"]))
        elif path == "/session/status":
            self.reply({key: {"type": "busy"} for key, state in SESSIONS.items() if state.get("busy")})
        elif path == "/config/providers":
            self.reply({"providers": [{"id": "opencode", "models": {"free-model": {"limit": {"context": 200000}}}}]})
        else:
            self.send_error(404)

    def do_PUT(self):
        if not self.authorized():
            return
        path, body = self.path.split("?")[0], self.body()
        if path.startswith("/auth/"):
            # A service's credential, before the server serves anything else.
            record({"kind": "auth", "service": path.split("/")[2], "key": body.get("key"), "first": not SESSIONS})
            return self.reply(True)
        self.send_error(404)

    def do_POST(self):
        if not self.authorized():
            return
        path, body = self.path.split("?")[0], self.body()
        segments = path.strip("/").split("/")
        if path == "/session":
            session = ident("ses")
            SESSIONS[session] = {"messages": [], "queue": [], "busy": False, "aborted": False}
            emit("session.created", sessionID=session, info={"id": session})
            return self.reply({"id": session})
        if segments[0] == "mcp":
            if segments[2] == "connect":
                connect()
            return self.reply(True)
        session = segments[1]
        state = SESSIONS[session]
        if segments[2] == "prompt_async":
            if state["busy"]:
                state["queue"].append(body)
            else:
                record({"kind": "prompt", **body})
                state["busy"] = True
                threading.Thread(target=run, args=(session, body), daemon=True).start()
            self.send_response(204)
            self.send_header("content-length", "0")
            return self.end_headers()
        if segments[2] == "abort":
            state["aborted"] = True
            return self.reply(True)
        if segments[2] == "summarize":
            record({"kind": "summarize", **body})
            entry = message(session, "assistant", summary=True, modelID=body["modelID"], tokens={"output": 42})
            part(entry, type="text", text="summary", time={"start": 1, "end": 2})
            return self.reply(True)
        self.send_error(404)


def main() -> None:
    with open(LOG + ".server", "a") as log:
        log.write(json.dumps({key: os.environ.get(key) for key in (
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "OPENCODE_DB", "PORTAL_RESIDENT_ID",
            "PORTAL_DATABASE_URL", "PORTAL_OPENCODE_API_KEY", "OPENROUTER_API_KEY")}) + "\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=connect, daemon=True).start()
    print(f"opencode server listening on http://127.0.0.1:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
