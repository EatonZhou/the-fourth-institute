from __future__ import annotations

from pathlib import Path
from stat import S_ISDIR
from typing import TYPE_CHECKING

from ..errors import ResourceOperationError
from . import folder_files

if TYPE_CHECKING:
    from ..service import ResourceService


def _root(record):
    raw = (record.get("attributes") or {}).get("path")
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ResourceOperationError("workspace_content_root_invalid", "Workspace has no valid registered path", 500)
    root = Path(raw)
    if not root.is_absolute() or len(root.parts) < 2 or ".." in root.parts:
        raise ResourceOperationError("workspace_content_root_invalid", "Workspace requires an absolute registered directory", 500)
    return root


def _resolve(service: "ResourceService", address, *, actor="", **_context):
    root_id, separator, suffix = address.partition("/")
    record = service.get(root_id, actor=actor)
    attributes = record.get("attributes") or {}
    exists = attributes.get("status") != "deleted" and not attributes.get("deleted_at")
    # get() checks current descriptor lifecycle and the Resource facade. File
    # traversal remains pinned below.
    result = {
        "address": address,
        "root_id": root_id,
        "resource_type": "workspace",
        "target_kind": "workspace",
        "record": record,
        "exists": bool(exists),
        "portal_url": record.get("url", ""),
    }
    if not separator:
        try:
            path = Path(str(attributes.get("path") or ""))
            result["directory_status"] = "available" if S_ISDIR(path.stat().st_mode) else "not_directory"
        except FileNotFoundError:
            result["directory_status"] = "missing"
        except OSError:
            result["directory_status"] = "unavailable"
        return result
    parts = folder_files.file_parts(suffix)
    if parts is None:
        raise ValueError("Workspace file address requires <workspace-id>/file/<relative-path>")
    from management_portal.society_service.lineage_access_policy import workspace_reaches

    # Before any file is touched, so a denied read looks like a missing one.
    root = _root(record)
    file_path = str(root.joinpath(*parts))
    if not workspace_reaches(actor, root_id, write=False, path=file_path):
        raise KeyError(address)
    # Sensitive paths (.env, secrets/, ...) read as missing, as in the Lineage Folder.
    return folder_files.describe(result, root, parts, hide=True)


def _read_content(resolved, *, max_bytes=4 * 1024 * 1024, **_context):
    if resolved["target_kind"] not in {"file", "directory"}:
        raise ValueError("Workspace root has no byte content; use <workspace-id>/file/<relative-path>")
    return folder_files.read_content(resolved, _root(resolved["record"]), max_bytes=max_bytes)


def _read_content_range(resolved, *, start, end, **_context):
    if resolved["target_kind"] != "file":
        raise ValueError("Workspace root has no byte content; use <workspace-id>/file/<relative-path>")
    return folder_files.read_content_range(resolved, _root(resolved["record"]), start=start, end=end)


def install_resolver(service: "ResourceService") -> None:
    service.register_resolver(
        "workspace", lambda address, **context: _resolve(service, address, **context),
        read_content=_read_content,
        read_content_range=_read_content_range,
    )


def install_mutation_provider(service: "ResourceService") -> None:
    from management_portal import permission_system
    from management_portal.society_service.lineage_access_policy import hidden_private_ids
    from ..collaboration_registry import descriptor_resource_record
    from ..workspace_store import get_workspace_store

    def update(resource_id, payload, *, actor=""):
        unknown = set(payload) - {"title", "summary", "expected_version"}
        if unknown:
            raise ValueError("Workspace update only accepts title, summary and expected_version: " + ", ".join(sorted(unknown)))
        if "title" in payload and payload["title"] is None:
            raise ValueError("Workspace title must be a nonempty string")
        if "summary" in payload and payload["summary"] is None:
            raise ValueError("Workspace summary must be text")

        def authorize(descriptor):
            record = descriptor_resource_record(descriptor)
            if hidden_private_ids(actor, [record]):
                raise KeyError(resource_id)
            if resource_id not in permission_system.permitted_ids(actor, "UpdateResource", "resource_id", [resource_id]):
                raise PermissionError("Workspace update is not permitted")

        descriptor = get_workspace_store().update_metadata(
            resource_id, title=payload.get("title"), summary=payload.get("summary"),
            expected_version=payload.get("expected_version"), authorize=authorize,
        )
        return descriptor_resource_record(descriptor)

    service.register_mutation_provider("workspace", update=update)
