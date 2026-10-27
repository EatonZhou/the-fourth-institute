"""Recovery discovery reads owned pending records, never idle aggregates."""
import json
import threading

import pytest

from management_portal.society_service import (
    graph_storage, lineage_catalog, lineage_program_polling as polling,
    program_work_storage, resident_catalog, resident_execution_work, resident_storage,
)
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_conversation_projection_authority import RESIDENT, _repository

LINEAGE = "lineage_aaaaaaaaaaaa"
OTHER = "lineage_bbbbbbbbbbbb"
AT = "2026-10-15T00:00:00Z"


def add_lineage(store, identifier):
    store.put_lineage_definition(identifier, {
        "lineage_id": identifier, "title": "Recovery candidates",
        "nodes": [], "edges": [], "program": {},
    }, {"lineage_id": identifier})


@pytest.fixture
def store():
    store = get_lineage_data_store()
    add_lineage(store, LINEAGE)
    add_lineage(store, OTHER)
    return store


@pytest.mark.parametrize("kind", ["delivery", "expiry", "operator", "path", "absorption"])
def test_only_pending_lineages_are_discovered_and_settlement_removes_them(store, kind, monkeypatch):
    """仅发现有待处理工作的 Lineage 且结算后移出候选"""
    monkeypatch.setattr(lineage_catalog.LineageCatalog, "snapshot",
                        lambda *_: pytest.fail("Recovery loaded all definitions"))
    with store.database.transaction() as c:
        c.execute("INSERT INTO operator_work VALUES(%s,'idle','{}')", (OTHER,))
        if kind == "delivery":
            table = "pending_event_deliveries"
            c.execute("INSERT INTO pending_event_deliveries VALUES(%s,'wire','claim',0,'{}')", (LINEAGE,))
        elif kind == "expiry":
            table = "pending_expiry_notices"
            c.execute("INSERT INTO pending_expiry_notices VALUES(%s,'wire','cursor',0,'{}')", (LINEAGE,))
        elif kind == "operator":
            table = "operator_work"
            c.execute("INSERT INTO operator_work VALUES(%s,'busy',%s)",
                      (LINEAGE, json.dumps({"pending_deliveries": {"delivery": {"event": {}}}})))
        elif kind == "path":
            table = "trigger_paths"
            c.execute("""INSERT INTO trigger_paths(trigger_path_id,lineage_id,root_kind,occurrence_id,
                root_node_id,cause_kind,cause_id,occurred_at,input_json,status)
                VALUES('path',%s,'event','event','source','timer','timer',%s,'{}','active')""", (LINEAGE, AT))
        else:
            table = "context_absorption_work"
            c.execute("INSERT INTO lineage_nodes(node_id,lineage_id,node_type) VALUES('node',%s,'seed')", (LINEAGE,))
            c.execute("""INSERT INTO lineage_edges(edge_id,lineage_id,edge_type,source_node_id,target_node_id)
                         VALUES('edge',%s,'absorption','node','node')""", (LINEAGE,))
            c.execute("INSERT INTO context_absorption_work VALUES(%s,'edge',%s)",
                      (LINEAGE, json.dumps({"target_message_id": "message"})))
    assert polling.recovery_lineage_ids(include_observations=False) == [LINEAGE]
    with store.database.transaction() as c:
        if kind == "path":
            c.execute("UPDATE trigger_paths SET status='completed',finished_at=%s", (AT,))
        else:
            c.execute(f"DELETE FROM {table} WHERE lineage_id=%s", (LINEAGE,))
    assert polling.recovery_lineage_ids(include_observations=False) == []


def test_bootstrap_finds_new_timer_but_ignores_paused_sources_and_periodic_recovery(store):
    """启动发现新定时器且周期恢复跳过暂停源的观察工作"""
    with store.database.transaction() as c:
        for identifier, source in ((LINEAGE, "enabled"), (OTHER, "paused")):
            c.execute("INSERT INTO lineage_nodes(node_id,lineage_id,node_type) VALUES(%s,%s,'event_source')", (source, identifier))
            c.execute("INSERT INTO lineage_components(node_id,config_json) VALUES(%s,%s)",
                      (source, json.dumps({"event_type": "time.reached"})))
        c.execute("INSERT INTO event_source_progress VALUES(%s,'paused',%s)", (OTHER, '{"paused":true}'))
    assert polling.recovery_lineage_ids(include_observations=False) == []
    assert polling.recovery_lineage_ids(include_observations=True) == [LINEAGE]
    # A paused source may still have accepted deliveries. Recover those only.
    with program_work_storage.transaction(store.database, OTHER) as state:
        state["event_wires"] = {"wire": {"pending_events": [{"claim_token": "claim", "event": {}}]}}
    assert polling.recovery_lineage_ids(include_observations=False) == [OTHER]
    with store.database.read() as c:
        assert graph_storage.observation_lineage_ids(c, polling.AUTOMATIC_EVENT_TYPES) == {LINEAGE}


