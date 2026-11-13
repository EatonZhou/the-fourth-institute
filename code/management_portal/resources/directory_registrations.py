"""Directory locations declared by registry owners, shared by delete and move.

Registry documents stay authoritative. This inventory is a view, not another
path index; callers enlist mutations in the object owner's transaction.
"""
from pathlib import Path
from contextlib import contextmanager

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.directory_paths import validate_removal
from management_common.directory_retention import enqueue_directory_cleanup
from management_common.instance_locations import located_path
from management_common.instance_locations import stored_location

from .registry_store import get_registry_store

COLLECTIONS = ('repo', 'article', 'paper', 'knowledge', 'report')
IGNORE_COLLECTION = 'auto_registration_ignore'
PROJECTION_RELEASES = 'resource_projection_releases'


def request_projection_release(resource_id, *, actor, connection):
    """Transactional outbox of IDs only; the projection owner performs effects."""
    with get_registry_store().writer(PROJECTION_RELEASES, connection=connection) as writer:
        if writer.get(PROJECTION_RELEASES, resource_id) is None:
            writer.put(PROJECTION_RELEASES, resource_id, {
                'resource_id': resource_id, 'actor': actor, 'attempts': 0, 'error': '',
            })


def assert_empty_location(path, *, connection):
    """Creation checks filesystem occupancy and durable cleanup reservations."""
    from management_common.directory_paths import assert_missing_location, lexical_path, validate_filename
    path = lexical_path(path)
    validate_filename(path)
    get_directory_cleanup_store().assert_available(path, connection=connection)
    assert_missing_location(path)


def suppress_removed(writer, collection, resource_id, entries):
    if collection not in {'repo', 'article'}:
        return
    from management_common.clock import utc_now_iso
    for entry in entries:
        path = str(Path(located_path(entry['path'])).resolve(strict=False))
        writer.put(IGNORE_COLLECTION, collection + ':' + path, {
            'schema': 'tfi.resource-auto-ignores.v1', 'resource_type': collection,
            'path': path, 'resource_id': resource_id, 'ignored_at': utc_now_iso(),
        })


def is_deleted(resource_id, *, connection=None):
    registry = get_registry_store()
    registry._ensure()
    from contextlib import nullcontext
    with nullcontext(connection) if connection is not None else registry.database.read() as conn:
        return conn.execute(f'SELECT 1 FROM "{registry.database.schema}".deleted_identities WHERE resource_id=%s',
                            (resource_id,)).fetchone() is not None


def excluded_from_scan(resource_id, path):
    """Deletion is intentional absence, never an unreadable-source fallback."""
    return is_deleted(resource_id) or bool(get_directory_cleanup_store().pending_at(path))


def unregister_identity(resource_id, *, connection, actor=''):
    """Identity tombstones contain no paths and prevent disk rediscovery."""
    registry = get_registry_store()
    registry._ensure()
    connection.execute(f'INSERT INTO "{registry.database.schema}".deleted_identities(resource_id) VALUES (%s) ON CONFLICT DO NOTHING',
                       (resource_id,))
    from .config import resource_database_schema
    from management_common.portal_database import portal_database
    table = f'"{portal_database(schema=resource_database_schema()).schema}".resource_registration_operations'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0]:
        connection.execute(f'UPDATE {table} SET deleted=TRUE WHERE resource_id=%s', (resource_id,))
    from management_common.directory_retention import RetentionStore
    RetentionStore(connection=connection).release_owner(resource_id, connection=connection)
    from management_portal import permission_system
    permission_system.unregister_resources([resource_id], connection=connection)
    request_projection_release(resource_id, actor=actor, connection=connection)


