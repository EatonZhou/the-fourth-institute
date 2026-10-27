""":importance: P2"""
from management_portal.society_service import resident_lineage
from management_portal.society_service.domain_ports import resident_lineage_host_port
import copy
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from management_common.projection_receipts import CommittedProjectionError
from management_portal.society_service import (
    lineage_domain,
    lineage_program_runtime,
    lineage_program_effect_execution,
    domain_ports,
    invariant_locks,
    resident_access,
)
from tests.cases.test_resident_lifecycle_operations import operations
from tests.cases import test_lineage_generic_primitives as primitive_cases
from tests.cases.isa_test_support import isolated_instruction_store, run  # noqa: F401 - fixture
from tests.cases.test_lineage_backend import lineage_store
from tests.cases.test_lineage_generic_primitives import primitive_store
from tests.cases.test_lineage_program_runtime_claims import lineage_id


from management_portal.society_service import resident_directory_domain, resident_lifecycle_domain, resident_project_domain, resident_record_domain, resident_workspace_domain
from tests.cases.effect_script_support import effect_config, lower_effect_call

@pytest.fixture(autouse=True)
def forbid_external_effects(tmp_path, monkeypatch, isolate_portal_databases):
    monkeypatch.setenv('PORTAL_SOCIETY_SERVICE_PROCESS', '0')
    monkeypatch.setattr(invariant_locks, '_ROOT_PROVIDER', lambda: tmp_path / 'locks')

    def forbidden(*args, **kwargs):
        raise AssertionError('helper10 forbids network/provider subprocess side effects')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    from management_portal.execution.service import ExecutionService
    # The fixture's quota read is local too; transport side effects stay banned.
    monkeypatch.setattr(ExecutionService, 'binding_quota_states',
                        lambda _self, bindings: {key: {'state': 'open', 'reason': ''} for key in bindings})


def test_empty_lineage_instruction_persists_catalog_and_revision(isolated_instruction_store):
    """CreateLineage 创建的空 Lineage 以 revision 1 落盘并出现在 Lineage 列表中"""
    receipt = run('CreateLineage', {'title': 'helper10 empty aggregate', 'project_id': 'project_test'}, key='helper10-empty')
    assert receipt.status == 'committed'
    lineage_id = receipt.result['lineage_id']
    persisted = lineage_domain._read_lineage_state(lineage_id)
    assert persisted['nodes'] == []
    assert persisted['edges'] == []
    assert persisted['revision'] == 1
    assert lineage_domain._lineage_dir(lineage_id).is_relative_to(isolated_instruction_store)
    assert lineage_domain._lineage_dir(lineage_id).is_dir()
    listed = lineage_domain.list_lineages('sample_admin')['lineages']
    assert [item['lineage_id'] for item in listed] == [lineage_id]
    assert listed[0]['node_count'] == 0


def test_delete_effect_removes_its_wires_only(isolated_instruction_store):
    """DeleteComponent 删除 effect 时只移除连到它的连线，保留其他连线"""
    quiet = effect_config('ListComponentTypes', {}, [])
    created = lineage_domain.create_lineage({
        'title': 'helper10 delete contained effect',
        'program': {
            'components': [
                {'id': 'event_source_helper10_start', 'kind': 'event_source', 'config': {'event_type': 'manual.triggered'}},
                {'id': 'effect_helper10_target', 'kind': 'effect', 'config': copy.deepcopy(quiet)},
                {'id': 'effect_helper10_neighbor', 'kind': 'effect', 'config': copy.deepcopy(quiet)},
            ],
            'wires': [
                    {'id': 'wire_helper10_remove', 'kind': 'route', 'source': 'event_source_helper10_start', 'target': 'effect_helper10_target'},
                    {'id': 'wire_helper10_keep', 'kind': 'route', 'source': 'event_source_helper10_start', 'target': 'effect_helper10_neighbor'},
            ],
        },
    }, 'sample_admin')
    lineage_id = created['lineage_id']
    receipt = run('DeleteComponent', {'lineage_id': lineage_id, 'component_id': 'effect_helper10_target'},
                  key='helper10-delete', version=created['revision'])
    assert receipt.status == 'committed', receipt.to_payload()
    current = lineage_domain._read_lineage_state(lineage_id)
    assert current['revision'] == created['revision'] + 1
    assert {item['id'] for item in current['program']['components']} == {'event_source_helper10_start', 'effect_helper10_neighbor'}
    assert [item['id'] for item in current['program']['wires']] == ['wire_helper10_keep']


