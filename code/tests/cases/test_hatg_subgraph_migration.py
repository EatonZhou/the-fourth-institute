"""MoveLineageObjects: atomic cross-Lineage object moves (decision_6591e7793116, decision_6fc653d55c79)."""

from __future__ import annotations

import pytest

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import lineage_domain, lineage_program_runtime, lineage_subgraph
from management_portal.society_service import resident_backend, resident_lifecycle_domain
from management_portal.society_service import resident_record_domain, resident_workspace_domain
from tests.cases.isa_test_support import ok, run
from tests.cases.test_hatg_move_resident import ACTOR, graph, teams  # noqa: F401 - teams is a fixture
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401 - fixture
    _append_conversation_turn, _new_session_payload, future_creation_store,
)
from tests.cases.effect_script_support import effect_config, lower_effect_call


def event_source(lineage_id: str, title: str = "Start") -> str:
    return ok("CreateComponent", {"lineage_id": lineage_id, "kind": "event_source", "title": title,
                                  "config": {"event_type": "manual.triggered"}}, key=f"source-{title}")["component_id"]


def setup_flow(teams):
    source, _, member = teams
    event_id = event_source(source["lineage_id"])
    ok("CreateWire", {"lineage_id": source["lineage_id"], "kind": "route", "source": event_id,
                      "target": member["resident_id"]}, key="flow-wire")
    return [member["resident_id"], event_id]


def move_call(teams, ids, key="subgraph-test", **arguments):
    _source, target, _ = teams
    return {
        "arguments": {"lineage_id": _source["lineage_id"], "object_ids": ids, "target_lineage_id": target["lineage_id"], **arguments},
        "key": key,
    }


def execute(call, actor=ACTOR):
    return run("MoveLineageObjects", call["arguments"], actor=actor, key=call["key"])


def moved(teams, ids, **arguments):
    return execute(move_call(teams, ids, **arguments)).raise_for_error()


def add_relations(lineage: dict, edges: list[dict]) -> None:
    lineage_domain._apply_lineage_graph_patch(lineage["lineage_id"], {
        "patch": {"edges": edges}, "history_reason": "test relations",
    }, ACTOR, synchronize_external_projections=False)


@pytest.mark.parametrize("empty_target", [False, True])
def test_move_connected_objects_preserves_identity_and_replays(teams, empty_target):
    """MoveLineageObjects 迁移相连对象时保留相对位置和 Resident 的会话、文件，重复调用回放"""
    ids = setup_flow(teams)
    source, target, member = teams
    if empty_target:
        target = lineage_domain.create_lineage({"title": "New", "project_id": source["project_id"]}, ACTOR)
        teams = source, target, member
    before = [graph(source), graph(target)]
    _append_conversation_turn(member["resident_id"], 0)
    chat = get_agent_data_store().read_chat_messages(member["resident_id"])
    record = resident_workspace_domain._find_resident(member["resident_id"])
    session = resident_backend.record_session_id(record)
    folder = resident_record_domain._resident_folder_path(record)
    (folder / "sample.txt").write_text("preserved")
    call = move_call(teams, ids)
    first = execute(call)
    result = first.raise_for_error()
    assert graph(source)["revision"] > before[0]["revision"]
    assert graph(target)["revision"] > before[1]["revision"]
    assert result["detached"] == []
    assert graph(source)["program"]["wires"] == []
    assert graph(target)["program"]["wires"] == before[0]["program"]["wires"]
    source_positions = {item["id"]: item for item in [*before[0]["nodes"], *before[0]["program"]["components"]]}
    target_graph = graph(target)
    target_positions = {item["id"]: item for item in [*target_graph["nodes"], *target_graph["program"]["components"]]}
    for axis in ("x", "y"):
        assert target_positions[ids[1]][axis] - target_positions[ids[0]][axis] == pytest.approx(
            source_positions[ids[1]][axis] - source_positions[ids[0]][axis])
    replay = execute(call)
    assert replay.replayed and replay.result["operation_id"] == result["operation_id"]
    assert replay.result["target_lineage_id"] == target["lineage_id"]
    assert result["operation_id"].startswith("lineage-move_")
    status = ok("GetMoveOperation", {"operation_id": result["operation_id"]})
    assert status["status"] != "completed"
    transfer = result["operations"][0]["operation_id"]
    assert status["resident_operations"][0]["resident_id"] == member["resident_id"]
    resident_lifecycle_domain._lifecycle_operations().run(transfer, resident_lifecycle_domain._apply_resident_lifecycle_step)
    assert ok("GetMoveOperation", {"operation_id": result["operation_id"]})["status"] == "completed"
    current = resident_workspace_domain._find_resident(member["resident_id"])
    assert current["lineage_id"] == target["lineage_id"]
    assert current["owned_workspace_id"] == record["owned_workspace_id"]
    assert resident_backend.record_session_id(current) == session
    assert (resident_record_domain._resident_folder_path(current) / "sample.txt").read_text() == "preserved"
    assert get_agent_data_store().read_chat_messages(member["resident_id"]) == chat


