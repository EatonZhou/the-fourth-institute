"""Lineage ownership and authored-item authorization rules.

This module decides permissions from the caller and current records. It does not
read or write the Lineage aggregate, so callers choose their own state scope.
"""

from __future__ import annotations

import time

from management_common.principal import actor_principal, is_user_or_resident

from typing import Any, Mapping

from . import identity_contract, resident_catalog
from .lineage_values import _safe_actor, _text


def actor_can_manage_lineage(
    lineage: Mapping[str, Any], actor: object, *, operation: str = "manage",
) -> bool:
    if not actor_can_manage_lineages(actor):
        return False
    if is_personal_lineage(lineage):
        return private_reaches(actor, lineage.get("created_by"), "GetLineage", "lineage_id", lineage.get("lineage_id"))
    return True


def lineage_denial(lineage: Mapping[str, Any], action: str) -> str:
    """Why ``actor_can_manage_lineage`` refused: a personal AMA's privacy, or no valid identity."""
    if is_personal_lineage(lineage):
        owner = _text(lineage.get("created_by"), 120)
        return f"{_text(lineage.get('lineage_id'), 80)} 是 {owner} 的个人 AMA，只有 {owner} 本人可以{action}"
    return "需要有效的 Portal 登录身份"


def personal_owner(actor: object, owner: object) -> bool:
    """Whether the actor is the User a personal AMA belongs to, answered from the record alone."""
    owner_id = str(owner or "").strip()
    return bool(owner_id) and str(actor or "").strip().casefold() == owner_id.casefold()


def is_personal_lineage(lineage: Mapping[str, Any]) -> bool:
    return str(lineage.get("lineage_membership") or "").strip().lower() == "personal"


def private_reaches(actor: object, owner: object, instruction: str, parameter: str, resource_id: object) -> bool:
    """Whether the actor may reach ``resource_id``, a resource of the personal AMA ``owner`` has.

    With the permission system off a personal AMA (its Lineage and every
    Resident carrying its owner) is private to the owner and the Residents
    representing the owner, administrators included.
    With it on the scheme places personal AMAs (decision_b820faf42218 §5.5):
    the read ``instruction`` naming the resource decides, so lists hide what
    that read refuses.
    """
    from management_portal import permission_system

    return permission_system.private_reaches(
        str(actor or ""), _text(owner, 120), instruction, parameter, _text(resource_id, 80))


def changeable_ids(actor: object, owners_by_id: Mapping[str, object], instruction: str, parameter: str) -> set[str]:
    """The resources (ID -> personal AMA owner or "") on which ``instruction`` would pass with ``parameter`` naming them.

    Read payloads report this as ``can_manage`` so pages offer only the
    changes the permission system will accept; with it off, everyone may.
    """
    from management_portal import permission_system

    ids = [str(resource_id) for resource_id in owners_by_id if resource_id]
    owners = {str(key): _text(value, 120) for key, value in owners_by_id.items() if _text(value, 120)}
    return permission_system.permitted_ids(actor, instruction, parameter, ids, owners=owners)


def hidden_private_ids(actor: object, records) -> set[str]:
    """The resource records a personal AMA keeps from the actor.

    Resource search and identity reads apply the same rule as the domain: a
    personal Lineage, a Resident with a registered owner, and a Workspace
    inside a personal Lineage's folder are readable by that owner and the
    Residents representing the owner, and unknown to everyone else, with the
    permission system off.  With it on, those records are hidden from whoever
    ResolveResource would refuse.  Other records are not affected.
    """
    from management_portal import permission_system
    from management_portal.resources import timing

    mark = time.perf_counter()
    owners: dict[str, str] = {}
    folders = None
    for record in records:
        attributes = record.get("attributes") or {}
        resource_id = _text(record.get("resource_id"), 80)
        kind = record.get("resource_type")
        if kind == "lineage" and is_personal_lineage(attributes):
            owner = attributes.get("created_by")
        elif kind in {"resident", "family"} and _text(attributes.get("personal_owner_id"), 120):
            owner = attributes.get("personal_owner_id")
        elif kind == "workspace" and _text(attributes.get("path"), 4096):
            folders = personal_folders() if folders is None else folders
            area = personal_area(_text(attributes.get("path"), 4096), folders)
            if area is None:
                continue
            owner = area[2]
        else:
            continue
        if not personal_owner(actor, owner):
            owners[resource_id] = _text(owner, 120)
    mark = timing.lap("private_detect", mark)
    if not owners or not str(actor or "").strip():
        return set(owners)
    hidden = permission_system.hidden_private(str(actor), owners)
    timing.lap("private_owners", mark)
    return hidden


# What reading and writing a file ask: the instructions that read and write files, on the object holding it.
FILE_READ = ("ReadResource", "address")
FILE_WRITE = ("UpdateWorkspaceFile", "address")


def file_reaches(actor: object, resource_id: str, *, write: bool, owner: str = "") -> bool:
    """Whether ReadResource (or, ``write``, UpdateWorkspaceFile) would pass on the object holding a file.

    ``resource_id`` is a Workspace, a personal AMA Lineage whose folder holds
    the file, or an unregistered shared folder (its path, which the scheme
    places under its R default node).  ``owner`` is the personal AMA owner
    of a file inside a personal AMA's folder: with the permission system off
    only that owner and the Residents representing the owner may.
    """
    from management_portal import permission_system

    actor_id = str(actor or "").strip()
    if not actor_id or not resource_id:
        return False
    instruction, parameter = FILE_WRITE if write else FILE_READ
    return resource_id in permission_system.permitted_ids(
        actor_id, instruction, parameter, [resource_id], owners={resource_id: owner} if owner else None)


