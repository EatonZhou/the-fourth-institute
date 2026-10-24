"""Explicit first-account import of the installing OS user's native login.

Only native authentication and model preferences cross this boundary. Histories,
tools, project trust and arbitrary environment variables never do. Source files
remain Provider-owned and are never edited or removed by Portal.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys
import tomllib

from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
from management_common.file_lock import try_lock_descriptor
from management_portal.hatg.contracts import InstructionError


def user_home() -> Path:
    # Installed services deliberately have an instance HOME. Agent CODEX_HOME
    # and CLAUDE_CONFIG_DIR belong to their current session, not the OS user.
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _read(path: Path, *, optional=False) -> bytes:
    if path.parent.is_symlink():
        raise ValueError("Local login directory must not be a symbolic link")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if optional:
            return b""
        raise
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 2 * 1024 * 1024:
            raise ValueError("Invalid local login file")
        return stream.read(2 * 1024 * 1024 + 1)


def _snapshot(provider: str) -> tuple[dict[str, bytes], str]:
    if sys.platform != "linux" or provider not in {"codex", "claude"}:
        raise ValueError("Local login import is unavailable")
    root = user_home() / (".codex" if provider == "codex" else ".claude")
    filename = "auth.json" if provider == "codex" else ".credentials.json"
    raw = _read(root / filename)
    auth = json.loads(raw)
    files = {}
    model = ""
    if provider == "codex":
        tokens = auth.get("tokens") or {}
        if auth.get("auth_mode") not in (None, "chatgpt") or auth.get("OPENAI_API_KEY") or not all(
                isinstance(tokens.get(k), str) and tokens[k] for k in ("access_token", "refresh_token")):
            raise ValueError("No local Codex subscription login")
        auth = {"auth_mode": "chatgpt", "tokens": {key: tokens[key] for key in
                ("access_token", "refresh_token", "id_token", "account_id") if key in tokens},
                **({"last_refresh": auth["last_refresh"]} if "last_refresh" in auth else {})}
        config = tomllib.loads(_read(root / "config.toml", optional=True).decode())
        if config.get("model_provider") not in (None, "openai"):
            raise ValueError("Local Codex uses a custom API provider")
        safe = {k: config[k] for k in ("model", "model_reasoning_effort", "service_tier")
                if isinstance(config.get(k), str)}
        model = safe.get("model", "")
        if safe:
            files["config.toml"] = ("\n".join(f"{key} = {json.dumps(value)}" for key, value in safe.items()) + "\n").encode()
    else:
        tokens = auth.get("claudeAiOauth") or {}
        if not all(isinstance(tokens.get(k), str) and tokens[k] for k in ("accessToken", "refreshToken")):
            raise ValueError("No local Claude subscription login")
        auth = {"claudeAiOauth": {key: tokens[key] for key in
                ("accessToken", "refreshToken", "expiresAt", "scopes", "subscriptionType", "rateLimitTier")
                if key in tokens}}
        config = json.loads(_read(root / "settings.json", optional=True) or b"{}")
        if any(str(k).startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "CLAUDE_CODE_OAUTH_"))
               for k in config.get("env", {})) or config.get("apiKeyHelper"):
            raise ValueError("Local Claude uses separate API configuration")
        model = config.get("model", "") if isinstance(config.get("model", ""), str) else ""
        if model:
            files["settings.json"] = json.dumps({"model": model}).encode()
    files[filename] = json.dumps(auth).encode()
    return files, model


def describe(provider: str) -> dict:
    """Public availability, never paths, tokens, identities or raw exceptions."""
    try:
        _, model = _snapshot(provider)
        return {"available": True, "default_model": model}
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return {"available": False, "default_model": ""}


def import_login(provider: str, destination: Path) -> dict[str, bytes]:
    """Import once into an empty Portal-owned home, or leave it unchanged."""
    try:
        files, _ = _snapshot(provider)
        if destination.is_symlink() or destination.parent.is_symlink():
            raise ValueError("Invalid account directory")
        # Lock outside the account directory so the directory remains a native
        # Provider home. Existing account authentication is never overwritten.
        lock = destination.parent / ("." + destination.name + ".local-login.lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as stream:
            if not try_lock_descriptor(stream.fileno()):
                raise ValueError("Local login import is already running")
            if any(destination.iterdir()):
                raise ValueError("Account directory is not empty")
            written = []
            try:
                for name, content in files.items():
                    path = destination / name
                    atomic_write_private_bytes(path, content)
                    written.append(path)
            except BaseException:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
        return files
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise InstructionError("instruction.conflict", "本机登录无法复用或账号目录已有内容，请保留现有账号并通过登录授权完成接入。",
                               details={"reason": "account.local_login_unavailable"}) from None


@contextmanager
def registration_lock(provider: str, account_id: str):
    """Fence *every* same-home registration through DB exit and file rollback."""
    if provider not in {"codex", "claude"}:
        yield
        return
    from .account_hosting import account_home, _check_home
    destination = account_home(provider, account_id)
    _check_home(destination)
    ensure_private_directory(destination.parent)
    lock = destination.parent / ("." + destination.name + ".registration.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a") as stream:
        if not try_lock_descriptor(stream.fileno()):
            raise InstructionError("instruction.conflict", "该账号正在登记，请稍后重试。")
        yield


@contextmanager
def registration_home(provider: str, account_id: str):
    """Keep imported files only if the surrounding registration commits."""
    from .account_hosting import account_home, ensure_account_home
    destination = account_home(provider, account_id)
    existed = destination.exists()
    ensure_account_home(provider, account_id)
    imported = {}
    try:
        imported = import_login(provider, destination)
        yield
    except BaseException:
        if imported:
            # The registration transaction holds the identity lock; no
            # execution can yet resolve this uncommitted account.
            for name, content in imported.items():
                path = destination / name
                if path.is_file() and not path.is_symlink() and path.read_bytes() == content:
                    path.unlink()
        if not existed and not any(destination.iterdir()):
            destination.rmdir()
        raise
