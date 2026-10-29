"""The Family Router (decision_44cf587c1721): a Router Resident between two script Effects.

The Router Effect gathers what the choice needs and asks the Router Resident;
the Resident answers with a Packet; the relay Effect hands the facade
message to the member the Packet names.  The model work is the Router
Resident's own turn, so its Provider and model are its own settings (CASE-A 10-06).

:module: lineage.hatg
"""

from __future__ import annotations

import json

import pytest

from management_portal.society_service import family_definition as builtins

script_module = pytest.importorskip("management_portal.society_service.lineage_effect_script")

MEMBERS = ["resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"]
STEWARD = "resident_cccccccccccc"
FACADE = "resident_dddddddddddd"
ROUTER = "resident_eeeeeeeeeeee"
MESSAGE_ID = "msg_" + "1" * 16
PARTS = [{"type": "text", "text": "登录页 500 了"}]
ROUNDS = [{"kind": "request", "message_id": "msg_" + "0" * 16, "created_at": "2026-10-12T11:14:52Z", "request": "登录页白屏",
           "answered_by": {"resident_id": MEMBERS[1]}, "state": "completed", "reply": "已修复"}]


def _call(calls, instruction):
    return next(arguments for name, arguments in calls if name == instruction)


def _run(script: str, inputs: dict, results: dict, failing: dict | None = None):
    calls = []

    def execute(request):
        calls.append((request.instruction, request.arguments))
        error = (failing or {}).get(request.instruction)
        if error:
            return script_module.CallResult(False, {}, {"code": error, "message": "不行"})
        result = results[request.instruction]
        return script_module.CallResult(True, result(request.arguments) if callable(result) else result)

    outcome = script_module.Interpreter(script_module.parse(script), inputs, execute).run()
    return outcome, calls


SUMMARIES = [{"resident_id": MEMBERS[0], "title": "登录", "summary": "负责登录", "summary_updated_at": "t1"},
             {"resident_id": MEMBERS[1], "title": "页面", "summary": "负责页面", "summary_updated_at": "t1"}]
GUIDE = {"routing_rules": ["登录相关的问题交给权限内核"]}
ASKED = "msg_" + "2" * 16


def _ask(relay_depth: int = 0, *, failing: dict | None = None, parts: list | None = None, states: list | None = None,
         quoted: str = "", members: list | None = None, rounds: list | None = None, quote: dict | None = None,
         record: dict | None = None, in_context: dict | None = None, summaries: list | None = None,
         guide: dict | None = None, goal: str = "让登录稳定", direct: list | None = None, continued: str = ""):
    """One Router Effect activation; ``record`` is what the Router was told before, ``in_context`` its messages' fate."""

    message = {"content_parts": parts or PARTS, "author_id": "CASE-A", "created_at": "2026-10-12T11:22:40Z",
               **({"quote": quote} if quote else {})}
    cells = {f"family_guide_{FACADE}": GUIDE if guide is None else guide,
             f"family_corrections_{FACADE}": [], f"family_router_context_{FACADE}": record}
    results = {
        "GetMessage": {"message": message},
        "ListRecentRounds": {"rounds": ROUNDS if rounds is None else rounds, "direct": direct or []},
        "GetResidentSummaries": {"summaries": SUMMARIES if summaries is None else summaries},
        "ListResidentStates": {"states": states or []},
        "GetSharedState": lambda arguments: {"value": cells[arguments["key"]]},
        "GetInputState": lambda arguments: {"input_state": {
            "state": "completed", "in_context": (in_context or {}).get(arguments["message_id"], False)}},
        "DeliverMessage": {"message_id": ASKED, "resident_id": ROUTER},
        "DispatchFamilyTask": {"message_id": ASKED, "resident_id": ROUTER},
        "UpdateSharedState": lambda arguments: {"key": arguments["key"], "value": arguments["value"], "changed": True},
        "RelayFamilyMessage": {"status": "relayed"},
    }
    inputs = {
        "arguments": {
            "members": MEMBERS if members is None else members, "router": ROUTER, "lineage_id": "lineage_000000000001",
            "guide_key": f"family_guide_{FACADE}", "corrections_key": f"family_corrections_{FACADE}",
            "context_key": f"family_router_context_{FACADE}", "family": "登录 Family", "goal": goal,
        },
        "event": {"payload": {"resident_id": FACADE, "message_id": MESSAGE_ID, "relay_depth": relay_depth,
                              "quoted_member_id": quoted, "continued_member_id": continued}},
        "packet": {},
        "endpoint": {},
    }
    return _run(builtins.FAMILY_ROUTER_SCRIPT, inputs, results, failing)


