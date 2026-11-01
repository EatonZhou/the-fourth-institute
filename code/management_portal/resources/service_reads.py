"""ResourceService reads: resolving addresses, reading content and cross-surface references."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .address import ResourceAddress
from .content_payload import resolved_media_type
from .contracts import normalize_resolution
from .file_references import FILE_REFERENCE_KEYS, file_reference_address, file_reference_for
from .providers import folder_files

if TYPE_CHECKING:
    from .service import ResourceService


def resolve(
    service: "ResourceService",
    address: str | Mapping[str, str],
    *,
    actor: str = "",
) -> dict[str, Any]:
    parsed = service.parse_address(address)
    if parsed.resource_type in FILE_REFERENCE_KEYS.values() and parsed.suffix and parsed.suffix[0] == "file":
        return _resolve_file(service, parsed, actor=actor)
    service._ensure_resolvers(parsed.resource_type)
    resolver = service._resolvers.resolver_for(parsed.resource_type)
    resolved = normalize_resolution(
        parsed,
        resolver.resolve(
            parsed.value,
            actor=str(actor or ""),
        ),
    )
    service._access.authorize_read(resolved, actor=str(actor or ""))
    if not parsed.suffix:
        canonical_url = service.portal_url_for_resource(
            parsed.root_id,
            parsed.resource_type,
            record=resolved.get("record"),
        )
        if canonical_url:
            # Root resource navigation is owned by the resource-type URL
            # provider, so domain resolver output cannot override it.
            resolved["portal_url"] = canonical_url
    return resolved


def _resolve_file(service, parsed, *, actor):
    # Root identity and authorization precede inspecting any relative filename.
    root = service.resolve(parsed.root_id, actor=actor)
    record = root["record"]
    attributes = record.get("attributes") or {}
    if (attributes.get("status") == "deleted" or attributes.get("deleted_at")
            or record.get("status") == "deleted" or record.get("deleted_at")):
        raise KeyError(parsed.root_id)
    parts = folder_files.file_parts("/".join(parsed.suffix))
    if parsed.resource_type == "workspace":
        from .providers.workspace import _root, authorize_file

        directory = _root(record)
        authorize_file(actor, parsed.root_id, directory, parts)
    else:
        directory = Path(root["checkout_path" if parsed.resource_type == "repo" else "article_root"])
    if not directory.is_absolute():
        raise ValueError("File reference requires a registered absolute directory")
    result = {**root, "address": parsed.value, "exists": True}
    result.pop("resolved_path", None)
    try:
        result = folder_files.describe(result, directory, parts, hide=parsed.resource_type == "workspace")
    except KeyError as exc:
        raise FileNotFoundError(parsed.value) from exc
    if not result["exists"]:
        raise FileNotFoundError(parsed.value)
    result["file_reference"] = file_reference_for(parsed.root_id, "/".join(parts))
    result = normalize_resolution(parsed, result)
    result["address"] = file_reference_address(result["file_reference"])
    return result


def _file_root(resolved):
    if resolved["resource_type"] == "workspace":
        return Path(resolved["record"]["attributes"]["path"])
    return Path(resolved["checkout_path" if resolved["resource_type"] == "repo" else "article_root"])


def read_content(
    service: "ResourceService",
    address: str | Mapping[str, str],
    *,
    actor: str = "",
    max_bytes: int = 4 * 1024 * 1024,
) -> tuple[bytes, str, dict[str, Any]]:
    parsed = service.parse_address(address)
    service._ensure_resolvers(parsed.resource_type)
    resolver = service._resolvers.resolver_for(parsed.resource_type)
    resolved = service.resolve(
        address,
        actor=actor,
    )
    if resolved.get("file_reference"):
        return folder_files.read_content(resolved, _file_root(resolved), max_bytes=max_bytes)
    if resolver.read_content is None:
        raise ValueError(f"{parsed.resource_type} resources expose no byte content")
    return resolver.read_content(
        resolved,
        address=parsed.value,
        actor=str(actor or ""),
        max_bytes=max_bytes,
    )


def open_content(
    service: "ResourceService",
    address: str | Mapping[str, str],
    *,
    actor: str = "",
    max_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any] | tuple[bytes, str, dict[str, Any]]:
    parsed = service.parse_address(address)
    service._ensure_resolvers(parsed.resource_type)
    resolver = service._resolvers.resolver_for(parsed.resource_type)
    resolved = service.resolve(address, actor=actor)
    if resolved.get("file_reference"):
        if resolved["target_kind"] == "file":
            return resolved
        return folder_files.read_content(resolved, _file_root(resolved), max_bytes=max_bytes)
    resolved_path = Path(str(resolved.get("resolved_path") or ""))
    if resolved_path.is_file() or (
        resolved.get("target_kind") == "file"
        and type(resolved.get("content_size")) is int
        and type(resolved.get("content_mtime_ns")) is int
        and resolver.read_content_range is not None
    ):
        return resolved
    if resolver.read_content is None:
        raise ValueError(f"{parsed.resource_type} resources expose no byte content")
    data, content_type, content_resolution = resolver.read_content(
        resolved,
        address=parsed.value,
        actor=str(actor or ""),
        max_bytes=max_bytes,
    )
    metadata = content_resolution if isinstance(content_resolution, Mapping) else {}
    return data, content_type, {**resolved, **metadata}


def read_content_range(
    service: "ResourceService",
    address: str | Mapping[str, str],
    *,
    start: int,
    end: int,
    actor: str = "",
) -> tuple[bytes, str, dict[str, Any]]:
    if type(start) is not int or type(end) is not int or start < 0 or end < start:
        raise ValueError("content range requires non-negative inclusive start and end")
    parsed = service.parse_address(address)
    service._ensure_resolvers(parsed.resource_type)
    resolver = service._resolvers.resolver_for(parsed.resource_type)
    resolved = service.resolve(
        address,
        actor=actor,
    )
    if resolved.get("file_reference"):
        return folder_files.read_content_range(resolved, _file_root(resolved), start=start, end=end)
    if resolver.read_content_range is None:
        raise ValueError(f"{parsed.resource_type} resources expose no ranged byte content")
    return resolver.read_content_range(
        resolved,
        address=parsed.value,
        actor=str(actor or ""),
        start=start,
        end=end,
    )


def reference(
    service: "ResourceService",
    address: str | Mapping[str, str],
    *,
    actor: str = "",
) -> dict[str, Any]:
    parsed = service.parse_address(address)
    resolved = service.resolve(
        address,
        actor=actor,
    )
    if resolved.get("exists") is False:
        raise KeyError(parsed.value)

    try:
        indexed = service.get(parsed.root_id, actor=actor)
    except (KeyError, RuntimeError, ValueError):
        indexed = {}
    candidate = (
        dict(resolved.get("record"))
        if isinstance(resolved.get("record"), Mapping)
        else {}
    )
    title, summary = _reference_text(parsed, resolved, indexed, candidate)
    attributes = (
        dict(indexed.get("attributes"))
        if isinstance(indexed.get("attributes"), Mapping)
        else {}
    )
    facets = [
        str(value)
        for value in indexed.get("facets") or []
        if str(value).strip()
    ]
    content_editable = _content_editable(service, parsed, actor)
    version = _reference_version(resolved, candidate, indexed, attributes)
    filename, media_type, content_kind, content_size = _reference_content(resolved, candidate)
    portal_url = _reference_portal_url(service, parsed, resolved, indexed, candidate)
    return {
        "address": resolved["address"] if resolved.get("file_reference") else parsed.value,
        **({"file_reference": resolved["file_reference"]} if resolved.get("file_reference") else {}),
        "resource_id": parsed.root_id,
        "resource_type": parsed.resource_type,
        "title": title or parsed.root_id,
        "filename": filename,
        "content_kind": content_kind,
        "media_type": media_type,
        "content_size": content_size,
        "summary": summary,
        "status": "resolved",
        "target_kind": str(resolved.get("target_kind") or "").strip(),
        "url": portal_url,
        "facets": facets,
        "attributes": attributes,
        "version": version,
        "capabilities": {
            "download": True,
            "edit": content_editable,
            "save": content_editable,
        },
    }


def _reference_text(
    parsed: ResourceAddress,
    resolved: Mapping[str, Any],
    indexed: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> tuple[str, str]:
    """The reference's title and summary."""
    if parsed.suffix:
        # A nested address names the child object, while the index still
        # describes its root.  Prefer target metadata supplied by the
        # domain resolver so every surface presents the addressed object.
        title = str(
            resolved.get("title")
            or candidate.get("title")
            or candidate.get("name")
            or candidate.get("label")
            or candidate.get("filename")
            or indexed.get("title")
            or parsed.root_id
        ).strip()
        summary = str(
            resolved.get("summary")
            or candidate.get("summary")
            or candidate.get("description")
            or indexed.get("summary")
            or ""
        ).strip()
    else:
        title = str(
            indexed.get("title")
            or candidate.get("title")
            or candidate.get("name")
            or candidate.get("label")
            or candidate.get("filename")
            or parsed.root_id
        ).strip()
        summary = str(
            indexed.get("summary")
            or candidate.get("summary")
            or candidate.get("description")
            or ""
        ).strip()
    return title, summary