def test_delivery_summary_survives_duplicate_exhausted_and_read_then_resets(lineage_id):
    """事件源投递摘要只记计数，经重复、耗尽触发和读取后保留，重置触发预算后清空"""
    source = 'event_source_helper10_summary'
    lineage_program_runtime.claim_event_occurrence(lineage_id, source, 'occurrence-a', max_fires=1)
    summary = lineage_program_runtime.record_event_delivery_summary(lineage_id, source, [
        {'status': 'sent', 'prompt': 'must-not-copy', 'payload': {'private': 'must-not-copy'}},
        {'status': 'queued'}, {'status': 'dropped'}, {'status': 'failed'},
    ])
    assert [summary[key] for key in ['delivered_count', 'queued_count', 'dropped_count', 'failed_count']] == [1, 1, 1, 1]
    assert 'must-not-copy' not in str(summary)
    duplicate = lineage_program_runtime.claim_event_occurrence(lineage_id, source, 'occurrence-a', max_fires=1)
    exhausted = lineage_program_runtime.claim_event_occurrence(lineage_id, source, 'occurrence-b', max_fires=1)
    assert duplicate['status'] in {'duplicate', 'exhausted'}
    assert exhausted['status'] == 'exhausted'
    view = lineage_program_runtime.event_source_runtime_view(lineage_id, {source: {'max_fires': 1}})[source]
    assert view['last_delivery'] == summary
    assert lineage_program_runtime.event_source_runtime_view(lineage_id, {source: {'max_fires': 1}})[source]['last_delivery'] == summary
    lineage_program_runtime.reset_event_source_fire_budget(lineage_id, source, expected_generation=view['generation'])
    assert lineage_program_runtime.event_source_runtime_view(lineage_id, {source: {'max_fires': 1}})[source]['last_delivery'] == {}


def test_terminal_transport_failure_deduplicates_without_reexecution(primitive_store, monkeypatch, record_property):
    """目标传输不可用的终态失败在事件重放时按重复去重，不再执行 effect"""
    dispatch = lineage_domain.dispatch_lineage_program_event
    script_inputs = lineage_program_effect_execution.effect_script_inputs
    dispatches = []
    identities = []

    def observe_dispatch(*args, **kwargs):
        result = dispatch(*args, **kwargs)
        if args[1] == 'event_source_failure_start':
            dispatches.append((args, kwargs, result))
        return result

    def observe_inputs(definition, invocation):
        if definition.get('effect_id') == 'effect_failure_subject':
            identities.append((invocation.id, invocation.activation.id))
        return script_inputs(definition, invocation)

    monkeypatch.setattr(lineage_domain, 'dispatch_lineage_program_event', observe_dispatch)
    monkeypatch.setattr(lineage_program_effect_execution, 'effect_script_inputs', observe_inputs)
    primitive_cases.test_effect_failure_routes_through_an_explicit_event_source(primitive_store, monkeypatch)
    assert dispatches
    assert len(set(identities)) == 1
    request_count = len(identities)
    sent = []
    monkeypatch.setattr(primitive_cases.lineage_test_support, 'NODE_MESSAGE_SENDER', lambda node, prompt, actor: sent.append(node['id']) or {'message_id': 'job_helper10_retry'})
    arguments, keywords, first_result = dispatches[-1]
    retried = dispatch(*arguments, **keywords)
    record_property('retry_result', json.dumps(retried, ensure_ascii=False))
    record_property('request_identities', json.dumps(identities))
    assert sent == []
    assert len(identities) == request_count
    assert len(set(identities)) == 1
    assert first_result['matched_count'] == 1
    assert first_result['results'][0]['status'] == 'failed'
    assert 'target transport unavailable' in first_result['results'][0]['error']
    assert first_result['results'][0]['failure_event']['payload']['retryable'] is False
    assert retried['matched_count'] == 0
    assert retried['results'] == []
    assert retried['duplicate'] is True


