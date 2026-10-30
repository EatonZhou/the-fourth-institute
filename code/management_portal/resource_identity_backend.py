from __future__ import annotations
from management_common.product_environment import read_product_env
from management_common.portal_paths import PORTAL_ROOT, path as portal_path
from management_common.clock import utc_now_iso
from management_common.text_values import stripped_text

import functools
import mimetypes
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import unquote


from management_portal.resources import get_resource_service
from management_portal.resources.registry_store import announce_registry_changes, get_registry_store
from management_portal.resources.errors import GitCheckoutRequired
from management_portal.resources import indexed_content
from management_portal.resources.indexed_content import _safe_repo_relative_path

from . import registry_sync


FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RESOURCE_SERVICE = get_resource_service()
REGISTRY_SCHEMA = "tfi.repo.v1"
REPO_COLLECTION = "repo"
DEFAULT_SSH_KEY_ROOT = Path(
    read_product_env("PORTAL_SSH_KEY_ROOT", str(Path.home() / ".ssh"))
).expanduser()
DEFAULT_SCAN_ROOTS = (
    portal_path("share_root"),
    PORTAL_ROOT,
)

# These trees can contain copied environments or generated Git metadata. They
# are not TFI Portal resources and make a full /share scan unnecessarily noisy.
PRUNED_DIR_NAMES = {
    ".cache",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}


