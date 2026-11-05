import json
from types import SimpleNamespace

from management_common import postgres_database, memory_observation
from management_portal.society_service import memory_profile, lineage_read_cache


def test_owned_json_walk_is_bounded_and_never_calls_custom_size():
    class Private:
        def __sizeof__(self):
            raise AssertionError('must not inspect arbitrary application objects')
    value = {'private': Private(), 'rows': [str(index) for index in range(1000)]}
    result = memory_observation.json_footprint([value], max_nodes=20)
    assert result['visited_nodes'] <= 20
    assert not result['walk_complete']
    assert 'private' not in json.dumps(result)


def test_pool_diagnostics_are_numeric_and_never_export_connection_configuration(monkeypatch):
    pool = SimpleNamespace(closed=False, get_stats=lambda: {'pool_size': 2, 'pool_available': 1})
    shared = SimpleNamespace(pool=pool, references=4)
    with monkeypatch.context() as scoped:
        scoped.setattr(postgres_database, '_POOL_REGISTRY', {
            (postgres_database.os.getpid(), 'secret-dsn-must-not-escape', 'lineage', 0, 2, 30, False): shared})
        result = postgres_database.pool_diagnostics()
    assert result['pool_count'] == 1
    assert result['pools'][0]['owners'] == 4
    assert 'secret-dsn' not in json.dumps(result)


def test_cache_diagnostics_distinguish_expired_owner_records_without_values(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(lineage_read_cache.time, 'monotonic', lambda: clock[0])
    cache = lineage_read_cache.LineageReadCache(ttl_seconds=5)
    cache.put('state', ('root', 'lineage', 'actor'), {'fingerprint': 'x', 'lineage': {'body': 'private-body' * 100}})
    clock[0] = 10
    result = cache.diagnostics()
    assert result['kinds']['state'] == {'entries': 1, 'expired': 1}
    assert result['owned_bytes_lower_bound'] > 1000
    assert 'private-body' not in json.dumps(result)


def test_thread_observation_contains_only_names_positions_and_counts():
    private = 'request-body-sentinel-must-not-escape'
    result = memory_profile.thread_summary()
    assert result['python_threads'] >= 1
    assert private not in json.dumps(result)
    assert all(set(frame) == {'file', 'function', 'line'} for row in result['groups'] for frame in row['stack'])


def test_worker_rejects_legacy_and_mismatched_target_lease(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_profile, 'instance_runtime_root', lambda: tmp_path)
    monkeypatch.setattr(memory_profile, '_role', 'read')
    folder = tmp_path / 'diagnostics'
    folder.mkdir()
    lease = {'expires_at': memory_profile.time.time() + 30, 'trace_allocations': False}
    (folder / 'society-memory-profile.json').write_text(json.dumps(lease))
    assert memory_profile.start() is None
    lease['targets'] = {str(memory_profile.os.getpid()): 1}
    (folder / 'society-memory-profile.json').write_text(json.dumps(lease))
    assert memory_profile.start() is None
    assert not list(folder.glob('*.jsonl'))


def _worker_observation():
    return memory_profile.sample(detailed=False)


def test_real_index_worker_observes_its_own_role_and_allocators():
    from management_portal.society_service.server import LineageReadProcessPool
    pool = LineageReadProcessPool(1, role='index')
    try:
        result = pool._executor.submit(_worker_observation).result(timeout=15)
        assert result['pid'] in pool.health()['worker_pids']
        assert result['role'] == 'index'
        assert result['process']['created_at'] > 0
        assert result['allocators']['pymalloc']['available'] is True
        assert result['threads']['python_threads'] >= 1
    finally:
        pool.close()
