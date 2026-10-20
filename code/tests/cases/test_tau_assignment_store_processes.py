"""Concurrent Test runs keep every Resident they record in the TAU mapping.

Every Test run executor is its own process and each change rewrites the
whole module -> Resident mapping file.  Only a thread lock guarded it, so in
a full TAU run a second run's write could replace the file with a copy read
before the first run recorded a newly created executor; that module then
reported its account configuration as never ready and the Resident it had
created was orphaned.  Two store instances stand in for two processes here.

:module: operations.test-workbench
"""
from __future__ import annotations

import threading

from management_portal.test_framework.tau_residents import TauResidentAssignments


def test_a_second_process_waits_instead_of_discarding_a_recorded_resident(tmp_path):
    """并发 Test Run 不会丢失已记录的 TAU 执行器"""
    path = tmp_path / "tau-residents.json"
    first, second = TauResidentAssignments(path), TauResidentAssignments(path)
    first.assignment("module.one")
    first.assignment("module.two")
    read_done, release = threading.Event(), threading.Event()
    original_read = first._read

    def paused_read():
        payload = original_read()
        if threading.current_thread().name == "first-run":
            read_done.set()
            assert release.wait(5)
        return payload

    first._read = paused_read
    recording = threading.Thread(
        target=first.record_identity, args=("module.one",),
        kwargs={"resident_id": "resident_aaaaaaaaaaaa", "lineage_id": "lineage_aaaaaaaaaaaa"},
        name="first-run",
    )
    recording.start()
    assert read_done.wait(5)
    other = threading.Thread(
        target=second.record_identity, args=("module.two",),
        kwargs={"resident_id": "resident_bbbbbbbbbbbb", "lineage_id": "lineage_bbbbbbbbbbbb"},
    )
    other.start()
    other.join(0.5)
    assert other.is_alive(), "the second process wrote while the first was mid-update"
    release.set()
    recording.join(5)
    other.join(5)
    reopened = TauResidentAssignments(path)
    assert reopened.assignment("module.one")["resident_id"] == "resident_aaaaaaaaaaaa"
    assert reopened.assignment("module.two")["resident_id"] == "resident_bbbbbbbbbbbb"


def test_nested_use_within_one_process_does_not_wait_for_itself(tmp_path):
    """TAU 映射在同一进程内嵌套使用不会自锁"""
    store = TauResidentAssignments(tmp_path / "tau-residents.json")
    with store._locked():
        assert store.assignment("module.one")["module_id"] == "module.one"
        store.record_identity("module.one", resident_id="resident_cccccccccccc", lineage_id="lineage_cccccccccccc")
    assert store.assignment("module.one")["resident_id"] == "resident_cccccccccccc"
