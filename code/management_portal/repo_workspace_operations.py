"""Git changes to a Repo's checkout: commit, branch create and switch, sync with a remote, discard."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from management_common.site_compatibility import compatibility_value
from management_common.text_values import stripped_text
from management_portal import resource_identity_backend
from management_portal.repo_workspace_checkout import RepoWorkspaceConflict, _checkout, _repo_lock
from management_portal.repo_workspace_git import (
    GitAuthenticationRequired, GitOperationError, _git, _redact_text,
)
from management_portal.repo_workspace_network import _git_write_access_denied, _network_git, _repo_ssh_identity
from management_portal.repo_workspace_overview import repo_overview
from management_portal.repo_workspace_state import (
    _branch_name, _branch_records, _head_commit, _remote_auth_context, _remote_records, _status_payload,
)


def commit_repo_changes(
    repo_id: str,
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
    with _repo_lock(repo_id):
        _record, checkout = _checkout(repo_id)
        status = _status_payload(checkout)
        if status["conflict_count"]:
            raise RepoWorkspaceConflict("存在未解决的 Git 冲突，暂时不能提交。")
        if not status["dirty"]:
            raise RepoWorkspaceConflict("当前工作区没有可提交的变更。")
        _git(checkout, "add", "-A")
        staged = _git(
            checkout,
            "diff",
            "--cached",
            "--name-only",
            allowed_returncodes=(0,),
        )
        if not staged:
            raise RepoWorkspaceConflict("当前工作区没有可提交的变更。")
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
        _git(checkout, *args, timeout=180)
        return {
            "id": repo_id,
            "commit": _head_commit(checkout),
            "status": _status_payload(checkout),
        }


def _validate_branch_name(checkout: Path, name: str) -> None:
    if not name or name.startswith("-") or not _git(
        checkout, "check-ref-format", "--branch", name, allowed_returncodes=(0, 1, 128),
    ):
        raise ValueError("invalid branch name")


def update_repo_branch(
    repo_id: str,
    operation: str,
    name: str,
    *,
    start_point: str = "HEAD",
) -> dict[str, Any]:
    clean_operation = stripped_text(operation).casefold()
    clean_name = stripped_text(name)
    if clean_operation not in {"create", "switch"}:
        raise ValueError("branch operation must be create or switch")
    if not clean_name:
        raise ValueError("branch name required")
    with _repo_lock(repo_id):
        _record, checkout = _checkout(repo_id)
        if _status_payload(checkout)["dirty"]:
            raise RepoWorkspaceConflict("请先提交或处理当前变更，再切换分支。")
        if clean_operation == "create":
            _validate_branch_name(checkout, clean_name)
            if any(item["name"] == clean_name for item in _branch_records(checkout)):
                raise RepoWorkspaceConflict(f"分支已存在：{clean_name}")
            clean_start = stripped_text(start_point) or "HEAD"
            _git(checkout, "switch", "-c", clean_name, clean_start)
        else:
            if not any(item["name"] == clean_name for item in _branch_records(checkout)):
                raise FileNotFoundError(f"branch not found: {clean_name}")
            _git(checkout, "switch", clean_name)
        return repo_overview(repo_id)


def sync_repo(
    repo_id: str,
    operation: str,
    *,
    remote: str = "",
    branch: str = "",
    actor: str = "",
    credential: dict[str, Any] | None = None,
    remember_credential: bool = False,
    credential_root: str | Path | None = None,
    credential_key_path: str | Path | None = None,
) -> dict[str, Any]:
    clean_operation = stripped_text(operation).casefold()
    if clean_operation not in {"fetch", "pull", "push"}:
        raise ValueError("sync operation must be fetch, pull, or push")
    with _repo_lock(repo_id):
        record, checkout = _checkout(repo_id)
        ssh_identity_file = _repo_ssh_identity(record)
        if clean_operation == "push" and record.get("push_enabled") is False:
            raise RepoWorkspaceConflict(
                "此 Repo 已在设置中禁用 Push。"
            )
        remotes = _remote_records(checkout)
        remote_names = {item["name"] for item in remotes}
        clean_remote = stripped_text(remote)
        if clean_remote and clean_remote not in remote_names:
            raise ValueError(f"unknown remote: {clean_remote}")
        if not remotes:
            raise RepoWorkspaceConflict("当前 Repo 尚未配置 remote。")
        current_branch = _branch_name(checkout)
        clean_branch = stripped_text(branch) or current_branch
        if clean_operation in {"pull", "push"} and not current_branch:
            raise RepoWorkspaceConflict("detached HEAD 状态不能 Pull 或 Push。")
        if clean_operation in {"pull", "push"}:
            _validate_branch_name(checkout, clean_branch)
        if clean_operation == "pull" and _status_payload(checkout)["dirty"]:
            raise RepoWorkspaceConflict("请先提交或处理当前变更，再执行 Pull。")

        if clean_operation == "fetch":
            args = ["fetch", "--prune"]
            if clean_remote:
                args.append(clean_remote)
                target_remote = clean_remote
            else:
                args.append("--all")
                target_remote = remotes[0]["name"] if len(remotes) == 1 else ""
        elif clean_operation == "pull":
            args = ["pull", "--ff-only"]
            if clean_remote:
                args.extend(["--", clean_remote, clean_branch])
                target_remote = clean_remote
            else:
                upstream = _git(
                    checkout,
                    "rev-parse",
                    "--abbrev-ref",
                    "--symbolic-full-name",
                    "@{upstream}",
                    allowed_returncodes=(0, 128),
                )
                upstream_remote = upstream.partition("/")[0] if "/" in upstream else ""
                target_remote = (
                    upstream_remote
                    if upstream_remote in remote_names
                    else remotes[0]["name"] if len(remotes) == 1 else ""
                )
        else:
            target_remote = clean_remote or (
                remotes[0]["name"] if len(remotes) == 1 else "origin"
            )
            if target_remote not in remote_names:
                raise ValueError("select a valid remote before pushing")
            args = ["push", "--", target_remote, clean_branch]
            upstream = _git(
                checkout,
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
                allowed_returncodes=(0, 128),
            )
            if not upstream:
                args.insert(1, "--set-upstream")

        credential_result = {
            "used_saved": False,
            "saved": False,
            "credential_id": "",
        }
        if target_remote:
            context = _remote_auth_context(
                checkout,
                target_remote,
                push=clean_operation == "push",
            )
            try:
                output, credential_result = _network_git(
                    checkout,
                    *args,
                    repo_id=repo_id,
                    operation=clean_operation,
                    remote=target_remote,
                    branch=clean_branch,
                    context=context,
                    actor=actor,
                    credential=credential,
                    remember=remember_credential,
                    credential_root=credential_root,
                    credential_key_path=credential_key_path,
                    ssh_identity_file=ssh_identity_file,
                )
            except GitAuthenticationRequired:
                raise
            except GitOperationError as exc:
                if (
                    clean_operation == "push"
                    and _git_write_access_denied(exc)
                ):
                    resource_identity_backend.update_repo_policy(
                        repo_id,
                        push_enabled=False,
                    )
                    raise GitOperationError(
                        "Remote 明确拒绝此 Repo 的写入权限；"
                        "Portal 已自动关闭“允许 Portal Push 此 Repo”。",
                        returncode=exc.returncode,
                    ) from exc
                raise
        else:
            if credential:
                raise ValueError(
                    "存在多个 remote 时，请先选择需要认证的 remote。"
                )
            output = _git(
                checkout,
                *args,
                timeout=360,
                git_config=("credential.helper=",),
            )
        return {
            "id": repo_id,
            "operation": clean_operation,
            "output": _redact_text(output),
            "credential": credential_result,
            "workspace": repo_overview(repo_id),
        }


def discard_repo_changes(
    repo_id: str,
) -> dict[str, Any]:
    """Discard staged, unstaged, and untracked changes in one checkout."""
    with _repo_lock(repo_id):
        _record, checkout = _checkout(repo_id)
        before = _status_payload(checkout)
        if not before["dirty"]:
            return {
                "id": repo_id,
                "operation": "discard",
                "discarded_count": 0,
                "workspace": repo_overview(
                    repo_id,
                ),
            }
        if not _head_commit(checkout):
            raise RepoWorkspaceConflict(
                "当前 Repo 尚无 commit，不能自动放弃全部改动。"
            )
        _git(checkout, "reset", "--hard", "HEAD")
        _git(checkout, "clean", "-fd")
        return {
            "id": repo_id,
            "operation": "discard",
            "discarded_count": len(before["changes"]),
            "workspace": repo_overview(
                repo_id,
            ),
        }