def test_relations_across_the_boundary_are_detached_and_listed(teams):
    """跨越迁移边界的连线被断开，并列在结果的 detached 中"""
    ids = setup_flow(teams)
    source, target, member = teams
    wire_id = graph(source)["program"]["wires"][0]["id"]
    result = moved(teams, ids[:1])
    assert result["detached"] == [{"id": wire_id, "source": ids[1], "target": member["resident_id"], "type": "wire"}]
    assert [item["id"] for item in graph(source)["program"]["components"]] == [ids[1]]
    assert graph(source)["program"]["wires"] == graph(target)["program"]["wires"] == []
    assert member["resident_id"] in {node["id"] for node in graph(target)["nodes"]}


def test_moving_an_object_another_instruction_deleted_changes_nothing(teams):
    """迁移已被另一条指令删除的对象时不做任何修改

    验证 MoveLineageObjects 的对象在迁移前已被删除时指令失败，源和目标 Lineage 都保持原样。
    """
    ids = setup_flow(teams)
    call = move_call(teams, ids)
    ok("DeleteComponent", {"lineage_id": teams[0]["lineage_id"], "component_id": ids[1]}, key="delete-before-move")
    before = [graph(teams[0]), graph(teams[1])]
    assert execute(call).status == "failed"
    assert [graph(teams[0]), graph(teams[1])] == before


def test_moving_the_whole_graph_leaves_an_empty_source_and_hands_over_its_coordinators(teams):
    """迁走整个图后源 Lineage 为空，Leader 转到目标 Lineage"""
    ids = setup_flow(teams)
    source, _, member = teams
    target = lineage_domain.create_lineage({"title": "New", "project_id": source["project_id"]}, ACTOR)
    result = moved((source, target, member), ids + [source["resident_id"]])
    assert graph(source)["nodes"] == []
    assert graph(source)["leader_resident_id"] == ""
    assert graph(target)["leader_resident_id"] == source["resident_id"]
    assert len(result["operations"]) == 2


def test_moving_the_leader_out_of_a_partial_selection_picks_a_new_leader(teams):
    """只迁走源 Leader 时源 Lineage 从剩余成员中选出新 Leader"""
    source, target, member = teams
    second = lineage_domain.create_lineage_resident(
        source["lineage_id"], _new_session_payload("Second member"), ACTOR,
    )["resident"]
    target_leader = graph(target)["leader_resident_id"]
    moved(teams, [source["resident_id"]])
    assert graph(source)["leader_resident_id"] in {member["resident_id"], second["resident_id"]}
    assert graph(target)["leader_resident_id"] == target_leader
    assert source["resident_id"] in {node["id"] for node in graph(target)["nodes"]}


