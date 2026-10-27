"""Familize and Unfamilize through the ISA (decision_44cf587c1721, decision_5b5653c3eaa5).

:module: lineage.hatg
"""

from __future__ import annotations

import math

import pytest

from management_portal.society_service import family_facade, resident_execution_domain
from management_portal.society_service.resident_workspace_domain import find_resident as _find_resident
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_isa_lineage_lifecycle import (  # noqa: F401 - lineage is a fixture
    future_creation_store,
    graph,
    lineage,
    resident,
)


@pytest.fixture
def charters(monkeypatch):
    sent = []
    monkeypatch.setattr(
        resident_execution_domain, "deliver_resident_message_content",
        lambda resident_id, actor, content, **options: sent.append(
            (resident_id, content, options.get("activation_policy", ""), actor),
        ) or {},
    )
    return sent


def _wire(lineage_id: str, target: str, key: str, *, shared_with: str) -> str:
    source = ok("CreateComponent", {"lineage_id": lineage_id, "kind": "event_source", "title": "Start",
                                    "config": {"event_type": "manual.triggered"}}, key=f"{key}-source")["component_id"]
    # A shared source remains outside the Family and exercises its public entry.
    ok("CreateWire", {"lineage_id": lineage_id, "kind": "route", "source": source,
                      "target": shared_with}, key=f"{key}-shared")
    return ok("CreateWire", {'lineage_id': lineage_id, 'kind': 'route', 'source': source, 'target': target},
              key=key)["wire_id"]


def _target_of(lineage_id: str, wire_id: str) -> str:
    return next(item for item in graph(lineage_id)["program"]["wires"] if item["id"] == wire_id)["target"]


def test_facade_rename_updates_family_title_and_survives_parameter_changes(lineage, charters):
    """门面改名同步 Family 标题且后续参数更新保留名称"""
    member = resident(lineage, "Member")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [member], "title": "Original"})
    facade = family["facade_resident_id"]
    ok("UpdateResident", {"lineage_id": lineage, "resident_id": facade, "title": "Renamed"})
    block = next(item for item in graph(lineage)["function_blocks"]
                    if item["id"] == family["family_id"])
    assert block["title"] == "Renamed"
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": family["family_id"],
                                      "goal": "Updated goal"})
    assert _find_resident(facade)["title"] == "Renamed"
    assert family_facade.chat_presentation(_find_resident(facade))["title"] == "Renamed"
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": family["family_id"],
                                      "title": "Edited in block menu"})
    assert _find_resident(facade)["title"] == "Edited in block menu"
    assert family_facade.chat_presentation(_find_resident(facade))["title"] == "Edited in block menu"


def test_an_untitled_family_is_named_after_a_member_and_a_lineage_family_after_its_lineage(lineage, charters):
    """未指定标题时，选区 Family 以随机成员名称加 -family 命名，整条 Lineage 的 Family 用 Lineage 标题"""
    first, second, third = (resident(lineage, name) for name in ("Auth", "Pages", "Menu"))
    named = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second]})
    template = next(t for t in ok("ListTemplates", {})["templates"] if t.get("builtin_key") == "family")
    applied = ok("ApplyTemplate", {"lineage_id": lineage, "template_id": template["template_id"],
                                   "target": {"kind": "resident", "resident_ids": [third]}})
    blocks = {block["id"]: block["family"] for block in graph(lineage)["function_blocks"]}
    assert blocks[named["family_id"]]["title"] in {"Auth-family", "Pages-family"}
    assert _find_resident(named["facade_resident_id"])["title"] == blocks[named["family_id"]]["title"]
    assert blocks[applied["family_id"]]["title"] == "Menu-family"

    whole = ok("CreateLineage", {"title": "Whole team", "project_id": "project_test"}, key="whole")["lineage_id"]
    resident(whole, "Member")
    family = ok("Familize", {"lineage_id": whole, "scope": "lineage"})
    assert next(block["family"]["title"] for block in graph(whole)["function_blocks"]
                if block["id"] == family["family_id"]) == "Whole team"


