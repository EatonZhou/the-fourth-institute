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

from .registry_store import get_registry_store, registered_paths
from .registry_directory_records import COLLECTIONS, locations, refresh_directory_dependency

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


def unregister_identity(resource_id, *, connection, actor='', preserved_paths=()):
    """Identity tombstones contain no paths and prevent disk rediscovery."""
    from management_common.directory_retention import RetentionStore
    RetentionStore(connection=connection).release_owner(resource_id, connection=connection,
                                                       preserved_paths=preserved_paths)
    _unregister_identity_metadata(resource_id, connection=connection, actor=actor)


def _unregister_identity_metadata(resource_id, *, connection, actor='', release_projections=True):
    """Enlist tombstone/projection/permissions after the directory owner decision."""
    registry = get_registry_store()
    registry._ensure()
    connection.execute(f'INSERT INTO "{registry.database.schema}".deleted_identities(resource_id) VALUES (%s) ON CONFLICT DO NOTHING',
                       (resource_id,))
    from .config import resource_database_schema
    from management_common.portal_database import portal_database
    table = f'"{portal_database(schema=resource_database_schema()).schema}".resource_registration_operations'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0]:
        connection.execute(f'UPDATE {table} SET deleted=TRUE WHERE resource_id=%s', (resource_id,))
    from management_portal import permission_system
    permission_system.unregister_resources([resource_id], connection=connection)
    if release_projections:
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



def unregister_nested(roots, *, connection, actor):
    if not roots:
        return []
    return unregister_tree(roots, connection=connection, actor=actor)['resource_ids']


