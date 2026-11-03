"""Bounded Article editing owns its LSP tree without retaining other editors."""
import sys
import threading
import time

import pytest

from management_common import process_runtime
from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
from management_portal import article_texlab_backend as texlab
from tests.cases.test_three_platform_local_tools import LSP_STUB


def wait_for(predicate):
    deadline = time.monotonic() + 8
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.02)


@pytest.fixture
def native_lsp(tmp_path, monkeypatch):
    stub = tmp_path / 'lsp.py'
    stub.write_text(LSP_STUB, encoding='utf-8')
    original = process_runtime.spawn_process
    children = []
    def spawn(_argv, **kwargs):
        child = original([sys.executable, str(stub)], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(process_runtime, 'spawn_process', spawn)
    monkeypatch.setattr(texlab, '_texlab_command', lambda: sys.executable)
    yield children
    texlab._close_all()
    for child in children:
        process_runtime.terminate_process_tree(child, force=True)


def test_scope_close_releases_real_lsp_and_keeps_other_editors(tmp_path, native_lsp):
    store = LifecycleStore()
    left = store.open(str(tmp_path / 'left'), actor='test-actor', run_id='left', case_id='article')
    right = store.open(str(tmp_path / 'right'), actor='test-actor', run_id='right', case_id='article')
    ordinary = texlab._manager('article_existing', tmp_path)
    ordinary.ensure_started()
    with lifecycle_scope(left, actor='test-actor'):
        first = texlab._manager('article_existing', tmp_path)
        first.ensure_started()
        assert texlab._manager('article_existing', tmp_path) is first
    with lifecycle_scope(right, actor='test-actor'):
        second = texlab._manager('article_existing', tmp_path)
        second.ensure_started()
    assert len({id(ordinary), id(first), id(second)}) == 3
    with pytest.raises(TimeoutError):
        store.seal(left, timeout=.05)
    store.request_close(left)
    store.seal(left, timeout=8)
    wait_for(lambda: (left, 'article_existing') not in texlab._TEXLAB_MANAGERS)
    assert not first.alive
    assert ordinary.alive and second.alive
    with pytest.raises(RuntimeError, match='closed'):
        first.ensure_started()
    store.complete(left)
    store.request_close(right)
    store.seal(right, timeout=8)
    store.complete(right)


def test_scope_cleanup_waits_for_current_request(tmp_path, monkeypatch, native_lsp):
    store = LifecycleStore()
    scope = store.open(str(tmp_path), actor='test-actor', run_id='current', case_id='article')
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(texlab.article_registry, '_article_source_context', lambda _: ({}, {}, tmp_path))
    action = texlab._request_with_manager
    def delayed(*args):
        result = action(*args)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(texlab, '_request_with_manager', delayed)
    results = []
    def worker():
        with lifecycle_scope(scope, actor='test-actor'):
            results.append(texlab.article_texlab_request('article_existing', {'action': 'status'}))
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(5)
        manager = texlab._TEXLAB_MANAGERS[(scope, 'article_existing')]
        store.request_close(scope)
        with pytest.raises(TimeoutError):
            store.seal(scope, timeout=.1)
        assert manager.alive
        release.set()
        thread.join(5)
        assert results[0]['available'] is True
        store.seal(scope, timeout=8)
        assert not manager.alive
        store.complete(scope)
    finally:
        release.set()
        thread.join(5)


def test_native_cleanup_failure_retains_owner_and_retries(tmp_path, monkeypatch, native_lsp):
    store = LifecycleStore()
    scope = store.open(str(tmp_path), actor='test-actor', run_id='retry', case_id='article')
    with lifecycle_scope(scope, actor='test-actor'):
        manager = texlab._manager('article_existing', tmp_path)
        manager.ensure_started()
    original = process_runtime.terminate_process_tree
    failed = threading.Event()
    owned = manager.process
    def terminate(process, **kwargs):
        if process is owned and not failed.is_set():
            failed.set()
            raise OSError('native ownership temporarily unavailable')
        return original(process, **kwargs)
    monkeypatch.setattr(process_runtime, 'terminate_process_tree', terminate)
    store.request_close(scope)
    assert failed.wait(5)
    assert manager.process is not None
    assert texlab._TEXLAB_MANAGERS[(scope, 'article_existing')] is manager
    store.seal(scope, timeout=8)
    wait_for(lambda: (scope, 'article_existing') not in texlab._TEXLAB_MANAGERS)
    assert manager.process is None
    store.complete(scope)


def test_web_shutdown_retains_failed_manager_and_closes_others(tmp_path, monkeypatch):
    first = texlab._manager('article_first', tmp_path)
    second = texlab._manager('article_second', tmp_path)
    original = first.close
    def fail():
        raise OSError('unknown native state')
    monkeypatch.setattr(first, 'close', fail)
    texlab._close_all()
    assert texlab._TEXLAB_MANAGERS[('', 'article_first')] is first
    assert ('', 'article_second') not in texlab._TEXLAB_MANAGERS
    monkeypatch.setattr(first, 'close', original)
    texlab._close_all()
    assert ('', 'article_first') not in texlab._TEXLAB_MANAGERS
