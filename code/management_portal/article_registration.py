"""Registering article directories: scanning roots into the registry, registering one
directory with its first build settings, and unregistering an article.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Iterable

from management_common.text_values import stripped_text
from management_portal import article_discovery, article_registry, article_settings
from management_portal.resources import get_resource_service
from management_portal.resources.registry_store import (
    announce_registry_changes,
    get_registry_store,
)


def scan_and_sync_article_registry(
    roots: Iterable[str | Path] | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    article_roots, skipped = article_discovery.discover_article_roots(roots)
    groups = article_discovery.group_logical_articles(article_roots)
    report = article_registry.ARTICLE_REGISTRY.sync(groups, merge=article_registry._merge_record, apply=apply)
    report.update(
        {
            "article_root_count": len(article_roots),
            "skipped_count": len(skipped),
            "skipped": skipped,
        }
    )
    return report


def register_article(
    path: str | Path,
    *,
    project_ids: Iterable[str] = (),
    article_id: str = "",
    primary_file: str = "",
    compiler: str = "",
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    item = article_discovery.inspect_article_root(path, primary_file=primary_file)
    report = article_registry.ARTICLE_REGISTRY.sync(
        [[item]],
        merge=functools.partial(article_registry._merge_record, explicit_project_ids=tuple(project_ids)),
        apply=True,
        forced_id=article_id,
    )
    changed = [*report["created"], *report["updated"]]
    if changed:
        record = changed[0]
        _apply_build_settings(record, item, created=bool(report["created"]), primary_file=primary_file,
                              compiler=compiler, build_root=build_root)
        return record
    resolved_path = str(Path(path).resolve(strict=False))
    for record in article_registry.load_article_records():
        if any(
            isinstance(root, dict)
            and str(Path(stripped_text(root.get("path"))).resolve(strict=False)) == resolved_path
            for root in record.get("roots") or []
        ):
            _apply_build_settings(record, item, created=False, primary_file=primary_file,
                                  compiler=compiler, build_root=build_root)
            return record
    raise KeyError(path)


def _apply_build_settings(record, item, *, created, primary_file, compiler, build_root) -> None:
    """A new article gets its first build settings; an article registered before keeps
    its own and changes only what the caller names, e.g. when a moved directory is re-registered."""
    if not str(item["primary_file"]).casefold().endswith(".tex"):
        return
    if created:
        settings = {
            "primary_file": item["primary_file"],
            "compiler": compiler or article_discovery._tex_program_hint(
                Path(item["path"]) / item["primary_file"]
            ) or "pdflatex",
        }
    else:
        settings = {key: value for key, value in (("primary_file", primary_file and item["primary_file"]),
                                                  ("compiler", compiler)) if value}
        if not settings:
            return
    article_settings.update_article_settings(str(record.get("id") or ""), settings, build_root=build_root)


def unregister_article(article_id: str) -> dict[str, Any]:
    """Remove an article registry record while preserving every manuscript file."""
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article id: {article_id}")
    with get_registry_store().writer(article_registry.ARTICLE_COLLECTION) as writer:
        stored = writer.get(article_registry.ARTICLE_COLLECTION, article_id)
        if stored is None:
            raise KeyError(article_id)
        record = article_registry._validated_article(article_id, stored)
        writer.delete(article_registry.ARTICLE_COLLECTION, article_id)
    announce_registry_changes(article_registry.ARTICLE_COLLECTION, {article_id: "deleted"}, resource_type="article")
    return {
        "id": article_id,
        "deleted": True,
        "manuscript_paths": [
            stripped_text(item.get("path"))
            for item in record.get("roots") or []
            if isinstance(item, dict) and stripped_text(item.get("path"))
        ],
    }
