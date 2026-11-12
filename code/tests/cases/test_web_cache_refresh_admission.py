"""A denied background worker must not leave readers joined to a dead refresh."""
import pytest

from management_portal import web_read_caches


@pytest.mark.parametrize('mode', ['cold', 'stale'])
def test_failed_thread_start_releases_refresh_and_allows_next_read(monkeypatch, mode):
    cache = web_read_caches._CoalescingReadCache(30)
    old = {'revision': 1}
    if mode == 'stale':
        cache.get('viewer', lambda: old)
        cache.invalidate(preserve_stale=True)
    admitted = []

    class RefusedThread:
        def __init__(self, **kwargs):
            admitted.append(kwargs['args'][2])

        def start(self):
            raise RuntimeError("can't start new thread")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(web_read_caches.threading, 'Thread', RefusedThread)
            options = {'cold_value': {'loaded': False}} if mode == 'cold' else {}
            assert cache.get('viewer', lambda: pytest.fail('No worker was started'), **options) == (
                options['cold_value'] if mode == 'cold' else old)
        assert admitted[0].done(), 'Concurrent waiters must be released'
        assert not cache._inflight
        assert cache.get('viewer', lambda: {'revision': 2}, allow_stale=False, timeout=.1) == {'revision': 2}
    finally:
        cache.invalidate()