def register_owned(collection, resource_id, paths, *, scanning=False, connection=None):
    """Persist a file-backed owner's registration; disk files remain content only.

    This is the owner's record in ResourceService's existing registry, not a
    second directory index. Its directory entries are the path authority.
    """
    if collection not in {'paper', 'knowledge'}:
        raise ValueError('Unsupported file-backed owner')
    paths = tuple(paths)
    if not paths:
        raise ValueError('Resource directory registration requires a path')
    queue = get_directory_cleanup_store()
    if scanning and connection is None:
        # Observing an existing owner does not allocate/move a path. Keep this
        # branch read-only: a deletion after the reads cannot be undone by a
        # late scan. Unknown identities still take the locked writer below.
        previous = get_registry_store().get(collection, resource_id)
        if previous is not None:
            known = {str(Path(located_path(item['path']))) for item in locations(collection, previous)}
            incoming = {str(Path(path)) for path in paths}
            if known != incoming or any(queue.pending_at(path) for path in paths) or is_deleted(resource_id):
                return None
            return previous
    context = directory_writer(collection) if connection is None else get_registry_store().writer(collection, connection=connection)
    with context as writer:
        queue.lock(writer.connection)
        if is_deleted(resource_id, connection=writer.connection):
            if scanning:
                return None
            raise KeyError(resource_id)
        previous = writer.get(collection, resource_id)
        if scanning and previous is not None:
            # A scan observes the registered location; it cannot move an ID.
            # A relocation can temporarily publish its new files while the
            # source remains authoritative. Do not adopt that second path or
            # reject an unchanged authoritative read due to its move lease.
            known = {str(Path(located_path(item['path']))) for item in locations(collection, previous)}
            incoming = {str(Path(path)) for path in paths}
            if known != incoming:
                return None
            if any(queue.pending_at(path, connection=writer.connection) for path in paths):
                return None
            return previous
        for path in paths:
            if scanning and queue.pending_at(path, connection=writer.connection):
                return None
            try:
                queue.assert_available(path, connection=writer.connection)
            except FileExistsError:
                if scanning:
                    return None
                raise
        if previous is not None:
            known = {str(Path(located_path(item['path'])).resolve(strict=False)) for item in locations(collection, previous)}
            incoming = {str(Path(path).resolve(strict=False)) for path in paths}
            if known != incoming:
                raise ValueError('Resource directory differs from its owner registration: ' + resource_id)
            return previous
        document = {'id': resource_id, 'directories': [
            {'path': stored_location(path), 'owner_id': resource_id} for path in paths
        ], 'directory': {'owner_id': resource_id}}
        writer.put(collection, resource_id, document)
        return document


def unregister_owned(collection, resource_id, *, actor='', projection_ids=()):
    document, cleanup_ids = unregister_registry(collection, resource_id, actor=actor, projection_ids=projection_ids)
    return {'deleted': True, 'cleanup_ids': cleanup_ids, 'directory': document.get('directory', {})}


@contextmanager
def directory_writer(*collections):
    """Lock paths before owner registries, consistently with deletion/moves."""
    queue = get_directory_cleanup_store()
    registry = get_registry_store()
    registry._ensure()
    with registry.database.transaction() as connection:
        queue.lock(connection)
        with registry.writer(*collections, connection=connection) as writer:
            yield writer


def owned_locations(entries, resource_id):
    """Declare ownership identically for every registered location."""
    for entry in entries:
        entry['owner_id'] = resource_id
    return entries


def locations(collection, document):
    if collection == 'repo':
        entries = document.get('checkouts') or []
        canonical = document.get('canonical_checkout')
    elif collection == 'article':
        entries = document.get('roots') or []
        canonical = document.get('canonical_root')
    elif collection in {'paper', 'knowledge', 'report'}:
        return document.get('directories') or []
    else:
        raise ValueError('Registry collection has not declared its directory locations')
    result = [entry for entry in entries if entry.get('path')]
    if canonical and canonical not in {entry['path'] for entry in result}:
        result.append({'path': canonical, 'owner_id': document['id']})
    return result


def unregister_nested(roots, *, connection, actor):
    if not roots:
        return []
    return unregister_tree(roots, connection=connection, actor=actor)['resource_ids']


