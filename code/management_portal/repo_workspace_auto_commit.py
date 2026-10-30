"""Automatic commits: the change context sent to the AI, then commit and push."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from management_common.text_values import stripped_text
from management_portal import resource_identity_backend
from management_portal.repo_workspace_ai import (
    DEFAULT_COMMIT_PROVIDER, _generate_commit_message, _normalize_generated_commit,
)
from management_portal.repo_workspace_checkout import RepoWorkspaceConflict, _checkout, _repo_lock, _target
from management_portal.repo_workspace_git import (
    GitAuthenticationRequired, GitOperationError, _git, _git_bytes, _redact_text,
)
from management_portal.repo_workspace_operations import commit_repo_changes, sync_repo
from management_portal.repo_workspace_overview import _automation_status_payload
from management_portal.repo_workspace_state import (
    _branch_name, _push_remote_name, _remote_records, _status_payload,
)


MAX_COMMIT_CONTEXT_BYTES = 160 * 1024
MAX_UNTRACKED_SAMPLE_BYTES = 24 * 1024


def _looks_sensitive_path(value: object) -> bool:
    relative = stripped_text(value).replace("\\", "/").casefold()
    if not relative:
        return False
    name = PurePosixPath(relative).name
    if name.endswith((".example", ".sample", ".template", ".pub")):
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith((".key", ".pem", ".p12", ".pfx", ".jks", ".keystore")):
        return True
    if name in {
        "credentials.json",
        "service-account.json",
        "service_account.json",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
    }:
        return True
    parts = set(PurePosixPath(relative).parts)
    return bool(parts & {"secrets", ".secrets"})


def _commit_change_context(
    repo_id: str,
    record: dict[str, Any],
    checkout: Path,
    status: dict[str, Any],
) -> dict[str, Any]:
    changes = list(status.get("changes") or [])
    sensitive_paths = sorted(
        {
            stripped_text(item.get("path"))
            for item in changes
            if _looks_sensitive_path(item.get("path"))
        }
    )
    if sensitive_paths:
        shown = "、".join(sensitive_paths[:6])
        suffix = " 等" if len(sensitive_paths) > 6 else ""
        raise RepoWorkspaceConflict(
            f"自动提交检测到可能包含凭据的文件：{shown}{suffix}。请先手动检查。"
        )

    pathspecs: list[str] = []
    for item in changes:
        for key in ("path", "original_path"):
            path = stripped_text(item.get(key))
            if path and path not in pathspecs:
                pathspecs.append(path)
    diff_args = [
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--find-renames",
        "HEAD",
    ]
    if pathspecs:
        diff_args.extend(["--", *pathspecs[:500]])
    tracked_diff = _git_bytes(
        checkout,
        *diff_args,
        allowed_returncodes=(0, 128),
    )
    stat_args = ["diff", "--stat", "--no-color", "HEAD"]
    if pathspecs:
        stat_args.extend(["--", *pathspecs[:500]])
    diff_stat = _git(
        checkout,
        *stat_args,
        allowed_returncodes=(0, 128),
    )

    untracked_sections: list[bytes] = []
    signature_parts = [
        _git_bytes(
            checkout,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ),
        tracked_diff,
    ]
    for item in changes:
        if not item.get("untracked"):
            continue
        relative = stripped_text(item.get("path"))
        try:
            target = _target(checkout, relative, must_exist=True)
            stat = target.stat()
        except (FileNotFoundError, OSError, ValueError):
            continue
        signature_parts.append(
            f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}".encode(
                "utf-8",
                errors="replace",
            )
        )
        if not target.is_file() or target.is_symlink():
            continue
        with target.open("rb") as stream:
            sample = stream.read(MAX_UNTRACKED_SAMPLE_BYTES)
        if b"\x00" in sample:
            sample = b"[binary file omitted]"
        section = (
            f"\n--- new file: {relative}\n".encode("utf-8", errors="replace")
            + sample
        )
        untracked_sections.append(section)

    combined = tracked_diff + b"".join(untracked_sections)
    truncated = len(combined) > MAX_COMMIT_CONTEXT_BYTES
    combined = combined[:MAX_COMMIT_CONTEXT_BYTES]
    diff_text = combined.decode("utf-8", errors="replace")
    if truncated:
        diff_text += "\n\n[diff context truncated by TFI Portal]"
    status_lines = [
        f"{stripped_text(item.get('code')):>2} {stripped_text(item.get('path'))}"
        for item in changes
    ]
    return {
        "repo_id": repo_id,
        "repo_name": stripped_text(record.get("name")) or checkout.name,
        "branch": _branch_name(checkout) or "(detached HEAD)",
        "change_count": len(changes),
        "status": "\n".join(status_lines),
        "diff_stat": diff_stat,
        "diff": diff_text,
        "truncated": truncated,
        "signature": hashlib.sha256(b"\0".join(signature_parts)).hexdigest(),
    }


def auto_commit_repo(
    repo_id: str,
    *,
    actor: str = "",
    provider_id: str = "",
    message_generator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    record, checkout = _checkout(repo_id)
    status = _status_payload(checkout)
    remotes = _remote_records(checkout)
    push_enabled = record.get("push_enabled") is not False
    branch = _branch_name(checkout)
    if status["conflict_count"]:
        raise RepoWorkspaceConflict(
            "存在未解决的 Git 冲突，自动提交已停止。"
        )
    if not status["dirty"]:
        return {
            "schema": "tfi.repo-auto-commit.v1",
            "id": repo_id,
            "outcome": "clean",
            "committed": False,
            "pushed": False,
            "reason": "",
            "automation_status": _automation_status_payload(
                repo_id,
                record,
                checkout,
            ),
        }
    if remotes and push_enabled and not branch:
        raise RepoWorkspaceConflict(
            "detached HEAD 状态不能自动 commit 并 push。"
        )

    context = _commit_change_context(
        repo_id,
        record,
        checkout,
        status,
    )
    generated_raw = (
        message_generator(context)
        if message_generator is not None
        else _generate_commit_message(
            context,
            actor=actor,
            provider_id=provider_id,
        )
    )
    generated = _normalize_generated_commit(generated_raw)
    with _repo_lock(repo_id):
        record, checkout = _checkout(repo_id)
        current_context = _commit_change_context(
            repo_id,
            record,
            checkout,
            _status_payload(checkout),
        )
        if current_context["signature"] != context["signature"]:
            raise RepoWorkspaceConflict(
                "Kimi 分析期间工作区发生了变化，请重新执行自动提交。"
            )

        committed = commit_repo_changes(
            repo_id,
            generated["subject"],
            description=generated["reason"],
            actor=actor,
        )
        push = {
            "attempted": bool(remotes) and push_enabled,
            "ok": False,
            "remote": "",
            "branch": branch,
            "error": "",
            "auth_challenge": None,
        }
        if remotes and push_enabled:
            push["remote"] = _push_remote_name(checkout, remotes)
            try:
                sync_repo(
                    repo_id,
                    "push",
                    remote=push["remote"],
                    branch=branch,
                    actor=actor,
                )
                push["ok"] = True
            except GitAuthenticationRequired as exc:
                push["error"] = _redact_text(str(exc))[:800]
                push["auth_challenge"] = exc.challenge
            except (
                GitOperationError,
                RepoWorkspaceConflict,
                ValueError,
            ) as exc:
                push["error"] = _redact_text(str(exc))[:800]

        if not remotes or not push_enabled:
            outcome = "committed"
        elif push["ok"]:
            outcome = "committed_and_pushed"
        else:
            outcome = "committed_push_failed"
        latest_record = resource_identity_backend.get_repo_record(repo_id)
        return {
            "schema": "tfi.repo-auto-commit.v1",
            "id": repo_id,
            "outcome": outcome,
            "committed": True,
            "pushed": bool(push["ok"]),
            "message": generated["subject"],
            "reason": generated["reason"],
            "ai": {
                "provider": generated["provider"]
                or (stripped_text(provider_id) or DEFAULT_COMMIT_PROVIDER),
                "model": generated["model"],
                "usage": generated["usage"],
            },
            "commit": committed["commit"],
            "push": push,
            "auth_challenge": push["auth_challenge"],
            "automation_status": _automation_status_payload(
                repo_id,
                latest_record,
                checkout,
            ),
        }
