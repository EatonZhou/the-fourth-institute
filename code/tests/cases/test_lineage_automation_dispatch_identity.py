"""Automatic observations and saved activations have independent live authority."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from management_common.operation_identity import current_operation_identity, operation_identity_scope
from management_common.principal import OperationIdentity
from management_portal.society_service import (
    lineage_automation_identity as automation,
    lineage_instruction_context as contexts,
    lineage_primitives as primitives,
    lineage_program_backlog as backlog,
    lineage_program_event_dispatch as dispatch,
    lineage_program_effect_execution as effects,
    lineage_program_polling as polling,
    lineage_program_terminal_reconciliation as terminal,
    lineage_program_runtime as runtime,
    trigger_paths,
)
from tests.cases.society_service_test_support import _install_isolated_portal_access

USER = "sample_admin"
OTHER = "sample_guest"
RESIDENT = "resident_aaaaaaaaaaaa"
LINEAGE = "lineage_aaaaaaaaaaaa"
SOURCE = "event_source_aaaaaaaaaaaa"
WIRE = "wire_aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def live_users(monkeypatch):
    _install_isolated_portal_access(monkeypatch)
    return contexts.get_portal_access_store()


def event(actor=USER, *, initiator="", kind="manual.triggered"):
    return primitives.make_event(kind, identity=OperationIdentity.for_actor(actor, initiated_by=initiator).to_dict())


def test_new_message_arrival_author_does_not_delegate_human_authority():
    from management_portal.society_service import lineage_message_events
    identity = lineage_message_events._sender_identity(RESIDENT, USER)
    arrival = primitives.make_event("resident.message_received", payload={"author_id": USER}, identity=identity)
    assert contexts.event_context(arrival, RESIDENT).caller == RESIDENT
    assert arrival.payload["author_id"] == USER


def test_automatic_scope_replaces_and_restores_the_ambient_identity():
    """自动作用域内改用指定身份，退出后恢复外层身份"""
    outer_identity = OperationIdentity.for_actor(OTHER)
    with operation_identity_scope(outer_identity):
        with contexts.automatic_scope(USER):
            assert current_operation_identity() == OperationIdentity.for_actor(USER)
            assert contexts.current_event_identity() is None
        assert current_operation_identity() == outer_identity
    with operation_identity_scope(outer_identity):
        with contexts.automatic_scope("portal"):
            assert current_operation_identity() == OperationIdentity.for_actor("portal")
        assert current_operation_identity() == outer_identity


def test_derived_initiator_is_preserved():
    """事件作用域保留派生事件的发起人，无身份的事件被拒绝"""
    original = event(RESIDENT, initiator=USER)
    with operation_identity_scope(OperationIdentity.for_actor(OTHER)):
        with contexts.event_scope(original, RESIDENT):
            assert current_operation_identity().to_dict() == original.identity
            assert contexts.event_context(original, RESIDENT).caller == USER
            assert contexts.resident_context(LINEAGE, original, RESIDENT, RESIDENT).caller == USER
            assert terminal._terminal_dispatcher({}, RESIDENT, None) == (RESIDENT, original.identity)
    with pytest.raises(PermissionError, match="no verified identity"):
        contexts.event_context(replace(original, identity={}), USER)


@pytest.mark.parametrize('initiator', ['portal', 'resident_bbbbbbbbbbbb', ''])
def test_non_user_continuation_uses_actual_resident_and_saved_identity(initiator):
    """无 User 的后续事件按实际 Resident 权限执行

    无 User 的 Resident 后续事件使用实际执行者，重放不借用环境 User，并保留事件原始因果身份。

    :design: decision_ae422b92c6f3, decision_5ff0cb51ed84
    """
    original = event(RESIDENT, initiator=initiator, kind='resident.work_finished')
    with contexts.event_scope(original, RESIDENT):
        context = contexts.event_context(original, RESIDENT)
        assert context.caller == RESIDENT
        assert current_operation_identity().to_dict() == original.identity


@pytest.mark.parametrize('initiator', [USER, RESIDENT])
def test_service_forwarded_completion_preserves_verified_initiator(initiator):
    """服务转发完成事件保留已验证发起者

    Portal 转发完成事件时保留 User 或 Resident 发起者，不能升级为服务权限。

    :design: decision_ae422b92c6f3, decision_5ff0cb51ed84
    """
    original = event('portal', initiator=initiator, kind='resident.work_finished')
    with contexts.event_scope(original, 'portal'):
        assert contexts.event_context(original, 'portal').caller == initiator
        assert current_operation_identity().to_dict() == original.identity


@pytest.mark.parametrize("packet", [False, True])
def test_replay_keeps_saved_identity_and_rechecks_current_permissions(packet):
    """重放事件沿用保存的身份，每次投递前重新检查当前权限"""
    original = event(OTHER)
    seen = []
    allowed = [True]
    def authorize(actor):
        seen.append(("authorize", actor))
        assert current_operation_identity().to_dict() == original.identity
        if not allowed[0]:
            raise PermissionError("credential revoked")
        return {"actor": actor}
    ports = backlog.ReplayDelivery(
        authorize=authorize,
        has_registered_invocation=lambda *args: False,
        packet=lambda lineage, target, saved, invocation, actor: {"status": "delivered"},
        event=lambda source, saved, actor, **kwargs: {"matched_count": 1, "results": [
            {"wire_id": WIRE, "status": "delivered"}]},
    )
    pending = {"event": original.to_dict(), "invocation": {"packet": {}}} if packet else {"event": original.to_dict()}
    with operation_identity_scope(OperationIdentity.for_actor(USER)):
        result = backlog.deliver_claimed(WIRE, SOURCE, {"target_id": RESIDENT}, pending, ports)
        assert result["results"][0]["status"] == "delivered"
        allowed[0] = False
        with pytest.raises(PermissionError, match="credential revoked"):
            backlog.deliver_claimed(WIRE, SOURCE, {"target_id": RESIDENT}, pending, ports)
    assert seen == [("authorize", OTHER), ("authorize", OTHER)]


@pytest.mark.parametrize("missing_identity", [False, True])
def test_replay_rejects_disabled_user_even_with_empty_instruction_protection(live_users, monkeypatch, missing_identity):
    """保存的用户已停用或身份缺失时，即使指令没有保护项也拒绝重放"""
    original = event()
    if missing_identity:
        original = replace(original, identity={})
    else:
        monkeypatch.setattr(live_users, "is_active", lambda actor: False)
    calls = []
    # This intentionally represents an instruction whose protection collection is empty.
    ports = backlog.ReplayDelivery(
        authorize=lambda actor: {},
        has_registered_invocation=lambda *args: False,
        packet=lambda *args: calls.append(args),
        event=lambda *args, **kwargs: calls.append(args),
    )
    with pytest.raises(PermissionError, match="no verified identity|inactive"):
        backlog.deliver_claimed(WIRE, SOURCE, {"target_id": RESIDENT}, {"event": original.to_dict()}, ports)
    assert calls == []


def lineage(kind=None, *, configured=True, absorption=False, identifier=LINEAGE):
    result = {"lineage_id": identifier, "program": {"components": [], "wires": []}, "nodes": [], "edges": []}
    if kind:
        result["program"]["components"] = [{"id": SOURCE, "kind": "event_source", "config": {"event_type": kind}}]
    if configured:
        result["leader_resident_id"] = RESIDENT
        result["nodes"] = [{"id": RESIDENT}]
    if absorption:
        result["edges"] = [{"type": "absorption", "absorption": {"target_message_id": "message_saved"}}]
    return result


def polling_ports(monkeypatch, lineages, *, stored=None, handoffs=None):
    snapshots = stored or {}
    calls = []
    scans = []
    monkeypatch.setattr(polling, "shutdown_requested", lambda: False)
    monkeypatch.setattr(polling, "recovery_lineage_ids", lambda **_: [x["lineage_id"] for x in lineages])
    monkeypatch.setattr(runtime, "read_runtime", lambda identifier: snapshots.get(identifier, {}))
    monkeypatch.setattr(runtime, "pending_resident_handoffs", lambda identifier: (handoffs or {}).get(identifier, []))
    monkeypatch.setattr(polling.business_fact_views, "canonical_view", lambda data, **kwargs: data)
    monkeypatch.setattr(polling, "_compiled_program", lambda data: {"event_sources": {}})
    def reconcile(identifier, actor, **kwargs):
        calls.append(("observe", identifier, actor, kwargs))
        assert current_operation_identity().principal.id == actor
        return {"event_count": 1}
    def recover(identifier, actor, **kwargs):
        calls.append(("recover", identifier, actor, kwargs))
        assert current_operation_identity().principal.id == actor
        return {"checked_node_count": 1}
    def scan(ids):
        scans.append(ids)
        return {"shared": True}
    ports = polling.ProgramPollingPorts(
        lineage_catalog_store=lambda: SimpleNamespace(get=lambda identifier: next(x for x in lineages if x["lineage_id"] == identifier)),
        lineage_runtime_actor=automation.runtime_actor,
        read_lineage_state=lambda identifier: next(x for x in lineages if x["lineage_id"] == identifier),
        read_resident_activity_snapshot=scan,
        ensure_manager=lambda *args: None,
        reconcile_lineage_system_events=reconcile, reconcile_lineage_runtime=recover,
    )
    return ports, calls, scans


@pytest.mark.parametrize("kind", [None, "manual.triggered", "effect.succeeded"])
def test_empty_and_manual_lineages_do_not_require_automatic_configuration(monkeypatch, kind):
    """没有事件源或只有手动、Effect 事件源的 Lineage 不要求配置自动运行身份"""
    ports, calls, scans = polling_ports(monkeypatch, [lineage(kind, configured=False)])
    result = polling.poll_all_system_events(ports=ports)
    assert result["failed"] == 0 and result["checked"] == 1
    assert calls == scans == []


@pytest.mark.parametrize("kind", [None, "manual.triggered", "time.reached"])
def test_saved_recovery_is_independent_of_missing_automation_configuration(monkeypatch, kind):
    """未配置自动运行身份时，已保存事件仍按原身份恢复，事件源状态不变"""
    saved = event(OTHER)
    stored = {LINEAGE: {"event_sources": {SOURCE: {"paused": True, "cursor": "keep", "fire_count": 7}},
                        "event_wires": {WIRE: {"pending_events": [{"event": saved.to_dict()}]}}}}
    before = deepcopy(stored)
    ports, calls, scans = polling_ports(monkeypatch, [lineage(kind, configured=False)], stored=stored)
    result = polling.poll_all_system_events(ports=ports)
    assert calls == [("recover", LINEAGE, OTHER, {"recovery_only": True, "drain": True})]
    assert result["recovered"] == 1
    assert result["failed"] == 0
    assert stored == before


def test_all_idle_observations_share_one_activity_scan(monkeypatch):
    """多个 residents.all_idle 观测共用一次 Resident 活动扫描"""
    definitions = [lineage("residents.all_idle"), lineage("residents.all_idle", identifier="lineage_bbbbbbbbbbbb")]
    ports, calls, scans = polling_ports(monkeypatch, definitions)
    assert polling.poll_all_system_events(ports=ports)["failed"] == 0
    assert scans == [None]
    assert calls[0][3]["resident_activity_snapshot"] is calls[1][3]["resident_activity_snapshot"]


@pytest.mark.parametrize("configured", [False, True])
def test_absorption_only_lineages_require_leader_for_new_work(monkeypatch, configured):
    """新吸收工作使用 Leader；没有 Leader 时阻断且不重试。"""
    ports, calls, scans = polling_ports(monkeypatch, [lineage(configured=configured, absorption=True)])
    result = polling.poll_all_system_events(ports=ports)
    assert result["failed"] == 0
    assert result["blocked"] == (0 if configured else 1)
    assert result["retry_lineage_ids"] == result["failure_samples"] == []
    assert calls == ([("recover", LINEAGE, RESIDENT, {})] if configured else [])
    assert bool(result["blocked_lineages"]) is (not configured)


@pytest.mark.parametrize("unavailable", ["missing", "non_member", "non_resident"])
def test_automatic_configuration_blocks_without_retry_then_recovers(monkeypatch, live_users, unavailable):
    """缺失、离图或非 Resident 的 Leader 阻断新观测，修正后恢复。"""
    definition = lineage("time.reached", configured=unavailable != "missing")
    if unavailable == "non_member":
        definition["nodes"] = []
    elif unavailable == "non_resident":
        definition["leader_resident_id"] = USER
    definition["run_as_principal"] = {"kind": "user", "id": USER}
    # Neither the reader nor creator metadata can supply unattended authority.
    definition.update(created_by=USER)
    before = deepcopy(definition)
    ports, calls, scans = polling_ports(monkeypatch, [definition])
    with operation_identity_scope(OperationIdentity.for_actor(OTHER)):
        with pytest.raises(automation.AutomationConfigurationError) as caught:
            automation.runtime_actor(definition)
        error = str(caught.value)
        direct = polling.reconcile_system_events_as_service(LINEAGE, ports=ports)
        result = polling.poll_all_system_events(ports=ports)
    assert "Leader" in error
    assert direct == {"lineage_id": LINEAGE, "status": "blocked", "error": error,
                      "event_count": 0, "events": []}
    assert result["checked"] == 1 and result["blocked"] == 1
    assert result["failed"] == result["emitted"] == result["recovered"] == 0
    assert result["blocked_lineages"] == {LINEAGE: error}
    assert result["retry_lineage_ids"] == result["failure_samples"] == []
    assert definition == before and calls == scans == []

    definition["leader_resident_id"] = RESIDENT
    definition["nodes"] = [{"id": RESIDENT}]
    result = polling.poll_all_system_events(ports=ports)
    assert result["blocked"] == result["failed"] == 0
    assert result["blocked_lineages"] == {} and result["emitted"] == 1
    assert calls == [("observe", LINEAGE, RESIDENT, {"resident_activity_snapshot": None})]


@pytest.mark.parametrize("error_type", [OSError, PermissionError])
def test_operational_failure_is_retryable_not_configuration_blocked(monkeypatch, error_type):
    """身份有效后的运行故障记为可重试失败，不记为配置阻断"""
    ports, calls, scans = polling_ports(monkeypatch, [lineage("time.reached")])

    def fail(*args, **kwargs):
        raise error_type("operation failed after valid identity")

    ports = replace(ports, reconcile_lineage_system_events=fail)
    with pytest.raises(error_type, match="operation failed"):
        polling.reconcile_system_events_as_service(LINEAGE, ports=ports)
    result = polling.poll_all_system_events(ports=ports)
    assert result["failed"] == 1 and result["blocked"] == 0
    assert result["blocked_lineages"] == {}
    assert result["retry_lineage_ids"] == [LINEAGE]
    assert result["failure_samples"] == [{"lineage_id": LINEAGE,
        "error_type": error_type.__name__, "error": "operation failed after valid identity"}]


@pytest.mark.parametrize("has_leader", [False, True])
def test_accepted_event_recovers_even_when_new_observations_have_no_leader(monkeypatch, has_leader):
    """旧事件按保存身份恢复；新观测独立使用 Leader，空图只阻断新观测。"""
    saved = event(OTHER)
    stored = {LINEAGE: {"event_wires": {WIRE: {"pending_events": [{"event": saved.to_dict()}]}}}}
    before = deepcopy(stored)
    ports, calls, scans = polling_ports(monkeypatch, [lineage("time.reached", configured=has_leader)], stored=stored)
    result = polling.poll_all_system_events(ports=ports)
    expected = [("recover", LINEAGE, OTHER, {"recovery_only": True, "drain": True})]
    if has_leader:
        expected.append(("observe", LINEAGE, RESIDENT, {"resident_activity_snapshot": None}))
    assert calls == expected
    assert result["recovered"] == 1 and result["emitted"] == int(has_leader)
    assert result["failed"] == 0 and result["blocked"] == int(not has_leader)
    assert bool(result["blocked_lineages"]) is (not has_leader)
    assert result["retry_lineage_ids"] == result["failure_samples"] == []
    assert stored == before and scans == []


def test_periodic_recovery_replays_saved_event_without_observing_active_timer(monkeypatch):
    """周期恢复只按保存的身份重放待处理事件，不观测仍在运行的定时器"""
    saved = event(OTHER)
    ports, calls, scans = polling_ports(monkeypatch, [lineage("time.reached")], stored={
        LINEAGE: {"event_wires": {WIRE: {"pending_events": [{"event": saved.to_dict()}]}}},
    })
    result = polling.poll_all_system_events(ports=ports, recovery_only=True)
    assert result["failed"] == 0
    assert calls == [("recover", LINEAGE, OTHER, {"recovery_only": True, "drain": True})]
    assert scans == []


@pytest.mark.parametrize("complete", [True, False])
def test_recovery_drains_each_lineage_once_per_pass(monkeypatch, complete):
    """每个身份各自结算交接，排空完成一次后同一轮里其他身份不再重复排空"""
    ports, _calls, _scans = polling_ports(monkeypatch, [lineage("time.reached")], stored={
        LINEAGE: {"event_wires": {WIRE: {"pending_events": [
            {"event": event(OTHER).to_dict()}, {"event": event(USER).to_dict()},
        ]}}},
    })
    recoveries = []

    def recover(_identifier, actor, **kwargs):
        recoveries.append((actor, kwargs["drain"]))
        return {"program_event_backlog": {"complete": complete} if kwargs["drain"] else {}}

    result = polling.poll_all_system_events(ports=replace(ports, reconcile_lineage_runtime=recover), recovery_only=True)
    assert result["failed"] == 0
    # A drain that stopped before it was done leaves the next identity to go on.
    assert recoveries == [(OTHER, True), (USER, not complete)]


def test_paused_observation_notification_needs_no_automatic_principal(monkeypatch):
    """事件源已暂停时对账不需要自动运行身份，也不触发观测"""
    ports, calls, scans = polling_ports(monkeypatch, [lineage("time.reached", configured=False)],
                                       stored={LINEAGE: {"event_sources": {SOURCE: {"paused": True}}}})
    assert polling.reconcile_system_events_as_service(LINEAGE, ports=ports)["event_count"] == 0
    assert calls == scans == []


def test_recovery_only_does_not_observe_new_completion_facts(monkeypatch):
    """recovery_only 对账只排空已有积压，不观测新的 Resident 完成事实"""
    current = lineage("resident.work_finished", configured=False)
    current["nodes"] = [{"id": RESIDENT}]
    saved = deepcopy(current)
    calls = []
    def forbidden(*args, **kwargs):
        pytest.fail("recovery_only must not create new observations or absorption work")
    monkeypatch.setattr(terminal, "_compiled_program", lambda data: {})
    monkeypatch.setattr(terminal.lineage_program, "resident_completion_subscriptions",
                        lambda *args: [(SOURCE, {RESIDENT}, False, ["consumer"])])
    monkeypatch.setattr(runtime, "pending_resident_handoffs", lambda identifier: [])
    monkeypatch.setattr(terminal, "reconcile_terminal_absorption_edges", forbidden)
    ports = SimpleNamespace(
        state=SimpleNamespace(lineage_scope=lambda *args: nullcontext(), read_lineage=lambda *args, **kwargs: current,
                              ensure_manager=lambda *args: None),
        resident=SimpleNamespace(node_has_active_execution=forbidden, latest_completed_message_reference=forbidden,
                                 node_resident_id=lambda node: node["id"], handle_resident_message_completion=forbidden,
                                 resident_input_reconciliation_state=forbidden, handle_resident_message_terminal=forbidden),
        flow=SimpleNamespace(drain_program_event_wire_backlog=lambda *args: calls.append(args) or {}),
        directory=SimpleNamespace(lineage_execution_authority=lambda _: nullcontext()),
    )
    result = terminal.reconcile_runtime_with_authority(LINEAGE, OTHER, recovery_only=True, ports=ports)
    assert result["checked_node_count"] == result["program_created_count"] == 0
    assert calls == [(LINEAGE, OTHER)]
    assert current == saved


def test_derived_effect_and_runtime_events_retain_original_identity():
    """Effect 结果事件和运行时派生事件保留原始事件的身份"""
    original = event(RESIDENT, initiator=USER)
    invocation = primitives.make_effect_invocation(original)
    outcome = effects._outcome_event("effect_aaaaaaaaaaaa", "RunScript", invocation,
                                    event_type="effect.succeeded", instruction_id="instruction_test", body={})
    assert outcome.identity == original.identity
    sent = []
    authority = SimpleNamespace(dispatch_lineage_program_event=lambda *args, **kwargs: sent.append((args[2], args[3])) or {})
    plan = {"event_sources": {"event_source_bbbbbbbbbbbb": {
        "kind": "event_source", "event_type": "budget.exhausted", "subject_event_source_id": SOURCE}}}
    dispatch._dispatch_program_runtime_event_watchers(
        LINEAGE, plan, USER, event_type="budget.exhausted", observed_event_source_id=SOURCE,
        original_event=original, payload={}, authority=authority,
    )
    assert len(sent) == 1 and sent[0][0].identity == original.identity and sent[0][1] == RESIDENT


def test_dispatch_denial_is_recorded_in_trigger_path(monkeypatch):
    """事件分发被权限拒绝时，触发路径记录原始身份和拒绝原因"""
    original = event()
    settled = []
    recorded = []
    monkeypatch.setattr(trigger_paths, "begin_event", lambda lineage_id, source, data, **kwargs: recorded.append(data) or "path")
    monkeypatch.setattr(trigger_paths, "event_settled", lambda *args, **kwargs: settled.append(kwargs))
    def deny(*args, **kwargs):
        assert current_operation_identity().to_dict() == original.identity
        raise PermissionError("current permission denied")
    monkeypatch.setattr(dispatch, "_dispatch_lineage_program_event_locked", deny)
    with pytest.raises(PermissionError, match="current permission denied"):
        dispatch.dispatch_program_event(LINEAGE, SOURCE, original, USER, cursor="test", authority=SimpleNamespace())
    assert recorded[0]["identity"] == original.identity
    assert settled == [{"error": "current permission denied"}]


@pytest.mark.parametrize("denied", [False, True])
def test_expired_deliveries_use_each_saved_identity_without_leaking_ambient_scope(monkeypatch, live_users, denied):
    """过期投递逐条按保存的身份分发且不沿用外层授权，身份停用或缺失的记为失败"""
    first, second = event(USER), event(OTHER)
    pending = [
        {"event_source_id": SOURCE, "wire_id": WIRE, "cursor": str(index), "event": saved.to_dict()}
        for index, saved in enumerate([first, second])
    ]
    if denied:
        monkeypatch.setattr(live_users, "is_active", lambda actor: actor != OTHER)
        pending.append({"event_source_id": SOURCE, "wire_id": WIRE, "cursor": "missing",
                        "event": replace(second, identity={}).to_dict()})
    monkeypatch.setattr(runtime, "drain_expired_deliveries", lambda identifier: pending)
    monkeypatch.setattr(backlog, "_compiled_program", lambda data: {})
    recorded, dispatched, reads, settled = [], [], [], []
    monkeypatch.setattr(trigger_paths, "begin_event",
                        lambda lineage_id, source, saved, **kwargs: recorded.append(saved) or "failed_path")
    monkeypatch.setattr(trigger_paths, "event_settled", lambda *args, **kwargs: settled.append(kwargs["error"]))
    def read(identifier, *, actor):
        assert current_operation_identity().principal.id == actor
        reads.append(actor)
        return {}
    def watcher(identifier, plan, actor, **kwargs):
        assert kwargs["original_event"].identity == current_operation_identity().to_dict()
        assert actor == current_operation_identity().principal.id
        dispatched.append(actor)
        return [{"actor": actor}]
    monkeypatch.setattr(dispatch, "_dispatch_program_runtime_event_watchers", watcher)
    authority = SimpleNamespace(lineage_scope=lambda identifier: nullcontext(), read_lineage=read,
                                ensure_manager=lambda data, actor: None, event_authority=lambda: None)
    outer = OperationIdentity.for_actor(RESIDENT)
    with operation_identity_scope(outer):
        result = backlog.dispatch_expired_deliveries(LINEAGE, RESIDENT, authority=authority)
    assert reads == dispatched == ([USER] if denied else [USER, OTHER])
    if denied:
        assert [item["status"] for item in result[1:]] == ["failed", "failed"]
        assert "inactive" in settled[0] and "no verified identity" in settled[1]
        assert recorded[0]["identity"] == second.identity and recorded[1]["identity"] == {}
    else:
        assert recorded == settled == []
