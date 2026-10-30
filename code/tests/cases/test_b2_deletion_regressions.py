""":module: spec.design-governance"""
from pathlib import Path

import pytest

from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service import resident_workspace_domain as domain
from tests.cases.test_resident_folder_creation_contract import (
    _new_session_payload, future_creation_store, resident_domain,
)


def test_deletion_retires_unused_independent_workspace(future_creation_store):
    """Lineage 内的独立 Workspace 无其他使用者时随删除清理"""
    record = resident_domain.register_resident(_new_session_payload('Deletion guard'), 'sample_admin')
    folder = Path(record['workspace_path']) / 'handoff-workspaces/copy'
    folder.mkdir(parents=True)
    wid = 'workspace_eeeeeeeeeeee'
    get_workspace_store().ensure(wid, str(folder))
    from management_portal.society_service import lineage_lifecycle
    lineage_lifecycle.delete_lineage(record['lineage_id'], 'sample_admin')
    for operation in resident_domain.ready_resident_lifecycle_operations():
        resident_domain.resume_resident_lifecycle_operation(operation)
    assert get_workspace_store().rows([wid]) == {}
    assert not folder.exists()


def test_project_deletion_retires_unused_nested_workspace(monkeypatch, tmp_path):
    """项目内无其他使用者的 Workspace 不阻止删除"""
    from tests.cases.test_project_deletion import _configure_project
    from management_portal.society_service import project_domain

    root, registry, audit, deleted = _configure_project(monkeypatch, tmp_path)
    nested = root / 'src'
    nested.mkdir()
    get_workspace_store().ensure('workspace_dddddddddddd', str(nested))
    project_domain.delete_project('Example', 'alice')
    assert not nested.exists()
    assert get_workspace_store().rows(['workspace_dddddddddddd']) == {}


def test_project_deletion_preserves_workspace_used_by_surviving_resident(monkeypatch, tmp_path):
    """删除项目保留仍被外部 Resident 使用的根 Workspace 和绑定

    :module: platform.core"""
    # Every Project registers its root as a Workspace; that alone refused to
    # delete FastRobotDemo. The root is the folder being deleted, so it retires.
    from tests.cases.test_project_deletion import _configure_project
    from management_portal.society_service import project_domain
    from management_portal.society_service.state_store import LineageDataStore

    root, registry, audit, deleted = _configure_project(monkeypatch, tmp_path)
    workspaces = get_workspace_store()
    workspaces.ensure('workspace_cccccccccccc', str(root))
    workspaces.ensure('workspace_bbbbbbbbbbbb', str(tmp_path / 'reader'))
    store = LineageDataStore()
    with store.database.transaction() as connection:
        connection.execute("INSERT INTO residents(resident_id,title,description,owned_workspace_id) VALUES (%s,%s,%s,%s)",
                           ('resident_cccccccccccc', 'Reader', 'Reads the project', 'workspace_bbbbbbbbbbbb'))
        connection.execute("INSERT INTO resident_workspace_bindings VALUES (%s,%s,%s)",
                           ('resident_cccccccccccc', 'workspace_cccccccccccc', 'project files'))

    project_domain.delete_project('Example', 'alice')

    assert root.exists()
    with workspaces.database.read() as connection:
        row = connection.execute(f"SELECT deleted_by FROM {workspaces.table} WHERE workspace_id=%s",
                                 ('workspace_cccccccccccc',)).fetchone()
    assert row['deleted_by'] is None
    with store.database.snapshot() as connection:
        assert connection.execute("SELECT count(*) AS n FROM resident_workspace_bindings").fetchone()['n'] == 1


def test_permanent_remove_failure_is_retained_without_retry():
    """永久删除失败保留错误状态并停止重试"""
    from management_common.json_columns import json_value
    from management_portal.society_service.resident_lifecycle import ResidentLifecycleOperations, LifecyclePermanentFailure
    from management_portal.society_service.state_store import LineageDataStore

    operations = ResidentLifecycleOperations(LineageDataStore())
    operation_id = operations.enqueue('resident_aaaaaaaaaaaa', [{'kind': 'remove'}])

    def fail(*_):
        raise LifecyclePermanentFailure('invalid operation payload')

    operations.run(operation_id, fail)
    assert operation_id not in operations.ready()
    operations.run(operation_id, lambda *_: pytest.fail('terminal operation retried'))
    with operations.store.database.read() as connection:
        row = connection.execute('SELECT payload_json,last_error FROM resident_lifecycle_operations WHERE operation_id=%s', (operation_id,)).fetchone()
    assert json_value(row['payload_json'])['status'] == 'failed'
    assert 'invalid operation payload' in row['last_error']
    assert operations.health()['failed'] == 1
