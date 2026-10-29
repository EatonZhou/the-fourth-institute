"""Runtime inputs accept canonical formats; rejected history remains unchanged.

:design: decision_15aab18c0ab3
:importance: P1
"""
from copy import deepcopy
from dataclasses import replace

import pytest

from management_common.json_columns import json_value
from management_common.portal_database import portal_database
from management_portal import runtime_status
from management_portal.execution.agent_data_store import AgentDataStore, get_agent_data_store
from management_portal.execution.agent_data_tables import initialize_agent_data
from management_portal.execution.context_seed import context_seed
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.scheduler import Scheduler
from management_portal.society_service.state_store import LineageDataStore


# The schema initialization each store runs at startup.
STORE_INITIALIZERS = {'AgentDataStore': initialize_agent_data, 'LineageDataStore': LineageDataStore._initialize}


@pytest.mark.parametrize('store,table,tag,schema', [
    ('AgentDataStore', 'agent_store_meta', 'tfi.agent-data.v18', 'writer_agent'),
    ('LineageDataStore', 'lineage_meta', 'tfi.lineage-data.v60', 'writer_lineage'),
])
def test_new_marker_and_existing_alias_startup_are_distinct(store, table, tag, schema):
    """新库写规范标记，旧前缀与错版本均拒绝且不改写值和行版本。"""
    initialize = STORE_INITIALIZERS[store]
    database = portal_database(schema=schema)
    database.write(initialize)
    def row():
        with database.read() as connection:
            return dict(connection.execute(
                f"SELECT value,xmin::text AS row_version FROM {table} WHERE key='schema'").fetchone())
    assert row()['value'] == tag
    for marker in (tag, 'archive.' + tag[4:], tag.rsplit('.v', 1)[0] + '.v999'):
        database.write(lambda connection: connection.execute(
            f"UPDATE {table} SET value=%s WHERE key='schema'", (marker,)))
        before = row()
        if marker != tag:
            with pytest.raises(RuntimeError, match='does not match its schema'):
                database.write(initialize)
        else:
            database.write(initialize)
            database.write(initialize)
        assert row() == before


@pytest.mark.parametrize('prefix', ['archive.', 'tfi.'])
@pytest.mark.parametrize('version', ['v18', 'v17'])
def test_runtime_status_reads_the_durable_marker_without_rewriting(prefix, version):
    """状态页仅认可规范前缀与当前版本，保留不兼容标记原文。"""
    store = AgentDataStore()
    marker = prefix + 'agent-data.' + version
    store.database.write(lambda connection: connection.execute(
        "UPDATE agent_store_meta SET value=%s WHERE key='schema'", (marker,)))
    def row():
        with store.database.read() as connection:
            return dict(connection.execute(
                "SELECT value,xmin::text AS row_version FROM agent_store_meta WHERE key='schema'").fetchone())
    before = row()
    try:
        result = runtime_status.portal_runtime_status()
        assert result['schema'] == 'tfi.portal-runtime-status.v1'
        assert result['agent_data'] == dict(
            compatible=prefix == 'tfi.' and version == 'v18', expected_schema='tfi.agent-data.v18',
            actual_schema=marker, expected_version='v18',
            actual_version=version if prefix == 'tfi.' else marker)
        assert row() == before
    finally:
        store.database.write(lambda connection: connection.execute(
            "UPDATE agent_store_meta SET value='tfi.agent-data.v18' WHERE key='schema'"))


def operation_rows(store, kind):
    with store.database.read() as connection:
        return [dict(row) for row in connection.execute(
            'SELECT operation_id,data_json,xmin::text AS row_version FROM agent_operations WHERE kind=%s ORDER BY operation_id',
            (kind,)).fetchall()]


def legacy_fixture(store, kind, schema):
    """Only mutate synthetic test rows to represent historical persisted facts."""
    store.database.write(lambda connection: connection.execute(
        "UPDATE agent_operations SET data_json=jsonb_set(data_json,'{schema}',to_jsonb(%s::text)) WHERE kind=%s",
        (schema,kind)))


