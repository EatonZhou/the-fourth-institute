"""Git limited to one registered workspace directory (an Article inside a larger repository)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from management_common.site_compatibility import compatibility_value
from management_common.text_values import stripped_text
from management_portal.repo_workspace_checkout import (
    RepoWorkspaceConflict, _repo_lock, _safe_relative_path, _target,
)
from management_portal.repo_workspace_git import _git, _git_bytes
from management_portal.repo_workspace_history import MAX_DIFF_BYTES
from management_portal.repo_workspace_state import _branch_name, _commit_record, _head_commit, _status_payload


def _scoped_git_context(workspace_path: str | Path) -> tuple[Path, Path, str]:
    workspace = Path(workspace_path).resolve(strict=False)
    if not workspace.is_dir():
        raise FileNotFoundError(workspace)
    root_value = _git(
        workspace,
        "rev-parse",
        "--show-toplevel",
        allowed_returncodes=(0, 128),
    )
    if not root_value:
        raise ValueError("当前 Article 目录尚未纳入 Git 仓库。")
    repo_root = Path(root_value).resolve(strict=False)
    try:
        scope = workspace.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError("Article 目录不在 Git 仓库工作树内。") from exc
    return workspace, repo_root, scope or "."


def scoped_git_status(workspace_path: str | Path) -> dict[str, Any]:
    """Return Git state constrained to one registered workspace directory."""
    workspace = Path(workspace_path).resolve(strict=False)
    try:
        workspace, _repo_root, scope = _scoped_git_context(workspace)
    except ValueError as exc:
        return {
            "available": False,
            "reason": str(exc),
            "branch": "",
            "scope": "",
            "head": None,
            "dirty": False,
            "staged_count": 0,
            "unstaged_count": 0,
            "untracked_count": 0,
            "conflict_count": 0,
            "changes": [],
        }
    status = _status_payload(workspace, current_directory_only=True)
    return {
        "available": True,
        "reason": "",
        "branch": _branch_name(workspace) or "detached HEAD",
        "scope": scope,
        "head": _head_commit(workspace),
        **status,
    }


def initialize_scoped_git_workspace(workspace_path: str | Path) -> dict[str, Any]:
    workspace = Path(workspace_path).resolve(strict=False)
    if not workspace.is_dir():
        raise FileNotFoundError(workspace)
    existing = scoped_git_status(workspace)
    if existing.get("available"):
        return existing
    with _repo_lock(f"path:{workspace}"):
        _git(workspace, "init", "-b", "main")
        return scoped_git_status(workspace)


def scoped_git_diff(workspace_path: str | Path, path: str) -> dict[str, Any]:
    workspace, _repo_root, _scope = _scoped_git_context(workspace_path)
    relative = _safe_relative_path(path)
    status = _status_payload(workspace, current_directory_only=True)
    change = next(
        (
            item
            for item in status["changes"]
            if item["path"] == relative or item.get("original_path") == relative
        ),
        None,
    )
    if change is None:
        return {"path": relative, "change": None, "diff": "", "truncated": False}
    has_head = bool(
        _git(workspace, "rev-parse", "--verify", "HEAD", allowed_returncodes=(0, 128))
    )
    if change["untracked"] or not has_head:
        target = _target(workspace, relative, must_exist=True)
        patch_bytes = b"" if target.is_dir() else _git_bytes(
            workspace,
            "diff",
            "--no-index",
            "--no-ext-diff",
            "--no-color",
            "--",
            "/dev/null",
            relative,
            allowed_returncodes=(0, 1),
        )
    else:
        patch_bytes = _git_bytes(
            workspace,
            "diff",
            "HEAD",
            "--no-ext-diff",
            "--no-color",
            "--find-renames",
            "--",
            relative,
        )
    return {
        "path": relative,
        "change": change,
        "diff": patch_bytes[:MAX_DIFF_BYTES].decode("utf-8", errors="replace"),
        "truncated": len(patch_bytes) > MAX_DIFF_BYTES,
    }


def scoped_git_commit_diff(workspace_path: str | Path, sha: str) -> dict[str, Any]:
    """Return one commit patch constrained to the registered workspace directory."""
    workspace, _repo_root, scope = _scoped_git_context(workspace_path)
    clean_sha = str(sha or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", clean_sha):
        raise ValueError("无效的 Git commit ID。")
    verified = _git(
        workspace,
        "rev-parse",
        "--verify",
        f"{clean_sha}^{{commit}}",
        allowed_returncodes=(0, 128),
    )
    if not verified:
        raise FileNotFoundError(f"commit not found: {clean_sha}")
    raw = _git(
        workspace,
        "show",
        "-s",
        "--date=iso-strict",
        "--format=%H%x1f%h%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%s%x1f%D%x1f%b",
        verified,
    )
    patch_bytes = _git_bytes(
        workspace,
        "show",
        "--format=",
        "--no-ext-diff",
        "--no-color",
        "--find-renames",
        "--find-copies",
        "--patch",
        verified,
        "--",
        ".",
    )
    return {
        "scope": scope,
        "commit": _commit_record(raw),
        "diff": patch_bytes[:MAX_DIFF_BYTES].decode("utf-8", errors="replace"),
        "truncated": len(patch_bytes) > MAX_DIFF_BYTES,
    }


def scoped_git_history(workspace_path: str | Path, *, limit: int = 20) -> dict[str, Any]:
    workspace, _repo_root, scope = _scoped_git_context(workspace_path)
    bounded_limit = max(1, min(int(limit), 100))
    raw = _git(
        workspace,
        "log",
        f"-{bounded_limit}",
        "--date=iso-strict",
        "--format=%H%x1f%h%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%s%x1f%D%x1f%b%x1e",
        "--",
        ".",
        allowed_returncodes=(0, 128),
    )
    commits = [
        _commit_record(item.strip())
        for item in raw.split("\x1e")
        if item.strip()
    ]
    return {"available": True, "scope": scope, "commits": commits}


def stage_scoped_git_paths(
    workspace_path: str | Path,
    paths: Iterable[str],
    *,
    staged: bool,
) -> dict[str, Any]:
    workspace, repo_root, _scope = _scoped_git_context(workspace_path)
    relative_paths = list(dict.fromkeys(_safe_relative_path(path) for path in paths))
    if not relative_paths:
        raise ValueError("至少选择一个 Article 文件。")
    with _repo_lock(f"path:{repo_root}"):
        if staged:
            _git(workspace, "add", "-A", "--", *relative_paths)
        else:
            has_head = bool(
                _git(workspace, "rev-parse", "--verify", "HEAD", allowed_returncodes=(0, 128))
            )
            if has_head:
                _git(workspace, "restore", "--staged", "--", *relative_paths)
            else:
                _git(
                    workspace,
                    "rm",
                    "--cached",
                    "-r",
                    "--ignore-unmatch",
                    "--",
                    *relative_paths,
                )
        return scoped_git_status(workspace)


def commit_scoped_git_changes(
    workspace_path: str | Path,
    message: str,
    *,
    description: str = "",
    actor: str = "",
) -> dict[str, Any]:
    clean_message = stripped_text(message)
    if not clean_message:
        raise ValueError("commit message required")
    if "\n" in clean_message or len(clean_message) > 200:
        raise ValueError("commit message must be one line and at most 200 characters")
    clean_description = str(description or "").strip()
    if len(clean_description) > 8000:
        raise ValueError("commit description is too long")
    workspace, repo_root, scope = _scoped_git_context(workspace_path)
    with _repo_lock(f"path:{repo_root}"):
        status = _status_payload(workspace, current_directory_only=True)
        if status["conflict_count"]:
            raise RepoWorkspaceConflict("Article 中存在未解决的 Git 冲突，暂时不能提交。")
        all_staged = {
            value.strip()
            for value in _git(repo_root, "diff", "--cached", "--name-only").splitlines()
            if value.strip()
        }
        scope_prefix = "" if scope == "." else f"{scope}/"
        scoped_from_root = {
            value
            for value in all_staged
            if not scope_prefix or value.startswith(scope_prefix)
        }
        if not scoped_from_root:
            raise RepoWorkspaceConflict("请先暂存要提交的 Article 文件。")
        outside = sorted(all_staged - scoped_from_root)
        if outside:
            raise RepoWorkspaceConflict(
                "仓库暂存区还包含 Article 目录之外的文件；请先提交或取消暂存这些文件，Portal 不会混合提交。"
            )
        author = re.sub(r"[\x00\r\n]+", " ", stripped_text(actor)).strip() or compatibility_value("repo_git_author_name", "TFI Portal")
        email_local = re.sub(r"[^A-Za-z0-9._-]+", "-", author).strip("-") or "portal"
        args = [
            "-c",
            f"user.name={author}",
            "-c",
            f"user.email={email_local}@{compatibility_value('repo_git_email_domain', 'tfi.local')}",
            "commit",
            "-m",
            clean_message,
        ]
        if clean_description:
            args.extend(["-m", clean_description])
        _git(workspace, *args, timeout=180)
        return {"commit": _head_commit(workspace), "status": scoped_git_status(workspace)}
