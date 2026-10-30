from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from management_portal import serve_management_portal as portal
from management_portal import http_auth, http_responses, web_inbox, web_read_caches


class _ResponseHarness:
    def __init__(self, path: str, body: dict[str, object] | None = None) -> None:
        encoded = json.dumps(body or {}).encode("utf-8")
        self.path = path
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(encoded)),
        }
        self.rfile = BytesIO(encoded)
        self.wfile = BytesIO()
        self.status = 0

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, _name: str, _value: str) -> None:
        return

    def end_headers(self) -> None:
        return


def test_active_agent_refresh_bursts_share_a_short_cache_without_locking_projection(
    monkeypatch,
) -> None:
    """短时间内连续读取活跃 agent 共用一次投影结果，收集投影时不持有缓存锁"""
    calls: list[str] = []

    def collect_active_agents(*, actor: str) -> dict[str, object]:
        assert web_read_caches._ACTIVE_AGENTS_CACHE_LOCK.acquire(blocking=False)
        web_read_caches._ACTIVE_AGENTS_CACHE_LOCK.release()
        calls.append(actor)
        return {"status": "ok", "actor": actor}

    service = SimpleNamespace(
        projection=SimpleNamespace(collect_active_agents=collect_active_agents),
    )
    monkeypatch.setattr(web_read_caches, "get_society_service", lambda: service)
    with web_read_caches._ACTIVE_AGENTS_CACHE_LOCK:
        web_read_caches._ACTIVE_AGENTS_CACHE.clear()
        web_read_caches._ACTIVE_AGENTS_INFLIGHT.clear()

    first = web_read_caches._cached_active_agents("reader")
    second = web_read_caches._cached_active_agents("reader")

    assert first == second == {"status": "ok", "actor": "reader"}
    assert calls == ["reader"]


def test_coalescing_read_cache_builds_one_snapshot_for_a_concurrent_burst() -> None:
    """合并读缓存在并发请求下只构建一次快照并返回给所有请求"""
    cache = web_read_caches._CoalescingReadCache(5)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def build() -> dict[str, int]:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"revision": calls}

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(cache.get, "same", build) for _ in range(12)]
        assert started.wait(timeout=1)
        release.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert results == [{"revision": 1}] * 12


def test_coalescing_read_cache_does_not_publish_snapshot_invalidated_mid_build() -> None:
    """合并读缓存不保存构建期间已被失效的快照，下次读取重新构建"""
    cache = web_read_caches._CoalescingReadCache(5)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def build() -> dict[str, int]:
        nonlocal calls
        calls += 1
        revision = calls
        if revision == 1:
            started.set()
            assert release.wait(timeout=2)
        return {"revision": revision}

    with ThreadPoolExecutor(max_workers=1) as executor:
        stale = executor.submit(cache.get, "same", build)
        assert started.wait(timeout=1)
        cache.invalidate()
        release.set()
        assert stale.result(timeout=2) == {"revision": 1}

    assert cache.get("same", build) == {"revision": 2}
    assert calls == 2


def test_coalescing_read_cache_serves_stale_while_refreshing_and_on_error() -> None:
    """合并读缓存刷新期间和刷新失败后都返回旧值"""
    cache = web_read_caches._CoalescingReadCache(0)
    assert cache.get("same", lambda: {"revision": 1}) == {"revision": 1}
    started = threading.Event()
    release = threading.Event()

    def fail_refresh() -> dict[str, int]:
        started.set()
        assert release.wait(timeout=2)
        raise TimeoutError("backend is temporarily saturated")

    assert cache.get("same", fail_refresh) == {"revision": 1}
    assert started.wait(timeout=1)
    assert cache.get("same", lambda: {"revision": 2}) == {"revision": 1}
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with cache._lock:
            if not cache._inflight:
                break
        time.sleep(0.01)
    with cache._lock:
        assert not cache._inflight


