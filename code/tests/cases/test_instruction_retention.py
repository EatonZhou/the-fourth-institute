"""Recovery windows protect retries without retaining full ISA history.

:module: isa.instructions
:importance: P2
"""
from __future__ import annotations

import json
import uuid

import pytest

from management_common.portal_database import portal_database
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.store import ExecutionStore

START = "2026-10-01T00:00:00.000Z"
BEFORE = "2026-10-07T23:59:59.999Z"
AFTER = "2026-10-08T00:00:00.001Z"
SCOPE = ("caller", "alice")


@pytest.fixture
def store():
    return ExecutionStore(portal_database(schema="retention_" + uuid.uuid4().hex[:8]))


def call(key):
    return InstructionCall("UpdateProviderAccount", {"account_id": "codex_one", "max_concurrency": 2},
                           idempotency_key=key)


def begin(store, key, *, now=START, recovery_key="", caller="alice"):
    return store.begin(instruction_id="inst_exe_" + uuid.uuid4().hex[:20], call=call(key), caller=caller,
                       entry="lineage" if recovery_key else "web", scope=SCOPE, now=now, recovery_key=recovery_key)


def completed(store, key, *, recovery_key="", error=None):
    record, _ = begin(store, key, recovery_key=recovery_key)
    store.finish(record["instruction_id"], status="committed" if error is None else "failed", now=START,
                 result={"resident_id": "resident_0123456789ab"}, error=error)
    return record["instruction_id"]


def test_busy_scope_cannot_evict_unexpired_requests(store):
    """高频 ISA 调用不提前淘汰恢复窗口内记录

    同一范围超过旧数量上限后，7天内的原请求仍能返回原执行结果。

    :design: decision_898b8911a2ff, decision_d918ebebc062
    :design-point:
    """
    first = completed(store, "first")
    # More than the former 1024-record ceiling, within one retention window.
    store.database.write(lambda c: c.execute(
        "INSERT INTO instruction_executions "
        "(instruction_id,instruction,scope_kind,scope_id,caller,entry,request_key,fingerprint,status,created_at,updated_at,references_json,error_json) "
        "SELECT 'load-'||n,'UpdateProviderAccount','caller','alice','alice','web','','f','committed',%s,%s,'null','null' "
        "FROM generate_series(1,1100) n", (START, START)))
    assert store.prune(now=BEFORE) == 0
    record, replayed = begin(store, "first", now=BEFORE)
    assert replayed and record["instruction_id"] == first


def test_expired_request_is_fenced_without_retaining_identity_or_response(store):
    """ISA 窗口外旧请求拒绝自动重放

    执行记录到期清理，摘要守卫拒绝旧副作用请求，且不保留原身份或响应。

    :design: decision_898b8911a2ff, decision_d918ebebc062
    :design-point:
    """
    ident = completed(store, "one-side-effect")
    assert store.prune(now=BEFORE) == 0
    assert store.prune(now=AFTER) == 1
    assert store.get(ident) is None
    with pytest.raises(InstructionError) as failure:
        begin(store, "one-side-effect", now=AFTER)
    assert failure.value.to_dict()["details"]["reason"] == "idempotency_window_expired"
    assert failure.value.to_dict()["commit_state"] == "unknown"
    with store.database.read() as c:
        row = dict(c.execute("SELECT * FROM instruction_retry_guards").fetchone())
    assert set(row) == {"request_digest", "expired_at"}
    assert len(row["request_digest"]) == 64
    assert "alice" not in json.dumps(row, default=str) and "resident_" not in json.dumps(row, default=str)
    assert begin(store, "one-side-effect", now=AFTER, caller="bob")[1] is False


def test_equivalent_timestamp_notation_cannot_shorten_seven_day_window(store):
    """执行记录的时间戳换用等价写法不会提前结束 7 天恢复窗口

    :design: decision_898b8911a2ff, decision_d918ebebc062
    :design-point:
    """
    ident = completed(store, "boundary")
    store.database.write(lambda c: c.execute("UPDATE instruction_executions SET updated_at=%s WHERE instruction_id=%s",
                                            ("2026-10-01T00:00:00+00:00", ident)))
    assert store.prune(now="2026-10-08T00:00:00.000Z") == 0
    assert store.prune(now=AFTER) == 1


def test_unknown_results_expire_but_unsettled_executions_and_projections_remain(store):
    """结果未知的执行记录到期清理，执行中和投影待完成的记录保留

    :module: platform.core"""
    active, _ = begin(store, "active")
    pending = completed(store, "projection", error={"code": "instruction.projection_pending", "commit_state": "committed"})
    unknown = completed(store, "interrupted", error={"code": "instruction.failed", "commit_state": "unknown"})
    assert store.prune(now=AFTER) == 1
    assert store.get(unknown) is None
    assert all(store.get(i) is not None for i in (active["instruction_id"], pending))
    with pytest.raises(InstructionError, match="恢复窗口"):
        begin(store, "interrupted", now=AFTER)


def test_orphaned_executions_settle_without_retry_and_keep_seven_days(store):
    """无人认领的执行结算为提交状态未知的失败，不重试并保留 7 天

    :module: platform.core"""
    orphan, _ = begin(store, "orphan")
    assert store.settle_interrupted(now="2026-10-01T00:00:01Z") == 1
    settled = store.get(orphan["instruction_id"])
    assert settled["status"] == "failed"
    assert settled["error"]["commit_state"] == "unknown"
    assert settled["error"]["details"]["reason"] == "interrupted"
    assert store.prune(now=AFTER) == 0
    assert store.prune(now="2026-10-08T00:00:02Z") == 1
    with pytest.raises(InstructionError, match="恢复窗口"):
        begin(store, "orphan", now="2026-10-08T00:32:00Z")


