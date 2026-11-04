from __future__ import annotations
from management_common.portal_paths import path as portal_path

from pathlib import Path
from contextlib import ExitStack
from typing import Any


from management_common.instance_locations import located_path
from management_common.directory_paths import validate_removal
from management_common.clock import utc_now_iso
from management_portal import project_registry, project_rename

from . import project_folders
from . import lineage_aggregate


PROJECTS_ROOT = (portal_path('projects'))


class ProjectExecutionControl:
    """Society owns Project membership and the directory execution contract.

    Constructed in the RPC handler process, never passed over RPC or installed
    in a caller-local ContextVar. Directory users and stop/restore semantics
    remain owned by resident_lifecycle_domain.
    """

    def __init__(self, actor: str, entry: str = 'system'):
        self.actor, self.entry = actor, entry

    def _call(self, instruction, arguments):
        from management_portal import isa_client
        return isa_client.call(self.actor, instruction, arguments, entry=self.entry)

    def busy(self, resident_ids):
        busy = []
        for start in range(0, len(resident_ids), 200):
            states = self._call('ListResidentStates', {
                'resident_ids': resident_ids[start:start + 200], 'include_viewer_state': False})
            for item in states.get('states') or []:
                state = item.get('state') if isinstance(item.get('state'), dict) else item
                if state.get('work_state') in {'running', 'stopping'}:
                    busy.append(str(item.get('resident_id') or ''))
        return busy

    def affected_residents(self, change):
        from .state_store import get_lineage_data_store
        from . import resident_lifecycle_domain

        with get_lineage_data_store().database.snapshot() as connection:
            found = {row['resident_id'] for row in connection.execute(
                'SELECT DISTINCT r.resident_id FROM lineage_nodes n '
                'JOIN lineages l ON l.lineage_id=n.lineage_id '
                'JOIN residents r ON r.resident_id=n.node_id '
                'WHERE l.project_id=%s AND r.deleted_at IS NULL', (change.old_id,),
            ).fetchall()}
        if change.moves:
            records, _runs = resident_lifecycle_domain.directory_execution_users(change.old_root)
            found.update(record['resident_id'] for record in records)
        return sorted(found)

    def move_scope(self, resident_ids):
        from . import resident_lifecycle_domain
        return resident_lifecycle_domain.directory_move_execution_scope(
            resident_ids=resident_ids, actor=self.actor)

    def rewrite_database(self, change, rewriter, *, apply):
        from .directory_relocation import rewrite_project_database
        return rewrite_project_database(change, rewriter, apply=apply)

    def reconcile_dependencies(self, source, target):
        from .directory_relocation import reconcile_dependencies
        return reconcile_dependencies(source, target)


def rename_project(project_id: str, new_project_id: str, actor: str, *,
                   dry_run: bool = False, entry: str = 'system') -> dict[str, Any]:
    from management_portal.hatg.contracts import InstructionError
    try:
        return project_rename.rename_project(project_id, new_project_id, actor,
            dry_run=dry_run, execution_control=ProjectExecutionControl(actor, entry))
    except project_rename.RenameBlocked as exc:
        raise InstructionError('instruction.conflict', str(exc), details=exc.details) from exc


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
    """Validate the owner directory or authorize replay of its deletion receipt."""
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
    identities = {str(project_id or "").strip(), str(record.get("id") or "").strip()}
    identities.discard("")
    return identities


def _registered_deletable_root(record: dict[str, Any]) -> Path:
    # Identity reads return a resolved path; deletion must inspect the path
    # from the registry before following symlinks.
    return _deletable_root(Path(located_path(record["path"])).expanduser())


