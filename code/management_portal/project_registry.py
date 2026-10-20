"""The Project registry, stored in PostgreSQL (decision_86aed2f0e8c7).

One registry document holds the whole registry -- ``schema_version``, ``status_vocab``, ``tag_vocab`` and
the ordered ``projects`` list -- so every reader sees one consistent version
and writers change it under the registry lock.
"""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

import copy
import hashlib
import json
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from management_portal.resources.registry_store import (
    announce_registry_changes,
    get_registry_store,
    registry_change_key,
)

COLLECTION = "project_registry"
DOCUMENT_ID = "projects"
SCHEMA_VERSION = 1
CHANGE_PATH = registry_change_key(COLLECTION, DOCUMENT_ID)
SHARE_ROOT = portal_path("share_root")
# A Project's ID is its only name (decision_4648f7fa3295): 1-80 letters (Chinese
# included), digits and ``_ . @ + -``.  A record has no separate display name.
PROJECT_ID = re.compile(r"[\w.@+-]{1,80}")
# The fields Portal reads.  Any other field -- a display ``name``, the legacy
# ``db_name`` -- is a copy nothing keeps current, so the registry refuses it.
# ``created_by``/``created_for`` say who created the Project (and for which
# User); the permission system reads them (decision_b820faf42218 §5.7).
PROJECT_FIELDS = frozenset({"id", "status", "project_type", "description", "path", "ddl", "tags", "created_by", "created_for"})

_cache_lock = threading.Lock()
_cache: tuple[int, dict[str, Any]] | None = None


def empty_registry() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "status_vocab": [], "tag_vocab": [], "projects": []}


