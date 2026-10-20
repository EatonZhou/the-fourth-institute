"""Work facts have one author; completed inputs are checked without AI.

:module: resident.identity
"""
from types import SimpleNamespace

import pytest

from tests.cases.test_resident_context import context, A, B, C, _record, _turn, _summarize
from tests.cases.test_family_familize import lineage, charters, future_creation_store
from management_portal.society_service.resident_context import work_summary as work
from management_portal.society_service import resident_conversation_stream, resident_held_infos, resident_execution_domain

SUMMARY = {"current_focus": "维护登录", "active_threads": ["权限分工"], "durable_expertise": ["登录"],
           "recent_results": ["修正权限边界"], "primary_ownership": ["登录"], "supporting_expertise": [],
           "branch_delta": "", "route_when": ["登录请求"], "avoid_when": ["页面样式"]}


@pytest.fixture
def setup_work(context, monkeypatch):
    context.source.records.append(_record(C, "门面"))
    context.sync_catalog()
    roles = {"member": [A, B], "steward": ["resident_dddddddddddd"], "router": ["resident_eeeeeeeeeeee"], "facade": [C]}
    records = {rid: {"resident_id": rid, "title": rid, "family": {"family_id": "function_block_aaaaaaaaaaaa", "role": role}}
               for role, ids in roles.items() for rid in ids}
    monkeypatch.setattr(work, "resident_context", lambda: context)
    monkeypatch.setattr(work, "_record", lambda rid: records[rid])
    monkeypatch.setattr(work, "_visible", lambda actor, rid: records[rid])
    monkeypatch.setattr(work, "_family", lambda record: ({"title": "登录组", "goal": "维护登录"}, roles))
    states = {"one": "completed", "two": "completed", "running": "running", "failed": "failed"}
    refs = {}
    pending, sent = [], []
    repo = SimpleNamespace(input_state=lambda rid, mid: {"state": states[mid]} if mid in states else None,
                           message_business_ref=lambda rid, mid: refs.get(mid, ""))
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repo)
    monkeypatch.setattr(resident_held_infos, "peek", lambda record: list(pending))
    def deliver(rid, actor, text, **kwargs):
        from management_portal.society_service.resident_message_contract import message_identity
        message_identity(kwargs["message_id"])
        sent.append((rid, actor, text, kwargs))
        pending.append({"message_id": kwargs["message_id"]})
        refs[kwargs["message_id"]] = kwargs["business_ref"]
    monkeypatch.setattr(resident_execution_domain, "deliver_resident_message_content", deliver)
    return context, roles, records, states, refs, pending, sent


def test_coverage_is_per_input_and_no_change_does_not_advance_content(setup_work):
    """摘要按输入记录覆盖且内容未变时版本不递增"""
    context, roles, *_ = setup_work
    context.source.converse(A, _turn(1, "旧工作", "已维护旧模块"), 1)
    context.reconcile()
    _summarize(context, A, "维护旧模块")
    initial = work.maintenance_view(A, A)
    assert initial["revision"] == 1 and initial["summary"]["current_focus"] == "维护旧模块"
    result = work.update_resident_work_summary(A, A, 1, summary=SUMMARY, message_ids=["running"])
    assert result["revision"] == 2
    assert work.check_resident_work_summary(C, A, "one")["status"] == "reminder_attached"
    result = work.update_resident_work_summary(A, A, 2, message_ids=["one"])
    assert result["revision"] == 2 and not result["missing_message_ids"]
    assert work.check_resident_work_summary(C, A, "one")["status"] == "covered"
    with pytest.raises(ValueError, match="版本"):
        work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["two"])
    with pytest.raises(ValueError, match="输入"):
        work.update_resident_work_summary(A, A, 2, message_ids=["failed"])
    with pytest.raises(PermissionError):
        work.update_resident_work_summary(B, A, 2, summary=SUMMARY, message_ids=["two"])


def test_missing_rounds_share_one_next_input_reminder_and_maintenance_is_excluded(setup_work):
    """缺失摘要轮次共用下次输入提醒且排除维护工作"""
    context, roles, records, states, refs, pending, sent = setup_work
    assert work.check_resident_work_summary(C, A, "failed")["status"] == "skipped"
    assert work.check_resident_work_summary(C, A, "one")["status"] == "reminder_attached"
    assert work.check_resident_work_summary(C, A, "two")["status"] == "reminder_pending"
    assert len(sent) == 1 and sent[0][1] == C and sent[0][3]["activation_policy"] == "attach_to_next"
    assert context.store.work_summary(A)["missing_message_ids"] == ["one", "two"]
    refs["two"] = work.ROLLUP_REF
    assert work.check_resident_work_summary(C, A, "two")["status"] == "skipped"