def delete_project(project_id: str, actor: str) -> dict[str, Any]:
    """Delete a project and everything in it (decision_4648f7fa3295).

    Terminate child execution, then commit identities, nested registrations,
    bindings, the deletion receipt and directory cleanup together. Filesystem
    failures remain queue retries after this transaction has committed.
    """

    from . import lineage_lifecycle

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
    from .resident_workspace_deletion import remove_deleted_folder
    from .state_store import get_lineage_data_store
    store = get_lineage_data_store()
    lineage_ids: list[str] = []
    resident_ids: list[str] = []
    try:
        from management_portal.resources.registry_store import get_registry_store
        registry = get_registry_store()
        registry._ensure()
        with ExitStack() as preparation:
            from . import invariant_locks
            preparation.enter_context(lineage_aggregate.execution_authorities([*ordinary, *principals]))
            preparation.enter_context(invariant_locks.lineage_scopes([*ordinary, *principals], operation='project.delete'))
            commits = [(lineage_id, preparation.enter_context(
                lineage_lifecycle.prepare_lineage_deletion(lineage_id, actor_id)))
                for lineage_id in [*ordinary, *principals]]
            from . import resident_catalog
            member_ids = [record['resident_id'] for lineage_id in [*ordinary, *principals]
                          for record in resident_catalog.get_catalog().for_lineage(lineage_id)]
            connection = preparation.enter_context(store.database.transaction())
            from management_common.directory_cleanup_store import get_directory_cleanup_store
            get_directory_cleanup_store().lock(connection)
            with registry.writer(
                project_registry.COLLECTION, 'project_deletions', connection=connection,
            ) as writer:
                document = writer.get(project_registry.COLLECTION, project_registry.DOCUMENT_ID)
                project_record, project_root = project_folders.project_record_from_document(document, project_id)
                project_root = _registered_deletable_root(project_record)
                if _project_lineage_ids(identities) != (ordinary, principals):
                    raise ValueError('Project membership changed during deletion; retry')
                from .directory_owner import directory_metadata
                directory_metadata(project_record.get('directory'), registry_id)
                from management_common.directory_move_store import cancel_owner_moves
                cancel_owner_moves(connection, registry_id)
                from management_portal.resources.directory_registrations import unregister_tree
                nested = unregister_tree(
                    [project_root],
                    owner_ids=[registry_id, *ordinary, *principals, *member_ids], connection=connection, actor=actor_id)
                for lineage_id, commit in commits:
                    result = commit()
                    lineage_ids.append(lineage_id)
                    resident_ids.extend(str(item) for item in result.get("deleted_resident_ids") or [])
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
                    "resource_ids": sorted(set([registry_id, *lineage_ids, *resident_ids, *nested['resource_ids']])),
                    "effects": {"status": "pending", "completed": {}, "errors": {}, "attempts": 0},
                }
                document["projects"] = _registry_without_project(document, registry_id)["projects"]
                from .resident_workspace_deletion import delete_workspaces
                delete_workspaces(connection, owner_ids=[registry_id], deleted_at=entry['deleted_at'], deleted_by=actor_id)
                remove_deleted_folder(project_root, connection=connection, owner_id=registry_id,
                                      actor=actor_id)
                from management_portal.resources.directory_registrations import unregister_identity
                unregister_identity(registry_id, connection=connection, actor=actor_id)
                writer.put(project_registry.COLLECTION, project_registry.DOCUMENT_ID, document)
                writer.put('project_deletions', registry_id, entry)
    except Exception as exc:
        from management_portal.hatg.contracts import InstructionError
        committed = _finish_deleted_project(registry_id, actor_id)
        if committed is not None:
            return {**committed, 'projection_error': str(exc)}
        raise InstructionError(
            "instruction.failed", f"Project 删除未完成：{exc}",
            details={"project_id": registry_id, "deleted_lineage_ids": [],
                     "deleted_resident_ids": []},
            commit_state="not_committed",
        ) from exc
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    return {**entry, "project_id": registry_id, 'directory_cleanup': get_directory_cleanup_store().status(owner_id=registry_id)}


def _finish_deleted_project(project_id: str, actor_id: str) -> dict[str, Any] | None:
    """The audit record of an earlier deletion whose registry entry is already removed.

    Only the actor who started that deletion gets it back; nothing remains to
    delete once the registry entry is gone.
    """

    from management_portal.resources.registry_store import get_registry_store
    target = str(project_id or "").strip()
    entry = get_registry_store().get('project_deletions', target)
    if entry is None or str(entry.get("deleted_by") or "") != actor_id:
        return None
    registry_id = str(entry.get("project_id") or target)
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    return {**entry, "project_id": registry_id,
            'directory_cleanup': get_directory_cleanup_store().status(owner_id=registry_id)}


def _deletable_root(project_root: Path) -> Path:
    """The resolved project directory, refused before any side effect when it is unsafe to remove."""

    validate_removal(project_root)
    return project_root