def test_coalescing_read_cache_preserves_committed_snapshot_during_invalidation() -> None:
    """合并读缓存以保留旧值方式失效时，刷新完成前继续返回已提交的快照"""
    cache = web_read_caches._CoalescingReadCache(30)
    assert cache.get("same", lambda: {"revision": 1}) == {"revision": 1}
    started = threading.Event()
    release = threading.Event()

    def refresh() -> dict[str, int]:
        started.set()
        assert release.wait(timeout=2)
        return {"revision": 2}

    cache.invalidate(preserve_stale=True)
    assert cache.get("same", refresh) == {"revision": 1}
    assert started.wait(timeout=1)
    assert cache.get("same", refresh) == {"revision": 1}
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with cache._lock:
            if not cache._inflight:
                break
        time.sleep(0.01)
    assert cache.get("same", refresh) == {"revision": 2}


def test_schedule_cold_build_survives_refresh_and_preserves_viewer_context(monkeypatch) -> None:
    """定时任务状态首次构建不被过期打断，并沿用查看者的上下文变量"""
    from contextvars import ContextVar

    cache = web_read_caches._CoalescingReadCache(30)
    monkeypatch.setattr(web_read_caches, "_SCHEDULED_TRIGGERED_READ_CACHE", cache)
    started, release = threading.Event(), threading.Event()
    viewer = ContextVar("scheduled_viewer", default="missing")
    calls = []
    def build(actor):
        calls.append((actor, viewer.get()))
        started.set()
        assert release.wait(timeout=2)
        return {"loaded": True, "status": "ok", "items": ["schedule"]}
    monkeypatch.setattr(web_read_caches.scheduled_triggered_monitor, "get_scheduled_triggered_status", build)
    token = viewer.set("current-subject")
    try:
        assert web_read_caches._cached_scheduled_triggered_status("viewer")["loaded"] is False
        assert started.wait(timeout=1)
        cache.expire("viewer")
        assert web_read_caches._cached_scheduled_triggered_status("viewer")["status"] == "loading"
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = web_read_caches._cached_scheduled_triggered_status("viewer")
            if result["loaded"]:
                break
            time.sleep(0.01)
        assert result["items"] == ["schedule"]
        assert calls == [("viewer", "current-subject")]
    finally:
        release.set()
        viewer.reset(token)


@pytest.mark.parametrize("warm", [False, True])
def test_schedule_post_mutation_http_read_waits_for_new_build(monkeypatch, warm) -> None:
    """A committed control cannot read an old cold or stale monitor rebuild."""
    from management_portal.http_agent_status import AgentStatusHttp

    cache = web_read_caches._CoalescingReadCache(30)
    monkeypatch.setattr(web_read_caches, "_SCHEDULED_TRIGGERED_READ_CACHE", cache)
    old = {"loaded": True, "items": [{"paused": False}]}
    new = {"loaded": True, "items": [{"paused": True}]}
    if warm:
        cache.get("viewer", lambda: old)
        cache.expire("viewer")
    started, release = threading.Event(), threading.Event()
    fresh_started, fresh_release = threading.Event(), threading.Event()
    calls = []

    def build(actor):
        calls.append(actor)
        if len(calls) == 1:
            started.set()
            assert release.wait(timeout=5)
            return old
        fresh_started.set()
        assert fresh_release.wait(timeout=5)
        return new

    monkeypatch.setattr(web_read_caches.scheduled_triggered_monitor, "get_scheduled_triggered_status", build)
    handler = AgentStatusHttp(
        respond=lambda request, status, data: (status, data), unavailable=None,
        resident_api=None, cached_summary=None, cached_active=None,
        cached_scheduled_triggered=web_read_caches._cached_scheduled_triggered_status,
        invalidate_scheduled_triggered=cache.expire,
    )
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        web_read_caches._cached_scheduled_triggered_status("viewer")
        assert started.wait(timeout=1)
        old_refresh = cache._inflight["viewer"]
        # The actual HTTP adapter must propagate fresh mode after PauseComponent.
        response = pool.submit(handler.scheduled_triggered, None,
                               urlparse("/api/agent-status/scheduled-triggered?refresh=1&fresh=1"), "viewer")
        assert fresh_started.wait(timeout=1)
        assert not response.done(), "Post-mutation read may not return a loading or stale plan"
        fresh_release.set()
        assert response.result(timeout=2) == (200, new)
        release.set()
        assert old_refresh.result(timeout=2) == old
        assert web_read_caches._cached_scheduled_triggered_status("viewer") == new
        assert calls == ["viewer", "viewer"]
    finally:
        release.set()
        fresh_release.set()
        pool.shutdown(wait=True)