def _relay(payload: dict, *, prompt: str = "负责登录", threshold: int = 60, relay_status: str = "relayed"):
    results = {
        "GetMessage": {"message": {"content_parts": PARTS, "author_id": "CASE-A"}},
        "DeliverMessage": {"message_id": "msg_" + "3" * 16},
        "DispatchFamilyTask": {"message_id": "msg_" + "3" * 16},
        "RelayFamilyMessage": {"status": relay_status},
    }
    inputs = {
        "arguments": {"members": MEMBERS, "steward": STEWARD, "threshold": threshold},
        # The completion names the Router input and its explicit immutable business reference.
        "event": {"subject_resident_id": ROUTER, "payload": {"message_id": ASKED,
                  "input_business_ref": f"family-route/{FACADE}/message/{MESSAGE_ID}"}},
        "packet": {"key": "route", "message": {"text": prompt, "data": payload}},
        "endpoint": {},
    }
    return _run(builtins.FAMILY_ROUTE_RELAY_SCRIPT, inputs, results)


def test_the_router_effect_asks_its_router_resident_with_what_the_choice_needs():
    """持续角色按需取材，Effect 保留原请求绑定与路由保护。"""
    outcome, calls = _ask()
    assert outcome.status == "succeeded"
    assert calls == [("DispatchFamilyTask", {"resident_id": FACADE, "trigger": "route", "source_message_id": MESSAGE_ID})]


def test_router_selection_does_not_depend_on_cached_context():
    """持续角色按需取材，Effect 保留原请求绑定与路由保护。"""
    for record in ({}, {"head": "旧规则"}, {"last_message_id": "gone"}):
        outcome, calls = _ask(record=record, in_context={ASKED: False})
        assert outcome.status == "succeeded"
        assert [n for n, _ in calls] == ["DispatchFamilyTask"]


def test_one_member_still_routes_organization_requests():
    """一个业务成员也需要区分本 Steward 的组织职责。"""
    outcome, calls = _ask(members=MEMBERS[:1])
    assert outcome.status == "succeeded"
    assert calls == [("DispatchFamilyTask", {"resident_id": FACADE, "trigger": "route", "source_message_id": MESSAGE_ID})]
    failed, calls = _ask(members=MEMBERS[:1], failing={"DispatchFamilyTask": "instruction.forbidden"})
    assert failed.status == "failed" and len(calls) == 1
    failed, calls = _ask(members=MEMBERS[:1], relay_depth=4)
    assert failed.status == "failed" and calls == []


def test_empty_roster_still_routes_to_steward():
    """空名册的组织请求仍可交给本 Steward。"""
    outcome, calls = _ask(members=[])
    assert outcome.status == "succeeded"
    assert calls == [("DispatchFamilyTask", {"resident_id": FACADE, "trigger": "route", "source_message_id": MESSAGE_ID})]


def test_organization_route_accepts_only_own_steward():
    """本 Steward 是组织请求候选，其他 Family 的角色仍被拒绝。"""
    outcome, calls = _relay({"target": STEWARD, "confidence": 100, "scope": "in"})
    assert outcome.status == "succeeded"
    assert _call(calls, "RelayFamilyMessage")["member_id"] == STEWARD
    outcome, calls = _relay({"target": "resident_ffffffffffff", "confidence": 100, "scope": "in"})
    assert outcome.status == "failed"
    assert [name for name, _ in calls] == ["DispatchFamilyTask"]


