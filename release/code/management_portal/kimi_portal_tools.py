#!/usr/bin/env python3
"""Offer Portal-executed tools to Kimi Code.

Kimi Code runs this program in two roles.

``serve`` is the stdio MCP server named ``portal`` in Kimi Code's user-level
``mcp.json``.  Kimi Code starts one per workspace and shares it between the
sessions there; it lists the tools only when it connects, and a call does not
say which session made it.  So the server lists one catalog for everybody:
every tool an Execution worker has ever offered.  A worker that offers a tool
the catalog lacks adds it and renames the catalog in ``mcp.json``, which makes
Kimi Code connect again; each turn then switches off, for its own session,
the tools it does not have.

``announce`` is a ``PreToolUse`` hook.  Kimi Code runs it before every call
of a Portal tool and tells it the session and the call ID.  The hook leaves
that as a note under the call's name and arguments; the server picks the
note up when the call arrives.  Two sessions that make the same call are
let through one after the other, so a note always belongs to the call that
picks it up.

The program holds no tool itself.  The worker that runs a Kimi turn listens,
while the turn runs, on a Unix socket named after its session, and the
server passes each call to the worker of the session that made it.  A call
of a session no worker is running a turn for is refused.

Only the standard library is used: Kimi Code runs the program with the
system Python.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import socket
import sys
import threading
import time
import tomllib
import uuid
from pathlib import Path

# Kimi launches this file directly with system Python from its own workspace.
# Resolve Portal's shared modules independently of the caller's cwd/PYTHONPATH.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.local_ipc import connect_local
from management_common.portal_paths import path as portal_path

MCP_SERVER_NAME = "portal"
TOOL_NAME_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
GATEWAY_ROOT = Path(os.environ.get("PORTAL_KIMI_TOOLS_ROOT", str(portal_path("staging_root") / "portal_locks" / "kimi-tools")))
CATALOG_ENVIRONMENT = "PORTAL_KIMI_TOOLS_CATALOG"
CALL_TIMEOUT_SECONDS = 330.0
# A Worker handover leaves the session's prompt running without a Worker for
# a moment; a call made then waits this long for the next Worker's gateway.
GATEWAY_WAIT_SECONDS = 180.0
# How long the server waits for the note of a call that has arrived.
NOTE_WAIT_SECONDS = 3.0
# How long a call waits for an identical call of another session to be picked up.
ANNOUNCE_WAIT_SECONDS = 20.0
# A note nobody picked up belongs to a call that was never made.
NOTE_STALE_SECONDS = 60.0
HOOK_TIMEOUT_SECONDS = 30


def catalog_path() -> Path:
    """Every Portal tool a worker has offered to Kimi Code."""

    return GATEWAY_ROOT / "catalog.json"


def gateway_socket_path(session_id: str) -> Path:
    """Where the worker that runs this session's turn listens."""

    # A short name: a Unix socket path holds about a hundred bytes.
    key = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:24]
    return GATEWAY_ROOT / "turns" / f"{key}.sock"


