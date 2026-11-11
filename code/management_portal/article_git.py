"""An article manuscript's Git repository: status, diffs, history, staging and commits."""
from __future__ import annotations

from typing import Any, Iterable

from management_portal import article_registry, permission_system, repo_workspace_scoped_git as git_workspace_backend


def require_git_write(article_id: str, actor: str) -> None:
    if article_id not in permission_system.permitted_ids(actor, "RunResourceAction", "resource_id", [article_id]):
        raise PermissionError("需要当前 Article 的协作权限才能修改源代码仓库。")


def article_git_status(
    article_id: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_status(article_root)


def pull_article_git(
    article_id: str, *, actor: str = "", credential: dict[str, Any] | None = None,
    remember: bool = False,
) -> dict[str, Any]:
    require_git_write(article_id, actor)
    _record, _root_item, article_root = article_registry._article_source_context(article_id)
    return git_workspace_backend.pull_scoped_git_workspace(
        article_root, article_id=article_id, actor=actor, credential=credential, remember=remember,
    )


def article_git_diff(
    article_id: str,
    path: str,
    *, staged: bool = False,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_diff(article_root, path, staged=staged)


def run_article_git_operation(article_id: str, operation: str, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
    require_git_write(article_id, actor)
    _record, _root_item, root = article_registry._article_source_context(article_id)
    if operation in {"fetch", "push", "sync"}:
        return git_workspace_backend.sync_scoped_git_workspace(
            root, operation, article_id=article_id, actor=actor,
            credential=payload.get("credential"), remember=bool(payload.get("remember", False)),
        )
    if operation == "branch":
        return git_workspace_backend.switch_scoped_git_branch(root, str(payload.get("name") or ""), create=payload.get("create") is True)
    if operation == "discard":
        paths = payload.get("paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise ValueError("paths must be a list of file names")
        return git_workspace_backend.discard_scoped_git_paths(root, paths)
    raise ValueError("unknown Article Git operation")


def article_git_commit_diff(
    article_id: str,
    sha: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_commit_diff(article_root, sha)


def initialize_article_git(
    article_id: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.initialize_scoped_git_workspace(article_root)


def article_git_history(
    article_id: str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_history(article_root, limit=limit)


def stage_article_git_paths(
    article_id: str,
    paths: Iterable[str],
    *,
    staged: bool,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.stage_scoped_git_paths(
        article_root, paths, staged=staged
    )


def commit_article_git_changes(
    article_id: str,
    message: str,
    *,
    description: str = "",
    actor: str = "",
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.commit_scoped_git_changes(
        article_root,
        message,
        description=description,
        actor=actor,
    )
