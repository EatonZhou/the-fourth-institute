"""The Resident record is a projection of closed domain stores.

:module: lineage.service
:design: decision_d918ebebc062
"""
import pytest
from management_common.json_columns import json_value
from management_portal.resources.workspace_store import get_workspace_store

def _identity(rid, **fields):
    workspace = get_workspace_store().ensure("workspace_" + rid[-12:], "/tmp/" + rid, title="Test Workspace")
    return {"owned_workspace_id": workspace, **fields}

from management_portal.society_service import resident_storage as contract
from management_portal.society_service.state_store import LineageDataStore, lineage_data_schema_matches


def test_schema_has_explicit_owners_and_no_document_catch_all():
    """Resident 数据表每列有明确归属，不含通用 JSON 文档列和已退役的表

    :design-point:"""
    store = LineageDataStore()
    with store.database.read() as connection:
        assert lineage_data_schema_matches(connection)
        columns = {(row['table_name'], row['column_name']) for row in connection.execute(
            'SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=current_schema()'
        ).fetchall()}
    assert ('residents', 'lineage_id') not in columns
    assert ('lineage_nodes', 'lineage_id') in columns
    assert ('resident_execution_configuration', 'execution_settings_json') in columns
    assert not any(table == 'resident_initializations' for table, _ in columns)
    assert ('resident_derivations', 'fork_mode') in columns
    assert ('residents', 'deleted_at') in columns
    assert ('residents', 'deleted_by') in columns
    assert ('residents', 'visibility') not in columns
    assert not any(column in {'profile_json', 'present_fields_json', 'membership_json', 'derivation_json', 'runtime_json'}
                   or table == 'resident_memberships' for table, column in columns)
    assert not any(table in {'resident_deletions', 'resident_watchboards'} for table, _ in columns)
    assert ('conversation_states', 'runtime_settings_json') not in columns


@pytest.mark.parametrize('field', ['watchboard', 'extensions', 'extra', 'metadata', 'profile_json', 'lineage_membership', 'native_codex_fork', 'runtime_settings', 'visibility', 'fork_context', 'fork_method', 'context_seed_applied_at'])
def test_unknown_or_retired_fields_are_rejected(field):
    """Resident 记录中的未知或已退役字段被拒绝

    :design-point:"""
    with pytest.raises(ValueError, match='Unknown Resident fields'):
        contract.validate_record_fields({field: {}})


def test_configuration_backend_has_one_authority_and_closed_keys():
    """执行配置的 backend 只来自 Resident 身份，写入未知设置或更换 backend 被拒绝

    :design-point:"""
    store = LineageDataStore()
    rid = 'resident_111111111111'
    with store.database.transaction() as connection:
        contract.put_fields(connection, 'residents', contract.IDENTITY_FIELDS, rid, _identity(rid, backend='codex'))
        contract.put_fields(connection, 'resident_execution_configuration', contract.CONFIGURATION_FIELDS, rid,
                            {'execution_settings': {'model': 'test-model'}, 'harness_config': {}})
    actual = store.read_resident_execution_settings(rid)
    assert actual['backend'] == 'codex'
    assert actual['model'] == 'test-model'
    actual['model'] = 'changed-model'
    store.write_resident_execution_settings(rid, actual)
    with store.database.read() as connection:
        row = connection.execute('SELECT execution_settings_json FROM resident_execution_configuration WHERE resident_id=%s', (rid,)).fetchone()
    assert 'backend' not in json_value(row['execution_settings_json'])
    assert store.read_resident_execution_settings(rid)['model'] == 'changed-model'
    with pytest.raises(ValueError, match='Unsupported Resident execution settings'):
        store.write_resident_execution_settings(rid, {**actual, 'unbounded': {}})
    with pytest.raises(ValueError, match='Backend changes'):
        store.write_resident_execution_settings(rid, {**actual, 'backend': 'claude'})



def test_deleted_sparse_identity_needs_no_current_lineage_or_workspace():
    """已删除的 Resident 身份不需要当前 Lineage 或 Workspace，仅在包含已删除时可读

    :design-point:"""
    store = LineageDataStore()
    rid = 'resident_333333333333'
    with store.database.transaction() as connection:
        contract.put_fields(connection, 'residents', contract.IDENTITY_FIELDS, rid, {
            'title': 'Historical identity', 'lineage_id': 'lineage_333333333333',
            'deleted_at': '2026-10-08T10:00:00Z', 'historical_project_id': 'HistoricalProject',
        })
    with pytest.raises(KeyError):
        store.resident_record(rid)
    assert store.residents_by_id([rid]) == []
    assert store.residents_for_lineage('lineage_333333333333') == []
    record = store.resident_record(rid, include_deleted=True)
    assert record['title'] == 'Historical identity'
    assert record['project_id'] == 'HistoricalProject'
    assert 'backend' not in record
    assert 'workspace_path' not in record
    assert store.resident_deletion(rid) == record
    assert store.read_resident_execution_settings(rid) is None
    with pytest.raises(KeyError):
        store.write_resident_execution_settings(rid, {'backend':'codex'})


@pytest.mark.parametrize('entry', ['message', 'info', 'job'])
def test_deleted_identity_cannot_accept_work_after_a_stale_service_lookup(entry):
    """已删除的 Resident 不再接受消息、info 或执行记录

    :design-point:"""
    store = LineageDataStore()
    rid = 'resident_444444444444'
    with store.database.transaction() as connection:
        contract.put_fields(connection, 'residents', contract.IDENTITY_FIELDS, rid, _identity(rid))
    store.remove_residents([{'resident_id': rid, 'deleted_at': '2026-10-08T10:00:00Z', 'deleted_by': 'test'}])
    message = {'message_id': 'msg-test', 'content_parts': [{'type': 'text', 'text': 'late message'}]}
    with pytest.raises(KeyError):
        if entry == 'message':
            store.mailbox_accept(rid, message)
        elif entry == 'info':
            store.mailbox_hold_info(rid, message, held_at='2026-10-08T10:01:00Z', limit=10)
        else:
            store.put_resident_execution_record(rid, {'queue': [], 'running': True})
    assert store.mailbox_entries([rid]) == []
    assert store.resident_execution_record(rid) == {}
