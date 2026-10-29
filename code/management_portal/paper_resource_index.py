"""Paper-owned resource index adapters; loaded only by the Paper feature hook."""
from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path
from typing import Any, Iterable

import yaml

from management_common.scan_cancellation import check_scan_cancelled, walk_paths
from management_common.text_values import stripped_text
from management_portal import paper_backend
from management_portal.resource_index_backend import ChangeSet, RESOURCE_IDENTITY, _PathsAdapter, _dedupe, _known_path, _path_modified_at, _resolved_path, _resolved_root, _watch_tree, resource_record
from management_portal.resources.schema import resource_relation

# Snapshot the loaded projection; later disk edits take effect after reload.
PROJECTION_CODE_FINGERPRINT = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


_PAPER_ATTRIBUTE_FIELDS = {
    "authors",
    "venue",
    "year",
    "session",
    "tags",
    "project_ids",
    "workload",
    "platform",
    "architecture",
    "optimization",
    "status",
    "created_by",
    "created_at",
    "updated_at",
    "has_pdf",
    "has_analysis",
    "has_summary",
    "category_tags",
    "tag_count",
}


def _paper_store_path(path: Path) -> Path:
    """A real path under the Paper items root, written as a full scan finds
    it (through the configured, possibly symlinked, items root)."""
    items_root = Path(paper_backend.PAPER_STORE.items_root)
    try:
        return items_root / path.relative_to(_resolved_root(items_root))
    except ValueError:
        return path


def _paper_watch_directories() -> list[Path]:
    root = Path(paper_backend.PAPER_STORE.items_root)
    directories = set(_watch_tree(root, max_depth=2))
    for paper_dir in tuple(directories):
        try:
            depth = len(paper_dir.relative_to(root).parts)
        except ValueError:
            continue
        reviews = paper_dir / "reviews"
        if depth == 2 and reviews.is_dir():
            directories.add(reviews.resolve(strict=False))
    return sorted(directories, key=str)


