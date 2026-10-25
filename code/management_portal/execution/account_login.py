"""Subscription login sessions; Provider CLIs exclusively own credentials."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from management_common.instance_paths import instance_root
from management_common import portal_network
from management_common.file_lock import try_lock_descriptor
from management_common.local_ipc import LocalListener, connect_local
from management_common.process_runtime import run_process, spawn_process, terminate_process_tree
from management_common.portal_database import portal_database
from management_portal.hatg.contracts import InstructionError, invalid, not_found
from .account_hosting import account_home, ensure_account_home, start_codex_instance, validate_account_id


ACTIVE = ("pending", "awaiting_user")
LOGIN_SECONDS = 900
PROXY_KEYS = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def now():
    return datetime.now(timezone.utc)


def _public(row):
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in dict(row).items()}


class LoginSessionStore:
    def __init__(self, database=None):
        self.database = database or portal_database(schema="execution_accounts")
        self._ready = False
        self._lock = threading.Lock()

    @contextmanager
    def connection(self):
        if not self._ready:
            with self._lock:
                if not self._ready:
                    with self.database.connect() as c:
                        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':login', 0))")
                        c.execute("""CREATE TABLE IF NOT EXISTS login_sessions (
                            account_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                            verification_code TEXT NOT NULL DEFAULT '', verification_url TEXT NOT NULL DEFAULT '',
                            error TEXT NOT NULL DEFAULT '', started_at TIMESTAMPTZ NOT NULL,
                            expires_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""")
                        c.execute("""CREATE TABLE IF NOT EXISTS login_states (
                            account_id TEXT PRIMARY KEY, state TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL)""")
                    self._ready = True
        with self.database.connect() as c:
            yield c

    def get(self, account_id):
        with self.connection() as c:
            c.execute("UPDATE login_sessions SET state='expired', verification_code='', verification_url='', "
                      "error='登录已过期，请重新开始', updated_at=%s "
                      "WHERE account_id=%s AND state IN ('pending','awaiting_user') AND expires_at<=%s",
                      (now(), account_id, now()))
            row = c.execute("SELECT * FROM login_sessions WHERE account_id=%s", (account_id,)).fetchone()
            return _public(row) if row else None

    def begin(self, account_id):
        # Caller holds the host-wide account lock for the complete subprocess lifetime.
        started = now()
        with self.connection() as c:
            row = c.execute("INSERT INTO login_sessions(account_id,state,started_at,expires_at,updated_at) "
                            "VALUES(%s,'pending',%s,%s,%s) ON CONFLICT(account_id) DO UPDATE SET "
                            "state='pending',verification_code='',verification_url='',error='', "
                            "started_at=EXCLUDED.started_at,expires_at=EXCLUDED.expires_at,updated_at=EXCLUDED.updated_at "
                            "RETURNING *", (account_id, started, started + timedelta(seconds=LOGIN_SECONDS), started)).fetchone()
            return _public(row)

    def transition(self, session, state, *, code="", url="", error=""):
        if state not in {*ACTIVE, "succeeded", "failed", "expired"}:
            raise ValueError("Invalid login session state")
        if state not in ACTIVE:
            code = url = ""
        with self.connection() as c:
            return bool(c.execute("UPDATE login_sessions SET state=%s,verification_code=%s,verification_url=%s,error=%s,updated_at=%s "
                                  "WHERE account_id=%s AND started_at=%s AND state IN ('pending','awaiting_user')",
                                  (state, code, url, error, now(), session["account_id"], session["started_at"])).rowcount)

    def login_state(self, account_id, *, default="logged_out"):
        with self.connection() as c:
            row = c.execute("SELECT state FROM login_states WHERE account_id=%s", (account_id,)).fetchone()
            return row["state"] if row else default

    def observe(self, account_id, state, *, observed_at=None):
        if state not in {"logged_in", "login_expired", "logged_out"}:
            raise ValueError("Invalid login state")
        observed_at = observed_at or now()
        with self.connection() as c:
            c.execute("INSERT INTO login_states(account_id,state,observed_at) VALUES(%s,%s,%s) "
                      "ON CONFLICT(account_id) DO UPDATE SET state=EXCLUDED.state,observed_at=EXCLUDED.observed_at "
                      "WHERE login_states.observed_at<=EXCLUDED.observed_at", (account_id, state, observed_at))

    def remove(self, account_id):
        with self.connection() as c:
            c.execute("DELETE FROM login_sessions WHERE account_id=%s", (account_id,))
            c.execute("DELETE FROM login_states WHERE account_id=%s", (account_id,))


@lru_cache(maxsize=8)
def _login_store(dsn, namespace):
    return LoginSessionStore()


def login_store():
    return _login_store(os.environ.get("PORTAL_DATABASE_URL", ""), os.environ.get("PORTAL_DATABASE_NAMESPACE", ""))


def get_login_state(registration):
    if registration.get("kind") != "subscription_login":
        return "not_applicable"
    store, account_id = login_store(), registration["account_id"]
    state = store.login_state(account_id, default=None)
    if state is not None:
        return state
    # Existing credentials can predate the observation table. Timestamp the read
    # before running the CLI so a concurrent login/monitor result remains newer.
    observed_at = now()
    try:
        logged_in = provider_logged_in(registration["provider"], account_id)
    except (OSError, ValueError, subprocess.TimeoutExpired, InstructionError):
        state = store.login_state(account_id, default=None)
        if state is not None:
            return state
        raise InstructionError("instruction.owner_unavailable", "暂时无法查询账号登录状态，请检查 Provider 安装和出口配置",
                               details={"reason": "account.login_unavailable"}, retryable=True) from None
    store.observe(account_id, "logged_in" if logged_in else "logged_out", observed_at=observed_at)
    return store.login_state(account_id)


def record_login_state(account_id, state, *, observed_at=None):
    login_store().observe(account_id, state, observed_at=observed_at)


def local_login_environment(provider, account_id):
    """The login environment without a network route, for logins that stay on this host."""
    # Do not inherit API keys, cloud credentials, another account's home or session.
    if sys.platform not in {'linux', 'darwin', 'win32'}:
        raise NotImplementedError(f'Unsupported login platform: {sys.platform}')
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(Path.home()), "LANG": "C.UTF-8",
           "NO_COLOR": "1"}
    if sys.platform == 'win32':
        allowed = {'SYSTEMROOT': 'SystemRoot', 'WINDIR': 'WINDIR', 'COMSPEC': 'COMSPEC',
                   'PATHEXT': 'PATHEXT', 'TEMP': 'TEMP', 'TMP': 'TMP'}
        env.update({allowed[key.upper()]: value for key, value in os.environ.items() if key.upper() in allowed})
        env['USERPROFILE'] = env['HOME']
        env['BROWSER'] = subprocess.list2cmdline([env.get('COMSPEC', 'cmd.exe'), '/d', '/c', 'exit', '0'])
    else:
        env['BROWSER'] = '/bin/true'
    home = account_home(provider, account_id)
    env["CODEX_HOME" if provider == "codex" else "CLAUDE_CONFIG_DIR"] = str(home)
    return env


def login_environment(provider, account_id):
    env = local_login_environment(provider, account_id)
    managed_proxy = portal_network.saved_proxy_environment()
    if managed_proxy is not None:
        env.update(managed_proxy)
        return env
    configured = os.environ.get("PORTAL_PROVIDER_EGRESS_ENV_FILE", "")
    if configured:
        # A configured proxy source must exist and supply a route. Never silently
        # fall back to the host's physical egress when the site file is broken.
        for line in Path(configured).read_text(encoding="utf-8").splitlines():
            for item in shlex.split(line, comments=True):
                key, equal, value = item.partition("=")
                if equal and key in PROXY_KEYS:
                    env[key] = value
        if not any(env.get(key) for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")):
            raise ValueError("Configured Provider egress has no HTTPS proxy")
    else:
        env.update({key: os.environ[key] for key in PROXY_KEYS if key in os.environ})
    return env


def provider_binary(provider):
    """The installed CLI that logs a Provider account in."""
    if provider not in {"codex", "claude"}:
        raise ValueError("Provider does not support subscription login")
    from management_common.site_config import read_site_config
    from management_common.portal_paths import resolve_directory
    configured = read_site_config().get("deployment", {}).get("providers", {}).get(f"{provider}_binary")
    # Codex logs in with the program its app-server runs; that link already
    # reuses a Codex found on PATH.
    binary = (str(resolve_directory(configured)) if configured
              else None if provider == "codex" else shutil.which(provider))
    if not binary:
        from .provider_updates import CodexRuntime, ClaudeRuntime
        if provider == "codex":
            candidate = CodexRuntime().link()
        else:
            root = ClaudeRuntime().link()
            candidates = [*root.glob("lib/python*/site-packages/claude_agent_sdk/_bundled/claude"),
                          root / "Lib/site-packages/claude_agent_sdk/_bundled/claude.exe"]
            candidate = next((path for path in candidates if path.is_file()), None)
        if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            binary = str(candidate)
    if configured and (not Path(binary).is_file() or not os.access(binary, os.X_OK)):
        raise InstructionError("instruction.failed", "配置的 Provider 登录程序不可执行",
                               details={"reason": "account.runtime_missing"})
    if not binary:
        raise InstructionError("instruction.failed", "Provider 登录程序未安装", details={"reason": "account.runtime_missing"})
    return binary


def login_command(provider, *, status=False):
    binary = provider_binary(provider)
    if provider == "codex":
        return [binary, "login", "status" if status else "--device-auth"]
    return [binary, "auth", "status", "--json"] if status else [binary, "auth", "login", "--claudeai"]


def provider_logged_in(provider, account_id):
    result = run_process(login_command(provider, status=True), env=login_environment(provider, account_id),
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    if provider == "codex":
        return result.returncode == 0
    try:
        state = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError("Claude 登录状态查询未返回有效结果") from None
    if not isinstance(state, dict) or not isinstance(state.get("loggedIn"), bool):
        raise ValueError("Claude 登录状态查询未返回有效结果")
    if state["loggedIn"] and result.returncode:
        raise ValueError("Claude 登录状态查询未成功")
    return state["loggedIn"]


class LoginOutputParser:
    """Keep a bounded, ephemeral input window; persist only an auth URL/device code."""
    def __init__(self, provider):
        self.provider = provider
        self._pending = ""
        self.code = self.url = ""

    def feed(self, data, *, final=False):
        self._pending += data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        lines = re.split(r"[\r\n]", self._pending)
        self._pending = "" if final else lines.pop()[-16384:]
        for raw in lines:
            line = ANSI.sub("", raw).strip()
            for candidate in re.findall(r"https://[^\s\x1b<>]+", line):
                candidate = candidate.rstrip(".,;)")
                parts = urlsplit(candidate)
                allowed = ({"auth.openai.com", "chatgpt.com"} if self.provider == "codex"
                           else {"claude.ai", "claude.com", "console.anthropic.com", "platform.claude.com"})
                if parts.hostname in allowed and ("oauth" in parts.path or "device" in parts.path):
                    # Never accept credential-bearing callback URLs as public login prompts.
                    if not re.search(r"(?:[?#&])(?:access_token|refresh_token|id_token)=", candidate):
                        self.url = candidate
            if self.provider == "codex":
                match = re.fullmatch(r"(?:Code:\s*)?([A-Z0-9]{4,6}-[A-Z0-9]{4,6})", line)
                if match:
                    self.code = match.group(1)
        return self.code, self.url


def _session_paths(account_id):
    validate_account_id(account_id)
    root = instance_root() / "login"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256(account_id.encode()).hexdigest()[:16]
    return root / f"{digest}.lock", root / f"{digest}.sock"


def _terminate(process):
    if process is not None:
        terminate_process_tree(process, timeout=9)


class LoginManager:
    def __init__(self, store=None):
        self.store = store or login_store()

    def start(self, registration):
        account_id, provider = registration["account_id"], registration["provider"]
        # Validate runtime/proxy before acquiring a persistent session.
        command, env = login_command(provider), login_environment(provider, account_id)
        ensure_account_home(provider, account_id)
        lock_path, socket_path = _session_paths(account_id)
        lock = lock_path.open("a+")
        try:
            if not try_lock_descriptor(lock.fileno()):
                raise BlockingIOError
        except BlockingIOError:
            lock.close()
            session = self.store.get(account_id)
            if session and session["state"] in ACTIVE:
                return session
            raise InstructionError("instruction.conflict", "上一次登录正在结束，请稍后重试",
                                   details={"reason": "account.login_busy"}, retryable=True) from None
        try:
            session = self.store.begin(account_id)
            thread = threading.Thread(target=self._run, args=(registration, session, command, env, lock, socket_path),
                                      name=f"account-login-{account_id}", daemon=True)
            thread.start()
            return session
        except BaseException:
            lock.close()
            raise

    def _run(self, registration, session, command, env, lock, socket_path):
        process = listener = None
        selector = selectors.DefaultSelector()
        parser = LoginOutputParser(registration["provider"])
        observed = ("", "")
        chunks = queue.Queue(maxsize=64)
        output_done = threading.Event()
        output_thread = None
        def read_output():
            try:
                while not output_done.is_set():
                    chunk = process.stdout.read1(8192)
                    while not output_done.is_set():
                        try:
                            chunks.put(chunk, timeout=.1)
                            break
                        except queue.Full:
                            pass
                    if not chunk:
                        return
            except (OSError, ValueError):
                return
        try:
            listener = LocalListener(socket_path, backlog=4, replace=True)
            listener.settimeout(.5)
            selector.register(listener, selectors.EVENT_READ, "code")
            process = spawn_process(command, env=env, cwd=account_home(registration["provider"], session["account_id"]),
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            # Windows selectors accept sockets, not anonymous subprocess pipes.
            output_thread = threading.Thread(target=read_output, name="login-output", daemon=True)
            output_thread.start()
            while True:
                current = self.store.get(session["account_id"])
                if not current or current["started_at"] != session["started_at"] or current["state"] not in ACTIVE:
                    return
                for key, _ in selector.select(timeout=0.5):
                    if key.data == "code":
                        try:
                            connection, _ = listener.accept()
                        except TimeoutError:
                            continue
                        with connection:
                            connection.settimeout(6)
                            submitted = b""
                            while b"\n" not in submitted and len(submitted) <= 4096:
                                chunk = connection.recv(4097 - len(submitted))
                                if not chunk:
                                    break
                                submitted += chunk
                            code = submitted.rstrip(b"\n")
                            if (registration["provider"] == "claude" and current["state"] == "awaiting_user"
                                    and 0 < len(code) <= 4096 and b"\n" not in code and b"\r" not in code
                                    and process.poll() is None):
                                process.stdin.write(code + b"\n")
                                process.stdin.flush()
                                connection.sendall(b"ok")
                            else:
                                connection.sendall(b"invalid")
                while True:
                    try:
                        data = chunks.get_nowait()
                    except queue.Empty:
                        break
                    parser.feed(data, final=not data)
                prompts = (parser.code, parser.url)
                if prompts != observed and parser.url:
                    self.store.transition(session, "awaiting_user", code=parser.code, url=parser.url)
                    observed = prompts
                if process.poll() is not None:
                    success = process.returncode == 0 and provider_logged_in(registration["provider"], session["account_id"])
                    changed = self.store.transition(session, "succeeded" if success else "failed",
                                                    error="" if success else "Provider 未确认登录成功，请重试")
                    if changed and success:
                        self.store.observe(session["account_id"], "logged_in")
                        if registration["provider"] == "codex" and registration.get("enabled", True):
                            start_codex_instance(session["account_id"])
                    return
        except Exception:
            # Neither command output nor exception strings cross the login boundary.
            self.store.transition(session, "failed", error="登录进程无法完成，请检查 Provider 安装和出口配置后重试")
        finally:
            output_done.set()
            try:
                _terminate(process)
            finally:
                if output_thread is not None:
                    output_thread.join(5)
                if process:
                    process.stdin.close()
                    if output_thread is None or not output_thread.is_alive():
                        process.stdout.close()
                selector.close()
                if listener:
                    listener.close()
                lock.close()

    def submit_code(self, registration, code):
        session = self.store.get(registration["account_id"])
        if registration["provider"] != "claude" or not session or session["state"] != "awaiting_user":
            raise InstructionError("instruction.conflict", "该账号当前没有等待授权码的登录会话",
                                   details={"reason": "account.login_not_awaiting"})
        if not isinstance(code, str) or not code.strip() or len(code.encode()) > 4096 or any(c in code for c in "\r\n\0"):
            raise invalid("授权码格式无效")
        _, socket_path = _session_paths(registration["account_id"])
        try:
            with connect_local(socket_path, timeout=9) as connection:
                connection.sendall(code.strip().encode() + b"\n")
                if connection.recv(16) != b"ok":
                    raise OSError("Login not accepting input")
        except OSError:
            raise InstructionError("instruction.conflict", "登录进程不可用，请取消后重新开始",
                                   details={"reason": "account.login_unavailable"}) from None
        return self.store.get(registration["account_id"])

    def cancel(self, account_id):
        session = self.store.get(account_id)
        if session:
            self.store.transition(session, "failed", error="登录已取消")
        return self.store.get(account_id)


def delete_login_data(account_id):
    """Stop the login writer before its owning account removes credentials."""
    manager = LoginManager()
    manager.cancel(account_id)
    lock_path, socket_path = _session_paths(account_id)
    deadline = time.monotonic() + 15
    with lock_path.open("a+") as lock:
        while True:
            try:
                if not try_lock_descriptor(lock.fileno()):
                    raise BlockingIOError
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise InstructionError("instruction.conflict", "登录进程正在结束，请稍后重试删除",
                                           details={"reason": "account.login_busy"}, retryable=True) from None
                time.sleep(0.05)
        manager.store.remove(account_id)
        socket_path.unlink(missing_ok=True)


def _registration(inv):
    from .account_registrations import registration as read_registration
    registration = read_registration(inv.argument("account_id"))
    if not registration:
        raise not_found("执行账号不存在")
    if registration["kind"] != "subscription_login" or registration["provider"] not in {"codex", "claude"}:
        raise invalid("该账号不支持订阅登录")
    return registration


def _session_result(registration, session):
    return {**session, "requires_code_input": registration["provider"] == "claude"} if session else None


def open_provider_account_login(inv):
    registration = _registration(inv)
    try:
        session = LoginManager().start(registration)
    except (ValueError, OSError):
        raise InstructionError("instruction.failed", "无法启动登录，请检查账号目录、Provider 安装和出口配置",
                               details={"reason": "account.login_unavailable"}) from None
    return {"session": _session_result(registration, session)}


def update_provider_account_login(inv):
    registration = _registration(inv)
    session = LoginManager().submit_code(registration, inv.argument("authorization_code"))
    return {"session": _session_result(registration, session)}


def get_provider_account_login(inv):
    registration = _registration(inv)
    session = login_store().get(registration["account_id"])
    return {"session": _session_result(registration, session), "login_state": get_login_state(registration)}


def cancel_provider_account_login(inv):
    registration = _registration(inv)
    return {"session": _session_result(registration, LoginManager().cancel(registration["account_id"]))}


HANDLERS = {
    "OpenProviderAccountLogin": open_provider_account_login,
    "UpdateProviderAccountLogin": update_provider_account_login,
    "GetProviderAccountLogin": get_provider_account_login,
    "CancelProviderAccountLogin": cancel_provider_account_login,
}