def workspace_reaches(actor: object, workspace_id: str, *, write: bool, path: str = "") -> bool:
    """Whether the actor may read (or ``write``) a Workspace's files; ``path`` is the file's absolute path."""
    area = personal_area(path, personal_folders()) if path else None
    return file_reaches(actor, workspace_id, write=write, owner=area[2] if area is not None else "")


def workspace_file_address(path: str) -> str:
    """``<workspace_id>/file/<relative path>`` of the deepest registered Workspace holding ``path``, or ""."""
    from pathlib import Path

    found = path_resource(path)
    if found is None or not found[1].startswith("workspace_"):
        return ""
    relative = Path(str(path)).resolve(strict=False).relative_to(found[0]).as_posix()
    return f"{found[1]}/file/{relative}" if relative != "." else ""


def path_resource(path: str, *, shared_roots=(), private_roots=()) -> tuple[str, str, str] | None:
    """``(root, resource_id, personal owner)`` of the object an absolute path belongs to, or None.

    The deepest live Workspace or personal AMA Lineage folder holding the
    path; failing both, a folder of ``shared_roots`` (not inside
    ``private_roots``) stands for itself.  Links are followed first.
    """
    from pathlib import Path

    from management_portal.resources.workspace_store import get_workspace_store

    from .state_store import get_lineage_data_store

    target = Path(str(path)).resolve(strict=False)
    ancestors = [str(target), *(str(parent) for parent in target.parents)]
    with get_lineage_data_store().database.read() as connection:
        rows = connection.execute(
            f"SELECT workspace_id, path FROM {get_workspace_store().table} WHERE path = ANY(%s) AND deleted_at IS NULL",
            (ancestors,)).fetchall()
    area = personal_area(str(target), personal_folders())
    found = [(len(row["path"]), row["path"], row["workspace_id"]) for row in rows]
    if area is not None:
        found.append((len(area[0]), area[0], area[1]))
    if found:
        _, root, resource_id = max(found)
        inside = area is not None and Path(root).is_relative_to(Path(area[0]))
        return root, resource_id, area[2] if inside else ""
    if any(target.is_relative_to(Path(item).resolve(strict=False)) for item in private_roots):
        return None
    for item in shared_roots:
        root = Path(item).resolve(strict=False)
        if target.is_relative_to(root):
            return str(root), str(root), ""
    return None


def personal_folders() -> list[tuple[str, str, str]]:
    """``(folder, lineage_id, owner)`` of each live personal Lineage, folder fully resolved."""
    from .lineage_storage import canonical_lineage_folder
    from .state_store import get_lineage_data_store

    with get_lineage_data_store().database.read() as connection:
        rows = connection.execute(
            "SELECT lineage_id, created_by FROM lineages WHERE lineage_membership='personal' AND deleted_at IS NULL",
        ).fetchall()
    return [(str(canonical_lineage_folder(row["lineage_id"]).resolve(strict=False)), row["lineage_id"], _text(row["created_by"], 120))
            for row in rows]


def personal_area(path: str, folders: list[tuple[str, str, str]]) -> tuple[str, str, str] | None:
    """The personal Lineage whose folder holds ``path`` (an absolute path, links followed), if any."""
    from pathlib import Path

    if not folders or not path or not Path(path).is_absolute():
        return None
    clean = Path(path).resolve(strict=False)
    return next((folder for folder in folders if clean.is_relative_to(Path(folder[0]))), None)


def actor_can_manage_lineages(actor: object) -> bool:
    """An active User or a registered, live Resident; the permission system decides the rest."""

    actor_id = _safe_actor(actor)
    try:
        principal = actor_principal(actor_id)
    except ValueError:
        return False
    if principal.kind == "user":
        from management_portal.portal_access_control import get_portal_access_store

        if not get_portal_access_store().is_active(principal.id):
            return False
    if principal.kind == "resident":
        try:
            resident = resident_catalog.get_catalog().get(actor_id)
        except KeyError:
            return False
        if (
            str(resident.get("deleted_at") or "").strip()
            or not resident_catalog.registration_ready(resident)
        ):
            return False
    return is_user_or_resident(principal) or (principal.kind == "service" and principal.id == "portal")


def resident_caller_source(member: Mapping[str, Any]) -> str:
    """Require an active Resident identity before checking a target Lineage."""
    source_lineage_id = str(member.get("lineage_id") or "")
    if not source_lineage_id or member.get("deleted_at"):
        raise PermissionError("调用 Resident 没有有效的 Lineage 身份")
    return source_lineage_id


def canonical_resident_principal(resident_id: str) -> str:
    principal = _text(resident_id, 80).lower()
    if not identity_contract.is_valid_id(principal, "resident"):
        raise PermissionError("HATG Resident Tool 必须绑定规范 resident_* 身份")
    return principal


def authorize_resident_principal(
    lineage_id: str,
    resident_id: str,
    lineage: Mapping[str, Any],
    member: Mapping[str, Any],
    *,
    require_leader: bool = False,
) -> str:
    """Check the current membership against the canonical Resident identity."""
    principal = canonical_resident_principal(resident_id)
    if str(member.get("lineage_id") or "") != lineage_id or member.get("deleted_at"):
        raise PermissionError("调用 Resident 不属于目标 Lineage")
    if require_leader and principal != _text(lineage.get("leader_resident_id"), 80).lower():
        raise PermissionError("调用 Resident 不是目标 Lineage 的当前 Leader")
    return principal