class PaperManifestAdapter(_PathsAdapter):
    name = "paper"
    resource_types = frozenset({"paper"})
    protect_existing_sources = True

    @property
    def roots(self) -> tuple[Path, ...]:
        return (Path(paper_backend.PAPER_STORE.items_root),)

    def source_scope(self) -> dict[str, Any]:
        """Each package's manifest and the files named beside it (packages
        live at items/<year>/<package>)."""
        return {
            "trees": [{
                "root": str(_resolved_root(self.roots[0])),
                "max_depth": 2,
                "files": ["manifest.json", "paper.pdf", "analysis.md", "summary.md"],
            }],
            "files": [],
        }

    def watch_directories(self) -> list[Path]:
        return _paper_watch_directories()

    def _record_for_paper(self, paper: dict[str, Any]) -> dict[str, Any]:
        paper_id = stripped_text(paper.get("id"))
        manifest_path = stripped_text(paper.get("_manifest_path"))
        attributes = {
            key: value for key, value in paper.items() if key in _PAPER_ATTRIBUTE_FIELDS
        }
        search_terms: list[object] = [
            paper.get("slug"),
            paper.get("authors"),
            paper.get("venue"),
            paper.get("year"),
            paper.get("abstract"),
        ]
        for key in (
            "tags",
            "project_ids",
            "workload",
            "platform",
            "architecture",
            "optimization",
        ):
            search_terms.extend(paper.get(key) or [])
        return resource_record(
            resource_id=paper_id,
            resource_type="paper",
            title=paper.get("title") or paper_id,
            summary=paper.get("abstract"),
            adapter=self.name,
            path=manifest_path,
            modified_at=_path_modified_at(manifest_path, paper.get("updated_at")),
            facets=["paper"],
            attributes=attributes,
            search_terms=search_terms,
        )

    def _record_for_manifest(self, manifest_path: Path) -> dict[str, Any]:
        with paper_backend.LOCK:
            manifest = paper_backend.PAPER_STORE.read_manifest(manifest_path)
            if not manifest:
                raise ValueError(f"invalid Paper manifest: {manifest_path}")
            paper = paper_backend._paper_public(
                paper_backend.PAPER_STORE.manifest_to_paper(manifest, manifest_path)
            )
        return self._record_for_paper(paper)

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        records: list[dict[str, Any]] = []
        resource_errors: list[dict[str, str]] = []
        for manifest_path in paper_backend.PAPER_STORE.manifest_paths():
            check_scan_cancelled()
            try:
                records.append(self._record_for_manifest(manifest_path))
            except Exception as exc:
                resource_errors.append(
                    {
                        "path": str(manifest_path),
                        "error": str(exc)[:1000],
                    }
                )
        return records, {
            "manifest_count": len(records),
            "resource_errors": resource_errors,
        }

    def scan_changes(
        self,
        paths: Iterable[str],
        previous: Iterable[dict[str, Any]],
        _source_state: dict[str, Any],
    ) -> ChangeSet:
        changed_paths = tuple(paths)
        items_root = _resolved_root(self.roots[0])
        previous_by_manifest = {
            _known_path(stripped_text((record.get("source") or {}).get("path"))): record
            for record in previous
        }
        manifests_by_package = {
            manifest_path.parent: manifest_path for manifest_path in previous_by_manifest
        }
        manifests: set[Path] = set()
        for raw_path in changed_paths:
            path = _known_path(raw_path)
            try:
                path.relative_to(items_root)
            except ValueError:
                continue
            for candidate in (path, *path.parents):
                if candidate in manifests_by_package:
                    manifests.add(manifests_by_package[candidate])
            cursor = path if path.is_dir() else path.parent
            if path.name == "manifest.json":
                manifests.add(path)
            while cursor != items_root and items_root in cursor.parents:
                candidate = cursor / "manifest.json"
                if candidate.is_file():
                    manifests.add(candidate.resolve(strict=False))
                    break
                cursor = cursor.parent

        upserts: list[dict[str, Any]] = []
        deletes: list[str] = []
        resource_errors: list[dict[str, str]] = []
        for manifest_path in sorted(manifests, key=str):
            previous_record = previous_by_manifest.get(manifest_path)
            if not manifest_path.is_file():
                if previous_record:
                    deletes.append(stripped_text(previous_record.get("resource_id")))
                continue
            try:
                upserts.append(self._record_for_manifest(_paper_store_path(manifest_path)))
            except Exception as exc:
                resource_errors.append(
                    {
                        "resource_id": stripped_text(
                            (previous_record or {}).get("resource_id")
                        ),
                        "path": str(manifest_path),
                        "error": str(exc)[:1000],
                    }
                )
        return ChangeSet(
            adapter=self.name,
            upserts=tuple(upserts),
            deletes=tuple(deletes),
            source_state={
                "last_incremental_path_count": len(changed_paths),
                "resource_errors": resource_errors,
            },
        )


