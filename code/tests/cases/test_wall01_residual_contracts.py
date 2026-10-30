""":importance: P2"""
import asyncio
import json
import os
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.cases.test_lineage_backend import lineage_store

from management_portal.society_service import lineage_aggregate


def test_overflow_record_reaches_full_index_and_archive_recovery(monkeypatch, tmp_path):
    """文件监听溢出触发全索引刷新与归档快照恢复"""
    from management_portal import knowledge_archive_projection as archive
    from management_portal.notifications import file_watch
    from management_portal.resource_index_backend import IndexStore
    from management_portal.resource_index_refresh import AdapterLayer

    layer = AdapterLayer(
        IndexStore(),
        [SimpleNamespace(name="knowledge"), SimpleNamespace(name="projects")],
        start_worker=False,
    )
    layer.request_refresh(["knowledge"], paths=["changed.md"])
    publisher = mock.Mock()
    monkeypatch.setattr(archive, "publish_local_projection_change", publisher)
    read_fd, write_fd = os.pipe()

    def init_descriptor(flags):
        return read_fd

    def add_watch(*args):
        raise AssertionError("No real filesystem watches are required")

    monkeypatch.setattr(
        file_watch.ctypes, "CDLL",
        lambda *args, **kwargs: SimpleNamespace(
            inotify_init1=init_descriptor, inotify_add_watch=add_watch,
        ),
    )

    def recover():
        layer._handle_file_overflow()
        archive._archive_overflow()
        watcher._stopped.set()

    watcher = file_watch.RecursiveFileChangeWatcher(
        [], lambda path: pytest.fail(f"Overflow is not a file change: {path}"), recover,
    )
    try:
        os.write(write_fd, watcher._EVENT.pack(-1, watcher._IN_Q_OVERFLOW, 0, 0))
        watcher._run()
        assert watcher.status()["last_error"] == ""
        assert set(layer._pending) == {"knowledge", "projects"}
        assert all(refresh.force and refresh.full for refresh in layer._pending.values())
        assert layer._queue.qsize() == 2
        publisher.assert_called_once_with(
            "knowledge-archive", "snapshot", fields=["filesystem-overflow"],
        )
        assert watcher._fd == -1
    finally:
        os.close(write_fd)
        layer.stop()


def test_missing_resource_socket_returns_503_without_stale_success(monkeypatch, tmp_path):
    """资源套接字缺失时返回 503 且不携带过期成功数据"""
    from management_portal import asgi
    from management_portal import http_auth, web_resources
    from management_portal.resources.transport import ResourceServiceClient
    from tests.cases.test_portal_asgi import _request, _response_body

    client = ResourceServiceClient(socket_path=tmp_path / "absent.sock", timeout=0.1)
    monkeypatch.setattr(http_auth, "_check_session", lambda handler: True)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda handler: "alice")
    monkeypatch.setattr(asgi.portal, "get_resource_service", lambda: client)
    monkeypatch.setattr(web_resources, "get_resource_service", lambda: client)
    response = asyncio.run(asgi.portal_http(
        _request("/api/resources/resolve?address=resident_111111111111"),
    ))
    payload = json.loads(asyncio.run(_response_body(response)))
    assert response.status_code == 503
    assert payload["code"] == "resource_service_unavailable"
    assert "resource" not in payload
    assert "record" not in payload


def test_empty_lineage_delete_releases_state_lock_before_external_effects(
    lineage_store, monkeypatch,
):
    """空 Lineage 删除在清理目录前释放状态锁"""
    from management_portal.society_service import invariant_locks, lineage_lifecycle

    created = lineage_lifecycle.create_lineage(
        {"title": "Isolated residual lock contract", "nodes": [], "edges": []},
        "sample_admin",
    )
    observed = []
    move = lineage_lifecycle._purge_deleted_lineage_folder

    def observe_move(*args, **kwargs):
        observed.append(("filesystem_purge", invariant_locks.lock_held()))
        return move(*args, **kwargs)

    monkeypatch.setattr(lineage_lifecycle, "_purge_deleted_lineage_folder", observe_move)
    deleted = lineage_lifecycle.delete_lineage(created["lineage_id"], "sample_admin")
    assert deleted["lineage_id"] == created["lineage_id"]
    assert observed == [("filesystem_purge", False)]