def test_a_failed_commit_rolls_back_members_graphs_and_outbox(teams, monkeypatch):
    """目标 Lineage 提交失败时对象迁移回滚成员、两边图和 outbox"""
    ids = setup_flow(teams)
    before = [graph(teams[0]), graph(teams[1])]
    original = lineage_domain._persist_lineage_state

    def fail_target(previous, current, *args, **kwargs):
        if current["lineage_id"] == teams[1]["lineage_id"]:
            raise ValueError("injected target failure")
        return original(previous, current, *args, **kwargs)

    monkeypatch.setattr(lineage_domain, "_persist_lineage_state", fail_target)
    assert "injected" in execute(move_call(teams, ids)).error["message"]
    assert [graph(teams[0]), graph(teams[1])] == before
    assert resident_workspace_domain._find_resident(teams[2]["resident_id"])["lineage_id"] == teams[0]["lineage_id"]
    assert not resident_lifecycle_domain._lifecycle_operations().pending(teams[2]["resident_id"])


@pytest.mark.parametrize("side", [0, 1])
def test_both_lineage_owners_are_required(teams, monkeypatch, side):
    """MoveLineageObjects 要求调用者同时拥有源和目标 Lineage"""
    ids = setup_flow(teams)
    ensure_manager = lineage_domain._ensure_manager

    def check(lineage, actor):
        if lineage["lineage_id"] == teams[side]["lineage_id"]:
            raise PermissionError("No permission for this Lineage")
        return ensure_manager(lineage, actor)

    monkeypatch.setattr(lineage_domain, "_ensure_manager", check)
    assert execute(move_call(teams, ids)).error["code"] == "instruction.forbidden"


def test_selected_residents_move_without_stopping_their_work(teams, monkeypatch):
    """选中的 Resident 有待处理工作时也直接迁移，不停止其工作"""
    ids = setup_flow(teams)
    monkeypatch.setattr(resident_lifecycle_domain, "_resident_has_pending_work", lambda _: True)
    moved(teams, ids)
    assert resident_workspace_domain._find_resident(ids[0])["lineage_id"] == teams[1]["lineage_id"]


def test_event_cursors_and_unsettled_fanout_move_with_their_sources(teams):
    """事件源的已消费游标和未结算的分发随事件源一起迁移"""
    ids = setup_flow(teams)
    source, target, member = teams
    event_id = ids[1]
    assert lineage_program_runtime.claim_event(source["lineage_id"], event_id, "already-consumed")
    moved(teams, ids)
    assert event_id not in lineage_program_runtime.read_runtime(source["lineage_id"])["event_sources"]
    assert not lineage_program_runtime.claim_event(target["lineage_id"], event_id, "already-consumed")
    lineage_program_runtime.claim_event_occurrence(target["lineage_id"], event_id, "unsettled")
    pending = lineage_program_runtime.read_runtime(target["lineage_id"])["event_sources"][event_id]
    moved((target, source, member), [event_id], key="move-back")
    assert lineage_program_runtime.read_runtime(source["lineage_id"])["event_sources"][event_id] == pending


def test_components_alone_can_move(teams):
    """只迁移组件时连线一并迁移，操作立即完成且无 Workspace 转移"""
    source, target, _ = teams
    first = event_source(source["lineage_id"], "First")
    effect = ok("CreateComponent", {"lineage_id": source["lineage_id"], "kind": "effect", "title": "Notify",
                                    "config": effect_config("CreateNotification", {}, [])},
                key="effect")["component_id"]
    ok("CreateWire", {"lineage_id": source["lineage_id"], "kind": "route", "source": first, "target": effect},
       key="event-wire")
    result = moved(teams, [first, effect])
    assert result["operations"] == [] and not result["workspace_pending"]
    # Nothing waits for a workspace move: the batch is already complete.
    status = ok("GetMoveOperation", {"operation_id": result["operation_id"]})
    assert (status["phase"], status["status"]) == ("completed", "completed")
    assert {item["id"] for item in graph(target)["program"]["components"]} == {first, effect}
    assert len(graph(target)["program"]["wires"]) == 1