def retire_workspace_registration(row, *, connection, actor, pending_dependency='unknown',
                                  pending_diagnostics=()):
    """Retire only this Workspace identity, preserving independent registrations.

    The Workspace owner has locked/authorized the row and checked its usage
    bindings in this transaction. Installed owner dependencies prove that this
    exact identity has no remaining owned artifacts or dependent operations.
    Physical containment does not establish ownership: independent registrations
    and their bindings stay untouched, including registrations at the same path.
    No paths are rewritten and no physical cleanup is scheduled, even if the
    original parent is later restored. Keep that decision in the retirement log.
    """
    import json
    from management_common import directory_dependencies as deps
    from management_common.directory_paths import cleanup_location, cleanup_path, lexical_path, DirectoryComparison
    from .creation import creation_dependency_record, CreationDependencyUnknown
    from .workspace_store import get_workspace_store, now

    identity = row['workspace_id']
    if row['owner_id'] != identity:
        raise ValueError('Workspace is still owned by another identity')
    queue = get_directory_cleanup_store()
    queue.lock(connection)
    deps.require_ready(connection)
    root = lexical_path(located_path(row['path']))
    location = cleanup_location(root)
    own = connection.execute(f'SELECT * FROM {deps.schema()}.directory_dependencies '
                            "WHERE kind='workspace' AND object_id=%s", (identity,)).fetchall()
    if (len(own) != 1 or own[0]['owner_id'] != identity or own[0]['location'] != location
            or own[0]['disposition'] != 'owned'):
        raise deps.DirectoryDependenciesMissing('Workspace directory ownership evidence is incomplete')
    # Conservative lexical candidates include case aliases. Check claims at
    # both locations; independent nested resources are not a deletion closure.
    roots = list(dict.fromkeys((root, root.resolve(strict=False))))
    keys = [deps.path_keys(path)[0] for path in roots]
    ancestors = sorted({key for path in roots for key in deps.path_keys(path)[1]})
    owned = connection.execute(f'SELECT 1 FROM {deps.schema()}.directory_dependencies '
        "WHERE owner_id=%s AND NOT (kind='workspace' AND object_id=%s) LIMIT 1",
        (identity, identity)).fetchone()
    if owned:
        raise ValueError('Workspace still has other owned directories or dependent operations')
    if pending_dependency != 'independent':
        from .errors import ResourceOperationError
        present = pending_dependency == 'dependent'
        error = ResourceOperationError('instruction.invalid_arguments',
            'Workspace has pending identity dependencies' if present else
            'Workspace has unknown pending identity dependencies or operation claims', details={
                'reason': 'workspace_dependency_present' if present else 'workspace_dependency_unknown',
                'dependency_diagnostics': list(pending_diagnostics),
            })
        error.commit_state = 'not_committed'
        raise error
    claims = connection.execute(f'SELECT kind,object_id,owner_id,location FROM {deps.schema()}.directory_dependencies '
        "WHERE disposition='claim' AND (path_key=ANY(%s::text[]) "
        'OR ancestors && %s::text[])', (ancestors, keys))
    comparison = DirectoryComparison()
    for claim in claims:
        path = cleanup_path(claim['location'])
        strict_child = all(
            any(comparison.contains(root, candidate) for root in roots)
            and not any(comparison.contains(candidate, root) for root in roots)
            for candidate in (path, path.resolve(strict=False)))
        independent = False
        if strict_child and claim['kind'] == 'cleanup':
            # Existing independent cleanup remains its owner's responsibility;
            # this operation neither consumes nor creates its work.
            independent = bool(claim['owner_id'] and claim['owner_id'] != identity)
        elif strict_child and claim['kind'] == 'reservation':
            try:
                evidence = creation_dependency_record(claim['object_id'], connection=connection)
            except CreationDependencyUnknown:
                evidence = None
            independent = bool(evidence and not evidence['completed'] and not evidence['aborted']
                and evidence['workspace_dependencies'] is not None
                and identity not in evidence['workspace_dependencies']['workspace_ids']
                and claim['object_id'] in evidence['claim_operation_ids'])
        if not independent:
            raise ValueError('Workspace overlaps pending directory cleanup or operation claims')
    store = get_workspace_store()
    timestamp = now()
    plan = {'plan_id': 'workspace-retirement:' + identity, 'mode': 'registration_only',
            'reason': 'preserve_files', 'actor': actor, 'retired_at': timestamp,
            'resource_ids': [identity], 'workspace_ids': [identity],
            'workspace_paths': [str(root)], 'preserved_paths': [str(path) for path in roots],
            'binding_count': 0, 'retired_nested_count': 0,
            'preserves_independent_registrations': True, 'preserves_projections': True,
            'preserves_independent_claims': True,
            'cleanup_roots': [], 'cleanup_ids': []}
    store.mark_deleted(identity, deleted_at=timestamp, deleted_by=actor, connection=connection)
    # Discussion projection release can delete its Resident and owned files.
    # Registration-only retirement must not schedule that indirect cascade.
    _unregister_identity_metadata(identity, connection=connection, actor=actor,
                                  release_projections=False)
    connection.execute(f'INSERT INTO {deps.schema()}.identity_retirements(identity,root,plan) '
                       'VALUES (%s,%s,%s::jsonb)', (identity, location, json.dumps(plan)))
    return plan