@pytest.mark.parametrize('name,method', [('resource', 'read_content'), ('execution', 'get_execution'), ('workspace', 'authorize_workspace_path'), ('project', 'resolve_project_selection')])
def test_adjacent_owner_port_delegates_and_propagates_failure(name, method):
    """相邻所有者端口把调用原样委托给绑定实现并抛出其异常"""
    calls = []

    def implementation(*args, **kwargs):
        calls.append((args, kwargs))
        if kwargs.get('fail'):
            raise LookupError(name + ' unavailable')
        return {'owner': name, 'stable_id': args[0]}

    port = domain_ports._BoundPort('helper10 ' + name)
    port.bind(SimpleNamespace(**{method: implementation}))
    assert getattr(port, method)('fixture_id') == {'owner': name, 'stable_id': 'fixture_id'}
    with pytest.raises(LookupError, match='unavailable'):
        getattr(port, method)('fixture_id', fail=True)
    assert calls == [(('fixture_id',), {}), (('fixture_id',), {'fail': True})]


def test_projection_failure_keeps_committed_definition_and_can_rebuild(lineage_store, monkeypatch):
    """资源投影失败时 Lineage 更新已提交并标明只需重建投影，之后可重建"""
    created = lineage_domain.create_lineage({'title': 'helper10 projection', 'nodes': [], 'edges': []}, 'sample_admin')
    original = lineage_domain._sync_lineage_resource_identity

    def failing_projection(*args, **kwargs):
        raise OSError('helper10 resource projection unavailable; retry projection')

    monkeypatch.setattr(lineage_domain, '_sync_lineage_resource_identity', failing_projection)
    candidate = {**created, 'description': 'committed before projection error'}
    with pytest.raises(CommittedProjectionError) as failure:
        lineage_domain.update_lineage(created['lineage_id'], {'expected_revision': created['revision'], 'lineage': candidate}, 'sample_admin')
    assert failure.value.details['domain_committed'] is True
    assert failure.value.details['recovery'] == 'projection_only'
    assert failure.value.details['committed_revision'] == created['revision'] + 1
    assert isinstance(failure.value.__cause__, OSError)
    assert 'retry projection' in str(failure.value.__cause__)
    committed = lineage_domain._read_lineage_state(created['lineage_id'])
    assert committed['revision'] == created['revision'] + 1
    assert committed['description'] == candidate['description']
    monkeypatch.setattr(lineage_domain, '_sync_lineage_resource_identity', original)
    original(committed)
    assert lineage_domain.get_lineage(created['lineage_id'], 'sample_admin')['description'] == candidate['description']


