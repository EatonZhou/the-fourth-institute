"""Object deletion owns its directory cleanup in the identity transaction.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
import pytest

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.instance_manifest import current_instance
from management_portal.society_service.resident_workspace_deletion import delete_workspaces, remove_deleted_folder
from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace
from tests.cases.test_resident_folder_creation_contract import future_creation_store, resident_domain, _new_session_payload


def test_invalid_outer_root_does_not_unregister_descendants(workspace):
    root = current_instance().area('projects')
    child = root / 'registered-child'
    child.mkdir(parents=True)
    wid = 'workspace_998877665544'
    workspace.store.ensure(wid, str(child), origin='adopted')
    from management_portal.society_service.state_store import get_lineage_data_store
    with pytest.raises(ValueError, match='root|ancestor'):
        get_lineage_data_store().database.write(lambda c: delete_workspaces(
            c, paths=[root], deleted_at='2026-11-02T00:00:00Z', deleted_by='sample_admin',
        ))
    assert workspace.store.rows([wid])


def test_folder_cleanup_requires_owner_transaction(workspace):
    with pytest.raises(TypeError):
        remove_deleted_folder(workspace.root)


def test_folder_cleanup_and_nested_registration_rollback_together(workspace):
    from management_portal.society_service.state_store import get_lineage_data_store
    wid = 'workspace_998877665544'
    child = workspace.root / 'child'
    child.mkdir()
    workspace.store.ensure(wid, str(child), origin='adopted')
    with pytest.raises(RuntimeError, match='abort owner'):
        with get_lineage_data_store().database.transaction() as connection:
            remove_deleted_folder(workspace.root, connection=connection,
                                  owner_id='resident_112233445566', actor='sample_admin')
            assert not workspace.store.rows([wid], connection=connection)
            raise RuntimeError('abort owner')
    assert workspace.store.rows([wid])
    assert not get_directory_cleanup_store().status(owner_id='resident_112233445566')


def test_created_owner_tree_deletes_bound_workspaces_without_stopping_reader(future_creation_store):
    from pathlib import Path
    from management_portal.society_service import resident_workspace_domain, lineage_lifecycle, resident_catalog
    from management_portal.resources.workspace_store import get_workspace_store
    owner = resident_domain.register_resident(_new_session_payload('Owner'), 'sample_admin')
    reader = resident_domain.register_resident(_new_session_payload('Reader'), 'sample_admin')
    path = Path(owner['owned_workspace']['path'])
    (path / 'keep-until-cleanup').write_text('data')
    resident_workspace_domain.add_resident_reference_workspace(reader['resident_id'], str(path), 'sample_admin',
                                                               workspace_id=owner['owned_workspace_id'])
    lineage_lifecycle.delete_lineage(owner['lineage_id'], 'sample_admin')
    from management_portal.resources.directory_registrations import is_deleted
    assert all(is_deleted(identity) for identity in (
        owner['resident_id'], owner['lineage_id'], owner['owned_workspace_id']))
    assert path.exists()
    assert not get_workspace_store().rows([owner['owned_workspace_id']])
    current_reader = resident_catalog.get_catalog().get(reader['resident_id'])
    assert current_reader['intake_open'] is True
    assert owner['owned_workspace_id'] not in {b['workspace_id'] for b in current_reader['workspace_bindings']}
    get_directory_cleanup_store().run_pending()
    assert not path.exists()


@pytest.mark.parametrize('origin', ['created', 'adopted'])
def test_external_workspace_follows_its_owner_and_origin(future_creation_store, tmp_path, origin):
    from management_portal.society_service import lineage_lifecycle
    from management_portal.resources.workspace_store import get_workspace_store
    owner = resident_domain.register_resident(_new_session_payload('Owner'), 'sample_admin')
    external = tmp_path / 'external'
    external.mkdir()
    wid = 'workspace_aabbccddeeff'
    get_workspace_store().ensure(wid, str(external), origin=origin, owner_id=owner['resident_id'])
    lineage_lifecycle.delete_lineage(owner['lineage_id'], 'sample_admin')
    assert not get_workspace_store().rows([wid])
    get_directory_cleanup_store().run_pending()
    assert external.exists() == (origin == 'adopted')


def test_nested_resource_other_created_checkout_expands_workspace_cascade(workspace, tmp_path):
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.society_service.state_store import get_lineage_data_store
    registry = get_registry_store()
    external = tmp_path / 'other-checkout'
    child = external / 'workspace'
    child.mkdir(parents=True)
    wid = 'workspace_998877665544'
    workspace.store.ensure(wid, str(child), origin='adopted')
    rid = 'repo_112233445566'
    with registry.writer('repo') as writer:
        writer.put('repo', rid, {'id': rid, 'checkouts': [
            {'path': str(workspace.root / 'repo'), 'origin': 'adopted'},
            {'path': str(external), 'origin': 'created'}]})
    get_lineage_data_store().database.write(lambda connection: remove_deleted_folder(
        workspace.root, connection=connection, owner_id='owner', actor='sample_admin'))
    assert not workspace.store.rows([wid])
    assert registry.get('repo', rid) is None


@pytest.mark.parametrize('terminal', ['cancelled', 'running'])
def test_deletion_cancels_execution_before_identity_commit(future_creation_store, monkeypatch, terminal):
    from types import SimpleNamespace
    from management_portal.society_service import lineage_lifecycle, resident_catalog
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.society_service.domain_ports import execution_port
    owner = resident_domain.register_resident(_new_session_payload('Running'), 'sample_admin')
    rid = owner['resident_id']
    events = []
    monkeypatch.setattr(get_lineage_data_store(), 'resident_execution_record', lambda _: {
        'execution_id': 'execution-test', 'submitted_at': '2026-11-02T00:00:00Z', 'actor': 'sample_admin'})
    def cancel(execution_id, **kwargs):
        assert resident_catalog.get_catalog().get(rid)['resident_id'] == rid
        events.append('cancel')
    def wait(execution_id, **kwargs):
        assert events == ['cancel']
        events.append('wait')
        return {'status': terminal}
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(cancel=cancel, wait=wait))
    if terminal == 'running':
        with pytest.raises(RuntimeError, match='termination'):
            lineage_lifecycle.delete_lineage(owner['lineage_id'], 'sample_admin')
        assert resident_catalog.get_catalog().get(rid)['resident_id'] == rid
        assert not get_directory_cleanup_store().status(owner_id=rid)
    else:
        lineage_lifecycle.delete_lineage(owner['lineage_id'], 'sample_admin')
        assert get_directory_cleanup_store().status(owner_id=rid)
    assert events == ['cancel', 'wait']


def test_lifecycle_migration_uses_caller_transaction_and_is_idempotent(future_creation_store):
    from management_portal.society_service.state_store import get_lineage_data_store
    from scripts.directory_protocol_lifecycle_migration import migrate
    owner = resident_domain.register_resident(_new_session_payload('Migration'), 'sample_admin')
    store = get_lineage_data_store()
    store.database.write(lambda connection: connection.execute('UPDATE residents SET directory_json=NULL'))
    with pytest.raises(RuntimeError, match='abort migration'):
        with store.database.transaction() as connection:
            assert migrate(connection)['residents'] == 1
            raise RuntimeError('abort migration')
    with store.database.read() as connection:
        assert connection.execute('SELECT directory_json FROM residents WHERE resident_id=%s',
                                  (owner['resident_id'],)).fetchone()[0] is None
    with store.database.transaction() as connection:
        assert migrate(connection)['residents'] == 1
        assert migrate(connection)['residents'] == 0


def test_move_execution_scope_restores_original_intake_on_failure(future_creation_store):
    from management_portal.society_service.resident_lifecycle_domain import directory_move_execution_scope
    from management_portal.society_service import resident_catalog
    owner = resident_domain.register_resident(_new_session_payload('Move'), 'sample_admin')
    rid = owner['resident_id']
    with pytest.raises(RuntimeError, match='copy failed'):
        with directory_move_execution_scope([owner]):
            assert resident_catalog.get_catalog().get(rid)['intake_open'] is False
            raise RuntimeError('copy failed')
    assert resident_catalog.get_catalog().get(rid)['intake_open'] is True


def test_project_queue_failure_rolls_back_children_without_publishing_tombstones(future_creation_store, monkeypatch):
    from management_portal.society_service import project_domain, project_folders, resident_catalog
    from management_portal.society_service.resident_resource_projection import ResidentResourceProjectionWorker
    from tests.cases.registry_support import write_projects
    from management_portal.hatg.contracts import InstructionError
    owner = resident_domain.register_resident(_new_session_payload('Project child'), 'sample_admin')
    project_root = project_folders.project_root_for_id('project_test')
    write_projects({'schema_version': 1, 'projects': [{
        'id': 'project_test', 'path': str(project_root),
        'directory': {'origin': 'created', 'owner_id': 'project_test'}}]})
    published = []
    monkeypatch.setattr(ResidentResourceProjectionWorker, 'confirm_tombstone', lambda self, rid: published.append(rid))
    queue = get_directory_cleanup_store()
    enqueue = queue.enqueue
    def fail_project(path, **kwargs):
        if kwargs['owner_id'] == 'project_test':
            raise RuntimeError('project queue unavailable')
        return enqueue(path, **kwargs)
    monkeypatch.setattr(queue, 'enqueue', fail_project)
    with pytest.raises(InstructionError, match='project queue unavailable'):
        project_domain.delete_project('project_test', 'sample_admin')
    assert resident_catalog.get_catalog().get(owner['resident_id'])['resident_id'] == owner['resident_id']
    assert not queue.status(owner_id=owner['resident_id'])
    from management_portal.resources.directory_registrations import is_deleted
    assert not any(is_deleted(identity) for identity in ('project_test', owner['resident_id'], owner['lineage_id']))
    assert published == []
