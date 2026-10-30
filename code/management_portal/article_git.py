"""An article manuscript's Git repository: status, diffs, history, staging and commits."""
from __future__ import annotations

from typing import Any, Iterable

from management_portal import article_registry, repo_workspace_scoped_git as git_workspace_backend


def article_git_status(
    article_id: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_status(article_root)


def article_git_diff(
    article_id: str,
    path: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_diff(article_root, path)


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
