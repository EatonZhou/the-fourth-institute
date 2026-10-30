"""Definition identity reads must not hold parent locks over Workspace work."""
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
import copy
import time

import psycopg
import pytest

from management_portal.society_service import lineage_branches, lineage_state, resource_client
from management_portal.society_service.lineage_catalog import LineageCatalog
from management_portal.society_service.resident_catalog import ResidentCatalog
from management_portal.society_service import lineage_aggregate
from tests.cases.society_service_test_support import install_resource_boundary
from tests.cases.test_lineage_object_state import LINEAGE_ID, _store


TARGETS = lineage_state._resident_targets
SOURCE = "resident_111111111111"
OTHER = "resident_222222222222"


class PreparationReached(Exception):
    """Stop the isolated Fork at the Provider boundary; never start a model."""


@pytest.fixture
def identity_store(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(tmp_path / "lineages"))
    install_resource_boundary(monkeypatch)
    store = _store(tmp_path)
    catalog = ResidentCatalog(tmp_path, store)
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: LineageCatalog(store))
    monkeypatch.setattr(lineage_state.resident_catalog, "get_catalog", lambda: catalog)
    monkeypatch.setattr(lineage_state, "_resident_targets", TARGETS)
    # Authorization and read-view assembly are outside the commit under test.
    monkeypatch.setattr(lineage_aggregate, "ensure_manager", lambda *_: None)
    monkeypatch.setattr(lineage_aggregate, "read_lineage", lambda lid, **_: store.lineage_definition(lid))
    return store, catalog


def _fork_preflight():
    return lineage_branches.fork_lineage_resident(LINEAGE_ID, {
        "source": SOURCE, "fork_mode": "recent-10", "child_title": "Isolated child",
        "idempotency_key": "isolated-parent-lock",
    }, "sample_admin")


def _layout(store, started=None):
    # Disable the standalone retry loop: a timeout must stay visible.
    with store.database.transaction() as connection:
        connection.execute("SET LOCAL lock_timeout='400ms'")
        connection.execute("SET LOCAL statement_timeout='3s'")
        if started is not None:
            started.put(connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
        return store.upsert_layout_positions(LINEAGE_ID, [
            {"object_id": OTHER, "x": 70, "y": 80},
        ], updated_at="2026-10-16T04:07:12Z")


def _blocked_by(store, waiter, holder):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with store.database.read() as connection:
            blockers = connection.execute("SELECT pg_blocking_pids(%s) AS pids", (waiter,)).fetchone()["pids"]
        if holder in blockers:
            return
        time.sleep(.005)
    raise AssertionError("the old identity read did not hold the Lineage row")


def test_negative_control_full_projection_holds_fork_parent_lock(identity_store, monkeypatch):
    """对照组：改用完整记录读取时 Workspace 投影占住 Fork 父节点锁，布局保存拿不到锁"""
    store, catalog = identity_store
    entered, started, release = Queue(), Queue(), Event()
    paths = resource_client.workspace_paths_many

    def blocked_paths(*args, **kwargs):
        connection = kwargs["connection"]
        entered.put(connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
        assert release.wait(5), "test did not release the projection boundary"
        return paths(*args, **kwargs)

    def prepared(*_):
        raise PreparationReached

    monkeypatch.setattr(resource_client, "workspace_paths_many", blocked_paths)
    # Controlled regression: use the real former full-record reader, not a
    # copied implementation of Fork, identity resolution or persistence.
    monkeypatch.setattr(catalog, "get_identity_records", catalog.get_many)
    monkeypatch.setattr(lineage_branches, "_create_derived_resident", prepared)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fork = pool.submit(_fork_preflight)
        try:
            holder = entered.get(timeout=5)
            layout = pool.submit(_layout, store, started)
            waiter = started.get(timeout=3)
            _blocked_by(store, waiter, holder)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                layout.result(timeout=3)
        finally:
            release.set()
        with pytest.raises(PreparationReached):
            fork.result(timeout=5)
    assert store.lineage_definition(LINEAGE_ID)["layout_revision"] == 0


def test_fork_validation_and_layout_finish_while_workspace_projection_waits(identity_store, monkeypatch):
    """Workspace 投影阻塞期间，Fork 校验和布局保存仍能完成"""
    store, catalog = identity_store
    entered, release, preparing, finish = Event(), Event(), Event(), Event()
    paths = resource_client.workspace_paths_many

    def blocked_paths(*args, **kwargs):
        entered.set()
        assert release.wait(6)
        return paths(*args, **kwargs)

    def prepared(*_):
        preparing.set()
        assert finish.wait(5)
        raise PreparationReached

    monkeypatch.setattr(resource_client, "workspace_paths_many", blocked_paths)
    monkeypatch.setattr(lineage_branches, "_create_derived_resident", prepared)
    with ThreadPoolExecutor(max_workers=3) as pool:
        view = pool.submit(catalog.get_many, [SOURCE])
        try:
            assert entered.wait(3)
            fork = pool.submit(_fork_preflight)
            assert preparing.wait(3), "Fork validation waited for Workspace projection"
            result = pool.submit(_layout, store).result(timeout=3)
            assert not view.done(), "projection was released before the commit"
            assert result["layout_revision"] == 1
        finally:
            release.set()
            finish.set()
        assert view.result(timeout=3)[0]["resident_id"] == SOURCE
        with pytest.raises(PreparationReached):
            fork.result(timeout=3)
    saved = store.lineage_definition(LINEAGE_ID)
    assert (next(n for n in saved["nodes"] if n["id"] == OTHER)["x"],
            next(n for n in saved["nodes"] if n["id"] == OTHER)["y"]) == (70, 80)
    assert {n["id"] for n in saved["nodes"]} == {SOURCE, OTHER}
    assert not saved.get("edges")


def test_concurrent_definition_merge_uses_only_canonical_identity(identity_store, monkeypatch):
    """并发合并定义只读 Resident 的身份记录，不进入 Workspace 投影，未注册成员被拒绝"""
    store, catalog = identity_store
    expected = {
        r["resident_id"]: {k: str(r.get(k) or "") for k in
            ("resident_id", "source_resident_id", "lineage_id", "title", "backend")}
        for r in catalog.get_many([SOURCE, OTHER])
    }

    def forbidden_projection(*_, **__):
        raise AssertionError("definition commit entered Workspace projection")

    monkeypatch.setattr(resource_client, "workspace_paths_many", forbidden_projection)
    assert lineage_state._resident_targets([OTHER, SOURCE, OTHER, "resident_ffffffffffff"]) == expected
    base = store.lineage_definition(LINEAGE_ID)
    theirs = copy.deepcopy(base)
    theirs.update(description="Concurrent description", revision=base["revision"] + 1)
    store.put_lineage_definition(LINEAGE_ID, theirs, store.lineage_snapshot(LINEAGE_ID)[1])
    ours = copy.deepcopy(base)
    ours["title"] = "Merged title"
    with store.database.transaction():
        merged, did_merge = lineage_state._committed_definition(base, ours, lineage_state.lineage_state_domains.GRAPH)
        assert did_merge
        store.put_lineage_definition(LINEAGE_ID, merged, store.lineage_snapshot(LINEAGE_ID)[1])
    saved = store.lineage_definition(LINEAGE_ID)
    assert (saved["title"], saved["description"]) == ("Merged title", "Concurrent description")
    assert saved["revision"] == base["revision"] + 2
    missing = copy.deepcopy(saved)
    missing["nodes"].append({"id": "resident_ffffffffffff"})
    with pytest.raises(ValueError, match="不存在或未注册"):
        lineage_state._normalize_lineage_payload(missing, saved, "sample_admin")
