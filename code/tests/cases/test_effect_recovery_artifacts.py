"""Effect recovery preserves exact results and commits history before cleanup.

:module: lineage.hatg
:importance: P2
:design: decision_4637ca4a69e2, decision_898b8911a2ff
"""
from dataclasses import replace

import pytest

from management_common.principal import OperationIdentity
from management_portal.hatg import routing
from management_portal.portal_access_control import get_portal_access_store
from management_portal.society_service import lineage_effect_script_journal as journal
from management_portal.society_service import lineage_program_effect_execution as execution
from management_portal.society_service import lineage_primitives
from management_portal.society_service.effect_artifacts import ArtifactStore
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_effect_script import _Host, _Outcome, _lineage, _plan
from tests.cases.test_lineage_generic_primitives import primitive_store


@pytest.fixture(autouse=True)
def artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_EFFECT_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    get_portal_access_store().upsert_user("CASE-A", actor="portal", status="active")


def activation(actor="CASE-A"):
    return lineage_primitives.EffectInvocation(id="invocation_recovery", activation=lineage_primitives.LineageEvent(
        id="event_recovery", type="manual.triggered", payload={},
        identity=OperationIdentity.for_actor(actor).to_dict()))


def test_large_call_restarts_without_repeating_and_preserves_business_summary(monkeypatch):
    """大结果调用中断后重启不重复执行，输出完整，调用摘要只留业务 ID，再次执行直接回放

    :design-point:"""
    lineage = _lineage("lineage_large_recovery")
    inv = replace(activation(), source_kind="packet", source_id="event_source_recovery", source_wire_id="wire_recovery")
    payload = {"resident_id": "resident_created", "body": "完整" * journal.MAX_RESULT_BYTES}
    count = []
    stopping = [True]

    def execute(call, context):
        assert context.recovery_key
        count.append(call["instruction"])
        if call["instruction"] == "Crash" and stopping[0]:
            raise SystemExit("handover")
        return _Outcome("committed", payload if call["instruction"] == "ForkResident" else {}, instruction_id="inst_lin_test")

    monkeypatch.setattr(routing, "execute", execute)
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **kw: {})
    plan = _plan('child=$(ForkResident source_resident_id="resident_source")\nCrash\noutput resident_id="${child.resident_id}" body="${child.body}"')
    plan["event_sources"] = {"event_source_recovery": {"event_type": "manual.triggered"}}
    with pytest.raises(SystemExit):
        execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", inv, "CASE-A", host=_Host())
    store = get_lineage_data_store()
    [key] = store.effect_work.list(lineage['lineage_id'])
    original = journal.load(lineage["lineage_id"], key)
    assert original["event_source_id"] == inv.source_id
    assert original["source_wire_id"] == inv.source_wire_id
    assert original["event_id"] == inv.activation.id
    ref = original["calls"]["c1"]["result_artifact"]
    assert ArtifactStore().read(lineage["lineage_id"], key, ref) == payload
    assert "result_omitted" not in original["calls"]["c1"]
    stopping[0] = False
    result = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", inv, "CASE-A", host=_Host())
    assert count == ["ForkResident", "Crash", "Crash"]
    output_ref = result["typed_outputs"]["artifact"]
    assert ArtifactStore().read(lineage["lineage_id"], key, output_ref) == payload
    with pytest.raises(FileNotFoundError):
        ArtifactStore().read(lineage["lineage_id"], key, ref)
    assert result["calls"][0]["business_ids"] == {"resident_id": "resident_created"}
    assert "body" not in str(result["calls"])
    assert result["calls"][0]["started_at"] <= result["calls"][0]["at"]
    finished = journal.trigger_paths.effect_finished(lineage["lineage_id"], key)
    path = journal.trigger_paths.get(lineage["lineage_id"], finished["trigger_path_id"])
    assert path["root_kind"] == "effect" and path["occurrence_id"] == key
    again = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", inv, "CASE-A", host=_Host())
    assert again["replayed"] and again["typed_outputs"] == result["typed_outputs"]
    assert len(count) == 3