def unregister_tree(roots=(), *, connection, actor, workspace_ids=(), owner_ids=(), deleted_at=None, cleanup_owner=enqueue_directory_cleanup):
    """Every directory owner uses one closure and one registration transaction.

    A durable artifact owner can retain filesystem cleanup responsibility.
    Workspace identity owns its deletion; Society's binding constraint reacts
    in the same database transaction without an upward Python dependency.
    """
    from management_common.directory_paths import contains, lexical_path
    from management_common.directory_retention import RetentionStore
    from .workspace_store import get_workspace_store, now
    queue = get_directory_cleanup_store()
    queue.lock(connection)
    workspace = get_workspace_store()
    connection.execute(f'LOCK TABLE {workspace.table} IN SHARE ROW EXCLUSIVE MODE')
    rows = connection.execute(f'SELECT * FROM {workspace.table} WHERE deleted_at IS NULL ORDER BY workspace_id FOR UPDATE').fetchall()
    registry = get_registry_store()
    roots = {lexical_path(root) for root in roots}
    selected, owners, resource_ids = set(workspace_ids), set(owner_ids), set()
    with registry.writer(*COLLECTIONS, IGNORE_COLLECTION, connection=connection) as writer:
        documents = [(collection, identifier, locations(collection, document))
                     for collection in COLLECTIONS for identifier, document in writer.documents(collection).items()]
        while True:
            before = (len(roots), len(selected), len(owners), len(resource_ids))
            for root in roots:
                validate_removal(root)
            for row in rows:
                path = Path(located_path(row['path']))
                if row['workspace_id'] in selected or row['owner_id'] in owners or any(contains(root, path) for root in roots):
                    selected.add(row['workspace_id'])
                    owners.add(row['workspace_id'])
                    roots.add(path)
            for _collection, identifier, entries in documents:
                if identifier in owners or any(contains(root, Path(located_path(item['path']))) for item in entries for root in roots):
                    resource_ids.add(identifier)
                    owners.add(identifier)
                    roots.update(Path(located_path(item['path'])) for item in entries)
            if before == (len(roots), len(selected), len(owners), len(resource_ids)):
                break
        timestamp = deleted_at or now()
        deleted_paths = []
        retention = RetentionStore(connection=connection)
        retention.unregister_tree(roots, connection=connection)
        for owner in owners:
            retention.release_owner(owner, connection=connection)
        for row in rows:
            if row['workspace_id'] not in selected:
                continue
            workspace.mark_deleted(row['workspace_id'], deleted_at=timestamp, deleted_by=actor, connection=connection)
            unregister_identity(row['workspace_id'], connection=connection, actor=actor)
            path = located_path(row['path'])
            deleted_paths.append(path)
            cleanup_owner(path, owner_id=row['workspace_id'], operation_id='delete:' + row['workspace_id'], actor=actor, connection=connection)
        for collection, identifier, entries in documents:
            if identifier not in resource_ids:
                continue
            writer.delete(collection, identifier)
            suppress_removed(writer, collection, identifier, entries)
            unregister_identity(identifier, connection=connection, actor=actor)
            for item in entries:
                cleanup_owner(located_path(item['path']), owner_id=identifier,
                              operation_id='delete:' + identifier, actor=actor, connection=connection)
    return {'resource_ids': sorted(resource_ids | selected), 'workspace_paths': deleted_paths}


def unregister_registry(collection, resource_id, *, actor='', projection_ids=()):
    with directory_writer(*COLLECTIONS, IGNORE_COLLECTION) as writer:
        document = writer.get(collection, resource_id)
        if document is None:
            raise KeyError(resource_id)
        entries = locations(collection, document)
        writer.delete(collection, resource_id)
        suppress_removed(writer, collection, resource_id, entries)
        unregister_identity(resource_id, connection=writer.connection, actor=actor)
        for projection_id in projection_ids:
            request_projection_release(projection_id, actor=actor, connection=writer.connection)
        roots = [Path(located_path(item['path'])) for item in entries]
        for root in roots:
            validate_removal(root)
        cleanup_ids = [enqueue_directory_cleanup(root, owner_id=resource_id, operation_id='delete:' + resource_id,
                                     actor=actor, connection=writer.connection) for root in roots]
        unregister_nested(roots, connection=writer.connection, actor=actor)
    return document, cleanup_ids