def test_the_relay_hands_the_facade_message_to_the_member_the_router_named():
    """Router Resident 的 Proposal 选定成员后，转交 Effect 把门面消息交给该成员

    Router Resident 回复 route Proposal（payload.target、confidence，prompt 为理由）；proposal 连线激活的转交 Effect 读取原门面消息，以原发送者身份带 content_parts 投递给目标成员，输出 target、confidence 与 reason。

    :design: decision_44cf587c1721, decision_4adcc1ff2f2c
    """
    outcome, calls = _relay({"target": MEMBERS[0], "confidence": 90})

    assert outcome.status == "succeeded"
    assert [name for name, _ in calls] == ["RelayFamilyMessage"]
    assert _call(calls, "RelayFamilyMessage") == {
        "resident_id": FACADE, "message_id": MESSAGE_ID, "member_id": MEMBERS[0],
    }
    assert outcome.outputs == {"target": MEMBERS[0], "confidence": 90, "reason": "负责登录"}


def test_low_confidence_also_notifies_the_steward_and_deep_relays_fail():
    """持续角色按需取材，Effect 保留原请求绑定与路由保护。"""
    outcome, calls = _relay({"target": MEMBERS[0], "confidence": 40})
    assert outcome.status == "succeeded"
    assert calls[-1][0] == "DispatchFamilyTask"
    assert calls[-1][1]["trigger"] == "route_review"
    assert calls[-1][1]["facts"]["confidence"] == 40
    failed, none = _ask(relay_depth=4)
    assert failed.status == "failed" and none == []


def test_a_request_no_member_owns_waits_for_the_steward_instead_of_a_member():
    """职责空缺的路由请求等待 steward 且不重复审核

    The Router finds no member's duties cover it: the facade holds it for the steward (CASE-A 10-11)."""

    outcome, calls = _relay({"target": MEMBERS[1], "confidence": 30, "scope": "out", "gap": "数据库迁移"},
                            prompt="没有人负责数据库", relay_status="waiting_for_steward")

    assert outcome.status == "succeeded"
    # The platform tells the steward; no low-confidence review goes out on top of it.
    assert [name for name, _ in calls] == ["RelayFamilyMessage"]
    assert _call(calls, "RelayFamilyMessage") == {
        "resident_id": FACADE, "message_id": MESSAGE_ID, "member_id": MEMBERS[1],
        "scope_gap": "数据库迁移", "reason": "没有人负责数据库",
    }
    assert outcome.outputs == {"target": MEMBERS[1], "state": "waiting_for_steward", "scope": "out",
                               "gap": "数据库迁移", "reason": "没有人负责数据库"}
    _outcome, calls = _relay({"target": MEMBERS[1], "scope": "out"}, relay_status="waiting_for_steward")
    assert _call(calls, "RelayFamilyMessage")["scope_gap"] == "Router 未写明"
    # An in-scope answer relays as before.
    _outcome, calls = _relay({"target": MEMBERS[0], "confidence": 90, "scope": "in"})
    assert "scope_gap" not in _call(calls, "RelayFamilyMessage")


def test_the_router_rules_ask_for_the_scope_and_two_waiting_requests_signal_the_steward():
    """Router 规则要求判断职责范围且两条排队请求通知 steward"""
    from management_portal.society_service.family_tasks import TASKS
    rules = TASKS["route"][3]
    assert '"scope":"in"' in rules and 'scope="out"' in rules
    queue = next(item for item in builtins.FAMILY_MEMBER_SIGNALS if item[0] == "member_queue")
    # The source fires above the threshold: two waiting requests (CASE-A 10-11).
    assert queue[2]["queue_length_threshold"] == 1


