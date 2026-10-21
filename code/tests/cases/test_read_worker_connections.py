"""Projection reads must not retain one backend per process and schema."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from management_common.portal_database import portal_database


def test_projection_and_service_pool_lifetimes_do_not_alias(monkeypatch):
    """投影读连接池与服务连接池互不共用，关闭投影池不影响服务池"""
    monkeypatch.delenv("PORTAL_LINEAGE_READ_WORKER", raising=False)
    service = portal_database(schema="reader_lifetime", max_size=2)
    monkeypatch.setenv("PORTAL_LINEAGE_READ_WORKER", "1")
    projection = portal_database(schema="reader_lifetime", max_size=2)
    try:
        with service.read() as service_connection:
            assert service_connection.execute("SELECT 1").fetchone()[0] == 1
        with projection.read() as projection_connection:
            assert service._pool is not projection._pool
            assert service_connection.raw is not projection_connection.raw
        projection.close()
        assert projection_connection.raw.closed
        with service.read() as c:
            assert c.execute("SELECT 2").fetchone()[0] == 2
    finally:
        service.close()
        projection.close()


def test_frequent_projection_reads_share_bounded_pool_across_domains(monkeypatch):
    """投影读进程的各 schema 共用最多两个连接的池，每个事务切到自己的 schema

    :importance: P1"""
    monkeypatch.setenv("PORTAL_LINEAGE_READ_WORKER", "1")
    databases = [portal_database(schema=f"reader_release_{i}") for i in range(5)]
    borrowed = set()
    try:
        # Logical schemas share physical connections while each transaction
        # selects its own schema, including on repeated reuse.
        for _ in range(3):
            for db in databases:
                with db.read() as connection:
                    assert db._pool is databases[0]._pool
                    borrowed.add(connection.raw)
                    assert connection.execute("SELECT current_schema()").fetchone()[0] == db.schema
                assert db._pool.get_stats()["pool_size"] <= 2
        assert len(borrowed) <= 2
    finally:
        for db in databases:
            db.close()
    assert all(raw.closed for raw in borrowed)


def test_projection_error_rolls_back_and_keeps_nested_domains_isolated(monkeypatch):
    """投影读中嵌套的另一 schema 事务出错只回滚自身，外层事务照常提交"""
    monkeypatch.setenv("PORTAL_LINEAGE_READ_WORKER", "1")
    first = portal_database(schema="reader_first")
    second = portal_database(schema="reader_second")
    try:
        for db in (first, second):
            with db.transaction() as c:
                c.execute("CREATE TABLE IF NOT EXISTS values_by_domain (value text)")
        with first.transaction() as outer:
            outer.execute("INSERT INTO values_by_domain VALUES ('outer')")
            with first.read() as nested:
                assert nested.raw is outer.raw
            with pytest.raises(ValueError, match="rollback"):
                with second.transaction() as failing:
                    assert failing.raw is not outer.raw
                    failing.execute("INSERT INTO values_by_domain VALUES ('discard')")
                    raise ValueError("rollback")
            assert not outer.raw.closed
        with first.snapshot() as c:
            assert c.execute("SELECT value FROM values_by_domain").fetchone()[0] == "outer"
        with second.read() as c:
            assert c.execute("SELECT count(*) FROM values_by_domain").fetchone()[0] == 0
    finally:
        first.close()
        second.close()
    assert failing.raw.closed
    assert outer.raw.closed


def test_projection_connections_are_bounded_and_close_with_last_owner(monkeypatch):
    """投影读连接数满两个时新读取等待，所有使用者关闭后连接随之关闭"""
    monkeypatch.setenv("PORTAL_LINEAGE_READ_WORKER", "1")
    db = portal_database(schema="reader_concurrent", max_size=1)
    acquired = Event()
    started = Event()
    second_acquired = Event()
    release_second = Event()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            def second_reader():
                with db.read() as c:
                    second_acquired.set()
                    assert release_second.wait(10)
                    return c.raw
            def another_reader():
                started.set()
                with db.read() as c:
                    acquired.set()
                    return c.execute("SELECT current_schema()").fetchone()[0], c.raw
            try:
                with db.read() as outer:
                    # Projection workers use one shared two-connection pool,
                    # including when a domain requests a different size.
                    second = executor.submit(second_reader)
                    assert second_acquired.wait(2)
                    future = executor.submit(another_reader)
                    assert started.wait(2)
                    assert not acquired.wait(.05)
                    assert not outer.raw.closed
                    assert db._pool.get_stats()["pool_size"] == 2
                schema, raw = future.result(timeout=5)
            finally:
                release_second.set()
            second_raw = second.result(timeout=5)
            assert schema == db.schema
            assert db._pool.get_stats()["pool_size"] <= 2
    finally:
        db.close()
    assert raw.closed
    assert second_raw.closed
    assert outer.raw.closed
