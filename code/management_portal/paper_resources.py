"""Papers as Portal resources: resource records, identity scans, addresses and deletion."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from management_common.secure_paths import relative_parts
from management_portal import paper_state_store
from management_portal.paper_records import _now
from management_portal.paper_reviews import _all_reviews, _announce_review_changes
from management_portal.paper_store import LOCK, PAPER_STORE, PaperStore, _paper_by_id, _validated_paper_id
from management_portal.resources import get_resource_service
from management_portal.resources.portal import portal_url_for_address


def _paper_store_for_root(paper_root: str | Path | None = None) -> PaperStore:
    if paper_root is None:
        return PAPER_STORE
    root = Path(paper_root).expanduser().resolve(strict=False)
    if root == PAPER_STORE.root.expanduser().resolve(strict=False):
        return PAPER_STORE
    return PaperStore(root)


def scan_and_clean_paper_ids(
    *,
    paper_root: str | Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    store = _paper_store_for_root(paper_root)
    changes: list[tuple[Path, dict]] = []
    invalid: list[dict[str, str]] = []
    collisions: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    unchanged_count = 0
    paper_count = 0

    for path in store.manifest_paths():
        manifest = store.read_manifest(path)
        paper_id = str(manifest.get("paper_id") or "").strip()
        if not paper_id:
            invalid.append({"path": str(path), "error": "paper_id required"})
            continue
        paper_count += 1
        try:
            paper_id = _validated_paper_id(paper_id)
        except ValueError as exc:
            invalid.append({"path": str(path), "paper_id": paper_id, "error": str(exc)})
            continue
        previous = seen.get(paper_id)
        if previous:
            collisions.append(
                {"paper_id": paper_id, "path": str(path), "conflicts_with": previous}
            )
            continue
        seen[paper_id] = str(path)
        unchanged_count += 1

    if apply and (invalid or collisions):
        raise ValueError(
            f"Paper identity update blocked: {len(invalid)} invalid manifest(s), "
            f"{len(collisions)} collision(s)"
        )
    return {
        "schema": "tfi.paper_identity_scan.v1",
        "apply": bool(apply),
        "paper_root": str(store.root),
        "paper_count": paper_count,
        "changed_count": len(changes),
        "unchanged_count": unchanged_count,
        "invalid_count": len(invalid),
        "collision_count": len(collisions),
        "invalid": invalid,
        "collisions": collisions,
    }


def _paper_resource_record(paper: dict) -> dict[str, Any]:
    paper_id = _validated_paper_id(paper.get("id"))
    return {
        "id": paper_id,
        "type": "paper",
        "paper_id": paper_id,
        "title": str(paper.get("title") or "").strip(),
        "year": str(paper.get("year") or "").strip(),
        "venue": str(paper.get("venue") or "").strip(),
        "status": str(paper.get("status") or "").strip(),
        "root": str(paper.get("_store_root") or paper.get("folder") or "").strip(),
    }


def load_paper_resources(paper_root: str | Path | None = None) -> list[dict[str, Any]]:
    store = _paper_store_for_root(paper_root)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paper in store.all_papers():
        record = _paper_resource_record(paper)
        if record["id"] in seen:
            raise ValueError(f"duplicate Paper resource ID: {record['id']}")
        seen.add(record["id"])
        records.append(record)
    return records


def _safe_paper_relative_path(raw_path: str) -> str:
    decoded = unquote(str(raw_path or ""))
    if not decoded or "\x00" in decoded or "\n" in decoded or "\r" in decoded:
        raise ValueError("invalid paper-relative path")
    return "/".join(relative_parts(PurePosixPath(decoded).as_posix()))


def resolve_paper_address(
    address: str,
    *,
    paper_root: str | Path | None = None,
) -> dict[str, Any]:
    clean_address = unquote(str(address or "").strip()).strip("/")
    root_id, separator, suffix = clean_address.partition("/")
    if not get_resource_service().is_valid_id(root_id, "paper"):
        raise ValueError(f"invalid Paper address: {address}")
    store = _paper_store_for_root(paper_root)
    paper = store.paper_by_id(root_id)
    if not paper:
        raise KeyError(root_id)
    paper_id = str(paper.get("id") or "").strip()
    paper_root_path = Path(
        str(paper.get("_store_root") or paper.get("folder") or store.root)
    ).resolve(strict=False)
    record = _paper_resource_record(paper)
    result: dict[str, Any] = {
        "schema": "tfi.resource_resolution.v2",
        "address": clean_address,
        "root_id": root_id,
        "resource_type": "paper",
        "target_kind": "paper",
        "record": record,
        "paper_id": paper_id,
        "paper_root": str(paper_root_path),
        "portal_url": portal_url_for_address(paper_id, "paper"),
        "exists": paper_root_path.is_dir(),
    }
    if not separator:
        return result
    parts = suffix.split("/")
    if len(parts) >= 2 and parts[0] == "file":
        relative_path = _safe_paper_relative_path("/".join(parts[1:]))
        target = (paper_root_path / relative_path).resolve(strict=False)
        try:
            target.relative_to(paper_root_path)
        except ValueError as exc:
            raise ValueError("paper-relative path escapes the Paper package") from exc
        result.update(
            {
                "target_kind": "file",
                "relative_path": relative_path,
                "resolved_path": str(target),
                "exists": target.is_file(),
            }
        )
        return result
    raise ValueError(f"unsupported nested Paper address: {address}")


def delete_paper(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        paper = _paper_by_id(paper_id)
        if not paper:
            raise KeyError("paper not found")
        from .workbench_projections.resource_lifecycle import release_resource
        release_resource(paper_id, actor=actor)
        for review in _all_reviews():
            if str(review.get('paper_id') or '') == paper_id:
                release_resource(review['review_id'], actor=actor)
        store_deleted = PAPER_STORE.delete_paper(paper_id)
        kinds = ("review_jobs", "reviews", "notes", "deep_read_jobs", "exp_jobs")
        with paper_state_store.locked(*kinds):
            for kind in kinds:
                paper_state_store.delete_paper(kind, paper_id)
    # The package's review files went with it.
    _announce_review_changes()
    return {
        "deleted": True,
        "paper_id": paper_id,
        "title": str(paper.get("title") or ""),
        "deleted_by": actor,
        "deleted_at": _now(),
        "store": store_deleted or {},
    }
