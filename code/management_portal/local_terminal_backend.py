from __future__ import annotations

import codecs
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from management_common.terminal_runtime import shell_command, spawn_terminal


SESSION_TTL_SECONDS = 6 * 60 * 60
MAX_OUTPUT_CHARS = 200_000
DEFAULT_CWD = Path.home()


@dataclass
class TerminalSession:
    session_id: str
    actor: str
    process: object
    created_at: float
    last_access: float
    cwd: str
    argv: tuple[str, ...] = ()
    credential_resource_id: str = ""
    cols: int = 100
    rows: int = 30
    output: str = ""
    output_start: int = 0
    decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")(errors="replace"),
        repr=False,
    )
    initial_password: str = field(default="", repr=False)
    password_sent: bool = field(default=False, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock)


_LOCK = threading.RLock()
_SESSIONS: dict[str, TerminalSession] = {}


def _cleanup_locked() -> None:
    now = time.time()
    stale = [
        sid
        for sid, session in _SESSIONS.items()
        if now - session.last_access > SESSION_TTL_SECONDS
    ]
    for sid in stale:
        session = _SESSIONS.pop(sid, None)
        if session:
            _close_session(session)


def _close_session(session: TerminalSession) -> None:
    with session.lock:
        session.initial_password = ""
        session.process.close()


def _safe_size(cols: object = None, rows: object = None) -> tuple[int, int]:
    try:
        clean_cols = int(cols)
    except (TypeError, ValueError, OverflowError):
        clean_cols = 100
    try:
        clean_rows = int(rows)
    except (TypeError, ValueError, OverflowError):
        clean_rows = 30
    return max(20, min(240, clean_cols)), max(6, min(80, clean_rows))


def _terminal_cwd(value: object = None) -> Path:
    raw = str(value or "").strip()
    path = Path(raw).expanduser() if raw else DEFAULT_CWD
    if not path.is_absolute():
        raise ValueError("terminal cwd must be an absolute path")
    resolved = path.resolve(strict=False)
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError("terminal cwd does not exist or is not a directory")
    return resolved


def _spawn_process(
    actor: str,
    argv: list[str],
    cols: object = None,
    rows: object = None,
    cwd: object = None,
    *,
    initial_password: str = "",
    credential_resource_id: str = "",
) -> TerminalSession:
    clean_cols, clean_rows = _safe_size(cols, rows)
    shell_cwd = _terminal_cwd(cwd)
    process = spawn_terminal(argv, cwd=shell_cwd, cols=clean_cols, rows=clean_rows)
    now = time.time()
    return TerminalSession(
        session_id=f"term_{uuid.uuid4().hex[:16]}",
        actor=str(actor or "anonymous"),
        process=process,
        created_at=now,
        last_access=now,
        cwd=str(shell_cwd),
        argv=tuple(argv),
        credential_resource_id=str(credential_resource_id or ""),
        cols=clean_cols,
        rows=clean_rows,
        initial_password=str(initial_password or ""),
    )


def _spawn_shell(actor: str, cols: object = None, rows: object = None, cwd: object = None) -> TerminalSession:
    return _spawn_process(
        actor,
        shell_command(),
        cols,
        rows,
        cwd,
    )


def _drain(session: TerminalSession) -> None:
    chunk = session.process.read()
    if chunk:
        decoded = session.decoder.decode(chunk)
        session.output += decoded
        if (
            session.initial_password
            and not session.password_sent
            and "password:" in session.output[-256:].casefold()
        ):
            session.process.write(f"{session.initial_password}\n".encode("utf-8"))
            session.password_sent = True
            session.initial_password = ""
        if len(session.output) > MAX_OUTPUT_CHARS:
            session.output_start += len(session.output) - MAX_OUTPUT_CHARS
            session.output = session.output[-MAX_OUTPUT_CHARS:]


def _public_state(session: TerminalSession, cursor: object = 0) -> dict:
    with session.lock:
        _drain(session)
        session.last_access = time.time()
        try:
            start = int(cursor or 0)
        except (TypeError, ValueError, OverflowError):
            start = 0
        next_cursor = session.output_start + len(session.output)
        if start < session.output_start or start > next_cursor:
            start = session.output_start
        chunk = session.output[start - session.output_start:]
        return {
            "session_id": session.session_id,
            "actor": session.actor,
            "pid": session.process.pid,
            "alive": session.process.poll() is None,
            "created_at": session.created_at,
            "last_access": session.last_access,
            "chunk": chunk,
            "cursor": next_cursor,
            "cwd": session.cwd,
            "cols": session.cols,
            "rows": session.rows,
        }


def start_terminal(
    actor: str,
    session_id: str = "",
    cols: object = None,
    rows: object = None,
    cwd: object = None,
) -> dict:
    actor_id = str(actor or "anonymous")
    with _LOCK:
        _cleanup_locked()
        existing = _SESSIONS.get(str(session_id or ""))
        if existing and existing.actor == actor_id and existing.process.poll() is None:
            return _public_state(existing)
        session = _spawn_shell(actor_id, cols, rows, cwd)
        _SESSIONS[session.session_id] = session
        return _public_state(session)