def test_familize_hands_leadership_and_wires_to_the_facade_and_unfamilize_returns_them(lineage, charters):
    """Familize 把 leader 与外部连线交给门面，Unfamilize 交还

    Familize 创建门面与 steward、转移 leader 与自动运行身份、改接顶层连线，并由门面向 steward 投递章程（info 只写内容，性质由投递方式表达）；周期回顾的结果只写入记忆库，只有需要 owner 决定或处理的事项才通知 owner（章程与周期回顾事件一致）；门面的 Chat 按顺序列出 Family 名称与成员；Unfamilize 原样交还。

    :module: lineage.team
    :design: decision_44cf587c1721, decision_5b5653c3eaa5
    """
    first, second, outsider = (resident(lineage, name) for name in ("Auth", "Pages", "Outsider"))
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": first}}, key="leader")
    wire = _wire(lineage, second, "to-pages", shared_with=outsider)

    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second], "title": "Portal Auth",
                             "goal": "维护 Portal 登录"}, key="familize")

    facade, steward, router = family["facade_resident_id"], family["steward_resident_id"], family["router_resident_id"]
    view = graph(lineage)
    assert view["leader_resident_id"] == facade == family["leader_resident_id"]
    # The Router is a Resident of its own: Codex's Luna (CASE-A 10-06) at medium effort to start with (CASE-A 10-12).
    assert _find_resident(router)["backend"] == "codex"
    settings = ok("GetResidentSettings", {"resident_id": router})["settings"]
    assert (settings["model"], settings["reasoning_effort"]) == ("gpt-5.6-luna", "medium")
    assert "family-work-guide.md" in charters[0][1]
    assert family["rewired_wire_ids"] == [wire] and _target_of(lineage, wire) == facade
    assert family_facade.facade_configuration(_find_resident(facade))["previous_leader_id"] == first
    assert family_facade.is_steward(_find_resident(steward))
    assert family_facade.family_member_ids(_find_resident(facade)) == {first, second}
    internal = [item for item in view["program"]["wires"] if item["id"] in _elements(lineage, family["family_id"])]
    # Generated structure evolves with summary/recovery duties. Check its
    # endpoints and roles rather than a historical aggregate wire count.
    element_ids = {item["id"] for item in view["nodes"] + view["program"]["components"] + view["program"]["wires"]}
    assert len({item["id"] for item in internal}) == len(internal)
    assert all(item["source"] in element_ids and item["target"] in element_ids for item in internal)
    sources = [item for item in view["program"]["components"]
               if item["id"] in _elements(lineage, family["family_id"]) and item["kind"] == "event_source"]
    assert sorted(item["config"]["event_type"] for item in sources) == sorted([
        "resident.work_finished", "resident.queue_pressure_exceeded", "resident.work_failed",
        "time.reached", "time.reached", "resident.message_received", "time.reached",
        "resident.work_finished", "effect.failed", "effect.failed", "event.delivery_failed", "event.delivery_failed",
        "resident.message_received", "resident.state_changed",
    ])
    watched = {(item["source"], item["target"]) for item in internal if item["kind"] == "watch"}
    summary_check = next(item for item in view["program"]["components"]
                         if item["kind"] == "effect" and "CheckResidentWorkSummary" in item["config"].get("script", ""))
    summary_source = next(item["source"] for item in internal
                          if item["kind"] == "route" and item["target"] == summary_check["id"])
    assert {target for source, target in watched if source == summary_source} == {first, second}
    assert sum(1 for item in sources if {(item["id"], first), (item["id"], second)} <= watched) == 5
    assert {item["target"] for item in internal if item["kind"] == "watch" and item["target"].startswith("resident_")} == {first, second, facade, steward, router}
    assert charters and charters[0][0] == steward and "Family 已创建" in charters[0][1]
    from management_portal.society_service.family_tasks import guide
    path, _, content = guide(_find_resident(steward))
    assert path in charters[0][1] and "维护 Portal 登录" in content
    assert "UpdateResidentWorkSummary" in content and "GetResidentSummaries" in content
    assert "不是自动删除指令" in content
    # The facade's Chat names the Family and its members, in their order.
    presentation = family_facade.chat_presentation(_find_resident(facade))
    assert {key: presentation[key] for key in ("family_id", "title", "members")} == {
        "family_id": family["family_id"], "title": "Portal Auth",
        "members": sorted([{"resident_id": first, "title": "Auth"}, {"resident_id": second, "title": "Pages"}], key=lambda item: item["resident_id"]),
    }
    assert presentation["lineage_id"] == lineage
    assert [node["role"] for node in presentation["nodes"]] == ["router", "steward", "member", "member"]
    assert family_facade.chat_presentation(_find_resident(first)) == {}

    refused = run("DeleteResident", {"lineage_id": lineage, "resident_id": facade})
    assert refused.error["code"] == "instruction.conflict"
    again = run("Familize", {"lineage_id": lineage, "resident_ids": [second, outsider]})
    assert again.error["code"] == "instruction.invalid_arguments" and "只能直接属于一个 Family" in again.error["message"]
    direct = run("DeleteFunctionBlock", {"lineage_id": lineage, "function_block_id": family["family_id"]})
    assert direct.error["code"] == "instruction.invalid_arguments"
    fork = run("ForkResident", {"lineage_id": lineage, "source_resident_id": facade})
    assert fork.error["code"] == "instruction.conflict"

    # The facade never runs: its settings are its Router Resident's own.
    ok("UpdateResidentSettings", {"resident_id": facade, "changes": {"model": "gpt-5.6-sol"}}, key="router-model")
    assert ok("GetResidentSettings", {"resident_id": router})["settings"]["model"] == "gpt-5.6-sol"
    assert ok("GetResidentSettings", {"resident_id": facade})["settings"]["model"] == "gpt-5.6-sol"
    block = next(item for item in graph(lineage)["function_blocks"]
                    if item["id"] == family["family_id"])
    assert "router_model" not in block["family"] and "router_api_id" not in block["family"]
    # A later expansion leaves the Router's own choice alone.
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": family["family_id"],
                                       "route_threshold": 70}, key="threshold")
    assert ok("GetResidentSettings", {"resident_id": router})["settings"]["model"] == "gpt-5.6-sol"
    for refused in (run("DeleteResident", {"lineage_id": lineage, "resident_id": router}),):
        assert refused.error["code"] == "instruction.conflict" and "Router" in refused.error["message"]

    undone = ok("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"]}, key="unfamilize")

    view = graph(lineage)
    nodes = {node["id"] for node in view["nodes"]}
    assert view["leader_resident_id"] == first == undone["leader_resident_id"]
    assert _target_of(lineage, wire) == first
    assert {first, second, outsider} <= nodes and not {facade, steward, router} & nodes
    assert not [item for item in view["function_blocks"] if item.get("family")]


