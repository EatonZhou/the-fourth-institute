"""Projection HTTP batches keep full-address authorization and byte budgets."""
import hashlib
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from management_portal import resources
from management_portal.workbench_projections import http, resource_access
from management_portal.workbench_projections.store import ProjectionError
from tests.cases.test_workspace_resource_content import ADDRESS, RESIDENT, WORKSPACE, resource_http, workspace


@pytest.mark.parametrize("failure,code,status", [
    (FileNotFoundError("missing file"), "file_not_found", 404),
    (PermissionError("denied"), "permission_denied", 403),
])
def test_single_resolve_preserves_missing_and_denied(failure, code, status):
    """单项解析保留缺失与拒绝的真实原因，不将不存在归为权限问题"""
    def resolve(address, *, actor):
        assert actor == RESIDENT
        raise failure
    result = resource_access.dispatch(
        SimpleNamespace(resolve=resolve), "resolve", {"address": ADDRESS},
        RESIDENT, check_workbench=lambda: None,
    )
    assert result["readable"] is False
    assert result["status"] == status
    assert result["error"]["code"] == code


@pytest.fixture
def batch_reader(workspace, resource_http, monkeypatch):
    base, client = resource_http
    calls, rpc_calls = [], []
    gate = SimpleNamespace(allowed=True)
    def get(app_id, actor):
        calls.append((app_id, actor))
        if app_id != 'workbench_111111111111' or actor != 'alice' or not gate.allowed:
            raise PermissionError('workbench unavailable')
        return {}
    exchange = client._exchange
    def tracked(method, params, **kwargs):
        rpc_calls.append((method, dict(params)))
        return exchange(method, params, **kwargs)
    monkeypatch.setattr(client, '_exchange', tracked)
    monkeypatch.setattr(http, 'get_service', lambda: SimpleNamespace(store=SimpleNamespace(get=get)))
    monkeypatch.setattr(resources, 'get_resource_service', lambda: client)
    def request(operation, **arguments):
        req = Request(base + '/api/workbenches/resource', method='POST',
                      data=json.dumps({'id': 'workbench_111111111111', 'operation': operation, 'arguments': arguments}).encode(),
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    return SimpleNamespace(request=request, calls=calls, rpc_calls=rpc_calls, gate=gate)


def test_batch_http_reads_each_full_address_once_with_individual_failures(workspace, batch_reader, monkeypatch):
    """批量读取按完整地址逐项授权并分别返回错误"""
    denied = WORKSPACE + '/file/denied.txt'
    (workspace.root / 'denied.txt').write_text('not public')
    missing = WORKSPACE + '/file/missing.txt'
    authorize = workspace.service._access.authorize_read
    checked = []
    def check(resolved, *, actor):
        checked.append((resolved['address'], actor))
        if resolved['address'] == denied:
            raise PermissionError('child path denied')
        return authorize(resolved, actor=actor)
    monkeypatch.setattr(workspace.service._access, 'authorize_read', check)
    addresses = [ADDRESS, denied, missing, ADDRESS]
    result = batch_reader.request('read_many', addresses=addresses)
    assert [item['address'] for item in result['results']] == addresses
    first, blocked, absent, duplicate = result['results']
    assert first['ok'] and duplicate['result'] == first['result']
    assert first['result']['metadata']['content_sha256'] == hashlib.sha256(workspace.file.read_bytes()).hexdigest()
    assert not blocked['ok'] and blocked['status'] == 403 and blocked['error']['code'] == 'permission_denied'
    assert not absent['ok'] and absent['status'] == 404
    assert result['total_bytes'] == 2 * len(workspace.file.read_bytes())
    assert batch_reader.calls == [('workbench_111111111111', 'alice')] * 2
    assert [method for method, _ in batch_reader.rpc_calls] == ['resource.read_content'] * 4
    assert [params['address'] for _, params in batch_reader.rpc_calls] == addresses
    assert all(params['actor'] == 'alice' for _, params in batch_reader.rpc_calls)
    assert (denied, 'alice') in checked and checked.count((ADDRESS, 'alice')) == 2
    assert not any(ref == WORKSPACE for ref, _ in checked)


def test_resolve_many_is_metadata_only_and_does_not_hide_item_errors(workspace, batch_reader):
    """批量解析仅返回元数据并保留逐项错误"""
    result = batch_reader.request('resolve_many', addresses=[ADDRESS, WORKSPACE + '/file/missing.txt'])
    value = result['results'][0]['result']
    assert value['readable']
    assert set(value) == {'readable', 'resource_type', 'title', 'portal_url'}
    assert result['results'][1]['ok'] is False and result['results'][1]['status'] == 404
    assert result['total_bytes'] == 0
    assert [method for method, _ in batch_reader.rpc_calls] == ['resource.resolve'] * 2


def test_batch_rechecks_resource_revocation_and_workbench_at_delivery(workspace, batch_reader, monkeypatch):
    """批量读取重新检查资源撤权并在交付时检查工作台权限"""
    read = workspace.service.read_content
    count = 0
    def revoking(address, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            workspace.grants.pop(('alice', WORKSPACE))
        return read(address, **kwargs)
    monkeypatch.setattr(workspace.service, 'read_content', revoking)
    result = batch_reader.request('read_many', addresses=[ADDRESS, ADDRESS])
    assert result['results'][0]['ok'] and not result['results'][1]['ok']
    workspace.grants[('alice', WORKSPACE)] = '@contributor'
    def revoke_workbench(address, **kwargs):
        data = read(address, **kwargs)
        batch_reader.gate.allowed = False
        return data
    monkeypatch.setattr(workspace.service, 'read_content', revoke_workbench)
    with pytest.raises(HTTPError) as rejected:
        batch_reader.request('read_many', addresses=[ADDRESS])
    assert rejected.value.code == 403


def test_batch_total_budget_returns_complete_bytes_and_never_truncates(workspace, batch_reader):
    """批量预算内返回完整字节且超限条目不截断"""
    raw = b'x' * (5 * 1024 * 1024)
    (workspace.root / 'large.txt').write_bytes(raw)
    large = WORKSPACE + '/file/large.txt'
    result = batch_reader.request('read_many', addresses=[large] * 4 + [ADDRESS])
    assert all(item['ok'] for item in result['results'][:3])
    assert all(item['result']['content'].encode() == raw for item in result['results'][:3])
    assert result['results'][3]['ok'] is False
    assert result['results'][4]['ok'] is True
    assert result['total_bytes'] == 3 * len(raw) + len(workspace.file.read_bytes())
    assert result['total_bytes'] < result['max_total_bytes'] == 16 * 1024 * 1024
    assert batch_reader.rpc_calls[3][1]['max_bytes'] == 1024 * 1024


def test_exhausted_budget_has_explicit_error_without_reading_more(monkeypatch):
    """批量预算耗尽后返回明确错误且停止读取"""
    monkeypatch.setattr(resource_access, 'RESOURCE_BATCH_MAX_BYTES', 3)
    calls = []
    def read(address, **kwargs):
        calls.append((address, kwargs))
        return b'abc', 'text/plain', {}
    result = resource_access.dispatch(SimpleNamespace(read_content=read), 'read_many', {'addresses': ['a', 'b']}, 'alice', check_workbench=lambda: None)
    assert result['total_bytes'] == 3 and len(calls) == 1
    assert result['results'][1]['error']['code'] == 'projection.resource_budget'


@pytest.mark.parametrize('arguments', [
    {'addresses': []}, {'addresses': ['a'] * 129}, {'addresses': [{'address': 'a', 'actor': 'root'}]},
    {'addresses': ['a'], 'max_bytes': 10**9}, {'addresses': ['a'], 'format': {}},
])
def test_batch_invalid_envelope_fails_before_any_resource_call(arguments):
    """批量请求格式无效时不调用资源服务"""
    with pytest.raises(ProjectionError) as rejected:
        resource_access.dispatch(SimpleNamespace(), 'read_many', arguments, 'alice', check_workbench=lambda: None)
    assert rejected.value.code == 'projection.resource_batch'


def test_batch_actor_override_and_workbench_denial_precede_reads(workspace, batch_reader):
    """批量读取前拒绝身份覆盖和无权访问工作台的请求"""
    with pytest.raises(HTTPError) as rejected:
        batch_reader.request('resolve_many', addresses=[ADDRESS], actor=RESIDENT)
    assert rejected.value.code == 403 and not batch_reader.rpc_calls
    batch_reader.gate.allowed = False
    with pytest.raises(HTTPError) as denied:
        batch_reader.request('read_many', addresses=[ADDRESS])
    assert denied.value.code == 403 and not batch_reader.rpc_calls


def test_batch_128_entries_and_transient_error_keep_order_and_actor():
    """批量解析 128 条资源保留顺序、身份及临时错误"""
    from management_portal.resources.errors import ResourceServiceUnavailable
    calls = []
    def resolve(address, *, actor):
        calls.append((address, actor))
        if address == 'source63':
            raise ResourceServiceUnavailable('offline')
        return {'exists': True, 'title': address, 'content': 'must not escape', 'resolved_path': '/private'}
    addresses = [f'source{i}' for i in range(128)]
    result = resource_access.dispatch(SimpleNamespace(resolve=resolve), 'resolve_many', {'addresses': addresses}, 'alice', check_workbench=lambda: None)
    assert calls == [(address, 'alice') for address in addresses]
    assert [item['address'] for item in result['results']] == addresses
    assert result['results'][63]['status'] == 503
    assert result['results'][63]['error']['retryable'] is True
    assert all(item['ok'] for i, item in enumerate(result['results']) if i != 63)
