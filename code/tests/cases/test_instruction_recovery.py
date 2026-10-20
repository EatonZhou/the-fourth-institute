"""Owners settle recovery receipts without repeating their business handlers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import importlib
import uuid

import pytest

from management_common.portal_database import portal_database
from management_common.projection_receipts import project_committed
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import InstructionKernel
from management_portal.hatg.store import ExecutionStore


@pytest.fixture(params=[
    ('management_portal.web_isa', 'UpdateUser', {'user_id': 'alice', 'changes': {'display_name': 'Alice'}}),
    ('management_portal.resources.isa', 'RegisterResource', {'resource_type': 'repo'}),
    ('management_portal.execution.isa', 'UpdateProviderAccount', {
        'account_id': 'acct', 'max_concurrency': 2,
        'dispatch_interval_min_seconds': 1, 'dispatch_interval_max_seconds': 5,
    }),
])
def owner(request, monkeypatch):
    from management_portal.society_service import lineage_domain

    module, instruction, arguments = request.param
    binding = importlib.import_module(module).binding()
    binding.handlers = dict(binding.handlers)
    effects, projections = [], []
    receipt = {'projection': 'lineage.resource_identity', 'lineage_id': 'lineage_0123456789ab'}

    def fail():
        raise OSError('temporary ResourceService outage')

    def handler(inv):
        effects.append(inv.instruction_id)
        project_committed(receipt, fail)
        return {'lineage_id': receipt['lineage_id']}

    def recover(failures, actor):
        projections.append((failures, actor))

    monkeypatch.setattr(lineage_domain, 'retry_lineage_projections', recover)
    binding.handlers[instruction] = handler
    store = ExecutionStore(portal_database(schema='instruction_recovery_' + uuid.uuid4().hex[:8]))
    kernel = InstructionKernel(binding, store=store, authorize=lambda *_: None)
    return kernel, InstructionCall(instruction, arguments, idempotency_key='once'), effects, projections


def test_all_owners_recover_through_lineage_without_repeating_business(owner):
    """各 owner 的投影待完成指令重放时经 Lineage 补投影，不重复执行业务"""
    kernel, call, effects, projections = owner
    context = InstructionContext('alice', 'web')
    first = kernel.execute(call, context)
    assert first.error['code'] == 'instruction.projection_pending'
    assert first.error['retryable']
    replay = kernel.execute(call, context)
    assert replay.replayed and replay.status == 'committed', replay
    assert replay.instruction_id == first.instruction_id
    assert effects == [first.instruction_id]
    assert len(projections) == 1
    assert projections[0][1] == 'alice'
    assert kernel.store.get(first.instruction_id)['error'] is None


def test_maintenance_recovers_without_request_key_and_then_expires(owner):
    """没有幂等键的投影待完成指令由维护任务补投影，过保留期后被清理"""
    kernel, call, effects, projections = owner
    call = InstructionCall(call.instruction, call.arguments)
    first = kernel.execute(call, InstructionContext('alice', 'web'))
    maintained = kernel.maintain(now='2099-01-01T00:00:00Z')
    assert maintained['projections'] == 1
    assert kernel.store.get(first.instruction_id)['error'] is None
    assert effects == [first.instruction_id] and len(projections) == 1
    assert kernel.store.prune(now='2099-01-08T00:00:00Z') == 0
    assert kernel.store.prune(now='2099-01-08T00:00:01Z') == 1


def test_temporary_recovery_failure_keeps_receipt_and_retries(owner, monkeypatch):
    """补投影暂时失败时保留回执不变，之后的维护再重试成功"""
    kernel, call, effects, _ = owner
    original = kernel.binding.recover_projections

    def unavailable(*_):
        raise TimeoutError('owner not available')

    monkeypatch.setattr(kernel.binding, 'recover_projections', unavailable)
    first = kernel.execute(call, InstructionContext('alice', 'web'))
    pending = kernel.store.get(first.instruction_id)
    kernel.maintain()
    assert kernel.store.get(first.instruction_id) == pending
    monkeypatch.setattr(kernel.binding, 'recover_projections', original)
    kernel.maintain()  # completes the bounded cursor sweep
    kernel.maintain()
    assert kernel.store.get(first.instruction_id)['error'] is None
    assert effects == [first.instruction_id]


def test_unrecoverable_projection_is_failed_committed_and_can_expire(owner, monkeypatch):
    """无法恢复的投影记为已提交的失败，过期后同键调用报告幂等窗口已过且不重做业务"""
    kernel, call, effects, _ = owner

    def removed(*_):
        raise KeyError('lineage_0123456789ab')

    monkeypatch.setattr(kernel.binding, 'recover_projections', removed)
    first = kernel.execute(call, InstructionContext('alice', 'web'))
    kernel.maintain(now='2099-01-01T00:00:00Z')
    record = kernel.store.get(first.instruction_id)
    assert record['status'] == 'failed'
    assert record['error']['commit_state'] == 'committed'
    assert record['error']['details']['reason'] == 'projection_recovery_failed'
    replay = kernel.execute(call, InstructionContext('alice', 'web'))
    assert replay.replayed and replay.status == 'failed'
    assert effects == [first.instruction_id]
    assert kernel.store.prune(now='2099-01-08T00:00:01Z') == 1
    expired = kernel.execute(call, InstructionContext('alice', 'web'))
    assert expired.error['details']['reason'] == 'idempotency_window_expired'
    assert effects == [first.instruction_id]


def test_concurrent_retry_and_maintenance_project_once_without_transaction(owner, monkeypatch):
    """重试与维护并发时补投影只执行一次，且不在数据库事务内执行"""
    kernel, call, effects, _ = owner
    first = kernel.execute(call, InstructionContext('alice', 'web'))
    entered, release = Event(), Event()
    projected = []

    def recover(*_):
        # An external observer sees commits while projection recovery is paused.
        assert kernel.store.database._current_connection() is None
        projected.append(True)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(kernel.binding, 'recover_projections', recover)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(kernel.maintain)
        try:
            assert entered.wait(5)
            replay = kernel.execute(call, InstructionContext('alice', 'web'))
            assert replay.error['code'] == 'instruction.projection_pending'
        finally:
            release.set()
        future.result()
    assert projected == [True] and effects == [first.instruction_id]
    assert kernel.store.get(first.instruction_id)['error'] is None


def test_pending_projection_cursor_does_not_starve_later_receipts(owner, monkeypatch):
    """一个回执持续补投影失败时，维护游标仍会处理之后的回执"""
    from management_portal.hatg import kernel as kernel_module, store as store_module
    kernel, call, effects, _ = owner
    monkeypatch.setattr(store_module, 'PRUNE_BATCH_SIZE', 1)
    monkeypatch.setattr(kernel_module, 'PRUNE_BATCH_SIZE', 1)
    first = kernel.execute(call, InstructionContext('alice', 'web'))
    second = kernel.execute(InstructionCall(call.instruction, call.arguments, idempotency_key='second'),
                            InstructionContext('alice', 'web'))
    ids = sorted([first.instruction_id, second.instruction_id])

    def recover(failures, inv):
        if inv.instruction_id == ids[0]:
            raise TimeoutError('one target stays unavailable')

    monkeypatch.setattr(kernel.binding, 'recover_projections', recover)
    kernel.maintain()
    kernel.maintain()
    assert kernel.store.get(ids[0])['error']['code'] == 'instruction.projection_pending'
    assert kernel.store.get(ids[1])['error'] is None
    assert len(effects) == 2


def test_owner_worker_maintains_idle_store_and_stops(owner):
    """owner 的维护线程在空闲时也执行维护，并能正常停止"""
    from management_portal.hatg.maintenance import InstructionMaintenance
    kernel, call, effects, _ = owner
    first = kernel.execute(call, InstructionContext('alice', 'web'))
    finished = Event()
    maintain = kernel.maintain

    def observed():
        try:
            return maintain()
        finally:
            finished.set()

    kernel.maintain = observed
    worker = InstructionMaintenance(kernel)
    worker.start()
    try:
        assert finished.wait(5)
    finally:
        worker.stop()
    assert not worker._thread.is_alive()
    assert kernel.store.get(first.instruction_id)['error'] is None
    assert effects == [first.instruction_id]


def test_authorized_query_settles_fresh_owner_exit_without_repeating_business(owner, monkeypatch):
    """owner 刚退出留下的执行中记录，经有权查询结算为提交状态未知的失败，不重做业务"""
    kernel, call, effects, _ = owner
    business = {}

    def disconnected(inv):
        effects.append(inv.instruction_id)
        business['published_revision'] = 70
        raise SystemExit('owner stopped before storing completion')

    monkeypatch.setitem(kernel.binding.handlers, call.instruction, disconnected)
    context = InstructionContext('alice', 'web')
    with pytest.raises(SystemExit):
        kernel.execute(call, context)
    instruction_id, = effects
    assert kernel.store.get(instruction_id)['status'] == 'executing'
    with pytest.raises(InstructionError, match='不存在'):
        kernel.execution_record(instruction_id, InstructionContext('bob', 'web'))
    assert kernel.store.get(instruction_id)['status'] == 'executing'

    receipt = kernel.execution_record(instruction_id, context)
    assert receipt['status'] == 'failed'
    assert receipt['error']['commit_state'] == 'unknown'
    assert receipt['error']['details']['reason'] == 'interrupted'
    replay = kernel.execute(call, context)
    assert replay.instruction_id == instruction_id and replay.replayed
    assert replay.error == receipt['error']
    assert effects == [instruction_id] and business == {'published_revision': 70}


def test_live_handler_stays_executing_during_query_retry_and_maintenance(owner, monkeypatch):
    """处理函数仍在运行时，查询、重试和维护都不把执行判为中断"""
    kernel, call, effects, _ = owner
    other = InstructionKernel(kernel.binding, store=ExecutionStore(kernel.store.database, create=False),
                              authorize=lambda *_: None)
    entered, release = Event(), Event()

    def running(inv):
        effects.append(inv.instruction_id)
        entered.set()
        assert release.wait(10)
        return {'resource_id': 'workbench_fbfebc527201'}

    monkeypatch.setitem(kernel.binding.handlers, call.instruction, running)
    context = InstructionContext('alice', 'web')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(kernel.execute, call, context)
        try:
            assert entered.wait(5)
            instruction_id, = effects
            assert other.execution_record(instruction_id, context)['status'] == 'executing'
            assert other.maintain()['interrupted'] == 0
            retry = other.execute(call, context)
            assert retry.error['code'] == 'instruction.in_progress'
            assert retry.error['details']['instruction_id'] == instruction_id
        finally:
            release.set()
        assert future.result().status == 'committed'
    assert other.execution_record(instruction_id, context)['status'] == 'committed'
    assert effects == [instruction_id]


def test_fresh_interrupted_cursor_does_not_starve_behind_live_execution(owner, monkeypatch):
    """维护游标不因前面有仍在运行的执行而漏处理之后已中断的执行"""
    from management_portal.hatg import store as store_module
    from management_portal.hatg.kernel import now_iso

    kernel, call, effects, _ = owner
    monkeypatch.setattr(store_module, 'PRUNE_BATCH_SIZE', 1)
    for suffix in ('a', 'b'):
        kernel.store.begin(instruction_id='inst_exe_' + suffix * 20,
                           call=InstructionCall(call.instruction, call.arguments, idempotency_key=suffix),
                           caller='alice', entry='web', scope=('caller', 'alice'), now=now_iso())
    with kernel.store.active_execution('inst_exe_' + 'a' * 20) as acquired:
        assert acquired
        assert kernel.maintain()['interrupted'] == 0
        assert kernel.maintain()['interrupted'] == 1
        assert kernel.store.get('inst_exe_' + 'a' * 20)['status'] == 'executing'
        assert kernel.store.get('inst_exe_' + 'b' * 20)['status'] == 'failed'
    assert effects == []