def test_the_facade_card_goes_beside_the_most_central_member(lineage, charters):
    """Familize 把门面放在最靠近成员中心的成员旁边

    门面在折叠时代表可能分散在大型 Lineage 中的成员，因此放在离成员中心最近的那个成员旁的空位，而不是第一个成员旁。

    :design: decision_44cf587c1721
    """
    # Folded, the facade stands for members that may be scattered over a large
    # Lineage, so it goes beside the member nearest their middle, not the first one.
    placed = {
        name: ok("CreateResident", {"lineage_id": lineage, "title": name, "provider": "codex", "x": x, "y": 40},
                 key=f"r-{name}")["resident_id"]
        for name, x in (("West", 10), ("Middle", 52), ("East", 90))
    }
    family = ok("Familize", {"lineage_id": lineage, "title": "Spread",
                             "resident_ids": [placed["West"], placed["Middle"], placed["East"]]}, key="familize-spread")
    nodes = {node["id"]: node for node in graph(lineage)["nodes"]}
    facade = nodes[family["facade_resident_id"]]

    def distance(resident_id: str) -> float:
        other = nodes[resident_id]
        return math.hypot((facade["x"] - other["x"]) * 12.8, (facade["y"] - other["y"]) * 9.0)

    assert distance(placed["Middle"]) < min(distance(placed["West"]), distance(placed["East"]))


def test_familize_needs_one_member_and_updates_the_live_roster(lineage, charters):
    """Familize 至少需要一个成员且路由随成员变化更新

    :module: platform.core"""
    only = resident(lineage, "Alone")

    empty = run("Familize", {"lineage_id": lineage, "resident_ids": []})

    assert empty.error["code"] == "instruction.invalid_arguments"
    assert charters == []

    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [only]})
    facade = _find_resident(family["facade_resident_id"])
    assert family_facade.family_members(facade) == [only]
    assert family["steward_resident_id"] and family["router_resident_id"]
    assert "family-work-guide.md" in charters[0][1]

    # Rebinding changes the existing route's arguments; no rebuilding a Family
    # or switching a mode is needed, including empty -> one after restoration.
    second = resident(lineage, "Another")
    for number, members in enumerate(([only, second], [only], [], [only])):
        ok("UpdateFamily", {"lineage_id": lineage, "family_id": family["family_id"],
                                            "add_member_ids": [rid for rid in members if rid not in family_facade.family_members(facade)],
                                            "remove_member_ids": [rid for rid in family_facade.family_members(facade) if rid not in members]}, key=f"members-{number}")
        assert family_facade.family_members(facade) == sorted(members)
        route = next(w for w in graph(lineage)["program"]["wires"]
                     if w["id"] in _elements(lineage, family["family_id"])
                     and "members" in w.get("config", {}).get("invoke_profiles", {}).get("effect", {}).get("arguments", {})
                     and "router" in w["config"]["invoke_profiles"]["effect"]["arguments"])
        assert route["config"]["invoke_profiles"]["effect"]["arguments"]["members"] == {"value": members}
        # Queue supervision exists exactly when there are business members to watch.
        view = graph(lineage)
        queue = [c for c in view["program"]["components"]
                 if c.get("config", {}).get("event_type") == "resident.queue_pressure_exceeded"]
        assert len(queue) == bool(members)
        if members:
            source = queue[0]["id"]
            assert queue[0]["config"]["queue_length_threshold"] == 1
            assert {w["target"] for w in view["program"]["wires"]
                    if w["kind"] == "watch" and w["source"] == source} == set(members)
            queue_route = next(w for w in view["program"]["wires"]
                               if w["kind"] == "route" and w["source"] == source)
            queue_effect = next(c for c in view["program"]["components"] if c["id"] == queue_route["target"])
            assert "DispatchFamilyTask" in queue_effect["config"]["script"]
            assert queue_route["config"]["invoke_profiles"]["effect"]["arguments"]["trigger"] == {"value": "queue"}
        else:
            # The same reconciliation used by upgrades must keep an empty Family valid.
            ok("UpdateFamily", {"lineage_id": lineage, "family_id": family["family_id"]}, key="refresh-empty")
            keys = {e.get("key") for b in graph(lineage)["function_blocks"]
                    if b["id"] == family["family_id"] for e in b["elements"]}
            assert not {"member_queue", "notify_member_queue"} & keys
            assert {"member_failed", "recovery_finished", "recovery_timer"} <= keys
        diagnostics = ok("GetLineageDiagnostics", {"lineage_id": lineage})
        assert diagnostics["runnable"], diagnostics["diagnostics"]

    ok("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"]})
    assert _find_resident(only) and _find_resident(second)


