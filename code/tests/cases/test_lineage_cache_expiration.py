"""Idle projections expire without another request or a manual cache clear."""
import threading
import time
import weakref

from management_portal.society_service.lineage_read_cache import LineageReadCache


def test_idle_cache_releases_payload_and_timer_without_a_request():
    released = threading.Event()

    class Payload:
        def __del__(self):
            released.set()

    cache = LineageReadCache(ttl_seconds=0.03)
    cache.put('state', ('root', 'lineage', 'actor'), {'payload': Payload()})
    assert released.wait(2), 'Expired payload remains strongly owned during idle'
    with cache._lock:
        assert not any(cache._records.values())
        assert cache._expiry_timer is None


def test_timer_does_not_keep_discarded_cache_alive():
    cache = LineageReadCache(ttl_seconds=10)
    cache.put('view', ('root', 'lineage', 'actor'), {'value': 1})
    timer = cache._expiry_timer
    owner = weakref.ref(cache)
    del cache
    try:
        assert owner() is None
    finally:
        timer.cancel()


def test_canceled_callback_cannot_replace_a_new_timer():
    cache = LineageReadCache(ttl_seconds=10)
    key = ('root', 'lineage', 'actor')
    cache.put('state', key, {'value': 1})
    old_token = cache._expiry_token
    cache.clear('state')
    cache.put('state', key, {'value': 2})
    current_timer = cache._expiry_timer
    try:
        cache._expire(weakref.ref(cache), old_token)
        assert cache._expiry_timer is current_timer
        assert cache._records['state'][key]['value'] == 2
    finally:
        cache.clear('state')


def test_replacing_full_cache_does_not_evict_unrelated_actor():
    cache = LineageReadCache(ttl_seconds=10, max_entries=2)
    first, second = ('root', 'lineage', 'actor-a'), ('root', 'lineage', 'actor-b')
    try:
        cache.put('state', first, {'fingerprint': 1})
        cache.put('state', second, {'fingerprint': 2})
        cache.put('state', first, {'fingerprint': 3})
        assert cache.get('state', second, 2) is not None
        assert cache.get('state', first, 1) is None
        assert cache.get('state', first, 3) is not None
    finally:
        cache.clear('state')


def test_concurrent_replacement_expiration_and_invalidation():
    cache = LineageReadCache(ttl_seconds=.005)
    errors = []

    def exercise(actor):
        key = ('root', 'lineage', actor)
        try:
            for version in range(40):
                cache.put('state', key, {'fingerprint': version})
                result = cache.get('state', key, version)
                assert result is None or result['fingerprint'] == version
                if version % 7 == 0:
                    cache.invalidate('lineage')
                time.sleep(.001)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=exercise, args=(str(index),)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert not errors
    deadline = time.monotonic() + 2
    while any(cache._records.values()) and time.monotonic() < deadline:
        time.sleep(.01)
    assert not any(cache._records.values())
