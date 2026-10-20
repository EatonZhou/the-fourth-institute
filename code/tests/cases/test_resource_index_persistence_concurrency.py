"""Index reads do not wait for the index's database write.

Persisting index changes took the in-memory index lock for the whole
database write, including the schema-wide advisory lock shared with the
other Portal instance; every query, get and change routing waited for it.
The write now runs outside the lock; changes made meanwhile are written by
the next persist, and a failed write puts back what it did not persist.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from management_portal import resource_index_backend as backend
from tests.cases.test_resource_index_backend import _record

FIRST = "skill_0123456789ab"
SECOND = "skill_fedcba987654"


def _slow_writes(monkeypatch, store):
    entered, release = threading.Event(), threading.Event()
    original = store._connect

    @contextmanager
    def slow_connect():
        entered.set()
        assert release.wait(5)
        with original() as connection:
            yield connection

    monkeypatch.setattr(store, "_connect", slow_connect)
    return entered, release


def test_reads_answer_from_memory_while_a_write_is_in_progress(monkeypatch, isolated_portal_case):
    """索引写库期间读取不等待"""
    with isolated_portal_case():
        store = backend.IndexStore()
        store.publish_snapshot("skill", [_record(FIRST, "skill", adapter="skill")], {})
        entered, release = _slow_writes(monkeypatch, store)
        updated = _record(FIRST, "skill", adapter="skill", title="Updated")
        writer = threading.Thread(target=store.publish_snapshot, args=("skill", [updated], {}))
        writer.start()
        try:
            assert entered.wait(5)
            seen = []
            reader = threading.Thread(target=lambda: seen.append(store.internal_get(FIRST)["title"]))
            reader.start()
            reader.join(2)
            assert seen == ["Updated"], "a read waited for the database write"
        finally:
            release.set()
            writer.join(5)
        reopened = backend.IndexStore()
        assert reopened.internal_get(FIRST)["title"] == "Updated"


def test_a_change_made_during_a_write_is_written_next(monkeypatch, isolated_portal_case):
    """写库期间的新变化由下一次写入保存"""
    with isolated_portal_case():
        store = backend.IndexStore()
        store.publish_snapshot("skill", [_record(FIRST, "skill", adapter="skill")], {})
        entered, release = _slow_writes(monkeypatch, store)
        writer = threading.Thread(target=store.publish_snapshot, args=(
            "skill", [_record(FIRST, "skill", adapter="skill", title="During")], {},
        ))
        writer.start()
        assert entered.wait(5)
        later = threading.Thread(target=store.publish_snapshot, args=(
            "skill", [_record(FIRST, "skill", adapter="skill", title="During"),
                      _record(SECOND, "skill", adapter="skill", title="Later")], {},
        ))
        later.start()
        release.set()
        writer.join(5)
        later.join(5)
        reopened = backend.IndexStore()
        assert reopened.internal_get(FIRST)["title"] == "During"
        assert reopened.internal_get(SECOND)["title"] == "Later"


def test_a_failed_write_is_retried_by_the_next_persist(monkeypatch, isolated_portal_case):
    """写库失败的变化由下一次写入重试"""
    with isolated_portal_case():
        store = backend.IndexStore()
        original = store._connect

        @contextmanager
        def failing_connect():
            raise RuntimeError("database unavailable")
            yield  # pragma: no cover

        monkeypatch.setattr(store, "_connect", failing_connect)
        with pytest.raises(RuntimeError):
            store.publish_snapshot("skill", [_record(FIRST, "skill", adapter="skill", title="Kept")], {})
        monkeypatch.setattr(store, "_connect", original)
        store.flush()
        assert backend.IndexStore().internal_get(FIRST)["title"] == "Kept"
