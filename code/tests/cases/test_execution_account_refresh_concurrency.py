"""Concurrent readers share the existing dynamic catalog refresh.

:module: execution.accounts
"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import time

import pytest

from management_portal.execution import accounts


def test_expired_catalog_is_rebuilt_once_for_concurrent_readers(monkeypatch, record_property):
    catalog = accounts.ExecutionAccounts((), provider_registry=object(), dynamic=True)
    catalog._refreshed_at = time.monotonic() - 2
    ready, release, duplicate = threading.Event(), threading.Event(), threading.Event()
    start = threading.Barrier(9)
    calls = []
    fresh = SimpleNamespace(account_id='fixture', aliases=())

    def load(**kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            duplicate.set()
        ready.set()
        assert release.wait(3)
        return SimpleNamespace(adapters={}, _accounts={'fixture': fresh})

    def read(index):
        start.wait(3)
        return catalog.get('fixture') if index % 2 else catalog.accounts()[0]

    monkeypatch.setattr(accounts, 'default_execution_accounts', load)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(read, index) for index in range(8)]
        try:
            start.wait(3)
            assert ready.wait(3)
            duplicate.wait(.1)
        finally:
            release.set()
        assert all(future.result(3) is fresh for future in futures)
    record_property('concurrent_readers', 8)
    record_property('catalog_rebuilds', len(calls))
    assert len(calls) == 1
    # The existing refresh interval still applies to subsequent calls.
    catalog._refreshed_at = time.monotonic() - 2
    assert catalog.accounts() == (fresh,)
    assert len(calls) == 2


def test_failed_refresh_releases_lock_and_does_not_advance_freshness(monkeypatch):
    catalog = accounts.ExecutionAccounts((), provider_registry=object(), dynamic=True)
    previous = catalog._refreshed_at = time.monotonic() - 2
    fresh = SimpleNamespace(account_id='fixture')
    calls = []

    def load(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise OSError('fixture source unavailable')
        return SimpleNamespace(adapters={}, _accounts={'fixture': fresh})

    monkeypatch.setattr(accounts, 'default_execution_accounts', load)
    with pytest.raises(OSError, match='fixture source unavailable'):
        catalog.accounts()
    assert catalog._refreshed_at == previous
    assert catalog.accounts() == (fresh,)
    assert len(calls) == 2