def unregister_tree(roots=(), *, connection, actor, workspace_ids=(), owner_ids=(), deleted_at=None,
                    cleanup_owner=enqueue_directory_cleanup, preserve_resources=False):
    """Every directory owner uses one closure and one registration transaction.

    A durable artifact owner can retain filesystem cleanup responsibility.
    Workspace identity owns its deletion; Society's binding constraint reacts
    in the same database transaction without an upward Python dependency.
    """
    from management_common.directory_paths import DirectoryComparison, lexical_path, deletion_roots
    from management_common.directory_retention import RetentionStore
    from .workspace_store import get_workspace_store, now
    queue = get_directory_cleanup_store()
    queue.lock(connection)
    workspace = get_workspace_store()
    # The directory queue serializes registration, movement and deletion. Read
    # the closure without locking unrelated FK targets used by Resident writes.
    rows = connection.execute(f'SELECT * FROM {workspace.table} WHERE deleted_at IS NULL ORDER BY workspace_id').fetchall()
    registry = get_registry_store()
    roots = {lexical_path(root) for root in roots}
    selected, owners, resource_ids = set(workspace_ids), set(owner_ids), set()
    with registry.writer(*COLLECTIONS, IGNORE_COLLECTION, connection=connection) as writer:
        documents = [(collection, identifier, locations(collection, document))
                     for collection in COLLECTIONS for identifier, document in writer.documents(collection).items()]
        # Only this closure decision shares path evidence; subsequent cleanup
        # decisions retain their own fresh filesystem/instance observations.
        comparison = DirectoryComparison()
        paths = {}
        def directory_path(location):
            if location not in paths:
                paths[location] = Path(located_path(location))
            return paths[location]
        preserved = tuple(directory_path(item['path']) for _, _, entries in documents
                          for item in entries) if preserve_resources else ()
        explicit_owners = set(owner_ids)
        while True:
            before = (len(roots), len(selected), len(owners), len(resource_ids))
            for root in roots:
                validate_removal(root)
            for row in rows:
                path = directory_path(row['path'])
                if (row['workspace_id'] not in selected and row['owner_id'] not in explicit_owners
                        and any(comparison.contains(protected, path) for protected in preserved)):
                    continue
                if row['workspace_id'] in selected or row['owner_id'] in owners or any(comparison.contains(root, path) for root in roots):
                    selected.add(row['workspace_id'])
                    owners.add(row['workspace_id'])
                    roots.add(path)
            for _collection, identifier, entries in documents:
                if preserve_resources:
                    continue
                if identifier in owners or any(comparison.contains(root, directory_path(item['path'])) for item in entries for root in roots):
                    resource_ids.add(identifier)
                    owners.add(identifier)
                    roots.update(directory_path(item['path']) for item in entries)
            if before == (len(roots), len(selected), len(owners), len(resource_ids)):
                break
        selected_rows = [row for row in rows if row['workspace_id'] in selected]
        rows = connection.execute(
            f'SELECT * FROM {workspace.table} WHERE workspace_id=ANY(%s) '
            'AND deleted_at IS NULL ORDER BY workspace_id FOR UPDATE',
            ([row['workspace_id'] for row in selected_rows],)).fetchall()
        # Ownership handoffs take the directory queue, so none finishes after the
        # closure was read. Refuse a selection changed by a writer that bypassed
        # the queue rather than retire its previous owner view.
        def ownership_snapshot(items):
            return {(row['workspace_id'], row['path'], row['owner_id']) for row in items}
        if ownership_snapshot(rows) != ownership_snapshot(selected_rows):
            raise RuntimeError('Workspace ownership changed during deletion; retry')
        timestamp = deleted_at or now()
        deleted_paths = []
        retention = RetentionStore(connection=connection)
        cleanup_roots = list(dict.fromkeys(path for root in roots
                             for path in deletion_roots(root, preserved)))
        retention.unregister_tree(cleanup_roots, connection=connection)
        for owner in owners:
            retention.release_owner(owner, connection=connection, preserved_paths=preserved)
        for row in rows:
            if row['workspace_id'] not in selected:
                continue
            workspace.mark_deleted(row['workspace_id'], deleted_at=timestamp, deleted_by=actor, connection=connection)
            unregister_identity(row['workspace_id'], connection=connection, actor=actor,
                                preserved_paths=preserved)
            path = located_path(row['path'])
            deleted_paths.append(path)
            for cleanup_root in deletion_roots(path, preserved):
                cleanup_owner(cleanup_root, owner_id=row['workspace_id'], operation_id='delete:' + row['workspace_id'], actor=actor, connection=connection)
        for collection, identifier, entries in documents:
            if identifier not in resource_ids:
                continue
            writer.delete(collection, identifier)
            suppress_removed(writer, collection, identifier, entries)
            unregister_identity(identifier, connection=connection, actor=actor)
            for item in entries:
                cleanup_owner(located_path(item['path']), owner_id=identifier,
                              operation_id='delete:' + identifier, actor=actor, connection=connection)
    return {'resource_ids': sorted(resource_ids | selected), 'workspace_paths': deleted_paths,
            'preserved_paths': list(preserved), 'cleanup_roots': cleanup_roots}


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
