"""Real PostgreSQL regressions for the definition/layout lock inversion."""
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import time

import pytest

from tests.cases.society_service_test_support import install_resource_boundary
from tests.cases.test_lineage_object_state import LINEAGE_ID, _store


@pytest.fixture
def layout_store(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(tmp_path / "lineages"))
    install_resource_boundary(monkeypatch)
    return _store(tmp_path)


def _layout_write(store, started, object_id, x, y):
    # Own the transaction explicitly: production's standalone retry loop must
    # not hide a deadlock by retrying this one fragment of a larger operation.
    with store.database.transaction() as connection:
        connection.execute("SET LOCAL lock_timeout='3s'")
        connection.execute("SET LOCAL statement_timeout='8s'")
        started.put(connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
        return store.upsert_layout_positions(
            LINEAGE_ID, [{"object_id": object_id, "x": x, "y": y}],
            updated_at="2026-10-16T03:21:00Z",
        )


def _wait_for_lock(connection, pid):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        blockers = connection.execute(
            "SELECT pg_blocking_pids(%s) AS blockers", (pid,),
        ).fetchone()["blockers"]
        if blockers:
            return blockers
        time.sleep(0.01)
    raise AssertionError(f"layout transaction {pid} did not reach the held lock")


def test_definition_and_layout_commit_without_lock_inversion(layout_store):
    """定义与布局并发提交时按相同锁顺序完成"""
    store = layout_store
    definition, runtime = store.lineage_snapshot(LINEAGE_ID)
    definition["title"] = "Graph committed alongside layout"
    definition["revision"] += 1
    started = Queue()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout='3s'")
            connection.execute("SET LOCAL statement_timeout='8s'")
            store.lock_lineage_definition(LINEAGE_ID)
            parent_pid = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
            future = executor.submit(
                _layout_write, store, started, "resident_111111111111", 10, 20,
            )
            layout_pid = started.get(timeout=3)
            assert parent_pid in _wait_for_lock(connection, layout_pid)
            # Exercise the actual graph persistence and layout initialization
            # while the other transaction is waiting on this Lineage.
            store.put_lineage_definition(LINEAGE_ID, definition, runtime)
        result = future.result(timeout=5)
    saved = store.lineage_definition(LINEAGE_ID)
    assert saved["title"] == definition["title"]
    assert saved["revision"] == definition["revision"]
    assert result["revision"] == definition["revision"]
    assert result["layout_revision"] == saved["layout_revision"] == 1
    node = next(node for node in saved["nodes"] if node["id"] == "resident_111111111111")
    assert (node["x"], node["y"]) == (10, 20)


def test_concurrent_layout_commits_preserve_both_revisions(layout_store):
    """并发布局提交保留坐标并分别递增布局版本"""
    store = layout_store
    started = Queue()
    before = store.lineage_definition(LINEAGE_ID)
    with ThreadPoolExecutor(max_workers=2) as executor:
        with store.database.transaction() as connection:
            store.lock_lineage_definition(LINEAGE_ID)
            futures = [executor.submit(_layout_write, store, started, *position) for position in (
                ("resident_111111111111", 10, 20),
                ("resident_222222222222", 30, 40),
            )]
            for _ in futures:
                _wait_for_lock(connection, started.get(timeout=3))
        results = [future.result(timeout=5) for future in futures]
    saved = store.lineage_definition(LINEAGE_ID)
    assert saved["revision"] == before["revision"]
    assert saved["layout_revision"] == before["layout_revision"] + 2
    assert sorted(result["layout_revision"] for result in results) == [1, 2]
    positions = {node["id"]: (node["x"], node["y"]) for node in saved["nodes"]}
    assert positions["resident_111111111111"] == (10, 20)
    assert positions["resident_222222222222"] == (30, 40)
