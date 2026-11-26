"""Work facts have one author; completed inputs are checked without AI.

:module: resident.identity
"""
from types import SimpleNamespace

import pytest

from tests.cases.test_resident_context import context, A, B, C, _record, _turn, _summarize
from tests.cases.test_family_familize import lineage, charters, future_creation_store
from management_portal.society_service.resident_context import work_summary as work
from management_portal.society_service import (
    family_facade,
    resident_conversation_stream,
    resident_held_infos,
    resident_intake_domain,
)

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
    monkeypatch.setattr(family_facade, "relay_target_ids", lambda record: roles["member"] + roles["steward"]
                        if record["resident_id"] == C else [])
    # These Families' facades kept no notices (their relay state lives in no Lineage here).
    monkeypatch.setattr(family_facade, "kept_notices", lambda facade: [])
    states ={"one": "completed", "two": "completed", "running": "running", "failed": "failed"}
    completed_at = {"one": "2026-11-26T10:01:00Z", "two": "2026-11-26T10:00:00Z"}
    refs = {}
    pending, sent = [], []
    repo = SimpleNamespace(input_state=lambda rid, mid: {"state": states[mid]} if mid in states else None,
                           input_statuses_by_id=lambda requests: {
                               (rid, mid): {"state": states[mid], "updated_at": completed_at.get(mid, "")}
                               for rid, mid in requests if mid in states
                           },
                           message_business_ref=lambda rid, mid: refs.get(mid, ""))
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repo)
    monkeypatch.setattr(resident_held_infos, "peek", lambda record: list(pending))
    monkeypatch.setattr(resident_held_infos, "is_info", lambda record, mid: mid == "info")
    def deliver(rid, actor, text, **kwargs):
        from management_portal.society_service.resident_message_contract import message_identity
        message_identity(kwargs["message_id"])
        sent.append((rid, actor, text, kwargs))
        pending.append({"message_id": kwargs["message_id"]})
        refs[kwargs["message_id"]] = kwargs["business_ref"]
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", deliver)
    return context, roles, records, states, refs, pending, sent


def test_coverage_is_per_input_and_no_change_does_not_advance_content(setup_work):
    """摘要按输入记录覆盖且内容未变时版本不递增"""
    context, roles, *_ = setup_work
    context.source.converse(A, _turn(1, "旧工作", "已维护旧模块"), 1)
    context.reconcile()
    _summarize(context, A, "维护旧模块")
    initial = work.maintenance_view(A, A)
    assert initial["revision"] == 1 and initial["summary"]["current_focus"] == "维护旧模块"
    result = work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["running"])
    assert result["revision"] == 2
    work.record_completed_work_round(A, "one")
    assert work.maintenance_view(A, A)["missing_message_ids"] == ["one"]
    result = work.update_resident_work_summary(A, A, message_ids=["one"])
    assert result["revision"] == 2 and not result["missing_message_ids"]
    work.record_completed_work_round(A, "one")
    assert not work.maintenance_view(A, A)["missing_message_ids"]
    with pytest.raises(ValueError, match="输入"):
        work.update_resident_work_summary(A, A, message_ids=["failed"])
    with pytest.raises(PermissionError):
        work.update_resident_work_summary(B, A, summary=SUMMARY, message_ids=["two"])


def test_completed_work_registration_is_idempotent_and_excludes_non_work(setup_work):
    """缺失摘要轮次共用下次输入提醒且排除维护工作"""
    context, roles, records, states, refs, pending, sent = setup_work
    for mid, ref in (("reminder", work.REMINDER_REF), ("rollup", work.ROLLUP_REF),
                     ("progress", "duty/family-progress")):
        states[mid], refs[mid] = "completed", ref
    states["info"] = "completed"
    for mid in ("missing", "failed", "running", "reminder", "rollup", "progress", "info", "one", "one"):
        work.record_completed_work_round(A, mid)
    work.record_completed_work_round(C, "one")
    records[B].pop("family")
    work.record_completed_work_round(B, "one")
    assert context.store.work_summary(A)["missing_message_ids"] == ["one"]
    assert not context.store.work_summary(B)["missing_message_ids"]
    assert not context.store.work_summary(C)["missing_message_ids"]
    assert not sent and not pending