def test_schedule_refresh_does_not_publish_a_pre_mutation_rebuild(monkeypatch) -> None:
    """定时任务状态刷新不发布修改前开始的旧重建结果，以修改后的重建为准"""
    cache = web_read_caches._CoalescingReadCache(30)
    monkeypatch.setattr(web_read_caches, "_SCHEDULED_TRIGGERED_READ_CACHE", cache)
    old = {"loaded": True, "items": [{"paused": False}]}
    new = {"loaded": True, "items": [{"paused": True}]}
    cache.get("viewer", lambda: old)
    started, release = threading.Event(), threading.Event()
    calls = []

    def build(actor):
        calls.append(actor)
        if len(calls) == 1:
            started.set()
            assert release.wait(timeout=2)
            return old
        return new

    monkeypatch.setattr(web_read_caches.scheduled_triggered_monitor, "get_scheduled_triggered_status", build)
    try:
        cache.expire("viewer")
        assert web_read_caches._cached_scheduled_triggered_status("viewer")["refreshing"] is True
        assert started.wait(timeout=1)
        old_refresh = cache._inflight["viewer"]
        cache.expire("viewer")  # The pause commits while an older read is blocked.
        assert web_read_caches._cached_scheduled_triggered_status("viewer")["refreshing"] is True
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and web_read_caches._cached_scheduled_triggered_status("viewer") != new:
            time.sleep(0.01)
        assert web_read_caches._cached_scheduled_triggered_status("viewer") == new
        release.set()
        assert old_refresh.result(timeout=1) == old
        assert web_read_caches._cached_scheduled_triggered_status("viewer") == new
        assert calls == ["viewer", "viewer"]
    finally:
        release.set()


def test_the_inbox_read_after_opening_a_chat_shows_it_read(monkeypatch) -> None:
    """打开 Chat 后再次读取收件箱时显示为已读

    打开 Resident Chat 记录已读后，浏览器紧接着重新读取该 Resident 的收件箱条目，服务器不得返回记录已读之前缓存的未读副本；其他用户的缓存不受影响。
    """
    # Opening a Chat records the read, then the browser reads the Resident's
    # Inbox entry again. A copy cached before the read put the unread mark back.
    cache = web_read_caches._CoalescingReadCache(30)
    monkeypatch.setattr(web_read_caches, "_AGENT_CONVERSATION_READ_CACHE", cache)
    unread = {"sample_admin": True}
    monkeypatch.setattr(web_inbox.conversation_read_state, "set_read", lambda actor, *_args, **_kwargs: unread.update(
        {actor: False}) or {"unread": False, "pinned": False, "read_revision": 2, "dismissed_through": ""})
    monkeypatch.setattr(web_inbox, "_publish_conversation_read_changes", lambda *_args, **_kwargs: None)
    mine, theirs = ("sample_admin", "resident_a", 50, ""), ("sample_guest", "resident_a", 50, "")
    assert cache.get(mine, lambda: {"unread": unread["sample_admin"]}) == {"unread": True}
    assert cache.get(theirs, lambda: {"unread": True}) == {"unread": True}
    started, release = threading.Event(), threading.Event()

    def slow_build() -> dict[str, bool]:
        started.set()
        assert release.wait(timeout=2)
        return {"unread": True}

    with cache._lock:  # a rebuild that began before the read
        cache._values[mine] = (0.0, {"unread": True})
    threading.Thread(target=cache.get, args=(mine, slow_build), daemon=True).start()
    assert started.wait(timeout=1)

    web_inbox._set_agent_conversation_read_state("sample_admin", "resident:resident_a", unread=False)
    web_read_caches._invalidate_agent_conversation_cache()
    release.set()
    assert cache.get(mine, lambda: {"unread": unread["sample_admin"]}) == {"unread": False}
    assert cache.get(theirs, lambda: {"unread": True}) == {"unread": True}


