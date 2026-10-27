""":importance: P2"""
from __future__ import annotations
from tests.cases.agent_data_test_support import register_session
from management_portal.society_service import resident_backend
from management_portal.society_service.domain_ports import execution_port

import threading
import multiprocessing
import os
from contextlib import ExitStack

import pytest

from management_portal.society_service import invariant_locks
from management_portal.society_service import resident_domain
from management_portal.society_service import execution_client
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.session_manager import SessionManager
from management_portal.society_service.resident_lifecycle import (
    LifecycleAttemptFailed, LifecycleStepPending, ResidentLifecycleOperations,
)
from management_portal.society_service.state_store import LineageDataStore


from management_portal.society_service import (
    resident_lifecycle_domain,
    resident_lifecycle_steps_domain,
    resident_record_domain,
    resident_session_backend_domain,
)

@pytest.fixture
def operations(tmp_path, monkeypatch):
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    return ResidentLifecycleOperations(LineageDataStore())


def test_lifecycle_pending_execution_preserves_step_and_does_not_report_failure(operations):
    """生命周期步骤处于 pending 时保留该步骤稍后重跑，不记为失败"""
    operation_id = operations.enqueue("resident_111111111111", [{"kind": "session_restart"}])
    calls = []
    def pending(effect_id, step):
        calls.append(effect_id)
        raise LifecycleStepPending()
    assert not operations.run(operation_id, pending)
    assert operations.pending("resident_111111111111")
    assert operations.health()["last_error"] == ""
    operations.store.database.write(lambda connection: connection.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (operation_id,),
    ))
    assert operations.run(operation_id, lambda effect_id, step: calls.append(effect_id)) == "resident_111111111111"
    assert calls == [operation_id + ":0"] * 2


def test_lifecycle_intent_rolls_back_with_identity_commit(operations):
    """身份事务回滚时同一事务中登记的生命周期操作一并撤销"""
    def fail():
        operations.enqueue("resident_111111111111", [{"kind": "archive"}])
        raise RuntimeError("identity rollback")
    with pytest.raises(RuntimeError, match="identity rollback"):
        operations.store.transaction(fail)
    assert operations.ready() == []


def test_lifecycle_recovery_resumes_only_unfinished_steps(operations):
    """生命周期操作中途失败后恢复只重跑未完成的步骤，完成后不再重复执行"""
    operation_id = operations.enqueue("resident_111111111111", [{"kind": "a"}, {"kind": "b"}])
    calls = []
    def fail_second(key, step):
        calls.append(step["kind"])
        if step["kind"] == "b":
            raise RuntimeError("offline")
    assert not operations.run(operation_id, fail_second)
    assert operations.pending("resident_111111111111")
    assert "offline" in operations.health()["last_error"]
    operations.store.database.write(lambda c: c.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (operation_id,),
    ))
    recovered = ResidentLifecycleOperations(LineageDataStore())
    assert recovered.run(operation_id, lambda key, step: calls.append(step["kind"]))
    assert calls == ["a", "b", "b"]
    assert not recovered.pending("resident_111111111111")
    assert not recovered.run(operation_id, lambda *_: pytest.fail("duplicate applied"))


def test_slow_lifecycle_effect_does_not_block_commands_or_other_residents(operations):
    """缓慢的生命周期步骤在锁外执行，不阻塞该 Resident 的命令和其他 Resident 的操作"""
    first = operations.enqueue("resident_111111111111", [{"kind": "a"}])
    second = operations.enqueue("resident_222222222222", [{"kind": "b"}])
    entered, release = threading.Event(), threading.Event()
    def slow(*_):
        assert not invariant_locks.lock_held()
        entered.set()
        assert release.wait(3)
    worker = threading.Thread(target=lambda: operations.run(first, slow))
    worker.start()
    try:
        assert entered.wait(1)
        with invariant_locks.resident_lock("resident_111111111111"):
            pass
        assert operations.run(second, lambda *_: None)
        assert not operations.run(first, lambda *_: pytest.fail("duplicate owner"))
    finally:
        release.set()
        worker.join(3)
    assert operations.ready() == []


def test_lifecycle_effect_refuses_state_lock_scope(operations):
    """持有 Resident 状态锁时运行生命周期操作会被拒绝"""
    operation_id = operations.enqueue("resident_111111111111", [])
    with invariant_locks.resident_lock("resident_111111111111"):
        with pytest.raises(RuntimeError, match="outside state scopes"):
            operations.run(operation_id, lambda *_: None)