def test_family_rollup_changes_only_when_material_changes_and_has_one_writer(setup_work):
    """Family 汇总仅在材料变化时更新且仅 steward 可写"""
    context, roles, *_ = setup_work
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    inputs = work.get_facade_summary_inputs(C, C)
    assert "expected_revision" not in inputs
    assert inputs["needs_update"]
    steward = roles["steward"][0]
    work.update_facade_summary(steward, C, summary=SUMMARY, source_versions=inputs["source_versions"])
    assert not work.get_facade_summary_inputs(C, C)["needs_update"]
    work.update_resident_work_summary(A, A, message_ids=["two"])
    assert not work.get_facade_summary_inputs(C, C)["needs_update"]
    work.update_resident_work_summary(A, A, summary={**SUMMARY, "recent_results": ["完成隔离"]}, message_ids=["two"])
    work.update_facade_summary(steward, C, summary=SUMMARY, source_versions=inputs["source_versions"])
    assert work.get_facade_summary_inputs(C, C)["needs_update"]
    with pytest.raises(PermissionError):
        work.update_facade_summary(A, C, summary=SUMMARY, source_versions=inputs["source_versions"])
    with pytest.raises(PermissionError):
        work.update_resident_work_summary(C, C, summary=SUMMARY, message_ids=["one"])
    with pytest.raises(ValueError, match="source_versions"):
        work.update_facade_summary(steward, C, summary=SUMMARY, source_versions={})


def test_standalone_facade_reads_visible_member_summaries_without_family(setup_work, monkeypatch):
    from management_portal import permission_system

    context, _, records, *_ = setup_work
    records[C].pop("family")
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["one"])
    monkeypatch.setattr(context.source, "authorized_resident_ids", lambda actor, ids: [A])
    monkeypatch.setattr(permission_system, "allows_roles",
                        lambda actor, roles: actor == "case-admin" and roles == [("@admin", C)])
    result = work.get_facade_summary_inputs(C, C)
    assert [member["resident_id"] for member in result["members"]] == [A]
    assert result["members"][0]["summary"]["current_focus"] == SUMMARY["current_focus"]
    assert B not in result["source_versions"]
    with pytest.raises(PermissionError):
        work.get_facade_summary_inputs(A, C)
    assert work.get_facade_summary_inputs("case-admin", C) == result
    with pytest.raises(PermissionError):
        work.update_facade_summary(C, C, SUMMARY, result["source_versions"])
    with pytest.raises(PermissionError):
        work.update_facade_summary("case-admin", C, SUMMARY, result["source_versions"])


def test_summary_instructions_dispatch_to_their_respective_writers(setup_work):
    from management_portal.hatg import catalog
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation
    from management_portal.society_service.isa import residents

    _, roles, *_ = setup_work
    steward = roles["steward"][0]

    def invoke(instruction, caller, resident_id, **fields):
        declaration = catalog.declaration(instruction)
        arguments = {"resident_id": resident_id, **fields}
        declaration.validate_arguments(arguments)
        invocation = Invocation(
            declaration=declaration,
            call=InstructionCall.from_payload({"instruction": instruction, "arguments": arguments}),
            context=InstructionContext(caller=caller, entry="web"),
            instruction_id="inst_summary_writer_contract",
        )
        return residents.HANDLERS[instruction](invocation)

    work_view = invoke("UpdateResidentWorkSummary", A, A, summary=SUMMARY, message_ids=["one"])
    assert work_view["layers"]["recent"] == SUMMARY
    consolidated = invoke("UpdateResidentSummaryConsolidation", steward, A,
                          consolidation={"phase": SUMMARY, "consolidated_message_ids": ["one"]})
    assert consolidated["layers"]["phase"] == SUMMARY
    inputs = invoke("GetFacadeSummaryInputs", steward, C)
    saved = invoke("UpdateFacadeSummary", steward, C, summary=SUMMARY, source_versions=inputs["source_versions"])
    assert saved["source_versions"] == inputs["source_versions"]


@pytest.mark.parametrize("role", ["router", "steward"])
def test_internal_roles_do_not_receive_summary_reminders_or_consolidation_work(setup_work, role, monkeypatch):
    """Router／Steward 均免写自身摘要，不进入提醒或归纳清单。"""
    context, roles, records, states, refs, pending, sent = setup_work
    router = roles[role][0]
    work.record_completed_work_round(router, "one")
    assert not context.store.work_summary(router)["missing_message_ids"]
    assert not sent
    original = context.store.work_summary
    def only_members(rid):
        assert rid not in roles["router"] + roles["steward"]
        return original(rid)
    monkeypatch.setattr(context.store, "work_summary", only_members)
    work.get_facade_summary_inputs(C, C, overview_only=True)
    with pytest.raises(PermissionError):
        work.update_resident_summary_consolidation(roles["steward"][0], router,
                                         consolidation={"phase": SUMMARY})


