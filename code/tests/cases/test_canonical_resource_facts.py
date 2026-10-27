"""Real domain DB, descriptor replay and public reads share canonical truth.

:module: spec.design-governance
"""
import copy
from types import SimpleNamespace

import pytest

from management_portal.society_service import business_fact_views as facts, resource_client, state_store
from management_portal.society_service.lineage_catalog import LineageCatalog
from management_portal.society_service.resident_catalog import ResidentCatalog
from tests.cases.test_domain_tombstone_and_restore import projection, R, L, T1, T2, descriptor
from tests.cases.test_lineage_catalogs import _record, _lineage_definition


@pytest.fixture
def canonical(tmp_path, monkeypatch):
    state = state_store.LineageDataStore()
    monkeypatch.setattr(state_store, 'get_lineage_data_store', lambda: state)
    lineages = LineageCatalog(state)
    residents = ResidentCatalog(tmp_path, state)
    lineages.upsert(_lineage_definition(lineage_id=L, title='Lineage', updated_at=T1))
    residents.upsert({**_record(R, lineage_id=L), 'updated_at': T1, 'title': 'Resident'})
    return SimpleNamespace(state=state, lineages=lineages, residents=residents)


def payload(canonical, kind):
    return (resource_client._resident_resource_projection(canonical.residents.get(R)) if kind == 'resident'
            else resource_client._lineage_resource_projection(canonical.lineages.get(L)))


@pytest.mark.parametrize('kind', ['resident', 'lineage'])
def test_live_old_payload_retries_descriptor_only_and_preserves_independent_fields(canonical, projection, kind):
    """旧活跃投影重试只写描述并保留描述的独立修改"""
    item = payload(canonical, kind); rid = item['descriptor']['resource_id']
    first = projection.service.sync_collaboration_resources([item], domain_identity='society-service')
    saved = projection.descriptors.get(rid)
    saved['title'] = 'independent descriptor'; saved['updated_at'] = T2
    saved['attributes']['extension'] = {'keep': True}
    projection.descriptors.put(saved)
    again = projection.service.sync_collaboration_resources([item], domain_identity='society-service')
    assert 'acl' not in first['resources'][0] and 'acl' not in again['resources'][0]
    assert again['resources'][0]['descriptor']['title'] == 'independent descriptor'
    assert again['resources'][0]['descriptor']['attributes']['extension'] == {'keep': True}
    assert projection.permissions.call_count == 2


@pytest.mark.parametrize('replay', [False, True])
def test_resident_projection_keeps_only_its_current_home_in_the_permission_trees(
    canonical, projection, monkeypatch, replay,
):
    """Resident 投影只把当前所属 Lineage 交给权限树

    :module: resource.service
    :design: decision_0f57656a2acd, decision_1b2bdf5c33d6, decision_600ae27b0538
    """
    from management_portal.permission_system.facts import fact_from_record

    destination = 'lineage_aaaaaaaaaaaa'
    canonical.lineages.upsert(_lineage_definition(lineage_id=destination, updated_at=T2))
    first = payload(canonical, 'resident')
    projection.service.sync_collaboration_resources([first], domain_identity='society-service')
    canonical.residents.upsert({**canonical.residents.get(R), 'lineage_id': destination, 'updated_at': T2})
    latest = payload(canonical, 'resident')
    if replay:
        # A saved source receipt can coexist with a descriptor whose old
        # publisher already accumulated both homes. Replay must repair it.
        polluted = copy.deepcopy(latest['descriptor'])
        polluted['relations'].append({'kind': 'lineage', 'target_id': L})
        polluted['attributes']['extension'] = {'keep': True}
        polluted['title'] = 'independent descriptor'
        projection.descriptors.put(polluted)
        with projection.registry.writer('domain_live_sources') as writer:
            writer.put('domain_live_sources', R, projection.descriptors.normalize(latest['descriptor']))
    saved = projection.service.sync_collaboration_resources([latest], domain_identity='society-service')['resources'][0]['descriptor']
    assert [r['target_id'] for r in saved['relations'] if r['kind'] == 'lineage'] == [destination]
    told = projection.permissions.call_args_list[-1].args[0]
    assert fact_from_record(told, 'resident').owners == {'lineage': destination}
    if replay:
        assert saved['title'] == 'independent descriptor'
        assert saved['attributes']['extension'] == {'keep': True}