def test_lifecycle_tombstone_precedes_release_and_recovery_cleanup(operations, tmp_path, monkeypatch):
    """删除整个 Lineage 先写墓碑再释放 Resident，释放失败后重试沿用同一 effect 标识"""
    record = {'resident_id': 'resident_111111111111', 'lineage_id': 'lineage_111111111111', 'folder_schema': 'lineage-folder-v3', 'workspace_path': str(tmp_path / 'folder'),
              'owned_workspace_id': 'workspace_111111111111',
              'owned_workspace': {'workspace_id': 'workspace_111111111111',
                                  'path': str(tmp_path / 'folder' / 'residents' / 'resident_111111111111' / 'workspace')},
              'workspace_bindings': []}
    folder = tmp_path / 'folder'
    folder.mkdir()
    monkeypatch.setattr(resident_record_domain, 'find_resident', lambda resident_id: record)
    monkeypatch.setattr(resident_access, 'can_manage', lambda *args: True)
    monkeypatch.setattr(resident_lineage, 'is_lineage_leader', lambda item: True)
    monkeypatch.setattr(resident_directory_domain, 'lineage_registry_records_locked', lambda lineage_id: [record])
    monkeypatch.setattr(resident_lifecycle_domain, 'resident_has_pending_work', lambda resident_id: False)
    monkeypatch.setattr(resident_workspace_domain, '_safe_lineage_workspace_for_delete', lambda item: folder)
    monkeypatch.setattr(resident_record_domain, 'resident_workspace_path', lambda item: folder)
    monkeypatch.setattr(resident_project_domain, 'project_info_for_workspace', lambda *args: {})
    monkeypatch.setattr(resident_lifecycle_domain, 'resident_release_step', lambda member, actor: {'kind': 'release', 'resident_id': member['resident_id']})
    from management_portal.society_service.lineage_resident_operations import LineageDeletionToken

    monkeypatch.setattr(resident_lineage_host_port, 'prepare_lineage_deletion',
                        lambda lineage_id, actor, deleted_at, folder_path: LineageDeletionToken(
                            lineage_id, actor, deleted_at, folder_path, {'lineage_id': lineage_id}))
    # Deleting a whole Lineage tombstones it before releasing its Residents (a single
    # DeleteResident keeps the Lineage and re-picks its Leader).
    prepared = resident_workspace_domain._prepare_resident_deletion(record['resident_id'], 'sample_admin', whole_lineage=True)
    steps = prepared['steps']
    assert [step['kind'] for step in steps] == ['lineage_tombstone', 'release', 'forget_deleted', 'lineage_folder_unbind', 'remove']
    operation_id = operations.enqueue(record['resident_id'], steps)
    calls = []

    def first(effect_id, step):
        calls.append((effect_id, step['kind']))
        if step['kind'] == 'release':
            raise OSError('controlled release adapter failure')

    assert not operations.run(operation_id, first)
    assert [kind for _, kind in calls] == ['lineage_tombstone', 'release']
    operations.store.database.write(lambda connection: connection.execute('UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (operation_id,)))
    assert operations.run(operation_id, lambda effect_id, step: calls.append((effect_id, step['kind']))) == record['resident_id']
    assert [kind for _, kind in calls] == ['lineage_tombstone', 'release', 'release', 'forget_deleted', 'lineage_folder_unbind', 'remove']
    assert calls[1][0] == calls[2][0]


def test_markdown_projection_failure_and_rebuild_leave_definition_intact(lineage_store, monkeypatch):
    """README.md 投影写入失败与重建都不改动已持久化的 Lineage 定义"""
    created = lineage_domain.create_lineage({'title': 'helper10 Markdown projection', 'nodes': [], 'edges': []}, 'sample_admin')
    persisted = copy.deepcopy(lineage_domain._read_lineage_state(created['lineage_id']))
    readme = lineage_domain._lineage_dir(created['lineage_id']) / 'README.md'
    assert readme.is_relative_to(lineage_store)
    readme.unlink()
    original = lineage_domain._write_text

    def unavailable(path, content):
        assert path == readme
        raise OSError('temporary Markdown projection storage failure')

    monkeypatch.setattr(lineage_domain, '_write_text', unavailable)
    with pytest.raises(OSError, match='Markdown projection'):
        lineage_domain._ensure_lineage_folder_files(persisted)
    assert lineage_domain._read_lineage_state(created['lineage_id']) == persisted
    monkeypatch.setattr(lineage_domain, '_write_text', original)
    lineage_domain._ensure_lineage_folder_files(persisted)
    assert readme.is_file()
    assert lineage_domain._read_lineage_state(created['lineage_id']) == persisted


def test_typed_workspace_and_project_ports_retain_ownership_and_errors(tmp_path):
    """Workspace 与 Project 端口每次调用取所有者的最新结果并抛出其拒绝错误"""
    calls = []
    workspace = domain_ports._ResidentDomainPort()
    project = domain_ports._ProjectDomainPort()
    snapshots = {'revision': 1}

    def authorize(path, actor, *, write=False):
        calls.append(('workspace', path, actor, write))
        if write:
            raise PermissionError('temporary owner rejected write')
        return {'workspace_id': 'workspace_111111111111', 'revision': snapshots['revision']}

    def validate(payload):
        calls.append(('project', copy.deepcopy(payload)))
        if payload['project_id'] == 'missing':
            raise LookupError('temporary Project owner rejected selection')

    workspace.bind(SimpleNamespace(authorize_workspace_path=authorize))
    project.bind(SimpleNamespace(validate_existing_project_selection=validate))
    assert workspace.authorize_workspace_path(tmp_path, 'fixture-user')['revision'] == 1
    snapshots['revision'] = 2
    assert workspace.authorize_workspace_path(tmp_path, 'fixture-user')['revision'] == 2
    with pytest.raises(PermissionError):
        workspace.authorize_workspace_path(tmp_path, 'fixture-user', write=True)
    project.validate_existing_project_selection({'project_id': 'fixture-project'})
    with pytest.raises(LookupError):
        project.validate_existing_project_selection({'project_id': 'missing'})
    assert len(calls) == 5