def test_reminder_uses_oldest_completed_gap_unless_a_reminder_is_held(setup_work):
    """摘要提醒按完成时间取最早缺口，重试同一 ID，挂起时省略

    :module: platform.core"""
    _, _, _, _, refs, pending, sent = setup_work
    work.record_completed_work_round(A, "one")
    work.record_completed_work_round(A, "two")
    first = work.maintenance_view(C, A)["reminder"]
    assert first["message_id"] == work.derived_message_identity(work.REMINDER_REF, A, "two")
    assert work.maintenance_view(C, A)["reminder"] == first
    refs[first["message_id"]] = work.REMINDER_REF
    pending.append({"message_id": first["message_id"]})
    assert "reminder" not in work.maintenance_view(C, A)
    work.update_resident_work_summary(A, A, summary=SUMMARY, message_ids=["two"])
    assert "reminder" not in work.maintenance_view(C, A)
    pending.clear()
    assert work.maintenance_view(C, A)["reminder"]["message_id"] == work.derived_message_identity(
        work.REMINDER_REF, A, "one",
    )
    assert not sent


def test_global_summary_jobs_delete_on_familize_but_internal_work_stays_readable(context):
    """加入 Family 后全局摘要任务删除且内部摘要可读"""
    context.source.converse(A, _turn(1, "登录", "修正登录"), 1)
    context.sync_catalog()
    context.reconcile()
    context.prepare_due()
    claim = next(j for j in context.claim_summary_jobs("writer")["jobs"] if j["resident_id"] == A)
    context.source.records[0]["discovery_visible"] = False
    context.sync_catalog()
    with pytest.raises(KeyError):
        context.submit_summary("writer", claim["claim_id"], SUMMARY, model_version="test", account_id="")
    context.store.write_work_summary(A, A, SUMMARY, ["one"], {})
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
    reminder = {"message_id": "summary-reminder", "content_parts": [{"type": "text", "text": "Record the round"}]}
    outcome, calls = _run(FAMILY_SUMMARY_CHECK_SCRIPT, {"event": asdict(event)}, {
        "GetResidentSummaries": {"summaries": [{"missing_message_ids": ["completed-input"], "reminder": reminder}]},
        "DeliverMessage": {"message_id": reminder["message_id"]},
    })
    assert outcome.status == "succeeded"
    assert calls == [
        ("GetResidentSummaries", {"resident_ids": [A], "include_maintenance": True}),
        ("DeliverMessage", {"resident_id": A, "business_ref": "duty/work-summary-reminder",
                            "activation_policy": "attach_to_next", "message": reminder}),
    ]
    assert outcome.outputs == {"status": "reminder_attached", "message_id": reminder["message_id"]}


def test_hourly_effect_skips_unchanged_and_busy_stewards():
    """每小时汇总跳过材料未变或 steward 忙碌的情况"""
    from tests.cases.test_family_router_script import _run
    from management_portal.society_service.family_definition import FAMILY_SUMMARY_ROLLUP_SCRIPT
    inputs = {"arguments": {"facade": C, "steward": B}, "event": {"payload": {}}}
    notification = {"message_id": "rollup", "content_parts": [{"type": "text", "text": "Consolidate"}]}
    for state, needs, expected in [("idle", False, "unchanged"), ("running", True, "busy"), ("idle", True, "requested")]:
        result, calls = _run(FAMILY_SUMMARY_ROLLUP_SCRIPT, inputs, {
            "ListResidentStates": {"states": [{"resident_id": B, "state": {"work_state": state, "queued_count": 0}}]},
            "GetFacadeSummaryInputs": {"needs_update": needs, "source_versions": {A: 1}, "notification": notification},
            "DeliverMessage": {"message_id": "rollup"},
        })
        assert result.status == "succeeded" and result.outputs["status"] == expected
        sent = [args for name, args in calls if name == "DeliverMessage"]
        assert bool(sent) == (expected == "requested")
        if sent:
            assert sent[0] == {"resident_id": B, "business_ref": "duty/family-work-summary", "message": notification}