def test_lineage_projection_removes_members_that_moved_and_keeps_independent_relations(canonical, projection):
    """Lineage 投影移除已迁出成员并保留独立关系

    :module: resource.service
    :design: decision_0f57656a2acd, decision_1b2bdf5c33d6
    """
    from datetime import datetime, timedelta

    original = payload(canonical, 'lineage')
    projection.service.sync_collaboration_resources([original], domain_identity='society-service')
    extra = {'kind': 'related', 'target_id': 'resident_aaaaaaaaaaaa'}
    current = projection.descriptors.get(L)
    later = (datetime.fromisoformat(current['updated_at'].replace('Z', '+00:00'))
             + timedelta(seconds=1)).isoformat()
    current['relations'].append(extra)
    projection.descriptors.put(current)
    destination = 'lineage_aaaaaaaaaaaa'
    canonical.lineages.upsert(_lineage_definition(lineage_id=destination, updated_at=T2))
    canonical.residents.upsert({**canonical.residents.get(R), 'lineage_id': destination, 'updated_at': T2})
    canonical.lineages.upsert({**canonical.lineages.get(L), 'updated_at': later, 'graph_updated_at': later})
    result = projection.service.sync_collaboration_resources([payload(canonical, 'lineage')], domain_identity='society-service')
    saved = result['resources'][0]['descriptor']
    assert not any(r['kind'] == 'member' and r['target_id'] == R for r in saved['relations'])
    assert extra in saved['relations']


def test_resident_projection_removes_detached_workspaces_without_clearing_its_owned_workspace(canonical, projection):
    """Resident 投影移除解绑 Workspace 并保留自有空间

    :module: resource.service
    :design: decision_0f57656a2acd, decision_1b2bdf5c33d6
    """
    from management_portal.resources.workspace_store import get_workspace_store

    shared = 'workspace_eeeeeeeeeeee'
    path = str(canonical.residents.root / 'shared')
    get_workspace_store().ensure(shared, path)
    canonical.residents.upsert({**canonical.residents.get(R), 'workspace_bindings': [
        {'workspace_id': shared, 'purpose': 'shared', 'path': path},
    ]})
    initial = payload(canonical, 'resident')
    projection.service.sync_collaboration_resources([initial], domain_identity='society-service')
    canonical.residents.upsert({**canonical.residents.get(R), 'workspace_bindings': [], 'updated_at': T2})
    latest = payload(canonical, 'resident')
    saved = projection.service.sync_collaboration_resources([latest], domain_identity='society-service')['resources'][0]['descriptor']
    workspaces = [r['target_id'] for r in saved['relations'] if r['kind'] == 'workspace']
    assert shared not in workspaces
    assert workspaces == [canonical.residents.get(R)['owned_workspace_id']]


@pytest.mark.parametrize('kind', ['resident', 'lineage'])
def test_later_canonical_change_rejects_old_live_payload_before_any_commit(canonical, projection, kind):
    """规范事实更新后旧活跃投影在提交前被拒绝"""
    item = payload(canonical, kind); rid = item['descriptor']['resource_id']
    catalog = canonical.residents if kind == 'resident' else canonical.lineages
    current = catalog.get(rid)
    catalog.upsert({**current, 'title': 'later canonical', 'updated_at': T2})
    with pytest.raises(ValueError, match='Stale canonical'):
        projection.service.sync_collaboration_resources([item], domain_identity='society-service')
    assert projection.registry.get(kind, rid) is None
    assert projection.registry.get('domain_live_sources', rid) is None
    projection.permissions.assert_not_called()


def test_live_retry_rejects_same_second_different_source_and_mixed_bundle(canonical, projection):
    """同秒源内容变化时拒绝旧重试及整个混合投影"""
    first = payload(canonical, 'resident')
    projection.service.sync_collaboration_resources([first], domain_identity='society-service')
    current = canonical.residents.get(R)
    canonical.residents.upsert({**current, 'title': 'new source same second'})
    bad = copy.deepcopy(first)
    deleted = {'descriptor': descriptor(L, deleted=True)}
    before = projection.registry.documents('lineage')
    with pytest.raises(ValueError, match='Stale canonical'):
        projection.service.sync_collaboration_resources([deleted, bad], domain_identity='society-service')
    assert projection.registry.documents('lineage') == before
    assert projection.registry.get('domain_live_sources', R) == projection.descriptors.normalize(first['descriptor'])


