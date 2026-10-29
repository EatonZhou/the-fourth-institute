from management_portal.society_service import business_fact_views
"""Definition commits and runtime recovery preserve concurrent authority."""
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from management_common.principal import OperationIdentity

from management_portal.society_service import coordinator
from management_portal.society_service import lineage_definition_merge as merge
from management_portal.society_service.errors import LineageConflictError
from management_portal.society_service.script_runs import service


def test_function_block_fields_merge_independently():
    """三方合并时功能块的不同字段各自保留双方的修改"""
    base = {"nodes": [{"id": "resident_111111111111"}], "function_blocks": [
        {"id": "fbi_111111111111", "title": "one", "template_id": None,
         "elements": [{"id": "resident_111111111111", "kind": "resident", "key": None}]}]}
    ours, current = deepcopy(base), deepcopy(base)
    ours["function_blocks"][0]["title"] = "ours"
    current["function_blocks"][0]["template_id"] = "template_111111111111"
    result = merge.merge_definition(base, ours, current, successor=lambda *_: "")
    assert result["function_blocks"][0]["title"] == "ours"
    assert result["function_blocks"][0]["template_id"] == "template_111111111111"




def test_cancel_preserves_result_written_during_stop(tmp_path, monkeypatch):
    """取消脚本运行时保留停止期间写出的结果，状态记为 succeeded

    :module: spec.design-governance"""
    identifier = "script_run_race"
    root = tmp_path / identifier
    root.mkdir()
    row = {"script_run_id": identifier, "lineage_id": "lineage_aaaaaaaaaaaa", "status": "running", "unit": "test",
           "started_at": "2026-10-11T00:00:00+00:00"}

    @contextmanager
    def edit(_identifier):
        yield row

    monkeypatch.setattr(service.store, "edit", edit)
    monkeypatch.setattr(service.runtime, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(service, "_authorize", lambda *_: None)
    monkeypatch.setattr(service, "revoke", lambda *_: None)
    monkeypatch.setattr(service, "public", deepcopy)
    monkeypatch.setattr(service, "_stop_run", lambda *a, **k: (root / "result.json").write_text(json.dumps({
        "status": "succeeded", "finished_at": "2026-10-11T00:00:01Z", "duration_seconds": 1,
    })))
    assert service.cancel(identifier, "sample_admin", lineage_id="lineage_aaaaaaaaaaaa")["status"] == "succeeded"


def test_failed_lineage_reconcile_keeps_a_retry_wake():
    """Lineage 对账失败后安排一次 300 秒内的重试唤醒

    :module: spec.design-governance"""
    def fail(_):
        raise RuntimeError("temporary failure")

    worker = coordinator.LineageCoordinator(
        reconcile_all=lambda: {}, reconcile_lineage=fail,
        resume_residents=lambda: {}, resume_resident=lambda _: {},
    )
    retries = []
    worker._schedule_lineage_retry = lambda *args: retries.append(args)
    worker._reconcile_one("lineage_111111111111")
    assert retries and retries[0][0] == "lineage_111111111111"
    assert 0 < retries[0][1] <= 300


def test_failed_lineage_reconcile_names_the_lineage_and_logs_its_traceback_once(capsys):
    """Lineage 对账失败的日志写明是哪个 Lineage，并在第一次出现时附带调用栈

    For three days (10-26 to 10-29) one Lineage's reconcile failed every five
    minutes as a bare "KeyError: 'resident not found'", with no Lineage and
    no traceback in the log.

    :module: spec.design-governance"""
    def fail(_):
        raise KeyError("resident not found")

    worker = coordinator.LineageCoordinator(
        reconcile_all=lambda: {}, reconcile_lineage=fail,
        resume_residents=lambda: {}, resume_resident=lambda _: {},
    )
    worker._schedule_lineage_retry = lambda *args: None
    worker._reconcile_one("lineage_111111111111")
    first = capsys.readouterr().err
    assert "coordinator failed: lineage_111111111111: KeyError: 'resident not found'" in first
    assert "Traceback (most recent call last)" in first and "in fail" in first
    assert worker.snapshot()["last_error"] == "lineage_111111111111: KeyError: 'resident not found'"
    worker._recover_resident("resident_111111111111")  # a success between failures clears last_error
    worker._reconcile_one("lineage_111111111111")
    again = capsys.readouterr().err
    assert "coordinator failed: lineage_111111111111" in again and "Traceback" not in again


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_watch_trigger_path_uses_revisions_and_stops_at_terminal(monkeypatch, status):
    """监听触发路径按修订游标输出更新，路径完成或失败后停止"""
    from management_portal.society_service.isa import runs
    observations = []
    def observe(*args, **kwargs):
        observations.append(1)
        return {"path": {"cursor": 3 if len(observations) == 1 else 401,
                         "status": "active" if len(observations) == 1 else status},
                "steps": []}
    monkeypatch.setattr(runs, "_observe", observe)
    monkeypatch.setattr(runs, "wait_for_shutdown", lambda _: False)
    args = {"trigger_path_id": "path", "lineage_id": "lineage", "after_cursor": 2}
    batches = list(runs.watch_trigger_path(SimpleNamespace(argument=lambda key: args.get(key))))
    assert [item["cursor"] for batch in batches for item in batch] == [3, 401]
    assert all(item["change"]["type"] == "trigger_path.updated" for batch in batches for item in batch)
    assert batches[-1][0]["change"]["path"]["status"] == status
    assert len(observations) == 2


AT = "2026-10-14T00:00:00Z"
RESIDENT = "resident_111111111111"


def handoff(lineage_id, message_id):
    from management_portal.society_service import lineage_program_runtime as runtime, trigger_paths
    event = {"id": "event-" + message_id, "type": "manual.triggered", "occurred_at": AT, "payload": {},
             "identity": OperationIdentity.for_actor(RESIDENT).to_dict()}
    path = trigger_paths.begin_event(lineage_id, "source", event, cause_kind="input", cause_id=message_id)
    runtime.register_resident_handoff(lineage_id, message_id=message_id, resident_id=RESIDENT,
        trigger_path_id=path, event=event, source_wire_id="wire")
    trigger_paths.event_settled(path, "source", event["id"])
    return path


def complete_input(message_id):
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution import execution_inputs
    store = get_agent_data_store()
    store.ensure_agent(RESIDENT, actor_ref="sample_admin")
    eid = "execution-" + message_id
    store.begin_execution(execution_id=eid, agent_id=RESIDENT, actor_ref="sample_admin", provider="codex", started_at=AT)
    execution_inputs.attach(store, eid, [{"message_id": message_id, "role": "primary", "accepted_at": AT}])
    store.finish_execution(eid, status="completed", finished_at=AT)
    return eid


def test_profile_retention_and_terminal_cleanup(monkeypatch):
    """投递配置超过一千条也全部保留，交接完成后只删除对应消息的配置"""
    from management_portal.society_service import lineage_program_runtime as runtime
    from tests.cases.test_lineage_catalogs import _lineage_definition, LINEAGE_ID
    from management_portal.society_service.state_store import LineageDataStore
    from management_portal.society_service.lineage_catalog import LineageCatalog
    LineageCatalog(LineageDataStore()).upsert(_lineage_definition())
    profiles = {f"msg_{i:08d}": {"isolated": True} for i in range(1100)}
    with runtime._runtime_transaction(LINEAGE_ID) as body:
        body["delivery_profiles"] = profiles
    assert len(runtime.read_runtime(LINEAGE_ID)["delivery_profiles"]) == len(profiles)
    handoff(LINEAGE_ID, "msg_00000001")
    complete_input("msg_00000001")
    runtime.settle_resident_handoffs(LINEAGE_ID, message_id="msg_00000001", resident_id="resident_111111111111", status="completed")
    assert "msg_00000001" not in runtime.read_runtime(LINEAGE_ID)["delivery_profiles"]
    assert "msg_00000000" in runtime.read_runtime(LINEAGE_ID)["delivery_profiles"]


def test_pending_only_lineage_is_reconciled(monkeypatch):
    """只有待处理 Resident 交接的 Lineage 也会被轮询对账"""
    from management_portal.society_service import lineage_program_polling as polling
    lineage = {"lineage_id": "lineage_111111111111", "program": {}, "edges": []}
    monkeypatch.setattr(polling, "recovery_lineage_ids", lambda **_: [lineage["lineage_id"]])
    calls = []
    monkeypatch.setattr(business_fact_views, 'canonical_view', lambda x, **kw: x)
    monkeypatch.setattr(polling, "_compiled_program", lambda _: {})
    monkeypatch.setattr(polling.lineage_program_runtime, "read_runtime", lambda _: {})
    event = {"id": "pending-recovery", "type": "manual.triggered", "occurred_at": AT,
             "identity": OperationIdentity.for_actor(RESIDENT).to_dict()}
    monkeypatch.setattr(polling.lineage_program_runtime, "pending_resident_handoffs", lambda _: [{"event": event}])
    ports = polling.ProgramPollingPorts(
        lineage_catalog_store=lambda: SimpleNamespace(get=lambda _: lineage),
        lineage_runtime_actor=lambda _: "portal", read_lineage_state=lambda _: lineage,
        read_resident_activity_snapshot=lambda _: {}, ensure_manager=lambda *_: None,
        reconcile_lineage_system_events=lambda *a, **k: {},
        reconcile_lineage_runtime=lambda lid, actor, **kwargs: calls.append(lid) or {},
    )
    polling.poll_all_system_events(ports=ports)
    assert calls == [lineage["lineage_id"]]


def test_departed_resident_terminal_input_settles_invocation(monkeypatch):
    """Resident 已不在 Lineage 图中时，其输入结束后对账仍把交接结算为 completed"""
    from contextlib import nullcontext
    from unittest.mock import Mock
    from management_portal.society_service import lineage_program_runtime as runtime
    from management_portal.society_service import lineage_program_terminal_reconciliation as terminal
    from management_portal.society_service import resident_conversation_stream
    from management_portal.society_service.lineage_catalog import LineageCatalog
    from management_portal.society_service.state_store import LineageDataStore
    from tests.cases.test_lineage_catalogs import _lineage_definition, LINEAGE_ID
    graph = _lineage_definition()
    LineageCatalog(LineageDataStore()).upsert(graph)
    handoff(LINEAGE_ID, "msg_departed")
    complete_input("msg_departed")
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: SimpleNamespace(
        input_state=lambda rid, mid: {"state": "completed", "updated_at": "2026-10-11T00:00:00Z"}))
    monkeypatch.setattr(terminal, "_compiled_program", lambda _: {})
    monkeypatch.setattr(terminal, "reconcile_terminal_absorption_edges", lambda *a, **kw: 0)
    state = Mock(lineage_scope=lambda _: nullcontext(), read_lineage=lambda *a, **kw: graph,
                 ensure_manager=lambda *_: None)
    ports = terminal.ProgramTerminalPorts(state=state, resident=Mock(),
        directory=Mock(lineage_execution_authority=lambda _: nullcontext()),
        flow=Mock(drain_program_event_wire_backlog=lambda *_: {}))
    result = terminal.reconcile_runtime_with_authority(LINEAGE_ID, "portal", ports=ports)
    assert result["recovered_handoff_count"] == 1
    assert runtime.pending_resident_handoffs(LINEAGE_ID) == []
    assert runtime.resident_handoff(LINEAGE_ID, "resident_111111111111", "msg_departed")["status"] == "completed"


