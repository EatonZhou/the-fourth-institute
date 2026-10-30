"""Authorized flow definitions and HATG runtime reads (decision_481dc4e6292c).

:design: decision_481dc4e6292c
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from management_portal import scheduled_triggered_monitor as monitor
from management_portal.hatg.contracts import InstructionError

from management_portal.society_service import lineage_aggregate


RESIDENT = "resident_0123456789ab"
LINEAGE = "lineage_0123456789ab"
BROKEN = "lineage_aaaaaaaaaaaa"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def authorize_reads(monkeypatch):
    calls = []
    def authorize(declaration, arguments, context):
        calls.extend((declaration.name, args, context.caller, context.entry) for args in arguments)
        return [None] * len(arguments)
    monkeypatch.setattr(monitor.permission_system, "authorize_reads", authorize)
    return calls


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def lineage() -> dict:
    return {
        "lineage_id": LINEAGE, "title": "Task Lineage", "revision": 8,
        "nodes": [{"id": RESIDENT, "title": "Worker"}],
        "program": {
            "components": [
                {"id": "event_source_clock", "kind": "event_source", "title": "Daily", "config": {
                    "event_type": "time.reached", "schedule_mode": "recurring", "recurrence_kind": "interval",
                    "interval_seconds": 3600, "jitter_seconds": 10,
                }},
                {"id": "event_source_manual", "kind": "event_source", "title": "Run once",
                 "config": {"event_type": "manual.triggered"}},
                {"id": "event_source_unused", "kind": "event_source", "title": "Unused",
                 "config": {"event_type": "file.changed"}},
            ],
            "wires": [
                {"id": "wire_clock", "kind": "route", "source": "event_source_clock", "target": RESIDENT,
                 "target_port": "invoke",
                 "config": {"invoke_profiles": {"agent": {"arguments": {"prompt": {"value": "Run the daily job."}}}}}},
                {"id": "wire_manual", "kind": "route", "source": "event_source_manual", "target": RESIDENT,
                 "target_port": "invoke"},
            ],
        },
    }


class Reads:
    """ISA reads the monitor performs, answered from fixtures."""

    def __init__(self) -> None:
        self.lineage = lineage()
        self.runtime = {"components": [{"component_id": "event_source_clock", "paused": False,
                                        "next_scheduled_at": "2026-09-29T13:00:00Z"}]}
        self.diagnostics: list[dict] = []
        self.runs = [{"trigger_path_id": "run_2", "status": "completed", "resident_ids": [RESIDENT],
                      "started_at": iso(NOW - timedelta(minutes=30)), "finished_at": iso(NOW - timedelta(minutes=20))}]
        self.state = {"state": {"work_state": "idle", "intake_state": "open"}, "controls": {"can_cancel": False}}
        self.forbidden: set[str] = set()
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, instruction: str, arguments: dict) -> dict:
        self.calls.append((instruction, dict(arguments)))
        if instruction in self.forbidden:
            raise InstructionError("instruction.forbidden", "not allowed")
        if instruction == "GetLineage":
            if arguments["lineage_id"] == BROKEN:
                raise OSError("read failed")
            return {"lineage": copy.deepcopy(self.lineage)}
        if instruction == "GetComponentRuntime":
            return copy.deepcopy(self.runtime)
        if instruction == "GetLineageDiagnostics":
            return {"diagnostics": copy.deepcopy(self.diagnostics)}
        if instruction == "ListTriggerPaths":
            runs = [run for run in self.runs if not arguments.get("resident_id") or arguments["resident_id"] in (run.get("resident_ids") or [])]
            return {"paths": [{**run, "occurred_at": run["started_at"]} for run in copy.deepcopy(runs[:arguments.get("limit", 50)])]}
        if instruction == "GetResident":
            return {"resident": {"resident_id": RESIDENT, "title": "Worker", "provider": "codex"}}
        if instruction == "ListResidentStates":
            assert arguments["resident_ids"] == [RESIDENT]
            return {"states": [copy.deepcopy(self.state)]}
        if instruction == "GetResidentSettings":
            return {"settings": {"model": "fixture"}}
        raise AssertionError(instruction)


@pytest.fixture
def reads(monkeypatch):
    fixture = Reads()
    def definition(lineage_id, actor):
        assert actor == "sample_admin"
        if lineage_id == BROKEN:
            raise OSError("read failed")
        return copy.deepcopy(fixture.lineage)

    listing = SimpleNamespace(get_lineage_monitor_definitions=lambda ids, actor: definition_batch(ids, actor, definition), list_lineages=lambda actor: {"lineages": [
        {"lineage_id": LINEAGE, "title": "Task Lineage"},
        {"lineage_id": BROKEN, "title": "Broken Lineage"},
    ]})
    monkeypatch.setattr(monitor, "get_society_service", lambda: SimpleNamespace(lineage=listing))
    monkeypatch.setattr(monitor, "_now", lambda: NOW)
    return fixture


def status(reads):
    return monitor.get_scheduled_triggered_status("sample_admin", read=reads)


def test_independent_lineage_reads_overlap_and_keep_viewer_context(monkeypatch, reads):
    """定时与自动触发面板并行读取各 Lineage，并保持查看者身份和上下文

    :design-point:"""
    from contextvars import ContextVar
    from threading import Barrier

    viewer = ContextVar("monitor_test_viewer", default="missing")
    barrier = Barrier(2, timeout=5)
    ids = [LINEAGE, "lineage_bbbbbbbbbbbb"]
    listing = SimpleNamespace(
        list_lineages=lambda actor: {"lineages": [{"lineage_id": lid} for lid in ids]},
        get_lineage_monitor_definitions=lambda values, actor: {
            "definitions": {lid: {"lineage_id": lid} for lid in values}},
    )
    monkeypatch.setattr(monitor, "get_society_service", lambda: SimpleNamespace(lineage=listing))
    observed = []
    def collect(definition, reader, now):
        observed.append((definition["lineage_id"], reader.actor, viewer.get()))
        barrier.wait()
        return []
    monkeypatch.setattr(monitor, "_lineage_rows", collect)
    token = viewer.set("current-execution-subject")
    try:
        assert status(reads)["lineage_errors"] == []
    finally:
        viewer.reset(token)
    assert sorted(observed) == [(lid, "sample_admin", "current-execution-subject") for lid in ids]


def definition_batch(ids, actor, read):
    result = {"definitions": {}, "lineage_errors": {}}
    for lineage_id in ids:
        try:
            result["definitions"][lineage_id] = read(lineage_id, actor)
        except Exception as exc:
            result["lineage_errors"][lineage_id] = str(exc)
    return result


def test_rows_come_from_timed_sources_wired_to_a_resident_and_errors_stay_per_lineage(reads):
    """面板每行来自连到 Resident 的定时事件源，单个 Lineage 读取失败只记在该 Lineage 下

    :design-point:"""
    result = status(reads)
    assert result["schema"] == monitor.SCHEMA
    assert result["counts"] == {"agents": 1, "effects": 0, "sources": 1, "paused": 0, "errors": 1}
    assert result["lineage_errors"] == [{"lineage_id": BROKEN, "title": "Broken Lineage", "error": "read failed"}]
    row = result["items"][0]
    assert row["resident_id"] == RESIDENT and row["lineage_id"] == LINEAGE and "can_manage" not in row
    assert [source["component_id"] for source in row["sources"]] == ["event_source_clock"]
    assert [source["component_id"] for source in row["manual_sources"]] == ["event_source_manual"]
    assert row["tasks"] == [{"wire_id": "wire_clock", "component_id": "event_source_clock", "text": "Run the daily job."}]
    assert row["sources"][0]["runtime"]["next_scheduled_at"] == "2026-09-29T13:00:00Z"
    assert row["health"] == "ok" and row["warnings"] == []
    assert {name for name, _ in reads.calls} <= {
        "GetLineage", "GetComponentRuntime", "GetLineageDiagnostics", "ListTriggerPaths",
        "GetResident", "ListResidentStates", "GetResidentSettings",
    }


def test_paused_sources_show_as_paused(reads):
    """定时事件源暂停时面板行显示为已暂停

    :design-point:"""
    reads.runtime["components"][0]["paused"] = True
    assert status(reads)["items"][0]["health"] == "paused"


@pytest.mark.parametrize("reason", [
    "请在 Lineage 设置中显式配置自动运行用户",
    "自动运行用户不可用或已停用",
])
def test_blocked_automatic_source_exposes_runtime_reason_before_first_run(reads, reason):
    """自动触发受阻时首次运行前就显示受阻原因，恢复后警告消失

    :design-point:"""
    reads.runs = []
    runtime = reads.runtime["components"][0]
    runtime.update(state="blocked", error=reason, next_scheduled_at="", next_due_at="")

    row = status(reads)["items"][0]

    assert row["health"] == "error"
    assert row["warnings"] == [f"自动触发受阻：Daily：{reason}"]
    assert row["sources"][0]["runtime"]["next_due_at"] == ""
    assert row["manual_sources"][0]["component_id"] == "event_source_manual"

    runtime.update(state="listening", next_scheduled_at="2026-09-29T13:00:00Z",
                   next_due_at="2026-09-29T13:00:05Z")
    runtime.pop("error")
    restored = status(reads)["items"][0]
    assert restored["health"] == "ok"
    assert restored["warnings"] == []
    assert restored["sources"][0]["runtime"]["next_due_at"] == "2026-09-29T13:00:05Z"


def test_failures_combine_runs_diagnostics_and_consecutive_errors(reads):
    """运行失败、Lineage 诊断错误和连续失败次数合并为错误状态

    :design-point:"""
    reads.runs = [
        {"trigger_path_id": f"run_{index}", "status": "failed", "resident_ids": [RESIDENT],
         "started_at": iso(NOW - timedelta(hours=index))} for index in (1, 2)
    ]
    reads.diagnostics = [{"level": "error", "code": "event.missing_time", "message": "x"}]
    row = status(reads)["items"][0]
    assert row["health"] == "error"
    assert row["consecutive_failures"] == 2
    assert any("诊断" in warning for warning in row["warnings"])
    assert any("连续失败" in warning for warning in row["warnings"])


def test_stale_and_closed_intake_warn_without_being_errors(reads):
    """最近运行陈旧或接单已关闭时显示警告，不算错误

    :design-point:"""
    reads.runs = [{"trigger_path_id": "run_old", "status": "completed", "resident_ids": [RESIDENT],
                   "started_at": iso(NOW - timedelta(hours=5)), "finished_at": iso(NOW - timedelta(hours=5))}]
    reads.state["state"]["intake_state"] = "closed"
    row = status(reads)["items"][0]
    assert row["health"] == "warn"
    assert any("陈旧" in warning for warning in row["warnings"])
    assert any("接单已关闭" in warning for warning in row["warnings"])


def test_runs_of_other_residents_do_not_count(reads):
    """其他 Resident 的运行不计入该行的最近运行和健康状态

    :design-point:"""
    reads.runs = [{"trigger_path_id": "run_other", "status": "failed", "resident_ids": ["resident_ffffffffffff"],
                   "started_at": iso(NOW)}] + reads.runs
    row = status(reads)["items"][0]
    assert [run["trigger_path_id"] for run in row["recent_runs"]] == ["run_2"]
    assert row["health"] == "ok"


def test_unreadable_resident_details_do_not_hide_the_row(reads):
    """无权读取 Resident 设置或状态时该行仍正常显示

    :design-point:"""
    reads.forbidden = {"GetResidentSettings", "ListResidentStates"}
    row = status(reads)["items"][0]
    assert row["settings"] == {} and row["state"] == {}
    assert row["read_errors"] == []
    assert row["health"] == "ok"


def test_a_resident_with_several_timers_keeps_one_source_each(reads):
    """一个 Resident 连有多个定时事件源时每个来源各列一项

    :design-point:"""
    second = copy.deepcopy(reads.lineage["program"]["components"][0])
    second.update(id="event_source_weekly", title="Weekly")
    reads.lineage["program"]["components"].append(second)
    reads.lineage["program"]["wires"].append({"id": "wire_weekly", "kind": "route", "source": "event_source_weekly",
                                               "target": RESIDENT, "target_port": "invoke"})
    row = status(reads)["items"][0]
    assert [source["title"] for source in row["sources"]] == ["Daily", "Weekly"]
    assert status(reads)["counts"]["sources"] == 2


def test_lineages_without_timed_sources_produce_no_rows(reads):
    """没有定时事件源连到 Resident 的 Lineage 不产生面板行

    :design-point:"""
    reads.lineage["program"]["wires"] = [reads.lineage["program"]["wires"][1]]
    assert status(reads)["items"] == []


def test_a_busy_lineage_still_shows_the_residents_own_runs(reads):
    """运行频繁的 Lineage 中仍显示定时 Resident 自己的最近运行

    同一 Lineage 中其他 Resident 有几百条 Trigger Path 时，面板按 Resident 读取 ListTriggerPaths（resident_id），仍列出这个 Resident 的最近触发流程。

    :design: decision_68215b3a825b
    :design-point:
    """
    # The Knowledge Base Lineage has ~600 runs a day; a Lineage-wide page of 200
    # pushed a daily Resident's run off the panel.
    reads.runs = [{"trigger_path_id": f"run_busy_{index}", "status": "completed", "resident_ids": ["resident_ffffffffffff"],
                   "started_at": iso(NOW)} for index in range(300)] + reads.runs
    row = status(reads)["items"][0]
    assert [run["trigger_path_id"] for run in row["recent_runs"]] == ["run_2"]
    assert ("ListTriggerPaths", {"lineage_id": LINEAGE, "resident_id": RESIDENT, "limit": monitor.RUN_SCAN}) in reads.calls


def test_many_ineligible_lineages_do_not_expand_workbench_views_or_read_runtime(monkeypatch, authorize_reads):
    """无监控候选时不展开完整工作台

    验证多个不可匹配的Lineage仅执行授权定义读取，不展开完整GetLineage和运行视图。

    :importance: P1
    :design-point:
    """
    ids = [f"lineage_{index:012x}" for index in range(108)]
    definitions = []
    reads = Reads()

    def definition(lineage_id, actor):
        definitions.append((lineage_id, actor))
        value = lineage()
        value["lineage_id"] = lineage_id
        value["program"]["wires"] = []
        return value

    domain = SimpleNamespace(
        list_lineages=lambda actor: {"lineages": [{"lineage_id": value} for value in ids]},
        get_lineage_monitor_definitions=lambda ids, actor: definition_batch(ids, actor, definition),
    )
    monkeypatch.setattr(monitor, "get_society_service", lambda: SimpleNamespace(lineage=domain))
    result = monitor.get_scheduled_triggered_status("fixture_actor", read=reads)
    assert result["items"] == [] and result["lineage_errors"] == []
    assert definitions == [(value, "fixture_actor") for value in ids]
    assert reads.calls == []
    assert authorize_reads == [("GetLineage", {"lineage_id": value}, "fixture_actor", "web") for value in ids]


@pytest.mark.parametrize("code", ["instruction.not_found", "instruction.owner_unavailable"])
def test_current_permission_denial_or_unavailability_prevents_definition_read(monkeypatch, code):
    """监控候选遵守当前权限判定

    验证权限系统拒绝或不可用时不读取候选定义。

    :importance: P1
    :design-point:
    """
    def authorize(*args):
        raise InstructionError(code, "current policy refused read")

    def forbidden(*args):
        raise AssertionError("definition must not be read before authorization")

    monkeypatch.setattr(monitor.permission_system, "authorize_reads", authorize)
    domain = SimpleNamespace(
        list_lineages=lambda actor: {"lineages": [{"lineage_id": LINEAGE}]},
        get_lineage_monitor_definitions=forbidden,
    )
    monkeypatch.setattr(monitor, "get_society_service", lambda: SimpleNamespace(lineage=domain))
    result = monitor.get_scheduled_triggered_status("viewer")
    assert result["items"] == []
    assert result["lineage_errors"][0]["error"] == "current policy refused read"


def test_definition_denial_after_listing_stays_local_and_is_rechecked_each_request(monkeypatch):
    """候选定义逐次重新检查授权

    验证目录读取后的权限变化被保留为局部错误，下一请求重新校验权限。

    :importance: P1
    :design-point:
    """
    reads = Reads()
    actors = []

    def definition(lineage_id, actor):
        actors.append(actor)
        if actor == "denied":
            raise PermissionError("current access denied")
        return lineage()

    domain = SimpleNamespace(
        list_lineages=lambda actor: {"lineages": [{"lineage_id": LINEAGE}]},
        get_lineage_monitor_definitions=lambda ids, actor: definition_batch(ids, actor, definition),
    )
    monkeypatch.setattr(monitor, "get_society_service", lambda: SimpleNamespace(lineage=domain))
    assert len(monitor.get_scheduled_triggered_status("allowed", read=reads)["items"]) == 1
    reads.calls.clear()
    result = monitor.get_scheduled_triggered_status("denied", read=reads)
    assert result["items"] == []
    assert result["lineage_errors"][0]["error"] == "current access denied"
    assert reads.calls == [] and actors == ["allowed", "denied"]


@pytest.mark.parametrize("allowed", [True, False])
def test_monitor_definition_authorizes_before_returning_detached_minimal_fields(monkeypatch, allowed):
    """监控定义按授权返回最小副本

    验证身份与可见性校验后只返回监控需要的独立定义字段。

    :importance: P1
    :design-point:
    """
    from management_portal.society_service import lineage_domain as domain, lineage_reads, lineage_state

    source = lineage()
    source["nodes"][0].update(workspaces=[{"path": "/private"}], runtime_settings={"model": "private"})
    source["runtime"] = {"private": True}
    calls = []

    def read(lineage_id, *, actor):
        calls.append(("read", lineage_id, actor))
        return source

    def authorize(value, actor, *, operation):
        calls.append(("authorize", value["lineage_id"], actor, operation))
        return allowed

    def forbidden(*args, **kwargs):
        raise AssertionError("full workbench view must not be assembled")

    monkeypatch.setattr(lineage_aggregate, "read_lineage", read)
    monkeypatch.setattr(lineage_state, "_actor_can_manage_lineage", authorize)
    monkeypatch.setattr(lineage_reads, "_actor_can_manage_lineage", authorize)
    monkeypatch.setattr(domain, "_actor_can_manage_lineage", authorize)
    monkeypatch.setattr(lineage_aggregate, "_actor_can_manage_lineage", authorize)
    monkeypatch.setattr(lineage_reads, "_current_lineage_view_for_read", forbidden)
    if not allowed:
        with pytest.raises(PermissionError):
            lineage_reads.get_lineage_monitor_definition(LINEAGE, "viewer")
    else:
        result = lineage_reads.get_lineage_monitor_definition(LINEAGE, "viewer")
        assert set(result) == {"lineage_id", "title", "revision", "nodes", "program"}
        assert result["nodes"] == [{"id": RESIDENT, "title": "Worker"}]
        result["program"]["components"][0]["config"]["interval_seconds"] = 99
        result["nodes"][0]["title"] = "Changed"
        assert source["program"]["components"][0]["config"]["interval_seconds"] == 3600
        assert source["nodes"][0]["title"] == "Worker"
    assert calls == [("read", LINEAGE, "viewer"), ("authorize", LINEAGE, "viewer", "read")]


def test_monitor_definition_preserves_deletion_guard_and_read_rpc_contract(monkeypatch):
    """监控定义保留删除和只读RPC边界

    验证已删除Lineage不能绕过原有读取边界，新增RPC保持只读契约。

    :importance: P1
    :design-point:
    """
    from management_portal.society_service import lineage_reads
    from management_portal.society_service.contracts import operation_contract
    from management_portal.society_service.errors import LineageConflictError

    monkeypatch.setattr(lineage_aggregate, "lineage_deletion_intent", lambda lineage_id: {"phase": "deleting"})
    with pytest.raises(LineageConflictError):
        lineage_reads.get_lineage_monitor_definition(LINEAGE, "viewer")
    # Monitors read definitions through the batch RPC, which checks each Lineage on its own.
    operation = operation_contract("lineage", "get_lineage_monitor_definitions")
    assert (operation.mode, operation.authority) == ("read", "lineage")


@pytest.mark.parametrize('effect_failed', [False, True])
def test_failed_trigger_or_effect_is_unhealthy_before_any_script_run(effect_failed):
    """触发投递或 Effect 失败时，即使还没有脚本运行也显示为错误

    :design-point:"""
    definition = lineage()
    effect_id = 'effect_0123456789ab'
    definition['program']['components'].append({'id': effect_id, 'kind': 'effect', 'title': 'Snapshot',
        'config': {'script': 'run=$(RunScript path="/share/Agents/example/run.py" runtime=python)'}})
    definition['program']['wires'][0]['target'] = effect_id
    if effect_failed:
        definition['program_effect_runtime'] = {effect_id: {'status': 'failed', 'error': 'RunScript 参数无效'}}
    def read(instruction, **arguments):
        if instruction == 'GetLineage':
            return {'lineage': definition}
        if instruction == 'GetComponentRuntime':
            return {'components': [{'component_id': 'event_source_clock', 'paused': False,
                'last_delivery': {'statuses': {'failed': 0 if effect_failed else 1}}}]}
        if instruction == 'ListScriptRuns':
            return {'runs': []}
        if instruction == 'GetLineageDiagnostics':
            return {'diagnostics': []}
        raise AssertionError(instruction)
    rows = monitor._lineage_rows(definition, read, NOW)
    assert rows[0]['health'] == 'error'
    assert rows[0]['warnings']
    assert not rows[0]['last_run']
    if effect_failed:
        assert rows[0]['state']['work_state'] == 'failed'
