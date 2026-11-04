"""Health inventories managed children, independent of warm-up scheduling."""
from types import SimpleNamespace

from management_portal.society_service.server import LineageReadProcessPool


def test_health_includes_children_that_did_not_run_a_warmup_and_follows_replacement():
    pool = LineageReadProcessPool(0)
    pool.workers = 3
    pool._executor = SimpleNamespace(_processes={
        11: SimpleNamespace(pid=11), 22: SimpleNamespace(pid=22), 33: SimpleNamespace(pid=33)})
    assert pool.health() == {'enabled': True, 'workers': 3, 'worker_pids': [11, 22, 33]}
    pool._executor = SimpleNamespace(_processes={44: SimpleNamespace(pid=44)})
    assert pool.health()['worker_pids'] == [44]
    pool._executor = None
    assert pool.health()['worker_pids'] == []
