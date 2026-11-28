"""Temporal summary ownership, budget and per-input consolidation.

:module: resident.identity
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from tests.cases.test_resident_context import context, A, B, C
from tests.cases.test_family_work_summaries import setup_work, SUMMARY
from management_portal.society_service.resident_context import work_summary as work, summary_layers as layers
from management_portal.society_service.resident_context.evidence import character_count, validate_summary


def test_concurrent_consolidations_keep_recent_and_unseen_input(setup_work, monkeypatch):
    """并发归纳只确认共同读取的轮次，保留新的近期事实与待归纳输入。"""
    monkeypatch.setenv("PORTAL_SUMMARY_PHASE_ROUNDS", "1")
    monkeypatch.setenv("PORTAL_SUMMARY_LONG_TERM_ROUNDS", "2")
    context, roles, *_ = setup_work
    first = work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    steward = roles["steward"][0]
    assert first["pending_message_ids"] == ["one"]
    current = work.update_resident_work_summary(A, A,
        summary={"current_focus": "新问题"}, message_ids=["two"])
    assert current["consolidation"]["level"] == "long_term"
    request = {"phase": {"recent_results": ["登录权限隔离已完成"], "active_threads": ["权限分工"]},
               "consolidated_message_ids": first["pending_message_ids"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = [pool.submit(work.update_resident_summary_consolidation, steward, A, request) for _ in range(2)]
        for result in concurrent:
            result.result(timeout=10)
    saved = context.store.work_summary(A)
    assert saved["layers"]["recent"] == current["layers"]["recent"]
    assert saved["maintenance"]["phase_rounds"] == 1
    assert saved["maintenance"]["long_term_rounds"] == 2
    assert saved["pending_message_ids"] == ["one", "two"]
    assert saved["revision"] == current["revision"] + 1
    assert context.store.resident(A)["summary"] == saved["summary"]
    assert work.update_resident_summary_consolidation(steward, A, request) == saved
    settled = work.update_resident_summary_consolidation(steward, A,
        consolidation={"phase": request["phase"], "long_term": SUMMARY, "consolidated_message_ids": saved["pending_message_ids"]})
    assert settled["pending_message_ids"] == []
    assert settled["layers"]["recent"] == current["layers"]["recent"]


def test_only_own_steward_can_consolidate_and_facades_keep_their_owner(setup_work):
    _, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    for actor in (A, B, C):
        with pytest.raises(PermissionError):
            work.update_resident_summary_consolidation(actor, A, consolidation={"phase": SUMMARY})
    with pytest.raises(ValueError, match="门面"):
        work.update_resident_summary_consolidation(roles["steward"][0], C, consolidation={"phase": SUMMARY})


def test_summary_total_limit_counts_all_fields_and_layers_atomically(setup_work, monkeypatch):
    monkeypatch.delenv("PORTAL_SUMMARY_TOTAL_CHARS", raising=False)
    context, roles, *_ = setup_work
    large = {"current_focus": "字" * 2500, "recent_results": ["字" * 2500]}
    assert validate_summary(large)["recent_results"] == large["recent_results"]
    with pytest.raises(ValueError, match="5000"):
        validate_summary({**large, "branch_delta": "超"})
    work.update_resident_work_summary(A, A, summary={"current_focus": "字" * 1000}, message_ids=["one"])
    higher = work.update_resident_summary_consolidation(roles["steward"][0], A,
        consolidation={"phase": {"current_focus": "字" * 4000}, "consolidated_message_ids": ["one"]})
    before = context.store.work_summary(A)
    with pytest.raises(ValueError, match="5000"):
        work.update_resident_work_summary(A, A,
            summary={"current_focus": "字" * 1001}, message_ids=["two"])
    assert context.store.work_summary(A) == before
    saved = work.update_resident_work_summary(A, A,
        summary={"current_focus": "字" * 1000}, message_ids=["two"])
    assert saved["layers"]["phase"] == higher["layers"]["phase"]
    assert character_count(saved["layers"]) == 5000
    assert saved["consolidation"]["due"]
    before = context.store.work_summary(A)
    with pytest.raises(ValueError, match="5000"):
        work.update_resident_summary_consolidation(roles["steward"][0], A,
            consolidation={"phase": large, "long_term": {"durable_expertise": ["超"]}, "consolidated_message_ids": ["two"]})
    assert context.store.work_summary(A) == before
    merged = work.update_resident_summary_consolidation(roles["steward"][0], A,
        consolidation={"phase": {"recent_results": ["字" * 4000]}, "long_term": {}, "consolidated_message_ids": ["two"]})
    assert merged["layers"]["phase"]["recent_results"] == ["字" * 4000]
    assert character_count(merged["layers"]) == 5000
    assert not merged["consolidation"]["due"]


def test_unresolved_items_survive_even_when_agent_omits_them_and_close_explicitly(setup_work):
    _, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    steward = roles["steward"][0]
    first = work.update_resident_summary_consolidation(steward, A,
        consolidation={"phase": {}, "long_term": {"active_threads": ["权限分工"], "primary_ownership": ["登录"]},
                       "consolidated_message_ids": ["one"]})
    second = work.update_resident_summary_consolidation(steward, A,
        consolidation={"phase": {"recent_results": ["阶段完成"]}, "resolved_threads": {"权限分工": "已完成职责交接，记录见项目文档"},
                       "consolidated_message_ids": []})
    assert not second["summary"]["active_threads"]
    assert second["layers"]["long_term"] == first["layers"]["long_term"]
    assert second["layers"]["recent"] == first["layers"]["recent"]


def test_total_limit_includes_automatically_retained_unresolved_items(monkeypatch):
    monkeypatch.delenv("PORTAL_SUMMARY_TOTAL_CHARS", raising=False)
    previous, state = layers.initial({})
    previous["phase"] = {"active_threads": ["待办"]}
    with pytest.raises(ValueError, match="5000"):
        layers.consolidate(previous, state, {"phase": {"current_focus": "字" * 5000}, "consolidated_message_ids": []})
    assert previous["phase"]["active_threads"] == ["待办"]
    merged, _ = layers.consolidate(previous, state,
        {"phase": {"current_focus": "字" * 4998}, "consolidated_message_ids": []})
    assert merged["phase"]["active_threads"] == ["待办"]
    assert character_count(merged) == 5000


def test_quantity_watermarks_and_maintenance_do_not_self_trigger(setup_work, monkeypatch):
    """归纳只按新覆盖轮次与容量到期，维护轮次和时间流逝都不触发"""
    monkeypatch.setenv("PORTAL_SUMMARY_PHASE_ROUNDS", "2")
    monkeypatch.setenv("PORTAL_SUMMARY_LONG_TERM_ROUNDS", "3")
    context, roles, records, states, refs, *_ = setup_work
    first = work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    assert not first["consolidation"]["due"]
    work.update_resident_work_summary(A, A, message_ids=["one"])
    assert context.store.work_summary(A)["maintenance"]["phase_rounds"] == 1
    refs["running"] = work.ROLLUP_REF
    work.update_resident_work_summary(A, A, message_ids=["running"])
    assert context.store.work_summary(A)["maintenance"]["phase_rounds"] == 1
    work.update_resident_work_summary(A, A, message_ids=["two"])
    work.record_completed_work_round(A, "two")
    view = work.maintenance_view(C, A)
    assert view["consolidation"]["due"]
    assert not view["missing_message_ids"]
    merged = work.update_resident_summary_consolidation(roles["steward"][0], A,
        consolidation={"phase": SUMMARY, "consolidated_message_ids": ["one", "two"]})
    assert merged["maintenance"]["phase_rounds"] == 0
    assert merged["maintenance"]["long_term_rounds"] == 2
    # An idle member stays settled however long ago its layers were condensed.
    long_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    idle = {**merged["maintenance"], "phase_at": long_ago, "long_term_at": long_ago}
    assert not layers.due({**merged["layers"], "recent": SUMMARY}, idle)["due"]
    state = layers.with_pending_rounds(merged["maintenance"], {"phase": ["three"], "long_term": ["one", "two", "three"]})
    assert layers.due(merged["layers"], state) == {**layers.due(merged["layers"], state), "due": True,
                                                   "level": "long_term", "reason": "rounds"}
    with pytest.raises(ValueError, match="长期"):
        layers.consolidate(merged["layers"], state, {"phase": SUMMARY, "consolidated_message_ids": ["one", "two", "three"]})


def test_legacy_oversized_is_readable_then_condensed_without_replaying_history(setup_work):
    context, roles, *_ = setup_work
    legacy = {**SUMMARY, "recent_results": ["旧过程" * 2100]}
    context.store.write_work_summary(A, A, legacy, ["one"], {})
    old = context.store.work_summary(A)
    assert old["layers"]["recent"] == legacy and old["consolidation"]["due"]
    work.update_resident_work_summary(A, A, message_ids=["two"])
    assert context.store.work_summary(A)["layers"] == old["layers"]
    with pytest.raises(ValueError, match="5000"):
        work.update_resident_summary_consolidation(roles["steward"][0], A,
            consolidation={"phase": SUMMARY, "consolidated_message_ids": ["two"]})
    assert context.store.work_summary(A)["layers"]["recent"] == legacy
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["two"])
    merged = work.update_resident_summary_consolidation(roles["steward"][0], A,
        consolidation={"phase": SUMMARY, "consolidated_message_ids": ["two"]})
    assert character_count(merged["layers"]) <= 5000
    assert merged["missing_message_ids"] == []


def test_legacy_aggregate_rounds_adopt_covered_inputs_once(setup_work):
    """旧轮次计数没有输入身份，保守纳入覆盖输入后仍只核销一次。"""
    context, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    with context.store.transaction() as connection:
        connection.execute("UPDATE work_summary_layers SET maintenance=maintenance - 'pending_rounds' WHERE resident_id=%s", (A,))
    before = context.store.work_summary(A)
    assert before["pending_message_ids"] == ["one"]
    work.update_resident_work_summary(A, A, message_ids=["two"])
    request = {"phase": SUMMARY, "consolidated_message_ids": before["pending_message_ids"]}
    steward = roles["steward"][0]
    saved = work.update_resident_summary_consolidation(steward, A, request)
    assert saved["pending_message_ids"] == ["two"]
    assert saved["layers"]["recent"] == before["layers"]["recent"]
    assert work.update_resident_summary_consolidation(steward, A, request) == saved


def test_overview_contains_only_actionable_ids_and_deduplicated_notification(setup_work, monkeypatch):
    monkeypatch.setenv("PORTAL_SUMMARY_PHASE_ROUNDS", "1")
    _, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    first = work.get_facade_summary_inputs(C, C, overview_only=True)
    # Same material keeps one task ID across hours, so a stopped or finished
    # summary task is not re-sent every hour.
    import time
    later = time.time() + 7200
    monkeypatch.setattr(time, "time", lambda: later)
    second = work.get_facade_summary_inputs(C, C, overview_only=True)
    assert "members" not in first and "previous_summary" not in first
    assert first["consolidation_due"][0]["resident_id"] == A
    assert first["notification"] == second["notification"]
    assert "UpdateResidentSummaryConsolidation" in first["notification"]["content_parts"][0]["text"]


def test_family_summary_effect_delivers_the_same_overview_notice(setup_work, monkeypatch):
    monkeypatch.setenv("PORTAL_SUMMARY_PHASE_ROUNDS", "1")
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    _, roles, *_ = setup_work
    steward = roles["steward"][0]
    first = work.get_facade_summary_inputs(C, C, overview_only=True)
    from tests.cases.test_family_router_script import _run
    from management_portal.society_service.family_definition import FAMILY_SUMMARY_ROLLUP_SCRIPT, FAMILY_SUMMARY_CHECK_SCRIPT
    for script in (FAMILY_SUMMARY_ROLLUP_SCRIPT, FAMILY_SUMMARY_CHECK_SCRIPT):
        result, calls = _run(script, {"arguments": {"facade": C, "steward": steward},
            "event": {"subject_resident_id": A, "payload": {"message_id": "one"}}}, {
            "GetResidentSummaries": {"summaries": [{"missing_message_ids": [], "consolidation": {"due": True}}]},
            "ListResidentStates": {"states": [{"state": {"work_state": "idle", "queued_count": 0}}]},
            "GetFacadeSummaryInputs": first, "DeliverMessage": {"message_id": first["notification"]["message_id"]}})
        assert result.status == "succeeded", result
        sent = next(args for name, args in calls if name == "DeliverMessage")
        assert sent == {"resident_id": steward, "business_ref": "duty/family-work-summary", "message": first["notification"]}


def test_personal_family_due_without_enabling_hourly_facade_summary(setup_work, monkeypatch):
    _, roles, *_ = setup_work
    monkeypatch.setattr(work, "_family", lambda record: ({"title": "个人", "goal": "", "personal_ama_id": "ama"}, roles))
    assert not work.get_facade_summary_inputs(C, C, overview_only=True)["needs_update"]
    monkeypatch.setenv("PORTAL_SUMMARY_PHASE_ROUNDS", "1")
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    view = work.get_facade_summary_inputs(C, C, overview_only=True)
    assert view["needs_update"] and not view["facade_needs_update"]
