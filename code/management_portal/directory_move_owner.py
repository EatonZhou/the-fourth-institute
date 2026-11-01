"""Registration owners enlisted by every directory relocation entry point.

Workspace rows and the resource registry's declared directory entries remain
their sole path authorities. Identity/graph changes are supplied by the caller.
"""
from pathlib import Path

from management_common.instance_locations import located_path
from management_common.directory_paths import cleanup_location, cleanup_path


def relocated(value, source, target):
    path = cleanup_path(value)
    if path.is_relative_to(source):
        return cleanup_location(target / path.relative_to(source))
    return value


def relocate_nested(connection, source, target):
    from management_common.directory_retention import relocate_artifacts
    from management_common.directory_move_store import relocate_pending_cleanup
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.resources.directory_registrations import COLLECTIONS, locations
    from management_portal.resources.registry_store import get_registry_store
    workspace = get_workspace_store()
    workspace.cleanup.lock(connection)
    rows = connection.execute(f'SELECT workspace_id,path FROM {workspace.table} WHERE deleted_at IS NULL FOR UPDATE').fetchall()
    for row in rows:
        path = relocated(row['path'], source, target)
        if path != row['path']:
            workspace.cleanup.assert_available(located_path(path), connection=connection)
            connection.execute(f'UPDATE {workspace.table} SET path=%s,updated_at=GREATEST(clock_timestamp(),'
                               "updated_at+interval '1 microsecond') WHERE workspace_id=%s", (path, row['workspace_id']))
    registry = get_registry_store()
    with registry.writer(*COLLECTIONS, connection=connection) as writer:
        for collection in COLLECTIONS:
            for resource_id, document in writer.documents(collection).items():
                changed = False
                for entry in locations(collection, document):
                    path = relocated(entry['path'], source, target)
                    if path != entry['path']:
                        entry['path'] = path
                        changed = True
                # Canonical location is the selected entry in these owner
                # documents; update it using the same transformation.
                for field in ('canonical_checkout', 'canonical_root'):
                    if document.get(field):
                        path = relocated(document[field], source, target)
                        changed |= path != document[field]
                        document[field] = path
                if changed:
                    writer.put(collection, resource_id, document)
    relocate_artifacts(connection, lambda value: relocated(value, source, target))
    relocate_pending_cleanup(connection, lambda value: relocated(value, source, target))


def reconcile_dependencies(source, target):
    from management_portal.provider_session_relocation import relocate_provider_sessions
    result = relocate_provider_sessions(source, target)
    if result['errors'] or result['conflicts']:
        raise RuntimeError('Provider working-directory relocation is pending: ' + str(result['errors'] + result['conflicts']))
    repair_git_worktrees(source, target)


def recover_pending_moves():
    """Portal System maintenance retries the journal's remaining dependencies."""
    from management_common.directory_move_store import DirectoryMoveStore
    return DirectoryMoveStore().recover(dependencies=reconcile_dependencies)


def repair_git_worktrees(source, target):
    """Repair only Git's declared pointers, including a worktree's external backlink."""
    import os
    def rewrite(pointer):
        if pointer.is_symlink() or not pointer.is_file():
            return
        text = pointer.read_text(encoding='utf-8')
        value = text.removeprefix('gitdir: ').strip()
        path = Path(value)
        if not path.is_absolute():
            return
        mapped = target / path.relative_to(source) if path.is_relative_to(source) else path
        if mapped != path:
            pointer.write_text(('gitdir: ' if text.startswith('gitdir: ') else '') + str(mapped) + '\n', encoding='utf-8')
    for root, directories, files in os.walk(target, followlinks=False):
        here = Path(root)
        directories[:] = [name for name in directories if not (here / name).is_symlink()]
        if '.git' in files:
            pointer = here / '.git'
            rewrite(pointer)
            text = pointer.read_text(encoding='utf-8').strip()
            if not text.startswith('gitdir: '):
                raise ValueError('Invalid Git worktree pointer: ' + str(pointer))
            admin = Path(text[len('gitdir: '):])
            if not admin.is_absolute():
                admin = (here / admin).resolve()
            if not (admin / 'gitdir').is_file():
                raise FileNotFoundError('Git worktree backlink is missing: ' + str(admin))
            rewrite(admin / 'gitdir')
        if here.name == '.git':
            for pointer in here.glob('worktrees/*/gitdir'):
                rewrite(pointer)
                worktree = Path(pointer.read_text(encoding='utf-8').strip())
                rewrite(worktree)
