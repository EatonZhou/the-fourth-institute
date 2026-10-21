"""Stop control and terminal receipt reads do not wait on unrelated projections.

:module: resident.conversation
:importance: P1
:aspects: functional, performance
:design: decision_c524c775e0be
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from management_portal.society_service import lifecycle, resident_execution_work
from management_portal.resources.content_changes import ContentChangeStore


def test_terminal_receipt_read_does_not_wait_for_a_receipt_writer():
    """终态回执读取不等待无关写事务

    :design-point:
    """
    store = ContentChangeStore()
    ready, release = threading.Event(), threading.Event()

    def writer():
        with store.database.transaction() as connection:
            connection.execute(
                "INSERT INTO content_changes(change_id,execution_id,actor,payload) VALUES (%s,%s,%s,%s)",
                ('uncommitted-stop-receipt', 'execution-unrelated', 'resident_ffffffffffff', '{}'),
            )
            ready.set()
            assert release.wait(5), 'test did not release receipt writer'

    def reader():
        return ContentChangeStore().for_execution('execution-stopped', 'resident_aaaaaaaaaaaa')

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(writer)
        assert ready.wait(5), 'receipt writer did not acquire its transaction'
        reading = pool.submit(reader)
        try:
            assert reading.result(timeout=1) == []
        finally:
            release.set()
            writing.result(timeout=5)
            reading.result(timeout=5)


def test_committed_stop_notifies_exact_execution_before_slow_projection(monkeypatch):
    """Stop提交后及时通知准确执行，不等待慢Conversation投影

    :design-point:
    """
    projection_started, release = threading.Event(), threading.Event()
    notified = threading.Event()
    targets = []

    def project(*args, **kwargs):
        projection_started.set()
        assert release.wait(5), 'test did not release Conversation projection'

    def cancel(execution_id, actor):
        targets.append((execution_id, actor))
        notified.set()

    monkeypatch.setattr(resident_execution_work, 'project_latest_conversation_state', project)
    monkeypatch.setattr(resident_execution_work, 'notify_changed', lambda *_: None)
    from management_portal.society_service import resident_media_domain
    monkeypatch.setattr(resident_media_domain, 'cleanup_expired_input_images', lambda *_: None)
    monkeypatch.setattr(lifecycle, 'notify_execution_cancellation', cancel)
    monkeypatch.setattr(lifecycle, 'notify_execution_binding', lambda *_: None)
    record = {'resident_id': 'resident_aaaaaaaaaaaa'}
    job = { 'execution_id': 'execution-' + 'a' * 24, 'actor': 'CASE-A',
           'stop_requested_at': '2026-10-08T13:20:35Z', "submitted_at": "2026-10-14T00:00:00Z"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        projection = pool.submit(resident_execution_work._post_execution_commit, record, job)
        assert projection_started.wait(5)
        try:
            assert notified.is_set(), 'Stop delivery waited for Conversation projection'
            assert targets == [(job['execution_id'], 'CASE-A')]
        finally:
            release.set()
            projection.result(timeout=5)


@pytest.mark.parametrize('failed_effect', ['cancel', 'projection'])
def test_stop_effect_failure_does_not_suppress_other_committed_effects(monkeypatch, failed_effect):
    """Stop已提交副作用独立交付，单项失败不抑制其他效果

    :aspects: functional, behavior
    :design-point:
    """
    calls = []
    def effect(name):
        def run(*args, **kwargs):
            calls.append(name)
            if failed_effect == name:
                raise RuntimeError(name)
        return run
    monkeypatch.setattr(resident_execution_work, 'project_latest_conversation_state', effect('projection'))
    monkeypatch.setattr(resident_execution_work, 'notify_changed', effect('change'))
    monkeypatch.setattr(lifecycle, 'notify_execution_cancellation', effect('cancel'))
    monkeypatch.setattr(lifecycle, 'notify_execution_binding', effect('binding'))
    with pytest.raises(RuntimeError, match=failed_effect):
        resident_execution_work._post_execution_commit({'resident_id': 'resident_aaaaaaaaaaaa'}, {
             'execution_id': 'execution-' + 'a' * 24, 'actor': 'CASE-A',
            'stop_requested_at': '2026-10-08T13:20:35Z',
         "submitted_at": "2026-10-14T00:00:00Z"})
    assert calls == ['cancel', 'projection', 'change', 'binding']
