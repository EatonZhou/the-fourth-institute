"""Validate owned read contracts without rewriting observations or device inputs."""
import copy
import json

import pytest
from management_portal import capacity_alerts, device_identity_backend


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
def test_device_status_read_keeps_files_unchanged(prefix, tmp_path):
    """设备缓存消费字段而不以格式标签作门槛；读取不改文件或返回共享引用。"""
    device = tmp_path/'status.json'
    saved = {'schema': f'{prefix}.device_status.v1', 'updated_at': 'original', 'servers': {'device_0123456789ab': {'payload': {'status': 'ok'}}}}
    device.write_text(json.dumps(saved)); original, mtime = device.read_bytes(), device.stat().st_mtime_ns
    cached = device_identity_backend.cached_gpu_server_status('device_0123456789ab', status_path=device)
    assert cached == {'status': 'ok'}
    cached['status'] = 'changed locally'
    assert device_identity_backend.cached_gpu_server_status('device_0123456789ab', status_path=device) == {'status': 'ok'}
    assert (device.read_bytes(), device.stat().st_mtime_ns) == (original, mtime)
    device.write_text(json.dumps({**saved, 'schema': f'{prefix}.device_status.v2'}))
    original, mtime = device.read_bytes(), device.stat().st_mtime_ns
    assert device_identity_backend.cached_gpu_server_status('device_0123456789ab', status_path=device) == {'status': 'ok'}
    assert (device.read_bytes(), device.stat().st_mtime_ns) == (original, mtime)
    for servers in ([], None, {'device_0123456789ab': {'payload': []}}):
        device.write_text(json.dumps({**saved, 'servers': servers}))
        original, mtime = device.read_bytes(), device.stat().st_mtime_ns
        assert device_identity_backend.cached_gpu_server_status('device_0123456789ab', status_path=device) is None
        assert (device.read_bytes(), device.stat().st_mtime_ns) == (original, mtime)


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_device_normalizer_keeps_identity_validation(prefix):
    """设备格式标签不影响读取；身份、字段和连接参数仍校验且输入不变。"""
    record = {'schema': f'{prefix}.device.v1', 'device_id': 'device_0123456789ab', 'name': 'test device',
              'device_kind': 'gpu_server', 'lifecycle': 'active', 'hostname': 'example',
              'network': {'primary_ip': '192.0.2.10', 'mac_addresses': []},
              'access_methods': {'ssh': {'host': 'example', 'config_path': '/tmp/test-config'}},
              'monitor': {'kind': 'nvidia_smi', 'expected_gpu_count': 1}}
    original = copy.deepcopy(record)
    for metadata in ({}, {'schema': f'{prefix}.device.v1'}, {'schema': f'{prefix}.device.v2'}):
        fields = {key: value for key, value in record.items() if key != 'schema'}
        assert device_identity_backend._normalize_record({**fields, **metadata}, record['device_id'])['name'] == 'test device'
    assert record == original
    for changes, document in [({'device_id': 'invalid'}, record['device_id']), ({}, 'device_ffffffffffff'),
                              ({'device_kind': '*'}, record['device_id']), ({'unexpected': True}, record['device_id']),
                              ({'access_methods': {'ssh': {'host': '-malicious-option'}}}, record['device_id'])]:
        with pytest.raises(ValueError):
            device_identity_backend._normalize_record({**record, **changes}, document)
    assert record == original
