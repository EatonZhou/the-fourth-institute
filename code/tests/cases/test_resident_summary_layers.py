"""Temporal summary ownership, budget and atomic consumption.

:module: resident.identity
"""
from datetime import datetime, timedelta, timezone

import pytest

from tests.cases.test_resident_context import context, A, B, C
from tests.cases.test_family_work_summaries import setup_work, SUMMARY
from management_portal.society_service.resident_context import work_summary as work, summary_layers as layers
from management_portal.society_service.resident_context.evidence import validate_summary


def test_steward_consumes_snapshot_and_member_cannot_overwrite_higher_layers(setup_work):
    """归纳原子消费近期事实，成员后续写入不覆盖高层"""
    context, roles, *_ = setup_work
    first = work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["one"])
    steward = roles["steward"][0]
    merged = work.update_resident_work_summary(steward, A, first["revision"],
        consolidation={"phase": {"recent_results": ["登录权限隔离已完成"], "primary_ownership": ["登录"]}})
    assert merged["layers"]["recent"] == {}
    assert "权限分工" in merged["summary"]["active_threads"]
    next_work = work.update_resident_work_summary(A, A, merged["revision"],
        summary={"current_focus": "新问题"}, message_ids=["two"])
    assert next_work["layers"]["phase"] == merged["layers"]["phase"]
    assert "登录权限隔离已完成" in next_work["summary"]["recent_results"]
    assert context.store.resident(A)["summary"] == next_work["summary"]
    with pytest.raises(ValueError, match="版本"):
        work.update_resident_work_summary(steward, A, merged["revision"], consolidation={"phase": {"current_focus": "过期结果"}})
    assert context.store.work_summary(A)["layers"]["recent"]["current_focus"] == "新问题"


def test_only_own_steward_can_consolidate_and_facades_keep_their_owner(setup_work):
    _, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["one"])
    for actor in (A, B, C):
        with pytest.raises(PermissionError):
            work.update_resident_work_summary(actor, A, 1, consolidation={"phase": SUMMARY})
    with pytest.raises(ValueError, match="门面"):
        work.update_resident_work_summary(roles["steward"][0], C, 0, consolidation={"phase": SUMMARY})


def test_total_budget_covers_fields_and_layers_without_truncation(setup_work, monkeypatch):
    monkeypatch.setenv("PORTAL_SUMMARY_TOTAL_CHARS", "400")
    context, roles, *_ = setup_work
    with pytest.raises(ValueError, match="总预算"):
        validate_summary({"current_focus": "字" * 201, "recent_results": ["字" * 200]})
    work.update_resident_work_summary(A, A, 0, summary={"current_focus": "字" * 290}, message_ids=["one"])
    higher = work.update_resident_work_summary(roles["steward"][0], A, 1,
        consolidation={"phase": {"current_focus": "字" * 290}})
    with pytest.raises(ValueError, match="总预算"):
        work.update_resident_work_summary(A, A, higher["revision"], summary={"recent_results": ["字" * 111]}, message_ids=["two"])
    assert context.store.work_summary(A)["revision"] == higher["revision"]
    with pytest.raises(ValueError, match="75%"):
        work.update_resident_work_summary(roles["steward"][0], A, higher["revision"],
            consolidation={"phase": {"current_focus": "字" * 301}})


def test_unresolved_items_survive_even_when_agent_omits_them_and_close_explicitly(setup_work):
    _, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["one"])
    steward = roles["steward"][0]
    first = work.update_resident_work_summary(steward, A, 1,
        consolidation={"phase": {}, "long_term": {"active_threads": ["权限分工"], "primary_ownership": ["登录"]}})
    second = work.update_resident_work_summary(steward, A, first["revision"],
        consolidation={"phase": {"recent_results": ["阶段完成"]}, "resolved_threads": {"权限分工": "已完成职责交接，记录见项目文档"}})
    assert not second["summary"]["active_threads"]
    assert second["layers"]["long_term"]["primary_ownership"] == ["登录"]


