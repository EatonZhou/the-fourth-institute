from __future__ import annotations

from pathlib import Path
from typing import Any

from management_common.actor_identity import canonical_actor_id, same_actor

from . import resident_store
from .domain_ports import resident_domain_port


def authorize_attachment_read(
    path: Path,
    actor: str,
    *,
    root: Path,
) -> dict[str, Any]:
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise PermissionError("attachment read requires an authenticated actor")
    root = root.resolve(strict=False)
    path = path.resolve(strict=False)
    metadata_path = path.with_suffix(path.suffix + ".json")
    try:
        relative = path.relative_to(root)
        metadata_path.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise PermissionError("attachment path or metadata escapes upload root") from exc
    if not path.is_file():
        raise FileNotFoundError("attachment not found")
    metadata = resident_store.read_json(metadata_path, {})
    if not isinstance(metadata, dict) or not metadata:
        raise PermissionError("attachment metadata is missing")
    stored_id = str(metadata.get("file_id") or "").strip().lstrip("/")
    if stored_id and stored_id != relative.as_posix():
        raise PermissionError("attachment identity mismatch")
    resident_id = str(metadata.get("resident_id") or "").strip()
    if resident_id:
        try:
            identity = resident_domain_port.resolve_resident_identity(resident_id, actor_id)
        except (KeyError, PermissionError, ValueError) as exc:
            raise PermissionError("cannot read attachment's Conversation") from exc
        if identity.get("resident_id") != resident_id:
            raise PermissionError("cannot read attachment's Conversation")
    elif not same_actor(metadata.get("actor"), actor_id):
        from management_portal import permission_system

        if permission_system.owners_only():
            raise PermissionError("unbound attachment is private to its uploader")
    return metadata
