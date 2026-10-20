"""Reference-aware Workspace retirement and resumable directory cleanup."""
from pathlib import Path

from management_common.portal_database import _database_schema

from .folder_removal import remove_tree, _owner_may_enter_and_change


def retire_unreferenced(connection, *, workspace_ids=(), paths=(), deleted_at, deleted_by):
    """Retire associated directories with no surviving Resident users.

    Call after marking the entire deletion set, so members being deleted
    together do not keep one another's Workspaces alive. Nested registrations
    participate independently: a shared child keeps its files and stable path.
    """
    table = f'"{_database_schema("workspace")}".workspaces'
    roots = sorted({str(Path(path).resolve(strict=False)) for path in paths})
    ids = sorted(set(workspace_ids))
    if not ids and not roots:
        return []
    # Serialize registration/moves with selecting the descendant set. Binding
    # writers lock their Workspace rows through ensure_workspaces as well.
    connection.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
    rows = connection.execute(
        f"SELECT w.workspace_id,w.path FROM {table} w WHERE w.deleted_at IS NULL AND ("
        "w.workspace_id=ANY(%s) OR EXISTS (SELECT 1 FROM unnest(%s::text[]) p(path) "
        "WHERE w.path=p.path OR starts_with(w.path,p.path || '/')) OR EXISTS ("
        f"SELECT 1 FROM {table} seed WHERE seed.workspace_id=ANY(%s) "
        "AND (w.path=seed.path OR starts_with(w.path,seed.path || '/')))) "
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
    return [row['path'] for row in retired]


def remove_retired_folder(folder):
    """Remove files outside live Workspaces; their presence never rejects deletion.

    Retain shared directories in place, including their ancestor directories.
    A registration created after the identity commit is also retained. The
    table lock prevents registration/moves racing the filesystem effect.
    """
    from management_portal.resources.workspace_store import get_workspace_store
    root = Path(folder).absolute()
    store = get_workspace_store()
    with store.database.transaction() as connection:
        connection.execute(f"LOCK TABLE {store.table} IN SHARE ROW EXCLUSIVE MODE")
        rows = connection.execute(
            f"SELECT path FROM {store.table} WHERE deleted_at IS NULL AND "
            "(path=%s OR starts_with(path,%s))",
            (str(root), str(root).rstrip('/') + '/'),
        ).fetchall()
        retained = [Path(row['path']) for row in rows]
        # An extra Workspace can contain another live Resident's system
        # directory. Keep that Lineage's files too, not just its workspace/.
        lineage = f'"{_database_schema("lineage")}"'
        owned = connection.execute(
            f"SELECT w.path FROM {lineage}.residents r JOIN {store.table} w "
            "ON w.workspace_id=r.owned_workspace_id WHERE r.deleted_at IS NULL "
            "AND w.deleted_at IS NULL AND starts_with(w.path,%s)",
            (str(root).rstrip('/') + '/',),
        ).fetchall()
        for row in owned:
            path = Path(row['path'])
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
