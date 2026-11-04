"""Lock records live through holders/waiters, without retaining retired scopes."""
import gc
import threading
import weakref

import pytest

from management_portal.society_service import invariant_locks as locks


def test_retired_scope_records_are_collectible_and_hits_do_not_allocate(monkeypatch, tmp_path):
    monkeypatch.setattr(locks, "_ROOT_PROVIDER", lambda: tmp_path)
    created = []
    original = locks._LockRecord
    class CountedRecord(original):
        def __init__(self):
            super().__init__()
            created.append(weakref.ref(self))
    monkeypatch.setattr(locks, "_LockRecord", CountedRecord)
    with locks.resident_lock("lifetime-reentry"):
        for _ in range(8):
            with locks.resident_lock("lifetime-reentry"):
                assert len(created) == 1
    for index in range(64):
        with locks.resident_lock(f"lifetime-retired-{index}"):
            pass
    gc.collect()  # Isolated test process only; never a service mitigation.
    assert all(reference() is None for reference in created)
    assert not any(key.startswith("resident:lifetime-") for key in locks._LOCKS)


def test_waiter_keeps_the_same_record_after_the_previous_holder_leaves(monkeypatch, tmp_path):
    monkeypatch.setattr(locks, "_ROOT_PROVIDER", lambda: tmp_path)
    obtained, proceed, entered = threading.Event(), threading.Event(), threading.Event()
    original = locks._record
    records = []
    failures = []
    def observed(scope):
        record = original(scope)
        if threading.current_thread().name == "lifetime-waiter":
            records.append(weakref.ref(record))
            obtained.set()
            if not proceed.wait(3):
                raise TimeoutError("waiter test gate")
        return record
    monkeypatch.setattr(locks, "_record", observed)
    def wait():
        try:
            with locks.resident_lock("lifetime-waiting"):
                entered.set()
        except BaseException as error:
            failures.append(error)
    worker = threading.Thread(target=wait, name="lifetime-waiter")
    try:
        with locks.resident_lock("lifetime-waiting"):
            worker.start()
            assert obtained.wait(2)
            first = weakref.ref(locks._LOCKS["resident:lifetime-waiting"])
        # The waiter already owns a strong reference, even before lock.acquire.
        assert records[0]() is first()
        with locks.resident_lock("lifetime-waiting"):
            assert locks._LOCKS["resident:lifetime-waiting"] is first()
            assert any(item["scope_id"] == "resident:lifetime-waiting" for item in locks.health()["active_holds"])
            proceed.set()
            assert not entered.wait(0.03)
        assert entered.wait(2)
    finally:
        proceed.set()
        worker.join(3)
    assert not worker.is_alive()
    assert not failures
    gc.collect()
    assert first() is None


def test_exception_and_deferred_reentry_release_the_record(monkeypatch, tmp_path):
    monkeypatch.setattr(locks, "_ROOT_PROVIDER", lambda: tmp_path)
    calls = []
    with pytest.raises(ValueError):
        with locks.resident_lock("lifetime-failure"):
            failed = weakref.ref(locks._LOCKS["resident:lifetime-failure"])
            raise ValueError("isolated failure")
    def callback():
        assert not locks.lock_held()
        with locks.resident_lock("lifetime-callback"):
            assert locks._LOCKS["resident:lifetime-callback"] is record()
            calls.append("reentered")
    with locks.resident_lock("lifetime-callback"):
        record = weakref.ref(locks._LOCKS["resident:lifetime-callback"])
        locks.defer_until_state_unlock(callback)
    gc.collect()
    assert calls == ["reentered"]
    assert failed() is None and record() is None
