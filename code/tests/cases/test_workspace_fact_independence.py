"""Workspace facts come from WorkspaceStore; descriptor writes and authenticated replay cannot change them.

:module: spec.design-governance
"""
import copy
import threading
from types import SimpleNamespace

import pytest

from management_portal.resources.collaboration_registry import descriptor_resource_record
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.resources.providers.workspace import _resolve
from management_portal.resources.transport import ResourceServiceClient
from management_portal.resources.server import ResourceServiceServer
from management_portal.resources.config import resource_rpc_signing_key_file
from management_portal.resources.rpc_auth import load_rpc_signing_key
from management_portal.resource_index_backend import IndexStore
from tests.cases.test_domain_tombstone_and_restore import projection, descriptor, live_lineage_projection, R, L, W, T0, T1, T2


def workspace(*, time=T1, deleted=False):
    return {**descriptor(W, time=time, deleted=deleted), 'attributes': {
        'path': '/share/retained'}}


def test_workspace_get_index_view_and_resolver_read_the_workspace_store(projection):
    """Workspace 的资源读取、索引视图与解析器都读 Workspace 存储且不带 owner_ids"""
    get_workspace_store().ensure(W, '/share/retained')
    got = projection.service.get(W, actor='CASE-A')
    assert 'owner_ids' not in got['attributes']
    assert got['attributes']['path'] == '/share/retained'
    assert got['capabilities']['read']
    assert _resolve(projection.service, W, actor='CASE-A')['exists']
    index = object.__new__(IndexStore)
    index._portal_access = None
    row = descriptor_resource_record(workspace())
    assert 'owner_ids' not in index._record_with_current_facts(row, 'CASE-A')['attributes']
    assert projection.registry.get('workspace', W) is None


def test_workspace_descriptor_writes_and_replay_cannot_modify_typed_authority(projection):
    """协作描述的写入或重放都不能修改 Workspace 存储中的记录"""
    store = get_workspace_store()
    store.ensure(W, '/share/retained', title='Independent title')
    before = store.rows([W])
    payload = {'descriptor': workspace()}
    with pytest.raises(ValueError, match='WorkspaceStore'):
        projection.descriptors.put(workspace())
    for _ in range(2):
        with pytest.raises(ValueError, match='Workspace store'):
            projection.service.sync_collaboration_resources([payload], domain_identity='society-service')
    assert store.rows([W]) == before
    assert projection.descriptors.get(W)['title'] == 'Independent title'
    assert projection.registry.get('workspace', W) is None


@pytest.mark.parametrize('deleted', [False, True])
def test_workspace_projection_rejects_mixed_batch_before_live_bridge(projection, deleted):
    """含 Workspace 描述的混合投影批次整批被拒，其中的 Lineage 部分也不写入"""
    store = get_workspace_store()
    store.ensure(W, '/share/retained')
    if deleted:
        store.database.write(lambda conn: store.mark_deleted(W, deleted_at=T2, deleted_by='test', connection=conn))
    # The live half must be valid so rejection exercises Workspace ownership.
    live = live_lineage_projection(L)
    workspace_before = store.rows([W], include_deleted=True)
    with pytest.raises(ValueError, match='Workspace store'):
        projection.service.sync_collaboration_resources([
            live,
            {'descriptor': workspace()},
        ])
    assert projection.registry.get('lineage', L) is None
    assert store.rows([W], include_deleted=True) == workspace_before
    projection.permissions.assert_not_called()


def test_real_signed_socket_reads_typed_workspace_and_rejects_projection_writes(projection, tmp_path, monkeypatch):
    """经签名 socket 可读取 Workspace 存储中的记录，投影写入和签名错误的请求被拒绝"""
    from tests.cases.test_resource_service_process import FakePublisher
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '0')
    get_workspace_store().ensure(W, '/share/retained')
    path = tmp_path / 'workspace.sock'
    key = load_rpc_signing_key(required=True, create=True, path=resource_rpc_signing_key_file(socket_path=path))
    server = ResourceServiceServer(path, service=projection.service, publisher=FakePublisher())
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    assert server.started.wait(2)
    try:
        client = ResourceServiceClient(path, timeout=2)
        payload = {'descriptor': workspace()}
        with pytest.raises(ValueError, match='Workspace store'):
            client.sync_collaboration_resources([payload])
        assert client.get(W, actor='CASE-A')['attributes']['path'] == '/share/retained'
        wrong = ResourceServiceClient(path, timeout=2)
        wrong._rpc_signing_key = b'bad key'
        with pytest.raises(PermissionError): wrong.sync_collaboration_resources([payload])
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