def test_preparation_survives_process_owner_exit_without_publishing(operations):
    """准备阶段的进程异常退出后，未发布的准备记录可被恢复并执行补偿步骤"""
    class ProcessExit(BaseException):
        pass
    with pytest.raises(ProcessExit):
        with operations.prepare("resident_111111111111", lambda _: [{"kind": "registration_abort"}]) as key:
            assert operations.pending("resident_111111111111")
            assert operations.ready() == []
            assert not operations.run(key, lambda *_: pytest.fail("Preparation still owned"))
            raise ProcessExit()
    recovered = ResidentLifecycleOperations(LineageDataStore())
    applied = []
    assert recovered.ready() == [key]
    assert recovered.run(key, lambda _, step: applied.append(step["kind"]))
    assert applied == ["registration_abort"]


def test_preparation_commit_rolls_back_with_identity_transaction(operations):
    """身份事务失败时准备记录的提交随之回滚，事务成功后准备记录才被清除"""
    with operations.prepare("resident_111111111111", lambda _: [{"kind": "registration_abort"}]) as key:
        def abort():
            operations.commit_preparation(key)
            raise RuntimeError("identity transaction failed")
        with pytest.raises(RuntimeError, match="identity transaction failed"):
            operations.store.transaction(abort)
        assert operations.pending("resident_111111111111")
        operations.store.transaction(lambda: operations.commit_preparation(key))
        assert not operations.pending("resident_111111111111")
    assert operations.ready() == []


def test_owned_preparation_prefix_does_not_starve_lifecycle_recovery(operations):
    """大量仍被持有的准备记录不阻挡其他就绪生命周期操作的恢复"""
    with ExitStack() as stack:
        for index in range(40):
            stack.enter_context(operations.prepare(
                f"resident_{index:012x}", lambda _: [{"kind": "registration_abort"}],
            ))
        operations.store.database.write(lambda c: c.execute(
            "UPDATE resident_lifecycle_operations SET available_at=to_timestamp(-1)",
        ))
        ready = operations.enqueue("resident_999999999999", [{"kind": "archive"}])
        assert operations.ready() == [ready]
        assert operations.run(ready, lambda *_: None) == "resident_999999999999"