@pytest.mark.parametrize("fail_commit", [False, True])
def test_inheritance_is_detached_without_reconnecting_or_losing_provenance(teams, monkeypatch, fail_commit):
    """迁移成员时断开跨边界的继承边，不重新连接且保留来源信息，提交失败时两边不变"""
    source, target, member = teams
    child = lineage_domain.create_lineage_resident(
        source["lineage_id"], _new_session_payload("Remaining child"), ACTOR,
    )["resident"]
    edges = [
        {"id": "edge_000000000001", "type": "inheritance", "source": source["resident_id"], "target": member["resident_id"]},
        {"id": "edge_000000000002", "type": "inheritance", "source": member["resident_id"], "target": child["resident_id"]},
    ]
    add_relations(source, edges)
    before = [graph(source), graph(target)]
    original_record = resident_workspace_domain._find_resident(member["resident_id"])
    if fail_commit:
        persist = lineage_domain._persist_lineage_state

        def reject_target(previous, current, *args, **kwargs):
            if current["lineage_id"] == target["lineage_id"]:
                raise ValueError("injected migration failure")
            return persist(previous, current, *args, **kwargs)

        monkeypatch.setattr(lineage_domain, "_persist_lineage_state", reject_target)
        assert "injected migration failure" in execute(move_call(teams, [member["resident_id"]])).error["message"]
        assert [graph(source), graph(target)] == before
        return
    call = move_call(teams, [member["resident_id"]])
    result = execute(call).raise_for_error()
    assert [item["id"] for item in result["detached"]] == [edge["id"] for edge in edges]
    assert graph(source)["edges"] == graph(target)["edges"] == []
    assert {node["id"] for node in graph(source)["nodes"]} == {source["resident_id"], child["resident_id"]}
    current = resident_workspace_domain._find_resident(member["resident_id"])
    for key in ("provenance", "context_source"):
        assert current.get(key) == original_record.get(key)
    assert execute(call).replayed


def test_internal_relations_move_and_every_crossing_relation_is_detached():
    """迁移计划带走选中对象之间的关系，所有跨边界关系都断开"""
    source = {"nodes": [{"id": name} for name in ("a", "b", "c")], "edges": [
        {"id": "external", "type": "inheritance", "source": "a", "target": "b"},
        {"id": "internal", "type": "inheritance", "source": "b", "target": "c"},
        {"id": "handoff", "type": "handoff", "source": "c", "target": "a"},
    ], "program": {}}
    target = {"nodes": [], "edges": [], "program": {}}
    result = lineage_subgraph.plan(source, target, ["b", "c"])
    assert result["detached_edge_ids"] == ["external", "handoff"]
    assert result["edge_ids"] == ["internal"]
    assert [edge["id"] for edge in result["source"]["edges"]] == []
    assert [edge["id"] for edge in result["target"]["edges"]] == ["internal"]


def block_source():
    return {"nodes": [{"id": "resident_000000000001"}, {"id": "resident_000000000002"}],
            "edges": [], "program": {
        "components": [{"id": "event"}, {"id": "effect"}],
        "wires": [{"id": "wire", "source": "event", "target": "effect"},
                  {"id": "ask", "source": "event", "target": "resident_000000000001"}],
    }, "function_blocks": [{"id": "block", "title": "Local", "elements": [
        {"id": eid, "kind": kind, "key": None} for eid,kind in (("event","component"),("effect","component"),
        ("wire","wire"),("ask","wire"),("resident_000000000002","resident"))]}]}


def test_selecting_part_of_a_function_block_moves_the_whole_boundary():
    """选中函数块的部分元素时整个函数块及其元素一起迁移"""
    result = lineage_subgraph.plan(block_source(), {"nodes": [], "edges": [], "program": {}}, ["event"])
    assert set(result["moved_ids"]) == {"event", "effect", "wire", "block", "resident_000000000002"}
    block = result["target"]["function_blocks"][0]
    assert {e["id"] for e in block["elements"]} == {"event","effect","wire","resident_000000000002"}
    assert {item["type"] for item in result["detached_edges"]} == {"wire"}
    assert [node["id"] for node in result["source"]["nodes"]] == ["resident_000000000001"]
    assert result["source"]["function_blocks"] == []


