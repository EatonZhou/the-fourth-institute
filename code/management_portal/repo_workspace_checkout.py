"""The checkout a Repo names, safe repo-relative paths inside it, and per-Repo write locks."""

from __future__ import annotations

import os
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from management_common.secure_paths import relative_parts
from management_portal import resource_identity_backend


_LOCKS_GUARD = threading.RLock()
_LOCKS: dict[str, threading.RLock] = {}


class RepoWorkspaceConflict(ValueError):
    """A valid operation could not be applied to the current checkout state."""


def _repo_lock(repo_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(repo_id, threading.RLock())


def _safe_relative_path(value: object, *, allow_empty: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("repo-relative path required")
    if raw.startswith("/") or "\x00" in raw or "\\" in raw:
        raise ValueError("repo-relative path contains unsupported characters")
    raw = raw.rstrip("/")
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("repo-relative path required")
    parts = relative_parts(PurePosixPath(raw).as_posix())
    if any((part.casefold() if os.name == "nt" else part) == ".git" for part in parts):
        raise ValueError("repo-relative path must stay inside the checkout")
    return "/".join(parts)


def _source_root(
    repo_id: str,
) -> tuple[dict[str, Any], Path]:
    resolved = resource_identity_backend.resolve_repo_address(
        repo_id,
    )
    root = Path(resolved["checkout_path"]).resolve(strict=False)
    if not root.is_dir():
        raise FileNotFoundError(f"repo checkout not found: {root}")
    marker = root / ".git"
    if resource_identity_backend.repo_source_kind(resolved["record"]) == "git_checkout" and not marker.exists():
        raise ValueError(f"repo checkout has no Git metadata: {root}")
    return dict(resolved["record"]), root


def _checkout(repo_id: str) -> tuple[dict[str, Any], Path]:
    record, root = _source_root(repo_id)
    resource_identity_backend.require_git_source(record)
    return record, root


def _source_capabilities(record: dict[str, Any], root: Path) -> dict[str, bool]:
    git = resource_identity_backend.repo_source_kind(record) == "git_checkout"
    return {
        "browse_files": True,
        "git": git,
        "write_files": git and os.access(root, os.W_OK),
        "search_files": git,
        "statistics": git,
    }


def _target(root: Path, relative_path: str, *, must_exist: bool = False) -> Path:
    if not relative_path:
        return root
    target = root / relative_path
    parent = target.parent.resolve(strict=False)
    try:
        parent_relative = parent.relative_to(root)
        if parent_relative.parts:
            _safe_relative_path(parent_relative.as_posix())
    except ValueError as exc:
        raise ValueError("repo-relative path escapes the checkout") from exc
    if not target.is_symlink():
        target = target.resolve(strict=False)
        try:
            _safe_relative_path(target.relative_to(root).as_posix())
        except ValueError as exc:
            raise ValueError("repo-relative path escapes the checkout") from exc
    if must_exist and not target.exists() and not target.is_symlink():
        raise FileNotFoundError(relative_path)
    return target