def test_default_request_is_new_but_alias_retry_keeps_legacy_submission_receipt():
    """旧请求被拒绝且不创建回执，规范请求幂等重试保留原始身份。"""
    store = get_agent_data_store()
    queue = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    current = ExecutionRequest.new(actor='test', agent_id='resident_aaaaaaaaaaaa',
        service_type='agent_session', provider='codex', operation='turn', prompt='fixture',
        metadata={'fork_context_input': {'schema': 'tfi.provider-context-seed.v1', 'segments': []},
                  'opaque': {'schema': 'archive.user-data.v1'}})
    assert current.schema == 'tfi.execution-request.v8'
    old_seed = deepcopy(current.metadata)
    old_seed['fork_context_input']['schema'] = 'archive.provider-context-seed.v1'
    original = replace(current, schema='archive.execution-request.v8', metadata=old_seed)
    with pytest.raises(ValueError, match='unsupported execution request schema'):
        queue.submit(original)
    assert operation_rows(store, 'execution_submission') == []
    queue.submit(current)
    assert json_value(operation_rows(store, 'execution_submission')[0]['data_json'])['schema'] == 'tfi.execution-submission.v1'
    before = operation_rows(store, 'execution_submission')
    def prepare(_):
        pytest.fail('existing execution cannot be prepared again')
    assert queue.submit(current, prepare=prepare)['execution_id'] == original.execution_id
    assert operation_rows(store, 'execution_submission') == before
    assert queue.snapshot(current.execution_id)['request'] == current.to_payload()
    changed = deepcopy(current.metadata)
    changed['opaque']['schema'] = 'tfi.user-data.v1'
    with pytest.raises(ValueError, match='different request'):
        queue.submit(replace(current, metadata=changed), prepare=prepare)


def test_context_seed_default_changes_without_rewriting_explicit_legacy_seed():
    """缺省上下文采用规范格式，旧显式格式被拒绝且输入不被重写。"""
    seed = {'segments': [], 'prompt': 'frozen', 'opaque': {'schema': 'archive.user.v1'}}
    request = ExecutionRequest.new(actor='test', prompt='fixture', service_type='agent_session',
        provider='codex', operation='turn', metadata={'fork_context_input': seed})
    assert context_seed(request)['schema'] == 'tfi.provider-context-seed.v1'
    assert request.metadata['fork_context_input'] == seed
    old = {**seed, 'schema': 'archive.provider-context-seed.v1', 'portable_prompt': ''}
    request = replace(request, metadata={'fork_context_input': old})
    with pytest.raises(ValueError, match='Unsupported Provider context seed schema'):
        context_seed(request)
    assert request.metadata['fork_context_input'] == old


def test_acceptance_receipt_new_tag_and_historical_reentry_preserve_original_fact():
    """接收回执新建写TFI，同Session后续输入保留旧首次接收证据。"""
    store = AgentDataStore()
    ref = store.ensure_provider_session(provider='codex', backend_route_id='fixture',
        backend_session_id='thread-fixture', agent_id='resident_fixture', actor_ref='test')['provider_session_ref']
    store.bind_provider_session('fixture:binding', ref)
    def accept(execution, turn):
        store.begin_execution(execution_id=execution, agent_id='resident_fixture', actor_ref='test',
            provider='codex', started_at='2026-10-19T00:00:00Z')
        store.bind_execution_provider_session(execution, provider_session_ref=ref)
        store.record_input_acceptance(execution, agent_id='resident_fixture', provider_turn_id=turn)
    accept('execution-first', 'turn-first')
    assert json_value(operation_rows(store, 'input_accepted')[0]['data_json'])['schema'] == 'tfi.session-input-acceptance.v1'
    legacy_fixture(store, 'input_accepted', 'archive.session-input-acceptance.v1')
    before = operation_rows(store, 'input_accepted')
    accept('execution-later', 'turn-later')
    assert store.binding_has_accepted_input('fixture:binding', agent_id='resident_fixture')
    assert operation_rows(store, 'input_accepted') == before


def test_transformation_reentry_keeps_historical_positions_and_receipt():
    """转换后的重入不重写旧位置证据及其持久回执标签。"""
    store = AgentDataStore()
    store.put_segment(segment_id='segment_source', segment_type='input', content='fixture',
        content_type='text/plain', created_by_actor_ref='test')
    store.append_agent_segments('resident_fixture', ['segment_source'], actor_ref='test')
    def transform():
        return store.transform_agent_segments('resident_fixture', ['segment_source'],
            segment_id='segment_result', content='reduced fixture', actor_ref='test',
            operation_id='operation-fixture', operation_type='agent_harness.transform')
    assert transform() == 'segment_result'
    assert json_value(operation_rows(store, 'trace_transformation')[0]['data_json'])['schema'] == 'tfi.trace-transformation.v1'
    legacy_fixture(store, 'trace_transformation', 'archive.trace-transformation.v1')
    before = operation_rows(store, 'trace_transformation')
    positions = store.sequence_segment_ids(store.trace_sequence_id('resident_fixture'))
    assert transform() == 'segment_result'
    assert operation_rows(store, 'trace_transformation') == before
    assert store.sequence_segment_ids(store.trace_sequence_id('resident_fixture')) == positions


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