def test_deleting_lineage_cleans_work_but_retains_trigger_paths():
    """删除 Lineage 清掉投递配置、事件进度和共享状态，触发路径保留并标为 cancelled"""
    from management_portal.society_service import lineage_program_runtime as runtime, lineage_graph_state
    from management_portal.society_service.state_store import LineageDataStore
    from management_portal.society_service.lineage_catalog import LineageCatalog
    from tests.cases.test_lineage_catalogs import _lineage_definition, LINEAGE_ID
    store = LineageDataStore()
    LineageCatalog(store).upsert(_lineage_definition())
    path = handoff(LINEAGE_ID, "msg_remove")
    runtime.record_delivery_profile(LINEAGE_ID, "msg_remove", {"isolated": True})
    runtime.claim_event_occurrence(LINEAGE_ID, "event_source_remove", "one", max_fires=None)
    lineage_graph_state.update_cell(LINEAGE_ID, "counter", 1)
    with store.database.read() as connection:
        before = connection.execute("SELECT count(*) AS n FROM trigger_paths WHERE lineage_id=%s", (LINEAGE_ID,)).fetchone()["n"]
    store.remove_lineage(LINEAGE_ID, deleted_by="sample_admin")
    with store.database.read() as connection:
        for table in ("resident_delivery_profiles", "event_source_progress", "lineage_shared_state"):
            assert connection.execute(f"SELECT count(*) AS n FROM {table} WHERE lineage_id=%s", (LINEAGE_ID,)).fetchone()["n"] == 0
        assert connection.execute("SELECT count(*) AS n FROM trigger_paths WHERE lineage_id=%s", (LINEAGE_ID,)).fetchone()["n"] == before
        assert connection.execute("SELECT status FROM trigger_paths WHERE trigger_path_id=%s", (path,)).fetchone()[0] == "cancelled"