def test_coalescing_read_cache_returns_cold_value_while_first_build_runs() -> None:
    """合并读缓存首次构建期间返回冷启动占位值，构建完成后返回结果"""
    cache = web_read_caches._CoalescingReadCache(30)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def build() -> dict[str, object]:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"loaded": True, "items": ["ready"]}

    cold = {"loaded": False, "items": []}
    assert cache.get("same", build, cold_value=cold) is cold
    assert started.wait(timeout=1)
    assert cache.get("same", build, cold_value=cold) is cold
    assert calls == 1
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with cache._lock:
            if not cache._inflight:
                break
        time.sleep(0.01)
    assert cache.get("same", build, cold_value=cold) == {
        "loaded": True,
        "items": ["ready"],
    }
    assert calls == 1


def test_active_agent_cache_serves_expired_snapshot_while_one_refreshes(
    monkeypatch,
) -> None:
    """活跃 agent 缓存过期后由一个后台刷新重建，期间继续返回旧快照"""
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def collect_active_agents(*, actor: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            assert release.wait(timeout=2)
        return {"status": "ok", "actor": actor, "revision": calls}

    service = SimpleNamespace(
        projection=SimpleNamespace(collect_active_agents=collect_active_agents),
    )
    monkeypatch.setattr(web_read_caches, "get_society_service", lambda: service)
    monkeypatch.setattr(web_read_caches, "_ACTIVE_AGENTS_CACHE_TTL_SECONDS", 0.0)
    with web_read_caches._ACTIVE_AGENTS_CACHE_LOCK:
        web_read_caches._ACTIVE_AGENTS_CACHE.clear()
        web_read_caches._ACTIVE_AGENTS_INFLIGHT.clear()

    assert web_read_caches._cached_active_agents("reader")["revision"] == 1
    assert web_read_caches._cached_active_agents("reader")["revision"] == 1
    assert started.wait(timeout=1)
    assert web_read_caches._cached_active_agents("reader")["revision"] == 1
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with web_read_caches._ACTIVE_AGENTS_CACHE_LOCK:
            if not web_read_caches._ACTIVE_AGENTS_INFLIGHT:
                break
        time.sleep(0.01)
    with web_read_caches._ACTIVE_AGENTS_CACHE_LOCK:
        assert web_read_caches._ACTIVE_AGENTS_CACHE["reader"][1]["revision"] == 2


def test_active_agent_change_detaches_stale_refresh_before_home_rereads(
    monkeypatch,
) -> None:
    """活跃 agent 投影变更后丢弃进行中的旧刷新，首页重读得到新快照"""
    first_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls: list[int] = []

    def collect_active_agents(*, actor: str) -> dict[str, object]:
        assert actor == "reader"
        with call_lock:
            revision = len(calls) + 1
            calls.append(revision)
        if revision == 1:
            first_started.set()
            assert release_first.wait(3)
        return {"status": "ok", "revision": revision}

    service = SimpleNamespace(
        projection=SimpleNamespace(collect_active_agents=collect_active_agents),
    )
    monkeypatch.setattr(web_read_caches, "get_society_service", lambda: service)
    with web_read_caches._ACTIVE_AGENTS_CACHE_LOCK:
        web_read_caches._ACTIVE_AGENTS_CACHE.clear()
        web_read_caches._ACTIVE_AGENTS_INFLIGHT.clear()

    with ThreadPoolExecutor(max_workers=1) as pool:
        stale_refresh = pool.submit(web_read_caches._cached_active_agents, "reader")
        assert first_started.wait(3)
        try:
            payload = web_read_caches._portal_change_payload(
                "reader",
                [
                    SimpleNamespace(
                        subject="portal.projection.changed.active-agents.snapshot",
                        payload={"kind": "changed"},
                    )
                ],
                {"projection"},
            )
            fresh = web_read_caches._cached_active_agents("reader")
        finally:
            release_first.set()
        stale = stale_refresh.result(timeout=3)

    cached = web_read_caches._cached_active_agents("reader")
    assert payload["projections"] == {"active-agents": ["snapshot"]}
    assert stale == {"status": "ok", "revision": 1}
    assert fresh == cached == {"status": "ok", "revision": 2}
    assert calls == [1, 2]


def test_agent_workbench_get_and_post_share_error_conversion(monkeypatch, subtests, isolated_portal_case):
    """工作台接口把参数、权限、缺失、冲突、发现服务不可用和内部错误转换为对应 HTTP 状态"""
    for error, expected_status in [
        (ValueError("invalid request"), 400),
        (PermissionError("access denied"), 403),
        (FileNotFoundError("missing resource"), 404),
            (FileExistsError("resource exists"), 409),
            (http_responses.ResidentDiscoveryUnavailable("discovery unavailable"), 503),
            (RuntimeError("backend failed"), 500),
    ]:
        with subtests.test(error=error, expected_status=expected_status), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
                    raise error

                monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "reader")
                monkeypatch.setattr(
                    portal,
                    "local_terminal_backend",
                    SimpleNamespace(open_terminal=fail),
                )

                post_handler = _ResponseHarness("/api/agent-workbench/local-terminal/start")
                portal._AGENT_WORKBENCH_HTTP.post_local_terminal_start(post_handler, {}, 'reader')

                assert post_handler.status == expected_status
                get_payload = json.loads(post_handler.wfile.getvalue())
                expected_message = "Portal request failed" if expected_status == 500 else str(error)
                assert get_payload["error"] == expected_message
                assert get_payload["http_status"] == expected_status
                assert get_payload["message"] == expected_message
                assert get_payload["code"].startswith("operation.")




