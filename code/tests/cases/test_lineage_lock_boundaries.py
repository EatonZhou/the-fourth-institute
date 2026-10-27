from __future__ import annotations

import threading
import time

import pytest

from management_portal.society_service import (
    invariant_locks,
    lineage_domain,
    resident_lineage,
    resident_execution_work,
    resident_protocol,
    resident_record_domain,
)
from management_portal.society_service import lineage_graph_state, lineage_program_event_dispatch


from management_portal.society_service import resident_lifecycle_domain

def test_effects_wait_for_all_state_scopes_and_discard_failed_transitions_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """状态副作用在全部锁释放后执行并丢弃失败回调"""
    with subtests.test(contract='test_effects_wait_for_all_state_scopes_and_discard_failed_transitions'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
            calls = []
            def committed():
                assert not invariant_locks.lock_held()
                calls.append("committed")
            with invariant_locks.lineage_scope("lineage_111111111111"):
                with invariant_locks.resident_lock("resident_111111111111"):
                    invariant_locks.defer_until_state_unlock(committed)
                with pytest.raises(RuntimeError):
                    with invariant_locks.resident_lock("resident_222222222222"):
                        invariant_locks.defer_until_state_unlock(lambda: calls.append("rolled back"))
                        raise RuntimeError("rollback")
                assert calls == []
            assert calls == ["committed"]
            with pytest.raises(RuntimeError):
                with invariant_locks.lineage_scope("lineage_111111111111"):
                    with invariant_locks.resident_lock("resident_111111111111"):
                        invariant_locks.defer_until_state_unlock(lambda: calls.append("outer rollback"))
                    raise RuntimeError("rollback outer")
            assert calls == ["committed"]
    with subtests.test(contract='test_reentered_scope_discards_only_its_failed_callbacks'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
            calls = []

            def record(label):
                assert not invariant_locks.lock_held()
                calls.append(label)

            with invariant_locks.lineage_scope("lineage_111111111111"):
                invariant_locks.defer_until_state_unlock(lambda: record("outer"))
                with invariant_locks.resident_lock("resident_111111111111"):
                    invariant_locks.defer_until_state_unlock(lambda: record("resident"))
                    with pytest.raises(RuntimeError, match="reentry rollback"):
                        with invariant_locks.lineage_scope("lineage_111111111111"):
                            invariant_locks.defer_until_state_unlock(lambda: record("rolled back"))
                            raise RuntimeError("reentry rollback")
                    with invariant_locks.lineage_scope("lineage_111111111111"):
                        invariant_locks.defer_until_state_unlock(lambda: record("reentered"))
                    assert calls == []
                assert calls == []
            assert calls == ["outer", "resident", "reentered"]
    with subtests.test(contract='test_request_collector_never_observes_rolled_back_state_changes'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            from management_portal.society_service import changes

            monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
            with changes.collect_changes() as collected:
                with pytest.raises(RuntimeError, match="rollback"):
                    with invariant_locks.resident_lock("resident_111111111111"):
                        changes.resident_changed("resident_111111111111")
                        assert not collected
                        raise RuntimeError("rollback")
                assert not collected
                with invariant_locks.resident_lock("resident_111111111111"):
                    changes.resident_changed("resident_111111111111")
                    assert not collected
                assert len(collected) == 1
    with subtests.test(contract='test_different_lineage_scopes_do_not_block_each_other'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
            first_entered = threading.Event()
            release_first = threading.Event()
            second_entered = threading.Event()

            def hold_first() -> None:
                with lineage_domain._lineage_scope("lineage_111111111111"):
                    first_entered.set()
                    release_first.wait(timeout=2)

            def enter_second() -> None:
                first_entered.wait(timeout=2)
                with lineage_domain._lineage_scope("lineage_222222222222"):
                    second_entered.set()

            first = threading.Thread(target=hold_first)
            second = threading.Thread(target=enter_second)
            first.start()
            second.start()
            try:
                assert second_entered.wait(timeout=1)
            finally:
                release_first.set()
                first.join(timeout=2)
                second.join(timeout=2)
    with subtests.test(contract='test_same_lineage_scopes_do_not_block_each_other'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
            first_entered = threading.Event()
            release_first = threading.Event()
            second_entered = threading.Event()

            def hold_first() -> None:
                with lineage_domain._lineage_scope("lineage_111111111111"):
                    first_entered.set()
                    release_first.wait(timeout=2)

            def enter_second() -> None:
                first_entered.wait(timeout=2)
                with lineage_domain._lineage_scope("lineage_111111111111"):
                    second_entered.set()

            first = threading.Thread(target=hold_first)
            second = threading.Thread(target=enter_second)
            first.start()
            second.start()
            try:
                # Writes on one Lineage merge at commit; the scope takes no lock.
                assert second_entered.wait(timeout=1)
            finally:
                release_first.set()
                first.join(timeout=2)
                second.join(timeout=2)
    with subtests.test(contract='test_resident_state_does_not_block_its_lineage_graph'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
            monkeypatch.setattr(
                resident_lifecycle_domain,
                "resident_aggregate_lineage_id",
                lambda _resident_id: "lineage_111111111111",
            )
            resident_entered = threading.Event()
            release_resident = threading.Event()
            lineage_entered = threading.Event()

            def hold_resident() -> None:
                with resident_record_domain.state_lock("resident_111111111111"):
                    resident_entered.set()
                    release_resident.wait(timeout=2)

            def enter_lineage() -> None:
                resident_entered.wait(timeout=2)
                with lineage_domain._lineage_scope("lineage_111111111111"):
                    lineage_entered.set()

            resident_thread = threading.Thread(target=hold_resident)
            lineage_thread = threading.Thread(target=enter_lineage)
            resident_thread.start()
            lineage_thread.start()
            try:
                assert lineage_entered.wait(timeout=1)
            finally:
                release_resident.set()
                resident_thread.join(timeout=2)
                lineage_thread.join(timeout=2)


def test_resident_states_run_concurrently(tmp_path, monkeypatch, subtests, isolated_portal_case):
    """不同 Resident 的状态锁可并发进入"""
    for same_lineage in [True, False]:
        with subtests.test(same_lineage=same_lineage), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                tmp_path = case_path
                monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
                lineage_by_resident = {
                    "resident_111111111111": "lineage_111111111111",
                    "resident_222222222222": "lineage_111111111111" if same_lineage else "lineage_222222222222",
                }
                monkeypatch.setattr(
                    resident_lifecycle_domain,
                    "resident_aggregate_lineage_id",
                    lineage_by_resident.__getitem__,
                )
                first_entered = threading.Event()
                release_first = threading.Event()
                second_entered = threading.Event()

                def hold_first() -> None:
                    with resident_record_domain.state_lock("resident_111111111111"):
                        first_entered.set()
                        release_first.wait(timeout=2)

                def enter_second() -> None:
                    first_entered.wait(timeout=2)
                    with resident_record_domain.state_lock("resident_222222222222"):
                        second_entered.set()

                first = threading.Thread(target=hold_first)
                second = threading.Thread(target=enter_second)
                first.start()
                second.start()
                try:
                    assert second_entered.wait(timeout=1)
                finally:
                    release_first.set()
                    first.join(timeout=2)
                    second.join(timeout=2)


def test_resident_mutation_lock_does_not_hold_an_identity_snapshot_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """Resident 修改锁不保留身份快照且查询不等待聚合锁"""
    with subtests.test(contract='test_resident_mutation_lock_does_not_hold_an_identity_snapshot'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
            monkeypatch.setattr(
                resident_lifecycle_domain,
                "resident_aggregate_lineage_id",
                lambda _resident_id: "lineage_111111111111",
            )

            def forbidden_snapshot_scope():
                raise AssertionError("mutation lock must not retain a read snapshot")

            monkeypatch.setattr(
                resident_lineage,
                "access_snapshot_scope",
                forbidden_snapshot_scope,
            )

            with resident_record_domain.state_lock("resident_111111111111"):
                pass
    with subtests.test(contract='test_state_lock_requires_explicit_lineage_identity'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)

            with pytest.raises(ValueError, match="lineage_id is required"):
                with lineage_domain._lineage_scope(""):
                    pass

            assert not list((tmp_path / ".locks").glob("*"))
    with subtests.test(contract='test_independent_catalog_mutations_do_not_block_each_other'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
            first_entered = threading.Event()
            release_first = threading.Event()
            second_entered = threading.Event()

            def hold_function_blocks() -> None:
                with lineage_domain._catalog_mutation_lock("function-blocks"):
                    first_entered.set()
                    release_first.wait(timeout=2)

            def enter_role_templates() -> None:
                first_entered.wait(timeout=2)
                with lineage_domain._catalog_mutation_lock("role-templates"):
                    second_entered.set()

            first = threading.Thread(target=hold_function_blocks)
            second = threading.Thread(target=enter_role_templates)
            first.start()
            second.start()
            try:
                assert second_entered.wait(timeout=1)
            finally:
                release_first.set()
                first.join(timeout=2)
                second.join(timeout=2)
    with subtests.test(contract='test_lineage_query_does_not_wait_for_aggregate_mutation_lock'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
            monkeypatch.setattr(
                lineage_domain,
                "_read_lineage",
                lambda lineage_id, *, actor=None: {"lineage_id": lineage_id},
            )
            monkeypatch.setattr(lineage_domain, "_ensure_visible", lambda *_args: None)
            monkeypatch.setattr(
                lineage_graph_state,
                "read_state",
                lambda lineage_id: {"lineage_id": lineage_id, "cells": {}},
            )
            lock_entered = threading.Event()
            release_lock = threading.Event()
            query_completed = threading.Event()

            def hold_mutation_lock() -> None:
                with lineage_domain._lineage_scope("lineage_111111111111"):
                    lock_entered.set()
                    release_lock.wait(timeout=2)

            def query() -> None:
                lock_entered.wait(timeout=2)
                lineage_domain.get_lineage_graph_state(
                    "lineage_111111111111",
                    {},
                    "sample_admin",
                )
                query_completed.set()

            first = threading.Thread(target=hold_mutation_lock)
            second = threading.Thread(target=query)
            first.start()
            second.start()
            try:
                assert query_completed.wait(timeout=1)
            finally:
                release_lock.set()
                first.join(timeout=2)
                second.join(timeout=2)
    with subtests.test(contract='test_state_lock_health_reports_slow_holds'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
            monkeypatch.setattr(invariant_locks, "_SLOW_SECONDS", 0.01)
            before = lineage_domain.state_lock_health()["slow_hold_count"]

            with invariant_locks.resident_lock(
                "resident_333333333333",
                operation="test.slow_hold",
            ):
                time.sleep(0.02)
                hold = next(
                    item for item in lineage_domain.state_lock_health()["active_holds"]
                    if item["scope_id"] == "resident:resident_333333333333"
                )
                assert "test_resident_mutation_lock_does_not_hold_an_identity_snapshot_contracts" in hold["owner_stack"]
                assert hold["held_seconds"] >= 0.01

            health = lineage_domain.state_lock_health()
            assert health["slow_hold_count"] >= before + 1
            assert health["recent_slow_events"][-1]["operation"] == "test.slow_hold"
    with subtests.test(contract='test_resident_continuation_lock_is_process_exclusive'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)

            with invariant_locks.resident_continuation_lock(
                "resident_aaaaaaaaaaaa"
            ) as first:
                with invariant_locks.resident_continuation_lock(
                    "resident_aaaaaaaaaaaa"
                ) as second:
                    assert first is True
                    assert second is False


def test_resident_query_does_not_wait_for_aggregate_mutation_lock(
    tmp_path,
    monkeypatch,
) -> None:
    """Resident 状态查询不等待聚合修改锁"""
    monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        resident_record_domain,
        "resolve_resident_runtime_target",
        lambda resident_id, _actor=None, **_kwargs: type(
            "Target",
            (),
            {"resident_id": resident_id, "record": {"backend": "codex"}},
        )(),
    )

    monkeypatch.setattr(resident_protocol.resident_access, "can_manage_resident", lambda *_args: True)

    class Repository:
        @staticmethod
        def state_snapshots(resident_ids):
            return {
                resident_id: {
                    "state": {"work_state": "idle"},
                    "cursor": 1,
                }
                for resident_id in resident_ids
            }

    monkeypatch.setattr(
        resident_protocol.resident_conversation_stream,
        "get_repository",
        Repository,
    )
    lock_entered = threading.Event()
    release_lock = threading.Event()
    query_completed = threading.Event()

    def hold_mutation_lock() -> None:
        with lineage_domain._lineage_scope("lineage_111111111111"):
            lock_entered.set()
            release_lock.wait(timeout=2)

    def query() -> None:
        lock_entered.wait(timeout=2)
        resident_protocol.get_resident_state(
            "resident_111111111111",
            "sample_admin",
        )
        query_completed.set()

    first = threading.Thread(target=hold_mutation_lock)
    second = threading.Thread(target=query)
    first.start()
    second.start()
    try:
        assert query_completed.wait(timeout=1)
    finally:
        release_lock.set()
        first.join(timeout=2)
        second.join(timeout=2)


def test_program_event_dispatch_does_not_wrap_delivery_in_state_lock(
    monkeypatch,
) -> None:
    """事件投递在状态锁之外执行"""
    from management_portal.portal_access_control import get_portal_access_store

    get_portal_access_store().upsert_user("sample_admin", actor="portal", status="active")
    observed: list[bool] = []

    def dispatch(*_args, **_kwargs):
        observed.append(lineage_domain.invariant_locks.lock_held())
        return {
            "event": {
                "id": "event_111111111111",
                "type": "timer.fired",
                "payload": {},
            },
            "matched_count": 0,
            "results": [],
        }

    monkeypatch.setattr(
        lineage_program_event_dispatch,
        "_dispatch_lineage_program_event_locked",
        dispatch,
    )

    monkeypatch.setattr(lineage_domain, "_read_lineage_state", lambda lineage_id: {
        "lineage_id": lineage_id, "revision": 1, "nodes": [], "edges": [],
        "program": {"components": [], "wires": []},
    })
    lineage_domain.dispatch_lineage_program_event(
        "lineage_111111111111",
        "event_source_111111111111",
        {
            "id": "event_111111111111",
            "type": "timer.fired",
            "payload": {},
        },
        "sample_admin",
        cursor="timer:1",
    )

    assert observed == [False]


def test_resident_recovery_does_not_prepare_a_peer_owned_waiter(tmp_path, monkeypatch):
    """Resident 恢复跳过其他进程持有的续行工作

    :design: decision_1b2bdf5c33d6
    :design-point:
    """
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    record = {"resident_id": "resident_aaaaaaaaaaaa"}
    other = {"resident_id": "resident_bbbbbbbbbbbb"}
    job = {'execution_id': "job-peer-owner", "outcome": ""}
    # Distinct file descriptors reproduce the ownership held by another process;
    # no thread is registered in this process's continuation registry.
    with invariant_locks.resident_continuation_lock(record["resident_id"]) as acquired:
        assert acquired
        assert not resident_execution_work.continuation_needed(record, job)
        assert resident_execution_work.continuation_needed(other, job)
    assert resident_execution_work.continuation_needed(record, job)


def test_execution_work_and_conversation_writer_scopes_are_independent(
    tmp_path,
    monkeypatch,
) -> None:
    """执行工作锁与会话写入锁相互独立"""
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    resident_id = "resident_aaaaaaaaaaaa"
    job_entered = threading.Event()
    release_job = threading.Event()
    conversation_entered = threading.Event()

    def hold_job() -> None:
        with invariant_locks.resident_lock(resident_id):
            job_entered.set()
            release_job.wait(timeout=2)

    def enter_conversation() -> None:
        job_entered.wait(timeout=2)
        with invariant_locks.resident_conversation_lock(resident_id):
            conversation_entered.set()

    job_thread = threading.Thread(target=hold_job)
    conversation_thread = threading.Thread(target=enter_conversation)
    job_thread.start()
    conversation_thread.start()
    try:
        assert conversation_entered.wait(timeout=1)
    finally:
        release_job.set()
        job_thread.join(timeout=2)
        conversation_thread.join(timeout=2)


def test_execution_work_and_conversation_scopes_do_not_form_a_lock_convoy(
    tmp_path,
    monkeypatch,
) -> None:
    """执行与会话作用域可并发进入"""
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    resident_id = "resident_aaaaaaaaaaaa"
    job_entered = threading.Event()
    release_job = threading.Event()
    conversation_entered = threading.Event()

    def hold_job_scope() -> None:
        with invariant_locks.resident_lock(resident_id):
            job_entered.set()
            release_job.wait(timeout=2)

    def enter_conversation_scope() -> None:
        job_entered.wait(timeout=2)
        with invariant_locks.resident_conversation_lock(resident_id):
            conversation_entered.set()

    job_thread = threading.Thread(target=hold_job_scope)
    conversation_thread = threading.Thread(target=enter_conversation_scope)
    job_thread.start()
    conversation_thread.start()
    try:
        assert conversation_entered.wait(timeout=1)
    finally:
        release_job.set()
        job_thread.join(timeout=2)
        conversation_thread.join(timeout=2)


def test_slow_hold_diagnostics_and_all_deferred_effects_run_after_unlock(
    tmp_path,
    monkeypatch,
) -> None:
    """慢锁诊断与后续副作用在解锁后全部执行"""
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    monkeypatch.setattr(invariant_locks, "_SLOW_SECONDS", 0.001)
    diagnostic_lock_states: list[bool] = []
    effects: list[str] = []

    monkeypatch.setattr(
        invariant_locks,
        "_record_slow_event",
        lambda _event: diagnostic_lock_states.append(invariant_locks.lock_held()),
    )

    def fail_first() -> None:
        assert not invariant_locks.lock_held()
        effects.append("first")
        raise RuntimeError("first deferred effect failed")

    def finish_second() -> None:
        assert not invariant_locks.lock_held()
        effects.append("second")

    with pytest.raises(RuntimeError, match="first deferred effect failed"):
        with invariant_locks.resident_lock("resident_bbbbbbbbbbbb"):
            invariant_locks.defer_until_state_unlock(fail_first)
            invariant_locks.defer_until_state_unlock(finish_second)
            time.sleep(0.005)

    assert effects == ["first", "second"]
    assert diagnostic_lock_states == [False]