def test_the_router_and_steward_start_with_what_was_chosen_when_placing_the_family(lineage, charters):
    """放入 Family 时为 Router 与 steward 选的后端和设置用于新建它们

    GetLineage 的 function_blocks 给出新建槽的 Provider 与起始设置，门面标为不可设置；Familize 的 slot_settings 让 Router 与 steward 按所选 Provider、模型与推理强度新建，换 Provider 时不带上槽自带的 Codex 设置；门面、绑定槽或字段不对的设置被拒绝。

    :design: decision_44cf587c1721, decision_5b5653c3eaa5
    """
    from tests.cases.model_api_test_support import DEEPSEEK_PRIMARY, register_apis
    register_apis(DEEPSEEK_PRIMARY)
    from management_portal.society_service.templates.familize import KIND
    roles = {role["key"]: role for role in KIND.describe({})["roles"]}
    assert roles["router"]["provider"] == "codex"
    assert roles["router"]["settings"] == {"model": "gpt-5.6-luna", "reasoning_effort": "medium"}

    first, second = resident(lineage, "First"), resident(lineage, "Second")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first], "title": "Chosen", "resident_settings": {
        "router": {"provider": "codex", "settings": {"model": "gpt-5.6-sol", "reasoning_effort": "low"}},
        "steward": {"provider": "claude"},
    }}, key="familize-chosen")
    settings = ok("GetResidentSettings", {"resident_id": family["router_resident_id"]})["settings"]
    assert (settings["model"], settings["reasoning_effort"]) == ("gpt-5.6-sol", "low")
    assert _find_resident(family["steward_resident_id"])["backend"] == "claude"

    # Another Provider leaves the slot's Codex settings behind.
    other = ok("Familize", {"lineage_id": lineage, "resident_ids": [second], "title": "Elsewhere",
                            "resident_settings": {"router": {"provider": "claude"}}}, key="familize-claude-router")
    assert _find_resident(other["router_resident_id"])["backend"] == "claude"
    assert ok("GetResidentSettings", {"resident_id": other["router_resident_id"]})["settings"].get("model") != "gpt-5.6-luna"

    # The facade runs no turns of its own, so there is nothing to choose for it.
    third = resident(lineage, "Third")
    for resident_settings in ({"facade": {"provider": "claude"}}, {"members": {}}, {"router": {"model": "x"}}):
        refused = run("Familize", {"lineage_id": lineage, "resident_ids": [third], "resident_settings": resident_settings})
        assert refused.error["code"] == "instruction.invalid_arguments", resident_settings


def _family(lineage_id: str, family_id: str) -> dict:
    return next(item for item in graph(lineage_id)["function_blocks"] if item["id"] == family_id)["family"]


def _elements(lineage_id: str, family_id: str) -> set:
    return {e["id"] for item in graph(lineage_id)["function_blocks"] if item["id"] == family_id for e in item["elements"]}


@pytest.mark.parametrize("role", ["facade", "steward", "router"])
def test_apply_changes_preserves_family_internal_residents(lineage, charters, role):
    """ApplyLineageChanges 拒绝删除 Family 内部角色"""
    first, second = (resident(lineage, name) for name in ("Auth", "Pages"))
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second]}, key="family-protection")
    resident_id = family[f"{role}_resident_id"]
    refused = run("ApplyLineageChanges", {"lineage_id": lineage, "operations": [
        {"op": "delete", "kind": "resident", "id": resident_id},
    ]})
    assert refused.error["code"] == "instruction.conflict", refused.error
    assert resident_id in {node["id"] for node in graph(lineage)["nodes"]}