def test_function_block_and_ownership_move_in_one_transaction(teams):
    """函数块与其成员在同一事务中迁移，内部元素位置不变"""
    source, target, member = teams
    event = event_source(source['lineage_id'])
    bid = ok('Functionalize', {'lineage_id':source['lineage_id'],
                             'element_ids':[member['resident_id'], event]})['function_block_id']
    before = graph(source)
    inner_positions = {item['id']: (item['x'], item['y'])
                       for item in [*before['nodes'], *before['program']['components']]
                       if item['id'] in {member['resident_id'], event}}
    moved(teams, [event])
    assert graph(source)['function_blocks'] == []
    block = next(b for b in graph(target)['function_blocks'] if b['id'] == bid)
    assert {e['id'] for e in block['elements']} == {member['resident_id'], event}
    after = graph(target)
    assert {item['id']: (item['x'], item['y'])
            for item in [*after['nodes'], *after['program']['components']]
            if item['id'] in inner_positions} == inner_positions


def test_operator_executor_bindings_across_the_boundary_are_cleared():
    """跨迁移边界的算子执行者绑定在两边都被清除并列为断开关系"""
    source = {"nodes": [{"id": "resident_000000000001", "operator_executor": {"component_id": "filter_000000000001", "kind": "filter"}}],
              "edges": [], "program": {"components": [
                  {"id": "filter_000000000001", "kind": "filter", "config": {"resident_executor_id": "resident_000000000001", "rule": "x"}},
              ]}}
    result = lineage_subgraph.plan(source, {"nodes": [], "edges": [], "program": {}}, ["filter_000000000001"])
    assert result["target"]["program"]["components"][0]["config"] == {"rule": "x"}
    assert "operator_executor" not in result["source"]["nodes"][0]
    assert result["detached_edges"] == [{"id": "filter_000000000001:executor", "source": "filter_000000000001",
                                         "target": "resident_000000000001", "type": "operator_executor"}]


def test_an_object_id_already_in_the_target_fails_the_whole_move():
    """目标 Lineage 已有相同 ID 的对象时整个迁移失败"""
    source = {"nodes": [{"id": "resident_000000000001"}], "edges": [], "program": {}}
    target = {"nodes": [{"id": "resident_000000000001"}], "edges": [], "program": {}}
    with pytest.raises(ValueError, match="相同 ID"):
        lineage_subgraph.plan(source, target, ["resident_000000000001"])


@pytest.mark.parametrize("busy_side", [0, 1])
def test_unselected_work_is_left_alone(teams, monkeypatch, busy_side):
    """迁移不影响两边 Lineage 中未选中对象的事件状态和待投递工作"""
    ids = setup_flow(teams)
    busy = teams[busy_side]
    lineage_id = busy["lineage_id"]
    unrelated = event_source(lineage_id, "Unrelated")
    lineage_program_runtime.claim_event_occurrence(lineage_id, unrelated, "unsettled-outside")
    from management_portal.society_service import trigger_paths
    event = {"id": "outside-event", "type": "manual.triggered", "occurred_at": "2026-10-12T00:00:00Z", "payload": {}}
    path = trigger_paths.begin_event(lineage_id, unrelated, event, cause_kind="external", cause_id="outside-event")
    lineage_program_runtime.register_resident_handoff(lineage_id, message_id="outside-message",
        resident_id=busy["resident_id"], trigger_path_id=path, event=event, source_wire_id="outside-wire")
    monkeypatch.setattr(resident_lifecycle_domain, "_resident_has_pending_work", lambda rid: rid == busy["resident_id"])
    before = lineage_program_runtime.read_runtime(lineage_id)
    pending = lineage_program_runtime.pending_resident_handoffs(lineage_id)
    moved(teams, ids)
    after = lineage_program_runtime.read_runtime(lineage_id)
    assert after["event_sources"][unrelated] == before["event_sources"][unrelated]
    assert lineage_program_runtime.pending_resident_handoffs(lineage_id) == pending
    assert resident_workspace_domain._find_resident(teams[2]["resident_id"])["lineage_id"] == teams[1]["lineage_id"]


