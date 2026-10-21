"""Human Effect disposition and Effect literal diagnostics.

:importance: P2
"""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import effect_disposition as disposition
from management_portal.society_service import lineage_effect_script_journal as journal
from management_portal.society_service import lineage_program_runtime as runtime
from management_portal.society_service import trigger_paths, lineage_program_effect_execution as execution
from management_portal.society_service.isa import definition, runs
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_effect_script import _lineage, _plan, _Host, _Outcome
from tests.cases.test_effect_recovery_artifacts import activation


@pytest.fixture
def admin(monkeypatch):
    from management_portal import permission_system
    requests = []
    monkeypatch.setattr(permission_system, "permitted", lambda actor, instruction, parameter, ids, **kw:
                        requests.append(("permitted", actor, list(ids))) or iter(()))
    monkeypatch.setattr(permission_system, "authorize_instruction", lambda *a, **kw:
                        requests.append(("authorize_instruction", a)))
    monkeypatch.setattr(journal, "cleanup", lambda *a: True)
    return requests


def unknown(lid="lineage_t3_unknown", key="effect:key"):
    _lineage(lid)
    journal.begin(lid, key, effect_id="effect_aaaaaaaaaaaa", trigger_path_id="", script_sha="sha",
                  source={"event_id": "event_recovery", "source_wire_id": "wire_aaaaaaaaaaaa"})
    journal.record_call(lid, key, "c1", {"status": "unresolved", "ok": False, "instruction": "Ping",
                        "caller": "original", "instruction_id": "inst_lin_t3",
                        "error": {"commit_state": "unknown"}})
    return lid, key


@pytest.mark.parametrize("status", ["succeeded", "failed"])
@pytest.mark.parametrize("call_status", ["unresolved", "executing"])
def test_disposition_ends_unknown_activation_and_releases_only_its_occurrence(admin, status, call_status):
    """人工处置结果未知的 Effect 后结束该次激活并只放行其事件，已定结论不能改写

    :importance: P1"""
    lid, key = unknown()
    if call_status == "executing":
        journal.record_call_started(lid, key, "c1", {"instruction": "Ping", "caller": "original",
                                                   "instruction_id": "inst_lin_t3"})
    for event_id in ["event_recovery", "event_next"]:
        event = {"id": event_id, "type": "manual.triggered", "occurred_at": "2026-10-14T00:00:00Z", "payload": {}}
        event["payload"]["trigger_path_id"] = trigger_paths.begin_event(
            lid, "event_source_aaaaaaaaaaaa", event, cause_kind="external", cause_id=event_id,
        )
        runtime.stage_event_delivery(lid, "event_source_aaaaaaaaaaaa", "wire_aaaaaaaaaaaa", event_id, event)
    active = journal.load(lid, key)
    before = trigger_paths.get(lid, active["trigger_path_id"])
    assert before["status"] == "active" and active["calls"]["c1"]["status"] in {"unresolved", "executing"}
    result = disposition.resolve(lid, key, status, "admin-subject", note="已核对外部结果")
    assert admin == []  # Authorization belongs to the ISA boundary.
    assert result["status"] == status
    assert result["disposition"]["actor"] == "admin-subject"
    assert result["disposition"]["note"] == "已核对外部结果"
    assert result["calls"]["c1"]["status"] == call_status  # No invented owner commit.
    assert not get_lineage_data_store().effect_work.list(lid)
    assert get_lineage_data_store().effect_cleanup_task(lid, key)["instructions"] == {"original": ["inst_lin_t3"]}
    journal.ensure_deletion_can_settle(lid)
    assert runtime.claim_pending_event(lid, "wire_aaaaaaaaaaaa")["event"]["id"] == "event_next"
    assert disposition.resolve(lid, key, status, "admin-subject") == result
    with pytest.raises(InstructionError, match="不能改写"):
        disposition.resolve(lid, key, "failed" if status == "succeeded" else "succeeded", "admin-subject")


def test_disposition_rejects_missing_or_confirmed_calls(admin):
    """人工处置拒绝不存在的激活，以及没有结果未知调用的激活"""
    lid, key = unknown()
    with pytest.raises(InstructionError) as missing:
        disposition.resolve(lid, "missing", "failed", "admin-subject")
    assert missing.value.code == "instruction.not_found"
    journal.record_call(lid, key, "c1", {"status": "committed", "ok": True, "result": {}})
    with pytest.raises(InstructionError, match="没有结果未知"):
        disposition.resolve(lid, key, "failed", "admin-subject")