def test_deleted_members_leave_the_family_and_keep_identity(lineage, charters):
    """删除成员将其移出 Family 并保留历史身份

    :module: lineage.team
    :design: decision_44cf587c1721
    :design-point:
    """
    from management_portal.society_service.resident_catalog import get_catalog

    first, second, third = (resident(lineage, name) for name in ("Auth", "Pages", "Docs"))
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second, third]}, key="familize-delete")
    other = ok("Familize", {"lineage_id": lineage, "resident_ids": [resident(lineage, "Other")]}, key="other-family")
    from management_portal.society_service.changes import collect_changes
    with collect_changes() as changes:
        ok("DeleteResident", {"lineage_id": lineage, "resident_id": third}, key="delete-third")
    assert _family(lineage, family["family_id"])["members"] == sorted([first, second])
    assert ("resident", family["facade_resident_id"], "projection") in [change[:3] for change in changes]
    assert ("resident", other["facade_resident_id"], "projection") not in [change[:3] for change in changes]
    assert get_catalog().get_historical(third)["deleted_at"]
    assert not any(node["id"] == third for node in graph(lineage)["nodes"])
    with collect_changes() as changes:
        assert ok("DeleteResident", {"lineage_id": lineage, "resident_id": third}, key="delete-third-again")["resident_id"] == third
    assert not any(change[2] == "projection" for change in changes)
    steward = run("DeleteResident", {"lineage_id": lineage, "resident_id": family["steward_resident_id"]})
    assert steward.error["code"] == "instruction.conflict"


def test_a_member_moved_to_another_lineage_leaves_the_family(lineage, charters):
    """迁到其他 Lineage 的成员离开 Family，名册、Router 候选和监听连线随之更新

    成员的继承边断开并列在回执中；门面、Router、steward 和其余成员不变，steward 以门面身份收到结构信号。门面、Router、steward 不能单独迁出，被拒绝时什么都不变。

    :module: lineage.team
    :design: decision_44cf587c1721, decision_6591e7793116
    """
    from management_portal.society_service import lineage_domain

    first, second, third = (resident(lineage, name) for name in ("Auth", "Pages", "Menu"))
    lineage_domain._apply_lineage_graph_patch(lineage, {"patch": {"edges": [
        {"id": "edge_000000000001", "type": "inheritance", "source": first, "target": third},
    ]}, "history_reason": "test fork"}, ACTOR, synchronize_external_projections=False)
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second, third]}, key="familize-move")
    target = ok("CreateLineage", {"title": "Elsewhere", "project_id": "project_test"}, key="move-target")["lineage_id"]

    notes = len(charters)
    moved = ok("MoveResident", {"lineage_id": lineage, "resident_id": third, "target_lineage_id": target}, key="move-member")
    assert moved["family_id"] == family["family_id"]
    assert [item["id"] for item in moved["detached"]] == ["edge_000000000001"]

    source = graph(lineage)
    assert _family(lineage, family["family_id"])["members"] == sorted([first, second])
    assert third not in {node["id"] for node in source["nodes"]}
    assert source["edges"] == []

    def mentions(value) -> bool:
        if isinstance(value, dict):
            return any(mentions(item) for item in value.values())
        if isinstance(value, list):
            return any(mentions(item) for item in value)
        return value == third

    assert not mentions(source["program"]) and not mentions(source["function_blocks"])
    assert {family[f"{role}_resident_id"] for role in ("facade", "router", "steward")} <= {node["id"] for node in source["nodes"]}
    assert third in {node["id"] for node in graph(target)["nodes"]}
    assert not graph(target)["function_blocks"]

    # The steward hears it once, as the facade.
    assert len(charters) == notes + 1
    steward, content, _, sender = charters[-1]
    assert (steward, sender) == (family["steward_resident_id"], family["facade_resident_id"])
    assert third in content and target in content

    before = graph(lineage)
    for role in ("facade", "router", "steward"):
        refused = run("MoveResident", {"lineage_id": lineage, "resident_id": family[f"{role}_resident_id"],
                                       "target_lineage_id": target})
        assert refused.status == "failed" and "Family" in refused.error["message"], refused.error
    assert graph(lineage)["revision"] == before["revision"]


