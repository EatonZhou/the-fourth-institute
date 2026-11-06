"""Simultaneous module starts share observations without caching identity."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor, Future

import pytest

from management_portal.test_framework import dev_target as target


@pytest.mark.parametrize('failed', [False, True])
def test_parallel_preflight_shares_result_then_rechecks(tmp_path, monkeypatch, failed):
    path = tmp_path / 'target.json'
    path.write_text(json.dumps({'schema': 'tfi.portal-test-target.v1', 'deployment': {
        'id': 'release', 'kind': 'release', 'label': 'Target',
        'origin': 'http://127.0.0.1:28399', 'build_id': 'candidate'}}))
    monkeypatch.setenv('PORTAL_TEST_TARGET_FILE', str(path))
    storage = tmp_path / 'storage.json'
    storage.write_text('{}')
    joined = threading.Event()
    lock = threading.Lock()
    waiters = 0
    original = Future.result
    def wait(self, *args, **kwargs):
        nonlocal waiters
        with lock:
            waiters += 1
            if waiters == 11:
                joined.set()
        return original(self, *args, **kwargs)
    monkeypatch.setattr(target, 'Future', type('ObservedFuture', (Future,), {'result': wait}))
    calls = []
    def observe(self, *args):
        calls.append(args)
        assert joined.wait(5)
        if failed:
            raise target.DevTargetError('wrong identity')
        return 'verified'
    monkeypatch.setattr(target.DevTargetVerifier, '_verify', observe)
    start = threading.Barrier(12)
    def run():
        start.wait(5)
        try:
            return target.DevTargetVerifier().verify(storage)
        except target.DevTargetError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: run(), range(12)))
    assert results == ['wrong identity' if failed else 'verified'] * 12
    assert len(calls) == 1
    assert not target._PREFLIGHTS
    # Success and failure both expire immediately, including changed identity.
    if failed:
        with pytest.raises(target.DevTargetError):
            target.DevTargetVerifier().verify(storage)
    else:
        assert target.DevTargetVerifier().verify(storage) == 'verified'
    assert len(calls) == 2


def test_http_page_budget_allows_slow_valid_response(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'ok'
    def open_(request, *, timeout):
        assert timeout == 60
        return Response()
    monkeypatch.setattr(target, 'urlopen', open_)
    assert target._http_get('http://127.0.0.1/development', {}) == b'ok'