def test_disposition_is_atomic_if_queue_settlement_fails(admin, monkeypatch):
    """队列结算失败时人工处置整体回滚，Effect 仍处于运行中"""
    lid, key = unknown()
    monkeypatch.setattr(runtime, "settle_effect_delivery", lambda *a: (_ for _ in ()).throw(OSError("storage")))
    with pytest.raises(OSError):
        disposition.resolve(lid, key, "failed", "admin-subject")
    assert journal.load(lid, key)["status"] == "running"
    assert trigger_paths.effect_finished(lid, key) is None


def test_running_external_call_cannot_be_disposed_and_holds_no_definition_lock(admin, monkeypatch):
    """外部调用仍在执行时不能人工处置也不占 Lineage 定义锁，处置后重放不再执行"""
    from management_portal.hatg import routing
    from management_portal.portal_access_control import get_portal_access_store
    get_portal_access_store().upsert_user("CASE-A", actor="portal", status="active")
    entered, release = threading.Event(), threading.Event()
    lineage = _lineage("lineage_bbbbbbbbbbbb")
    inv = activation()
    from management_portal.society_service.lineage_program_effect_rules import effect_idempotency_key
    key = effect_idempotency_key(lineage["lineage_id"], "effect_aaaaaaaaaaaa", inv)
    def external(*args):
        entered.set()
        assert release.wait(10)
        return _Outcome("failed", error={"commit_state": "unknown"})
    monkeypatch.setattr(routing, "execute", external)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(execution._execute_program_effect_component, lineage, _plan("Ping"),
                             "effect_aaaaaaaaaaaa", inv, "CASE-A", host=_Host())
        try:
            assert entered.wait(10)
            with get_lineage_data_store().database.transaction() as connection:
                connection.execute("SELECT lineage_id FROM lineages WHERE lineage_id=%s FOR UPDATE NOWAIT", (lineage["lineage_id"],))
            assert not disposition.can_resolve(lineage["lineage_id"], key, journal.load(lineage["lineage_id"], key))
            with pytest.raises(InstructionError, match="仍在执行"):
                disposition.resolve(lineage["lineage_id"], key, "succeeded", "admin-subject")
        finally:
            release.set()
        with pytest.raises(journal.RecoveryPending):
            future.result()
    result = disposition.resolve(lineage["lineage_id"], key, "succeeded", "admin-subject")
    monkeypatch.setattr(routing, "execute", lambda *a: pytest.fail("Must not reexecute"))
    replay = execution._execute_program_effect_component(lineage, _plan("Ping"), "effect_aaaaaaaaaaaa", inv,
                                                       "CASE-A", host=_Host())
    assert replay["disposition"] == result["disposition"] and replay["replayed"]


def _effect_diagnostics(monkeypatch, script):
    from management_portal.society_service import lineage_program_runtime_projection as projection

    program = {"components": [{"id": "effect_aaaaaaaaaaaa", "kind": "effect", "config": {"script": script}}], "wires": []}
    monkeypatch.setattr(definition, "domain", lambda: SimpleNamespace(get_lineage_definition_for_analysis=lambda *a: {"program": program, "nodes": []}))
    monkeypatch.setattr(projection, "_program_event_source_runtime_view", lambda _lineage: {})
    result = definition.get_lineage_diagnostics(SimpleNamespace(caller="sample_admin", argument=lambda _: "lineage_aaaaaaaaaaaa"))
    return [item for item in result["diagnostics"] if item["item_id"] == "effect_aaaaaaaaaaaa"], result["runnable"]


@pytest.mark.parametrize("argument", ['limit="bad"', 'limit:=true', 'limit:=0'])
def test_diagnostics_report_literal_parameter_location(monkeypatch, argument):
    """Effect 脚本字面参数类型或取值非法时，GetLineageDiagnostics 报告所在组件和行号"""
    found, runnable = _effect_diagnostics(monkeypatch, f'# query\nListTriggerPaths lineage_id="lineage_aaaaaaaaaaaa" {argument}')
    [diagnostic] = found
    assert diagnostic["level"] == "error" and not runnable
    assert diagnostic["message"].startswith("第 2 行") and "limit" in diagnostic["message"]


@pytest.mark.parametrize("argument", ['limit:=5', 'limit="${arguments.limit}"'])
def test_diagnostics_accept_typed_literal_and_defer_dynamic_arguments(monkeypatch, argument):
    """GetLineageDiagnostics 接受类型正确的字面参数，动态引用的参数留到运行时检查"""
    found, _runnable = _effect_diagnostics(monkeypatch, f'ListTriggerPaths lineage_id="lineage_aaaaaaaaaaaa" {argument}')
    assert not [item for item in found if item["level"] == "error"]