class PaperReviewAdapter(_PathsAdapter):
    name = "paper_review"
    resource_types = frozenset({"review"})
    protect_existing_sources = True

    @staticmethod
    def _markdown_paths() -> list[Path]:
        items_root = Path(paper_backend.PAPER_STORE.items_root)
        if not items_root.is_dir():
            return []
        return sorted(
            (path for path in walk_paths(items_root)
             if path.parent.name == "reviews" and fnmatch.fnmatchcase(path.name, "review_*.md")),
            key=str,
        )

    def source_scope(self) -> dict[str, Any]:
        """Every package's reviews/review_*.md.

        The stored review list is a database table; its writers ask for a
        rescan (paper_backend._announce_review_changes).
        """
        return {
            "trees": [{
                "root": str(_resolved_root(paper_backend.PAPER_STORE.items_root)),
                "max_depth": 3,
                "only": {"3": ["reviews"]},
                "files": ["review_*.md"],
            }],
        }

    def watch_directories(self) -> list[Path]:
        return sorted((path for path in _paper_watch_directories() if path.is_dir()), key=str)

    def owns_path(self, path: Path) -> bool:
        resolved = _resolved_path(path)
        items_root = _resolved_root(paper_backend.PAPER_STORE.items_root)
        return resolved == items_root or items_root in resolved.parents

    @staticmethod
    def _markdown_review(path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        metadata: dict[str, Any] = {}
        body_start = 0
        if lines and lines[0].strip() == "---":
            end = next(
                (index for index, line in enumerate(lines[1:400], start=1) if line.strip() == "---"),
                None,
            )
            if end is not None:
                loaded = yaml.safe_load("\n".join(lines[1:end])) or {}
                if not isinstance(loaded, dict):
                    raise ValueError(f"invalid Paper review frontmatter: {path}")
                metadata = dict(loaded)
                body_start = end + 1
        review_id = stripped_text(metadata.get("review_id"))
        paper_id = stripped_text(metadata.get("paper_id"))
        RESOURCE_IDENTITY.validate(review_id, "review")
        RESOURCE_IDENTITY.validate(paper_id, "paper")

        body = lines[body_start:]
        title = next(
            (line[2:].strip() for line in body if line.startswith("# ") and line[2:].strip()),
            review_id,
        )
        summary_lines: list[str] = []
        started = False
        for line in body:
            clean = line.strip()
            if not started:
                if not clean or clean.startswith("#"):
                    continue
                started = True
            if not clean or clean.startswith("#") or clean.startswith("```"):
                if summary_lines:
                    break
                continue
            summary_lines.append(clean)
        return {
            **metadata,
            "review_id": review_id,
            "paper_id": paper_id,
            "title": title,
            "summary": " ".join(summary_lines),
            "_path": path,
        }

    def _record_for_review(self, review: dict[str, Any], path: Path) -> dict[str, Any]:
        review_id = stripped_text(review.get("review_id"))
        paper_id = stripped_text(review.get("paper_id"))
        RESOURCE_IDENTITY.validate(review_id, "review")
        RESOURCE_IDENTITY.validate(paper_id, "paper")
        if not path.is_file():
            raise ValueError(f"Paper review source is missing: {path}")
        paper = paper_backend._paper_by_id(paper_id) or {}
        if not paper:
            raise ValueError(f"Paper review references a missing Paper: {paper_id}")
        return resource_record(
            resource_id=review_id,
            resource_type="review",
            title=review.get("title") or review_id,
            summary=review.get("summary"),
            adapter=self.name,
            path=path,
            modified_at=_path_modified_at(path, review.get("created_at")),
            facets=["review", "paper"],
            attributes={
                "project_ids": list(paper.get("project_ids") or []),
                "created_by": stripped_text(review.get("created_by")),
                "relations": [resource_relation("review_of", paper_id)],
                "status": stripped_text(review.get("status")),
                "created_at": review.get("created_at") or 0,
                "paper_id": paper_id,
                "overall_recommendation": stripped_text(review.get("overall_recommendation")),
                "confidence": stripped_text(review.get("confidence")),
            },
            search_terms=[
                paper_id,
                paper.get("title"),
                review.get("overall_recommendation"),
                review.get("confidence"),
            ],
        )

    def scan_changes(
        self,
        paths: Iterable[str],
        previous: Iterable[dict[str, Any]],
        _source_state: dict[str, Any],
    ) -> ChangeSet | None:
        """Re-read the changed review files.

        A review ID that another file or the stored review list places
        elsewhere needs the whole scan.
        """
        items_root = _resolved_root(paper_backend.PAPER_STORE.items_root)
        previous_records = list(previous)
        previous_by_path = {
            _known_path(source_path): record
            for record in previous_records
            if (source_path := stripped_text((record.get("source") or {}).get("path")))
        }
        previous_by_id = {stripped_text(record.get("resource_id")): record for record in previous_records}
        markdown_paths: set[Path] = set()
        for raw_path in paths:
            path = _known_path(raw_path)
            if (
                path.parent.name == "reviews"
                and fnmatch.fnmatchcase(path.name, "review_*.md")
                and items_root in path.parents
            ):
                markdown_paths.add(path)
        upserts: list[dict[str, Any]] = []
        deletes: list[str] = []
        resource_errors: list[dict[str, str]] = []
        with paper_backend.LOCK:
            live_reviews: dict[str, dict[str, Any]] | None = None
            for path in sorted(markdown_paths, key=str):
                previous_record = previous_by_path.get(path)
                previous_id = stripped_text((previous_record or {}).get("resource_id"))
                if not path.is_file():
                    if previous_id:
                        deletes.append(previous_id)
                    continue
                try:
                    review = self._markdown_review(path)
                except Exception as exc:
                    resource_errors.append(
                        {"resource_id": previous_id, "path": str(path), "error": str(exc)[:1000]}
                    )
                    continue
                review_id = stripped_text(review.get("review_id"))
                owner = previous_by_id.get(review_id)
                if owner is not None and owner is not previous_record:
                    return None
                if live_reviews is None:
                    live_reviews = {
                        stripped_text(item.get("review_id")): dict(item)
                        for item in paper_backend._all_reviews()
                        if isinstance(item, dict)
                    }
                live = live_reviews.get(review_id)
                record_path = _paper_store_path(path)
                if live is not None:
                    try:
                        live_path = paper_backend._review_markdown_path(live)
                    except Exception as exc:
                        # As in the full scan, the file alone stands for it.
                        resource_errors.append(
                            {"resource_id": review_id, "path": str(path), "error": str(exc)[:1000]}
                        )
                    else:
                        if live_path.resolve(strict=False) != path:
                            return None
                        record_path = live_path
                        review = {**review, **live}
                try:
                    upserts.append(self._record_for_review(review, record_path))
                except Exception as exc:
                    resource_errors.append(
                        {"resource_id": review_id, "path": str(path), "error": str(exc)[:1000]}
                    )
                    continue
                if previous_id and previous_id != review_id:
                    deletes.append(previous_id)
        return ChangeSet(
            adapter=self.name,
            upserts=tuple(upserts),
            deletes=tuple(_dedupe(deletes)),
            source_state={
                "last_incremental_path_count": len(markdown_paths),
                "resource_errors": resource_errors,
            },
        )

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        with paper_backend.LOCK:
            live_reviews = [
                dict(item)
                for item in paper_backend._all_reviews()
                if isinstance(item, dict)
            ]
            reviews_by_id: dict[str, tuple[dict[str, Any], Path]] = {}
            rejected_ids: set[str] = set()
            resource_errors: list[dict[str, str]] = []
            markdown_paths = self._markdown_paths()
            for path in markdown_paths:
                check_scan_cancelled()
                try:
                    review = self._markdown_review(path)
                except Exception as exc:
                    resource_errors.append(
                        {"path": str(path), "error": str(exc)[:1000]}
                    )
                    continue
                review_id = stripped_text(review.get("review_id"))
                if review_id in reviews_by_id:
                    reviews_by_id.pop(review_id, None)
                    rejected_ids.add(review_id)
                    resource_errors.append(
                        {
                            "resource_id": review_id,
                            "path": str(path),
                            "error": f"duplicate Paper review ID: {review_id}",
                        }
                    )
                    continue
                if review_id in rejected_ids:
                    continue
                reviews_by_id[review_id] = (review, path)
            for review in live_reviews:
                check_scan_cancelled()
                review_id = stripped_text(review.get("review_id"))
                try:
                    RESOURCE_IDENTITY.validate(review_id, "review")
                    path = paper_backend._review_markdown_path(review)
                except Exception as exc:
                    resource_errors.append(
                        {
                            "resource_id": review_id,
                            "error": str(exc)[:1000],
                        }
                    )
                    continue
                if review_id in rejected_ids:
                    continue
                persisted = reviews_by_id.get(review_id)
                if persisted and persisted[1].resolve(strict=False) != path.resolve(strict=False):
                    reviews_by_id.pop(review_id, None)
                    rejected_ids.add(review_id)
                    resource_errors.append(
                        {
                            "resource_id": review_id,
                            "path": str(path),
                            "error": f"duplicate Paper review ID: {review_id}",
                        }
                    )
                    continue
                merged = {**(persisted[0] if persisted else {}), **review}
                reviews_by_id[review_id] = (merged, path)
            records: list[dict[str, Any]] = []
            for review, path in reviews_by_id.values():
                check_scan_cancelled()
                try:
                    records.append(self._record_for_review(review, path))
                except Exception as exc:
                    resource_errors.append(
                        {
                            "resource_id": stripped_text(review.get("review_id")),
                            "path": str(path),
                            "error": str(exc)[:1000],
                        }
                    )
        return records, {
            "review_count": len(records),
            "live_review_count": len(live_reviews),
            "markdown_review_count": len(markdown_paths),
            "resource_errors": resource_errors,
        }


ADAPTERS = (PaperManifestAdapter, PaperReviewAdapter)