def test_permanent_handoff_is_retained_and_excluded_without_hiding_new_work(store):
    """阻塞交接保留且不影响新工作发现"""
    from management_portal.society_service import trigger_path_storage as paths, trigger_paths
    event = {"id": "legacy", "type": "manual.triggered", "payload": {}, "occurred_at": AT}
    with store.database.transaction() as c:
        path = paths.begin(c, lineage_id=LINEAGE, root_kind="event", occurrence_id="legacy",
                           root_node_id="source", cause_kind="input", cause_id="input", occurred_at=AT, input=event)
        step = paths.admit_step(c, trigger_path_id=path, node_id=RESIDENT, kind="resident",
                                activation_key="input", created_at=AT, input_message_id="input")
    item = {"trigger_path_id": path, "step_id": step, "message_id": "input", "event": event}
    assert trigger_paths.block_unrecoverable_inputs(LINEAGE, [item]) == 1
    assert trigger_paths.block_unrecoverable_inputs(LINEAGE, [item]) == 0
    assert polling.recovery_lineage_ids(include_observations=False) == []
    with store.database.transaction() as c:
        assert paths.read(c, path)["input"] == event
        new_step = paths.admit_step(c, trigger_path_id=path, node_id="another", kind="resident",
                                    activation_key="new", created_at=AT, input_message_id="new")
        assert new_step != step
    assert polling.recovery_lineage_ids(include_observations=False) == [LINEAGE]


def test_poll_parks_missing_identity_once_and_retries_transient_failures(store, monkeypatch):
    """恢复隔离永久失败并继续重试临时故障"""
    from tests.cases import society_service_test_support as support
    support._install_isolated_portal_access(monkeypatch)
    from types import SimpleNamespace
    from management_common.principal import OperationIdentity
    from management_portal.society_service import lineage_program_runtime as runtime
    event = {"id": "legacy", "type": "manual.triggered", "payload": {}, "occurred_at": AT}
    with program_work_storage.transaction(store.database, LINEAGE) as state:
        state["event_wires"] = {"wire": {"pending_events": [{"claim_token": "claim", "event": event}]}}
    def read(lid):
        with store.database.read() as c:
            return program_work_storage.read(c, lid)
    monkeypatch.setattr(runtime, "read_runtime", read)
    monkeypatch.setattr(runtime, "pending_resident_handoffs", lambda _: [])
    calls = []
    def recover(*args, **kw):
        calls.append(args)
        raise OSError("temporary outage")
    ports = SimpleNamespace(lineage_catalog_store=lambda: SimpleNamespace(get=lambda _: {"program": {}}),
                            reconcile_lineage_runtime=recover)
    first = polling.poll_all_system_events(ports=ports, recovery_only=True)
    second = polling.poll_all_system_events(ports=ports, recovery_only=True)
    assert first["blocked"] == 1 and first["failed"] == 0
    assert second["checked"] == 0 and not calls
    with program_work_storage.transaction(store.database, LINEAGE) as state:
        state["event_wires"]["wire"]["pending_events"].append({"claim_token": "new", "event": {
            **event, "id": "new", "identity": OperationIdentity.for_actor("sample_admin").to_dict()}})
    for _ in range(2):
        result = polling.poll_all_system_events(ports=ports, recovery_only=True)
        assert result["failed"] == 1, result
        assert "temporary outage" in str(result), result
    assert len(calls) == 2


def test_resident_preparation_and_unacknowledged_terminal_work_are_candidates(tmp_path, monkeypatch):
    """Resident 准备工作与未确认终态均进入恢复候选"""
    store = _repository(tmp_path).state_store
    add_lineage(store, LINEAGE)
    with store.database.transaction() as c:
        c.execute("INSERT INTO lineage_nodes(node_id,lineage_id,node_type) VALUES(%s,%s,'resident')", (RESIDENT, LINEAGE))
    monkeypatch.setattr(resident_catalog.get_catalog(), "all",
                        lambda *_: pytest.fail("Recovery loaded idle Residents"))
    assert resident_execution_work.pending_records() == []
    work = {"execution_id": "execution-preparing", "actor": "sample_admin", "user_message_id": "input"}
    store.put_resident_execution_record(RESIDENT, work)
    assert [r["resident_id"] for r in resident_execution_work.pending_records()] == [RESIDENT]
    store.put_resident_execution_record(RESIDENT, {**work, "submitted_at": AT, "outcome": "completed", "finished_at": AT})
    assert [r["resident_id"] for r in resident_execution_work.pending_records()] == [RESIDENT]
    store.acknowledge_resident_execution(RESIDENT, work["execution_id"])
    assert resident_execution_work.pending_records() == []


