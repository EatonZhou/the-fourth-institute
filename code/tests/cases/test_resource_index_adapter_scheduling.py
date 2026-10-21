import threading
from concurrent.futures import ThreadPoolExecutor

from management_portal import resource_index_backend as backend
from tests.cases.test_resource_index_backend import _record


def test_wait_includes_a_direct_refresh(isolated_portal_case):
    """刷新等待包含直接启动的刷新

    直接刷新同样等待提交完成。
    """
    entered, release = threading.Event(), threading.Event()
    class Adapter:
        name = "resource_registry"
        def fingerprint(self):
            return "source"
        def scan(self):
            entered.set()
            assert release.wait(3)
            return [], {}
    with isolated_portal_case():
        layer = backend.AdapterLayer(backend.IndexStore(), [Adapter()], start_worker=False)
        with ThreadPoolExecutor(1) as pool:
            refreshing = pool.submit(layer.refresh_now, "resource_registry", force=True)
            try:
                assert entered.wait(2)
                assert not layer.wait(timeout=0)
            finally:
                release.set()
            refreshing.result()
        assert layer.wait(timeout=0)
        layer.stop()


def test_wait_does_not_follow_later_refresh_batches(isolated_portal_case, monkeypatch):
    """刷新等待不被后到批次延长"""
    entered, release = threading.Event(), threading.Event()
    later, release_later, waiting = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(backend, "WATCH_FILESYSTEM", False)

    class Adapter:
        name = "resource_registry"
        scans = 0

        def fingerprint(self):
            return str(self.scans)

        def scan(self):
            self.scans += 1
            if self.scans == 1:
                entered.set()
                assert release.wait(5)
            else:
                later.set()
                assert release_later.wait(5)
            return [], {}

    with isolated_portal_case():
        layer = backend.AdapterLayer(backend.IndexStore(), [Adapter()])
        original_wait = layer._condition.wait
        def wait(timeout=None):
            if threading.current_thread().name.startswith("barrier"):
                waiting.set()
            return original_wait(timeout)
        monkeypatch.setattr(layer._condition, "wait", wait)
        try:
            assert entered.wait(2)
            with ThreadPoolExecutor(1, thread_name_prefix="barrier") as pool:
                result = pool.submit(layer.wait, timeout=3)
                assert waiting.wait(2)
                layer.request_refresh(force=True)
                release.set()
                assert later.wait(2)
                assert result.result(timeout=1)
        finally:
            release.set()
            release_later.set()
            layer.stop()


def test_short_change_burst_has_one_refresh(isolated_portal_case, monkeypatch):
    """密集资源变化合并为一次刷新"""
    monkeypatch.setattr(backend, "WATCH_FILESYSTEM", False)
    class Adapter:
        name = "resource_registry"
        scans = 0
        def fingerprint(self):
            return "source"
        def scan(self):
            self.scans += 1
            return [], {}
    with isolated_portal_case():
        adapter = Adapter()
        layer = backend.AdapterLayer(backend.IndexStore(), [adapter], merge_seconds=.2)
        try:
            for _ in range(50):
                layer.request_refresh(force=True)
            assert layer.wait(timeout=3)
            assert adapter.scans == 1
        finally:
            layer.stop()


def test_background_adapters_refresh_serially_while_existing_records_remain_readable(
    isolated_portal_case, monkeypatch,
):
    """后台适配器串行刷新且旧索引记录持续可读"""
    entered, release = threading.Event(), threading.Event()
    articles_entered = threading.Event()
    monkeypatch.setattr(backend, "WATCH_FILESYSTEM", False)

    class Knowledge:
        name = "project_knowledge"

        def fingerprint(self):
            return "knowledge"

        def scan(self):
            entered.set()
            assert release.wait(5)
            return [], {}

    class Articles:
        name = "resource_registry"

        def fingerprint(self):
            return "registered"

        def scan(self):
            articles_entered.set()
            return [_record("article_3067381f9309", "article", adapter=self.name)], {}

    with isolated_portal_case():
        store = backend.IndexStore()
        store.publish_snapshot("resource_registry", [
            _record("article_3067381f9309", "article", adapter="resource_registry", title="cached"),
        ], {})
        layer = backend.AdapterLayer(store, [Knowledge(), Articles()])
        try:
            assert entered.wait(2)
            assert not articles_entered.wait(0.2)
            assert store.internal_get("article_3067381f9309")["title"] == "cached"
            assert "project_knowledge" in layer.status()["running_adapters"]
            release.set()
            assert layer.wait(timeout=3)
            assert articles_entered.is_set()
            assert store.internal_get("article_3067381f9309")["resource_type"] == "article"
        finally:
            release.set()
            layer.stop()


def test_changes_during_a_scan_are_coalesced_and_committed_sequentially(
    isolated_portal_case, monkeypatch,
):
    """扫描期间多次变更合并后串行提交最新结果"""
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(backend, "WATCH_FILESYSTEM", False)

    class Articles:
        name = "resource_registry"
        title = "original"
        scans = 0
        active = 0
        maximum_active = 0

        def fingerprint(self):
            return self.title

        def scan(self):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.scans += 1
            title = self.title
            if self.scans == 1:
                entered.set()
                assert release.wait(5)
            self.active -= 1
            return [_record("article_3067381f9309", "article", adapter=self.name, title=title)], {}

    with isolated_portal_case():
        store = backend.IndexStore()
        adapter = Articles()
        layer = backend.AdapterLayer(store, [adapter])
        try:
            assert entered.wait(2)
            for title in ("intermediate", "latest"):
                adapter.title = title
                layer.request_refresh([adapter.name], force=True)
            release.set()
            assert layer.wait(timeout=3)
            assert store.internal_get("article_3067381f9309")["title"] == "latest"
            assert adapter.maximum_active == 1
            assert adapter.scans == 2
        finally:
            release.set()
            layer.stop()


def test_unregistration_fences_an_inflight_scan_even_before_first_indexing(isolated_portal_case):
    """注销立即撤下索引条目，先前启动的扫描不能重新发布已删除身份"""
    import pytest
    for already_indexed in (False, True):
        with isolated_portal_case():
            entered, release = threading.Event(), threading.Event()
            rid = "repo_123456789abc"
            record = _record(rid, "repo", adapter="resource_registry")
            class Adapter:
                name = "resource_registry"
                resource_types = ("repo",)
                def fingerprint(self):
                    return "source"
                def scan(self):
                    entered.set()
                    assert release.wait(3)
                    return [record], {}
            store = backend.IndexStore()
            layer = backend.AdapterLayer(store, [Adapter()], start_worker=False)
            try:
                if already_indexed:
                    store.apply(backend.ChangeSet("resource_registry", upserts=(record,)))
                with ThreadPoolExecutor(1) as pool:
                    running = pool.submit(layer.refresh_now, "resource_registry", force=True)
                    try:
                        assert entered.wait(2)
                        layer.project_unregistered("repo", rid)
                        with pytest.raises(KeyError):
                            store.internal_get(rid)
                    finally:
                        release.set()
                    running.result()
                with pytest.raises(KeyError):
                    store.internal_get(rid)
            finally:
                layer.stop()