def test_terminal_failure_keeps_journal_and_artifact_until_transaction_commits(monkeypatch):
    """终态写入失败时 Effect 日志和恢复产物原样保留，不生成清理任务

    :design-point:"""
    lid = _lineage("lineage_terminal_failure")["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    entry = journal.record_call(lid, "key", "c1", {"ok": True, "result": {"body": "x" * (journal.MAX_RESULT_BYTES + 1)}})
    original = journal.load(lid, "key")
    monkeypatch.setattr(journal.trigger_paths, "effect_settled", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        journal.finish(lid, "key", status="succeeded", outputs={})
    assert journal.load(lid, "key") == original
    assert journal.restore_call(lid, "key", entry)["result"]["body"].startswith("x")
    assert not get_lineage_data_store().effect_cleanup_tasks(lineage_id=lid)


def test_finish_unconfirmed_write_does_not_clear_journal(monkeypatch):
    """Effect 终态写入未确认时日志保持 running，不被清除

    :design-point:"""
    lid = _lineage("lineage_unconfirmed_finish")["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    monkeypatch.setattr(journal.trigger_paths, "effect_settled", lambda *a: False)
    with pytest.raises(journal.RecoveryPending):
        journal.finish(lid, "key", status="succeeded", outputs={})
    assert journal.load(lid, "key")["status"] == "running"


def test_failed_release_has_durable_retry_and_does_not_erase_outputs(monkeypatch):
    """Effect 结束时依赖释放失败会留下持久重试任务，重试成功后输出产物仍可读

    :importance: P1
    :design-point:
    """
    lid = _lineage("lineage_release_retry")["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    entry = journal.record_call(lid, "key", "c1", {"ok": True, "caller": "resident_caller", "instruction_id": "inst_lin_test", "result": {"large": "x" * (journal.MAX_RESULT_BYTES + 1)}})
    output = journal.output_reference(lid, "key", {"output": "retained"})
    def unavailable(*args):
        raise OSError("owner unavailable")
    monkeypatch.setattr(routing, "release_recovery", unavailable)
    journal.finish(lid, "key", status="succeeded", outputs=output)
    assert not get_lineage_data_store().effect_work.list(lid)
    assert journal.restore_call(lid, "key", entry)["result"]["large"]
    received = []
    monkeypatch.setattr(routing, "release_recovery", lambda key, ids, ctx: received.append((key, ids, ctx.caller)))
    assert journal.recover_cleanup(lid) == {"key": True}
    assert received == [("key", ["inst_lin_test"], "resident_caller")]
    assert ArtifactStore().read(lid, "key", output["artifact"]) == {"output": "retained"}
    assert journal.recover_cleanup(lid) == {}


@pytest.mark.parametrize("state", ["unknown", "committed"])
def test_uncertain_or_projection_pending_call_is_not_terminal(monkeypatch, state):
    """提交状态未知或投影待定的调用让 Effect 挂起待恢复，调用记为 unresolved

    :design-point:"""
    lineage = _lineage("lineage_unknown_" + state)
    monkeypatch.setattr(routing, "execute", lambda *a: _Outcome("projection_pending", error={"code": "instruction.projection_pending", "commit_state": state}, instruction_id="inst_lin_unknown"))
    with pytest.raises(journal.RecoveryPending):
        execution._execute_program_effect_component(lineage, _plan("Ping"), "effect_aaaaaaaaaaaa", activation(), "CASE-A", host=_Host())
    assert not journal.trigger_paths.effect_finished(lineage["lineage_id"], execution.effect_idempotency_key(lineage["lineage_id"], "effect_aaaaaaaaaaaa", activation()))
    [record] = get_lineage_data_store().effect_work.list(lineage['lineage_id']).values()
    assert journal.call_summary(record)[0]["status"] == "unresolved"


@pytest.mark.parametrize("code", ["instruction.cancelled", "instruction.timed_out"])
def test_known_not_committed_disposition_remains_explicit(monkeypatch, code):
    """确认未提交的取消或超时调用让 Effect 以失败结束并保留原错误

    :design-point:"""
    lineage = _lineage("lineage_" + code.split(".")[-1])
    error = {"code": code, "message": "confirmed by owner", "retryable": False, "commit_state": "not_committed"}
    monkeypatch.setattr(routing, "execute", lambda *a: _Outcome("failed", error=error, instruction_id="inst_lin_final"))
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **kw: {})
    result = execution._execute_program_effect_component(lineage, _plan("Ping"), "effect_aaaaaaaaaaaa", activation(), "CASE-A", host=_Host())
    assert result["status"] == "failed" and result["calls"][0]["error"] == error


def test_missing_artifact_fails_recovery_without_repeating_call(monkeypatch):
    """恢复产物丢失时恢复挂起并保留日志，不重复调用

    :design-point:"""
    lid = _lineage("lineage_missing_blob")["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    entry = journal.record_call(lid, "key", "c1", {"ok": True, "result": {"x": "x" * (journal.MAX_RESULT_BYTES + 1)}})
    ArtifactStore().clear_recovery(lid, "key")
    with pytest.raises(journal.RecoveryPending):
        journal.restore_call(lid, "key", entry)
    assert get_lineage_data_store().effect_work.get(lid, 'key')


def test_artifact_scope_and_integrity():
    """Effect 产物只能在所属 Lineage 内读取，内容被篡改时完整性校验失败

    :design-point:"""
    store = ArtifactStore()
    ref = store.put("lineage", "key", {"x": 1}, purpose="recovery")
    with pytest.raises(ValueError, match="foreign"):
        store.read("other", "key", ref)
    path = store._scope("lineage", "key") / "recovery" / (ref["sha256"] + ".json")
    path.write_text('{"x":2}')
    with pytest.raises(ValueError, match="integrity"):
        store.read("lineage", "key", ref)


def test_outer_transaction_rollback_does_not_release_or_delete_recovery(monkeypatch):
    """外层事务回滚时 Effect 结束不释放依赖，也不删除恢复记录

    :importance: P1
    :design-point:
    """
    lid = _lineage("lineage_outer_rollback")["lineage_id"]
    store = get_lineage_data_store()
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    entry = journal.record_call(lid, "key", "c1", {"ok": True, "caller": "resident_caller", "instruction_id": "inst_lin_test", "result": {"body": "x" * (journal.MAX_RESULT_BYTES + 1)}})
    released = []
    monkeypatch.setattr(routing, "release_recovery", lambda *a: released.append(a))
    with pytest.raises(RuntimeError, match="outer"):
        with store.database.transaction():
            journal.finish(lid, "key", status="succeeded", outputs={})
            assert journal.cleanup(lid, "key") is False
            raise RuntimeError("outer rollback")
    assert released == []
    assert journal.restore_call(lid, "key", entry)["result"]["body"]
    assert journal.load(lid, "key")["status"] == "running"
    assert not journal.trigger_paths.effect_finished(lid, "key")


def test_aggregate_journal_budget_offloads_without_losing_results(monkeypatch):
    """Effect 日志记录超出总预算时调用结果转存为产物，恢复和摘要仍完整

    :design-point:"""
    lid = _lineage("lineage_aggregate_budget")["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    monkeypatch.setattr(journal, "MAX_RECORD_BYTES", 512)
    result = {"script_run_id": "scriptrun_123", "body": "x" * 600}
    stored = journal.record_call(lid, "key", "c1", {"ok": True, "instruction": "RunScript", "result": result})
    assert "result_artifact" in stored and "result" not in stored
    assert journal.restore_call(lid, "key", stored)["result"] == result
    assert journal.call_summary(journal.load(lid, "key"))[0]["script_run_id"] == "scriptrun_123"


@pytest.mark.parametrize("status", ["cancelled", "timed_out"])
def test_explicit_effect_disposition_keeps_actual_reason_and_rejects_unknown(status):
    """Effect 以 cancelled 或 timed_out 结束须附原因，仍有结果未知的调用时不能结束

    :design-point:"""
    lid = _lineage("lineage_effect_disposition_" + status)["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    with pytest.raises(ValueError, match="evidenced"):
        journal.finish(lid, "key", status=status, outputs={})
    disposition = {"reason": "operator confirmed no instruction remains in flight", "occurred_at": "2026-10-08T12:00:00Z"}
    journal.record_call(lid, "key", "c1", {"status": "unresolved", "ok": False, "error": {"commit_state": "unknown", "retryable": True}})
    with pytest.raises(journal.RecoveryPending):
        journal.finish(lid, "key", status=status, outputs={}, failure=disposition)
    journal.record_call(lid, "key", "c1", {"status": "failed", "ok": False, "error": {"commit_state": "not_committed"}})
    journal.finish(lid, "key", status=status, outputs={}, failure=disposition)
    terminal = journal.trigger_paths.effect_finished(lid, "key")
    assert terminal["status"] == status and terminal["failure"] == disposition


def test_recovery_release_preserves_delegated_callers_and_waits_for_history(monkeypatch):
    """Effect 释放恢复依赖保留真实调用者并等待历史提交

    Family门面和原发起者各自持有的ISA依赖分别释放，释放发生在长期Effect终态事实提交之后。

    :importance: P1
    :design-point:
    """
    from dataclasses import replace
    lineage = _lineage("lineage_delegated_release")
    initiator = "resident_111111111111"
    facade = "resident_222222222222"
    def context(lineage, effect_id, instruction, arguments, original, *, lineage_id, event=None):
        return replace(original, caller=facade) if instruction == "FacadeCall" else original
    monkeypatch.setattr(execution, "_family_note_context", context)
    observed = []
    def execute(call, ctx):
        observed.append((call["instruction"], ctx.caller, ctx.recovery_key))
        return _Outcome("committed", {}, instruction_id="inst_lin_" + call["instruction"])
    monkeypatch.setattr(routing, "execute", execute)
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **kw: {})
    released = []
    def release(key, ids, ctx):
        assert journal.trigger_paths.effect_finished(lineage["lineage_id"], key)["status"] == "succeeded"
        assert ctx.recovery_key == key and ctx.entry == "lineage"
        released.append((ctx.caller, ids))
    monkeypatch.setattr(routing, "release_recovery", release)
    execution._execute_program_effect_component(lineage, _plan("Ping\nFacadeCall\noutput done=true"), "effect_aaaaaaaaaaaa", activation(initiator), initiator, host=_Host())
    assert observed[0][1] == initiator and observed[1][1] == facade
    assert observed[0][2] == observed[1][2]
    assert dict(released) == {initiator: ["inst_lin_Ping"], facade: ["inst_lin_FacadeCall"]}


def test_committed_cleanup_survives_lineage_deletion_and_service_restart(monkeypatch):
    """Effect 清理任务在 Lineage 删除和服务重启后存续

    长期输出先提交，独立cleanup outbox在Lineage删除后仍可由新worker处理，正式输出产物保持可读。

    :design-point:
    """
    from management_portal.society_service.effect_recovery_worker import EffectRecoveryWorker
    from management_portal.society_service.state_store import reset_lineage_data_store_for_tests
    lid = _lineage("lineage_cleanup_deleted")["lineage_id"]
    store = get_lineage_data_store()
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    entry = journal.record_call(lid, "key", "c1", {"ok": True, "caller": "resident_original", "instruction_id": "inst_lin_maint", "result": {"body": "x" * (journal.MAX_RESULT_BYTES + 1)}})
    output = journal.output_reference(lid, "key", {"output": "historical"})
    released = []
    monkeypatch.setattr(routing, "release_recovery", lambda key, ids, ctx: released.append((key, ids, ctx.caller)))
    with store.database.transaction():
        journal.finish(lid, "key", status="succeeded", outputs=output)
        assert not released
    store.remove_lineage(lid, deleted_by="sample_admin")
    assert store.effect_cleanup_task(lid, "key")
    reset_lineage_data_store_for_tests()
    # A new worker takes only persisted tasks; no graph or next Effect is used.
    worker = EffectRecoveryWorker()
    assert worker.drain_once() == {lid + "/key": True}
    assert released == [("key", ["inst_lin_maint"], "resident_original")]
    assert not get_lineage_data_store().effect_cleanup_task(lid, "key")
    assert journal.trigger_paths.effect_finished(lid, "key")["outputs"] == output
    assert ArtifactStore().read(lid, "key", output["artifact"]) == {"output": "historical"}
    with pytest.raises(FileNotFoundError):
        ArtifactStore().read(lid, "key", entry["result_artifact"])


def test_independent_worker_retries_cleanup_without_another_effect(monkeypatch):
    """独立 worker 无新 Effect 仍重试清理

    首次释放失败保存outbox任务，worker启动后独立重试，成功确认后删除。

    :design-point:
    """
    import threading
    from management_portal.society_service.effect_recovery_worker import EffectRecoveryWorker
    lid = _lineage("lineage_cleanup_tick")["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    journal.record_call(lid, "key", "c1", {"ok": True, "caller": "resident_original", "instruction_id": "inst_lin_tick", "result": {}})
    monkeypatch.setattr(routing, "release_recovery", lambda *a: (_ for _ in ()).throw(OSError("disconnected owner")))
    journal.finish(lid, "key", status="succeeded", outputs={})
    worker = EffectRecoveryWorker(poll_seconds=0.05)
    assert worker.drain_once() == {lid + "/key": False}
    assert get_lineage_data_store().effect_cleanup_task(lid, "key")
    completed = threading.Event()
    monkeypatch.setattr(routing, "release_recovery", lambda *a: completed.set())
    worker.start()
    try:
        assert completed.wait(3), "cleanup did not run independently on worker startup"
    finally:
        worker.stop()
    assert get_lineage_data_store().effect_cleanup_task(lid, "key") is None


@pytest.mark.parametrize("kind", ["missing_result", "missing_blob", "corrupt_blob"])
def test_success_receipt_integrity_failure_never_calls_router(monkeypatch, kind):
    """成功回执或恢复产物不完整时拒绝重执行

    缺失结果、缺失blob和校验失败均保留恢复保护，不再次调用router，不伪造Effect终态。

    :design-point:
    """
    import hashlib
    lineage = _lineage("lineage_receipt_integrity_" + kind)
    inv = activation()
    plan = _plan("Ping")
    key = execution.effect_idempotency_key(lineage["lineage_id"], "effect_aaaaaaaaaaaa", inv)
    journal.begin(lineage["lineage_id"], key, effect_id="effect_aaaaaaaaaaaa", trigger_path_id="", script_sha=hashlib.sha256(b"Ping").hexdigest())
    entry = journal.record_call(lineage["lineage_id"], key, "c1", {"ok": True, "result": {"x": "x" * (journal.MAX_RESULT_BYTES + 1)}})
    if kind == "missing_result":
        with get_lineage_data_store().effect_work.transaction(lineage['lineage_id'], key) as record:
            record["calls"]["c1"].pop("result_artifact")
    else:
        path = ArtifactStore()._scope(lineage["lineage_id"], key) / "recovery" / (entry["result_artifact"]["sha256"] + ".json")
        if kind == "missing_blob":
            path.unlink()
        else:
            path.write_text('{"incorrect":"bytes"}')
    called = []
    monkeypatch.setattr(routing, "execute", lambda *a: called.append(a))
    with pytest.raises(journal.RecoveryPending):
        execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", inv, "CASE-A", host=_Host())
    assert not called
    assert get_lineage_data_store().effect_work.get(lineage['lineage_id'], key)
    assert not journal.trigger_paths.effect_finished(lineage["lineage_id"], execution.effect_idempotency_key(lineage["lineage_id"], "effect_aaaaaaaaaaaa", activation()))


def test_lineage_deletion_settles_safe_effect_and_retains_cleanup_for_tick(monkeypatch):
    """删除 Lineage 终结已确认 Effect 并保留后续清理

    安全activation记录cancelled，提交outbox后由独立tick释放依赖并清理临时产物。

    :design-point:
    """
    from management_portal.society_service.lineage_domain import _lineage_catalog_store
    from management_portal.society_service.effect_recovery_worker import EffectRecoveryWorker
    lid = _lineage("lineage_delete_settled")["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    entry = journal.record_call(lid, "key", "c1", {"ok": True, "caller": "resident_original", "instruction_id": "inst_lin_delete", "result": {"body": "x" * (journal.MAX_RESULT_BYTES + 1)}})
    observed = []
    monkeypatch.setattr(routing, "release_recovery", lambda *a: observed.append(a))
    _lineage_catalog_store().remove(lid, deleted_by="sample_admin")
    assert journal.trigger_paths.effect_finished(lid, "key")["status"] == "cancelled"
    assert get_lineage_data_store().effect_cleanup_task(lid, "key")
    assert not observed
    assert EffectRecoveryWorker().drain_once() == {lid + "/key": True}
    with pytest.raises(FileNotFoundError):
        ArtifactStore().read(lid, "key", entry["result_artifact"])


def test_lineage_deletion_preserves_in_flight_effect_until_confirmed():
    """删除 Lineage 保留尚未确认的 Effect

    执行中调用拒绝删除并保留Lineage与journal，不生成未知结果的完成事实。

    :design-point:
    """
    from management_portal.society_service.lineage_domain import _lineage_catalog_store
    lid = _lineage("lineage_delete_inflight")["lineage_id"]
    journal.begin(lid, "key", effect_id="effect", trigger_path_id="", script_sha="sha")
    journal.record_call_started(lid, "key", "c1", {"instruction": "CreateResident", "caller": "resident_original"})
    with pytest.raises(journal.RecoveryPending):
        _lineage_catalog_store().remove(lid, deleted_by="sample_admin")
    assert get_lineage_data_store().lineage_definition(lid)
    assert journal.load(lid, "key")["calls"]["c1"]["status"] == "executing"
    assert not journal.trigger_paths.effect_finished(lid, "key")


@pytest.mark.parametrize("in_flight", [False, True])
def test_removing_effect_node_settles_only_confirmed_activation(primitive_store, in_flight):
    """移除 Effect 节点只终结已确认的 activation

    已确认可取消并删除节点；执行中activation保留节点和恢复状态，不能伪造完成。

    :design-point:
    """
    from management_portal.society_service import lineage_domain
    from tests.cases.test_lineage_generic_primitives import _worker, WORK_A, ACTOR
    effect_id = "effect_aaaaaaaaaaaa"
    created = lineage_domain.create_lineage({"title": "Effect deletion receipt",
        "nodes": [_worker(WORK_A)], "edges": [], "program": {"components": [{"id": effect_id, "kind": "effect",
        "config": {"script": f'GetResident resident_id="{WORK_A}"', "endpoints": []}}], "wires": []}}, ACTOR)
    lid = created["lineage_id"]
    journal.begin(lid, "key", effect_id=effect_id, trigger_path_id="", script_sha="sha")
    if in_flight:
        journal.record_call_started(lid, "key", "c1", {"instruction": "GetResident", "caller": ACTOR})
        with pytest.raises(journal.RecoveryPending):
            lineage_domain.delete_lineage_program_component(lid, {"component_id": effect_id, "expected_revision": created["revision"]}, ACTOR)
        assert any(node["id"] == effect_id for node in get_lineage_data_store().lineage_definition(lid)["program"]["components"])
        assert not journal.trigger_paths.effect_finished(lid, "key")
    else:
        lineage_domain.delete_lineage_program_component(lid, {"component_id": effect_id, "expected_revision": created["revision"]}, ACTOR)
        assert journal.trigger_paths.effect_finished(lid, "key")["status"] == "cancelled"
        assert not any(node["id"] == effect_id for node in get_lineage_data_store().lineage_definition(lid)["program"]["components"])


def test_registered_artifact_root_is_explicit_and_can_be_overridden_without_writing(monkeypatch, tmp_path):
    """Effect 产物存储根目录属于本安装且支持绝对路径覆盖

    默认位于本安装 staging_root 的 shared/effect_artifacts，环境覆盖须为绝对路径，确定根目录不写产物。

    :design-point:
    """
    from management_common.portal_paths import path as portal_path
    monkeypatch.delenv("PORTAL_EFFECT_ARTIFACT_ROOT")
    default = portal_path("staging_root") / "shared" / "effect_artifacts"
    assert ArtifactStore().root == default and not default.exists()
    monkeypatch.setenv("PORTAL_EFFECT_ARTIFACT_ROOT", str(tmp_path))
    assert ArtifactStore().root == tmp_path
    monkeypatch.setenv("PORTAL_EFFECT_ARTIFACT_ROOT", "relative")
    with pytest.raises(ValueError, match="absolute"):
        ArtifactStore()


def test_cleanup_worker_rotates_past_failed_first_batch(monkeypatch):
    """清理 worker 轮转越过失败批次避免饥饿

    首100个任务失败不阻塞第101个任务，keyset轮转后仍保留并重试失败任务。

    :design-point:
    """
    from management_portal.society_service.effect_recovery_worker import EffectRecoveryWorker
    lid = _lineage("lineage_cleanup_fairness")["lineage_id"]
    store = get_lineage_data_store()
    for i in range(101):
        store.enqueue_effect_cleanup(lid, f"key_{i:03}", {"resident_original": [f"inst_lin_{i}"]})
    def release(key, ids, ctx):
        if int(key.rsplit("_", 1)[1]) < 100:
            raise OSError("unavailable owner")
    monkeypatch.setattr(routing, "release_recovery", release)
    worker = EffectRecoveryWorker()
    first = worker.drain_once()
    assert len(first) == 100 and not any(first.values())
    assert worker.drain_once() == {lid + "/key_100": True}
    assert store.effect_cleanup_task(lid, "key_100") is None
    assert len(store.effect_cleanup_tasks(lineage_id=lid)) == 100
    assert worker.drain_once() == {}  # end of keyset resets the volatile cursor
    assert len(worker.drain_once()) == 100