def test_a_request_stopped_while_routed_ends_the_relay_without_a_review():
    """选择成员期间被停止的请求，转交 Effect 正常结束且不通知 steward

    RelayFamilyMessage 回报 withdrawn 时转交 Effect 输出 state=withdrawn 并结束，不因低置信度通知 steward。

    Its sender stopped it while the Router chose (CASE-A 10-09): nobody gets it, the steward hears nothing."""

    outcome, calls = _relay({"target": MEMBERS[0], "confidence": 40}, relay_status="withdrawn")

    assert outcome.status == "succeeded"
    assert [name for name, _ in calls] == ["RelayFamilyMessage"]
    assert outcome.outputs == {"target": MEMBERS[0], "state": "withdrawn"}


def test_an_answer_naming_no_member_goes_to_the_steward_and_relays_nothing():
    """持续角色按需取材，Effect 保留原请求绑定与路由保护。"""
    for payload in ({}, {"target": "resident_ffffffffffff", "confidence": 99}):
        outcome, calls = _relay(payload)
        assert outcome.status == "failed"
        assert [n for n, _ in calls] == ["DispatchFamilyTask"]
        assert calls[0][1]["trigger"] == "route_review"
        assert calls[0][1]["source_message_id"] == MESSAGE_ID


def test_a_complete_router_choice_missing_only_the_closing_tag_relays_once():
    """Router 选择包仅缺结束标签时仍只转交一次

    :module: platform.core"""
    from management_portal.society_service.lineage_primitives import parse_reply_packets

    reply = '<packet>' + json.dumps({"key": "route", "message": {
        "text": "负责登录", "data": {"target": MEMBERS[0], "confidence": 99, "scope": "in"}},
        "events": [], "parent_packet_ids": []})
    packet = parse_reply_packets(reply)[0]
    outcome, calls = _relay(packet.message["data"], prompt=packet.message["text"])
    assert outcome.status == "succeeded"
    assert [name for name, _ in calls] == ["RelayFamilyMessage"]
    assert _call(calls, "RelayFamilyMessage")["message_id"] == MESSAGE_ID


def test_the_family_block_builds_the_router_from_a_resident_and_two_effects():
    """内置 Family 模块用 Router Resident 加两个 Effect 实现路由

    Family 模块新建 router 槽位（Codex，初始模型 gpt-6.1-sol、推理强度 medium，不指定账号，之后是 Resident 自己的设置）；门面收到消息的连线并行激活 Router Effect，Router 的 proposal 连线激活转交 Effect 且不把理由附进消息；模块不再有 router_api_id / router_model 参数，也不调用 CompleteChat。

    :design: decision_44cf587c1721, decision_5b5653c3eaa5
    """
    from management_portal.society_service.templates.familize import reconcile_in_body
    block = {"id": "fbi_111111111111", "elements": [],
             "family": {"facade": FACADE, "steward": STEWARD, "router": ROUTER, "members": MEMBERS,
                        "goal": "登录稳定", "title": "登录", "route_threshold": 60, "review_interval_seconds": 86400}}
    body = {"lineage_id": "lineage_000000000001", "program": {}, "function_blocks": [block]}
    reconcile_in_body(body, block, "CASE-A")
    ids = {e["key"]: e["id"] for e in block["elements"]}
    route = next(w for w in body["program"]["wires"] if w["id"] == ids["route"])
    relay = next(w for w in body["program"]["wires"] if w["id"] == ids["relay"])
    assert (route["source"], route["target"]) == (ids["facade_inbox"], ids["router"])
    assert route["config"]["parallel"] is True
    arguments = route["config"]["invoke_profiles"]["effect"]["arguments"]
    assert arguments["router"] == {"value": ROUTER}
    assert arguments["family"] == {"value": "登录"}
    assert "context_key" not in arguments and "guide_key" not in arguments
    assert (relay["kind"], relay["source"], relay["target"]) == ("route", ROUTER, ids["route_relay"])
    assert relay["config"]["invoke_profiles"]["effect"]["arguments"]["prompt"] == {"value": ""}
    assert "CompleteChat" not in json.dumps(body, ensure_ascii=False)


