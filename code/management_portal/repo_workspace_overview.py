"""A Repo's workspace overview and the automation status of many Repos."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from management_common.text_values import stripped_text
from management_portal import resource_identity_backend
from management_portal.repo_workspace_checkout import _checkout, _source_capabilities, _source_root
from management_portal.repo_workspace_git import _git, _redact_text
from management_portal.repo_workspace_network import _available_ssh_keys
from management_portal.repo_workspace_state import (
    _branch_name, _branch_records, _default_branch, _head_commit, _push_remote_name, _remote_records,
    _status_payload, _tag_records,
)


def _automation_status_payload(
    repo_id: str,
    record: dict[str, Any],
    checkout: Path,
) -> dict[str, Any]:
    status = _status_payload(checkout)
    remotes = _remote_records(checkout)
    branch = _branch_name(checkout)
    remote = _push_remote_name(checkout, remotes)
    push_enabled = record.get("push_enabled") is not False
    dirty = bool(status["dirty"])
    conflicts = int(status["conflict_count"])
    writable = os.access(checkout, os.W_OK)
    blocked_reason = ""
    if conflicts:
        blocked_reason = "存在未解决的 Git 冲突"
    elif not writable:
        blocked_reason = "checkout 当前不可写"
    elif dirty and remotes and not branch:
        blocked_reason = "detached HEAD 无法自动 push"
    return {
        "id": repo_id,
        "name": stripped_text(record.get("name")) or checkout.name,
        "dirty": dirty,
        "change_count": len(status["changes"]),
        "conflict_count": conflicts,
        "ahead": int(status["ahead"]),
        "behind": int(status["behind"]),
        "branch": branch,
        "detached": not bool(branch),
        "has_remote": bool(remotes),
        "push_enabled": push_enabled,
        "will_push": bool(remotes) and push_enabled,
        "remote_count": len(remotes),
        "remote": remote,
        "remote_names": [
            stripped_text(item.get("name"))
            for item in remotes
            if stripped_text(item.get("name"))
        ],
        "writable": writable,
        "actionable": dirty and not blocked_reason,
        "blocked_reason": blocked_reason,
    }


def repo_automation_status(
    repo_ids: Iterable[str],
) -> dict[str, Any]:
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for value in repo_ids:
        repo_id = stripped_text(value)
        if not re.fullmatch(r"repo_[0-9a-f]{12}", repo_id) or repo_id in seen:
            continue
        seen.add(repo_id)
        ordered_ids.append(repo_id)
        if len(ordered_ids) >= 500:
            break

    records: list[dict[str, Any]] = []
    for repo_id in ordered_ids:
        try:
            record, checkout = _checkout(repo_id)
            records.append(
                _automation_status_payload(repo_id, record, checkout)
            )
        except Exception as exc:
            records.append(
                {
                    "id": repo_id,
                    "dirty": False,
                    "change_count": 0,
                    "conflict_count": 0,
                    "has_remote": False,
                    "actionable": False,
                    "blocked_reason": _redact_text(str(exc))[:400],
                    "error": _redact_text(str(exc))[:400],
                }
            )
    return {
        "schema": "tfi.repo-automation-status.v1",
        "repos": records,
        "actionable_count": sum(bool(item.get("actionable")) for item in records),
        "dirty_count": sum(bool(item.get("dirty")) for item in records),
    }


def repo_overview(
    repo_id: str,
) -> dict[str, Any]:
    record, checkout = _source_root(repo_id)
    capabilities = _source_capabilities(record, checkout)
    overview = {
        "schema": "tfi.repo-workspace.v1",
        "id": repo_id,
        "name": stripped_text(record.get("name")) or checkout.name,
        "description": stripped_text(record.get("description")),
        "ownership": stripped_text(record.get("ownership")),
        "source_kind": resource_identity_backend.repo_source_kind(record),
        "capabilities": capabilities,
        "project_ids": [stripped_text(value) for value in record.get("project_ids") or [] if stripped_text(value)],
        "checkout_path": str(checkout),
        "checkout_writable": capabilities["write_files"],
        "updated_at": stripped_text(record.get("updated_at")),
    }
    if not capabilities["git"]:
        return {
            **overview,
            "push_enabled": False,
            "ssh_key": "",
            "available_ssh_keys": [],
            "current_branch": "",
            "detached": False,
            "default_branch": "",
            "upstream": "",
            "head": None,
            "status": None,
            "branches": [],
            "branch_count": 0,
            "tags": [],
            "tag_count": 0,
            "remotes": [],
        }
    status = _status_payload(checkout)
    branches = _branch_records(checkout)
    tags = _tag_records(checkout)
    remotes = _remote_records(checkout)
    current_branch = _branch_name(checkout)
    upstream = _git(
        checkout,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        allowed_returncodes=(0, 128),
    )
    return {
        **overview,
        "push_enabled": record.get("push_enabled") is not False,
        "ssh_key": stripped_text(record.get("ssh_key")),
        "available_ssh_keys": _available_ssh_keys(),
        "current_branch": current_branch,
        "detached": not bool(current_branch),
        "default_branch": _default_branch(checkout, remotes),
        "upstream": upstream,
        "head": _head_commit(checkout),
        "status": status,
        "branches": branches,
        "branch_count": len(branches),
        "tags": tags,
        "tag_count": len(tags),
        "remotes": remotes,
    }