def test_active_agent_failed_refresh_returns_stale_rows_with_error(monkeypatch):
    """活跃 agent 刷新失败时返回旧数据并附带错误信息"""
    done = threading.Event()
    def fail(**kwargs):
        raise RuntimeError('projection unavailable')
    monkeypatch.setattr(web_read_caches, 'get_society_service', lambda: SimpleNamespace(projection=SimpleNamespace(collect_active_agents=fail)))
    with web_read_caches._ACTIVE_AGENTS_CACHE_LOCK:
        web_read_caches._ACTIVE_AGENTS_CACHE['failed-reader'] = (0, {'revision': 1})
        web_read_caches._ACTIVE_AGENTS_INFLIGHT.pop('failed-reader', None)
    original_thread = web_read_caches.threading.Thread
    def thread(*args, **kwargs):
        target = kwargs['target']
        def wrapped():
            try:
                target()
            finally:
                done.set()
        return original_thread(*args, **{**kwargs, 'target': wrapped})
    monkeypatch.setattr(web_read_caches.threading, 'Thread', thread)
    assert web_read_caches._cached_active_agents('failed-reader')['refreshing']
    assert done.wait(2)
    snapshot = web_read_caches._cached_active_agents('failed-reader')
    assert snapshot['revision'] == 1
    assert not snapshot['refreshing']
    assert snapshot['error'] == 'projection unavailable'