def test_family_rollup_changes_only_when_material_changes_and_has_one_writer(setup_work):
    """Family 汇总仅在材料变化时更新且仅 steward 可写"""
    context, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["one"])
    inputs = work.get_family_summary_inputs(C, C)
    assert inputs["needs_update"]
    steward = roles["steward"][0]
    work.update_resident_work_summary(steward, C, 0, summary=SUMMARY, source_versions=inputs["source_versions"])
    assert not work.get_family_summary_inputs(C, C)["needs_update"]
    work.update_resident_work_summary(A, A, 1, message_ids=["two"])
    assert not work.get_family_summary_inputs(C, C)["needs_update"]
    work.update_resident_work_summary(A, A, 1, summary={**SUMMARY, "recent_results": ["完成隔离"]}, message_ids=["two"])
    assert work.get_family_summary_inputs(C, C)["needs_update"]
    with pytest.raises(PermissionError):
        work.update_resident_work_summary(A, C, 1, summary=SUMMARY, source_versions=inputs["source_versions"])


@pytest.mark.parametrize("role", ["router", "steward"])
def test_internal_roles_do_not_receive_summary_reminders_or_consolidation_work(setup_work, role, monkeypatch):
    """Router／Steward 均免写自身摘要，不进入提醒或归纳清单。"""
    context, roles, records, states, refs, pending, sent = setup_work
    router = roles[role][0]
    assert work.check_resident_work_summary(C, router, "one")["status"] == "skipped"
    assert not sent
    original = context.store.work_summary
    def only_members(rid):
        assert rid not in roles["router"] + roles["steward"]
        return original(rid)
    monkeypatch.setattr(context.store, "work_summary", only_members)
    work.get_family_summary_inputs(C, C, overview_only=True)
    with pytest.raises(PermissionError):
        work.update_resident_work_summary(roles["steward"][0], router, 0,
                                         consolidation={"phase": SUMMARY})


def test_reminder_retries_keep_the_same_identity_and_other_inputs_stay_distinct(setup_work):
    """摘要提醒重试复用标识且不同输入保持独立

    :module: platform.core"""
    _, _, _, _, _, pending, sent = setup_work
    work.check_resident_work_summary(C, A, "one")
    first = sent[-1][3]["message_id"]
    pending.clear()  # A delivery retry sees no held projection yet.
    work.check_resident_work_summary(C, A, "one")
    assert sent[-1][3]["message_id"] == first
    pending.clear()
    work.check_resident_work_summary(C, A, "two")
    assert sent[-1][3]["message_id"] != first


def test_global_summary_jobs_retire_on_familize_but_internal_work_stays_readable(context):
    """加入 Family 后全局摘要任务退役且内部摘要可读"""
    context.source.converse(A, _turn(1, "登录", "修正登录"), 1)
    context.sync_catalog()
    context.reconcile()
    context.prepare_due()
    claim = next(j for j in context.claim_summary_jobs("writer")["jobs"] if j["resident_id"] == A)
    context.source.records[0]["discovery_visible"] = False
    context.sync_catalog()
    with pytest.raises(KeyError):
        context.submit_summary("writer", claim["claim_id"], SUMMARY, model_version="test", account_id="")
    context.store.write_work_summary(A, A, SUMMARY, ["one"], 0, {})
    assert A not in {d["resident_id"] for d in context.store.documents()}
    assert context.contexts(actor="caller", resident_ids=[A])["residents"][0]["summary"]["current_focus"] == "维护登录"


def test_completion_summary_check_uses_event_subject():
    """完成摘要检查从事件主体读取 Resident

    :module: platform.core"""
    from dataclasses import asdict
    from tests.cases.test_family_router_script import _run
    from management_portal.society_service.family_definition import FAMILY_SUMMARY_CHECK_SCRIPT
    from management_portal.society_service.lineage_primitives import make_event

    # A terminal event keeps its Resident in the envelope, not in its payload.
    event = make_event("resident.work_finished", subject_resident_id=A,
                       payload={"message_id": "completed-input", "job_id": "completed-input"})
    outcome, calls = _run(FAMILY_SUMMARY_CHECK_SCRIPT, {"event": asdict(event)}, {
        "CheckResidentWorkSummary": {"status": "reminder_attached"},
    })
    assert outcome.status == "succeeded"
    assert calls == [("CheckResidentWorkSummary", {"resident_id": A, "message_id": "completed-input"})]
    assert outcome.outputs == {"status": "reminder_attached"}


def test_hourly_effect_skips_unchanged_and_busy_stewards():
    """每小时汇总跳过材料未变或 steward 忙碌的情况"""
    from tests.cases.test_family_router_script import _run
    from management_portal.society_service.family_definition import FAMILY_SUMMARY_ROLLUP_SCRIPT
    inputs = {"arguments": {"facade": C, "steward": B}, "event": {"payload": {}}}
    for state, needs, expected in [("idle", False, "unchanged"), ("running", True, "busy"), ("idle", True, "requested")]:
        result, calls = _run(FAMILY_SUMMARY_ROLLUP_SCRIPT, inputs, {
            "ListResidentStates": {"states": [{"resident_id": B, "state": {"work_state": state, "queued_count": 0}}]},
            "GetFamilySummaryInputs": {"needs_update": needs, "source_versions": {A: 1},
                                       "notification": {"message_id": "rollup"}},
            "DispatchFamilyTask": {"message_id": "rollup"},
        })
        assert result.status == "succeeded" and result.outputs["status"] == expected
        sent = [args for name, args in calls if name == "DispatchFamilyTask"]
        assert bool(sent) == (expected == "requested")
        if sent:
            assert sent[0] == {"resident_id": C, "trigger": "summary", "message_id": "rollup"}
