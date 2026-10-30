"""Article registry records: matching, merging and reading records, and the manuscript
root and paths a record's operations work in.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Iterator

from management_common.clock import utc_now_iso
from management_common.text_values import stripped_text
from management_portal import article_discovery, article_tex, registry_sync
from management_portal.resources import get_resource_service
from management_portal.resources.registry_store import get_registry_store


REGISTRY_SCHEMA = "tfi.article.v1"
ARTICLE_COLLECTION = "article"


def _record_match_keys(record: dict[str, Any]) -> Iterator[tuple[str, ...]]:
    for root in record.get("roots") or []:
        if isinstance(root, dict) and root.get("path"):
            yield ("path", registry_sync.resolved_path(root.get("path")))
    fingerprints = article_tex._dedupe(
        [*record.get("title_fingerprints", []), article_tex.title_fingerprint(record.get("title"))]
    )
    for project_id in record.get("project_ids") or [""]:
        for fingerprint in fingerprints:
            yield ("title", stripped_text(project_id).casefold(), fingerprint)


def _root_match_keys(item: dict[str, Any]) -> Iterator[tuple[str, ...]]:
    """A manuscript root belongs to the record holding its path or, within one
    project, the record whose title it shares."""
    yield ("path", registry_sync.resolved_path(item["path"]))
    fingerprint = stripped_text(item.get("title_fingerprint"))
    for project_id in item.get("project_ids") or [""]:
        yield ("title", stripped_text(project_id).casefold(), fingerprint)


ARTICLE_REGISTRY = registry_sync.RegistrySync(
    resource_type="article",
    collection=ARTICLE_COLLECTION,
    location="article root",
    record_keys=_record_match_keys,
    location_keys=_root_match_keys,
)
_validated_article = ARTICLE_REGISTRY.validated


def load_article_records() -> list[dict[str, Any]]:
    return list(ARTICLE_REGISTRY.records().values())


def is_article_storage_path(
    path: str | Path,
) -> bool:
    candidate = Path(path).expanduser().resolve(strict=False)
    for record in load_article_records():
        for root in record.get("roots") or []:
            if not isinstance(root, dict) or not root.get("path"):
                continue
            try:
                candidate.relative_to(
                    Path(stripped_text(root.get("path"))).resolve(strict=False)
                )
            except ValueError:
                continue
            return True
    for ancestor in (candidate, *candidate.parents):
        if not ancestor.is_dir() or not article_discovery._is_article_directory(ancestor):
            continue
        try:
            if article_discovery._primary_manuscript(ancestor) is not None:
                return True
        except OSError:
            continue
    return False


def get_article_record(article_id: str) -> dict[str, Any]:
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article address: {article_id}")
    record = get_registry_store().get(ARTICLE_COLLECTION, article_id)
    if record is None:
        raise KeyError(article_id)
    return _validated_article(article_id, record)


def _article_root_role(path: str) -> str:
    lowered = path.casefold()
    if "arxiv" in lowered:
        return "arxiv"
    if any(token in lowered for token in ("camera_ready", "camera-ready", "submission")):
        return "submission"
    return "canonical"


def _select_root(roots: list[dict[str, Any]]) -> dict[str, Any]:
    rank = {"canonical": 0, "submission": 1, "arxiv": 2, "variant": 3}
    available = [item for item in roots if Path(stripped_text(item.get("path"))).is_dir()]
    pool = available or roots
    if not pool:
        raise FileNotFoundError("article has no manuscript root")
    return min(
        pool,
        key=lambda item: (
            rank.get(stripped_text(item.get("role")), 9),
            len(stripped_text(item.get("path"))),
            stripped_text(item.get("path")).casefold(),
        ),
    )


def _root_entry(item: dict[str, Any]) -> dict[str, Any]:
    path = registry_sync.resolved_path(item["path"])
    return {
        "path": path,
        "role": _article_root_role(path),
        "present": Path(path).is_dir(),
        "primary_file": stripped_text(item.get("primary_file")),
        "title": stripped_text(item.get("title")),
        "title_fingerprint": stripped_text(item.get("title_fingerprint")),
    }


def _merge_record(
    existing: dict[str, Any] | None,
    article_id: str,
    group: list[dict[str, Any]],
    explicit_project_ids: Iterable[str] = (),
) -> dict[str, Any]:
    previous = dict(existing or {})
    roots = registry_sync.merge_locations(previous.get("roots") or [], map(_root_entry, group))
    canonical = _select_root(roots)
    title = stripped_text(canonical.get("title")) or stripped_text(previous.get("title")) or article_id
    project_ids = article_tex._dedupe(
        [
            *previous.get("project_ids", []),
            *explicit_project_ids,
            *[project for item in group for project in item.get("project_ids") or []],
        ]
    )
    fingerprints = article_tex._dedupe(
        [
            *previous.get("title_fingerprints", []),
            article_tex.title_fingerprint(previous.get("title")),
            *[item.get("title_fingerprint") for item in roots],
        ]
    )
    now = utc_now_iso()
    return {
        "schema": REGISTRY_SCHEMA,
        "id": article_id,
        "type": "article",
        "title": title,
        "status": stripped_text(previous.get("status")) or "active",
        "project_ids": project_ids,
        "canonical_root": stripped_text(canonical.get("path")),
        "primary_file": stripped_text(canonical.get("primary_file")),
        "roots": roots,
        "title_fingerprints": fingerprints,
        "created_at": stripped_text(previous.get("created_at")) or now,
        "updated_at": now,
    }


def _article_source_context(
    article_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    record = get_article_record(article_id)
    root_item = _select_root(
        [item for item in record.get("roots") or [] if isinstance(item, dict)]
    )
    article_root = Path(stripped_text(root_item.get("path"))).resolve(strict=False)
    if not article_root.is_dir():
        raise FileNotFoundError(article_root)
    return record, root_item, article_root


def _article_target_path(
    article_id: str,
    raw_path: str,
    *,
    allow_root: bool = False,
) -> tuple[Path, Path]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    if allow_root and not stripped_text(raw_path):
        return article_root, article_root
    relative_path = article_tex._safe_relative_path(stripped_text(raw_path))
    target = (article_root / relative_path).resolve(strict=False)
    try:
        target.relative_to(article_root)
    except ValueError as exc:
        raise ValueError("article path escapes the article root") from exc
    return article_root, target