def test_preparation_is_recovered_after_real_process_exit(operations):
    """进程在准备阶段被直接终止后，新进程能恢复并执行该准备记录"""
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)

    def crash():
        with operations.prepare("resident_111111111111", lambda _: [{"kind": "registration_abort"}]) as key:
            send.send(key)
            os._exit(17)  # Deliberately bypass finally/context-manager cleanup.

    process = context.Process(target=crash)
    process.start()
    send.close()
    try:
        assert receive.poll(3), "Child did not persist preparation"
        key = receive.recv()
        process.join(3)
        assert process.exitcode == 17
        recovered = ResidentLifecycleOperations(LineageDataStore())
        assert recovered.ready() == [key]
        assert recovered.run(key, lambda *_: None) == "resident_111111111111"
    finally:
        receive.close()
        if process.is_alive():
            process.terminate()
            process.join(3)


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("backend", ["codex", "kimi"])
def test_session_creation_preparation_recovers_exact_request_before_archiving(
    operations, tmp_path, monkeypatch, accepted, backend,
):
    """codex 与 kimi 会话创建的回复丢失后，恢复时沿用同一请求，确认终态后才释放绑定并归档文件夹"""
    source, target = tmp_path / "new-resident", tmp_path / "aborted-resident"
    source.mkdir()
    (source / "keep.txt").write_text("recoverable", encoding="utf-8")
    record = {"resident_id": "resident_111111111111", "backend": backend}
    submitted, requests, events = [], {}, []
    outcome = {"status": "running", "result": {}}

    class Service:
        def resume(self, execution_id, **kwargs):
            if execution_id not in requests:
                raise KeyError(execution_id)
            return outcome
        def submit(self, request):
            assert not invariant_locks.lock_held()
            assert operations.pending(record["resident_id"])
            submitted.append(request.to_payload())
            if accepted or len(submitted) > 1:
                requests[request.execution_id] = request
            if len(submitted) == 1:
                raise ConnectionError("acceptance reply lost")
            return outcome
        def wait(self, execution_id, **kwargs):
            assert execution_id in requests
            return outcome
        def acknowledge(self, execution_id, **kwargs):
            assert not source.exists() and (target / "keep.txt").is_file()
            events.append("ack")

    monkeypatch.setattr(resident_record_domain, "lifecycle_operations", lambda: operations)
    monkeypatch.setattr(resident_record_domain, "find_resident_canonical", lambda _: None)
    monkeypatch.setattr(execution_port, "service", lambda: Service())
    monkeypatch.setattr(resident_backend, "session_record", lambda *args, **kwargs: {})
    monkeypatch.setattr(resident_backend, "delete_session_identity", lambda *args, **kwargs: events.append("release"))
    with pytest.raises(ConnectionError, match="acceptance reply lost"):
        with operations.prepare(record["resident_id"], lambda _: [{
            "kind": "registration_abort", "record": record, "actor": "CASE-A",
            "source": str(source), "target": str(target),
        }]) as key:
            resident_session_backend_domain.run_backend_session_operation(
                backend=backend, operation="create", actor="CASE-A", cwd=tmp_path,
                binding_id="resident:resident_111111111111",
                preparation_id=key,
            )
    operations = ResidentLifecycleOperations(LineageDataStore())
    # Missing or mismatched terminal facts cannot release bindings or files.
    assert not operations.run(key, resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    assert source.exists() and events == []
    outcome.update(status="completed", result={"status": "completed"})
    operations.store.database.write(lambda c: c.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (key,),
    ))
    assert operations.run(key, resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == record["resident_id"]
    assert events == ["release", "ack"]
    assert len(submitted) == (1 if accepted else 2)
    assert all(payload == submitted[0] for payload in submitted)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "recoverable"


def test_published_preparation_only_acknowledges_execution(operations):
    """已发布的准备记录恢复时只确认已登记的执行，登记后的执行请求不能更改"""
    with operations.prepare("resident_111111111111", lambda _: [{"kind": "registration_abort"}]) as key:
        request = {"execution_id": "execution-test", "actor": "CASE-A"}
        operations.stage_preparation_execution(key, request)
        with pytest.raises(ValueError, match="cannot change"):
            operations.stage_preparation_execution(key, {**request, "actor": "sample_admin"})
        operations.store.transaction(lambda: operations.commit_preparation(key))
    calls = []
    operations.run(key, lambda _, step: calls.append(step))
    assert calls == [{"kind": "execution_ack", "execution_id": "execution-test", "actor": "CASE-A"}]


def test_stale_preparation_never_compensates_published_resident_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """过期的准备记录不回滚已发布的 Resident，也不释放已被替换的会话"""
    with subtests.test(contract='test_stale_preparation_never_compensates_published_resident'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            source = tmp_path / 'published-resident'
            source.mkdir()
            monkeypatch.setattr(resident_record_domain, 'find_resident_canonical', lambda _: {'registration_state': 'ready'})
            monkeypatch.setattr(execution_port, 'service', lambda: pytest.fail('Published identity must not be touched'))
            monkeypatch.setattr(resident_backend, 'session_record', lambda _: pytest.fail('Published binding must not be released'))
            resident_lifecycle_steps_domain.apply_resident_lifecycle_step('stale:0', {'kind': 'registration_abort', 'record': {'resident_id': 'resident_111111111111'}, 'actor': 'CASE-A', 'source': str(source), 'target': str(tmp_path / 'archive')})
            assert source.is_dir()
    with subtests.test(contract='test_cleanup_never_releases_a_replaced_session'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = SessionManager(agent_data=AgentDataStore())
            monkeypatch.setattr(execution_client, 'sessions', lambda: manager)
            record = {'resident_id': 'resident_111111111111', 'backend': 'codex'}
            register_session(record, 'new-session')

            def forbidden(*args, **kwargs):
                pytest.fail('Stale cleanup must not release or delete a newer session')
            monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', forbidden)
            monkeypatch.setattr(resident_backend, 'delete_session_identity', forbidden)
            with pytest.raises(RuntimeError, match='binding changed'):
                resident_lifecycle_steps_domain.apply_resident_lifecycle_step('op:0', {'kind': 'release', 'record': record, 'actor': 'CASE-A', 'binding': {'provider_session_ref': 'old', 'backend_session_id': 'old-session'}})


def test_acknowledgement_replay_accepts_already_pruned_receipt(monkeypatch):
    """重放执行确认时，执行回执已被清理也视为成功"""
    class Service:
        def acknowledge(self, execution_id, **kwargs):
            raise KeyError(execution_id)
    monkeypatch.setattr(execution_port, "service", lambda: Service())
    resident_lifecycle_steps_domain.apply_resident_lifecycle_step("op:1", {
        "kind": "execution_ack", "execution_id": "execution-old", "actor": "CASE-A",
    })




def test_release_snapshot_keeps_owned_workspace_after_identity_deletion(tmp_path, monkeypatch):
    """会话释放步骤保存自有 Workspace 路径快照，Resident 身份删除后仍在该目录释放"""
    from pathlib import Path
    from tests.cases.test_lineage_catalogs import _record
    from management_portal.society_service.resident_runtime_domain import (
        resident_working_directory as _resident_working_directory,
    )
    record = _record('resident_111111111111')
    path = Path(record['default_cwd'])
    path.mkdir(parents=True, exist_ok=True)
    binding = {'provider_session_ref': 'session-test', 'backend_session_id': 'native-test'}
    monkeypatch.setattr(resident_backend, 'session_record', lambda *a, **k: binding)
    step = resident_lifecycle_domain.resident_release_step(record, 'CASE-A')
    record['deleted_at'] = '2026-10-10T00:00:00Z'
    record.pop('owned_workspace')
    assert _resident_working_directory(step['record']) == path
    calls = []
    monkeypatch.setattr(resident_session_backend_domain, 'run_backend_session_operation', lambda **kw: calls.append(kw))
    monkeypatch.setattr(resident_backend, 'delete_session_identity', lambda *a, **kw: None)
    resident_lifecycle_steps_domain.apply_resident_lifecycle_step('op:0', step)
    assert calls[0]['operation'] == 'release' and calls[0]['cwd'] == path
    assert 'workspace_bindings' not in step['record']


def test_recovered_release_resumes_the_same_execution_request(tmp_path, monkeypatch):
    """恢复的会话释放操作续用同一执行请求，不重复提交"""
    requests = []
    class Service:
        def resume(self, execution_id, **kwargs):
            if not requests:
                raise KeyError(execution_id)
            assert execution_id == requests[0].execution_id
            return {"status": "completed"}
        def submit(self, request):
            assert not invariant_locks.lock_held()
            requests.append(request)
            return {"status": "queued"}
        def wait(self, execution_id, **kwargs):
            return {"status": "completed", "result": {"status": "completed"}}
        def acknowledge(self, *args, **kwargs):
            pass
    monkeypatch.setattr(execution_port, "service", lambda: Service())
    for _ in range(2):
        result = resident_session_backend_domain.run_backend_session_operation(
            backend="codex", operation="release", actor="CASE-A", cwd=tmp_path,
            binding_id="resident:resident_111111111111", backend_route_id="zero",
            operation_id="durable-release:0",
        )
        assert result["status"] == "completed"
    assert len(requests) == 1


def test_lifecycle_retry_advances_identity_only_after_confirmed_failure(operations):
    """生命周期步骤只在确认失败后换用新的重试键，回复丢失时沿用原键"""
    operation_id = operations.enqueue("resident_111111111111", [{"kind": "release"}])
    keys = []
    outcomes = iter([RuntimeError("response lost"), LifecycleAttemptFailed("failed"), None])

    def apply(key, step):
        keys.append(key)
        error = next(outcomes)
        if error:
            raise error

    for _ in range(2):
        assert not operations.run(operation_id, apply)
        assert operations.pending("resident_111111111111")
        operations.store.database.write(lambda c: c.execute(
            'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s',
            (operation_id,),
        ))
        # Recreate the owner each time to verify attempt allocation is durable.
        operations = ResidentLifecycleOperations(LineageDataStore())
    assert operations.run(operation_id, apply) == "resident_111111111111"
    assert keys == [f"{operation_id}:0", f"{operation_id}:0", f"{operation_id}:0:retry-1"]
    assert not operations.pending("resident_111111111111")


def test_release_retry_requires_matching_terminal_result(tmp_path, monkeypatch, subtests, isolated_portal_case):
    """会话释放的已有执行处于失败、取消或运行中时报错，不重新提交"""
    for status in ["failed", "cancelled", "running"]:
        with subtests.test(status=status), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                tmp_path = case_path
                from unittest.mock import Mock

                service = Mock()
                service.resume.return_value = {"status": status}
                service.wait.return_value = {
                    "status": status, "result": {"status": "failed" if status == "running" else status},
                }
                monkeypatch.setattr(execution_port, "service", lambda: service)
                error_type = (
                    resident_domain.ResidentExecutionFailed if status == "running"
                    else LifecycleAttemptFailed
                )
                with pytest.raises(error_type):
                    resident_session_backend_domain.run_backend_session_operation(
                        backend="codex", operation="release", actor="CASE-A", cwd=tmp_path,
                        binding_id="resident:resident_111111111111", backend_route_id="zero",
                        operation_id="durable-release:0",
                    )
                service.submit.assert_not_called()
