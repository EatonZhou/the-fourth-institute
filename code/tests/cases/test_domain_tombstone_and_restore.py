"""Deletion replay and terminal retained identity (decision_b820faf42218).

:module: spec.design-governance
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tests.cases.historical_identity_test_support import _record
from tests.cases.historical_identity_test_support import deleted_identity

from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.registry_store import get_registry_store
from management_portal.resources.service import ResourceService
from management_portal.society_service import resource_client, resident_catalog
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401

R = 'resident_111111111111'
L = 'lineage_222222222222'
W = 'workspace_333333333333'
T0 = '2026-10-07T00:00:00Z'
T1 = '2026-10-07T00:01:00Z'
T2 = '2026-10-07T00:02:00Z'


def descriptor(rid, *, deleted=False, time=T1):
    return {'schema': 'tfi.collaboration-descriptor.v2', 'resource_id': rid,
            'title': 'old snapshot', 'attributes': {'retained': True},
            'status': 'deleted' if deleted else 'active', 'updated_at': time,
            'deleted_at': time if deleted else '',
            'relations': [{'kind': 'resident', 'target_id': R}] if rid == W else []}


@pytest.fixture
def projection(monkeypatch):
    from management_portal import permission_system
    descriptors, registry = CollaborationDescriptorStore(), get_registry_store()
    # What the permission system is told about each saved descriptor.
    permissions = Mock(return_value=False)
    monkeypatch.setattr(permission_system, 'sync_resource', permissions)
    service = ResourceService(collaboration_store=descriptors)
    monkeypatch.setattr(service, '_notify_mutation', lambda **kwargs: None)
    return SimpleNamespace(service=service, descriptors=descriptors, registry=registry, permissions=permissions)


def told(projection):
    """``(resource_id, status)`` of each record the permission system was told about."""
    return [(call.args[0]['resource_id'], call.args[0]['status']) for call in projection.permissions.call_args_list]


def live_lineage_projection(lineage_id):
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.society_service.lineage_catalog import LineageCatalog
    from tests.cases.test_lineage_catalogs import _lineage_definition
    catalog = LineageCatalog(get_lineage_data_store())
    catalog.upsert(_lineage_definition(lineage_id=lineage_id, updated_at=T1))
    return resource_client._lineage_resource_projection(catalog.get(lineage_id))


@pytest.mark.parametrize('rid', [R, L])
def test_old_tombstone_is_idempotent_and_preserves_descriptor(projection, rid):
    """旧删除墓碑幂等重放并保留描述，权限系统只收到删除

    :design: decision_b820faf42218
    :design-point:
    """
    current = {**descriptor(rid, time=T0), 'title': 'concurrent title',
               'attributes': {'extension': {'keep': True}}, 'summary': 'new summary'}
    projection.descriptors.put(current)
    payload = {'descriptor': descriptor(rid, deleted=True)}
    first = projection.service.sync_collaboration_resources([payload])
    again = projection.service.sync_collaboration_resources([payload])
    assert first == again and 'acl' not in first['resources'][0]
    assert told(projection) == [(rid, 'deleted'), (rid, 'deleted')]
    saved = projection.descriptors.get(rid)
    assert saved['status'] == 'deleted' and saved['deleted_at'] == T1
    for field in ('title', 'summary', 'attributes', 'relations'):
        assert saved[field] == current[field]
    assert payload['descriptor']['title'] == 'old snapshot'


@pytest.mark.parametrize('time', [T1, T2])
def test_stale_delete_rejects_whole_mixed_bundle_before_any_write(projection, time):
    """陈旧删除在写入前拒绝整个混合投影

    :design: decision_b820faf42218
    :design-point:
    """
    projection.descriptors.put({**descriptor(R, time=time), 'title': 'later live edit'})
    before = projection.descriptors.get(R)
    live = live_lineage_projection(L)
    deleted = {'descriptor': descriptor(R, deleted=True)}
    with pytest.raises(ValueError, match='Stale domain tombstone'):
        projection.service.sync_collaboration_resources([live, deleted])
    assert projection.descriptors.get(R) == before
    assert projection.registry.get('lineage', L) is None
    projection.permissions.assert_not_called()


def test_mixed_bundle_keeps_live_descriptor_and_shared_workspace(projection, tmp_path):
    """混合删除投影保留活跃描述及共享 Workspace"""
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.society_service.lineage_catalog import LineageCatalog
    from tests.cases.test_lineage_catalogs import _lineage_definition
    LineageCatalog(get_lineage_data_store()).upsert(
        _lineage_definition(lineage_id=L, title='current canonical', updated_at=T1))
    live = resource_client._lineage_resource_projection(get_lineage_data_store().lineage_definition(L))
    from management_portal.resources.workspace_store import get_workspace_store
    get_workspace_store().ensure(W, '/share/shared')
    shared = projection.descriptors.get(W)
    projection.descriptors.put(descriptor(R, time=T0))
    result = projection.service.sync_collaboration_resources([
        {'descriptor': descriptor(R, deleted=True)},
        live,
    ])
    assert projection.descriptors.get(W)['status'] == 'active'
    assert projection.descriptors.get(W)['relations'] == shared['relations']
    assert told(projection) == [(R, 'deleted'), (L, 'active')]
    assert ['acl' not in r for r in result['resources']] == [True, True]


def test_deleted_bundle_does_not_claim_workspace_ownership_from_path():
    """Resident 删除投影不根据路径宣称 Workspace 所有权

    :design: decision_b820faf42218
    :design-point:
    """
    record = {'resident_id': R, 'lineage_id': L, 'deleted_at': T1, 'updated_at': T1,
              'workspace_bindings': [{'workspace_id': W, 'path': f'/other/residents/{R}/workspace', 'purpose': ''}]}
    assert [item['descriptor']['resource_id'] for item in resource_client._resident_resource_bundle_projections(record)] == [R]


def test_projection_rpc_requires_signed_service_identity_even_when_auth_optional(tmp_path, monkeypatch):
    """投影远程调用即使认证可选也要求签名服务身份

    :design: decision_b820faf42218
    :design-point:
    """
    from management_portal.resources.server import ResourceServiceServer
    from management_portal.resources.rpc_auth import load_rpc_signing_key, sign_rpc_auth
    from management_portal.resources.config import resource_rpc_signing_key_file
    from tests.cases.test_resource_service_process import FakeResourceService
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '0')
    socket = tmp_path / 'projection.sock'
    key = load_rpc_signing_key(required=True, create=True, path=resource_rpc_signing_key_file(socket_path=socket))
    server = ResourceServiceServer(socket, service=FakeResourceService(), publisher=object())
    method = 'resource.collaboration.sync-many'
    params = {'resources': [{'descriptor': {'resource_id': R}}]}
    try:
        for extra in ({}, {'trusted': True}, {'actor': 'society-service'}, {'owner_ids': ['CASE-A']}):
            with pytest.raises(PermissionError):
                server.authenticate(method, {**params, **extra}, None)
        for actor in ('CASE-A', R, 'resource-service'):
            auth = sign_rpc_auth(key, method, params, actor=actor)
            authenticated = server.authenticate(method, dict(params), auth)
            with pytest.raises(PermissionError, match='authenticated SocietyService'):
                server.dispatch(method, params, actor=authenticated)
        auth = sign_rpc_auth(key, method, params, actor='society-service')
        actor = server.authenticate(method, dict(params), auth)
        assert server.dispatch(method, params, actor=actor)['resources'] == [{'resource_id': R}]
        extra = {**params, 'trusted': True}
        with pytest.raises(ValueError, match='Unexpected'):
            identity = server.authenticate(method, extra, sign_rpc_auth(key, method, extra, actor='society-service'))
            server.dispatch(method, extra, actor=identity)
        with pytest.raises(PermissionError, match='replayed'):
            server.authenticate(method, dict(params), auth)
        forged = {**params, 'actor': 'society-service'}
        with pytest.raises(PermissionError, match='assertion'):
            server.authenticate(method, forged, sign_rpc_auth(key, method, forged, actor='CASE-A'))
    finally:
        server.server_close()


def test_old_outbox_retry_keeps_revision_lease_and_receipt(projection, monkeypatch):
    """删除投影重试保留修订、更新租约，权限系统只收到删除

    :design: decision_b820faf42218
    :design-point:
    """
    from management_portal.society_service.state_store import LineageDataStore
    from management_portal.society_service.resident_resource_projection import ResidentResourceProjectionWorker
    store = LineageDataStore()
    payload = {'resident_id': R, 'lineage_id': L, 'title': 'old committed delete',
               'owner_ids': ['CASE-A'], 'updated_at': T1, 'deleted_at': T1,
               'owned_workspace_id': W, 'workspace_bindings': []}
    projection.descriptors.put(descriptor(R, time=T0))
    from management_portal.resources.workspace_store import get_workspace_store
    workspaces = get_workspace_store()
    workspaces.ensure(W, '/owned/workspace')
    workspaces.database.write(lambda conn: workspaces.mark_deleted(W, deleted_at=T1, deleted_by='test', connection=conn))
    deleted_workspace = workspaces.rows([W], include_deleted=True)
    monkeypatch.setattr(resource_client, 'get_resource_service', lambda: projection.service)
    worker = ResidentResourceProjectionWorker(store, resource_client)
    store.enqueue_resident_resource_projection(R, payload)
    first = store.claim_resident_resource_projections(resident_id=R)
    normal = projection.service.sync_collaboration_resources
    monkeypatch.setattr(projection.service, 'sync_collaboration_resources', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('unavailable')))
    with pytest.raises(RuntimeError, match='unavailable'):
        worker._publish(first)
    retry = store.claim_resident_resource_projections(resident_id=R)
    assert first[0]['payload'] == retry[0]['payload'] == payload
    assert first[0]['revision'] == retry[0]['revision']
    assert first[0]['lease_token'] != retry[0]['lease_token']
    assert store.complete_resident_resource_projections(first)['completed'] == 0
    monkeypatch.setattr(projection.service, 'sync_collaboration_resources', normal)
    assert worker._publish(retry) == 1
    assert told(projection) == [(R, 'deleted')]
    assert projection.descriptors.get(R)['status'] == projection.descriptors.get(W)['status'] == 'deleted'
    assert store.resident_resource_projection_health()['pending_count'] == 0
    assert workspaces.rows([W], include_deleted=True) == deleted_workspace


def test_same_timestamp_unchanged_source_can_delete(projection):
    """同时间戳源未变化时允许删除，权限系统只收到删除

    :design: decision_b820faf42218
    :design-point:
    """
    projection.descriptors.put(descriptor(R, time=T1))
    result = projection.service.sync_collaboration_resources([
        {'descriptor': descriptor(R, deleted=True)}])
    assert result['resources'][0]['descriptor']['status'] == 'deleted'
    assert told(projection) == [(R, 'deleted')]


def test_same_second_deletion_can_retire_domain_relations_without_losing_resource(projection):
    """同秒删除允许退役领域关系且保留已有资源描述

    :module: platform.core"""
    current = projection.descriptors.put({
        **descriptor(R, time=T1),
        'relations': [{'kind': 'lineage', 'target_id': L},
                      {'kind': 'workspace', 'target_id': W}],
    })
    with projection.registry.writer('domain_live_sources') as writer:
        writer.put('domain_live_sources', R, current)
    deleted = {**current, 'status': 'deleted', 'deleted_at': T1,
               'relations': [{'kind': 'lineage', 'target_id': L}]}

    result = projection.service.sync_collaboration_resources(
        [{'descriptor': deleted}], domain_identity='society-service')

    saved = result['resources'][0]['descriptor']
    assert saved['status'] == 'deleted' and saved['deleted_at'] == T1
    assert saved['relations'] == current['relations']
    assert projection.registry.get('domain_tombstone_sources', R) is not None


def test_same_second_deletion_still_rejects_independent_descriptor_edit(projection):
    """同秒删除仍拒绝覆盖独立的描述修改

    :module: platform.core"""
    current = projection.descriptors.put(descriptor(R, time=T1))
    with projection.registry.writer('domain_live_sources') as writer:
        writer.put('domain_live_sources', R, current)
    projection.descriptors.put({**current, 'title': 'independent title'})

    with pytest.raises(ValueError, match='Stale domain tombstone'):
        projection.service.sync_collaboration_resources(
            [{'descriptor': {**current, 'status': 'deleted', 'deleted_at': T1,
                             'relations': [{'kind': 'lineage', 'target_id': L}]}}],
            domain_identity='society-service')


def test_workspace_lifecycle_cannot_be_changed_by_resident_projection(projection):
    """Resident 投影不能改变 Workspace 的生命周期

    :module: resident.identity"""
    from management_portal.resources.workspace_store import get_workspace_store
    workspaces = get_workspace_store()
    workspaces.ensure(W, '/share/shared')
    before = workspaces.rows([W])
    with pytest.raises(ValueError, match='Workspace store'):
        projection.service.sync_collaboration_resources([
            {'descriptor': descriptor(W, deleted=True)}])
    assert workspaces.rows([W]) == before
    projection.permissions.assert_not_called()


@pytest.mark.parametrize('principal', ['CASE-A', R])
def test_socket_domain_projection_uses_service_identity(tmp_path, monkeypatch, principal):
    """套接字领域投影只接受 SocietyService 的服务身份签名

    :importance: P1
    :design: decision_b820faf42218
    :design-point:
    """
    import json
    import socket
    import threading
    import time
    from management_portal.resources.config import resource_rpc_signing_key_file
    from management_portal.resources.rpc_auth import load_rpc_signing_key, sign_rpc_auth
    from management_portal.resources.server import ResourceServiceServer
    from management_portal.resources.transport import ResourceServiceClient
    from tests.cases.test_resource_service_process import FakeResourceService, FakePublisher

    class ScopedService(FakeResourceService):
        def sync_collaboration_resource(self, descriptor, *, domain_identity=''):
            return self.sync_collaboration_resources(
                [{'descriptor': descriptor}], domain_identity=domain_identity)

    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '0')
    path = tmp_path / 'scoped-projection.sock'
    key = load_rpc_signing_key(required=True, create=True,
                               path=resource_rpc_signing_key_file(socket_path=path))
    server = ResourceServiceServer(path, service=ScopedService(), publisher=FakePublisher())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(1)
    client = ResourceServiceClient(path, timeout=2)
    payload = {'descriptor': descriptor(R, deleted=True)}

    def exchange(method, params, auth=None):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(path))
            with connection.makefile('rwb') as stream:
                stream.write(json.dumps({'id': 'regression', 'method': method,
                                         'params': params, 'auth': auth}).encode() + b'\n')
                stream.flush()
                return json.loads(stream.readline())

    try:
        assert client.sync_collaboration_resource(**payload)['resources'] == [{'resource_id': R}]
        assert client.sync_collaboration_resources([payload])['resources'] == [{'resource_id': R}]
        assert client._exchange('resource.query', {'actor': principal})['actor'] == principal
        method = 'resource.collaboration.sync-many'
        params = {'resources': [payload]}
        # Exercise failure responses through the real client as well as the
        # raw envelope cases that its normal API intentionally cannot emit.
        unsigned = ResourceServiceClient(path, timeout=2)
        unsigned._rpc_signing_key = None
        unsigned._rpc_signing_key_path = tmp_path / 'absent-signing-key'
        with pytest.raises(PermissionError):
            unsigned.sync_collaboration_resources([payload])
        wrong_key = ResourceServiceClient(path, timeout=2)
        wrong_key._rpc_signing_key = b'wrong signing key' * 2
        with pytest.raises(PermissionError):
            wrong_key.sync_collaboration_resources([payload])
        with pytest.raises(PermissionError):
            client._exchange(method, {**params, 'actor': principal})
        rejected = [
            (params, None),
            ({**params, 'actor': 'society-service'}, None),
            (params, sign_rpc_auth(b'wrong signing key' * 2, method, params, actor='society-service')),
            (params, sign_rpc_auth(key, method, params, actor=principal)),
        ]
        forged = {**params, 'actor': 'society-service'}
        rejected.append((forged, sign_rpc_auth(key, method, forged, actor=principal)))
        for request_params, auth in rejected:
            response = exchange(method, request_params, auth)
            assert not response['ok'], response
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize('rid', [R, L])
@pytest.mark.parametrize('change', ['descriptor', 'relations'])
def test_deleted_same_second_different_source_rejects_bundle_without_replacing_receipt(projection, rid, change):
    """同秒不同删除源使整个混合投影被拒绝且回执不变

    :importance: P1
    :design: decision_b820faf42218
    :design-point:
    """
    from management_portal.resources.domain_projection_evidence import EVIDENCE_COLLECTION, SOURCE_COLLECTION
    payload = {'descriptor': descriptor(rid, deleted=True)}
    projection.service.sync_collaboration_resources([payload], domain_identity='society-service')
    projection.permissions.reset_mock()
    before = {c: projection.registry.documents(c)
              for c in ('resident', 'lineage', 'workspace', EVIDENCE_COLLECTION, SOURCE_COLLECTION)}
    different = copy.deepcopy(payload)
    if change == 'descriptor':
        different['descriptor']['title'] = 'different deletion source'
    elif change == 'relations':
        different['descriptor']['relations'] = [{'kind': 'lineage', 'target_id': L}]
    live_id = 'lineage_555555555555'
    live = live_lineage_projection(live_id)
    with pytest.raises(ValueError, match='Stale domain tombstone'):
        projection.service.sync_collaboration_resources([live, different], domain_identity='society-service')
    assert {c: projection.registry.documents(c) for c in before} == before
    projection.permissions.assert_not_called()


@pytest.mark.parametrize('rid', [R, L])
def test_deleted_original_source_retry_preserves_concurrent_descriptor_and_receipt(projection, rid):
    """原始删除源重试保留并发描述修改与原始回执，权限系统只收到删除

    :design: decision_b820faf42218
    :design-point:
    """
    from management_portal.resources.domain_projection_evidence import EVIDENCE_COLLECTION, SOURCE_COLLECTION
    payload = {'descriptor': descriptor(rid, deleted=True)}
    projection.service.sync_collaboration_resources([payload], domain_identity='society-service')
    projection.permissions.reset_mock()
    receipt = projection.registry.get(EVIDENCE_COLLECTION, rid)
    source = projection.registry.get(SOURCE_COLLECTION, rid)
    concurrent = {**projection.descriptors.get(rid), 'title': 'independent title',
                  'summary': 'keep', 'attributes': {'other': {'keep': True}}, 'updated_at': T2}
    projection.descriptors.put(concurrent)
    before = projection.descriptors.get(rid)
    for _ in range(2):
        result = projection.service.sync_collaboration_resources([payload], domain_identity='society-service')
        assert result['resources'][0]['descriptor'] == before
        assert projection.registry.get(EVIDENCE_COLLECTION, rid) == receipt
        assert projection.registry.get(SOURCE_COLLECTION, rid) == source
    assert told(projection) == [(rid, 'deleted'), (rid, 'deleted')]


def test_deleted_without_saved_source_cannot_issue_replay_proof(projection):
    """删除记录缺少已保存来源时不能签发重放证明

    :design: decision_b820faf42218
    :design-point:
    """
    from management_portal.resources.domain_projection_evidence import EVIDENCE_COLLECTION, SOURCE_COLLECTION
    projection.descriptors.put(descriptor(R, deleted=True))
    payload = {'descriptor': descriptor(R, deleted=True)}
    with pytest.raises(ValueError, match='Stale domain tombstone'):
        projection.service.sync_collaboration_resources([payload], domain_identity='society-service')
    assert projection.registry.get(EVIDENCE_COLLECTION, R) is None
    assert projection.registry.get(SOURCE_COLLECTION, R) is None


@pytest.mark.parametrize('authenticated_receipt', [True, False])
def test_historical_deletion_retry_refreshes_source_only_with_authenticated_receipt(projection, authenticated_receipt):
    """旧 v1 删除源拒绝重试，即使有认证回执也不升级或重写原数据。"""
    from management_portal.resources.domain_projection_evidence import (
        EVIDENCE_COLLECTION, SOURCE_COLLECTION,
    )
    from tests.cases.canonical_domain_fixture import canonical_projection
    registry, service = projection.registry, projection.service
    payload = canonical_projection(R)
    payload['descriptor'].update(status='deleted', deleted_at=T1, updated_at=T1)
    service.sync_collaboration_resources([payload], domain_identity='society-service')
    receipt = registry.get(EVIDENCE_COLLECTION, R)
    historical_source = {'schema': 'tfi.domain-tombstone-source.v1',
                         'resource_id': R, 'source_fingerprint': 'historical descriptor fingerprint'}
    if not authenticated_receipt:
        receipt['domain_identity'] = ''
    with registry.writer(SOURCE_COLLECTION, EVIDENCE_COLLECTION) as writer:
        writer.put(SOURCE_COLLECTION, R, historical_source)
        writer.put(EVIDENCE_COLLECTION, R, receipt)
    with pytest.raises(ValueError, match='Stale domain tombstone'):
        service.sync_collaboration_resources([payload], domain_identity='society-service')
    assert registry.get(SOURCE_COLLECTION, R) == historical_source
    assert registry.get(EVIDENCE_COLLECTION, R) == receipt


def test_deleted_identity_is_terminal_and_preserves_known_business_fields():
    """已删除身份不可更新或恢复且保留删除时的字段

    :module: resident.identity"""
    from management_portal.society_service.state_store import LineageDataStore
    state = LineageDataStore()
    before = deleted_identity(state, {'resident_id': R, 'lineage_id': L,
        'title': 'Known Historical Title'}, deleted_at=T1)
    assert not state.all_residents()
    assert before['title'] == 'Known Historical Title'
    with pytest.raises(ValueError, match='cannot be updated or restored'):
        state.put_resident_record(R, {**before, 'deleted_at': '', 'title': 'revived'})
    assert state.resident_record(R, include_deleted=True) == before
    assert state.resident_deletion(R) == before
    assert 'record' not in before and 'record_json' not in before


def test_deleted_identity_keeps_unknown_business_fields_absent():
    """已删除身份保持未知业务字段缺失

    :module: resident.identity"""
    from management_portal.society_service.state_store import LineageDataStore
    state = LineageDataStore()
    historical = deleted_identity(state, {'resident_id': R, 'lineage_id': L}, deleted_at=T1)
    assert 'owner_ids' not in historical
    assert historical['deleted_at'] == T1 and historical['deleted_by'] == 'CASE-A'
