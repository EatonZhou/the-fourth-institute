"""Stopping an index scan preserves the last complete projection."""
from __future__ import annotations

from management_portal.paper_resource_index import PaperManifestAdapter, PaperReviewAdapter

import os
import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from management_common.scan_cancellation import ScanCancelled, cancellable_scan
from management_portal import paper_store, resource_index_backend as backend
from management_portal import resource_index_adapters, resource_index_refresh
from skill_board_lan import skill_backend
from tests.cases.test_resource_index_backend import _record


def _slow_tree(tmp_path, monkeypatch):
    """Real directories without matches reproduce the old glob's long wait."""
    root = tmp_path / "papers"
    year = root / "items" / "2026"
    year.mkdir(parents=True)
    for number in range(250):
        (year / str(number)).mkdir()
    entered = threading.Event()
    visited = []
    original = os.scandir

    def scandir(path):
        if str(path).startswith(str(year)):
            visited.append(str(path))
            entered.set()
            time.sleep(0.02)
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(paper_store, "PAPER_STORE", paper_store.PaperStore(root))
    return year, entered, visited


@pytest.mark.parametrize("scan", ["paper", "review", "paths", "skill"])
def test_filesystem_scans_stop_even_when_no_file_matches(tmp_path, monkeypatch, scan):
    """论文、评审、目录树和 Skill 文件扫描在没有匹配文件时也能中途停止"""
    year, entered, visited = _slow_tree(tmp_path, monkeypatch)
    stop = threading.Event()
    cancelled = []
    callbacks = {
        "paper": paper_store.PAPER_STORE.manifest_paths,
        "review": PaperReviewAdapter._markdown_paths,
        "paths": lambda: resource_index_adapters._tree_paths([year]),
        "skill": lambda: skill_backend._candidate_markdown_files(year),
    }

    def run():
        try:
            with cancellable_scan(stop):
                callbacks[scan]()
        except ScanCancelled:
            cancelled.append(True)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        stop.set()
        worker.join(2)
        assert not worker.is_alive()
        assert cancelled == [True]
        assert len(visited) < 250
    finally:
        stop.set()
        worker.join(10)


def test_stop_cancels_paper_scan_without_publishing_partial_snapshot(tmp_path, monkeypatch):
    """停止索引时取消进行中的论文扫描，保留上一份完整快照且不发布部分结果"""
    _year, entered, _visited = _slow_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(resource_index_refresh, "WATCH_FILESYSTEM", False)
    store = backend.IndexStore()
    record = _record("paper_0123456789ab", "paper", adapter="paper")
    store.publish_snapshot("paper", [record], {})
    before = store.status()["adapters"]["paper"]
    notify = mock.Mock()
    layer = resource_index_refresh.AdapterLayer(store, [PaperManifestAdapter()], on_projection_changed=notify)
    try:
        assert entered.wait(5)
        layer.stop(timeout=2)
        assert not layer._worker.is_alive()
        assert store.status()["adapters"]["paper"] == before
        assert store.internal_get(record["resource_id"])["title"] == record["title"]
        assert layer.status()["running_adapters"] == []
        assert layer.status()["pending_adapters"] == []
        assert layer.request_refresh(force=True) == []
        assert layer.wait(timeout=0) is False
        notify.assert_not_called()
        # The persisted snapshot also survives cancellation and restart.
        assert backend.IndexStore().internal_get(record["resource_id"])["title"] == record["title"]
    finally:
        layer.stop(timeout=10)


