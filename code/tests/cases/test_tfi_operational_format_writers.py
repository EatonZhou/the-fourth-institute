"""Canonical observations preserve data; retired schemas are rejected without writes."""
import copy
import json
import pytest

from management_portal import capacity_alerts
from management_portal import provider_transport_diagnostics as transport


def test_capacity_new_state_does_not_relabel_existing_state():
    """旧容量格式被拒绝且不改输入；当前格式推进保留既有序列与发件箱。"""
    sample = {'schema': 'tfi.runtime-capacity.v1', 'observed_at': '2026-01-01T00:00:00Z',
              **{key: {'status': 'unavailable'} for key in ('database', 'disks', 'execution_queue')}}
    kwargs = dict(lineage_id='lineage_test', recipients=[], policy=capacity_alerts.policy_with())
    fresh = capacity_alerts.advance(None, sample, **kwargs)
    assert fresh['schema'] == 'tfi.capacity-alerts.v1'
    old = {**fresh, 'schema': 'archive.capacity-alerts.v1'}
    original = copy.deepcopy(old)
    with pytest.raises(ValueError, match='Unsupported capacity alert state'):
        capacity_alerts.advance(old, sample, **kwargs)
    assert old == original
    result = capacity_alerts.advance(fresh, sample, **kwargs)
    assert result['schema'] == fresh['schema']
    assert result['sequence'] == fresh['sequence']
    assert result['outbox'] == fresh['outbox']


def test_transport_new_observation_and_legacy_sanitization_keep_privacy():
    """传输观察拒绝旧前缀与坏版本；当前格式清理敏感字段且不改输入。"""
    observed = []
    observation = transport.ProviderTransportObservation(b'{"prompt":"private"}', 15)
    observation.headers_received(200)
    observation.enter('reading_response_body')
    observation.body_received(b'{"answer":"private"}')
    observation.finish(None, observed.append)
    assert observed[0]['schema'] == 'tfi.provider-transport.v1'
    assert observed[0]['outcome'] == 'completed'
    legacy = {**observed[0], 'schema': 'archive.provider-transport.v1',
              'authorization': 'private', 'response': {**observed[0]['response'], 'body': 'private'}}
    original = copy.deepcopy(legacy)
    assert transport.sanitize_transport_observation(legacy) is None
    current = {**legacy, 'schema': 'tfi.provider-transport.v1'}
    current_original = copy.deepcopy(current)
    sanitized = transport.sanitize_transport_observation(current)
    assert sanitized['schema'] == 'tfi.provider-transport.v1'
    assert 'private' not in json.dumps(sanitized)
    assert legacy == original
    assert current == current_original
    assert transport.sanitize_transport_observation({**legacy, 'schema': 'tfi.provider-transport.v2'}) is None
