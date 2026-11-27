"""The Resident record is a projection of closed domain stores.

:module: lineage.service
:design: decision_d918ebebc062
"""
import pytest
from tests.cases.directory_fixture_support import directory_owners, resident_identity_write  # noqa: F401
from management_common.json_columns import json_value
from management_portal.resources.workspace_store import get_workspace_store

def _identity(rid, **fields):
    from tests.cases.canonical_domain_fixture import canonical_projection
    canonical_projection(rid, **fields)
    record = LineageDataStore().resident_record(rid)
    return {"owned_workspace_id": record['owned_workspace_id'],
            "directory": record['directory'], **fields}

from management_portal.society_service import resident_storage as contract
from management_portal.society_service.state_store import LineageDataStore, lineage_data_schema_matches


def test_schema_has_explicit_owners_and_no_document_catch_all():
    """Resident 数据表每列有明确归属，不含通用 JSON 文档列和已删除的表

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
def test_unknown_or_deleted_fields_are_rejected(field):
    """Resident 记录中的未知或已删除字段被拒绝

    :design-point:"""
    with pytest.raises(ValueError, match='Unknown Resident fields'):
        contract.validate_record_fields({field: {}})


@pytest.mark.usefixtures("directory_owners")
def test_configuration_backend_has_one_authority_and_closed_keys():
    """执行配置的 backend 只来自 Resident 身份，写入未知设置或更换 backend 被拒绝

    :design-point:"""
    store = LineageDataStore()
    rid = 'resident_111111111111'
    identity = _identity(rid, backend='codex')
    with store.database.transaction() as connection, resident_identity_write(connection, rid):
        contract.put_fields(connection, 'residents', contract.IDENTITY_FIELDS, rid, identity)
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


@pytest.mark.usefixtures("directory_owners")
def test_stored_default_permission_preset_moves_to_each_providers_key_once():
    """已存的权限预设 default 按 Provider 一次性迁移到新键，再次启动不再改写

    Codex 改为 ask、Claude 改为 restricted、Kimi 改为 manual、Antigravity 改为 portal-tools-only，
    profile 随之改名；没有受限档的 Provider 去掉该预设，其他取值不变。

    :module: execution.providers
    :design: decision_8b2fa224616b"""
    from management_portal.society_service import permission_preset_migration

    store = LineageDataStore()
    stored = {
        'resident_222222222201': ('codex', {'permission_preset': 'default', 'profile': 'permissions:default', 'permissions': ':workspace'}),
        'resident_222222222202': ('claude', {'permission_preset': 'default', 'profile': 'permissions:default'}),
        'resident_222222222203': ('kimi', {'permission_preset': 'default'}),
        'resident_222222222204': ('antigravity', {'permission_preset': 'default'}),
        'resident_222222222205': ('dsh', {'permission_preset': 'default', 'profile': 'permissions:default'}),
        'resident_222222222206': ('codex', {'permission_preset': 'full-access', 'profile': 'permissions:full-access'}),
    }
    for rid, (backend, settings) in stored.items():
        with store.database.transaction() as connection, resident_identity_write(connection, rid):
            contract.put_fields(connection, 'residents', contract.IDENTITY_FIELDS, rid, _identity(rid, backend=backend))
            contract.put_fields(connection, 'resident_execution_configuration', contract.CONFIGURATION_FIELDS, rid,
                                {'execution_settings': {'model': 'm', **settings}, 'harness_config': {}})
    assert permission_preset_migration.migrate(store.database) == 5
    with store.database.read() as connection:
        rows = {row['resident_id']: json_value(row['execution_settings_json']) for row in connection.execute(
            'SELECT resident_id,execution_settings_json FROM resident_execution_configuration WHERE resident_id = ANY(%s)',
            (list(stored),)).fetchall()}
    assert [(rows[rid].get('permission_preset'), rows[rid].get('profile')) for rid in stored] == [
        ('ask', 'permissions:ask'), ('restricted', 'permissions:restricted'), ('manual', None),
        ('portal-tools-only', None), (None, ''), ('full-access', 'permissions:full-access'),
    ]
    assert rows['resident_222222222201']['permissions'] == ':workspace'
    assert permission_preset_migration.migrate(store.database) == 0



def test_deleted_sparse_identity_needs_no_current_lineage_or_workspace():
    """已删除的 Resident 身份不需要当前 Lineage 或 Workspace，仅在包含已删除时可读

    :design-point:"""
    store = LineageDataStore()
    rid = 'resident_333333333333'
    with store.database.transaction() as connection, resident_identity_write(connection, rid):
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


@pytest.mark.usefixtures("directory_owners")
@pytest.mark.parametrize('entry', ['message', 'info', 'job'])
def test_deleted_identity_cannot_accept_work_after_a_stale_service_lookup(entry):
    """已删除的 Resident 不再接受消息、info 或执行记录

    :design-point:"""
    store = LineageDataStore()
    rid = 'resident_444444444444'
    identity = _identity(rid)
    with store.database.transaction() as connection, resident_identity_write(connection, rid):
        contract.put_fields(connection, 'residents', contract.IDENTITY_FIELDS, rid, identity)
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