def _slow_finder(tmp_path, monkeypatch):
    marker = tmp_path / "finder.pid"
    script = tmp_path / "finder.py"
    script.write_text(
        "import os, pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(resource_index_refresh, "_SOURCE_CHANGE_FINDER", script)
    return marker


def test_stop_reaps_source_change_finder_without_marking_source_failed(tmp_path, monkeypatch):
    """停止索引回收来源变更查找子进程，且不把该来源记为失败"""
    marker = _slow_finder(tmp_path, monkeypatch)
    monkeypatch.setattr(resource_index_refresh, "WATCH_FILESYSTEM", False)
    adapter = mock.Mock(name="adapter")
    adapter.name = "paper"
    adapter.fingerprint.side_effect = lambda: resource_index_refresh._find_source_changes({})
    # No source_scope / scan_changes means a plain fingerprint-based adapter.
    del adapter.source_scope
    del adapter.scan_changes
    store = backend.IndexStore()
    layer = resource_index_refresh.AdapterLayer(store, [adapter])
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        pid = int(marker.read_text())
        layer.stop(timeout=2)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        adapter.scan.assert_not_called()
        assert not store.status()["adapters"].get("paper", {}).get("last_error")
    finally:
        layer.stop(timeout=10)


def test_source_change_timeout_reaps_the_child(tmp_path, monkeypatch):
    """来源变更查找超时后回收其子进程"""
    marker = _slow_finder(tmp_path, monkeypatch)
    monkeypatch.setattr(resource_index_refresh, "SOURCE_CHANGE_TIMEOUT_SECONDS", 0.5)
    with pytest.raises(subprocess.TimeoutExpired):
        resource_index_refresh._find_source_changes({})
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_stop_wakes_waiters_and_discards_a_scan_finishing_after_stop(monkeypatch):
    """停止索引唤醒等待者，并丢弃停止后才完成的扫描结果"""
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(resource_index_refresh, "WATCH_FILESYSTEM", False)

    class Adapter:
        name = "paper"

        def fingerprint(self):
            return "new"

        def scan(self):
            entered.set()
            assert release.wait(5)
            return [], {}

    store = backend.IndexStore()
    record = _record("paper_0123456789ab", "paper", adapter="paper")
    store.publish_snapshot("paper", [record], {})
    before = store.status()["adapters"]["paper"]
    layer = resource_index_refresh.AdapterLayer(store, [Adapter()])
    result = []
    waiter = threading.Thread(target=lambda: result.append(layer.wait(timeout=5)))
    try:
        assert entered.wait(5)
        waiter.start()
        with mock.patch.object(store, "flush", wraps=store.flush) as flush:
            with pytest.raises(RuntimeError, match="did not stop"):
                layer.stop(timeout=0)
            flush.assert_not_called()  # Never flush concurrently with an active scan.
        waiter.join(1)
        assert result == [False]
        release.set()
        layer.stop(timeout=2)
        assert store.status()["adapters"]["paper"] == before
    finally:
        release.set()
        layer.stop(timeout=10)
        waiter.join(5)


def test_paper_scope_uses_the_configured_store(tmp_path, monkeypatch):
    """论文适配器的来源范围使用当前配置的 PaperStore 目录"""
    store = paper_store.PaperStore(tmp_path)
    monkeypatch.setattr(paper_store, "PAPER_STORE", store)
    adapter = PaperManifestAdapter()
    assert adapter.source_scope()["trees"][0]["root"] == str(store.items_root)


def test_knowledge_enumeration_cancels_between_entries_and_restores_context(tmp_path, monkeypatch):
    """知识条目枚举在条目之间响应取消，之后同一线程仍可完整枚举"""
    from knowledge_board_lan import knowledge_source

    for name in ("first.md", "second.md"):
        (tmp_path / name).write_text("# Example\n", encoding="utf-8")
    stop = threading.Event()
    original = knowledge_source._read_file_text
    reads = []

    def read(path):
        reads.append(path)
        stop.set()
        return original(path)

    monkeypatch.setattr(knowledge_source, "_read_file_text", read)
    with pytest.raises(ScanCancelled), cancellable_scan(stop):
        knowledge_source._index_entry_specs(tmp_path)
    assert len(reads) == 1
    # Cancellation belongs to that scan only; later calls on the same thread
    # may enumerate normally even though its owner's event remains set.
    assert len(knowledge_source._index_entry_specs(tmp_path)) == 2
