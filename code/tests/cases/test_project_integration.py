"""Project deletion commits identity once and retries its owned effects.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cases.test_resident_folder_creation_contract import future_creation_store, resident_domain, _new_session_payload
from tests.cases.registry_support import write_projects
from management_portal.society_service import project_domain, project_folders, resident_workspace_domain
from management_portal.society_service.state_store import get_lineage_data_store


def project_with_child():
    child = resident_domain.register_resident(_new_session_payload('Project child'), 'sample_admin')
    root = project_folders.project_root_for_id('project_test')
    write_projects({'schema_version': 1, 'projects': [{
        'id': 'project_test', 'path': str(root),
        'directory': {'owner_id': 'project_test'}}]})
    return child, root


def test_project_termination_allows_another_database_connection(future_creation_store, monkeypatch):
    from management_portal.society_service.domain_ports import execution_port
    child, root = project_with_child()
    store = get_lineage_data_store()
    monkeypatch.setattr(store, 'resident_execution_record', lambda _: {
        'execution_id': 'execution-test', 'submitted_at': '2026-11-02T00:00:00Z', 'actor': 'sample_admin'})
    def update():
        with store.database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout='300ms'")
            row = connection.execute('UPDATE residents SET intake_open=FALSE WHERE resident_id=%s RETURNING resident_id',
                                     (child['resident_id'],)).fetchone()
            assert row
    def wait(*args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(update).result(timeout=5)
        return {'status': 'cancelled'}
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(cancel=lambda *a, **kw: None, wait=wait))
    result = project_domain.delete_project('project_test', 'sample_admin')
    assert result['resident_ids'] == [child['resident_id']]


@pytest.mark.parametrize('filename', ['resident.md', 'diary.md'])
def test_existing_resident_authored_file_is_preserved(tmp_path, filename):
    path = tmp_path / filename
    path.write_text('user content\n')
    resident_workspace_domain.ensure_resident_file(tmp_path, filename, initial_content='generated')
    assert path.read_text() == 'user content\n'


def test_workspace_document_does_not_resurrect_missing_folder(future_creation_store):
    import shutil
    from management_portal.society_service import resident_record_domain
    child, _ = project_with_child()
    folder = resident_record_domain.resident_folder_path(child)
    shutil.rmtree(folder)
    resident_workspace_domain.refresh_resident_workspace_document(child)
    assert not folder.exists()


def test_workspace_document_failure_retries_existing_outbox(future_creation_store, monkeypatch):
    from management_portal.society_service import resident_record_domain, resident_lifecycle_steps_domain
    from management_portal.society_service.resident_lifecycle import ResidentLifecycleOperations
    child, _ = project_with_child()
    store = get_lineage_data_store()
    operations = ResidentLifecycleOperations(store)
    rid = child['resident_id']
    operation = operations.enqueue(rid, [{'kind': 'workspace_document', 'resident_id': rid}])
    replace = resident_workspace_domain.os.replace
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(resident_workspace_domain.os, 'replace', fail)
    assert not operations.run(operation, resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    with store.database.read() as connection:
        row = connection.execute('SELECT last_error FROM resident_lifecycle_operations WHERE operation_id=%s', (operation,)).fetchone()
    assert 'disk unavailable' in row['last_error']
    monkeypatch.setattr(resident_workspace_domain.os, 'replace', replace)
    store.database.write(lambda connection: connection.execute(
        "UPDATE resident_lifecycle_operations SET available_at=clock_timestamp() WHERE operation_id=%s", (operation,)))
    assert operations.run(operation, resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == rid
    assert (resident_record_domain.resident_folder_path(child) / 'workspaces.md').exists()


def test_workspace_document_skips_pending_cleanup(future_creation_store):
    from management_portal.society_service import resident_record_domain
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    child, _ = project_with_child()
    folder = resident_record_domain.resident_folder_path(child)
    target = folder / 'workspaces.md'
    target.write_text('before deletion')
    get_lineage_data_store().database.write(lambda connection: get_directory_cleanup_store().enqueue(
        folder, owner_id=child['resident_id'], operation_id='test-pending', actor='sample_admin', connection=connection))
    resident_workspace_domain.refresh_resident_workspace_document(child)
    assert target.read_text() == 'before deletion'


def test_script_start_cannot_cross_project_deletion_boundary(future_creation_store, monkeypatch, tmp_path):
    from threading import Event
    from management_portal.society_service import resident_lifecycle_domain
    from management_portal.society_service.script_runs import service
    child, _ = project_with_child()
    entered, release, attempting = Event(), Event(), Event()
    def terminate(record):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(resident_lifecycle_domain, 'terminate_resident_work', terminate)
    monkeypatch.setattr(service, 'launch', lambda *args: pytest.fail('Deleted Lineage launched work'))
    script = tmp_path / 'late.py'
    script.write_text('pass')
    def start():
        attempting.set()
        return service.start({'path': str(script), 'lineage_id': child['lineage_id']}, 'sample_admin')
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(project_domain.delete_project, 'project_test', 'sample_admin')
        try:
            assert entered.wait(5)
            starting = pool.submit(start)
            assert attempting.wait(5)
            assert not starting.done()
        finally:
            release.set()
        assert deleting.result(timeout=5)['resident_ids'] == [child['resident_id']]
        with pytest.raises(KeyError):
            starting.result(timeout=5)


def test_project_reports_resources_in_child_owned_external_workspace(future_creation_store, tmp_path):
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.resources.workspace_store import get_workspace_store
    child, _ = project_with_child()
    external = tmp_path / 'external-work'
    external.mkdir()
    get_workspace_store().ensure('workspace_abcdefabcdef', str(external), owner_id=child['resident_id'])
    registry = get_registry_store()
    rid = 'repo_abcdefabcdef'
    with registry.writer('repo') as writer:
        writer.put('repo', rid, {'id': rid, 'checkouts': [{'path': str(external / 'repo'), 'owner_id': rid}]})
    result = project_domain.delete_project('project_test', 'sample_admin')
    assert rid in result['resource_ids']
    assert registry.get('repo', rid) is None
    assert not get_workspace_store().rows(['workspace_abcdefabcdef'])
