"""Read canonical observations and reject retired tags without rewriting inputs."""
import copy
import json

import pytest
from management_portal import capacity_alerts, component_lifecycle, device_identity_backend, portal_deployment_registry


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_capacity_observation_and_previous_state_are_read_without_mutation(prefix):
    """容量读取保留输入，旧标签分别在观测和历史状态入口被拒绝"""
    sample = {'schema': f'{prefix}.runtime-capacity.v1', 'observed_at': '2026-01-01T00:00:00Z',
              'database': {'status': 'unavailable'}, 'disks': {'status': 'unavailable'},
              'execution_queue': {'status': 'unavailable'}}
    previous = {'schema': f'{prefix}.capacity-alerts.v1', 'sequence': 7, 'metrics': {}, 'outbox': []}
    original = copy.deepcopy([previous, sample])
    call = lambda p, s: capacity_alerts.advance(p, s, lineage_id='lineage_test', recipients=[], policy=capacity_alerts.policy_with())
    if prefix == 'archive':
        with pytest.raises(ValueError, match='Unsupported capacity alert state'):
            call(previous, {**sample, 'schema': 'tfi.runtime-capacity.v1'})
        with pytest.raises(ValueError, match='Unsupported runtime capacity schema'):
            call({**previous, 'schema': 'tfi.capacity-alerts.v1'}, sample)
        assert [previous, sample] == original
        return
    result = call(previous, sample)
    assert result['sequence'] == 7 and result['outbox'] == []
    assert [previous, sample] == original
    for previous_change, sample_change in [({'schema': f'{prefix}.capacity-alerts.v2'}, {}), ({}, {'schema': f'{prefix}.runtime-capacity.v2'}), ({}, {'database': {'status': 'guessed'}})]:
        with pytest.raises(ValueError):
            call({**previous, **previous_change}, {**sample, **sample_change})
    assert [previous, sample] == original


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_component_and_device_status_read_keep_files_unchanged(prefix, tmp_path):
    """组件和设备缓存只读取规范版本，退役标签及错误版本均不写回"""
    path = component_lifecycle.lifecycle_path('test', instance_root=tmp_path)
    path.parent.mkdir()
    payload = {'schema': f'{prefix}.portal-component-lifecycle.v1', 'instance': 'test', 'updated_at': 123,
               'components': {'web': {'state': 'ready', 'operation_id': 'frozen'}}}
    path.write_text(json.dumps(payload))
    original = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    expected = payload['components']['web'] if prefix == 'tfi' else {}
    assert component_lifecycle.read_component_state('test', 'web', instance_root=tmp_path) == expected
    assert (path.read_bytes(), path.stat().st_mtime_ns) == (original, mtime)
    path.write_text(json.dumps({**payload, 'schema': f'{prefix}.portal-component-lifecycle.v2'}))
    original, mtime = path.read_bytes(), path.stat().st_mtime_ns
    assert component_lifecycle.read_component_state('test', 'web', instance_root=tmp_path) == {}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == (original, mtime)
    device = tmp_path/'status.json'
    saved = {'schema': f'{prefix}.device_status.v1', 'updated_at': 'original', 'servers': {'device_0123456789ab': {'payload': {'status': 'ok'}}}}
    device.write_text(json.dumps(saved)); original, mtime = device.read_bytes(), device.stat().st_mtime_ns
    expected = {'status': 'ok'} if prefix == 'tfi' else None
    assert device_identity_backend.cached_gpu_server_status('device_0123456789ab', status_path=device) == expected
    assert (device.read_bytes(), device.stat().st_mtime_ns) == (original, mtime)
    device.write_text(json.dumps({**saved, 'schema': f'{prefix}.device_status.v2'}))
    original, mtime = device.read_bytes(), device.stat().st_mtime_ns
    assert device_identity_backend.cached_gpu_server_status('device_0123456789ab', status_path=device) is None
    assert (device.read_bytes(), device.stat().st_mtime_ns) == (original, mtime)


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_device_normalizer_keeps_identity_validation(prefix):
    """设备规范格式保留身份校验，退役格式和错误数字版本被拒绝且输入不变"""
    record = {'schema': f'{prefix}.device.v1', 'device_id': 'device_0123456789ab', 'name': 'test device',
              'device_kind': 'gpu_server', 'lifecycle': 'active', 'hostname': 'example',
              'network': {'primary_ip': '192.0.2.10', 'mac_addresses': []},
              'access_methods': {'ssh': {'host': 'example', 'config_path': '/tmp/test-config'}},
              'monitor': {'kind': 'nvidia_smi', 'expected_gpu_count': 1}}
    original = copy.deepcopy(record)
    if prefix == 'archive':
        with pytest.raises(ValueError, match='invalid schema'):
            device_identity_backend._normalize_record(record, record['device_id'])
        assert record == original
        return
    assert device_identity_backend._normalize_record(record, record['device_id'])['name'] == 'test device'
    assert record == original
    for changes, document in [({'schema': f'{prefix}.device.v2'}, record['device_id']), ({}, 'device_ffffffffffff'), ({'device_kind': '*'}, record['device_id'])]:
        with pytest.raises(ValueError):
            device_identity_backend._normalize_record({**record, **changes}, document)
    assert record == original


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_deployment_reader_keeps_loopback_validation_and_file(prefix, tmp_path):
    """部署读取拒绝退役标签及错误版本，规范格式仍校验回环地址且不改文件"""
    path = tmp_path/'deployments.json'
    instance = {'id': 'test', 'label': 'test', 'kind': 'exploration', 'origin': 'http://127.0.0.1:12345'}
    payload = {'schema': f'{prefix}.portal-deployments.v1', 'instances': [instance]}
    path.write_text(json.dumps(payload)); original, mtime = path.read_bytes(), path.stat().st_mtime_ns
    if prefix == 'archive':
        with pytest.raises(ValueError, match='unsupported deployment registry schema'):
            portal_deployment_registry.load_portal_deployments(path)
        assert (path.read_bytes(), path.stat().st_mtime_ns) == (original, mtime)
        return
    assert portal_deployment_registry.load_portal_deployments(path)[0].origin == instance['origin']
    assert (path.read_bytes(), path.stat().st_mtime_ns) == (original, mtime)
    for changes in [{'schema': f'{prefix}.portal-deployments.v2'}, {'instances': [{**instance, 'origin': 'http://192.0.2.1:12345'}]}]:
        path.write_text(json.dumps({**payload, **changes}))
        original, mtime = path.read_bytes(), path.stat().st_mtime_ns
        with pytest.raises(ValueError):
            portal_deployment_registry.load_portal_deployments(path)
        assert (path.read_bytes(), path.stat().st_mtime_ns) == (original, mtime)