def test_empty_lineage_delete_retries_the_same_intent_after_move_failure(
    lineage_store, monkeypatch,
):
    """空 Lineage 删除清理失败后复用原意图重试"""
    from management_portal.society_service import lineage_graph_edits, lineage_lifecycle, lineage_state

    created = lineage_lifecycle.create_lineage(
        {"title": "Retryable empty deletion", "nodes": [], "edges": []},
        "sample_admin",
    )
    move = lineage_lifecycle._purge_deleted_lineage_folder
    attempts = []

    def fail_once(*args, **kwargs):
        attempts.append(args)
        if len(attempts) == 1:
            raise OSError("isolated move failure")
        return move(*args, **kwargs)

    monkeypatch.setattr(lineage_lifecycle, "_purge_deleted_lineage_folder", fail_once)
    with pytest.raises(OSError, match="isolated move failure"):
        lineage_lifecycle.delete_lineage(created["lineage_id"], "sample_admin")

    intent = lineage_aggregate.lineage_deletion_intent(created["lineage_id"])
    assert intent["phase"] == "retryable"
    assert intent["operation_id"].startswith("lineage-delete_")
    with pytest.raises(lineage_state.LineageConflictError, match="deletion is in progress"):
        lineage_graph_edits.update_lineage(
            created["lineage_id"],
            {**created, "title": "must not revive"},
            "sample_admin",
        )

    deleted = lineage_lifecycle.delete_lineage(created["lineage_id"], "sample_admin")
    assert deleted["operation_id"] == intent["operation_id"]
    assert len(attempts) == 2
    assert not lineage_aggregate.lineage_deletion_intent(created["lineage_id"])
    assert not lineage_aggregate.lineage_catalog_store().get_access_records(
        [created["lineage_id"]]
    )


def test_empty_lineage_delete_fences_concurrent_update_without_holding_state_lock(
    lineage_store, monkeypatch,
):
    """空 Lineage 删除执行外部操作时无状态锁且阻止并发更新"""
    from management_portal.society_service import invariant_locks, lineage_graph_edits, lineage_lifecycle, lineage_state

    created = lineage_lifecycle.create_lineage(
        {"title": "Concurrent empty deletion", "nodes": [], "edges": []},
        "sample_admin",
    )
    move = lineage_lifecycle._purge_deleted_lineage_folder
    move_started = threading.Event()
    release_move = threading.Event()
    outcome = {}

    def blocked_move(*args, **kwargs):
        outcome["move_lock_held"] = invariant_locks.lock_held()
        move_started.set()
        assert release_move.wait(timeout=5)
        return move(*args, **kwargs)

    def delete():
        try:
            outcome["deleted"] = lineage_lifecycle.delete_lineage(
                created["lineage_id"], "sample_admin"
            )
        except BaseException as exc:
            outcome["error"] = exc

    monkeypatch.setattr(lineage_lifecycle, "_purge_deleted_lineage_folder", blocked_move)
    worker = threading.Thread(target=delete)
    worker.start()
    assert move_started.wait(timeout=5)
    try:
        with pytest.raises(
            lineage_state.LineageConflictError,
            match="deletion is in progress",
        ):
            lineage_graph_edits.update_lineage(
                created["lineage_id"],
                {**created, "title": "concurrent resurrection"},
                "sample_admin",
            )
    finally:
        release_move.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["move_lock_held"] is False
    assert outcome["deleted"]["lineage_id"] == created["lineage_id"]
    assert not lineage_aggregate.lineage_catalog_store().get_access_records(
        [created["lineage_id"]]
    )


def test_business_submit_returns_before_blocked_work_and_deduplicates():
    """业务任务提交在工作阻塞前返回且相同任务不重复启动"""
    from management_portal import business_tasks

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    task_id = "wall01-residual-isolated-blocked-submit"
    workers = []

    def blocked_work():
        workers.append(threading.current_thread())
        entered.set()
        try:
            assert release.wait(3)
        finally:
            finished.set()

    try:
        assert business_tasks.start(task_id, blocked_work) is True
        assert entered.wait(1)
        assert not finished.is_set()
        assert business_tasks.is_active(task_id)
        assert business_tasks.start(task_id, lambda: pytest.fail("duplicate worker")) is False
    finally:
        release.set()
        assert finished.wait(2)
        for worker in workers:
            worker.join(timeout=2)
            assert not worker.is_alive()
    assert not business_tasks.is_active(task_id)
