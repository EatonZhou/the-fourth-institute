"""Git state read from a checkout: remotes, commits, branches, tags and working-tree status."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from management_common.text_values import stripped_text
from management_portal.repo_workspace_checkout import _target
from management_portal.repo_workspace_git import _git, _git_bytes, _redact_url


def _remote_auth_context_from_url(value: object) -> dict[str, str]:
    raw = stripped_text(value)
    context = {
        "protocol": "",
        "host": "",
        "username": "",
        "path": "",
        "auth_type": "",
        "provider": "",
    }
    if not raw:
        return context
    if "://" in raw:
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return context
        protocol = parsed.scheme.casefold()
        host = parsed.netloc.rsplit("@", 1)[-1].casefold()
        username = unquote(parsed.username or "")
        path = parsed.path.lstrip("/")
    else:
        scp_like = re.fullmatch(
            r"(?:(?P<username>[^@/:]+)@)?(?P<host>[^:/\\]+):(?P<path>.+)",
            raw,
        )
        if scp_like:
            protocol = "ssh"
            host = scp_like.group("host").casefold()
            username = scp_like.group("username") or ""
            path = scp_like.group("path").lstrip("/")
        elif raw.startswith(("/", "./", "../")):
            protocol = "local"
            host = ""
            username = ""
            path = raw
        else:
            return context
    hostname = host.removeprefix("[").split("]", 1)[0].split(":", 1)[0]
    provider = "github" if hostname == "github.com" else (
        "gitee" if hostname == "gitee.com" else "generic"
    )
    if protocol == "ssh":
        auth_type = "ssh_secret"
        username = username or "git"
    elif protocol in {"http", "https"}:
        auth_type = "token" if provider == "github" else "password_or_token"
    else:
        auth_type = ""
    return {
        "protocol": protocol,
        "host": host,
        "username": username,
        "path": path,
        "auth_type": auth_type,
        "provider": provider,
    }


def _remote_web_url(value: object) -> str:
    raw = stripped_text(value)
    if not raw or re.match(r"^[A-Za-z]:[\\/]", raw):
        return ""
    context = _remote_auth_context_from_url(raw)
    protocol = context["protocol"]
    if protocol not in {"http", "https", "ssh"}:
        return ""
    path = re.sub(
        r"\.git/?$",
        "",
        context["path"].strip("/"),
        flags=re.IGNORECASE,
    )
    if not path:
        return ""
    try:
        parsed_host = urlsplit(f"//{context['host']}")
        hostname = parsed_host.hostname or ""
        port = parsed_host.port
    except ValueError:
        return ""
    if not hostname:
        return ""
    default_port = {"http": 80, "https": 443, "ssh": 22}[protocol]
    web_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != default_port:
        web_host = f"{web_host}:{port}"
    return urlunsplit(("https", web_host, f"/{path}", "", ""))


def _remote_auth_context(
    checkout: Path,
    remote: str,
    *,
    push: bool,
) -> dict[str, str]:
    args = ["remote", "get-url"]
    if push:
        args.append("--push")
    args.append(remote)
    url = _git(checkout, *args, allowed_returncodes=(0, 2))
    return _remote_auth_context_from_url(url)


def _commit_record(raw: str) -> dict[str, Any]:
    fields = raw.split("\x1f")
    fields.extend([""] * (10 - len(fields)))
    parents = [value for value in fields[2].split() if value]
    refs = [
        value.strip()
        for value in fields[8].strip(" ()").split(",")
        if value.strip() and value.strip() != "HEAD"
    ]
    return {
        "sha": fields[0],
        "short_sha": fields[1],
        "parents": parents,
        "author": fields[3],
        "author_email": fields[4],
        "authored_at": fields[5],
        "committer": fields[6],
        "subject": fields[7],
        "refs": refs,
        "body": fields[9].strip(),
    }


def _head_commit(checkout: Path) -> dict[str, Any] | None:
    raw = _git(
        checkout,
        "log",
        "-1",
        "--date=iso-strict",
        "--format=%H%x1f%h%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%s%x1f%D%x1f%b",
        allowed_returncodes=(0, 128),
    )
    return _commit_record(raw) if raw else None


def _branch_name(checkout: Path) -> str:
    return _git(
        checkout,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        allowed_returncodes=(0, 1),
    )


def _branch_records(checkout: Path) -> list[dict[str, Any]]:
    raw = _git(
        checkout,
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(refname:short)%00%(objectname)%00%(objectname:short)%00%(subject)%00%(authorname)%00%(committerdate:iso-strict)%00%(upstream:short)%00%(upstream:trackshort)",
        "refs/heads",
    )
    current = _branch_name(checkout)
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        fields = line.split("\x00")
        fields.extend([""] * (8 - len(fields)))
        records.append(
            {
                "name": fields[0],
                "sha": fields[1],
                "short_sha": fields[2],
                "subject": fields[3],
                "author": fields[4],
                "updated_at": fields[5],
                "upstream": fields[6],
                "tracking": fields[7],
                "current": fields[0] == current,
            }
        )
    return records


def _tag_records(checkout: Path) -> list[dict[str, Any]]:
    raw = _git(
        checkout,
        "for-each-ref",
        "--sort=-creatordate",
        "--format=%(refname:short)%00%(objectname)%00%(objectname:short)%00%(subject)%00%(creator)%00%(creatordate:iso-strict)",
        "refs/tags",
    )
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        fields = line.split("\x00")
        fields.extend([""] * (6 - len(fields)))
        records.append(
            {
                "name": fields[0],
                "sha": fields[1],
                "short_sha": fields[2],
                "subject": fields[3],
                "creator": fields[4],
                "created_at": fields[5],
            }
        )
    return records


def _remote_records(checkout: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in _git(checkout, "remote", allowed_returncodes=(0,)).splitlines():
        clean_name = name.strip()
        if not clean_name:
            continue
        fetch_url = _git(
            checkout,
            "remote",
            "get-url",
            clean_name,
            allowed_returncodes=(0, 2),
        )
        push_url = _git(
            checkout,
            "remote",
            "get-url",
            "--push",
            clean_name,
            allowed_returncodes=(0, 2),
        )
        records.append(
            {
                "name": clean_name,
                "url": _redact_url(fetch_url),
                "push_url": _redact_url(push_url),
                "web_url": _remote_web_url(fetch_url),
                "credentials_redacted": (
                    _redact_url(fetch_url) != fetch_url
                    or _redact_url(push_url) != push_url
                ),
            }
        )
    return records


def _working_path_modified_at(checkout: Path, relative_path: str) -> str:
    try:
        target = _target(checkout, relative_path)
        modified_at = target.lstat().st_mtime
    except (FileNotFoundError, OSError, ValueError):
        return ""
    return datetime.fromtimestamp(modified_at).astimezone().isoformat()


def _status_payload(checkout: Path, *, current_directory_only: bool = False) -> dict[str, Any]:
    args = [
        "status",
        "--porcelain=v1",
        "-z",
        "--branch",
        "--untracked-files=all",
    ]
    path_prefix = ""
    if current_directory_only:
        path_prefix = _git(checkout, "rev-parse", "--show-prefix")
        args.extend(["--", "."])
    chunks = _git_bytes(checkout, *args).decode("utf-8", errors="replace").split("\x00")
    branch_header = ""
    changes: list[dict[str, Any]] = []
    index = 0
    while index < len(chunks):
        value = chunks[index]
        index += 1
        if not value:
            continue
        if value.startswith("## "):
            branch_header = value[3:]
            continue
        if len(value) < 3:
            continue
        code = value[:2]
        path = value[3:]
        if path_prefix and path.startswith(path_prefix):
            path = path[len(path_prefix):]
        original_path = ""
        if code[0] in {"R", "C"} and index < len(chunks):
            original_path = chunks[index]
            index += 1
            if path_prefix and original_path.startswith(path_prefix):
                original_path = original_path[len(path_prefix):]
        staged = code[0] not in {" ", "?", "!"}
        unstaged = code[1] not in {" ", "?", "!"}
        untracked = code == "??"
        ignored = code == "!!"
        conflicted = "U" in code or code in {"AA", "DD"}
        changes.append(
            {
                "path": path,
                "original_path": original_path,
                "code": code,
                "staged": staged,
                "unstaged": unstaged,
                "untracked": untracked,
                "ignored": ignored,
                "conflicted": conflicted,
                "label": _status_label(code),
                "modified_at": _working_path_modified_at(checkout, path),
            }
        )

    ahead = 0
    behind = 0
    match = re.search(r"\[([^\]]+)\]", branch_header)
    if match:
        ahead_match = re.search(r"ahead (\d+)", match.group(1))
        behind_match = re.search(r"behind (\d+)", match.group(1))
        ahead = int(ahead_match.group(1)) if ahead_match else 0
        behind = int(behind_match.group(1)) if behind_match else 0
    return {
        "header": branch_header,
        "ahead": ahead,
        "behind": behind,
        "dirty": any(not item["ignored"] for item in changes),
        "staged_count": sum(bool(item["staged"]) for item in changes),
        "unstaged_count": sum(bool(item["unstaged"]) for item in changes),
        "untracked_count": sum(bool(item["untracked"]) for item in changes),
        "conflict_count": sum(bool(item["conflicted"]) for item in changes),
        "changes": [item for item in changes if not item["ignored"]],
    }


def _status_label(code: str) -> str:
    if code == "??":
        return "未跟踪"
    if code == "!!":
        return "已忽略"
    if "U" in code or code in {"AA", "DD"}:
        return "冲突"
    value = code.strip()
    if "R" in value:
        return "重命名"
    if "C" in value:
        return "复制"
    if "A" in value:
        return "新增"
    if "D" in value:
        return "删除"
    if "M" in value or "T" in value:
        return "修改"
    return value or "变更"


def _push_remote_name(
    checkout: Path,
    remotes: list[dict[str, Any]],
) -> str:
    if not remotes:
        return ""
    remote_names = {
        stripped_text(item.get("name"))
        for item in remotes
        if stripped_text(item.get("name"))
    }
    upstream = _git(
        checkout,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        allowed_returncodes=(0, 128),
    )
    upstream_remote, separator, _upstream_branch = upstream.partition("/")
    if separator and upstream_remote in remote_names:
        return upstream_remote
    if "origin" in remote_names:
        return "origin"
    return stripped_text(remotes[0].get("name"))


def _default_branch(checkout: Path, remotes: list[dict[str, Any]]) -> str:
    for remote in remotes:
        name = remote["name"]
        symbolic = _git(
            checkout,
            "symbolic-ref",
            "--quiet",
            "--short",
            f"refs/remotes/{name}/HEAD",
            allowed_returncodes=(0, 1),
        )
        prefix = f"{name}/"
        if symbolic.startswith(prefix):
            return symbolic[len(prefix) :]
    return ""