def queue_on_wire(lineage_id: str, wire_id: str, event_source_id: str) -> None:
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        runtime["event_wires"][wire_id] = {"pending_events": [{
            "event_source_id": event_source_id, "cursor": "pending-cursor", "claim_token": "pending-claim",
            "event": {"id": "selected-event", "type": "manual.triggered"},
            "queued_at": "2026-09-21T00:00:00+00:00",
        }]}


def test_queued_work_on_internal_wires_moves_with_them(teams):
    """随迁移的内部连线上排队的事件一起迁到目标 Lineage"""
    ids = setup_flow(teams)
    source, target, _ = teams
    wire_id = graph(source)["program"]["wires"][0]["id"]
    queue_on_wire(source["lineage_id"], wire_id, ids[1])
    queued = lineage_program_runtime.read_runtime(source["lineage_id"])["event_wires"][wire_id]
    moved(teams, ids)
    assert wire_id not in lineage_program_runtime.read_runtime(source["lineage_id"])["event_wires"]
    assert lineage_program_runtime.read_runtime(target["lineage_id"])["event_wires"][wire_id] == queued


def test_queued_work_on_a_detached_wire_is_deleted_with_the_wire(teams):
    """被断开的连线上排队的事件随连线一起删除"""
    ids = setup_flow(teams)
    source, target, _ = teams
    wire_id = graph(source)["program"]["wires"][0]["id"]
    queue_on_wire(source["lineage_id"], wire_id, ids[1])
    moved(teams, ids[:1])
    assert wire_id not in lineage_program_runtime.read_runtime(source["lineage_id"])["event_wires"]
    assert wire_id not in lineage_program_runtime.read_runtime(target["lineage_id"])["event_wires"]


@pytest.mark.parametrize("status", ["queued", "completed"])
def test_invocations_and_their_history_stay_in_the_originating_lineage(teams, status):
    """已登记的 Resident 投递及其历史留在原 Lineage，不随对象迁移"""
    ids = setup_flow(teams)
    source = teams[0]["lineage_id"]
    wire_id = graph(teams[0])["program"]["wires"][0]["id"]
    from management_portal.society_service import trigger_paths
    from tests.cases.society_service_test_support import completed_execution
    event = {"id": "selected-event", "type": "manual.triggered", "occurred_at": "2026-10-12T00:00:00Z", "payload": {}}
    path = trigger_paths.begin_event(source, ids[1], event, cause_kind="external", cause_id="selected-event")
    lineage_program_runtime.register_resident_handoff(source, message_id="selected-message",
        resident_id=ids[0], trigger_path_id=path, event=event, source_wire_id=wire_id)
    if status == "completed":
        completed_execution(ids[0], "selected-message")
        lineage_program_runtime.settle_resident_handoffs(
            source, message_id="selected-message", resident_id=ids[0], status=status,
        )
    pending = lineage_program_runtime.pending_resident_handoffs(source)
    moved(teams, ids)
    assert lineage_program_runtime.pending_resident_handoffs(source) == pending
    assert lineage_program_runtime.pending_resident_handoffs(teams[1]["lineage_id"]) == []
    assert lineage_program_runtime.resident_handoff(source, ids[0], "selected-message")["status"] == status
    assert lineage_program_runtime.resident_handoff(teams[1]["lineage_id"], ids[0], "selected-message") is None


def test_moving_into_a_new_lineage_when_no_target_is_given(teams):
    """未指定目标时 MoveLineageObjects 在同一 Project 下新建 Lineage 接收对象"""
    ids = setup_flow(teams)
    source = teams[0]
    result = run("MoveLineageObjects", {"lineage_id": source["lineage_id"], "object_ids": ids}, key="new-target").raise_for_error()
    created = result["target_lineage_id"]
    assert created not in {source["lineage_id"], teams[1]["lineage_id"]}
    assert graph({"lineage_id": created})["project_id"] == source["project_id"]
    assert ids[0] in {node["id"] for node in graph({"lineage_id": created})["nodes"]}