def _normalized(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise RuntimeError("Project registry must be an object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(f"Project registry requires schema_version {SCHEMA_VERSION}")
    payload = dict(document)
    for key in ("status_vocab", "tag_vocab", "projects"):
        if not isinstance(payload.get(key), list):
            payload[key] = []
    return payload


def _only_known_fields(payload: dict[str, Any]) -> dict[str, Any]:
    extra = [
        f"{item.get('id') or '?'}.{key}"
        for item in payload["projects"] if isinstance(item, dict)
        for key in item if key not in PROJECT_FIELDS
    ]
    if extra:
        raise ValueError(
            f"项目登记只有 {', '.join(sorted(PROJECT_FIELDS))} 这些字段，项目 ID 就是项目名称；"
            f"多余字段：{', '.join(extra)}"
        )
    return payload


def load() -> dict[str, Any]:
    """The current registry; an empty registry when none was ever written."""
    global _cache
    store = get_registry_store()
    revision = store.revision(COLLECTION)
    with _cache_lock:
        if _cache is not None and _cache[0] == revision:
            return copy.deepcopy(_cache[1])
    document = store.get(COLLECTION, DOCUMENT_ID)
    payload = empty_registry() if document is None else _normalized(document)
    with _cache_lock:
        _cache = (revision, payload)
    return copy.deepcopy(payload)


def revision() -> int:
    return get_registry_store().revision(COLLECTION)


def projects() -> list[dict[str, Any]]:
    return [item for item in load()["projects"] if isinstance(item, dict)]


def project(project_id: str) -> dict[str, Any] | None:
    target = str(project_id or "").strip()
    if not target:
        return None
    return next((item for item in projects() if str(item.get("id") or "").strip() == target), None)


def tags() -> list[dict[str, Any]]:
    return [item for item in load()["tag_vocab"] if isinstance(item, dict)]


TAG_ID = re.compile(r"^[a-z][a-z0-9_]*$")
TAG_FIELDS = ("id", "folder", "description", "param")


class TagsChanged(Exception):
    """The tags changed since the version the caller read."""

    def __init__(self, current_version: str) -> None:
        super().__init__("Project tags changed")
        self.current_version = current_version


def tags_version(tags: list[dict[str, Any]]) -> str:
    """A content token of the tag list: only tag changes move it."""
    encoded = json.dumps(tags, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def normalized_tags(value: object) -> list[dict[str, Any]]:
    """Validate a whole tag list: unique IDs and folders, one knowledge_base folder each."""
    if not isinstance(value, list):
        raise ValueError("tags 必须是标签对象的数组")
    output: list[dict[str, Any]] = []
    ids: set[str] = set()
    folders: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("每个标签都必须是对象")
        unknown = sorted(set(item) - set(TAG_FIELDS))
        if unknown:
            raise ValueError(f"标签只能包含 id、folder、description 与 param：{', '.join(unknown)}")
        tag_id = str(item.get("id") or "").strip()
        if not TAG_ID.fullmatch(tag_id):
            raise ValueError(f"标签 ID 以小写字母开头，只含小写字母、数字和下划线：{tag_id!r}")
        folder = str(item.get("folder") or tag_id).strip()
        if folder in {".", ".."} or "/" in folder or "\\" in folder or folder.startswith("."):
            raise ValueError(f"标签 {tag_id} 的 folder 必须是 knowledge_base 下的一层目录名：{folder!r}")
        description = str(item.get("description") or "").strip()
        if not description:
            raise ValueError(f"标签 {tag_id} 需要 description")
        if tag_id in ids:
            raise ValueError(f"标签 ID 重复：{tag_id}")
        if folder in folders:
            raise ValueError(f"标签 folder 重复：{folder}")
        ids.add(tag_id)
        folders.add(folder)
        entry: dict[str, Any] = {"id": tag_id, "folder": folder, "description": description}
        param = str(item.get("param") or "").strip()
        if param:
            entry["param"] = param
        output.append(entry)
    return output


def replace_tags(value: object, *, expected_version: str | None = None) -> list[dict[str, Any]]:
    """Replace the whole tag list; ``expected_version`` guards against a concurrent change."""
    clean = normalized_tags(value)
    with mutate() as document:
        current = [item for item in document["tag_vocab"] if isinstance(item, dict)]
        if expected_version is not None and str(expected_version) != tags_version(current):
            raise TagsChanged(tags_version(current))
        document["tag_vocab"] = clean
    return copy.deepcopy(clean)


def tag_folder_map() -> dict[str, str]:
    output: dict[str, str] = {}
    for item in tags():
        tag_id = str(item.get("id") or "").strip()
        folder = str(item.get("folder") or "").strip()
        if tag_id and folder:
            output[tag_id] = folder
    return output


def runtime_path(raw_path: object) -> Path | None:
    """The filesystem directory of a registry ``path`` such as ``/Projects/X``."""
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        return None
    if text.startswith(("{$", "${")):
        raise ValueError("项目注册路径不能使用变量占位符；请填写实际目录或共享根相对路径")
    if len(text) >= 2 and text[1] == ":":
        text = text[2:]
    if text.startswith(("/Projects/", "/Codes/")) or not text.startswith(("/", "~")):
        text = str(SHARE_ROOT) + "/" + text.lstrip("/")
    return Path(text).expanduser().resolve(strict=False)


def project_roots() -> list[tuple[str, Path]]:
    """``(project_id, directory)`` pairs, the most specific directory first."""
    output: list[tuple[str, Path]] = []
    for item in projects():
        project_id = str(item.get("id") or "").strip()
        path = runtime_path(item.get("path"))
        if project_id and path is not None:
            output.append((project_id, path))
    output.sort(key=lambda value: len(str(value[1])), reverse=True)
    return output


def yaml_text() -> str:
    """The registry rendered as the YAML document Portal used to serve."""
    return yaml.safe_dump(load(), allow_unicode=True, sort_keys=False)


@contextmanager
def mutate(*, before_commit: Callable[[dict[str, Any], Any], None] | None = None) -> Iterator[dict[str, Any]]:
    """Change the registry in place inside one locked transaction.

    The yielded payload is written when the block finishes without an
    exception; an exception leaves the registry unchanged.  ``before_commit``
    gets the written registry and the open transaction, for records that
    commit with it (a new Project's place in the permission trees).
    """
    global _cache
    store = get_registry_store()
    changed = False
    with store.writer(COLLECTION) as writer:
        document = writer.get(COLLECTION, DOCUMENT_ID)
        before = empty_registry() if document is None else _normalized(document)
        payload = copy.deepcopy(before)
        yield payload
        after = _only_known_fields(_normalized(payload))
        if after != before or document is None:
            writer.put(COLLECTION, DOCUMENT_ID, after)
            changed = True
        if before_commit is not None:
            before_commit(after, writer.connection)
    if changed:
        if writer.committed:
            with _cache_lock:
                _cache = (writer.revisions[COLLECTION], copy.deepcopy(after))
        announce_registry_changes(COLLECTION, {DOCUMENT_ID: "updated"})


def replace(payload: dict[str, Any]) -> None:
    """Write a whole registry (initial import and test fixtures)."""
    clean = _normalized(copy.deepcopy(payload))
    with mutate() as current:
        current.clear()
        current.update(clean)


def reset_cache_for_tests() -> None:
    global _cache
    with _cache_lock:
        _cache = None
