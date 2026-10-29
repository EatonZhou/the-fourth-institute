from __future__ import annotations
from management_common.portal_paths import path as portal_path

from pathlib import Path
from typing import Any


from management_common.site_registry import rewrite_shared_path
from management_common.clock import utc_now_iso
from management_portal import project_registry

from . import project_folders, resident_store, resource_client
from . import lineage_aggregate


PROJECTS_ROOT = (portal_path("share_root") / "Projects")
PROJECT_DELETION_AUDIT = (portal_path("share_root") / "Agents") / "_archive" / "deleted_projects" / "project_deletions.json"


def _actor_id(value: object) -> str:
    actor = str(value or "").strip()
    while actor.startswith(("@", "＠")):
        actor = actor[1:].strip()
    return actor


def validate_existing_project_selection(payload: dict[str, Any]) -> None:
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("Lineage Workbench 新增 Session 需要选择已有 Project")
    project_folders.project_root_for_id(project_id)




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
        "updated_at": entry.get("deleted_at", utc_now_iso()),
    })
    resident_store.write_json(index_path, payload)


def _project_lineage_ids(identities: set[str]) -> tuple[list[str], list[str]]:
    """Lineages of a project: (ordinary, project-level principal)."""

    ordinary: list[str] = []
    principals: list[str] = []
    for record in lineage_aggregate.lineage_catalog_store().listing_records():
        if str(record.get("project_id") or "").strip() not in identities:
            continue
        lineage_id = str(record.get("lineage_id") or "")
        level = str((record.get("principal_hierarchy") or {}).get("level") or "")
        (principals if level == "project" else ordinary).append(lineage_id)
    return sorted(ordinary), sorted(principals)


def project_teams(project_ids: list[str], actor: str) -> dict[str, dict[str, Any]]:
    """The team of each named Project as far as ``actor`` may read its Lineages.

    A Project's team is its Project-layer Lineage, whose Leader is the Project
    Resident, and its ordinary Lineages (decision_3e07c6d35cac); personal and
    User-layer Lineages are not part of it.  Each ordinary Lineage reports its
    Leader, how many members it has and how many of them are running now.
    """
    from . import principal_hierarchy
    from .lineage_access_policy import changeable_ids
    from .resident_protocol import read_resident_states

    wanted = list(dict.fromkeys(str(item or "").strip() for item in project_ids if str(item or "").strip()))
    catalog = lineage_aggregate.lineage_catalog_store()
    rows = [row for row in catalog.listing_records()
            if row.get("project_id") in wanted and row.get("lineage_membership") != "personal"
            and (row.get(principal_hierarchy.FIELD) or {}).get("level") != "user"]
    readable = changeable_ids(actor, {row["lineage_id"]: "" for row in rows}, "GetLineage", "lineage_id")
    rows = [row for row in rows if row["lineage_id"] in readable]
    graphs = catalog.listing_graph_summaries([row["lineage_id"] for row in rows])
    leaders = {str(row.get("leader_resident_id") or "") for row in rows
               if (row.get(principal_hierarchy.FIELD) or {}).get("level") != "project"}
    # A Project-layer Lineage also shows the ordinary Leaders as references;
    # they are members of their own Lineage only.
    members = {row["lineage_id"]: [node for node in graphs[row["lineage_id"]]["lineage_node_ids"]
                                   if (row.get(principal_hierarchy.FIELD) or {}).get("level") != "project"
                                   or node not in leaders]
               for row in rows}
    states = read_resident_states([node for nodes in members.values() for node in nodes])
    running = {resident_id for resident_id, state in states.items() if state.get("work_state") == "running"}
    teams = {project_id: {"project_resident_id": "", "project_lineage_id": "", "lineages": [],
                          "resident_count": 0, "running_resident_count": 0} for project_id in wanted}
    for row in sorted(rows, key=lambda item: (str(item.get("title") or "").casefold(), item["lineage_id"])):
        team = teams[row["project_id"]]
        nodes = members[row["lineage_id"]]
        team["resident_count"] += len(nodes)
        team["running_resident_count"] += sum(1 for node in nodes if node in running)
        if (row.get(principal_hierarchy.FIELD) or {}).get("level") == "project":
            team["project_resident_id"] = str(row.get("leader_resident_id") or "")
            team["project_lineage_id"] = row["lineage_id"]
            continue
        team["lineages"].append({
            "lineage_id": row["lineage_id"], "title": str(row.get("title") or ""),
            "leader_resident_id": str(row.get("leader_resident_id") or ""),
            "member_count": len(nodes), "running_count": sum(1 for node in nodes if node in running),
        })
    return teams


def validate_project_deletion(project_id: str, actor: str) -> dict[str, Any]:
    """Check the registered directory before ResourceService unregisters content."""
    actor_id = _actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后删除 project")
    try:
        record, _root = project_folders.project_record_for_id(project_id)
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
        project_record, project_root = project_folders.project_record_from_document(document, project_id)
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
            project_record, project_root = project_folders.project_record_from_document(document, project_id)
            project_root = _registered_deletable_root(project_record)
            entry = {
                "schema_version": 1,
                "project_id": registry_id,
                "project_name": project_root.name,
                "project_path": str(project_root),
                "deleted_at": utc_now_iso(),
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
        raise PermissionError(f"拒绝删除当前实例项目目录 {projects_root} 之外的 project")
    return resolved
