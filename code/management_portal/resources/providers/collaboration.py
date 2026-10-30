from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from . import folder_files
from .common import canonical_record, canonical_resolution

if TYPE_CHECKING:
    from ..service import ResourceService


READABLE_TYPES = frozenset({"resident", "lineage", "family"})


def _resolution(service: "ResourceService", address: object, **context: Any):
    from .. import indexed_content

    clean_address = str(address or "").strip()
    root_id, _, suffix = clean_address.partition("/")
    if root_id.startswith("lineage_") and suffix.startswith("program/"):
        from management_portal.society_service import get_society_service
        from management_portal.society_service.lineage_program_addresses import (
            parse_program_resource_suffix,
        )
        from ..portal import portal_url_for_address

        resolved = canonical_resolution(
            indexed_content.resolve_indexed_resource_address(
                root_id,
                actor=str(context.get("actor") or ""),
                resource_service=service,
            )
        )
        namespace, item_id = parse_program_resource_suffix(suffix)
        resolved.update({
            "address": clean_address,
            "target_kind": f"lineage_program_{namespace.replace('-', '_')}",
            "program_suffix": suffix,
            "portal_url": f"{portal_url_for_address(root_id)}&program_item={item_id}",
        })
        if not resolved["exists"]:
            return resolved
        projection = get_society_service().lineage.get_lineage_program_resource_projection(
            root_id, suffix,
        )
        item = projection["item"]
        resolved["exists"] = item is not None
        if item is not None:
            resolved["title"] = str(item.get("title") or item_id)
            resolved["summary"] = str(item.get("kind") or namespace)
        return resolved

    parts = folder_files.file_parts(suffix) if root_id.startswith(("resident_", "lineage_")) else None
    if parts is not None:
        return _folder_file(service, clean_address, root_id, parts, str(context.get("actor") or ""))
    return canonical_resolution(
        indexed_content.resolve_indexed_resource_address(
            str(address or "").strip(),
            actor=str(context.get("actor") or ""),
            resource_service=service,
        )
    )


def _folder_root(resolved: dict[str, Any]):
    root_id = str(resolved["root_id"])
    if resolved["resource_type"] == "lineage":
        return folder_files.lineage_folder(root_id)
    return folder_files.resident_folder(root_id, resolved.get("record") or {})


def _folder_file(service: "ResourceService", address: str, root_id: str, parts: tuple[str, ...], actor: str):
    """A file in a Resident Folder or Lineage Folder: readable by whoever may read the Resident or Lineage."""
    from management_portal.society_service.lineage_access_policy import file_reaches, is_personal_lineage

    from .. import indexed_content

    resolved = canonical_resolution(
        indexed_content.resolve_indexed_resource_address(root_id, actor=actor, resource_service=service)
    )
    attributes = (resolved.get("record") or {}).get("attributes") or {}
    if resolved["resource_type"] == "lineage":
        owner = str(attributes.get("created_by") or "") if is_personal_lineage(attributes) else ""
    else:
        owner = str(attributes.get("personal_owner_id") or "")
    # Before any file is touched, so a denied read looks like a missing one.
    if not file_reaches(actor, root_id, write=False, owner=owner):
        raise KeyError(address)
    resolved["address"] = address
    # Portal-managed folders keep their dot and credential paths out of reach.
    return folder_files.describe(resolved, _folder_root(resolved), parts, hide=True)


def _read_content(resolved: dict[str, Any], **context: Any):
    from management_portal.society_service import get_society_service

    # ResourceService has already authorized this root address.  SocietyService
    # owns only the bounded content projection and deliberately receives no
    # second resource lookup that could recurse into ResourceService.
    resource_id = str(resolved.get("root_id") or "").strip()
    resource_type = str(resolved.get("resource_type") or "").strip()
    if resolved.get("target_kind") in {"file", "directory"}:
        return folder_files.read_content(resolved, _folder_root(resolved), max_bytes=context.get("max_bytes") or 4 * 1024 * 1024)
    service = get_society_service()
    if resolved.get("program_suffix"):
        if not resolved.get("exists"):
            raise KeyError(resolved["address"])
        payload = service.lineage.get_lineage_program_resource_projection(
            resource_id, resolved["program_suffix"],
        )
        if payload["item"] is None:
            raise KeyError(resolved["address"])
    elif resource_type == "resident":
        payload = service.resident.get_resident_resource_projection(resource_id)
    elif resource_type == "lineage":
        payload = service.lineage.get_lineage_resource_projection(resource_id)
    elif resource_type == "family":
        payload = service.lineage.get_family_resource_projection(resource_id)
    else:
        raise ValueError(f"{resource_type} resources expose no byte content")
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    max_bytes = max(1, int(context.get("max_bytes") or 4 * 1024 * 1024))
    if len(data) > max_bytes:
        raise ValueError(
            f"{resource_type} resource projection exceeds max_bytes"
        )
    return (
        data,
        "application/json",
        {
            **resolved,
            "content_schema": str(payload.get("schema") or ""),
        },
    )


def install_resolver(service: "ResourceService") -> None:
    def resolve(address: object, **context: Any):
        return _resolution(service, address, **context)

    def read_range(resolved: dict[str, Any], *, start: int, end: int, **_context: Any):
        if resolved.get("target_kind") != "file":
            raise ValueError(f"{resolved.get('address')} has no byte range")
        return folder_files.read_content_range(resolved, _folder_root(resolved), start=start, end=end)

    service.register_resolver(
        sorted(READABLE_TYPES),
        resolve,
        read_content=_read_content,
        read_content_range=read_range,
    )


def install_mutation_provider(service: "ResourceService") -> None:
    from management_portal import isa_client
    from management_portal.hatg.contracts import InstructionError

    def unregister(resource_id, *, actor="", **_context):
        kind = resource_id.partition('_')[0]
        field = f'{kind}_id'
        read = 'GetResident' if kind == 'resident' else 'GetLineage'
        try:
            resource = isa_client.call(actor, read, {field: resource_id})[kind]
            if resource.get('status') != 'deleted':
                if kind == 'resident':
                    from management_portal.resident_handoff import release_resident

                    release_resident(actor=actor, resident_id=resource_id, lineage_id=resource['lineage_id'])
                else:
                    isa_client.call(actor, 'DeleteLineage', {field: resource_id}, key=f'resource-unregister:{resource_id}')
                if isa_client.call(actor, read, {field: resource_id})[kind].get('status') != 'deleted':
                    raise RuntimeError(f'{resource_id}: 删除尚未完成')
        except InstructionError as exc:
            if exc.code != 'instruction.not_found':
                raise
        return canonical_record(resource_id, {"deleted": True})

    service.register_mutation_provider(["lineage", "resident"], unregister=unregister)