def start_device_terminal(
    actor: str,
    device_id: str,
    session_id: str = "",
    cols: object = None,
    rows: object = None,
) -> dict:
    from management_portal import device_identity_backend

    actor_id = str(actor or "anonymous")
    ssh = device_identity_backend.effective_ssh_access(
        device_id,
    )
    via_resource_id = str(ssh["via_resource_id"])
    password = device_identity_backend.read_device_ssh_password(via_resource_id)
    command = ["ssh"]
    config_path = str(ssh.get("config_path") or "").strip()
    if config_path:
        command.extend(["-F", str(Path(config_path).expanduser())])
    command.extend(
        [
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
        ]
    )
    ssh_port = ssh.get("port")
    if ssh_port:
        command.extend(["-p", str(ssh_port)])
    identity_file = str(ssh.get("identity_file") or "").strip()
    if identity_file:
        command.extend(["-i", str(Path(identity_file).expanduser())])
    ssh_host = str(ssh.get("host") or "").strip()
    ssh_user = str(ssh.get("user") or "").strip()
    target = f"{ssh_user}@{ssh_host}" if ssh_user else ssh_host
    command.extend(["--", target])
    with _LOCK:
        _cleanup_locked()
        existing = _SESSIONS.get(str(session_id or ""))
        if existing and existing.actor == actor_id and existing.process.poll() is None:
            return _public_state(existing)
        session = _spawn_process(
            actor_id,
            command,
            cols,
            rows,
            DEFAULT_CWD,
            initial_password=password,
            credential_resource_id=via_resource_id,
        )
        _SESSIONS[session.session_id] = session
        result = _public_state(session)
        result["resource_id"] = str(device_id)
        result["via_resource_id"] = via_resource_id
        return result


def require_open_terminal(actor: str, arguments: dict | None = None) -> None:
    """Opening a shell is the OpenTerminal instruction; every route that starts one asks it first."""
    from management_portal import permission_system

    if not permission_system.allows(actor, "OpenTerminal", arguments or {}):
        raise PermissionError("无权打开 Terminal：需要能执行 OpenTerminal（默认只开放给 Portal 管理员）")


def open_terminal(
    actor: str,
    *,
    device_id: str = "",
    session_id: str = "",
    cols: object = None,
    rows: object = None,
    cwd: object = None,
) -> dict:
    """A host shell, or an SSH terminal on ``device_id``, for an actor that may run OpenTerminal."""
    require_open_terminal(actor, {"device_id": device_id} if device_id else {})
    if device_id:
        return start_device_terminal(actor, device_id, session_id, cols, rows)
    return start_terminal(actor, session_id, cols, rows, cwd)


def read_terminal(actor: str, session_id: str, cursor: object = 0) -> dict:
    actor_id = str(actor or "anonymous")
    with _LOCK:
        _cleanup_locked()
        session = _SESSIONS.get(str(session_id or ""))
        if not session or session.actor != actor_id:
            raise KeyError("terminal session not found")
        result = _public_state(session, cursor)
        if not result["alive"]:
            _SESSIONS.pop(session.session_id, None)
            _close_session(session)
        return result


def write_terminal(actor: str, session_id: str, text: str) -> dict:
    actor_id = str(actor or "anonymous")
    with _LOCK:
        session = _SESSIONS.get(str(session_id or ""))
        if not session or session.actor != actor_id:
            raise KeyError("terminal session not found")
    clean_text = str(text or "")
    if len(clean_text) > 8192:
        raise ValueError("terminal input exceeds 8192 characters")
    data = clean_text.encode("utf-8", errors="replace")
    with session.lock:
        if session.process.poll() is not None:
            raise RuntimeError("terminal session is not running")
        session.process.write(data)
    return _public_state(session)


def resize_terminal(actor: str, session_id: str, cols: object = None, rows: object = None) -> dict:
    actor_id = str(actor or "anonymous")
    with _LOCK:
        session = _SESSIONS.get(str(session_id or ""))
        if not session or session.actor != actor_id:
            raise KeyError("terminal session not found")
    clean_cols, clean_rows = _safe_size(cols, rows)
    with session.lock:
        session.process.resize(clean_cols, clean_rows)
        session.cols = clean_cols
        session.rows = clean_rows
    return _public_state(session)


def control_terminal(
    actor: str,
    session_id: str,
    action: str,
    cols: object = None,
    rows: object = None,
    cursor: object = 0,
    cwd: object = None,
) -> dict:
    actor_id = str(actor or "anonymous")
    action_id = str(action or "").strip().lower()
    if action_id == "restart":
        # A new shell is a new terminal.
        require_open_terminal(actor_id)
    with _LOCK:
        session = _SESSIONS.get(str(session_id or ""))
        if not session or session.actor != actor_id:
            if action_id == "restart":
                return start_terminal(actor_id, cols=cols, rows=rows, cwd=cwd)
            raise KeyError("terminal session not found")
        if action_id == "resize":
            return resize_terminal(actor_id, session.session_id, cols, rows)
        if action_id == "interrupt":
            if session.process.poll() is None:
                with session.lock:
                    session.process.interrupt()
            return _public_state(session, cursor)
        if action_id == "close":
            _SESSIONS.pop(session.session_id, None)
            _close_session(session)
            return {"session_id": "", "alive": False, "output": ""}
        if action_id == "restart":
            _SESSIONS.pop(session.session_id, None)
            _close_session(session)
            password = ""
            if session.credential_resource_id:
                from management_portal import device_identity_backend

                password = device_identity_backend.read_device_ssh_password(
                    session.credential_resource_id
                )
            new_session = _spawn_process(
                actor_id,
                list(session.argv) or shell_command(),
                cols,
                rows,
                cwd or session.cwd,
                initial_password=password,
                credential_resource_id=session.credential_resource_id,
            )
            _SESSIONS[new_session.session_id] = new_session
            return _public_state(new_session)
    raise ValueError("unknown terminal action")
