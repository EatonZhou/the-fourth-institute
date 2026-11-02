""":importance: P2"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest import mock

import pytest

from knowledge_board_lan import knowledge_backend, knowledge_details, knowledge_source
from management_portal import resource_index_backend as backend
from management_portal import resource_index_knowledge
from management_portal.resources import ResourceService
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.providers import experiment, knowledge
from management_portal.resources.registration import ResourceRegistrationStore
from management_portal.resources.server import ResourceServiceServer
from management_portal.resources.transport import ResourceServiceClient


@pytest.fixture
def knowledge_service(tmp_path, monkeypatch):
    project_root = tmp_path / 'Fixture'
    (project_root / 'knowledge_base').mkdir(parents=True)
    monkeypatch.setattr(knowledge_backend, '_parse_projects_yaml', lambda: [
        {'id': 'Fixture', 'path': str(project_root), 'status': 'active'},
    ])
    monkeypatch.setattr(knowledge_source, '_parse_projects_yaml', lambda: [
        {'id': 'Fixture', 'path': str(project_root), 'status': 'active'},
    ])
    monkeypatch.setattr(knowledge_backend, '_get_tag_config', lambda: (
        ['information'], {'information': 'information'},
        {'information': 'information'}, 'visibility-fixture',
    ))
    monkeypatch.setattr(knowledge_source, '_get_tag_config', lambda: (
        ['information'], {'information': 'information'},
        {'information': 'information'}, 'visibility-fixture',
    ))
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '0')
    monkeypatch.setenv('PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE', str(tmp_path / 'unused.key'))
    store = backend.IndexStore()
    adapter = resource_index_knowledge.ProjectKnowledgeAdapter()
    store.publish_snapshot(adapter.name, [], {'fixture_generation': 0})
    index = backend.ResourceIndexService(store=store, adapters=[adapter], start_worker=False)
    index.adapter_layer._on_projection_changed = None
    service = ResourceService(
        index_provider=index,
        collaboration_store=CollaborationDescriptorStore(),
        registration_store=ResourceRegistrationStore(),
        resolver_loader=knowledge.install_resolver,
        mutation_provider_loader=knowledge.install_mutation_provider,
    )
    monkeypatch.setattr(knowledge_backend, 'get_resource_service', lambda: service)
    monkeypatch.setattr(knowledge_source, 'get_resource_service', lambda: service)
    monkeypatch.setattr(knowledge_details, 'get_resource_service', lambda: service)
    return service, index


def _register(service, **options):
    options.setdefault('actor', 'test-actor')
    return service.register('information', {
        'project_id': 'Fixture', 'title': 'Registration visibility fixture',
        'body': 'Isolated UTF-8 fixture: 中文。',
    }, **options)

def test_committed_mutation_reports_failed_projection(subtests, isolated_portal_case):
    """更新与注销在单对象投影失败时明确报告领域已提交。"""
    from management_portal.resources.errors import ResourceOperationError
    for operation in ['update', 'unregister']:
        with subtests.test(operation=operation), isolated_portal_case() as case_path:
                resource_id = 'information_0123456789ab'
                index = mock.Mock()
                projector = index.project_updated if operation == 'update' else index.project_unregistered
                projector.side_effect = RuntimeError('projection unavailable')
                service = ResourceService(index_provider=index)
                provider = mock.Mock(return_value={'resource_id': resource_id, 'project_ids': ['Fixture'], 'committed': True})
                service.register_mutation_provider('information', **{operation: provider})
                with pytest.raises(ResourceOperationError) as failure:
                    if operation == 'update':
                        service.update(resource_id, {}, actor='test-actor', wait_for_index=True, index_timeout=0)
                    else:
                        service.unregister(resource_id, actor='test-actor', wait_for_index=True, index_timeout=0)
                assert failure.value.details['domain_committed'] is True
                assert failure.value.details['stage'] == 'index_projection'
                provider.assert_called_once()
                index.wait_for_refresh.assert_not_called()


def test_update_and_delete_are_durable_while_scan_queue_is_pending(knowledge_service):
    """积压的扫描不延迟单对象提交，旧扫描不能覆盖提交，重新打开索引可见结果。"""
    service, index = knowledge_service
    record = _register(service)
    resource_id = record['resource_id']
    old = index.store.internal_get(resource_id)
    scan_generation = index.store.generation()
    from management_portal.resource_index_records import ChangeSet
    index.adapter_layer.request_refresh(['project_knowledge'], force=True)
    with mock.patch.object(index, 'wait_for_refresh', side_effect=AssertionError('queue wait')), \
         mock.patch.object(index.store, 'records_for_adapter', side_effect=AssertionError('adapter copy')):
        service.update(resource_id, {'body': '# Updated immediately\nCurrent body.'}, actor='test-actor',
                       wait_for_index=True, index_timeout=0)
        current = index.store.internal_get(resource_id)
        assert current['title'] == 'Updated immediately'
        assert any(item['resource_id'] == resource_id for item in service.query(
            actor='test-actor', resource_types=['information'], q='Updated immediately')['resources'])
        assert backend.IndexStore().internal_get(resource_id)['title'] == current['title']
        index.store.publish_snapshot('project_knowledge', [old], {}, scan_started_generation=scan_generation)
        index.store.apply(ChangeSet('project_knowledge', upserts=(old,)), scan_started_generation=scan_generation)
        assert index.store.internal_get(resource_id)['title'] == current['title']
        service.unregister(resource_id, actor='test-actor', wait_for_index=True, index_timeout=0)
        index.store.publish_snapshot('project_knowledge', [old], {}, scan_started_generation=scan_generation)
        index.store.apply(ChangeSet('project_knowledge', upserts=(old,)), scan_started_generation=scan_generation)
        with pytest.raises(KeyError):
            backend.IndexStore().internal_get(resource_id)


def test_same_resource_mutations_order_domain_and_projection(knowledge_service, monkeypatch):
    """A 卡在投影时 B 不能先提交同资源；其他资源可继续，最终持久结果是 B。"""
    from concurrent.futures import ThreadPoolExecutor
    service, index = knowledge_service
    first, other = _register(service), _register(service)
    entered, release, started_b = threading.Event(), threading.Event(), threading.Event()
    original = index.project_updated

    def held(kind, record):
        if record['resource_id'] == first['resource_id'] and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(kind, record)

    def write(record, title):
        if title == 'B':
            started_b.set()
        return service.update(record['resource_id'], {'body': '# ' + title}, actor='test-actor')

    monkeypatch.setattr(index, 'project_updated', held)
    with ThreadPoolExecutor(max_workers=3) as pool:
        a = pool.submit(write, first, 'A')
        try:
            assert entered.wait(3)
            b = pool.submit(write, first, 'B')
            assert started_b.wait(3)
            pool.submit(write, other, 'Independent').result(3)
            assert not b.done()
            assert '# A' in Path(first['path']).read_text()
        finally:
            release.set()
        a.result(3)
        b.result(3)
    assert backend.IndexStore().internal_get(first['resource_id'])['title'] == 'B'


def test_registration_projects_authoritative_path_without_queueing(knowledge_service):
    """知识条目登记直接按权威路径写入索引，不进入变更队列"""
    service, index = knowledge_service
    with mock.patch.object(index, 'queue_change', wraps=index.queue_change) as queue_change:
        record = _register(service)
    queue_change.assert_not_called()
    assert index.store.internal_get(record['resource_id'])['source']['path'] == record['path']


def test_registration_projects_one_knowledge_record_without_adapter_snapshot(knowledge_service):
    """知识条目登记只投影这一条记录，不复制整个适配器快照"""
    service, index = knowledge_service
    with mock.patch.object(index.store, 'records_for_adapter', side_effect=AssertionError('full adapter copy')):
        record = _register(service)
    assert index.store.internal_get(record['resource_id'])['resource_id'] == record['resource_id']


@pytest.mark.parametrize('wait_for_index', [False, True])
def test_registration_is_immediately_readable_without_worker(knowledge_service, wait_for_index):
    """无论是否等待索引，知识条目登记后无需后台 worker 即可解析、查询和读取正文"""
    service, index = knowledge_service
    record = _register(service, wait_for_index=wait_for_index, index_timeout=0)
    assert record['created'] is True
    assert 'index_projection' not in record
    assert service.resolve(record['resource_id'], actor='test-actor')['root_id'] == record['resource_id']
    assert any(item['resource_id'] == record['resource_id'] for item in
               service.query(actor='test-actor', resource_types=['information'], q='Registration visibility')['resources'])
    content, _, resolved = service.read_content(record['resource_id'], actor='test-actor')
    assert resolved['root_id'] == record['resource_id']
    assert '中文' in content.decode()
    assert index.store._change_generation > 1


def test_committed_registration_persists_for_reopened_index_store(knowledge_service):
    """登记提交后重新打开的索引存储能读到新资源"""
    service, index = knowledge_service
    independent_store = backend.IndexStore()
    record = _register(service)
    assert index.store.get(record['resource_id'], actor='test-actor')['resource_id'] == record['resource_id']
    with pytest.raises(KeyError):
        independent_store.get(record['resource_id'], actor='test-actor')
    reopened_store = backend.IndexStore()
    assert reopened_store.get(record['resource_id'], actor='test-actor')['resource_id'] == record['resource_id']


def test_experiment_registration_is_immediately_queryable(knowledge_service, monkeypatch, tmp_path):
    """实验登记后立即可在索引中查询到

    :importance: P3"""
    service, index = knowledge_service
    project_root = tmp_path / 'Fixture'
    monkeypatch.setattr(experiment, '_project_roots', lambda: [('Fixture', project_root)])
    monkeypatch.setattr(knowledge_backend, '_get_tag_config', lambda: (
        ['information', 'experiment'],
        {'information': 'information', 'experiment': 'experiment'},
        {'information': 'information', 'experiment': 'experiment'},
        'visibility-fixture',
    ))
    monkeypatch.setattr(knowledge_source, '_get_tag_config', lambda: (
        ['information', 'experiment'],
        {'information': 'information', 'experiment': 'experiment'},
        {'information': 'information', 'experiment': 'experiment'},
        'visibility-fixture',
    ))
    experiment.install_mutation_provider(service)
    result = service.register('experiment', {
        'project_id': 'Fixture', 'title': 'Immediate experiment',
    }, actor='test-actor', wait_for_index=True, index_timeout=0)
    assert 'index_projection' not in result
    assert index.store.internal_get(result['resource_id'])['resource_id'] == result['resource_id']
    assert any(item['resource_id'] == result['resource_id'] for item in
               service.query(actor='test-actor', resource_types=['experiment'], q='Immediate experiment')['resources'])


def test_two_rpc_clients_observe_same_immediate_registration(knowledge_service, tmp_path):
    """一个 RPC 客户端登记后，另一个客户端立即能解析并读取同一资源"""
    service, index = knowledge_service
    publisher = mock.Mock()
    publisher.health.return_value = {'connected': False}
    server = ResourceServiceServer(tmp_path / 'resource.sock', service=service, publisher=publisher)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    assert server.started.wait(2)
    writer = ResourceServiceClient(server.socket_path)
    reader = ResourceServiceClient(server.socket_path)
    try:
        result = _register(writer, wait_for_index=True, index_timeout=0)
        assert result['created'] is True
        assert 'index_projection' not in result
        assert reader.resolve(result['resource_id'], actor='test-actor')['root_id'] == result['resource_id']
        content, _, resolved = reader.read_content(result['resource_id'], actor='test-actor')
        assert resolved['root_id'] == result['resource_id']
        assert Path(result['path']).read_bytes() == content
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
