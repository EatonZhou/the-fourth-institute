"""MoveResident through the HATG ISA (decision_6591e7793116)."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import (
    lineage_graph_edits,
    lineage_lifecycle,
    lineage_moves,
    lineage_reads,
    lineage_resident_nodes,
    resident_backend,
    resident_domain,
    resident_lifecycle_domain,
    resident_lifecycle_steps_domain,
)
from management_portal.society_service import resident_record_domain
from management_portal.society_service import lineage_aggregate
from management_portal.society_service import project_folders
from tests.cases.isa_test_support import ACTOR, ok, reset_kernels, run
from tests.cases.test_resident_folder_creation_contract import (
    _append_conversation_turn,
    _new_session_payload,
    future_creation_store,  # noqa: F401 - fixture
)


@pytest.fixture
def teams(future_creation_store, monkeypatch):  # noqa: F811 - pytest fixture
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    reset_kernels()
    source = resident_domain.register_resident(_new_session_payload("Source"), ACTOR)
    target = resident_domain.register_resident(_new_session_payload("Target"), ACTOR)
    member = lineage_resident_nodes.create_lineage_resident(
        source["lineage_id"], _new_session_payload("Existing member"), ACTOR,
    )["resident"]
    yield source, target, member
    reset_kernels()


def graph(record):
    return lineage_reads.get_lineage(record["lineage_id"], ACTOR)


def request(teams, key="move-one"):
    """One MoveResident call; it carries no Lineage version precondition."""

    _source, target, member = teams
    return {
        "instruction": "MoveResident",
        "key": key,
        "arguments": {"lineage_id": _source["lineage_id"], "resident_id": member["resident_id"], "target_lineage_id": target["lineage_id"]},
    }


def execute(call, actor=ACTOR, entry="web"):
    return run(call["instruction"], call["arguments"], actor=actor, key=call.get("key", ""),
               entry=entry)


def failure(call, **options) -> dict:
    result = execute(call, **options)
    assert result.status == "failed", result
    return result.error


def unchanged(teams, before):
    source, target, member = teams
    assert [graph(source), graph(target)] == before
    assert resident_domain.resolve_resident_identity(member["resident_id"], ACTOR)["lineage_id"] == source["lineage_id"]
    assert not resident_record_domain.lifecycle_operations().pending(member["resident_id"])


@pytest.mark.parametrize("empty_target", [False, True])
def test_move_preserves_member_conversation_files_and_replays(teams, empty_target):
    """MoveResident 迁移成员后保留会话、对话、Workspace 和文件，重复调用回放同一操作"""
    source, target, member = teams
    if empty_target:
        target = lineage_lifecycle.create_lineage({"title": "Empty target", "project_id": source["project_id"]}, ACTOR)
        teams = source, target, member
        assert graph(target)["nodes"] == []
    resident_id = member["resident_id"]
    _append_conversation_turn(resident_id, 0)
    agent_data = get_agent_data_store()
    conversation = agent_data.read_chat_messages(resident_id)
    identity = resident_record_domain.find_resident(resident_id)
    session_id = resident_backend.record_session_id(identity)
    workspace_id = identity["owned_workspace_id"]
    original = resident_record_domain.resident_folder_path(identity)
    (original / "artifact.txt").write_text("real artifact contents", encoding="utf-8")
    node = next(item for item in graph(source)["nodes"] if item["id"] == resident_id)
    call = request(teams)
    revisions = [graph(source)["revision"], graph(target)["revision"]]
    first = execute(call)
    assert first.status == "committed" and not first.replayed
    after = [graph(source), graph(target)]
    assert [after[0]["revision"], after[1]["revision"]] == [revisions[0] + 1, revisions[1] + 1]
    assert first.result["resident_id"] == resident_id
    assert first.result["operation_id"].startswith("resident-operation_")
    assert resident_id not in {item["id"] for item in after[0]["nodes"]}
    moved_node = next(item for item in after[1]["nodes"] if item["id"] == resident_id)
    assert moved_node["title"] == node["title"]
    assert after[0]["leader_resident_id"] == source["resident_id"]
    if not empty_target:
        assert after[1]["leader_resident_id"] == target["resident_id"]
    else:
        assert [item["id"] for item in after[1]["nodes"]] == [resident_id]
    replay = execute(call)
    assert replay.replayed and replay.instruction_id == first.instruction_id
    assert replay.result["operation_id"] == first.result["operation_id"]
    assert [graph(source), graph(target)] == after
    assert agent_data.read_chat_messages(resident_id) == conversation
    assert original.exists()
    operation = ok("GetMoveOperation", {"operation_id": first.result["operation_id"]})
    assert operation["source_lineage_id"] == source["lineage_id"]
    assert operation["target_lineage_id"] == target["lineage_id"]
    operations = resident_record_domain.lifecycle_operations()
    assert operations.run(first.result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == resident_id
    updated = resident_record_domain.find_resident(resident_id)
    assert updated["lineage_id"] == target["lineage_id"]
    assert resident_backend.record_session_id(updated) == session_id
    assert updated["owned_workspace_id"] == workspace_id
    assert not original.exists()
    assert (resident_record_domain.resident_folder_path(updated) / "artifact.txt").read_text() == "real artifact contents"
    assert agent_data.read_chat_messages(resident_id) == conversation
    assert execute(call).result["operation_id"] == first.result["operation_id"]


def test_move_avoids_target_node_at_the_source_position(teams):
    """迁入位置与目标 Lineage 已有节点重叠时，迁入节点避开该节点"""
    source, target, member = teams
    target_before = graph(target)
    obstacle = target_before["nodes"][0]
    lineage_lifecycle.update_lineage_layout(source["lineage_id"], {"positions": [
        {"id": member["resident_id"], "x": obstacle["x"], "y": obstacle["y"]},
    ]}, ACTOR)
    execute(request(teams)).raise_for_error()
    after = graph(target)
    moved = next(item for item in after["nodes"] if item["id"] == member["resident_id"])
    assert (abs(moved["x"] - obstacle["x"]) >= (154 + 24) / 12.8
            or abs(moved["y"] - obstacle["y"]) >= (94 + 24) / 9)
    assert next(item for item in after["nodes"] if item["id"] == obstacle["id"]) == obstacle


def test_move_preserves_source_position_when_destination_has_room(teams):
    """目标 Lineage 原位置空闲时迁入节点保持源位置坐标"""
    source, target, member = teams
    lineage_lifecycle.update_lineage_layout(source["lineage_id"], {"positions": [
        {"id": member["resident_id"], "x": 87, "y": 81},
    ]}, ACTOR)
    execute(request(teams)).raise_for_error()
    moved = next(item for item in graph(target)["nodes"] if item["id"] == member["resident_id"])
    assert (moved["x"], moved["y"]) == (87, 81)


@pytest.mark.parametrize("reason", ["leader", "same_lineage", "unknown_argument"])
def test_move_rejects_invalid_membership(teams, reason):
    """迁移 Leader、迁到同一 Lineage 或带未知参数时 MoveResident 被拒绝且不改变"""
    source, target, member = teams
    call = request(teams)
    if reason == "leader":
        call["arguments"]["resident_id"] = source["resident_id"]
    elif reason == "same_lineage":
        call["arguments"]["target_lineage_id"] = source["lineage_id"]
    else:
        call["arguments"]["source_lineage_id"] = target["lineage_id"]
    before = [graph(source), graph(target)]
    error = failure(call)
    assert error["code"] in {"instruction.invalid_arguments", "instruction.conflict"}
    if reason == "leader":
        assert "Leader" in error["message"]
    unchanged(teams, before)


def test_a_busy_resident_moves_and_its_folder_follows_when_the_running_turn_ends(teams, monkeypatch):
    """忙碌的 Resident 先迁入目标 Lineage，文件夹等运行中的 turn 结束后再移动"""
    source, target, member = teams
    resident_id = member["resident_id"]
    monkeypatch.setattr(resident_lifecycle_domain, "resident_has_pending_work", lambda identity: identity == resident_id)
    running = {"value": True}
    monkeypatch.setattr(resident_lifecycle_domain, "resident_turn_running", lambda identity: running["value"])
    original = resident_record_domain.resident_folder_path(resident_record_domain.find_resident(resident_id))
    result = execute(request(teams)).raise_for_error()
    assert resident_id in {node["id"] for node in graph(target)["nodes"]}
    operations = resident_record_domain.lifecycle_operations()
    # While the turn runs, the folder stays where the turn is working and queued work waits.
    assert operations.run(result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == ""
    assert original.exists() and operations.pending(resident_id)
    assert ok("GetMoveOperation", {"operation_id": result["operation_id"]})["terminal"] is False
    running["value"] = False
    operations.store.database.write(lambda connection: connection.execute(
        "UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s", (result["operation_id"],)))
    assert operations.run(result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == resident_id
    assert not original.exists()
    moved = resident_record_domain.find_resident(resident_id)
    assert resident_record_domain.resident_folder_path(moved).exists()


@pytest.mark.parametrize("side", ["source", "target"])
def test_move_checks_both_lineage_owners(teams, monkeypatch, side):
    """MoveResident 同时检查源和目标 Lineage 的所有者权限，任一不通过则两边不变"""
    blocked = teams[0 if side == "source" else 1]["lineage_id"]
    ensure_manager = lineage_aggregate.ensure_manager
    call = request(teams)
    before = [graph(teams[0]), graph(teams[1])]

    def check(lineage, actor):
        if lineage["lineage_id"] == blocked:
            raise PermissionError("No permission for this Lineage")
        return ensure_manager(lineage, actor)

    with monkeypatch.context() as denied:
        denied.setattr(lineage_aggregate, "ensure_manager", check)
        assert failure(call)["code"] == "instruction.forbidden"
    unchanged(teams, before)


@pytest.mark.parametrize("kind", ["wires", "components", "function_blocks"])
def test_move_reference_guard_does_not_prune_flows(teams, kind):
    """迁移前的引用检查发现连线、组件或函数块引用该成员时报错，不删除这些对象"""
    source, _, member = teams
    definition = graph(source)
    before = copy.deepcopy(definition)
    objects = definition if kind == "function_blocks" else definition["program"]
    objects[kind] = [{"id": "existing-object", "config": {"members": [member["resident_id"]]}}]
    with pytest.raises(ValueError, match=f"{kind}/existing-object"):
        lineage_moves._resident_move_patch(definition, member["resident_id"])
    assert objects[kind][0]["id"] == "existing-object"
    assert graph(source) == before


def test_move_detaches_relations_without_reconnecting(teams):
    """迁移成员时断开它的继承等关系并列在回执中，源图不绕过它补建继承边，来源信息保留"""
    source, target, member = teams
    child = lineage_resident_nodes.create_lineage_resident(
        source["lineage_id"], _new_session_payload("Forked from member"), ACTOR,
    )["resident"]
    edges = [
        {"id": "edge_000000000001", "type": "inheritance", "source": source["resident_id"], "target": member["resident_id"]},
        {"id": "edge_000000000002", "type": "inheritance", "source": member["resident_id"], "target": child["resident_id"]},
    ]
    lineage_graph_edits._apply_lineage_graph_patch(source["lineage_id"], {
        "patch": {"edges": edges}, "history_reason": "test relations",
    }, ACTOR, synchronize_external_projections=False)
    record = resident_record_domain.find_resident(member["resident_id"])
    result = execute(request(teams)).raise_for_error()
    assert [item["id"] for item in result["detached"]] == [edge["id"] for edge in edges]
    assert result["family_id"] == ""
    assert graph(source)["edges"] == graph(target)["edges"] == []
    moved = resident_record_domain.find_resident(member["resident_id"])
    for key in ("provenance", "context_source"):
        assert moved.get(key) == record.get(key)


def test_move_of_a_wired_resident_is_refused(teams):
    """有连线指向的 Resident 不能迁移"""
    source, _, member = teams
    component = ok("CreateComponent", {
        "lineage_id": source["lineage_id"], "kind": "event_source", "title": "Real workflow",
        "config": {"event_type": "manual.triggered"},
    }, key="source")
    ok("CreateWire", {
        "lineage_id": source["lineage_id"], "kind": "route",
        "source": component["component_id"], "target": member["resident_id"],
    }, key="wire")
    call = request(teams)
    before = [graph(source), graph(teams[1])]
    assert "仍被流程引用" in failure(call)["message"]
    unchanged(teams, before)


def test_concurrent_moves_of_different_members_both_land(teams):
    """并发迁移同一 Lineage 的不同成员都生效

    验证从同一源 Lineage 并发迁出两个不同成员时两次迁移都提交，源和目标的成员列表都包含两次修改。
    """
    source, target, _ = teams
    second = lineage_resident_nodes.create_lineage_resident(
        source["lineage_id"], _new_session_payload("Second real member"), ACTOR,
    )["resident"]
    calls = [request(teams, "first"), request((source, target, second), "second")]

    def attempt(call):
        result = execute(call)
        return result.status if result.status == "committed" else result.error["code"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, calls))
    assert results == ["committed", "committed"]
    assert [node["id"] for node in graph(source)["nodes"]] == [source["resident_id"]]
    assert {node["id"] for node in graph(target)["nodes"]} == {
        target["resident_id"], teams[2]["resident_id"], second["resident_id"],
    }


def test_a_resident_caller_can_move_across_lineages_in_open_mode(teams, monkeypatch):
    """开放模式下 Resident 调用者可以跨 Lineage 迁移成员"""

    caller = teams[0]["resident_id"]
    result = execute(request(teams), actor=caller, entry="harness")
    assert result.status == "committed", result
    moved_id = teams[2]["resident_id"]
    assert resident_record_domain.find_resident(moved_id)["lineage_id"] == teams[1]["lineage_id"]
    assert moved_id not in {node["id"] for node in graph(teams[0])["nodes"]}
    assert moved_id in {node["id"] for node in graph(teams[1])["nodes"]}


def test_move_failure_rolls_back_both_graphs_membership_and_outbox(teams, monkeypatch):
    """目标 Lineage 提交失败时迁移回滚两边图、成员关系和 outbox"""
    source, target, _ = teams
    call = request(teams)
    before = [graph(source), graph(target)]

    def fail(*args, **kwargs):
        raise ValueError("destination commit failed")

    monkeypatch.setattr(lineage_resident_nodes, "_append_registered_resident_node_locked", fail)
    assert "destination commit failed" in failure(call)["message"]
    unchanged(teams, before)


def test_move_refuses_the_last_leader_and_never_deletes_the_source(teams):
    """未允许清空源 Lineage 时拒绝迁走最后的 Leader，源 Lineage 不被删除"""
    source, target, member = teams
    assert execute(request(teams)).status == "committed"
    before = graph(source)
    assert "Leader" in failure(request((source, target, source), "last-member"))["message"]
    assert graph(source) == before
    assert [node["id"] for node in before["nodes"]] == [source["resident_id"]]
    assert before["leader_resident_id"] == source["resident_id"]
    assert {node["id"] for node in graph(target)["nodes"]} == {target["resident_id"], member["resident_id"]}




def test_an_idempotency_key_cannot_be_reused_for_another_move(teams):
    """同一 idempotency_key 换参数再次迁移返回幂等冲突"""
    call = request(teams)
    assert execute(call).status == "committed"
    after = [graph(teams[0]), graph(teams[1])]
    call["arguments"]["allow_empty_source"] = True
    assert failure(call)["code"] == "instruction.idempotency_conflict"
    assert [graph(teams[0]), graph(teams[1])] == after


def test_move_into_a_new_empty_lineage(teams):
    """Resident 可以迁入新建的空 Lineage，其他 Lineage 不受影响"""
    empty = ok("CreateLineage", {"title": "Empty target", "project_id": teams[0]["project_id"]}, key="empty")
    call = request(teams)
    call["arguments"]["target_lineage_id"] = empty["lineage_id"]
    before = graph(teams[1])
    moved = execute(call).raise_for_error()
    assert moved["target_lineage_id"] == empty["lineage_id"]
    assert [node["id"] for node in graph(empty)["nodes"]] == [teams[2]["resident_id"]]
    assert graph(teams[1]) == before
    assert teams[2]["resident_id"] not in {node["id"] for node in graph(teams[0])["nodes"]}
