"""Current Handoff locations and relocation of already registered copies.

Workspace IDs are authoritative. Historical attempts are immutable; current
configuration, target projections and current results follow directory moves.
No preparation, allocation, message dispatch or schema migration occurs here.
"""
from pathlib import Path

from management_portal.resources.workspace_store import get_workspace_store
from . import lineage_aggregate, lineage_state
from .lineage_handoffs import _handoff_edge, _handoff_lease
from .state_store import get_lineage_data_store
from .handoff_location_storage import synchronize


def reconcile_locations(lineage_id, handoff_id, actor):
    """Explicit current-reference repair; return missing targets without inventing readiness."""
    with _handoff_lease(lineage_id, handoff_id), lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(current, actor)
        _handoff_edge(current, handoff_id)
        return get_lineage_data_store().database.write(
            lambda c: synchronize(c, handoff_id=handoff_id, clear_missing=True))


def relocate_copy(lineage_id, handoff_id, item_id, actor, *, workspace_id,
                  expected_version, expected_owner, expected_path, operation_id):
    """Move an existing created copy, preserving identity and all usage bindings."""
    from .directory_relocation import relocate_nested, move_idle_directory
    with _handoff_lease(lineage_id, handoff_id), lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(current, actor)
        handoff = _handoff_edge(current, handoff_id)
        if handoff.get('status') == 'preparing':
            raise ValueError('Preparing Handoff must be recovered by its preparation owner first')
        item = next(x for x in handoff['items'] if x['item_id'] == item_id)
        if (item['transfer'].get('mode') != 'copy'
                or item['transfer'].get('method') not in {'filesystem_copy', 'git_worktree'}):
            raise ValueError('Only existing Handoff copies can be relocated')
        target_ids = {v for v in (item['target'].get('workspace_id'),
                                  item.get('result', {}).get('target_workspace_id')) if v}
        if target_ids != {workspace_id} or workspace_id == item['source'].get('workspace_id'):
            raise ValueError('Handoff copy Workspace identity does not match')
        if expected_owner not in {workspace_id, lineage_id}:
            raise ValueError('Handoff copy has a different directory owner')
        for part in (handoff_id, item_id):
            if not part or part in {'.', '..'} or Path(part).name != part or '\\' in part:
                raise ValueError('Invalid Handoff location component')
        target = lineage_state._lineage_dir(lineage_id) / 'handoff-workspaces' / handoff_id / item_id
        store = get_workspace_store()
        snapshot = dict(expected_version=expected_version, expected_owner=expected_owner,
                        expected_path=expected_path)
        from management_portal import permission_system
        from management_portal.resources.collaboration_registry import descriptor_resource_record
        from .lineage_access_policy import hidden_private_ids
        def check(connection):
            row = store.require_created_owner(workspace_id, connection=connection, **snapshot)
            if hidden_private_ids(actor, [descriptor_resource_record(store.descriptor(row))]):
                raise KeyError(workspace_id)
            if workspace_id not in permission_system.permitted_ids(actor, 'UpdateResource', 'resource_id', [workspace_id]):
                raise PermissionError('Workspace relocation requires UpdateResource permission')
        store.database.write(check)
        def update(connection, source, destination):
            check(connection)
            store.restore_self_owner(workspace_id, connection=connection, **snapshot)
            relocate_nested(connection, source, destination)
            return store.rows([workspace_id], connection=connection)[workspace_id]
        if Path(expected_path) == target:
            def repair(connection):
                check(connection)
                store.restore_self_owner(workspace_id, connection=connection, **snapshot)
                synchronize(connection, handoff_id=handoff_id)
                return store.rows([workspace_id], connection=connection)[workspace_id]
            return {'operation_id': '', 'workspace': store.database.write(repair)}
        op, row = move_idle_directory(Path(expected_path), target,
            owner_id=workspace_id, actor=actor,
            database=get_lineage_data_store().database, update=update, operation_id=operation_id)
        return {'operation_id': op, 'workspace': row}