def _content_editable(service: "ResourceService", parsed: ResourceAddress, actor: str) -> bool:
    """Whether the actor may save the content: the type takes content updates and the actor may change it."""
    type_capabilities = service.capabilities(parsed.resource_type)
    content_editable = bool(type_capabilities.get("content_updatable"))
    if content_editable:
        try:
            service._identity.validate(parsed.root_id, parsed.resource_type)
            service._mutation_providers.provider_for(parsed.resource_type)
            service._require_mutation_actor(actor)
        except (KeyError, PermissionError):
            content_editable = False
    return content_editable


def _reference_version(
    resolved: Mapping[str, Any],
    candidate: Mapping[str, Any],
    indexed: Mapping[str, Any],
    attributes: Mapping[str, Any],
) -> str:
    raw_version = (
        resolved.get("version")
        or candidate.get("version")
        or indexed.get("version")
        or attributes.get("version")
        or {}
    )
    version_value = (
        raw_version.get("number")
        if isinstance(raw_version, Mapping)
        else raw_version
    )
    return str(version_value or "").strip()


def _reference_content(
    resolved: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> tuple[str, str, str, int | None]:
    """The content's filename, media type, kind and size (None when unknown)."""
    filename = str(
        resolved.get("filename")
        or candidate.get("filename")
        or ""
    ).strip()
    if not filename:
        resolved_path = str(resolved.get("resolved_path") or "").strip()
        if resolved_path:
            filename = Path(resolved_path).name
    media_type = resolved_media_type(
        resolved,
        candidate,
        {"filename": filename},
    )
    content_kind = str(
        resolved.get("content_kind")
        or candidate.get("content_kind")
        or ""
    ).strip().lower()
    raw_content_size = resolved.get("content_size")
    if type(raw_content_size) is not int:
        raw_content_size = candidate.get("content_size")
    content_size = (
        raw_content_size
        if type(raw_content_size) is int and raw_content_size >= 0
        else None
    )
    return filename, media_type, content_kind, content_size


def _reference_portal_url(
    service: "ResourceService",
    parsed: ResourceAddress,
    resolved: Mapping[str, Any],
    indexed: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> str:
    def canonical_portal_url(value: object) -> str:
        from .portal import canonical_portal_location

        return canonical_portal_location(parsed.value, value)

    generated_portal_url = service.portal_url_for_resource(
        parsed.root_id,
        parsed.resource_type,
        record=resolved.get("record") or indexed,
    )
    return (
        (
            canonical_portal_url(resolved.get("portal_url"))
            if parsed.suffix
            else ""
        )
        or generated_portal_url
        or canonical_portal_url(resolved.get("portal_url"))
        or canonical_portal_url(indexed.get("url"))
        or canonical_portal_url(candidate.get("portal_url"))
        or canonical_portal_url(candidate.get("url"))
        or f"/r/{quote(parsed.value, safe='')}"
    )