def test_missing_canonical_descriptor_keeps_observations_separate(projection):
    """规范记录缺失时只凭已存描述的投影被拒绝"""
    projection.descriptors.put(descriptor(R))
    with pytest.raises(ValueError, match='Stale canonical'):
        projection.service.sync_collaboration_resources([{'descriptor': descriptor(R)}])


@pytest.mark.parametrize('write_kind', ['resident', 'parent', 'membership', 'binding'])
def test_domain_write_is_blocked_until_descriptor_and_source_commit(canonical, projection, monkeypatch, write_kind):
    """描述与源回执提交前阻止相关领域并发写入"""
    import threading
    from management_portal.society_service.state_store import LineageDataStore
    if write_kind == 'binding':
        from management_portal.resources.workspace_store import get_workspace_store
        workspace_id = 'workspace_eeeeeeeeeeee'
        get_workspace_store().ensure(workspace_id, str(canonical.residents.root / 'shared'))
        canonical.residents.upsert({**canonical.residents.get(R), 'workspace_bindings': [
            {'workspace_id': workspace_id, 'purpose': 'shared', 'path': str(canonical.residents.root / 'shared')},
        ]})
    item = payload(canonical, 'lineage' if write_kind == 'membership' else 'resident')
    entered, finished = threading.Event(), threading.Event()
    errors = []
    other = LineageDataStore()
    other_catalog = ResidentCatalog(canonical.residents.root, other)
    replacement = {**canonical.residents.get(R), 'title': 'concurrent canonical', 'updated_at': T2}
    def write():
        entered.set()
        try:
            if write_kind == 'parent':
                LineageCatalog(other).upsert({**canonical.lineages.get(L), 'title': 'new parent', 'updated_at': T2})
            elif write_kind == 'membership':
                other_catalog.upsert(_record('resident_eeeeeeeeeeee', lineage_id=L))
            elif write_kind == 'binding':
                other.database.write(lambda connection: connection.execute(
                    'DELETE FROM resident_workspace_bindings WHERE resident_id=%s', (R,),
                ))
            else:
                other_catalog.upsert(replacement)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()
    original = projection.descriptors.put_with_change
    thread = threading.Thread(target=write)
    def commit(raw, **kwargs):
        thread.start()
        assert entered.wait(1)
        assert not finished.wait(.15), 'Canonical committed before descriptor boundary'
        return original(raw, **kwargs)
    monkeypatch.setattr(projection.descriptors, 'put_with_change', commit)
    result = projection.service.sync_collaboration_resources([item], domain_identity='society-service')
    thread.join(3)
    assert finished.is_set() and not errors
    assert result['resources'][0]['descriptor']['title'] == item['descriptor']['title']
    if write_kind == 'resident':
        assert other_catalog.get(R)['title'] == 'concurrent canonical'
    assert projection.registry.get('domain_live_sources', item['descriptor']['resource_id']) == projection.descriptors.normalize(item['descriptor'])


@pytest.mark.parametrize('kind', ['resident', 'lineage'])
@pytest.mark.parametrize('same_home', [False, True])
def test_projection_does_not_wait_for_unrelated_canonical_writes(canonical, projection, kind, same_home):
    """资源投影仅锁定相关身份并允许无关规范写入

    真实数据库事务验证 Resident 和 Lineage 投影可与无关 Resident 写入并发，包括同一 Lineage 的其他成员；不使用全表共享锁。

    :module: resource.service
    :importance: P1
    :design: decision_71aa039e3211
    """
    import threading
    from management_portal.society_service.state_store import LineageDataStore
    other_id = 'resident_eeeeeeeeeeee'
    other_lineage = L if same_home else 'lineage_ffffffffffff'
    if not same_home:
        canonical.lineages.upsert(_lineage_definition(lineage_id=other_lineage))
    canonical.residents.upsert(_record(other_id, lineage_id=other_lineage))
    item = payload(canonical, kind)
    held, release = threading.Event(), threading.Event()
    errors = []

    def write():
        try:
            state = LineageDataStore()
            catalog = ResidentCatalog(canonical.residents.root, state)
            with state.database.transaction():
                catalog.upsert({**catalog.get(other_id), 'title': 'unrelated change'})
                held.set()
                assert release.wait(5)
        except Exception as exc:
            errors.append(exc)
            held.set()

    thread = threading.Thread(target=write)
    thread.start()
    try:
        assert held.wait(3) and not errors
        result = projection.service.sync_collaboration_resources([item], domain_identity='society-service')
        assert result['resources'][0]['descriptor']['title'] == item['descriptor']['title']
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and not errors


