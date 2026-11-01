"""Reference-aware Workspace retirement and resumable directory cleanup."""
from pathlib import Path
from contextlib import nullcontext

from management_common.portal_database import _database_schema

from .folder_removal import remove_tree, _owner_may_enter_and_change
from management_portal.resources.errors import ResourceOperationError


class WorkspaceRetirementRefused(ResourceOperationError):
    """All eligibility checks run before retirement or filesystem effects."""
    commit_state = "not_committed"


def retire_unreferenced(connection, *, workspace_ids=(), paths=(), deleted_at, deleted_by):
    """Retire associated directories with no surviving Resident users.

    Call after marking the entire deletion set, so members being deleted
    together do not keep one another's Workspaces alive. Nested registrations
    participate independently: a shared child keeps its files and stable path.
    """
    from management_common.instance_locations import located_path
    from management_portal.resources.workspace_store import LOCATION_PREFIX_SQL, stored_path

    table = f'"{_database_schema("workspace")}".workspaces'
    roots = sorted({stored_path(path) for path in paths})
    ids = sorted(set(workspace_ids))
    if not ids and not roots:
        return []
    # Serialize registration/moves with selecting the descendant set. Binding
    # writers lock their Workspace rows through ensure_workspaces as well.
    connection.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
    rows = connection.execute(
        f"SELECT w.workspace_id,w.path FROM {table} w WHERE w.deleted_at IS NULL AND ("
        "w.workspace_id=ANY(%s) OR EXISTS (SELECT 1 FROM unnest(%s::text[]) p(path) "
        f"WHERE w.path=p.path OR starts_with(w.path,{LOCATION_PREFIX_SQL.format(column='p.path')})) OR EXISTS ("
        f"SELECT 1 FROM {table} seed WHERE seed.workspace_id=ANY(%s) "
        f"AND (w.path=seed.path OR starts_with(w.path,{LOCATION_PREFIX_SQL.format(column='seed.path')})))) "
        "ORDER BY w.workspace_id FOR UPDATE OF w", (ids, roots, ids),
    ).fetchall()
    candidates = [row['workspace_id'] for row in rows]
    used = connection.execute(
        "SELECT owned_workspace_id AS workspace_id FROM residents "
        "WHERE deleted_at IS NULL AND owned_workspace_id=ANY(%s) UNION "
        "SELECT b.workspace_id FROM resident_workspace_bindings b "
        "JOIN residents r ON r.resident_id=b.resident_id "
        "WHERE r.deleted_at IS NULL AND b.workspace_id=ANY(%s)",
        (candidates, candidates),
    ).fetchall()
    retained = {row['workspace_id'] for row in used}
    retired = [row for row in rows if row['workspace_id'] not in retained]
    ids = [row['workspace_id'] for row in retired]
    if ids:
        connection.execute(
            f"UPDATE {table} SET deleted_at=%s,deleted_by=%s,"
            "updated_at=GREATEST(%s::timestamptz,updated_at + interval '1 microsecond') "
            "WHERE workspace_id=ANY(%s)", (deleted_at, deleted_by, deleted_at, ids),
        )
        connection.execute("DELETE FROM resident_workspace_bindings WHERE workspace_id=ANY(%s)", (ids,))
    return [located_path(row['path']) for row in retired]


