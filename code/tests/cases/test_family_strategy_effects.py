"""Family template strategies composed of public Effect calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    family_definition, family_strategy_state, family_task_dispatches, family_tasks,
)
from management_portal.society_service.lineage_effect_script import CallResult, Interpreter, parse


MEMBER = "resident_aaaaaaaaaaaa"
FACADE = "resident_bbbbbbbbbbbb"
STEWARD = "resident_cccccccccccc"
COMPLETED = "msg_" + "1" * 32
ROLLUP = "msg_" + "2" * 32
REMINDER = "msg_" + "3" * 32
ROUTER = "resident_dddddddddddd"
ARGUMENTS = {"facade": FACADE, "steward": STEWARD, "router": ROUTER,
             "members": [MEMBER], "threshold": 60}


def run(script, results, *, event=None, packet=None):
    calls = []

    def execute(request):
        calls.append((request.instruction, request.arguments))
        return CallResult(True, results[request.instruction])

    inputs = {"arguments": ARGUMENTS, "event": event or {}, "packet": packet or {}}
    result = Interpreter(parse(script), inputs, execute).run()
    assert result.status == "succeeded", result.error
    return result.outputs, calls


def test_missing_completed_round_attaches_the_same_reminder_on_replay():
    event = {"subject_resident_id": MEMBER, "payload": {"message_id": COMPLETED}}
    notification = {"message_id": REMINDER, "content_parts": [{"type": "text", "text": "摘要提醒"}]}
    results = {
        "GetResidentSummaries": {"summaries": [{"resident_id": MEMBER, "missing_message_ids": [COMPLETED],
                                               "reminder": notification}]},
        "DeliverMessage": {"message_id": REMINDER},
    }
    script = family_definition.FAMILY_SUMMARY_CHECK_SCRIPT
    outcome, calls = run(script, results, event=event)
    assert calls[0] == ("GetResidentSummaries", {"resident_ids": [MEMBER], "include_maintenance": True})
    name, delivery = calls[1]
    assert name == "DeliverMessage"
    assert delivery["resident_id"] == MEMBER
    assert delivery["activation_policy"] == "attach_to_next"
    assert delivery["business_ref"] == "duty/work-summary-reminder"
    assert delivery["message"] == notification
    assert outcome == {"status": "reminder_attached", "message_id": REMINDER}
    assert run(script, results, event=event)[1] == calls


def test_rollup_delivers_the_read_owners_message_without_rendering_a_second_one():
    notification = {"message_id": ROLLUP, "content_parts": [{"type": "text", "text": "本次摘要材料"}]}
    results = {
        "ListResidentStates": {"states": [{"state": {"work_state": "idle", "queued_count": 0}}]},
        "GetFacadeSummaryInputs": {"needs_update": True, "notification": notification},
        "DeliverMessage": {"message_id": ROLLUP},
    }
    outcome, calls = run(family_definition.FAMILY_SUMMARY_ROLLUP_SCRIPT, results)
    assert calls[-1] == ("DeliverMessage", {"resident_id": STEWARD, "message": notification,
                                          "business_ref": "duty/family-work-summary"})
    assert outcome == {"status": "requested", "message_id": ROLLUP}


def test_rollup_stops_for_busy_steward():
    results = {"ListResidentStates": {"states": [{"state": {"work_state": "running", "queued_count": 0}}]}}
    outcome, calls = run(family_definition.FAMILY_SUMMARY_ROLLUP_SCRIPT, results)
    assert outcome == {"status": "busy"}
    assert len(calls) == 1


def test_router_delivers_once_per_event_with_original_reference_and_request():
    event = {"id": "event_1234567890abcdef", "payload": {
        "resident_id": FACADE, "message_id": COMPLETED, "relay_depth": 0,
    }}
    result = {"request": {"author_id": "operator", "content_parts": [
        {"type": "text", "text": "保留原请求"},
        {"type": "resource", "address": "article_aaaaaaaaaaaa"},
    ]}}
    results = {"GetRelayContext": result, "DeliverMessage": {"message_id": ROLLUP}}
    script = family_definition.FAMILY_ROUTER_SCRIPT
    output, calls = run(script, results, event=event)
    assert calls[0] == ("GetRelayContext", {"resident_id": FACADE, "message_id": COMPLETED, "section": "request"})
    assert calls[1][0] == "DeliverMessage"
    assert calls[1][1]["resident_id"] == ROUTER
    assert calls[1][1]["business_ref"] == f"family-route/{FACADE}/message/{COMPLETED}"
    assert calls[1][1]["message"]["message_id"] == "msg_d0660000000000011234567890abcdef"
    assert "article_aaaaaaaaaaaa" in calls[1][1]["message"]["content_parts"][0]["text"]
    assert output["request_message_id"] == ROLLUP
    assert run(script, results, event=event)[1] == calls


def test_unknown_quota_continues_relay_and_low_quota_stops_for_review():
    event = {"id": "event_1234567890abcdef",
             "payload": {"input_business_ref": f"family-route/{FACADE}/message/{COMPLETED}"}}
    packet = {"message": {"data": {"target": MEMBER, "confidence": 90, "scope": "in",
                                    "continue_previous": True}}}
    script = family_definition.FAMILY_ROUTE_RELAY_SCRIPT
    for status, wanted in (("unknown", "RelayMessage"), ("quota_low", "DeliverMessage")):
        results = {"CheckRelayTarget": {"status": status, "remaining_percent": 3},
                   "RelayMessage": {"status": "relayed"}, "DeliverMessage": {"message_id": ROLLUP}}
        output, calls = run(script, results, event=event, packet=packet)
        assert calls[0] == ("CheckRelayTarget", {"resident_id": FACADE, "member_id": MEMBER})
        assert [name for name, _ in calls] == ["CheckRelayTarget", wanted]
        if status == "unknown":
            assert calls[1][1]["continue_previous"] is True
        else:
            assert output["state"] == "waiting_for_quota_review"
            assert calls[1][1]["resident_id"] == STEWARD


def test_migration_keeps_unfinished_intents_and_historical_completed_cursors():
    pending = {"steward": STEWARD, "activity": {MEMBER: ["old-time", COMPLETED]}, "message_id": ROLLUP}
    relay = {
        "activity_review": {"activity": {MEMBER: ["old-time", COMPLETED]}, "reviewed": {}, "pending": pending},
        "progress": {"count": 40, "reported_through": 40, "batches": {
            "20": {"status": "published"}, "40": {"status": "pending", "request_id": ROLLUP},
        }},
        "supervision": {"incidents": {"old": {"status": "resolved"}, "other": {"status": "resolved"},
                                      "active": {"status": "open"}},
                        "notice": {"message_id": REMINDER, "delivered": True, "incident_keys": ["old", "active"]}},
        "route_restorations": {COMPLETED: ["owned by relay"]},
    }
    reviews = {"requests": {COMPLETED: {"status": "waiting", "steward_message_id": ROLLUP},
                            REMINDER: {"status": "relayed"}},
               "scope_requests": {ROLLUP: {"status": "arranged"}}}
    before = deepcopy((relay, reviews))
    family_id = "fbi_aaaaaaaaaaaa"
    cells = family_strategy_state.plan_cells(
        family_id, relay, reviews, completed_cursor=lambda rid, mid: 41,
        root_status=lambda mid: "queued" if mid == COMPLETED else "cancelled",
        steward_status=lambda mid: "running",
    )
    assert {key.rsplit(".", 1)[1] for key in cells} == {"review", "progress", "recovery", "routing"}
    review = cells[f"family.{family_id}.review"]
    assert review["activity"] == {MEMBER: [41, COMPLETED]}
    assert review["pending"] == pending
    assert list(cells[f"family.{family_id}.progress"]["batches"]) == ["40"]
    recovery = cells[f"family.{family_id}.recovery"]
    assert list(recovery["incidents"]) == ["old", "active"]
    assert recovery["notice"]["message_id"] == REMINDER
    assert list(cells[f"family.{family_id}.routing"]["requests"]) == [COMPLETED]
    assert (relay, reviews) == before


def test_migration_fails_before_creating_missing_completion_evidence():
    relay = {"activity_review": {"activity": {MEMBER: ["old-time", COMPLETED]}, "reviewed": {}}}
    with pytest.raises(ValueError, match="completion receipt"):
        family_strategy_state.plan_cells(
            "fbi_aaaaaaaaaaaa", relay, {}, completed_cursor=lambda rid, mid: None,
            root_status=lambda mid: None, steward_status=lambda mid: None,
        )


def test_original_rollup_adoption_requires_the_whole_original_script():
    from tests.cases.test_isa_d066_definition_migration import ROLLUP_BEFORE_DELIVERY_CHECK, original_rollup_script

    for old in (original_rollup_script(), ROLLUP_BEFORE_DELIVERY_CHECK):
        assert family_definition.migrate_original_script(old) == family_definition.FAMILY_SUMMARY_ROLLUP_SCRIPT
        edited = old + '\noutput owner="custom"'
        assert family_definition.migrate_original_script(edited) == edited


def test_pending_frozen_dispatches_never_receive_a_replacement_message_id(monkeypatch):
    from management_portal.society_service.resident_message_contract import MessageIdentityConflict

    original = "msg_" + "4" * 32
    queued_root = "msg_" + "5" * 32
    orphan = "msg_" + "6" * 32
    invalid = "msg_" + "7" * 32

    def row(mid, root):
        return {"message_id": mid, "intent_json": {"request": {
            "target": ROUTER, "facade": FACADE, "trigger": "route",
            "business_ref": f"family-route/{FACADE}/message/{root}", "facts": {"message_id": root},
        }, "text": "冻结的路由任务"}}

    records = [row(ROLLUP, queued_root), row(original, COMPLETED), row(orphan, queued_root),
               row(invalid, COMPLETED)]
    connection = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: records))
    states = {(ROUTER, ROLLUP): {"state": "queued"}, (FACADE, COMPLETED): {"state": "completed"},
              (FACADE, queued_root): {"state": "queued"}}
    repository = SimpleNamespace(input_state=lambda rid, mid: states[rid, mid])

    def validated(repo, target, facade, mid, text, ref, *, allow_missing_business_ref=False):
        assert not allow_missing_business_ref
        assert (repo, target, facade) == (repository, ROUTER, FACADE)
        if mid == invalid:
            raise MessageIdentityConflict("另一个逻辑输入")
        return ({"message_id": mid} if mid == ROLLUP else None), None

    monkeypatch.setattr(family_task_dispatches, "_validate_original", validated)
    readers = family_task_dispatches.live_dispatch_readers(repository)
    actual = family_task_dispatches.plan_legacy_dispatches(connection, "lineage_aaaaaaaaaaaa", *readers)
    assert [(x["message_id"], x["state"]) for x in actual] == [
        (ROLLUP, "accepted"), (original, "expired"), (orphan, "blocked"), (invalid, "blocked"),
    ]
    assert actual[2]["reason"] == "unaccepted_frozen_intent"
    assert actual[3]["reason"] == "invalid_frozen_intent"


def test_state_cutover_persists_on_the_callers_transaction_and_rejects_a_conflict():
    from management_common.portal_database import portal_database
    from management_common.json_columns import json_text, json_value

    db = portal_database(schema="lineage")
    lineage_id, family_id = "lineage_aaaaaaaaaaaa", "fbi_aaaaaaaaaaaa"
    key = f"family.{family_id}.progress"
    relay = {"progress": {"count": 20, "reported_through": 20,
                          "batches": {"20": {"status": "pending", "request_id": ROLLUP}}}}
    with db.transaction() as connection:
        connection.execute("CREATE TEMP TABLE families (family_id text, lineage_id text, deleted_at timestamptz) ON COMMIT DROP")
        connection.execute("CREATE TEMP TABLE family_residents (family_id text, resident_id text, role text) ON COMMIT DROP")
        connection.execute("CREATE TEMP TABLE family_relays (lineage_id text, resident_id text, relay_json jsonb) ON COMMIT DROP")
        connection.execute("CREATE TEMP TABLE family_quota_reviews (lineage_id text, resident_id text, review_json jsonb) ON COMMIT DROP")
        connection.execute("CREATE TEMP TABLE lineage_shared_state (lineage_id text PRIMARY KEY, cells_json jsonb) ON COMMIT DROP")
        connection.execute("INSERT INTO families VALUES (%s, %s, NULL)", (family_id, lineage_id))
        connection.execute("INSERT INTO family_residents VALUES (%s, %s, 'facade'), (%s, %s, 'steward')",
                           (family_id, FACADE, family_id, STEWARD))
        connection.execute("INSERT INTO family_relays VALUES (%s, %s, %s)", (lineage_id, FACADE, json_text(relay)))
        assert family_strategy_state.migrate_family_state(connection, lineage_id) == [key]
        assert family_strategy_state.migrate_family_state(connection, lineage_id) == []
        state = json_value(connection.execute("SELECT cells_json FROM lineage_shared_state").fetchone()["cells_json"])
        assert state["revision"] == 1 and state["cells"][key] == relay["progress"]
        state["cells"][key]["count"] = 21
        connection.execute("UPDATE lineage_shared_state SET cells_json=%s", (json_text(state),))
        with pytest.raises(ValueError, match="conflicts with Shared State"):
            family_strategy_state.migrate_family_state(connection, lineage_id)
        assert json_value(connection.execute("SELECT relay_json FROM family_relays").fetchone()["relay_json"]) == relay


def test_only_a_complete_stock_guide_is_adopted():
    old = family_tasks.TASKS["queue"][3].replace("RelayMessage(", "RelayFamilyMessage(")
    assert family_tasks.saved_fields("queue", {"guide": old}) == {}
    edited = old + "请把整合队列保持到下周。"
    assert family_tasks.saved_fields("queue", {"guide": edited}) == {"guide": edited}


_OLD_TASK_SUFFIX = "\n${facts}\n指南：${guide_path}（${guide_version}）"
_OLD_SUMMARY_GUIDE_HEAD = (
    "用 GetFamilySummaryInputs(resident_id=facade, overview_only=true) 找到待归纳成员，再按需读取"
    "GetResidentSummaries(include_maintenance=true)。仅业务成员记录近期事实；Router 和 Steward 不维护自身摘要。"
    "Steward 归纳业务成员阶段与长期层；用 UpdateResidentWorkSummary 的 consolidation 和 expected_revision 提交，覆盖已归纳片段。"
)
_OLD_SUMMARY_GUIDE_TAIL = (
    "归纳后重读 GetFamilySummaryInputs；facade_needs_update 为真时，用返回的 expected_revision、"
    "source_versions 和九字段 summary 调用 UpdateResidentWorkSummary 更新门面。版本冲突后重读重算；"
    "材料未变不写入，不按输入批次另发简报，不群发。"
)


@pytest.mark.parametrize(("trigger", "field", "value", "digest"), [
    ("route", "prompt", "为原请求选择承接成员，输出 route packet。" + _OLD_TASK_SUFFIX,
     "64bfcbadd36e19f53f87bc621fd037e1ce58ef065d073595d7e59ae9d4ff95d2"),
    ("summary", "prompt", "按需归纳业务成员摘要并更新 Family 门面，在总预算内保留有效认知。" + _OLD_TASK_SUFFIX,
     "cf48802dec533aac5ab7491cf3c1469a37a1173316167850583eaf330ff733c9"),
    ("summary", "guide", _OLD_SUMMARY_GUIDE_HEAD +
     "所有层与字段共用总字数预算；保留未完成承诺、阻塞、重要约束及来源。项目长记录独立保存，"
     "摘要只留当前结论与入口。达到预算时重写取舍，不按时间删除未解决事项。" + _OLD_SUMMARY_GUIDE_TAIL,
     "e78bfbfb5fabdbbd74e69f4bcda2b60d0f4a45014fba49e85f0e34a91b6d677f"),
    ("summary", "guide", _OLD_SUMMARY_GUIDE_HEAD +
     "各层篇幅参考值只用于凝练提醒，不限制保存；保留未完成承诺、阻塞、重要约束及来源。项目长记录独立保存，"
     "摘要只留当前结论与入口。篇幅较长时归纳重写，不为满足参考值删除未解决事项。" + _OLD_SUMMARY_GUIDE_TAIL,
     "8055e241411b6a1fa20a051f304cf2014442a4253dd1df12a529073b1b3c9b23"),
])
def test_historical_stock_bindings_match_only_complete_values(trigger, field, value, digest):
    import hashlib

    from management_portal.society_service.instruction_name_migration import normalize_prompt

    assert hashlib.sha256(value.encode()).hexdigest() == digest
    assert family_tasks.saved_fields(trigger, {field: value}) == {}
    assert family_tasks.saved_fields(trigger, {field: normalize_prompt(value)}) == {}
    edited = {field: value + "\n补充一项自定义约束。"}
    assert family_tasks.saved_fields(trigger, edited) == edited
    other_trigger = "route" if trigger == "summary" else "summary"
    assert family_tasks.saved_fields(other_trigger, {field: value}) == {field: value}
