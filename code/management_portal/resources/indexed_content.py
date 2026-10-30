"""Addresses and bytes of indexed resources: a record's own file, or a file under its content root.

Knowledge, indexed records and the Lineage, Resident and Family providers
resolve and read through these; the Repo and Article backends add their own
kinds (``git_file``, ``article_section``) on top
(``resource_identity_backend.read_resolved_resource_content``).
"""
from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from management_common.text_values import stripped_text


def _safe_repo_relative_path(raw_path: str) -> str:
    decoded = unquote(raw_path)
    if not decoded or "\x00" in decoded or "\n" in decoded or "\r" in decoded:
        raise ValueError("invalid repo-relative path")
    pure = PurePosixPath(decoded)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("repo-relative path must not escape the checkout")
    return pure.as_posix()


def resolve_indexed_resource_address(
    clean_address: str,
    *,
    actor: str = "",
    resource_service: Any,
) -> dict[str, Any]:
    root_id, separator, suffix = clean_address.partition("/")
    record = resource_service.get(root_id, actor=actor)
    if "knowledge" in set(record.get("facets") or []):
        from knowledge_board_lan import knowledge_backend

        return knowledge_backend.resolve_knowledge_address(clean_address, actor=actor)
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    attributes = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    deleted = any(
        stripped_text(value.get("status")) == "deleted" or stripped_text(value.get("deleted_at"))
        for value in (record, attributes)
    )
    source_path = Path(stripped_text(source.get("path"))).expanduser().resolve(strict=False)
    result: dict[str, Any] = {
        "schema": "tfi.resource_resolution.v2",
        "address": clean_address,
        "root_id": root_id,
        "resource_type": stripped_text(record.get("resource_type")),
        "target_kind": stripped_text(record.get("resource_type")) or "resource",
        "record": record,
        "portal_url": stripped_text(record.get("url")),
        "resolved_path": str(source_path) if stripped_text(source.get("path")) else "",
        "exists": not deleted and (source_path.exists() if stripped_text(source.get("path")) else True),
    }
    if not separator:
        return result

    parts = suffix.split("/")
    if len(parts) < 2 or parts[0] != "file":
        raise ValueError(f"unsupported nested resource address: {clean_address}")
    raw_root = stripped_text(attributes.get("content_root"))
    if not raw_root:
        raise ValueError(f"resource does not expose nested files: {root_id}")
    content_root = Path(raw_root).expanduser().resolve(strict=False)
    relative_path = _safe_repo_relative_path("/".join(parts[1:]))
    target = (content_root / relative_path).resolve(strict=False)
    try:
        target.relative_to(content_root)
    except ValueError as exc:
        raise ValueError("resource-relative path escapes the content root") from exc
    result.update(
        {
            "target_kind": "file",
            "relative_path": relative_path,
            "resolved_path": str(target),
            "exists": not deleted and target.is_file(),
        }
    )
    return result


def read_resolved_resource_content(
    resolved: dict[str, Any],
    *,
    address: str = "",
    actor: str = "",
    max_bytes: int = 4 * 1024 * 1024,
) -> tuple[bytes, str, dict[str, Any]]:
    """The bytes of a resolved file: the target file, or the record's own file."""
    kind = resolved["target_kind"]
    resolved_path = stripped_text(resolved.get("resolved_path"))
    if kind == "file" or (resolved_path and Path(resolved_path).is_file()):
        path = Path(resolved_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size > max_bytes:
            raise ValueError(f"resource exceeds {max_bytes} byte content limit")
        data = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if content_type == "application/x-tex":
            content_type = "text/x-tex"
        return data, content_type, resolved
    raise ValueError(f"{resolved.get('resource_type') or 'resource'} root has no byte content")