def test_failure_discovery_uses_latest_input_due_time_and_manual_controls(tmp_path):
    """失败恢复按最新输入到期时间与人工控制筛选候选"""
    repository = _repository(tmp_path)
    store = repository.state_store
    add_lineage(store, LINEAGE)
    with store.database.transaction() as c:
        c.execute("INSERT INTO conversation_states(resident_id) VALUES(%s)", (RESIDENT,))
        c.execute("INSERT INTO conversation_input_states(resident_id,message_id,state,updated_at) VALUES(%s,'old','failed',%s),(%s,'latest','failed',%s)", (RESIDENT, AT, RESIDENT, AT))
        c.execute("INSERT INTO resident_mailbox_progress VALUES(%s,%s,%s)",
                  (LINEAGE, RESIDENT, json.dumps({"checkpoints": [{"message_id": "old"}, {"message_id": "latest"}]})))
        c.execute("INSERT INTO resident_observation_cache(resident_id,last_error) VALUES(%s,'timeout')", (RESIDENT,))
    def candidates():
        with store.database.read() as c:
            return resident_storage.failed_work_resident_ids(c, checked_before=1000)
    def recovery(value):
        with store.database.transaction() as c:
            c.execute("INSERT INTO resident_recovery(resident_id,work_recovery_json) VALUES(%s,%s) ON CONFLICT(resident_id) DO UPDATE SET work_recovery_json=excluded.work_recovery_json",
                      (RESIDENT, json.dumps({"latest": value})))
    assert candidates() == [RESIDENT]
    recovery({"checked_at": 1100})
    assert candidates() == []
    recovery({"disabled": True})
    assert candidates() == []
    recovery({"owner_notified": True})
    assert candidates() == [RESIDENT]  # Notification is not a retry-disable flag.
    recovery({"checked_at": ""})
    assert candidates() == [RESIDENT]  # Sparse old ledgers cannot abort discovery.
    with store.database.transaction() as c:
        c.execute("UPDATE residents SET intake_open=false WHERE resident_id=%s", (RESIDENT,))
    assert candidates() == []
    with store.database.transaction() as c:
        c.execute("UPDATE residents SET intake_open=true WHERE resident_id=%s", (RESIDENT,))
        c.execute("UPDATE conversation_input_states SET state='completed' WHERE message_id='latest'")
    assert candidates() == []  # An old failed input cannot restart the Resident.
    with store.database.transaction() as c:
        c.execute("UPDATE conversation_input_states SET state='failed' WHERE message_id='latest'")
        c.execute("UPDATE resident_observation_cache SET last_error='' WHERE resident_id=%s", (RESIDENT,))
    store.put_resident_execution_record(RESIDENT, {"execution_id": "failed", "actor": "sample_admin", "submitted_at": AT, "outcome": "failed"})
    assert candidates() == [RESIDENT]


def test_restart_and_quota_policies_are_discovered_without_execution_work(tmp_path):
    """无执行工作时仍可发现延期重启与额度覆盖策略"""
    store = _repository(tmp_path).state_store
    with store.database.transaction() as c:
        c.execute("INSERT INTO resident_sessions(resident_id,session_restart_json) VALUES(%s,%s)", (RESIDENT, '{"deferred":{"request_id":"restart"}}'))
        c.execute("INSERT INTO resident_execution_configuration(resident_id,quota_reserve_override_json) VALUES(%s,%s)", (RESIDENT, '{"reserve":1}'))
        assert resident_storage.deferred_restart_resident_ids(c) == [RESIDENT]
        assert resident_storage.quota_override_resident_ids(c) == [RESIDENT]
        assert c.execute("SELECT count(*) FROM resident_execution_work").fetchone()[0] == 0
        c.execute("UPDATE resident_sessions SET session_restart_json='{}'")
        c.execute("UPDATE resident_execution_configuration SET quota_reserve_override_json='{}'")
        assert resident_storage.deferred_restart_resident_ids(c) == []
        assert resident_storage.quota_override_resident_ids(c) == []


def test_periodic_program_recovery_does_not_block_resident_notifications():
    """周期程序恢复阻塞时仍可处理 Resident 通知"""
    from management_portal.society_service.coordinator import LineageCoordinator
    entered, release, resumed = threading.Event(), threading.Event(), threading.Event()
    bootstraps = []
    def recover():
        entered.set()
        release.wait(3)
        return {"checked": 1}
    coordinator = LineageCoordinator(
        resume_residents=dict, resume_resident=lambda _: resumed.set() or {},
        reconcile_all=lambda: bootstraps.append(1) or {}, reconcile_lineage=lambda _: {},
        recover_program=recover, recovery_seconds=0.05,
    )
    coordinator.start()
    try:
        assert entered.wait(2)
        coordinator.notify_resident(RESIDENT)
        assert resumed.wait(1)
        assert bootstraps == [1]
    finally:
        release.set()
        coordinator.stop()
