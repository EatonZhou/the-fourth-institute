"""Real Inbox cache caller key growth, idle release, and bounded SWR retention."""
import threading
import time
import weakref

from management_portal import web_inbox, web_read_caches


class Payload(dict):
    pass


def await_empty(cache, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with cache._lock:
            if not cache._values:
                return
        time.sleep(.005)
    raise AssertionError('Idle cache continues to own expired values')


def test_real_inbox_caller_releases_pages_without_an_invalidation(monkeypatch):
    cache = web_read_caches._CoalescingReadCache(.03, max_entries=4, stale_retention_seconds=0)
    monkeypatch.setattr(web_read_caches, '_AGENT_CONVERSATION_READ_CACHE', cache)
    references = []
    def page(actor, **options):
        payload = Payload(actor=actor, page=options['page_token'], body='x' * 8192)
        references.append(weakref.ref(payload))
        return payload
    monkeypatch.setattr(web_inbox, '_agent_conversation_payload', page)
    for index in range(12):
        assert web_inbox._cached_agent_conversation_payload('reader', page_size=2 + index % 2,
                                                          page_token=str(index))['page'] == str(index)
        assert len(cache._values) <= 4
    await_empty(cache)
    assert all(reference() is None for reference in references)
    assert not cache._inflight and cache._timer is None


def test_strict_inbox_invalidation_does_not_retain_unusable_stale_pages():
    cache = web_read_caches._CoalescingReadCache(30, stale_retention_seconds=0)
    payload = Payload(revision=1)
    reference = weakref.ref(payload)
    cache.get('page', lambda: payload, allow_stale=False)
    del payload
    cache.invalidate(preserve_stale=True)
    assert reference() is None
    assert cache._timer is None


def test_swr_grace_preserves_old_value_and_eventually_releases_it():
    cache = web_read_caches._CoalescingReadCache(0, stale_retention_seconds=.05)
    payload = Payload(revision=1)
    reference = weakref.ref(payload)
    cache.get('viewer', lambda: payload)
    del payload
    cache.invalidate(preserve_stale=True)
    started, release = threading.Event(), threading.Event()
    def refresh():
        started.set()
        assert release.wait(2)
        return {'revision': 2}
    assert cache.get('viewer', refresh)['revision'] == 1
    assert started.wait(1)
    assert cache.get('viewer', refresh)['revision'] == 1
    release.set()
    await_empty(cache)
    assert reference() is None


def test_capacity_eviction_keeps_inflight_read_completion_and_actor_isolation():
    cache = web_read_caches._CoalescingReadCache(30, max_entries=2)
    started, release = threading.Event(), threading.Event()
    results = []
    def build():
        started.set()
        assert release.wait(2)
        return {'actor': 'reader-a'}
    thread = threading.Thread(target=lambda: results.append(cache.get(('reader-a', 'page'), build)))
    thread.start()
    assert started.wait(1)
    for index in range(8):
        assert cache.get(('reader-b', index), lambda: {'actor': 'reader-b'})['actor'] == 'reader-b'
        assert len(cache._values) <= 2
    release.set()
    thread.join(2)
    assert results == [{'actor': 'reader-a'}]
    assert not cache._inflight and len(cache._values) <= 2
    cache.invalidate()


def test_canceled_expiry_callback_cannot_remove_a_replacement_timer():
    cache = web_read_caches._CoalescingReadCache(10)
    cache.get('first', lambda: {'revision': 1})
    token = cache._timer_token
    cache.invalidate()
    cache.get('second', lambda: {'revision': 2})
    timer = cache._timer
    cache._expire_idle(weakref.ref(cache), token)
    assert cache._timer is timer and 'second' in cache._values
    cache.invalidate()


def test_cache_owner_can_be_discarded_while_its_weak_timer_is_pending():
    cache = web_read_caches._CoalescingReadCache(30)
    cache.get('viewer', lambda: {'value': 1})
    timer, owner = cache._timer, weakref.ref(cache)
    del cache
    try:
        assert owner() is None
    finally:
        timer.cancel()