def test_a_router_choice_still_waiting_to_be_relayed_does_not_drop_the_next():
    """一个 Router 选择还在等待转交时，下一个选择照常转交

    转交连线上有一条较早的选择还在排队（例如投递失败后等待重试）时，Router 的下一次完成事件仍然送到转交 Effect，
    不会因为连线有积压而被丢弃；10-16 lineage_75e3399f4877 的门面请求就是这样丢失的。

    :design: decision_44cf587c1721, decision_4adcc1ff2f2c
    """
    from management_portal.society_service import lineage_event_routing, lineage_primitives
    from management_portal.society_service.templates.familize import reconcile_in_body
    block = {"id": "fbi_111111111111", "elements": [],
             "family": {"facade": FACADE, "steward": STEWARD, "router": ROUTER, "members": MEMBERS,
                        "goal": "登录稳定", "title": "登录", "route_threshold": 60, "review_interval_seconds": 86400}}
    body = {"lineage_id": "lineage_000000000001", "program": {}, "function_blocks": [block]}
    reconcile_in_body(body, block, "CASE-A")
    ids = {e["key"]: e["id"] for e in block["elements"]}
    relay = next(w for w in body["program"]["wires"] if w["id"] == ids["relay"])
    route = {"wire_id": relay["id"], "target_id": relay["target"], "target_kind": "effect", "config": relay["config"]}
    waiting = {relay["id"]: {"pending_count": 1, "oldest_cursor": "router-choice-1", "oldest_event_source_id": "event_source_router"}}
    event = lineage_primitives.LineageEvent(
        id="event_router_choice_2", type="resident.work_finished", subject_resident_id=ROUTER,
        occurred_at="2026-10-16T01:49:04Z", payload={"message_id": "msg_00000000000000f2"},
    )
    selection = lineage_event_routing.select_event_routes(
        "lineage_000000000001", "event_source_router", [route], event, "router-choice-2", waiting,
        bypass_unavailable_policy=True, can_consume_target=lambda _route: (True, ""),
    )
    assert [item["wire_id"] for item in selection.available_routes] == [relay["id"]]
    assert selection.unavailable_results == []


def test_a_missing_routing_guide_or_history_does_not_stop_routing():
    """持续角色按需取材，Effect 保留原请求绑定与路由保护。"""
    outcome, calls = _ask(failing={"GetSharedState": "missing", "ListRecentRounds": "missing"})
    assert outcome.status == "succeeded"
    assert [n for n, _ in calls] == ["DispatchFamilyTask"]


def test_a_request_with_an_image_goes_to_a_member_that_reads_images():
    """持续角色按需取材，Effect 保留原请求绑定与路由保护。"""
    outcome, calls = _ask(parts=[{"type": "image", "resource_id": "original-image"}])
    assert outcome.status == "succeeded"
    assert calls[0][1]["source_message_id"] == MESSAGE_ID



def test_a_quoted_members_message_goes_back_to_it_without_the_router_resident():
    """引用成员消息的请求不经 Router Resident 直接交回该成员

    到达事件带 quoted_member_id 时 Router Effect 优先直接交回对应成员或 Steward，置信度 100，不询问 Router Resident；该规则在单成员和业务成员为空时同样优先。

    :importance: P1
    :design: decision_44cf587c1721
    """
    # An explicit quote wins even with one member or while business members
    # are empty: the steward still needs to receive answers to its questions.
    for members in (MEMBERS, MEMBERS[:1], []):
        outcome, calls = _ask(quoted=MEMBERS[1], members=members)
        assert outcome.status == "succeeded"
        assert [name for name, _ in calls] == ["RelayFamilyMessage"]
        assert calls[-1][1]["member_id"] == MEMBERS[1] and outcome.outputs["confidence"] == 100