def test_a_new_resident_joins_the_family_it_is_made_in(lineage, charters):
    """新 Resident 按创建者和创建位置加入 Family

    owner 在 Family 外新建的 Resident 不加入 Family，steward 也不会收到通知；在展开的 Family 中新建（指明 Family 实例）时立即加入，steward 以门面身份收到补齐认知的结构信号；成员新建的 Resident 不用指明就加入成员所在的 Family；steward 自己新建的成员加入时不通知它自己；指明的不是本 Lineage 的 Family 时什么都不创建。

    CASE-A 10-05: the owner's fork, made outside the Family, was added minutes later by the steward,
    which guessed from the Lineage's changes. Who joins now follows who made the Resident and where.

    :design: decision_44cf587c1721
    """

    first, second = (resident(lineage, name) for name in ("Auth", "Pages"))
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second]}, key="familize-join")
    block, steward, facade = family["family_id"], family["steward_resident_id"], family["facade_resident_id"]
    members = lambda: _family(lineage, block)["members"]  # noqa: E731
    notes = len(charters)

    # The owner's Resident made outside the Family stays outside; the steward hears nothing.
    outside = ok("CreateResident", {"lineage_id": lineage, "title": "Outside", "provider": "codex"}, key="join-outside")
    assert "family_id" not in outside and members() == sorted([first, second]) and len(charters) == notes

    # Made inside the expanded Family, it joins at once and the steward hears it from the facade.
    inside = ok("CreateResident", {"lineage_id": lineage, "title": "Inside", "provider": "codex",
                                   "family_id": block}, key="join-inside")
    assert inside["family_id"] == block and members() == sorted([first, second, inside["resident_id"]])
    assert charters[-1][0] == steward and charters[-1][3] == facade and "已加入 Family" in charters[-1][1]

    # A member's new Resident joins the member's Family without naming it.
    made = ok("CreateResident", {"lineage_id": lineage, "title": "Helper", "provider": "codex"},
              actor=first, key="join-member")
    assert made["family_id"] == block and made["resident_id"] in members()
    assert f"成员 Auth（{first}）" in charters[-1][1]

    # The steward's own new member needs no note to itself.
    notes = len(charters)
    split = ok("CreateResident", {"lineage_id": lineage, "title": "Split", "provider": "codex"},
               actor=steward, key="join-steward")
    assert split["resident_id"] in members() and len(charters) == notes

    # Naming something that is not a Family of this Lineage creates nothing.
    count = len(graph(lineage)["nodes"])
    wrong = run("CreateResident", {"lineage_id": lineage, "title": "Nowhere", "provider": "codex",
                                   "family_id": "fbi_000000000000"}, key="join-wrong")
    assert "不是这个 Lineage 中的 Family" in str(wrong.error) and len(graph(lineage)["nodes"]) == count


def test_unfamilize_stops_a_busy_steward_first_and_changes_nothing_while_it_is_still_stopping(lineage, charters, monkeypatch):
    """Unfamilize 先停止忙碌的 steward，未停下前不作改动

    steward 仍在工作时先停止其工作；在等待时间内没有停下则返回可重试冲突，leader 不交还；停下后正常解除。

    The steward is usually at work; deleting it used to fail after the leader was already handed back.

    :module: lineage.team
    :design: decision_44cf587c1721
    """

    from management_portal.society_service import lineage_domain, resident_lifecycle_domain
    from management_portal.society_service.templates import familize as family_familize

    first, second = (resident(lineage, name) for name in ("Front", "Back"))
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": first}}, key="busy-leader")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second]}, key="busy-familize")
    steward = family["steward_resident_id"]
    busy, stopped = {steward}, []

    def quiesce(lineage_id, payload, actor):
        stopped.append(payload["target"])
        return {}

    monkeypatch.setattr(resident_lifecycle_domain, "resident_has_pending_work", lambda resident_id: resident_id in busy)
    monkeypatch.setattr(lineage_domain, "quiesce_lineage_resident", quiesce)
    monkeypatch.setattr(family_familize, "ROLE_STOP_WAIT_SECONDS", 0.0)

    still = run("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"]})

    assert still.error["code"] == "instruction.conflict" and still.error["retryable"] is True
    assert set(stopped) == {family["facade_resident_id"], steward, family["router_resident_id"]}
    assert graph(lineage)["leader_resident_id"] == family["facade_resident_id"]  # nothing handed back yet

    busy.clear()
    undone = ok("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"]}, key="busy-unfamilize")
    assert graph(lineage)["leader_resident_id"] == first == undone["leader_resident_id"]
    assert steward in undone["deleted_resident_ids"]


def test_a_family_speaks_to_its_steward_only_as_its_facade(lineage, charters, monkeypatch):
    """Family 只以门面身份与 steward 往来

    成员完成工作触发的 steward Event 以门面为发送者，即使是 owner 直接给成员发的消息；Event 由 Lineage 自动化身份派发，提问者保留为发起者；章程与结构信号也由门面送达；steward 不能给自己的门面发消息。

    A steward's Events come from the facade, whoever messaged the member; the facade never hears the steward.

    :module: lineage.team
    :design: decision_44cf587c1721
    """

    from management_portal.society_service import lineage_domain, resident_domain
    from management_portal.society_service.family_familize import facade_speaking_to
    from tests.cases.isa_test_support import ACTOR

    first, second = (resident(lineage, name) for name in ("Auth", "Pages"))
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": first}}, key="speaker-leader")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second]}, key="familize-speaker")
    facade, steward = family["facade_resident_id"], family["steward_resident_id"]
    assert charters[0][0] == steward and charters[0][3] == facade  # the facade hands over the charter
    assert facade_speaking_to(graph(lineage), steward) == facade
    assert facade_speaking_to(graph(lineage), first) == ""

    dispatched = []
    original_dispatch = lineage_domain.dispatch_lineage_program_event

    def observe_dispatch(lineage_id, event_source_id, event, actor, **options):
        dispatched.append((actor, dict(event.identity)))
        return original_dispatch(lineage_id, event_source_id, event, actor, **options)

    monkeypatch.setattr(lineage_domain, "dispatch_lineage_program_event", observe_dispatch)
    sent = []
    monkeypatch.setattr(lineage_domain, "_send_node_message", lambda node, prompt, actor, **options: sent.append(
        (str(node.get("id") or ""), actor, prompt),
    ) or {"message_id": "msg_" + f"{len(sent):032x}"})
    from tests.cases.society_service_test_support import record_terminal_execution
    record_terminal_execution(first, "msg_" + "1" * 32, ACTOR)
    # A committed Execution completion wakes the matching observer.
    lineage_domain.lineage_program_terminal_reconciliation.dispatch_program_resident_work_finished(
        lineage, first, "msg_" + "1" * 32, ACTOR, ports=lineage_domain._program_terminal_ports(),
    )
    assert not sent  # Successful completion performs an Effect check, never wakes the steward.
    # A completion preserves the verified initiating User through its Effects;
    # the Family's speaking facade is independent of execution authority.
    assert [(actor, identity["principal"]["id"], identity["initiated_by"]["id"]) for actor, identity in dispatched] == [
        (ACTOR, ACTOR, ACTOR), (ACTOR, ACTOR, ACTOR),
    ]

    accepted = resident_domain.deliver_resident_message_content(facade, steward, "我把记忆库更新了")
    assert accepted["message_id"]


