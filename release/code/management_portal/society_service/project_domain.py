from __future__ import annotations
from management_common.portal_paths import path as portal_path

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from management_common.site_registry import rewrite_shared_path
from management_portal import project_registry

from . import resident_store, resource_client


PROJECTS_ROOT = (portal_path("share_root") / "Projects")
PROJECT_DELETION_AUDIT = (portal_path("share_root") / "Agents") / "_archive" / "deleted_projects" / "project_deletions.json"


def _actor_id(value: object) -> str:
    actor = str(value or "").strip()
    while actor.startswith(("@", "＠")):
        actor = actor[1:].strip()
    return actor


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def project_root_from_registry(project_id: str) -> Path | None:
    try:
        return project_record_for_id(project_id)[1]
    except KeyError:
        return None


def project_root_for_id(project_id: str) -> Path:
    root = project_root_from_registry(project_id)
    if root is None or not root.is_dir():
        raise ValueError(f"项目不存在: {project_id}")
    return root




def validate_existing_project_selection(payload: dict[str, Any]) -> None:
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("Lineage Workbench 新增 Session 需要选择已有 Project")
    project_root_for_id(project_id)




def project_record_for_id(project_id: str) -> tuple[dict[str, Any], Path]:
    """Resolve only registered identity/path pairs through the Project registry."""
    return _project_record_from_document(project_registry.load(), project_id)


def _project_record_from_document(
    document: dict[str, Any], project_id: str
) -> tuple[dict[str, Any], Path]:
    target = str(project_id or "").strip()
    for item in document["projects"]:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").strip()
        project_path = (
            Path(rewrite_shared_path(raw_path)).expanduser().resolve(strict=False)
            if raw_path
            else None
        )
        registry_id = str(item.get("id") or "").strip()
        if target not in {registry_id, project_path.name if project_path else ""}:
            continue
        if project_path is None:
            raise ValueError(f"project path missing: {target}")
        return dict(item), project_path
    raise KeyError("project not found")


def _registry_without_project(
    document: dict[str, Any], registry_id: str
) -> dict[str, Any]:
    projects = document["projects"]
    remaining = [
        item
        for item in projects
        if not (
            isinstance(item, dict)
            and str(item.get("id") or "").strip() == registry_id
        )
    ]
    if len(remaining) == len(projects):
        raise KeyError("project not found in registry")
    updated = dict(document)
    updated["projects"] = remaining
    return updated


def _append_deletion_index(entry: dict[str, Any]) -> None:
    index_path = PROJECT_DELETION_AUDIT
    payload = resident_store.read_json(
        index_path, {"schema_version": 1, "projects": []}
    )
    if not isinstance(payload, dict):
        payload = {"schema_version": 1, "projects": []}
    projects = payload.get("projects")
    if not isinstance(projects, list):
        projects = []
    projects.append(entry)
    payload.update({
        "schema_version": 1,
        "projects": projects,
        "updated_at": entry.get("deleted_at", _now_iso()),
    })
    resident_store.write_json(index_path, payload)


def _project_lineage_ids(identities: set[str]) -> tuple[list[str], list[str]]:
    """Lineages of a project: (ordinary, project-level principal)."""

    from . import lineage_domain

    ordinary: list[str] = []
    principals: list[str] = []
    for record in lineage_domain._lineage_catalog_store().listing_records():
        if str(record.get("project_id") or "").strip() not in identities:
            continue
        lineage_id = str(record.get("lineage_id") or "")
        level = str((record.get("principal_hierarchy") or {}).get("level") or "")
        (principals if level == "project" else ordinary).append(lineage_id)
    return sorted(ordinary), sorted(principals)


def validate_project_deletion(project_id: str, actor: str) -> dict[str, Any]:
    """Check the registered directory before ResourceService unregisters content."""
    actor_id = _actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后删除 project")
    try:
        record, _root = project_record_for_id(project_id)
    except KeyError:
        if _finish_deleted_project(project_id, actor_id) is None:
            raise
        return {"deleted": True}
    project_root = _registered_deletable_root(record)
    return {"deleted": False}


def _project_identities(project_id: str, record: dict[str, Any], project_root: Path) -> set[str]:
    identities = {str(project_id or "").strip(), str(record.get("id") or "").strip(), project_root.name}
    identities.discard("")
    return identities