def test_a_residents_continuing_request_goes_to_its_member_without_the_router_resident():
    """Resident 接着发来的请求不经 Router Resident 交给原承接成员

    到达事件带 continued_member_id 时 Router Effect 直接交给该成员，置信度 100，不询问 Router Resident；同时带引用成员时引用优先。

    :importance: P1
    :design: decision_44cf587c1721
    """
    outcome, calls = _ask(continued=MEMBERS[0])
    assert outcome.status == "succeeded"
    assert [name for name, _ in calls] == ["RelayFamilyMessage"]
    assert calls[-1][1]["member_id"] == MEMBERS[0] and outcome.outputs["confidence"] == 100
    outcome, calls = _ask(quoted=MEMBERS[1], continued=MEMBERS[0])
    assert [arguments["member_id"] for _, arguments in calls] == [MEMBERS[1]]


def test_the_familys_calls_to_its_steward_and_router_come_from_the_facade():
    """Family 给 steward 和 Router 的调用以门面发出

    转交给成员的消息保留原发送者；Family Effect 给 steward 的通知、向 Router Resident 的选择请求及对 Router 的 GetInputState 以门面身份调用。

    Relays keep the requester as sender; asking the Router and the steward's note are the Family's own traffic.

    :design: decision_44cf587c1721, decision_4adcc1ff2f2c
    """

    from management_portal.hatg.contracts import InstructionContext
    from management_portal.society_service.lineage_program_effect_execution import _family_note_context

    lineage = {"program": {"components": [{"id": "effect_router00001", "kind": "effect"}, {"id": "effect_other000001", "kind": "effect"}]},
               "function_blocks": [{"id": "fbi_family0001", "elements": [{"id": "effect_router00001", "kind": "component", "key": "router"}],
                                    "family": {"facade": FACADE, "steward": STEWARD, "router": ROUTER, "members": MEMBERS}}]}
    requester = InstructionContext(caller="CASE-A", entry="lineage", trigger_path_id="run_1")
    here = "lineage_000000000001"

    def caller(effect_id, instruction, target):
        return _family_note_context(lineage, effect_id, instruction, {"resident_id": target}, requester,
                                    lineage_id=here).caller

    assert caller("effect_router00001", "DeliverMessage", STEWARD) == FACADE
    assert caller("effect_router00001", "DeliverMessage", ROUTER) == FACADE
    # Whether the brief is still in the Router's context is the Family's own question (CASE-A 10-12).
    assert caller("effect_router00001", "GetInputState", ROUTER) == FACADE
    assert caller("effect_router00001", "GetInputState", STEWARD) == "CASE-A"
    # The Router is no longer cleared, so nothing clears it as the facade.
    assert caller("effect_router00001", "ClearContext", ROUTER) == "CASE-A"
    assert caller("effect_router00001", "DeliverMessage", MEMBERS[0]) == "CASE-A"
    assert caller("effect_other000001", "DeliverMessage", ROUTER) == "CASE-A"
    assert caller("effect_router00001", "GetMessage", STEWARD) == "CASE-A"

    def state_caller(effect_id, instruction, key, lineage_id=here):
        return _family_note_context(lineage, effect_id, instruction, {"lineage_id": lineage_id, "key": key},
                                    requester, lineage_id=here).caller

    # The Family's own State Cells are read and written as the facade, whoever sent the message.
    for prefix in ("family_corrections_",):
        assert state_caller("effect_router00001", "GetSharedState", prefix + FACADE) == FACADE
    assert state_caller("effect_router00001", "UpdateSharedState", f"family_router_context_{FACADE}") == "CASE-A"
    # Another cell, another Family's cell, another Lineage or another component keep the requester.
    assert state_caller("effect_router00001", "UpdateSharedState", "notes") == "CASE-A"
    assert state_caller("effect_router00001", "GetSharedState", "family_guide_resident_ffffffffffff") == "CASE-A"
    assert state_caller("effect_router00001", "GetSharedState", f"family_guide_{FACADE}", "lineage_000000000002") == "CASE-A"
    assert state_caller("effect_other000001", "UpdateSharedState", f"family_router_context_{FACADE}") == "CASE-A"
    assert state_caller("effect_router00001", "DeleteSharedState", f"family_router_context_{FACADE}") == "CASE-A"