def test_live_execution_lock_protects_long_call_across_owner_instances(store):
    """长时间执行持有的锁使其他 owner 实例既不结算它也不允许同键重试

    :module: platform.core"""
    from concurrent.futures import ThreadPoolExecutor
    active, _ = begin(store, "long-call")
    other = ExecutionStore(store.database, create=False)
    with store.active_execution(active["instruction_id"]) as acquired:
        assert acquired
        with store.database.read() as connection:
            assert not connection.in_transaction
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(other.settle_interrupted, now=AFTER).result() == 0
            with pytest.raises(InstructionError, match="仍在执行"):
                pool.submit(begin, other, "long-call", now=AFTER).result()
        assert store.get(active["instruction_id"])["status"] == "executing"
    assert other.settle_interrupted(now=AFTER) == 1


def test_unknown_effect_result_stays_pinned_until_ack(store):
    """结果未知的 Effect 调用记录在恢复方确认前不被清理

    :module: platform.core"""
    ident = completed(store, "unknown-effect", recovery_key="effect:unknown",
                      error={"code": "instruction.failed", "commit_state": "unknown"})
    assert store.prune(now=AFTER) == 0
    assert store.release_recovery(caller="alice", recovery_key="effect:unknown", instruction_ids=[ident]) == 1
    assert store.prune(now=AFTER) == 1


def test_effect_dependencies_require_exact_owner_acknowledgement(store):
    """Effect 依赖只由对应恢复所有者释放

    调用者和恢复键都匹配才解除ISA保留保护，重复确认幂等。

    :design: decision_898b8911a2ff, decision_d918ebebc062
    :design-point:
    """
    ident = completed(store, "effect-call", recovery_key="effect:1")
    assert store.prune(now=AFTER) == 0
    assert store.release_recovery(caller="bob", recovery_key="effect:1", instruction_ids=[ident]) == 0
    assert store.release_recovery(caller="alice", recovery_key="effect:other", instruction_ids=[ident]) == 0
    assert store.prune(now=AFTER) == 0
    assert store.release_recovery(caller="alice", recovery_key="effect:1", instruction_ids=[ident]) == 1
    assert store.release_recovery(caller="alice", recovery_key="effect:1", instruction_ids=[ident]) == 0
    assert store.prune(now=AFTER) == 1




def test_recovery_context_survives_transport_and_cannot_be_set_by_web_entry():
    """Effect 恢复上下文可传输且网页不能冒设

    内部上下文传输保留恢复键，网页入口不能注入恢复依赖。

    :design: decision_898b8911a2ff, decision_d918ebebc062
    :design-point:
    """
    context = InstructionContext("alice", "lineage", trigger_path_id="run1", recovery_key="effect:1")
    assert InstructionContext.from_payload(context.to_payload()) == context
    with pytest.raises(InstructionError):
        InstructionContext("alice", "web", recovery_key="effect:1")


def test_recovery_release_rpc_preserves_caller_assertion_and_key(store, monkeypatch):
    """恢复释放 RPC 校验调用者及恢复键

    RPC拒绝错误调用者，匹配所有者确认后才允许清理记录。

    :importance: P1
    :design: decision_898b8911a2ff, decision_d918ebebc062
    :design-point:
    """
    from types import SimpleNamespace
    from management_portal.hatg import routing, socket_rpc
    ident = completed(store, "effect-rpc", recovery_key="effect:1")
    monkeypatch.setattr(routing, "kernel_for", lambda *a, **k: SimpleNamespace(store=store))
    params = {"context": InstructionContext("alice", "lineage", recovery_key="effect:1").to_payload(),
              "recovery_release": {"key": "effect:1", "instruction_ids": [ident]}}
    replies = []
    socket_rpc.serve(params, "ack", replies.append, owner="execution", verified_caller="bob")
    assert not replies[-1]["ok"]
    assert store.prune(now=AFTER) == 0
    socket_rpc.serve(params, "ack", replies.append, owner="execution", verified_caller="alice")
    assert replies[-1]["result"]["released"] == 1
    assert store.prune(now=AFTER) == 1


def test_expiry_serializes_with_a_retry_without_reexecuting(store):
    """到期清理与同键重试并发时两者串行，重试不会重新执行

    :design: decision_898b8911a2ff, decision_d918ebebc062
    :design-point:
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    ident = completed(store, "race")
    barrier = Barrier(2)

    def retry():
        barrier.wait()
        try:
            record, replayed = begin(store, "race", now=AFTER)
            assert replayed and record["instruction_id"] == ident
            return "replayed"
        except InstructionError as exc:
            assert exc.to_dict()["details"]["reason"] == "idempotency_window_expired"
            return "expired"

    def expire():
        barrier.wait()
        return store.prune(now=AFTER)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(retry), pool.submit(expire)
        assert one.result() in {"replayed", "expired"}
        assert two.result() in {0, 1}
    with store.database.read() as c:
        rows = c.execute("SELECT instruction_id FROM instruction_executions WHERE request_key='race'").fetchall()
    assert all(row[0] == ident for row in rows)
