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