def _dedupe_strings(values: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = stripped_text(value)
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _is_repo_id(value: object) -> bool:
    try:
        _RESOURCE_SERVICE.validate_id(stripped_text(value), "repo")
    except ValueError:
        return False
    return True


def normalize_remote_url(raw_url: object, *, checkout: Path | None = None) -> str:
    """Return a stable comparison key without changing the stored remote URL."""
    value = stripped_text(raw_url)
    if not value:
        return ""
    value = value.rstrip("/")
    if value.startswith("file://"):
        value = value[7:]
    if "://" not in value and not re.match(r"^[^/@:]+@[^/:]+:", value):
        path = Path(value).expanduser()
        if not path.is_absolute() and checkout is not None:
            path = checkout / path
        value = str(path.resolve(strict=False))
    else:
        value = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", value)
        value = re.sub(r"^[^/@:]+@", "", value)
        if ":" in value and "/" not in value.split(":", 1)[0]:
            host, tail = value.split(":", 1)
            value = f"{host}/{tail}"
        host, slash, tail = value.partition("/")
        value = host.casefold() + (slash + tail if slash else "")
    if value.casefold().endswith(".git"):
        value = value[:-4]
    return value.rstrip("/")


def _run_git(
    checkout: Path, *args: str, check: bool = True, env_overrides: dict[str, str] | None = None,
) -> str:
    from .repo_workspace_git import _git, GitOperationError

    try:
        return _git(checkout, *args, timeout=45, env_overrides=env_overrides)
    except GitOperationError as exc:
        if not check:
            return ""
        raise ValueError(str(exc)) from exc


def inspect_git_checkout(path: str | Path) -> dict[str, Any]:
    requested = Path(path).expanduser().resolve(strict=False)
    if not requested.exists() or not requested.is_dir():
        raise FileNotFoundError(f"checkout directory not found: {requested}")
    top_level_raw = _run_git(
        requested, "rev-parse", "--show-toplevel", check=False
    )
    if not top_level_raw:
        raise GitCheckoutRequired(requested)
    top_level = Path(top_level_raw).resolve(strict=False)
    if top_level != requested:
        raise ValueError(f"path is inside a Git checkout; register its root instead: {top_level}")

    common_raw = _run_git(top_level, "rev-parse", "--git-common-dir")
    common_path = Path(common_raw)
    if not common_path.is_absolute():
        common_path = top_level / common_path
    common_dir = common_path.resolve(strict=False)
    head = _run_git(top_level, "rev-parse", "HEAD", check=False)

    remotes: list[dict[str, str]] = []
    for name in _run_git(top_level, "remote", check=False).splitlines():
        clean_name = name.strip()
        if not clean_name:
            continue
        url = _run_git(top_level, "remote", "get-url", clean_name, check=False)
        if not url:
            continue
        push_url = _run_git(
            top_level, "remote", "get-url", "--push", clean_name, check=False
        )
        remotes.append(
            {
                "name": clean_name,
                "url": url,
                "push_url": push_url,
                "normalized_url": normalize_remote_url(url, checkout=top_level),
            }
        )
    identity_remote = next(
        (item["normalized_url"] for item in remotes if item["name"] == "origin"),
        remotes[0]["normalized_url"] if remotes else "",
    )
    return {
        "path": str(top_level),
        "git_common_dir": str(common_dir),
        "head": head if re.fullmatch(r"[0-9a-f]{40}", head) else "",
        "identity_remote": identity_remote,
        "remotes": remotes,
    }


def git_checkout_available(path: str | Path) -> bool:
    """Check checkout liveness; an unreadable checkout raises instead of disappearing."""
    requested = Path(path).expanduser().resolve(strict=False)
    try:
        directory_stat = requested.stat()
        marker_stat = (requested / ".git").stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    if not stat.S_ISDIR(directory_stat.st_mode):
        return False
    if not (stat.S_ISDIR(marker_stat.st_mode) or stat.S_ISREG(marker_stat.st_mode)):
        return False
    try:
        # The error text is matched below; services run with LANG=zh_CN.UTF-8.
        top_level = _run_git(requested, "rev-parse", "--show-toplevel", env_overrides={"LC_ALL": "C"})
    except ValueError as exc:
        message = str(exc).casefold()
        invalid_markers = (
            "not a git repository",
            "invalid gitfile format",
            "invalid line in gitfile",
        )
        if "permission denied" not in message and any(
            reason in message for reason in invalid_markers
        ):
            return False
        raise
    return bool(top_level) and Path(top_level).resolve(strict=False) == requested


def repo_source_kind(record: dict[str, Any]) -> str:
    """Repo content semantics are explicit; an unmarked record remains Git."""
    kind = record.get("source_kind", "git_checkout")
    if kind not in ("git_checkout", "exported_tree"):
        raise ValueError(f"unsupported repo source_kind: {kind!r}")
    return kind


def _exported_tree_root(record: dict[str, Any]) -> Path:
    raw = record.get("canonical_checkout")
    if not isinstance(raw, str) or not raw.strip() or not Path(raw).is_absolute():
        raise ValueError("exported_tree requires an absolute canonical_checkout")
    return Path(raw).resolve(strict=False)


def repo_checkout_paths(record: dict[str, Any]) -> list[str]:
    """Only a declared exported root can provide exported content, never an alias."""
    if repo_source_kind(record) == "exported_tree":
        return [str(_exported_tree_root(record))]
    paths = [
        item["path"] for item in record.get("checkouts") or []
        if isinstance(item, dict) and item.get("path")
    ]
    if record.get("canonical_checkout"):
        paths.append(record["canonical_checkout"])
    return list(dict.fromkeys(paths))


def repo_content_available(record: dict[str, Any]) -> bool:
    """Test the declared content source; unreadable storage is not deletion."""
    paths = repo_checkout_paths(record)
    if repo_source_kind(record) == "exported_tree":
        try:
            return stat.S_ISDIR(Path(paths[0]).stat().st_mode)
        except (FileNotFoundError, NotADirectoryError):
            return False
    return any(git_checkout_available(path) for path in paths)


def require_git_source(record: dict[str, Any]) -> None:
    if repo_source_kind(record) == "exported_tree":
        raise ValueError("exported_tree provides source files, not Git operations")


def discover_git_checkouts(
    roots: Iterable[str | Path] | None = None,
    *,
    excluded_roots: Iterable[str | Path] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    checkouts: list[dict[str, Any]] = []
    invalid_markers: list[dict[str, str]] = []
    visited_roots: set[str] = set()
    seen_checkouts: set[str] = set()
    exclusions = [
        Path(value).expanduser().resolve(strict=False)
        for value in excluded_roots
    ]

    def is_excluded(path: Path) -> bool:
        for excluded in exclusions:
            try:
                path.relative_to(excluded)
            except ValueError:
                continue
            return True
        return False

    for raw_root in roots or DEFAULT_SCAN_ROOTS:
        root = Path(raw_root).expanduser().resolve(strict=False)
        root_key = str(root)
        if (
            root_key in visited_roots
            or not root.exists()
            or not root.is_dir()
            or is_excluded(root)
        ):
            continue
        visited_roots.add(root_key)
        for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in PRUNED_DIR_NAMES and not name.startswith(".nox")
            ]
            current = Path(dirpath)
            if is_excluded(current):
                dirnames[:] = []
                continue
            dirnames[:] = [
                name
                for name in dirnames
                if not is_excluded(current / name)
            ]
            marker = current / ".git"
            if ".git" in dirnames:
                dirnames.remove(".git")
            if not marker.exists():
                continue
            try:
                checkout = inspect_git_checkout(current)
            except Exception as exc:
                invalid_markers.append({"path": str(marker), "error": str(exc)})
                continue
            key = checkout["path"]
            if key in seen_checkouts:
                continue
            seen_checkouts.add(key)
            checkouts.append(checkout)

    checkouts.sort(key=lambda item: item["path"].casefold())
    invalid_markers.sort(key=lambda item: item["path"].casefold())
    return checkouts, invalid_markers


def group_logical_repos(checkouts: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group clones/worktrees while keeping forks with only a shared upstream separate."""
    groups: list[list[dict[str, Any]]] = []
    key_to_group: dict[tuple[str, str], int] = {}
    for checkout in checkouts:
        keys = [("common", stripped_text(checkout.get("git_common_dir")))]
        if checkout.get("identity_remote"):
            keys.append(("origin", stripped_text(checkout.get("identity_remote"))))
        matching = {key_to_group[key] for key in keys if key in key_to_group}
        if not matching:
            group_index = len(groups)
            groups.append([checkout])
        else:
            group_index = min(matching)
            groups[group_index].append(checkout)
            # Merge a rare bridge between two provisional groups.
            for other_index in sorted(matching - {group_index}, reverse=True):
                groups[group_index].extend(groups[other_index])
                groups[other_index] = []
                for existing_key, existing_index in list(key_to_group.items()):
                    if existing_index == other_index:
                        key_to_group[existing_key] = group_index
        for key in keys:
            if key[1]:
                key_to_group[key] = group_index
    return [sorted(group, key=lambda item: item["path"].casefold()) for group in groups if group]


def _record_match_keys(record: dict[str, Any]) -> Iterator[tuple[str, str]]:
    if repo_source_kind(record) == "exported_tree":
        yield ("path", str(_exported_tree_root(record)))
        return
    for checkout in record.get("checkouts") or []:
        if not isinstance(checkout, dict):
            continue
        path = stripped_text(checkout.get("path"))
        common = stripped_text(checkout.get("git_common_dir"))
        origin = stripped_text(checkout.get("identity_remote"))
        if path:
            yield ("path", registry_sync.resolved_path(path))
        if common:
            yield ("git_common_dir", registry_sync.resolved_path(common))
        if origin:
            yield ("origin", origin)
    for remote in record.get("remotes") or []:
        if isinstance(remote, dict) and remote.get("is_identity") and remote.get("normalized_url"):
            yield ("origin", stripped_text(remote.get("normalized_url")))


def _checkout_match_keys(checkout: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """A checkout belongs to the record holding its path, its Git directory or its identity remote."""
    yield ("path", registry_sync.resolved_path(checkout["path"]))
    if stripped_text(checkout.get("git_common_dir")):
        yield ("git_common_dir", registry_sync.resolved_path(checkout["git_common_dir"]))
    origin = stripped_text(checkout.get("identity_remote"))
    if origin:
        yield ("origin", origin)


REPO_REGISTRY = registry_sync.RegistrySync(
    resource_type="repo",
    collection=REPO_COLLECTION,
    location="checkout",
    record_keys=_record_match_keys,
    location_keys=_checkout_match_keys,
)


def _validated_repo(document_id: str, record: object) -> dict[str, Any]:
    validated = REPO_REGISTRY.validated(document_id, record)
    if repo_source_kind(validated) == "exported_tree":
        _exported_tree_root(validated)
    return validated


def load_repo_records() -> list[dict[str, Any]]:
    return [_validated_repo(key, value) for key, value in REPO_REGISTRY.records().items()]


def get_repo_record(repo_id: str) -> dict[str, Any]:
    if not _is_repo_id(repo_id):
        raise ValueError(f"invalid repo address: {repo_id}")
    record = get_registry_store().get(REPO_COLLECTION, repo_id)
    if record is None:
        raise KeyError(repo_id)
    return _validated_repo(repo_id, record)


def _project_roots() -> list[tuple[str, Path]]:
    from management_portal import project_registry

    return project_registry.project_roots()


def _projects_for_path(path: str | Path, project_roots: list[tuple[str, Path]]) -> list[str]:
    candidate = Path(path).resolve(strict=False)
    matches: list[str] = []
    for project_id, project_root in project_roots:
        try:
            candidate.relative_to(project_root)
        except ValueError:
            continue
        matches.append(project_id)
    return _dedupe_strings(matches)


def project_roots() -> list[tuple[str, Path]]:
    return _project_roots()


def project_ids_for_path(path: str | Path) -> list[str]:
    return _projects_for_path(path, project_roots())


def _checkout_role(path: str) -> str:
    lower = path.casefold()
    if "/knowledge_base/experiment/" in lower:
        return "experiment"
    if "/knowledge_base/material/" in lower or "/to_archive/" in lower or "/backup" in lower:
        return "archive"
    if "/integrations/" in lower or "/_reference" in lower:
        return "reference"
    return "canonical"


def _canonical_checkout(checkouts: list[dict[str, Any]]) -> str:
    role_rank = {"canonical": 0, "clone": 1, "reference": 2, "experiment": 3, "archive": 4}
    available = [item for item in checkouts if Path(stripped_text(item.get("path"))).is_dir()]
    pool = available or checkouts
    if not pool:
        return ""
    selected = min(
        pool,
        key=lambda item: (
            role_rank.get(stripped_text(item.get("role")), 9),
            len(stripped_text(item.get("path"))),
            stripped_text(item.get("path")).casefold(),
        ),
    )
    return stripped_text(selected.get("path"))


def _repo_name(group: list[dict[str, Any]]) -> str:
    fallback = Path(group[0]["path"]).name
    for checkout in group:
        for remote in checkout.get("remotes") or []:
            if remote.get("name") != "origin":
                continue
            tail = stripped_text(remote.get("url")).rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            if tail.casefold().endswith(".git"):
                tail = tail[:-4]
            # Overleaf uses opaque hexadecimal project keys as remote paths;
            # local bundle remotes also make poor human-facing names.
            if tail and not re.fullmatch(r"[0-9a-fA-F]{16,64}", tail) and not tail.casefold().endswith(".bundle"):
                return tail
    return fallback


def _opaque_repo_name(value: object) -> bool:
    name = stripped_text(value)
    return bool(
        re.fullmatch(r"[0-9a-fA-F]{16,64}", name)
        or name.casefold().endswith(".bundle")
    )


def _ownership(group: list[dict[str, Any]]) -> str:
    identity_remotes = [stripped_text(item.get("identity_remote")) for item in group]
    if any(item for item in identity_remotes):
        return "external"
    return "unknown"


def _checkout_entry(checkout: dict[str, Any]) -> dict[str, Any]:
    path = registry_sync.resolved_path(checkout["path"])
    return {
        "path": path,
        "role": _checkout_role(path),
        "present": Path(path).is_dir(),
        "git_common_dir": checkout["git_common_dir"],
        "head": checkout.get("head") or "",
        "identity_remote": checkout.get("identity_remote") or "",
    }


def _merge_record(
    existing: dict[str, Any] | None,
    repo_id: str,
    group: list[dict[str, Any]],
    project_roots: list[tuple[str, Path]],
    explicit_project_ids: Iterable[str] = (),
) -> dict[str, Any]:
    now = utc_now_iso()
    previous = dict(existing or {})
    # Discovery/registration must not silently promote an imported source tree
    # into a Git working copy, even if someone adds Git metadata at its path.
    require_git_source(previous)
    merged_checkouts = registry_sync.merge_locations(
        previous.get("checkouts") or [], map(_checkout_entry, group)
    )

    remotes: list[dict[str, Any]] = []
    remote_keys: set[tuple[str, str]] = set()
    current_remote_names: set[str] = set()
    for checkout in group:
        identity = stripped_text(checkout.get("identity_remote"))
        for remote in checkout.get("remotes") or []:
            normalized = stripped_text(remote.get("normalized_url"))
            key = (stripped_text(remote.get("name")), normalized)
            if not normalized or key in remote_keys:
                continue
            remote_keys.add(key)
            current_remote_names.add(key[0])
            remotes.append(
                {
                    "name": key[0],
                    "url": stripped_text(remote.get("url")),
                    "push_url": stripped_text(remote.get("push_url")),
                    "normalized_url": normalized,
                    "is_identity": normalized == identity,
                }
            )
    for remote in previous.get("remotes") or []:
        if not isinstance(remote, dict):
            continue
        key = (stripped_text(remote.get("name")), stripped_text(remote.get("normalized_url")))
        if not key[1] or key in remote_keys or key[0] in current_remote_names:
            continue
        remote_keys.add(key)
        remotes.append(dict(remote))
    remotes.sort(key=lambda item: (stripped_text(item.get("name")), stripped_text(item.get("normalized_url"))))

    detected_projects: list[str] = []
    for checkout in merged_checkouts:
        detected_projects.extend(_projects_for_path(checkout["path"], project_roots))
    project_ids = _dedupe_strings(
        [*previous.get("project_ids", []), *explicit_project_ids, *detected_projects]
    )
    previous_name = stripped_text(previous.get("name"))
    detected_name = _repo_name(group)
    name = (
        detected_name
        if (
            not previous_name
            or _opaque_repo_name(previous_name)
        )
        else previous_name
    )
    record: dict[str, Any] = {
        "schema": REGISTRY_SCHEMA,
        "id": repo_id,
        "type": "repo",
        "name": name,
        "ownership": stripped_text(previous.get("ownership")) or _ownership(group),
        "push_enabled": previous.get("push_enabled") is not False,
        "ssh_key": stripped_text(previous.get("ssh_key")),
        "status": stripped_text(previous.get("status")) or "active",
        "project_ids": project_ids,
        "canonical_checkout": _canonical_checkout(merged_checkouts),
        "remotes": remotes,
        "checkouts": merged_checkouts,
        "created_at": stripped_text(previous.get("created_at")) or now,
        "updated_at": now,
    }
    description = stripped_text(previous.get("description"))
    if description:
        record["description"] = description
        description_generation = previous.get("description_generation")
        if isinstance(description_generation, dict):
            record["description_generation"] = dict(description_generation)
    upstream_repo_id = stripped_text(previous.get("upstream_repo_id"))
    if upstream_repo_id:
        record["upstream_repo_id"] = upstream_repo_id
    return record


@contextmanager
def _repo_writer() -> Iterator[Any]:
    with get_registry_store().writer(REPO_COLLECTION) as writer:
        yield writer


def _put_repo(writer: Any, record: dict[str, Any]) -> None:
    repo_id = stripped_text(record.get("id"))
    if not _is_repo_id(repo_id):
        raise ValueError(f"invalid repo id: {repo_id}")
    writer.put(REPO_COLLECTION, repo_id, record)


def _locked_repo(writer: Any, repo_id: str) -> dict[str, Any]:
    if not _is_repo_id(repo_id):
        raise ValueError(f"invalid repo address: {repo_id}")
    record = writer.get(REPO_COLLECTION, repo_id)
    if record is None:
        raise KeyError(repo_id)
    return _validated_repo(repo_id, record)


def scan_and_sync_repo_registry(
    roots: Iterable[str | Path] | None = None,
    *,
    excluded_roots: Iterable[str | Path] = (),
    apply: bool = False,
) -> dict[str, Any]:
    checkouts, invalid_markers = discover_git_checkouts(
        roots,
        excluded_roots=excluded_roots,
    )
    groups = group_logical_repos(checkouts)
    report = REPO_REGISTRY.sync(
        groups,
        merge=functools.partial(_merge_record, project_roots=_project_roots()),
        apply=apply,
    )
    report.update(
        {
            "checkout_count": len(checkouts),
            "invalid_marker_count": len(invalid_markers),
            "invalid_markers": invalid_markers,
        }
    )
    return report


def register_repo_checkout(
    path: str | Path,
    *,
    project_ids: Iterable[str] = (),
    repo_id: str = "",
) -> dict[str, Any]:
    checkout = inspect_git_checkout(path)
    report = REPO_REGISTRY.sync(
        [[checkout]],
        merge=functools.partial(
            _merge_record,
            project_roots=_project_roots(),
            explicit_project_ids=tuple(project_ids),
        ),
        apply=True,
        forced_id=repo_id,
    )
    records = [*report["created"], *report["updated"]]
    if records:
        return records[0]
    records_by_path = {
        str(Path(item.get("path", "")).resolve(strict=False)): record
        for record in load_repo_records()
        for item in record.get("checkouts") or []
        if isinstance(item, dict) and item.get("path")
    }
    return records_by_path[str(Path(path).resolve(strict=False))]


def initialize_git_checkout(path: str | Path) -> Path:
    checkout = Path(path).expanduser().resolve(strict=False)
    if not checkout.exists() or not checkout.is_dir():
        raise FileNotFoundError(f"checkout directory not found: {checkout}")
    try:
        inspect_git_checkout(checkout)
    except GitCheckoutRequired:
        _run_git(checkout, "init", "-q", "-b", "main")
    return checkout


def update_repo_remote(
    repo_id: str,
    *,
    name: str,
    url: str = "",
    push_url: str = "",
) -> dict[str, Any]:
    clean_name = stripped_text(name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", clean_name):
        raise ValueError("remote name contains unsupported characters")
    record = get_repo_record(repo_id)
    require_git_source(record)
    checkout = Path(stripped_text(_select_checkout(record).get("path")))
    existing_names = set(
        _run_git(checkout, "remote", check=False).splitlines()
    )
    if clean_name not in existing_names and not stripped_text(url):
        raise ValueError("fetch URL required when adding a remote")
    if clean_name in existing_names:
        if stripped_text(url):
            _run_git(checkout, "remote", "set-url", "--", clean_name, stripped_text(url))
    else:
        _run_git(checkout, "remote", "add", "--", clean_name, stripped_text(url))
    if stripped_text(push_url):
        _run_git(
            checkout,
            "remote",
            "set-url",
            "--push",
            "--",
            clean_name,
            stripped_text(push_url),
        )
    return register_repo_checkout(checkout, repo_id=repo_id)


def update_repo_description(
    repo_id: str,
    description: str,
    *,
    source_signature: str = "",
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Persist a concise Repo description without changing its Git checkout."""
    clean_description = re.sub(r"\s+", " ", stripped_text(description))
    if not clean_description:
        raise ValueError("repo description required")
    if len(clean_description) > 240:
        raise ValueError("repo description is too long")
    clean_signature = stripped_text(source_signature)
    if clean_signature and not re.fullmatch(r"[0-9a-f]{64}", clean_signature):
        raise ValueError("repo description source signature is invalid")

    with _repo_writer() as writer:
        record = _locked_repo(writer, repo_id)
        record["description"] = clean_description
        if stripped_text(provider) or stripped_text(model) or clean_signature:
            record["description_generation"] = {
                "provider": stripped_text(provider),
                "model": stripped_text(model),
                "generated_at": utc_now_iso(),
                "source_signature": clean_signature,
            }
        else:
            record.pop("description_generation", None)
        record["updated_at"] = utc_now_iso()
        _put_repo(writer, record)
    announce_registry_changes(REPO_COLLECTION, {repo_id: "updated"}, resource_type="repo")
    return record


def update_repo_policy(
    repo_id: str,
    *,
    push_enabled: bool,
) -> dict[str, Any]:
    """Persist Portal Git behavior without changing the checkout."""
    with _repo_writer() as writer:
        record = _locked_repo(writer, repo_id)
        record["push_enabled"] = bool(push_enabled)
        record["updated_at"] = utc_now_iso()
        _put_repo(writer, record)
    announce_registry_changes(REPO_COLLECTION, {repo_id: "updated"}, resource_type="repo")
    return record


def update_repo_ssh_key(
    repo_id: str,
    *,
    ssh_key: str,
) -> dict[str, Any]:
    """Persist the SSH identity filename selected for this Repo."""
    clean_key = stripped_text(ssh_key)
    if clean_key and (
        Path(clean_key).name != clean_key
        or not clean_key.startswith("id_")
        or clean_key.endswith(".pub")
    ):
        raise ValueError("invalid Repo SSH key name")
    if clean_key and not (
        (DEFAULT_SSH_KEY_ROOT / clean_key).is_file()
        and (DEFAULT_SSH_KEY_ROOT / f"{clean_key}.pub").is_file()
    ):
        raise FileNotFoundError(f"Repo SSH key not found: {clean_key}")
    with _repo_writer() as writer:
        record = _locked_repo(writer, repo_id)
        record["ssh_key"] = clean_key
        record["updated_at"] = utc_now_iso()
        _put_repo(writer, record)
    announce_registry_changes(REPO_COLLECTION, {repo_id: "updated"}, resource_type="repo")
    return record


def unregister_repo(repo_id: str) -> dict[str, Any]:
    """Remove a repo registry record while preserving every Git checkout."""
    with _repo_writer() as writer:
        record = _locked_repo(writer, repo_id)
        writer.delete(REPO_COLLECTION, repo_id)
    announce_registry_changes(REPO_COLLECTION, {repo_id: "deleted"}, resource_type="repo")
    return {
        "id": repo_id,
        "deleted": True,
        "checkout_paths": repo_checkout_paths(record),
    }


def _select_checkout(record: dict[str, Any]) -> dict[str, Any]:
    if repo_source_kind(record) == "exported_tree":
        return {"path": str(_exported_tree_root(record))}
    checkouts = [item for item in record.get("checkouts") or [] if isinstance(item, dict)]
    canonical = stripped_text(record.get("canonical_checkout"))
    for item in checkouts:
        if stripped_text(item.get("path")) == canonical and Path(canonical).is_dir():
            return item
    for item in checkouts:
        path = stripped_text(item.get("path"))
        if path and Path(path).is_dir():
            return item
    if checkouts:
        return checkouts[0]
    raise FileNotFoundError(f"repo has no checkout: {record.get('id')}")


def resolve_repo_address(address: str) -> dict[str, Any]:
    clean_address = unquote(stripped_text(address)).strip("/")
    root_id, separator, suffix = clean_address.partition("/")
    _RESOURCE_SERVICE.validate_id(root_id, "repo")
    record = get_repo_record(root_id)
    checkout = _select_checkout(record)
    checkout_root = Path(stripped_text(checkout.get("path"))).resolve(strict=False)
    result: dict[str, Any] = {
        "schema": "tfi.resource_resolution.v2",
        "address": clean_address,
        "root_id": root_id,
        "resource_type": "repo",
        "target_kind": "repo",
        "record": record,
        "checkout_path": str(checkout_root),
        "exists": checkout_root.is_dir(),
    }
    if not separator:
        return result

    parts = suffix.split("/")
    if len(parts) >= 2 and parts[0] == "file":
        relative_path = _safe_repo_relative_path("/".join(parts[1:]))
        target = (checkout_root / relative_path).resolve(strict=False)
        try:
            target.relative_to(checkout_root)
        except ValueError as exc:
            raise ValueError("repo-relative path escapes the checkout") from exc
        result.update(
            {
                "target_kind": "file",
                "relative_path": relative_path,
                "resolved_path": str(target),
                "exists": target.is_file(),
            }
        )
        return result

    if len(parts) >= 4 and parts[0] == "commit" and parts[2] == "file":
        require_git_source(record)
        commit = parts[1]
        if not FULL_COMMIT_RE.fullmatch(commit):
            raise ValueError("commit address requires a full 40-character lowercase SHA")
        relative_path = _safe_repo_relative_path("/".join(parts[3:]))
        resolved_commit = _run_git(
            checkout_root, "rev-parse", "--verify", f"{commit}^{{commit}}", check=False
        )
        if resolved_commit != commit:
            raise FileNotFoundError(f"commit not found in checkout: {commit}")
        blob_type = _run_git(checkout_root, "cat-file", "-t", f"{commit}:{relative_path}", check=False)
        result.update(
            {
                "target_kind": "git_file",
                "commit": commit,
                "relative_path": relative_path,
                "exists": blob_type == "blob",
            }
        )
        return result

    raise ValueError(f"unsupported nested repo address: {address}")


def read_resolved_resource_content(
    resolved: dict[str, Any],
    *,
    address: str = "",
    actor: str = "",
    max_bytes: int = 4 * 1024 * 1024,
) -> tuple[bytes, str, dict[str, Any]]:
    kind = resolved["target_kind"]
    if kind == "git_file":
        if not resolved.get("exists"):
            raise FileNotFoundError(address)
        checkout = Path(resolved["checkout_path"])
        spec = f"{resolved['commit']}:{resolved['relative_path']}"
        raw_size = _run_git(checkout, "cat-file", "-s", spec)
        try:
            size = int(raw_size)
        except ValueError as exc:
            raise FileNotFoundError(address) from exc
        if size > max_bytes:
            raise ValueError(f"resource exceeds {max_bytes} byte content limit")
        proc = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(checkout), "cat-file", "blob", spec],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45,
            check=False,
        )
        if proc.returncode != 0:
            raise FileNotFoundError(address)
        content_type = mimetypes.guess_type(resolved["relative_path"])[0] or "application/octet-stream"
        return proc.stdout, content_type, resolved
    if kind == "article_section":
        from . import article_sections
        return article_sections.read_article_section(resolved, max_bytes=max_bytes)
    return indexed_content.read_resolved_resource_content(
        resolved, address=address, actor=actor, max_bytes=max_bytes)
