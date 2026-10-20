from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Any

from .lineage_values import _bool, _integer, _text


HANDOFF_STATES = {"draft", "preparing", "ready", "failed"}
HANDOFF_MODES = {"direct", "copy"}
HANDOFF_METHODS = {"direct", "filesystem_copy", "git_worktree"}
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,160}$")
EXCLUDED_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "test-results",
    "credentials",
    "secrets",
    "private_keys",
}
EXCLUDED_FILES = {".env", ".env.local", ".env.production"}
DEFAULT_MAX_FILES = 60_000
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_HANDOFF_COMMAND_TIMEOUT_SECONDS = 90 * 60
HANDOFF_COMMAND_TIMEOUT_ENV = "PORTAL_HANDOFF_COMMAND_TIMEOUT_SECONDS"


class HandoffPreparationError(RuntimeError):
    pass


def _positive_seconds(value: object, default: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(seconds) or seconds <= 0:
        return default
    return seconds


def _handoff_command_timeout_seconds() -> float:
    return _positive_seconds(
        os.getenv(HANDOFF_COMMAND_TIMEOUT_ENV),
        DEFAULT_HANDOFF_COMMAND_TIMEOUT_SECONDS,
    )


def _scope(value: object) -> str:
    raw = str(value or ".").strip().replace("\\", "/")
    if raw in {"", "."}:
        return "."
    if (
        raw.startswith("/")
        or PureWindowsPath(raw).drive
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise ValueError("工作区内容范围必须是安全的相对路径")
    return raw


def _parameters(raw: object) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    return {
        "base_commit": _text(item.get("base_commit"), 160),
        "branch": _text(item.get("branch"), 180),
        "include_worktree_changes": _bool(item.get("include_worktree_changes"), False),
        "exclude": [
            _text(value, 200)
            for value in (item.get("exclude") if isinstance(item.get("exclude"), list) else [])[:80]
            if _text(value, 200)
        ],
    }


def normalize_handoff_fields(
    raw: object,
    existing: object = None,
    *,
    now: str,
) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    previous = existing if isinstance(existing, dict) else {}
    status = _text(item.get("status") or previous.get("status"), 32).lower() or "draft"
    if status not in HANDOFF_STATES:
        status = "draft"
    raw_items = item.get("items") if isinstance(item.get("items"), list) else previous.get("items") or []
    if len(raw_items) > 24:
        raise ValueError("Workspace Handoff 最多支持 24 个交接项")
    previous_items = {
        str(entry.get("item_id") or ""): entry
        for entry in previous.get("items") or []
        if isinstance(entry, dict)
    }
    normalized_items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items, start=1):
        entry = raw_item if isinstance(raw_item, dict) else {}
        item_id = _text(entry.get("item_id"), 80).lower() or f"item_{index}"
        before = previous_items.get(item_id, {})
        source = entry.get("source") if isinstance(entry.get("source"), dict) else before.get("source") or {}
        transfer = entry.get("transfer") if isinstance(entry.get("transfer"), dict) else before.get("transfer") or {}
        target = entry.get("target") if isinstance(entry.get("target"), dict) else before.get("target") or {}
        result = entry.get("result") if isinstance(entry.get("result"), dict) else before.get("result") or {}
        mode = _text(transfer.get("mode"), 24).lower() or "direct"
        if mode not in HANDOFF_MODES:
            raise ValueError("工作交接方式必须是直接共享或创建副本")
        method = _text(transfer.get("method"), 40).lower() or ("direct" if mode == "direct" else "filesystem_copy")
        if method not in HANDOFF_METHODS:
            raise ValueError("不支持的工作区准备方式")
        if mode == "direct" and method != "direct":
            raise ValueError("直接交接必须使用 direct 准备方式")
        if mode == "copy" and method == "direct":
            raise ValueError("创建副本必须选择普通复制或 Git worktree")
        attempts = []
        for attempt in (entry.get("attempts") if isinstance(entry.get("attempts"), list) else before.get("attempts") or [])[-40:]:
            if not isinstance(attempt, dict):
                continue
            attempts.append({
                "number": max(1, _integer(attempt.get("number"), len(attempts) + 1)),
                "status": _text(attempt.get("status"), 32).lower() or "failed",
                "method": _text(attempt.get("method"), 40).lower() or method,
                "started_at": _text(attempt.get("started_at"), 80),
                "completed_at": _text(attempt.get("completed_at"), 80),
                "error": _text(attempt.get("error"), 4000),
                "result": attempt.get("result") if isinstance(attempt.get("result"), dict) else {},
            })
        normalized_items.append({
            "item_id": item_id,
            "label": _text(entry.get("label") or before.get("label"), 160) or f"工作区内容 {index}",
            "source": {
                "workspace_id": _text(source.get("workspace_id"), 80).lower(),
                "path": _text(source.get("path"), 4096),
                "scope": _scope(source.get("scope")),
            },
            "transfer": {
                "mode": mode,
                "method": method,
                "parameters": _parameters(transfer.get("parameters")),
            },
            "target": {
                "workspace_id": _text(target.get("workspace_id"), 80).lower(),
                "path": _text(target.get("path"), 4096),
                "scope": _scope(target.get("scope")),
                "label": _text(target.get("label"), 160),
            },
            "attempts": attempts,
            "result": {
                "source_state": result.get("source_state") if isinstance(result.get("source_state"), dict) else {},
                "git": result.get("git") if isinstance(result.get("git"), dict) else {},
                "target_workspace_id": _text(result.get("target_workspace_id"), 80).lower(),
                "target_path": _text(result.get("target_path"), 4096),
                "target_scope": _scope(result.get("target_scope")),
                "file_count": max(0, _integer(result.get("file_count"))),
                "byte_size": max(0, _integer(result.get("byte_size"))),
                "content_hash": _text(result.get("content_hash"), 160),
                "checked_at": _text(result.get("checked_at"), 80),
            } if result else {},
        })
    return {
        "explanation": _text(
            item.get("explanation")
            if "explanation" in item
            else previous.get("explanation"),
            8000,
        ),
        "items": normalized_items,
        "status": status,
        "error": _text(item.get("error"), 4000),
        "ready_at": _text(item.get("ready_at") or previous.get("ready_at"), 80),
    }


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    timeout = _positive_seconds(
        timeout_seconds,
        _handoff_command_timeout_seconds(),
    )
    try:
        return subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        rendered = shlex.join(command)
        if len(rendered) > 500:
            rendered = rendered[:497] + "..."
        raise HandoffPreparationError(
            f"工作区交接命令运行超过 {timeout:g} 秒：{rendered}。"
            f"可通过 {HANDOFF_COMMAND_TIMEOUT_ENV} 调整超时时间。"
        ) from exc


def _git_text(repo: Path, *args: str) -> str:
    result = _run(["git", "-C", str(repo), *args])
    return result.stdout.decode("utf-8", errors="replace").strip()


def inspect_git_workspace(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    result = _run(
        ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
        check=False,
    )
    if result.returncode != 0:
        return {"is_repo": False}
    root = Path(result.stdout.decode("utf-8", errors="replace").strip()).resolve()
    commit = _git_text(root, "rev-parse", "HEAD")
    branch = _git_text(root, "branch", "--show-current")
    dirty = bool(_git_text(root, "status", "--porcelain", "--untracked-files=normal"))
    return {
        "is_repo": True,
        "root": str(root),
        "commit": commit,
        "branch": branch,
        "dirty": dirty,
    }


def _safe_target(path: Path, allowed_roots: list[Path]) -> Path:
    target = path.expanduser().resolve(strict=False)
    if not target.is_absolute():
        raise ValueError("目标工作区必须使用绝对路径")
    allowed = False
    for root in allowed_roots:
        resolved_root = root.expanduser().resolve(strict=False)
        try:
            target.relative_to(resolved_root)
            allowed = target != resolved_root
            if allowed:
                break
        except ValueError:
            continue
    if not allowed:
        raise PermissionError("目标工作区不在允许的 Handoff 根目录中")
    if target.exists():
        raise FileExistsError(f"目标工作区已经存在：{target}")
    return target


def _selected_source(root: Path, scope: str) -> Path:
    source = root if scope == "." else (root / scope)
    source = source.resolve(strict=True)
    try:
        source.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("交接内容位于来源 Workspace 外") from exc
    return source


def _copy_ignored(directory: str, names: list[str], extra: set[str]) -> set[str]:
    folder = Path(directory)
    return {
        name
        for name in names
        if (
            name in EXCLUDED_NAMES
            or name in EXCLUDED_FILES
            or name in extra
            or (folder / name).is_symlink()
        )
    }


def _manifest(root: Path, *, max_files: int, max_bytes: int) -> tuple[list[dict[str, Any]], int, str]:
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for folder, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in EXCLUDED_NAMES and not (Path(folder) / name).is_symlink()
        ]
        current = Path(folder)
        for name in filenames:
            path = current / name
            if name in EXCLUDED_FILES or name in EXCLUDED_NAMES or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            total_bytes += size
            if len(files) + 1 > max_files:
                raise ValueError(f"工作区文件超过 {max_files} 个")
            if total_bytes > max_bytes:
                raise ValueError(f"工作区内容超过 {max_bytes} bytes")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            files.append({"path": relative, "size": size, "sha256": digest.hexdigest()})
    files.sort(key=lambda value: value["path"])
    content_hash = hashlib.sha256(
        json.dumps(files, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return files, total_bytes, f"sha256:{content_hash}"


def _safe_untracked_path(relative: str) -> bool:
    path = Path(relative)
    return (
        not path.is_absolute()
        and all(part not in EXCLUDED_NAMES for part in path.parts)
        and path.name not in EXCLUDED_FILES
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _apply_worktree_changes(source_repo: Path, target: Path, base_commit: str) -> dict[str, Any]:
    patch = _run(["git", "-C", str(source_repo), "diff", "--binary", base_commit, "--"]).stdout
    if patch:
        _run(["git", "-C", str(target), "apply", "--whitespace=nowarn", "-"], input_bytes=patch)
    untracked_raw = _run(
        ["git", "-C", str(source_repo), "ls-files", "--others", "--exclude-standard", "-z"]
    ).stdout
    copied = 0
    for raw in untracked_raw.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="strict")
        if not _safe_untracked_path(relative):
            continue
        source = (source_repo / relative).resolve(strict=False)
        try:
            source.relative_to(source_repo)
        except ValueError:
            continue
        if not source.is_file() or source.is_symlink():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
    return {
        "patch_sha256": f"sha256:{hashlib.sha256(patch).hexdigest()}" if patch else "",
        "untracked_file_count": copied,
    }


def materialize_handoff_item(
    item: dict[str, Any],
    *,
    allowed_target_roots: list[Path],
    checked_at: str,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    transfer = item.get("transfer") if isinstance(item.get("transfer"), dict) else {}
    target = item.get("target") if isinstance(item.get("target"), dict) else {}
    mode = str(transfer.get("mode") or "direct")
    method = str(transfer.get("method") or ("direct" if mode == "direct" else "filesystem_copy"))
    source_root = Path(str(source.get("path") or "")).expanduser().resolve(strict=True)
    if not source_root.is_dir():
        raise ValueError("来源 Workspace 不存在或不是目录")
    source_scope = _scope(source.get("scope"))
    selected = _selected_source(source_root, source_scope)
    if mode == "direct":
        return {
            "source_state": {
                "captured_at": checked_at,
                "git": inspect_git_workspace(source_root),
            },
            "target_path": str(source_root),
            "target_scope": source_scope,
            "file_count": 0,
            "byte_size": 0,
            "content_hash": "",
            "checked_at": checked_at,
        }

    target_root = _safe_target(Path(str(target.get("path") or "")), allowed_target_roots)
    parameters = transfer.get("parameters") if isinstance(transfer.get("parameters"), dict) else {}
    if method == "filesystem_copy":
        extra_excluded = set(str(value) for value in parameters.get("exclude") or [])
        scope_parent = source_root
        for scope_part in Path(source_scope).parts:
            if _copy_ignored(str(scope_parent), [scope_part], extra_excluded):
                raise ValueError("选择的内容范围属于 Handoff 复制排除项")
            scope_parent /= scope_part
        copied_files = 0
        copied_bytes = 0

        def copy_with_limits(source_path, target_path):
            nonlocal copied_files, copied_bytes
            copied_files += 1
            copied_bytes += Path(source_path).stat().st_size
            if copied_files > max_files:
                raise ValueError(f"工作区文件超过 {max_files} 个")
            if copied_bytes > max_bytes:
                raise ValueError(f"工作区内容超过 {max_bytes} bytes")
            return shutil.copy2(source_path, target_path)

        try:
            target_root.parent.mkdir(parents=True, exist_ok=True)
            if selected.is_dir():
                shutil.copytree(
                    selected,
                    target_root,
                    symlinks=False,
                    copy_function=copy_with_limits,
                    ignore=lambda directory, names: _copy_ignored(directory, names, extra_excluded),
                )
            else:
                target_root.mkdir(parents=True)
                copy_with_limits(selected, target_root / selected.name)
            files, total_bytes, content_hash = _manifest(
                target_root,
                max_files=max_files,
                max_bytes=max_bytes,
            )
        except Exception:
            if target_root.exists():
                shutil.rmtree(target_root, ignore_errors=True)
            raise
        return {
            "source_state": {
                "captured_at": checked_at,
                "git": inspect_git_workspace(source_root),
            },
            "target_path": str(target_root),
            "target_scope": _scope(target.get("scope")),
            "file_count": len(files),
            "byte_size": total_bytes,
            "content_hash": content_hash,
            "checked_at": checked_at,
        }

    if method != "git_worktree":
        raise ValueError("不支持的工作区准备方式")
    if source_scope != ".":
        raise ValueError("Git worktree 只能交接整个 Git Workspace")
    git = inspect_git_workspace(source_root)
    if not git.get("is_repo"):
        raise ValueError("来源 Workspace 不是 Git 仓库，不能创建 worktree")
    repo = Path(str(git["root"]))
    branch = _text(parameters.get("branch"), 180)
    if not BRANCH_RE.fullmatch(branch):
        raise ValueError("Git worktree 需要合法且唯一的目标分支")
    branch_exists = _run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    ).returncode == 0
    if branch_exists:
        raise ValueError(f"Git 分支已经存在：{branch}")
    requested_base = _text(parameters.get("base_commit"), 160) or str(git.get("commit") or "HEAD")
    base_commit = _git_text(repo, "rev-parse", f"{requested_base}^{{commit}}")
    created = False
    try:
        target_root.parent.mkdir(parents=True, exist_ok=True)
        result = _run(
            ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(target_root), base_commit],
            check=False,
        )
        if result.returncode != 0:
            raise HandoffPreparationError(
                result.stderr.decode("utf-8", errors="replace").strip()
                or "Git worktree 创建失败"
            )
        created = True
        worktree_changes = {"patch_sha256": "", "untracked_file_count": 0}
        if _bool(parameters.get("include_worktree_changes"), False):
            worktree_changes = _apply_worktree_changes(repo, target_root, base_commit)
        files, total_bytes, content_hash = _manifest(
            target_root,
            max_files=max_files,
            max_bytes=max_bytes,
        )
        return {
            "source_state": {
                "captured_at": checked_at,
                "git": {
                    **git,
                    "base_commit": base_commit,
                    **worktree_changes,
                },
            },
            "target_path": str(target_root),
            "target_scope": ".",
            "file_count": len(files),
            "byte_size": total_bytes,
            "content_hash": content_hash,
            "checked_at": checked_at,
            "git": {
                "branch": branch,
                "base_commit": base_commit,
                "include_worktree_changes": _bool(parameters.get("include_worktree_changes"), False),
            },
        }
    except Exception:
        if created:
            _run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(target_root)],
                check=False,
            )
        elif target_root.exists():
            shutil.rmtree(target_root, ignore_errors=True)
        _run(["git", "-C", str(repo), "branch", "-D", branch], check=False)
        raise


def cleanup_materialized_item(item: dict[str, Any], result: dict[str, Any]) -> None:
    transfer = item.get("transfer") if isinstance(item.get("transfer"), dict) else {}
    if str(transfer.get("mode") or "") != "copy":
        return
    target = Path(str(result.get("target_path") or "")).expanduser().resolve(strict=False)
    if not target.exists():
        return
    method = str(transfer.get("method") or "")
    if method == "git_worktree":
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        git = inspect_git_workspace(Path(str(source.get("path") or "")))
        if git.get("is_repo"):
            _run(
                ["git", "-C", str(git["root"]), "worktree", "remove", "--force", str(target)],
                check=False,
            )
            branch = str((transfer.get("parameters") or {}).get("branch") or "")
            if branch:
                _run(["git", "-C", str(git["root"]), "branch", "-D", branch], check=False)
            return
    shutil.rmtree(target, ignore_errors=True)
