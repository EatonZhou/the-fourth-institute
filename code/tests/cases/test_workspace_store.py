""":module: spec.design-governance"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from management_portal.resources.workspace_store import get_workspace_store


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE", "ws_" + uuid4().hex[:12])
    instance = get_workspace_store()
    try:
        yield instance
    finally:
        instance.database.write(lambda conn: conn.execute(f'DROP SCHEMA "{instance.database.schema}" CASCADE'))
        instance.database.close()


def test_registration_arbitrates_concurrent_path_identity(store, tmp_path):
    """并发登记同一路径只产生一个 Workspace ID"""
    def register(index):
        return store.for_path(str(tmp_path), new_id=lambda: f"workspace_{index:012x}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(register, range(12)))
    assert len(set(ids)) == 1
    assert store.paths(ids) == {ids[0]: str(tmp_path)}


def test_deleted_identity_cannot_be_revived_or_reused(store, tmp_path):
    """已删除的 Workspace ID 不能恢复，原路径再次登记时得到新 ID"""
    wid = "workspace_123456789abc"
    store.ensure(wid, str(tmp_path))
    with store.database.transaction() as conn:
        store.mark_deleted(wid, deleted_at="2026-10-10T00:00:00Z", deleted_by="tester", connection=conn)
    with pytest.raises(KeyError):
        store.paths([wid])
    with pytest.raises(ValueError, match="restored"):
        store.ensure(wid, str(tmp_path), move=True)
    assert store.descriptors([wid])[wid]["status"] == "deleted"
    assert store.for_path(str(tmp_path), new_id=lambda: "workspace_abcdef123456") != wid


def test_external_path_is_immutable_and_explicit_move_retains_identity(store, tmp_path):
    """Workspace 路径不能隐式改变，显式移动后保留原 ID"""
    wid = "workspace_123456789abc"
    store.ensure(wid, str(tmp_path / "before"))
    with pytest.raises(ValueError, match="differs"):
        store.ensure(wid, str(tmp_path / "after"))
    store.ensure(wid, str(tmp_path / "after"), move=True)
    assert store.paths([wid]) == {wid: str(tmp_path / "after")}


def test_ensure_participates_in_outer_transaction_and_equal_save_is_noop(store, tmp_path):
    """Workspace 登记随外层事务一起回滚，路径相同的重复保存不改动记录"""
    wid = "workspace_123456789abc"
    with pytest.raises(RuntimeError):
        with store.database.transaction() as conn:
            store.ensure(wid, str(tmp_path), connection=conn)
            raise RuntimeError("rollback")
    assert store.rows([wid]) == {}
    store.ensure(wid, str(tmp_path))
    first = store.rows([wid])
    store.ensure(wid, str(tmp_path), move=True)
    assert store.rows([wid]) == first


def test_exact_path_observation_does_not_allocate_or_probe_registry(store, tmp_path, monkeypatch):
    """重复观察通过精确索引，不获取目录锁、不做 native-case 全表比较。"""
    from management_portal.resources import workspace_store as module
    wid = 'workspace_123456789abc'
    store.ensure(wid, str(tmp_path))
    with monkeypatch.context() as patch:
        patch.setattr(store.cleanup, 'lock', lambda *a: pytest.fail('observation acquired cleanup lock'))
        patch.setattr(module, 'location_key', lambda *a: pytest.fail('observation compared locations'))
        patch.setattr(store.database, 'write', lambda *a: pytest.fail('observation opened write transaction'))
        assert store.for_path(str(tmp_path), new_id=lambda: pytest.fail('allocated')) == wid
        with pytest.raises(ValueError, match='different identity'):
            store.for_path(str(tmp_path), new_id=lambda: '', preferred_id='workspace_aaaaaaaaaaaa')


def test_new_registration_compares_registry_once(store, tmp_path, monkeypatch):
    """新增路径只做一轮完整身份比较，目标 key 只计算一次。"""
    from management_portal.resources import workspace_store as module
    for index in range(5):
        store.ensure(f'workspace_{index:012x}', str(tmp_path / f'old-{index}'))
    calls = []
    key = module.location_key
    monkeypatch.setattr(module, 'location_key', lambda path: (calls.append(path), key(path))[1])
    store.for_path(str(tmp_path), new_id=lambda: 'workspace_aaaaaaaaaaaa')
    assert len(calls) == 6
    assert calls.count(tmp_path) == 1


def test_miss_rechecks_concurrent_registration_under_lock(store, tmp_path, monkeypatch):
    """只读 miss 后他方登记必须由锁内读取发现，不得另建身份。"""
    wid = 'workspace_123456789abc'
    lock = store.cleanup.lock
    entered = False
    def concurrent_registration(connection):
        nonlocal entered
        if not entered:
            entered = True
            store.ensure(wid, str(tmp_path))
        lock(connection)
    monkeypatch.setattr(store.cleanup, 'lock', concurrent_registration)
    assert store.for_path(str(tmp_path), new_id=lambda: pytest.fail('stale miss allocated')) == wid


def test_observed_identity_is_not_a_reservation_and_move_is_fresh(store, tmp_path):
    """观察后的移动/删除立即影响下一次查询，旧观察不预留位置。"""
    before, after = tmp_path / 'before', tmp_path / 'after'
    before.mkdir()
    after.mkdir()
    wid = store.for_path(str(before), new_id=lambda: 'workspace_123456789abc')
    assert store.for_path(str(before), new_id=lambda: '') == wid
    store.ensure(wid, str(after), move=True)
    assert store.for_path(str(after), new_id=lambda: '') == wid
    with pytest.raises(ValueError, match='differs'):
        store.for_path(str(before), new_id=lambda: '', preferred_id=wid)
    assert store.for_path(str(before), new_id=lambda: 'workspace_aaaaaaaaaaaa') != wid


def test_alias_and_tombstone_identity_checks_remain_locked(store, tmp_path, monkeypatch):
    """精确 miss 仍按 native-case 判定别名，墓碑 ID 不可复活。"""
    from management_portal.resources import workspace_store as module
    upper, lower = tmp_path / 'Upper', tmp_path / 'upper'
    upper.mkdir()
    lower.mkdir()
    wid = store.for_path(str(upper), new_id=lambda: 'workspace_123456789abc')
    monkeypatch.setattr(module, 'location_key', lambda p: tuple(part.casefold() for part in p.parts))
    assert store.for_path(str(lower), new_id=lambda: pytest.fail('alias allocated')) == wid
    with pytest.raises(ValueError, match='different identity'):
        store.for_path(str(lower), new_id=lambda: '', preferred_id='workspace_aaaaaaaaaaaa')
    with store.database.transaction() as conn:
        store.mark_deleted(wid, deleted_at='2026-10-10T00:00:00Z', deleted_by='tester', connection=conn)
    with pytest.raises(ValueError, match='restored'):
        store.for_path(str(lower), new_id=lambda: '', preferred_id=wid)
