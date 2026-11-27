"""Bounded, metadata-only discovery and browsing of host Provider directories.

Discovery is a hint, never an authentication observation. The management HTTP
entry owns authorization and supplies current registered paths for exclusion.
"""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass
import hashlib
from heapq import nsmallest
import os
from pathlib import Path
import stat
import string

from management_common.provider_account_paths import canonical_directory
from management_portal.hatg.contracts import InstructionError, invalid, not_found


@dataclass(frozen=True)
class ProviderRule:
    home_names: tuple[str, ...]
    environment: tuple[str, ...]
    markers: tuple[str, ...]


RULES = {
    "codex": ProviderRule((".codex", "codex"), ("CODEX_HOME",), ("auth.json",)),
    "claude": ProviderRule((".claude", "claude"), ("CLAUDE_CONFIG_DIR",), (".credentials.json",)),
    "antigravity": ProviderRule((".gemini", "gemini", "antigravity"), ("GEMINI_HOME",), ("oauth_creds.json",)),
    "kimi": ProviderRule((".kimi", "kimi"), ("KIMI_CODE_HOME", "KIMI_HOME"), ("config.toml",)),
}
# Discovery limits apply to the entire recursive search.
MAX_DIRECTORIES = 512
MAX_ENTRIES = 4096
MAX_CANDIDATES = 100
# Browse bounds child metadata probes and returned directories per page.
MAX_BROWSE_ENTRIES = 2048
MAX_BROWSE_DIRECTORIES = 500


def _directory(value, *, existing=True):
    path = canonical_directory(value)
    if existing and not stat.S_ISDIR(path.stat().st_mode):
        raise ValueError("路径不是目录")
    return path


def validate_reuse_directory(data):
    """Validate the caller's explicit value before new_registration fills defaults."""
    if data.get("kind") != "subscription_login":
        raise invalid("reuse_existing 仅用于订阅登录账号")
    config = data.get("config")
    value = config.get("account_dir") if isinstance(config, dict) else None
    try:
        _directory(value)
    except (ValueError, OSError, RuntimeError):
        raise invalid("复用已有登录必须指定现有的主机规范绝对目录，不能留空或使用符号链接") from None


def _roots(provider, home, env):
    """Known direct homes and narrow containers; never recursively scan HOME."""
    rule = RULES[provider]
    for key in rule.environment:
        if env.get(key):
            yield Path(env[key]), 0, f"环境变量 {key}"
    for name in rule.home_names:
        yield home / name, 0, "用户常见位置"
    for key in ("XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA"):
        base = env.get(key)
        if base:
            for name in rule.home_names:
                yield Path(base) / name.lstrip("."), 0, f"{key} 常见位置"
    for base in (home / ".config", home / "Library/Application Support"):
        for name in rule.home_names:
            yield base / name.lstrip("."), 0, "系统常见配置位置"
    yield home / f".{provider}-accounts", 2, "账号容器"
    # Antigravity uses GEMINI_HOME, so existing terminal accounts may use this name.
    if provider == "antigravity":
        yield home / ".gemini-accounts", 2, "账号容器"
    managed = home / ".tfi-provider-accounts"
    # Support both old provider/account and current instance/provider/account.
    yield managed / provider, 1, "Portal 账号容器"
    yield managed, 3, "Portal 实例账号容器"


def discover(provider, *, home=None, environ=None, excluded=()):
    """Reusable scanner. Optional inputs keep offline tests off the real host."""
    if provider not in RULES:
        raise invalid("该 Provider 不支持本机订阅登录目录扫描")
    home = Path.home() if home is None else Path(home)
    env = os.environ if environ is None else environ
    excluded = {os.path.normcase(str(Path(p).resolve())) for p in excluded}
    candidates, warnings = [], []
    visited = {}  # Highest traversal depth already inspected for this directory.
    pending = deque()
    truncated = False
    entries_left = MAX_ENTRIES
    directories_left = MAX_DIRECTORIES

    def warn(path, error):
        message = f"无法检查目录：{path}（{type(error).__name__}）"
        if message not in warnings:
            warnings.append(message)

    try:
        pending.extend(_roots(provider, home, env))
    except OSError as error:
        warn(home / ".tfi-provider-accounts", error)
    found = set()
    while pending:
        if directories_left <= 0 or entries_left <= 0:
            truncated = True
            break
        raw, depth, source = pending.popleft()
        try:
            path = _directory(str(raw))
        except FileNotFoundError:
            continue
        except (ValueError, OSError, RuntimeError) as error:
            warn(raw, error)
            continue
        key = os.path.normcase(str(path))
        if visited.get(key, -1) >= depth:
            continue
        visited[key] = depth
        directories_left -= 1
        try:
            for marker in RULES[provider].markers:
                # config.toml alone is not Kimi-specific outside a known Kimi home.
                if provider == "kimi" and source.startswith("Portal") and "kimi" not in {part.casefold() for part in path.parts}:
                    continue
                target = path / marker
                try:
                    mode = target.lstat().st_mode
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(mode) and key not in excluded and key not in found:
                    if len(candidates) >= MAX_CANDIDATES:
                        truncated = True
                        break
                    found.add(key)
                    candidates.append({"path": str(path), "provider": provider,
                        "evidence": f"{source}；存在 {marker}", "login_state": "unverified"})
                    break
            # List the directory too, so unreadable roots cannot claim usable evidence.
            with os.scandir(path) as entries:
                if depth == 0 or key in found or key in excluded:
                    continue
                for entry in entries:
                    if entries_left <= 0:
                        truncated = True
                        break
                    entries_left -= 1
                    if depth > 0 and entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                        pending.append((Path(entry.path), depth - 1, source))
        except OSError as error:
            if key in found:
                candidates = [c for c in candidates if os.path.normcase(c["path"]) != key]
                found.remove(key)
            warn(path, error)
    candidates.sort(key=lambda row: os.path.normcase(row["path"]))
    return {"candidates": candidates, "truncated": truncated, "warnings": warnings}