def test_time_and_quantity_watermarks_and_maintenance_do_not_self_trigger(setup_work, monkeypatch):
    monkeypatch.setenv("PORTAL_SUMMARY_PHASE_ROUNDS", "2")
    context, roles, records, states, refs, *_ = setup_work
    first = work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["one"])
    assert not first["consolidation"]["due"]
    work.update_resident_work_summary(A, A, 1, message_ids=["one"])
    assert context.store.work_summary(A)["maintenance"]["phase_rounds"] == 1
    refs["running"] = work.ROLLUP_REF
    work.update_resident_work_summary(A, A, 1, message_ids=["running"])
    assert context.store.work_summary(A)["maintenance"]["phase_rounds"] == 1
    work.update_resident_work_summary(A, A, 1, message_ids=["two"])
    assert work.check_resident_work_summary(C, A, "two")["consolidation_due"]
    merged = work.update_resident_work_summary(roles["steward"][0], A, 1, consolidation={"phase": SUMMARY})
    assert merged["maintenance"]["phase_rounds"] == 0
    assert merged["maintenance"]["long_term_rounds"] == 2
    now = datetime.now(timezone.utc)
    state = {**merged["maintenance"], "long_term_at": (now - timedelta(days=8)).isoformat()}
    assert layers.due(merged["layers"], state, now=now)["level"] == "long_term"
    with pytest.raises(ValueError, match="长期"):
        layers.consolidate(merged["layers"], state, {"phase": SUMMARY})


def test_legacy_oversized_is_readable_then_condensed_without_replaying_history(setup_work):
    context, roles, *_ = setup_work
    legacy = {**SUMMARY, "recent_results": ["旧过程" * 1100]}
    context.store.write_work_summary(A, A, legacy, ["one"], 0, {})
    old = context.store.work_summary(A)
    assert old["layers"]["recent"] == legacy and old["consolidation"]["due"]
    merged = work.update_resident_work_summary(roles["steward"][0], A, old["revision"], consolidation={"phase": SUMMARY})
    assert layers.character_count(merged["layers"]) <= 3000
    assert merged["missing_message_ids"] == []


def test_overview_contains_only_actionable_ids_and_deduplicated_notification(setup_work, monkeypatch):
    monkeypatch.setenv("PORTAL_SUMMARY_PHASE_ROUNDS", "1")
    _, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["one"])
    first = work.get_family_summary_inputs(C, C, overview_only=True)
    second = work.get_family_summary_inputs(C, C, overview_only=True)
    assert "members" not in first and "previous_summary" not in first
    assert first["consolidation_due"][0]["resident_id"] == A
    assert first["notification"] == second["notification"]
    from tests.cases.test_family_router_script import _run
    from management_portal.society_service.family_definition import FAMILY_SUMMARY_ROLLUP_SCRIPT, FAMILY_SUMMARY_CHECK_SCRIPT
    for script in (FAMILY_SUMMARY_ROLLUP_SCRIPT, FAMILY_SUMMARY_CHECK_SCRIPT):
        result, calls = _run(script, {"arguments": {"facade": C, "steward": B},
            "event": {"subject_resident_id": A, "payload": {"message_id": "one"}}}, {
            "CheckResidentWorkSummary": {"status": "covered", "consolidation_due": True},
            "ListResidentStates": {"states": [{"state": {"work_state": "idle", "queued_count": 0}}]},
            "GetFamilySummaryInputs": first, "DispatchFamilyTask": {"message_id": "sent"}})
        assert result.status == "succeeded", result
        sent = next(args for name, args in calls if name == "DispatchFamilyTask")
        assert sent == {"resident_id": C, "trigger": "summary", "message_id": first["notification"]["message_id"]}


def test_personal_family_due_without_enabling_hourly_facade_summary(setup_work, monkeypatch):
    _, roles, *_ = setup_work
    monkeypatch.setattr(work, "_family", lambda record: ({"title": "个人", "goal": "", "personal_ama_id": "ama"}, roles))
    assert not work.get_family_summary_inputs(C, C, overview_only=True)["needs_update"]
    monkeypatch.setenv("PORTAL_SUMMARY_PHASE_ROUNDS", "1")
    work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["one"])
    view = work.get_family_summary_inputs(C, C, overview_only=True)
    assert view["needs_update"] and not view["facade_needs_update"]
