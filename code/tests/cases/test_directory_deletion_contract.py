"""Directory deletion commits identity changes before touching files.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
from pathlib import Path

import pytest

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.instance_manifest import current_instance
from management_portal.society_service import resident_workspace_domain
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_workspace_unregistration import workspace, call
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, resident_domain, _new_session_payload,
)
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace, WID


def test_nested_unregistration_returns_workspace_and_enlists_permissions(workspace, monkeypatch):
    from management_portal.resources.directory_registrations import unregister_tree
    from management_portal import permission_system
    calls = []
    monkeypatch.setattr(permission_system, 'unregister_resources',
                        lambda ids, *, connection=None: calls.append((list(ids), connection)))
    with workspace.store.database.transaction() as connection:
        result = unregister_tree(workspace_ids=[WID], connection=connection, actor='sample_admin')
        assert WID in result['resource_ids']
        assert ([WID], connection) in calls


def test_unregister_bound_workspace_commits_without_waiting_for_users(
    future_creation_store, workspace,
):
    resident = resident_domain.register_resident(_new_session_payload('Directory reader'), 'sample_admin')
    resident_workspace_domain.add_resident_reference_workspace(
        resident['resident_id'], str(workspace.root), 'sample_admin', workspace_id=WID,
    )
    result = call(workspace).raise_for_error()
    assert result['attributes']['status'] == 'deleted'
    assert workspace.store.rows([WID]) == {}
    with get_lineage_data_store().database.read() as connection:
        assert not connection.execute(
            'SELECT 1 FROM resident_workspace_bindings WHERE workspace_id=%s', (WID,),
        ).fetchall()


@pytest.mark.parametrize('origin', ['created', 'adopted'])
@pytest.mark.parametrize('internal', [True, False])
def test_origin_decides_cleanup_regardless_of_area(workspace, origin, internal):
    root = (current_instance().area('projects') if internal else workspace.root) / 'directory'
    root.mkdir(parents=True)
    (root / 'data').write_text('object data')
    wid = 'workspace_aaaaaaaaaaaa'
    workspace.store.ensure(wid, str(root), origin=origin)
    result = call(workspace, wid).raise_for_error()
    assert result['attributes']['status'] == 'deleted'
    assert root.exists()
    assert workspace.store.rows([wid]) == {}
    cleanup = get_directory_cleanup_store()
    jobs = cleanup.status(owner_id=wid)
    assert bool(jobs) == (origin == 'created')
    cleanup.run_pending()
    assert root.exists() == (origin == 'adopted')


@pytest.mark.parametrize('outer_origin', ['created', 'adopted'])
def test_outer_created_directory_removes_nested_adoptions(workspace, outer_origin):
    parent_id, child_id = 'workspace_aaaaaaaaaaaa', 'workspace_bbbbbbbbbbbb'
    parent = workspace.root / 'outer'
    child = parent / 'inner'
    child.mkdir(parents=True)
    workspace.store.ensure(parent_id, str(parent), origin=outer_origin)
    workspace.store.ensure(child_id, str(child), origin='adopted')
    call(workspace, parent_id).raise_for_error()
    assert bool(workspace.store.rows([child_id])) == (outer_origin == 'adopted')
    get_directory_cleanup_store().run_pending()
    assert child.exists() == (outer_origin == 'adopted')


def test_cleanup_failure_does_not_restore_registration(workspace, monkeypatch):
    from management_common import directory_cleanup_store
    def busy(_path):
        raise PermissionError('file is occupied')
    monkeypatch.setattr(directory_cleanup_store, 'remove_path', busy)
    call(workspace).raise_for_error()
    outcome, = get_directory_cleanup_store().run_pending()
    assert outcome['status'] == 'retry'
    assert workspace.store.rows([WID]) == {}
    assert workspace.root.exists()


def test_nested_registry_is_unregistered_in_same_transaction(workspace):
    from management_portal.resources.registry_store import get_registry_store
    registry = get_registry_store()
    repo_id, article_id = 'repo_111111111111', 'article_222222222222'
    with registry.writer('repo', 'article') as writer:
        writer.put('repo', repo_id, {'id': repo_id, 'checkouts': [{'path': str(workspace.root / 'repo')} ]})
        writer.put('article', article_id, {'id': article_id, 'roots': [{'path': str(workspace.root / 'article')} ]})
    call(workspace).raise_for_error()
    assert registry.get('repo', repo_id) is None
    assert registry.get('article', article_id) is None


def test_deleted_owned_workspace_does_not_destroy_resident_reads_or_revive_on_save(
    future_creation_store, workspace,
):
    from management_portal.society_service.resident_catalog import get_catalog
    resident = resident_domain.register_resident(_new_session_payload('Owner'), 'sample_admin')
    wid = resident['owned_workspace_id']
    call(workspace, wid).raise_for_error()
    current = get_catalog().get(resident['resident_id'])
    assert current['owned_workspace'] == {'workspace_id': wid, 'path': '', 'status': 'deleted'}
    assert current['default_cwd'] == ''
    get_lineage_data_store().put_resident_record(resident['resident_id'], current)
    assert not workspace.store.rows([wid])
    assert not get_catalog().get(resident['resident_id']).get('deleted_at')


def test_workspace_identity_deletion_enlists_binding_cleanup_and_projection(future_creation_store, workspace):
    from management_portal.resources.workspace_store import now
    resident = resident_domain.register_resident(_new_session_payload('Nested binding'), 'sample_admin')
    resident_workspace_domain.add_resident_reference_workspace(
        resident['resident_id'], str(workspace.root), 'sample_admin', workspace_id=WID,
    )
    store = get_lineage_data_store()
    with store.database.transaction() as connection:
        workspace.store.mark_deleted(WID, deleted_at=now(), deleted_by='sample_admin', connection=connection)
        assert not connection.execute('SELECT 1 FROM resident_workspace_bindings WHERE workspace_id=%s', (WID,)).fetchall()
        row = connection.execute('SELECT payload_json FROM resident_lifecycle_operations WHERE resident_id=%s', (resident['resident_id'],)).fetchone()
        from management_common.json_columns import json_value
        assert {'kind':'workspace_document', 'resident_id':resident['resident_id']} in json_value(row['payload_json'])['steps']


@pytest.mark.parametrize('origin', ['created', 'adopted'])
def test_resource_outer_directory_uses_the_same_workspace_cascade(future_creation_store, workspace, origin):
    from management_portal.resources.directory_registrations import register_owned, unregister_owned
    outer, wid = workspace.root / 'outer', 'workspace_aaaaaaaaaaaa'
    nested = outer / 'nested'
    nested.mkdir(parents=True)
    workspace.store.ensure(wid, str(nested), origin='adopted')
    resident = resident_domain.register_resident(_new_session_payload('Resource reader'), 'sample_admin')
    resident_workspace_domain.add_resident_reference_workspace(
        resident['resident_id'], str(nested), 'sample_admin', workspace_id=wid,
    )
    register_owned('knowledge', 'experiment_111111111111', [outer], origin=origin)
    unregister_owned('knowledge', 'experiment_111111111111', actor='sample_admin')
    assert bool(workspace.store.rows([wid])) == (origin == 'adopted')
    with get_lineage_data_store().database.read() as connection:
        bindings = connection.execute('SELECT 1 FROM resident_workspace_bindings WHERE workspace_id=%s', (wid,)).fetchall()
        assert bool(bindings) == (origin == 'adopted')