def _browse_roots():
    if os.name != "nt":
        return [{"name": "/", "path": "/"}]
    roots = []
    for drive in string.ascii_uppercase:
        path = Path(f"{drive}:\\")
        try:
            if path.is_dir():
                roots.append({"name": str(path), "path": str(path)})
        except OSError:
            continue
    return roots


def _browse_name_key(name: str) -> tuple[str, str]:
    return name.casefold(), name


def _browse_after(cursor: str, scope: str) -> tuple[str, str]:
    if not cursor:
        return "", ""
    try:
        if not isinstance(cursor, str) or len(cursor) > 8192:
            raise ValueError
        cursor_scope, encoded_name = cursor.split(".", 1)
        name = os.fsdecode(base64.b64decode(encoded_name, altchars=b"-_", validate=True))
        if cursor_scope != scope or not name or name in {".", ".."} or "\0" in name or Path(name).name != name:
            raise ValueError
    except ValueError:
        raise invalid("目录游标无效，或与当前目录不匹配") from None
    return _browse_name_key(name)


def _browse_page(path: Path, limit: int, cursor: str) -> tuple[list[dict[str, str]], str]:
    scope = hashlib.sha256(os.fsencode(path)).hexdigest()
    after = _browse_after(cursor, scope)
    directories = []
    last_name = ""
    with os.scandir(path) as entries:
        # The filesystem supplies no portable name order. Keep only the first
        # batch of names after the cursor, then probe metadata for that batch.
        candidates = nsmallest(
            MAX_BROWSE_ENTRIES + 1,
            (entry for entry in entries if _browse_name_key(entry.name) > after),
            key=lambda entry: _browse_name_key(entry.name),
        )
        has_more = len(candidates) > MAX_BROWSE_ENTRIES
        for entry in candidates[:MAX_BROWSE_ENTRIES]:
            if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                try:
                    child = _directory(entry.path)
                except (ValueError, OSError, RuntimeError):
                    # A raced child or Windows junction is not selectable.
                    pass
                else:
                    if len(directories) == limit:
                        has_more = True
                        break
                    directories.append({"name": entry.name, "path": str(child)})
            last_name = entry.name
    if not has_more:
        return directories, ""
    name_token = base64.urlsafe_b64encode(os.fsencode(last_name)).decode("ascii")
    return directories, f"{scope}.{name_token}"


def browse(value=None, *, cursor: str = "", limit: int | None = None):
    limit = MAX_BROWSE_DIRECTORIES if limit is None else limit
    if type(limit) is not int or not 1 <= limit <= MAX_BROWSE_DIRECTORIES:
        raise invalid(f"limit 必须是 1 至 {MAX_BROWSE_DIRECTORIES} 的整数")
    value = str(Path.home()) if value is None else value
    try:
        path = _directory(value)
        directories, next_cursor = _browse_page(path, limit, cursor)
        return {"path": str(path), "parent": str(path.parent), "directories": directories,
                "roots": _browse_roots(), "truncated": bool(next_cursor), "next_cursor": next_cursor}
    except InstructionError:
        raise
    except FileNotFoundError:
        raise not_found("主机目录不存在") from None
    except PermissionError:
        raise InstructionError("instruction.forbidden", "主机目录不可读") from None
    except (ValueError, OSError, RuntimeError):
        raise invalid("请指定可读的主机规范绝对目录，不支持符号链接") from None