def test_the_steward_hears_its_notes_from_the_facade_and_stays_out_of_the_owners_conversations(lineage, monkeypatch):
    """steward 的章程、结构信号和 info 都以门面身份真实送达，steward 不进入 owner 的会话列表

    不替换投递函数：Familize 的章程、成员移入回收站与恢复的结构信号、owner 发给门面的 info 都以门面为发送者、owner 为发起者写入 steward 对话；owner 的会话活动里只有门面，没有 steward。修复前移入回收站会因发送者与当前操作身份不符而失败。

    The charter and info notes reach the steward as the facade, really delivered.

    Whoever caused a note is only its initiator, so the owner's conversation
    list never shows the steward, a part of the Family hidden behind its facade.

    :importance: P1
    :design: decision_44cf587c1721
    """

    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from management_portal.society_service import resident_conversation_stream
    from tests.cases.isa_test_support import ACTOR

    delivered = []
    deliver = resident_execution_domain.deliver_resident_message_content

    def observe(resident_id, actor, content, *args, **options):
        result = deliver(resident_id, actor, content, *args, **options)
        delivered.append((resident_id, result["message_id"]))
        return result

    monkeypatch.setattr(resident_execution_domain, "deliver_resident_message_content", observe)
    first, second, third = (resident(lineage, name) for name in ("Auth", "Pages", "Docs"))
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second, third],
                             "goal": "维护 Portal 登录"}, key="familize-notes")
    facade, steward = family["facade_resident_id"], family["steward_resident_id"]
    # An info the owner sends the facade reaches the steward at once, also from the facade.
    with operation_identity_scope(OperationIdentity.for_actor(ACTOR)):
        resident_execution_domain.deliver_resident_message_content(
            facade, ACTOR, "登录页改到周五上线。", activation_policy="attach_to_next",
        )

    repository = resident_conversation_stream.get_repository()
    notes = [repository.message(target, message_id) for target, message_id in delivered if target == steward]
    texts = ["".join(part.get("text") or "" for part in item["content_parts"]) for item in notes]
    assert len(notes) == 2
    assert "Family 已创建" in texts[0] and "family-work-guide.md" in texts[0]
    assert '"change": "登录页改到周五上线。"' in texts[1] and "family-work-guide.md" in texts[1]
    assert {(item["origin"]["sender"]["id"], item["origin"]["initiated_by"]["id"]) for item in notes} == {
        (facade, ACTOR),
    }
    # The info waits in the facade's Mailbox for its next request; it is the owner's activity there.
    activity = repository.actor_activity(ACTOR)
    assert facade in activity and steward not in activity


def test_family_chat_lists_only_active_router_steward_and_members(lineage, charters):
    """Family 对话仅列出活跃 Router、steward 和成员

    :module: lineage.team
    :design: decision_44cf587c1721
    :design-point:
    """
    from management_portal.society_service import family_facade
    first, second = (resident(lineage, name) for name in ("Auth", "Pages"))
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second], "title": "Status view", "goal": "显示状态"}, key="status-family")
    facade = _find_resident(family["facade_resident_id"])
    presentation = family_facade.chat_presentation(facade)
    assert [(n["resident_id"], n["role"]) for n in presentation["nodes"]] == [
        (family["router_resident_id"], "router"), (family["steward_resident_id"], "steward"), *[(rid, "member") for rid in sorted([first, second])]]
    ok("DeleteResident", {"lineage_id": lineage, "resident_id": second}, key="status-delete")
    presentation = family_facade.chat_presentation(facade)
    assert second not in {n["resident_id"] for n in presentation["nodes"]}


