from __future__ import annotations

import pytest

from management_common.portal_database import (
    _database_schema,
    portal_database,
    portal_database_url,
)
from management_common.postgres_database import PostgresDatabase


def test_portal_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 PORTAL_DATABASE_URL 时解析数据库地址报错"""
    monkeypatch.delenv("PORTAL_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="PORTAL_DATABASE_URL"):
        portal_database_url()


def test_every_domain_uses_the_canonical_postgresql_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每个领域都由统一工厂创建 PostgresDatabase，schema 带命名空间前缀"""
    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE", "case-domain")
    agent = portal_database(schema="agent")
    lineage = portal_database(schema="lineage")
    assert isinstance(agent, PostgresDatabase)
    assert isinstance(lineage, PostgresDatabase)
    assert agent.schema == "case_domain_agent"
    assert lineage.schema == "case_domain_lineage"


def test_repeated_domain_resolution_has_one_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一领域重复解析得到相同的数据库标识"""
    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE", "same")
    first = portal_database(schema="agent")
    second = portal_database(schema="agent")
    assert first.identity == second.identity


def test_domain_pool_is_demand_driven_and_attributable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """领域连接池按需建连，最小 0、最大 8，连接以实例名作 application_name"""
    monkeypatch.setenv("PORTAL_INSTANCE", "dev")
    monkeypatch.delenv("PORTAL_POSTGRES_DOMAIN_POOL_SIZE", raising=False)
    monkeypatch.delenv("PORTAL_POSTGRES_POOL_MAX_IDLE_SECONDS", raising=False)
    database = portal_database(schema="agent")
    assert database._min_size == 0
    assert database._max_size == 8
    assert database._max_idle == 1
    assert database._application_name.startswith("tfi:dev:")
    assert len(database._application_name) <= 63


def test_domain_pool_idle_window_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """领域连接池的空闲回收时间可由 PORTAL_POSTGRES_POOL_MAX_IDLE_SECONDS 配置"""
    monkeypatch.setenv("PORTAL_POSTGRES_POOL_MAX_IDLE_SECONDS", "7.5")
    assert portal_database(schema="agent")._max_idle == 7.5


def test_namespace_is_sanitized_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """数据库命名空间被规整为小写下划线名称，超长时报错"""
    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE", "Case / Alpha")
    assert _database_schema("Agent-Data") == "case_alpha_agent_data"
    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE", "x" * 63)
    with pytest.raises(ValueError, match="too long"):
        _database_schema("agent")


def test_domain_schema_is_an_explicit_keyword() -> None:
    """portal_database 的 schema 只能以关键字参数传入"""
    with pytest.raises(TypeError):
        portal_database("agent")  # type: ignore[misc]


def test_read_worker_limits_apply_after_explicit_domain_sizes(monkeypatch):
    """Lineage 读 Worker 的连接上限覆盖显式池大小，每个池最多 2 个连接"""
    monkeypatch.setenv("PORTAL_LINEAGE_READ_WORKER", "1")
    monkeypatch.setenv("PORTAL_POSTGRES_DOMAIN_POOL_SIZE", "8")
    monkeypatch.setenv("PORTAL_POSTGRES_POOL_SIZE", "16")
    monkeypatch.setenv("PORTAL_POSTGRES_POOL_MAX_IDLE_SECONDS", "30")
    for database in (portal_database(schema="agent"),
                     portal_database(schema="workspace", min_size=4, max_size=8)):
        assert database._min_size == 0
        assert database._max_size == 2
        assert database._max_idle == 30


def test_read_worker_reuses_one_bounded_pool_across_domains(monkeypatch):
    """只读进程跨领域复用有界连接池

    :importance: P1
    """
    import time

    monkeypatch.setenv("PORTAL_LINEAGE_READ_WORKER", "1")
    monkeypatch.setenv("PORTAL_POSTGRES_POOL_MAX_IDLE_SECONDS", "1")
    databases = [portal_database(schema=f"projection_idle_{i}") for i in range(5)]
    try:
        pids = set()
        for database in databases:
            with database.read() as connection:
                row = connection.execute("SELECT pg_backend_pid(), current_schema()").fetchone()
                pids.add(row[0])
                assert row[1] == database.schema
        assert len({id(db._pool) for db in databases}) == 1
        assert len(pids) <= 2
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if all(db._pool.get_stats()["pool_size"] == 0 for db in databases):
                break
            time.sleep(.05)
        assert all(db._pool.get_stats()["pool_size"] == 0 for db in databases)
        # Reuse after retirement still resolves the correct domain.
        for database in databases:
            with database.read() as connection:
                assert connection.execute("SELECT current_schema()").fetchone()[0] == database.schema
    finally:
        for database in databases:
            database.close()


def test_shared_read_pool_preserves_transactions_and_schema_isolation(monkeypatch):
    """共享连接池保持 schema 与事务隔离"""
    from management_common.postgres_database import atomic_transaction

    monkeypatch.setenv("PORTAL_LINEAGE_READ_WORKER", "1")
    a, b = [portal_database(schema=f"shared_schema_{name}") for name in ("a", "b")]
    try:
        for db in (a, b):
            with db.transaction() as c:
                c.execute("CREATE TABLE records(value TEXT)")
                c.execute("INSERT INTO records VALUES(%s)", (db.schema,))
        with pytest.raises(RuntimeError):
            with a.transaction() as c:
                c.execute("DELETE FROM records")
                raise RuntimeError("abort")
        with a.snapshot() as c:
            assert c.execute("SELECT value FROM records").fetchone()[0] == a.schema
            with b.read() as other:
                assert other.execute("SELECT value FROM records").fetchone()[0] == b.schema
            assert c.execute("SELECT current_setting('transaction_read_only')").fetchone()[0] == "on"
        with pytest.raises(RuntimeError):
            with atomic_transaction(a, b):
                for db in (a, b):
                    with db.transaction() as c:
                        c.execute("DELETE FROM records")
                raise RuntimeError("atomic abort")
        a.close()
        with b.read() as c:
            assert c.execute("SELECT value FROM records").fetchone()[0] == b.schema
    finally:
        a.close()
        b.close()


def test_default_pool_reclaims_a_connection_burst_without_capping_concurrency(monkeypatch):
    """默认连接池允许 8 个并发连接，突发结束后回收空闲连接"""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.delenv("PORTAL_LINEAGE_READ_WORKER", raising=False)
    monkeypatch.delenv("PORTAL_POSTGRES_POOL_MAX_IDLE_SECONDS", raising=False)
    database = portal_database(schema="pool_burst", max_size=8)
    barrier = threading.Barrier(8, timeout=10)

    def query():
        with database.read() as connection:
            value = connection.execute("SELECT 1").fetchone()[0]
            barrier.wait()
            return value

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            assert list(executor.map(lambda _: query(), range(8))) == [1] * 8
        deadline = time.monotonic() + 5
        while database._pool.get_stats()["pool_size"] > 6 and time.monotonic() < deadline:
            time.sleep(.05)
        assert database._pool.get_stats()["pool_size"] <= 6
        with database.read() as connection:
            assert connection.execute("SELECT 2").fetchone()[0] == 2
    finally:
        database.close()