def _note_path(tool: str, arguments: object) -> Path:
    call = json.dumps([tool, arguments or {}], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return GATEWAY_ROOT / "calls" / hashlib.sha256(call.encode("utf-8")).hexdigest()[:40]


def hook_command() -> str:
    command = [sys.executable, str(Path(__file__).resolve()), "announce"]
    if os.name == "nt":
        import subprocess
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def hook_config() -> str:
    """The hook entry of Kimi Code's ``config.toml`` that announces Portal tool calls."""

    return (
        "\n[[hooks]]\n"
        'event = "PreToolUse"\n'
        f'matcher = "{TOOL_NAME_PREFIX}.*"\n'
        f"command = {json.dumps(hook_command())}\n"
        f"timeout = {HOOK_TIMEOUT_SECONDS}\n"
    )


def service_ready(kimi_home: str | os.PathLike[str]) -> bool:
    """Whether the Kimi Code service was started with Portal's server and hook.

    Without the hook Portal cannot tell which session makes a call, so no
    worker offers tools until the service has been started by a launcher
    that installs both.
    """

    home = Path(kimi_home).expanduser()
    try:
        config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
        hooked = any(
            hook.get("event") == "PreToolUse"
            and hook.get("matcher") == f"{TOOL_NAME_PREFIX}.*"
            and shlex.split(hook.get("command", "")) == shlex.split(hook_command())
            for hook in config.get("hooks", [])
        )
    except (OSError, ValueError):
        return False
    return hooked and MCP_SERVER_NAME in _mcp_servers(home / "mcp.json")


def read_catalog() -> list[dict[str, object]]:
    try:
        tools = json.loads(catalog_path().read_text(encoding="utf-8")).get("tools")
    except (OSError, ValueError, AttributeError):
        return []
    named = {
        str(tool["name"]): tool
        for tool in (tools if isinstance(tools, list) else [])
        if isinstance(tool, dict) and str(tool.get("name") or "")
    }
    return [named[name] for name in sorted(named)]


def catalog_signature() -> str:
    return hashlib.sha256(json.dumps(read_catalog(), sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _mcp_servers(path: Path) -> dict[str, object]:
    try:
        servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers")
    except (OSError, ValueError, AttributeError):
        return {}
    return servers if isinstance(servers, dict) else {}


def _write_mcp_servers(path: Path, servers: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def install(kimi_home: str | os.PathLike[str]) -> bool:
    """Name this program in Kimi Code's user-level ``mcp.json``; whether the file changed.

    The launcher of the Kimi Code service calls this, so the service runs
    the server and the hook of the same source tree.  Other servers in the
    file are kept.
    """

    path = Path(kimi_home).expanduser() / "mcp.json"
    servers = _mcp_servers(path)
    wanted = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(Path(__file__).resolve())],
        "env": {CATALOG_ENVIRONMENT: catalog_signature()},
        # A Portal tool may wait for a streamed instruction result.
        "toolTimeoutMs": int(CALL_TIMEOUT_SECONDS * 1000),
    }
    if servers.get(MCP_SERVER_NAME) == wanted:
        return False
    _write_mcp_servers(path, {**servers, MCP_SERVER_NAME: wanted})
    return True


def name_catalog(kimi_home: str | os.PathLike[str]) -> bool:
    """Name the current catalog in the installed entry; whether the file changed.

    Kimi Code watches ``mcp.json``: when the entry changes, every workspace
    connects again and lists the tools again, without a restart.
    """

    path = Path(kimi_home).expanduser() / "mcp.json"
    servers = _mcp_servers(path)
    entry = servers.get(MCP_SERVER_NAME)
    if not isinstance(entry, dict):
        return False
    signature = catalog_signature()
    raw_environment = entry.get("env")
    environment: dict[str, object] = raw_environment if isinstance(raw_environment, dict) else {}
    if environment.get(CATALOG_ENVIRONMENT) == signature:
        return False
    _write_mcp_servers(path, {
        **servers, MCP_SERVER_NAME: {**entry, "env": {**environment, CATALOG_ENVIRONMENT: signature}},
    })
    return True


def _connect(path: Path, *, wait: float) -> socket.socket:
    """A connection to the session's gateway, waiting ``wait`` seconds for one to listen."""

    deadline = time.monotonic() + wait
    while True:
        try:
            return connect_local(path, timeout=5)
        except (FileNotFoundError, ConnectionRefusedError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def _ask(path: Path, request: dict[str, object], *, timeout: float, wait: float = 0.0) -> dict[str, object]:
    with _connect(path, wait=wait) as client:
        client.settimeout(timeout)
        client.sendall(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
        with client.makefile("rb") as stream:
            line = stream.readline()
    if not line:
        raise OSError("Portal tool gateway closed the connection")
    answer = json.loads(line)
    return answer if isinstance(answer, dict) else {}


def announce(event: dict[str, object]) -> str:
    """Leave the note of one Portal tool call; a reason when the call must not run."""

    name = str(event.get("tool_name") or "")
    if not name.startswith(TOOL_NAME_PREFIX):
        return ""
    path = _note_path(name[len(TOOL_NAME_PREFIX):], event.get("tool_input"))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    draft = path.with_name(f"{path.name}.{uuid.uuid4().hex}.draft")
    draft.write_text(json.dumps({
        "session_id": str(event.get("session_id") or ""),
        "tool_call_id": str(event.get("tool_call_id") or ""),
    }), encoding="utf-8")
    deadline = time.monotonic() + ANNOUNCE_WAIT_SECONDS
    try:
        while True:
            try:
                os.link(draft, path)  # the note appears whole, and only when there is none
            except FileExistsError:
                pass
            else:
                return ""
            try:
                if time.time() - path.stat().st_mtime > NOTE_STALE_SECONDS:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                continue  # picked up meanwhile
            if time.monotonic() >= deadline:
                return "Portal is still starting an identical call of another session; make this call again."
            time.sleep(0.02)
    finally:
        draft.unlink(missing_ok=True)


def _take_note(tool: str, arguments: object) -> dict[str, object]:
    """The note of the call that just arrived, taken once."""

    path = _note_path(tool, arguments)
    taken = path.with_name(f"{path.name}.{uuid.uuid4().hex}.taken")
    deadline = time.monotonic() + NOTE_WAIT_SECONDS
    while True:
        try:
            os.rename(path, taken)
            break
        except OSError:
            if time.monotonic() >= deadline:
                return {}
            time.sleep(0.02)
    try:
        note = json.loads(taken.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        note = {}
    finally:
        taken.unlink(missing_ok=True)
    return note if isinstance(note, dict) else {}


def call_portal_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Run one call with the worker of the session that made it; an MCP tool result."""

    note = _take_note(name, arguments)
    session_id = str(note.get("session_id") or "")
    if not session_id:
        return _error_result(
            "Portal could not tell which session made this call, so it did not run. "
            "(The Kimi Code service must be started by Portal to use Portal tools.)"
        )
    deadline = time.monotonic() + GATEWAY_WAIT_SECONDS
    try:
        while True:
            answer = _ask(gateway_socket_path(session_id), {
                "op": "call", "tool": name, "arguments": arguments, "call_id": str(note.get("tool_call_id") or ""),
            }, timeout=CALL_TIMEOUT_SECONDS, wait=max(0.0, deadline - time.monotonic()))
            # A retiring Worker did not run the call; the next Worker will.
            if not answer.get("retry") or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
    except (OSError, ValueError):
        return _error_result(
            "Portal tools work only inside a turn that Portal runs for this session; "
            "no Portal turn of this session is running."
        )
    result = answer.get("result")
    if isinstance(result, dict):
        return result
    return _error_result(str(answer.get("error") or "Portal tool call failed"))


def _error_result(text: str) -> dict[str, object]:
    return {"isError": True, "content": [{"type": "text", "text": text}]}


def serve() -> None:
    lock = threading.Lock()

    def send(message: dict[str, object]) -> None:
        with lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def call_tool(request_id: object, params: dict[str, object]) -> None:
        arguments = params.get("arguments")
        try:
            result = call_portal_tool(str(params.get("name") or ""), arguments if isinstance(arguments, dict) else {})
        except Exception as exc:  # noqa: BLE001 - the model gets an answer for every call
            result = _error_result(f"Portal tool call failed: {type(exc).__name__}")
        send({"jsonrpc": "2.0", "id": request_id, "result": result})

    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        method = str(message.get("method") or "")
        request_id = message.get("id")
        raw_params = message.get("params")
        params: dict[str, object] = raw_params if isinstance(raw_params, dict) else {}
        if request_id is None:
            continue  # a notification needs no answer
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": str(params.get("protocolVersion") or "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": MCP_SERVER_NAME, "version": "1"},
            }})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": read_catalog()}})
        elif method == "tools/call":
            # Calls of one model step run together.
            threading.Thread(target=call_tool, args=(request_id, params), daemon=True).start()
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}})


def main(arguments: list[str]) -> int:
    if arguments[:1] == ["announce"]:
        try:
            event = json.load(sys.stdin)
            reason = announce(event if isinstance(event, dict) else {})
        except Exception as exc:  # noqa: BLE001 - a call Portal cannot attribute must not run
            reason = f"Portal could not register this tool call ({type(exc).__name__})."
        if reason:
            print(json.dumps({"hookSpecificOutput": {
                "permissionDecision": "deny", "permissionDecisionReason": reason,
            }}))
        return 0
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
