"""A Repo's commit history, one commit's files and patch, blame, and working-tree diffs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from management_common.text_values import stripped_text
from management_portal.repo_workspace_checkout import _checkout, _safe_relative_path, _target
from management_portal.repo_workspace_git import _git, _git_bytes
from management_portal.repo_workspace_state import _commit_record, _status_payload


MAX_DIFF_BYTES = 2 * 1024 * 1024
MAX_BLAME_LINES = 2500
MAX_COMMIT_FILES = 2000


def list_repo_commits(
    repo_id: str,
    *,
    path: str = "",
    limit: int = 40,
    offset: int = 0,
    revision: str = "HEAD",
) -> dict[str, Any]:
    _record, checkout = _checkout(repo_id)
    relative = _safe_relative_path(path, allow_empty=True)
    bounded_limit = min(100, max(1, int(limit)))
    bounded_offset = max(0, int(offset))
    clean_revision = stripped_text(revision) or "HEAD"
    verified = _git(
        checkout,
        "rev-parse",
        "--verify",
        f"{clean_revision}^{{commit}}",
        allowed_returncodes=(0, 128),
    )
    if not verified:
        return {
            "id": repo_id,
            "path": relative,
            "revision": clean_revision,
            "commits": [],
            "has_more": False,
        }
    args = [
        "log",
        clean_revision,
        f"--max-count={bounded_limit + 1}",
        f"--skip={bounded_offset}",
        "--date=iso-strict",
        "--format=%x1e%H%x1f%h%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%s%x1f%D%x1f%b",
    ]
    if relative:
        args.extend(["--follow", "--", relative])
    raw = _git(checkout, *args)
    commits = [
        _commit_record(value.strip("\n"))
        for value in raw.split("\x1e")
        if value.strip()
    ]
    return {
        "id": repo_id,
        "path": relative,
        "revision": clean_revision,
        "commits": commits[:bounded_limit],
        "has_more": len(commits) > bounded_limit,
        "offset": bounded_offset,
    }


def _commit_diff_command(
    commit: dict[str, Any],
    *args: str,
) -> tuple[str, ...]:
    sha = str(commit["sha"])
    parents = list(commit.get("parents") or [])
    if parents:
        return ("diff", *args, parents[0], sha, "--")
    return (
        "diff-tree",
        "--root",
        "--no-commit-id",
        "-r",
        *args,
        sha,
    )


def _commit_file_records(
    checkout: Path,
    commit: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    status_raw = _git_bytes(
        checkout,
        *_commit_diff_command(
            commit,
            "--name-status",
            "-z",
            "--find-renames",
            "--find-copies",
        ),
    )
    chunks = status_raw.split(b"\x00")
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(chunks):
        status = chunks[index].decode("utf-8", errors="replace")
        index += 1
        if not status:
            continue
        code = status[:1]
        if code in {"R", "C"}:
            if index + 1 >= len(chunks):
                break
            original_path = chunks[index].decode("utf-8", errors="replace")
            path = chunks[index + 1].decode("utf-8", errors="replace")
            index += 2
        else:
            if index >= len(chunks):
                break
            original_path = ""
            path = chunks[index].decode("utf-8", errors="replace")
            index += 1
        records.append(
            {
                "path": path,
                "original_path": original_path,
                "status": status,
                "status_code": code,
                "status_label": {
                    "A": "新增",
                    "C": "复制",
                    "D": "删除",
                    "M": "修改",
                    "R": "重命名",
                    "T": "类型变更",
                    "U": "冲突",
                }.get(code, status),
                "additions": 0,
                "deletions": 0,
                "binary": False,
                "patch": "",
            }
        )

    numstat_raw = _git_bytes(
        checkout,
        *_commit_diff_command(
            commit,
            "--numstat",
            "-z",
            "--find-renames",
            "--find-copies",
        ),
    )
    stats: dict[str, tuple[int, int, bool]] = {}
    chunks = numstat_raw.split(b"\x00")
    index = 0
    while index < len(chunks):
        header = chunks[index]
        index += 1
        if not header:
            continue
        fields = header.split(b"\t", 2)
        if len(fields) != 3:
            continue
        additions_raw, deletions_raw, encoded_path = fields
        if encoded_path:
            path = encoded_path.decode("utf-8", errors="replace")
        else:
            if index + 1 >= len(chunks):
                break
            index += 1  # Original path; the destination is the displayed path.
            path = chunks[index].decode("utf-8", errors="replace")
            index += 1
        binary = additions_raw == b"-" or deletions_raw == b"-"
        additions = int(additions_raw) if additions_raw.isdigit() else 0
        deletions = int(deletions_raw) if deletions_raw.isdigit() else 0
        stats[path] = (additions, deletions, binary)

    for record in records:
        additions, deletions, binary = stats.get(
            str(record["path"]),
            (0, 0, False),
        )
        record["additions"] = additions
        record["deletions"] = deletions
        record["binary"] = binary
    truncated = len(records) > MAX_COMMIT_FILES
    return records[:MAX_COMMIT_FILES], truncated


def _commit_patch_bytes(
    checkout: Path,
    commit: dict[str, Any],
) -> bytes:
    return _git_bytes(
        checkout,
        *_commit_diff_command(
            commit,
            "--no-ext-diff",
            "--no-color",
            "--find-renames",
            "--find-copies",
            "--patch",
        ),
    )


def _patch_path(block: str) -> str:
    added = re.search(r"(?m)^\+\+\+ (?:b/)?([^\t]+)(?:\t.*)?$", block)
    if added and added.group(1) != "/dev/null":
        return added.group(1)
    renamed = re.search(r"(?m)^rename to (.+)$", block)
    if renamed:
        return renamed.group(1)
    deleted = re.search(r"(?m)^--- (?:a/)?([^\t]+)(?:\t.*)?$", block)
    if deleted and deleted.group(1) != "/dev/null":
        return deleted.group(1)
    return ""


def _attach_commit_patches(
    files: list[dict[str, Any]],
    patch: str,
) -> None:
    by_path = {str(record["path"]): record for record in files}
    unmatched = [record for record in files]
    blocks = [
        value
        for value in re.split(r"(?m)(?=^diff --git )", patch)
        if value.startswith("diff --git ")
    ]
    for block in blocks:
        path = _patch_path(block)
        record = by_path.get(path)
        if record is None:
            record = next(
                (
                    candidate
                    for candidate in unmatched
                    if not candidate["patch"]
                ),
                None,
            )
        if record is None:
            continue
        record["patch"] = block
        if record in unmatched:
            unmatched.remove(record)


def read_commit(
    repo_id: str,
    sha: str,
) -> dict[str, Any]:
    _record, checkout = _checkout(repo_id)
    clean_sha = stripped_text(sha)
    verified = _git(
        checkout,
        "rev-parse",
        "--verify",
        f"{clean_sha}^{{commit}}",
        allowed_returncodes=(0, 128),
    )
    if not verified:
        raise FileNotFoundError(f"commit not found: {clean_sha}")
    raw = _git(
        checkout,
        "show",
        "-s",
        "--date=iso-strict",
        "--format=%H%x1f%h%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%s%x1f%D%x1f%b",
        verified,
    )
    commit = _commit_record(raw)
    files, files_truncated = _commit_file_records(checkout, commit)
    patch_bytes = _commit_patch_bytes(checkout, commit)
    truncated = len(patch_bytes) > MAX_DIFF_BYTES
    patch = patch_bytes[:MAX_DIFF_BYTES].decode("utf-8", errors="replace")
    _attach_commit_patches(files, patch)
    additions = sum(int(record["additions"]) for record in files)
    deletions = sum(int(record["deletions"]) for record in files)
    return {
        "id": repo_id,
        "commit": commit,
        "stats": {
            "files_changed": len(files),
            "additions": additions,
            "deletions": deletions,
        },
        "files": files,
        "files_truncated": files_truncated,
        "hidden_file_count": sum(
            not record["patch"] and not record["binary"]
            for record in files
        ),
        "patch": patch,
        "truncated": truncated,
    }


def blame_repo_file(
    repo_id: str,
    path: str,
) -> dict[str, Any]:
    _record, checkout = _checkout(repo_id)
    relative = _safe_relative_path(path)
    if not _git(checkout, "ls-files", "--error-unmatch", "--", relative, allowed_returncodes=(0, 1)):
        return {"id": repo_id, "path": relative, "lines": [], "truncated": False}
    raw = _git(
        checkout,
        "blame",
        "--line-porcelain",
        "HEAD",
        "--",
        relative,
        allowed_returncodes=(0, 128),
    )
    lines: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
        if re.fullmatch(r"[0-9a-f]{40} \d+ \d+(?: \d+)?", line):
            parts = line.split()
            current = {
                "sha": parts[0],
                "original_line": int(parts[1]),
                "line": int(parts[2]),
            }
        elif line.startswith("author "):
            current["author"] = line[7:]
        elif line.startswith("author-time "):
            try:
                timestamp = int(line[12:])
                current["authored_at"] = datetime.fromtimestamp(
                    timestamp, tz=timezone.utc
                ).astimezone().isoformat()
            except ValueError:
                current["authored_at"] = ""
        elif line.startswith("summary "):
            current["subject"] = line[8:]
        elif line.startswith("\t"):
            current["content"] = line[1:]
            lines.append(dict(current))
            if len(lines) >= MAX_BLAME_LINES:
                break
    return {
        "id": repo_id,
        "path": relative,
        "lines": lines,
        "truncated": len(lines) >= MAX_BLAME_LINES,
    }


def working_diff(
    repo_id: str,
    path: str,
) -> dict[str, Any]:
    _record, checkout = _checkout(repo_id)
    relative = _safe_relative_path(path)
    status = _status_payload(checkout)
    change = next(
        (
            item
            for item in status["changes"]
            if item["path"] == relative or item.get("original_path") == relative
        ),
        None,
    )
    if change is None:
        return {"id": repo_id, "path": relative, "change": None, "diff": "", "truncated": False}
    has_head = bool(_git(checkout, "rev-parse", "--verify", "HEAD", allowed_returncodes=(0, 128)))
    if change["untracked"] or not has_head:
        target = _target(checkout, relative, must_exist=True)
        if target.is_dir():
            patch_bytes = b""
        else:
            patch_bytes = _git_bytes(
                checkout,
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
            checkout,
            "diff",
            "HEAD",
            "--no-ext-diff",
            "--no-color",
            "--find-renames",
            "--",
            relative,
        )
    truncated = len(patch_bytes) > MAX_DIFF_BYTES
    return {
        "id": repo_id,
        "path": relative,
        "change": change,
        "diff": patch_bytes[:MAX_DIFF_BYTES].decode("utf-8", errors="replace"),
        "truncated": truncated,
    }