def test_descriptor_failure_rolls_back_complete_mixed_projection(canonical, projection, monkeypatch):
    """描述回执写入失败时整体回滚混合投影"""
    from management_portal.resources.registry_store import RegistryWriter
    before = projection.registry.documents('resident'), projection.registry.documents('lineage')
    original = RegistryWriter.put
    def fail(self, collection, rid, document):
        if collection == 'domain_live_sources':
            raise RuntimeError('receipt unavailable')
        return original(self, collection, rid, document)
    monkeypatch.setattr(RegistryWriter, 'put', fail)
    with pytest.raises(RuntimeError, match='receipt unavailable'):
        projection.service.sync_collaboration_resources([payload(canonical, 'resident'), payload(canonical, 'lineage')], domain_identity='society-service')
    assert (projection.registry.documents('resident'), projection.registry.documents('lineage')) == before
    projection.permissions.assert_called()


@pytest.mark.parametrize('principal', ['CASE-A', R])
def test_real_socket_domain_projection_and_canonical_reads(canonical, projection, tmp_path, monkeypatch, principal):
    """真实套接字投影与读取保持规范事实"""
    import threading
    from management_portal.resources.transport import ResourceServiceClient
    from management_portal.resources.server import ResourceServiceServer
    from management_portal.resources.config import resource_rpc_signing_key_file
    from management_portal.resources.rpc_auth import load_rpc_signing_key
    from tests.cases.test_resource_service_process import FakePublisher
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '0')
    path = tmp_path / 'canonical.sock'
    load_rpc_signing_key(required=True, create=True, path=resource_rpc_signing_key_file(socket_path=path))
    server = ResourceServiceServer(path, service=projection.service, publisher=FakePublisher())
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    assert server.started.wait(2)
    client = ResourceServiceClient(path, timeout=3)
    try:
        result = client.sync_collaboration_resources([payload(canonical, 'resident'), payload(canonical, 'lineage')])
        assert all('acl' not in item for item in result['resources'])
        got = client.get(R, actor=principal)
        assert 'owner_ids' not in got['attributes'] and 'lineage_owner_ids' not in got['attributes']
    finally:
        server.shutdown(); thread.join(2)


@pytest.mark.parametrize('later', [False, True])
def test_current_domain_restore_requires_later_lifecycle_and_keeps_canonical_fields(canonical, projection, later):
    """领域恢复要求更新的生命周期并保留规范字段"""
    item = payload(canonical, 'resident')
    tombstone = {**item['descriptor'], 'status': 'deleted', 'deleted_at': T1}
    projection.descriptors.put(tombstone)
    if later:
        record = canonical.residents.get(R)
        canonical.residents.upsert({**record, 'updated_at': T2})
        item = payload(canonical, 'resident')
        result = projection.service.sync_collaboration_resources([item], domain_identity='society-service')
        assert result['resources'][0]['descriptor']['status'] == 'active'
        assert 'acl' not in result['resources'][0] and 'owner_ids' not in canonical.residents.get(R)
    else:
        with pytest.raises(ValueError, match='Stale canonical'):
            projection.service.sync_collaboration_resources([item], domain_identity='society-service')
        assert projection.descriptors.get(R)['status'] == 'deleted'


def test_persistence_payload_discards_readonly_parent_view_and_preserves_direct_facts(canonical, monkeypatch):
    """持久化载荷去除只读父视图并保留直接事实"""
    from management_portal.society_service import resident_catalog, resident_record_domain
    record = canonical.residents.get(R)
    record['last_error'] = 'retained runtime'
    canonical.residents.upsert(record)
    before = canonical.residents.get(R)
    view = facts.read_records([R])[R]
    monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: canonical.residents)
    persisted = resident_record_domain.validated_resident_record_payload(view)
    canonical.residents.upsert(persisted)
    assert canonical.residents.get(R) == before
    assert persisted['last_error'] == 'retained runtime'
    assert 'owner_ids' not in persisted