def unregister_workspace(workspace_id, *, actor, authorize, check_dependencies=None):
    """Explicit retirement refuses live users and independently registered children.

    The Workspace lock also serializes binding writers, moves and registration.
    Keep it through cleanup: a newly registered directory must never be removed
    by an earlier administrator request. Host directories are registration-only.
    """
    from management_common.instance_locations import current_instance, is_area_reference
    from management_portal.resources.workspace_store import get_workspace_store, now
    from .state_store import get_lineage_data_store

    store = get_workspace_store()
    database = get_lineage_data_store().database
    with database.transaction() as connection:
        connection.execute(f"LOCK TABLE {store.table} IN SHARE ROW EXCLUSIVE MODE")
        connection.execute(f"SELECT workspace_id FROM {store.table} WHERE workspace_id=%s FOR UPDATE", (workspace_id,))
        row = store.rows([workspace_id], connection=connection).get(workspace_id)
        if row is None:
            raise KeyError(workspace_id)
        authorize(store.descriptor(row))
        users = connection.execute(
            "SELECT resident_id,'owner' AS usage FROM residents "
            "WHERE deleted_at IS NULL AND owned_workspace_id=%s UNION "
            "SELECT r.resident_id,'binding' AS usage FROM resident_workspace_bindings b "
            "JOIN residents r ON r.resident_id=b.resident_id "
            "WHERE r.deleted_at IS NULL AND b.workspace_id=%s ORDER BY resident_id,usage",
            (workspace_id, workspace_id),
        ).fetchall()
        if users:
            raise WorkspaceRetirementRefused("workspace_in_use", "Workspace is owned or bound by a live Resident", 409,
                                         details={"users": [dict(user) for user in users]})
        from management_portal.resources.workspace_store import stored_path
        location = stored_path(row['path'])
        below = location if location.endswith(':') else location.rstrip('/') + '/'
        children = connection.execute(
            f"SELECT workspace_id FROM {store.table} WHERE deleted_at IS NULL "
            "AND workspace_id<>%s AND (path=%s OR starts_with(path,%s)) ORDER BY workspace_id",
            (workspace_id, location, below),
        ).fetchall()
        if children:
            raise WorkspaceRetirementRefused("workspace_nested_registration", "Workspace contains active registered resources", 409,
                                         details={"resource_ids": [child['workspace_id'] for child in children]})
        if check_dependencies is not None:
            check_dependencies(row['path'], connection)
        internal = is_area_reference(location)
        if internal and Path(row['path']) == current_instance().area(location.partition(':')[0]):
            raise WorkspaceRetirementRefused("workspace_area_root", "An instance area root cannot be removed as a Workspace", 409)
        retired = retire_unreferenced(connection, workspace_ids=[workspace_id], deleted_at=now(), deleted_by=actor)
        if not retired:
            raise WorkspaceRetirementRefused("workspace_in_use", "Workspace is owned or bound by a live Resident", 409)
        if internal:
            remove_retired_folder(row['path'], connection=connection)
        return store.descriptor(store.rows([workspace_id], connection=connection, include_deleted=True)[workspace_id])


def remove_retired_folder(folder, *, connection=None):
    """Remove files outside live Workspaces; their presence never rejects deletion.

    Retain shared directories in place, including their ancestor directories.
    A registration created after the identity commit is also retained. The
    table lock prevents registration/moves racing the filesystem effect.
    """
    from management_common.instance_locations import located_path
    from management_portal.resources.workspace_store import get_workspace_store, stored_path
    root = Path(folder).absolute()
    stored = stored_path(root)
    # An area root (``projects:``) already ends with its separator.
    below = stored if stored.endswith(":") else stored.rstrip("/") + "/"
    store = get_workspace_store()
    with nullcontext(connection) if connection is not None else store.database.transaction() as connection:
        connection.execute(f"LOCK TABLE {store.table} IN SHARE ROW EXCLUSIVE MODE")
        rows = connection.execute(
            f"SELECT path FROM {store.table} WHERE deleted_at IS NULL AND "
            "(path=%s OR starts_with(path,%s))",
            (stored, below),
        ).fetchall()
        retained = [Path(located_path(row['path'])) for row in rows]
        # An extra Workspace can contain another live Resident's system
        # directory. Keep that Lineage's files too, not just its workspace/.
        lineage = f'"{_database_schema("lineage")}"'
        owned = connection.execute(
            f"SELECT w.path FROM {lineage}.residents r JOIN {store.table} w "
            "ON w.workspace_id=r.owned_workspace_id WHERE r.deleted_at IS NULL "
            "AND w.deleted_at IS NULL AND starts_with(w.path,%s)",
            (below,),
        ).fetchall()
        for row in owned:
            path = Path(located_path(row['path']))
            if len(path.parents) >= 3 and path.parent.parent.name == 'residents':
                system_folder = path.parents[2]
                if system_folder == root or root in system_folder.parents:
                    retained.append(system_folder)

        def remove(path):
            if any(path == keep or keep in path.parents for keep in retained):
                return
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                if not any(path in keep.parents for keep in retained):
                    remove_tree(path)
                    return
                _owner_may_enter_and_change(str(path))
                for child in path.iterdir():
                    remove(child)
            elif path.exists():
                path.unlink()

        remove(root)