@pytest.mark.parametrize("instruction", ["DeleteResident", "ApplyLineageChanges"])
def test_a_deleted_member_leaves_the_routers_choices(lineage, charters, instruction):
    """删除成员后 Router 候选列表即时移除该成员"""
    first, second, third = (resident(lineage, name) for name in ("Stays", "Also stays", "Leaves"))
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second, third]}, key="familize-leave")
    router = family["router_resident_id"]

    def choices() -> list[list[str]]:
        return [
            wire["config"]["invoke_profiles"]["effect"]["arguments"]["members"]["value"]
            for wire in graph(lineage)["program"]["wires"]
            if wire["id"] in _elements(lineage, family["family_id"])
            and "members" in ((wire.get("config") or {}).get("invoke_profiles") or {}).get("effect", {}).get("arguments", {})
        ]

    assert choices() and all(third in members for members in choices())
    arguments = {"lineage_id": lineage, "resident_id": third} if instruction == "DeleteResident" else {
        "lineage_id": lineage, "operations": [{"op": "delete", "kind": "resident", "id": third}],
    }
    ok(instruction, arguments, key="delete-leaving-member")

    assert choices() and all(members == sorted([first, second]) for members in choices())
    # The Router is the same Resident as before.
    assert router in {node["id"] for node in graph(lineage)["nodes"]}


def test_a_members_new_title_reaches_its_steward_as_its_assigned_duty(lineage, charters):
    """成员改名后 steward 以门面身份收到结构信号，新标题即指定给它的分工

    owner 改成员标题时 steward 收到结构信号；同名保存、改门面或 Family 外的 Resident、steward 自己改名都不通知。

    CASE-A 10-11: renaming a member is how an owner assigns it work; the Router ranks titles above summaries."""

    first, second = (resident(lineage, name) for name in ("Mailbox", "Pages"))
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [first, second]}, key="familize-retitle")
    steward, facade = family["steward_resident_id"], family["facade_resident_id"]

    ok("UpdateResident", {"lineage_id": lineage, "resident_id": first, "title": "Menu 实现维护"}, key="retitle-owner")
    target, content, _, sender = charters[-1]
    assert (target, sender) == (steward, facade)
    assert f"把成员「Mailbox」（{first}）的标题改为「Menu 实现维护」" in content
    assert "owner 改过的标题以 owner 为准" in content

    # Saving the same title again, renaming the facade or a Resident outside the Family tells nobody.
    notes = len(charters)
    ok("UpdateResident", {"lineage_id": lineage, "resident_id": first, "title": "Menu 实现维护"}, key="retitle-same")
    ok("UpdateResident", {"lineage_id": lineage, "resident_id": facade, "title": "Family renamed"}, key="retitle-facade")
    outside = ok("CreateResident", {"lineage_id": lineage, "title": "Outside", "provider": "codex"}, key="retitle-outside")
    ok("UpdateResident", {"lineage_id": outside["lineage_id"], "resident_id": outside["resident_id"], "title": "Still outside"}, key="retitle-outside-rename")
    assert len(charters) == notes

    # The steward's own rename by stable duty needs no note to itself.
    ok("UpdateResident", {"lineage_id": lineage, "resident_id": second, "title": "Pages 与导航"}, actor=steward, key="retitle-steward")
    assert len(charters) == notes and _find_resident(second)["title"] == "Pages 与导航"


def test_unfamilize_drains_a_completion_queued_after_roles_stop(lineage, monkeypatch):
    """解除 Family 清理停止之后迟到的完成通知

    三个内部角色先关闭接单并停止；最终清队列与删除由 Program 执行权保护，保留成员，不需人工删除迟到通知。

    :module: resident.identity
    :design: decision_44cf587c1721
    """
    from management_portal.society_service import resident_conversation_stream
    from management_portal.society_service.templates import familize as family_familize
    from tests.cases.isa_test_support import ACTOR

    member = resident(lineage, "Member")
    with monkeypatch.context() as setup:
        setup.setattr(resident_execution_domain, "deliver_resident_message_content", lambda *_a, **_kw: {})
        family = ok("Familize", {"lineage_id": lineage, "resident_ids": [member]}, key="late-completion-family")
    steward = family["steward_resident_id"]
    stop = family_familize._stop_roles
    deliveries = []

    def stop_then_deliver(*args, **kwargs):
        stop(*args, **kwargs)
        if deliveries:
            return
        response = resident_execution_domain.deliver_resident_message_content(
            steward, ACTOR, "Member completed", message_id="msg_11111111111111111111111111111111",
        )
        deliveries.append(response["message_id"])
        state = resident_conversation_stream.get_repository().current_state(steward)
        assert state["intake_state"] == "closed" and state["queued_count"] == 1

    monkeypatch.setattr(family_familize, "_stop_roles", stop_then_deliver)
    undone = ok("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"]}, key="late-completion-remove")
    assert steward in undone["deleted_resident_ids"]
    assert deliveries == ["msg_11111111111111111111111111111111"]
    assert {node["id"] for node in graph(lineage)["nodes"]} >= {member}