def _registered_deletable_root(record: dict[str, Any]) -> Path:
    # Identity reads return a resolved path; deletion must inspect the path
    # from the registry before following symlinks.
    return _deletable_root(Path(rewrite_shared_path(record["path"])).expanduser())


def delete_project(project_id: str, actor: str) -> dict[str, Any]:
    """Delete a project and everything in it (decision_4648f7fa3295).

    The project does not need to be empty: its Lineages and Residents are
    deleted without archives, the project directory and all its files are
    removed, the registry entry is removed and an audit record appended.
    ResourceService unregisters the project's resources before calling this.
    A call that fails midway leaves what it already deleted; calling again
    deletes the rest, and a project whose registry entry is already gone
    returns its deletion record.
    """

    from . import lineage_domain

    actor_id = _actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后删除 project")
    document = project_registry.load()
    try:
        project_record, project_root = _project_record_from_document(document, project_id)
    except KeyError:
        finished = _finish_deleted_project(project_id, actor_id)
        if finished is None:
            raise
        return finished
    registry_id = str(project_record.get("id") or "").strip()
    if not registry_id:
        raise ValueError("project registry id missing")
    project_root = _registered_deletable_root(project_record)
    identities = _project_identities(project_id, project_record, project_root)
    ordinary, principals = _project_lineage_ids(identities)
    from .resident_workspace_retirement import remove_retired_folder
    from .state_store import get_lineage_data_store
    store = get_lineage_data_store()
    lineage_ids: list[str] = []
    resident_ids: list[str] = []
    try:
        for lineage_id in [*ordinary, *principals]:
            result = lineage_domain.delete_lineage(lineage_id, actor_id)
            lineage_ids.append(lineage_id)
            resident_ids.extend(str(item) for item in result.get("deleted_resident_ids") or [])
        with project_registry.mutate() as document:
            project_record, project_root = _project_record_from_document(document, project_id)
            project_root = _registered_deletable_root(project_record)
            entry = {
                "schema_version": 1,
                "project_id": registry_id,
                "project_name": project_root.name,
                "project_path": str(project_root),
                "deleted_at": _now_iso(),
                "deleted_by": actor_id,
                "description": str(project_record.get("description") or ""),
                "lineage_ids": lineage_ids,
                "resident_ids": resident_ids,
            }
            document["projects"] = _registry_without_project(document, registry_id)["projects"]
            store.retire_workspaces_under([project_root], deleted_at=entry["deleted_at"], deleted_by=actor_id)
            if project_root.exists():
                remove_retired_folder(project_root)
            _append_deletion_index(entry)
    except Exception as exc:
        from management_portal.hatg.contracts import InstructionError

        raise InstructionError(
            "instruction.failed", f"Project 删除未完成：{exc}",
            details={"project_id": registry_id, "deleted_lineage_ids": lineage_ids,
                     "deleted_resident_ids": resident_ids},
            commit_state="unknown",
        ) from exc
    return {**entry, "project_id": registry_id}


def _finish_deleted_project(project_id: str, actor_id: str) -> dict[str, Any] | None:
    """The audit record of an earlier deletion whose registry entry is already removed.

    Only the actor who started that deletion gets it back; nothing remains to
    delete once the registry entry is gone.
    """

    payload = resident_store.read_json(PROJECT_DELETION_AUDIT, {"projects": []})
    entries = payload.get("projects") if isinstance(payload, dict) else []
    target = str(project_id or "").strip()
    entry = next((
        item for item in reversed(entries if isinstance(entries, list) else [])
        if isinstance(item, dict)
        and target in {str(item.get("project_id") or ""), str(item.get("project_name") or "")}
    ), None)
    if entry is None or str(entry.get("deleted_by") or "") != actor_id:
        return None
    registry_id = str(entry.get("project_id") or target)
    return {**entry, "project_id": registry_id}


def _deletable_root(project_root: Path) -> Path:
    """The resolved project directory, refused before any side effect when it is unsafe to remove."""

    if project_root.is_symlink():
        raise ValueError("project path must be a directory, not a symbolic link")
    resolved = project_root.resolve(strict=False)
    projects_root = PROJECTS_ROOT.resolve(strict=False)
    if resolved == projects_root or projects_root not in resolved.parents:
        raise PermissionError("拒绝删除 /share/Projects 之外的 project")
    return resolved
