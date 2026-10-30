"""Moving a Lineage's last member (allow_empty_source) and creating Residents across Lineages."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import (
    lineage_domain,
    lineage_resident_nodes,
    resident_backend,
    resident_domain,
    resident_lifecycle_steps_domain,
)
from management_portal.society_service import resident_record_domain
from management_portal.society_service import lineage_aggregate
from tests.cases.isa_test_support import ok, run
from tests.cases.test_hatg_move_resident import (  # noqa: F401 - teams is a fixture
    ACTOR, execute, failure, graph, request, teams, unchanged,
)
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401 - fixture
    _append_conversation_turn, future_creation_store,
)


def empty_source_request(teams):
    source, target, _member = teams
    assert execute(request(teams)).status == "committed"
    singleton = (source, target, source)
    call = request(singleton, "empty-source")
    call["arguments"]["allow_empty_source"] = True
    return singleton, call


def finish_transfers():
    operations = resident_record_domain.lifecycle_operations()
    for operation_id in operations.ready():
        assert operations.run(operation_id, resident_lifecycle_steps_domain.apply_resident_lifecycle_step)


def test_move_last_member_preserves_identity_and_leaves_an_explicitly_deletable_source(teams):
    """迁走 Lineage 最后一个成员时保留其会话、对话和文件，源 Lineage 留空待显式删除"""
    singleton, call = empty_source_request(teams)
    source, target, _ = singleton
    resident_id = source["resident_id"]
    _append_conversation_turn(resident_id, 0)
    original = resident_record_domain.find_resident(resident_id)
    session_id = resident_backend.record_session_id(original)
    folder = resident_record_domain.resident_folder_path(original)
    (folder / "artifact.txt").write_text("Existing artifact", encoding="utf-8")
    conversation = get_agent_data_store().read_chat_messages(resident_id)
    revisions = [graph(source)["revision"], graph(target)["revision"]]
    first = execute(call)
    assert first.status == "committed", first
    after = graph(source)
    assert after["nodes"] == []
    assert after["leader_resident_id"] == ""
    assert "runtime_principal_id" not in after
    assert "leader_profile" not in after
    assert after["revision"] == revisions[0] + 1
    assert after["title"] == "Source"
    assert "owner_ids" not in after
    assert graph(target)["leader_resident_id"] == target["resident_id"]
    assert graph(target)["revision"] == revisions[1] + 1
    replay = execute(call)
    assert replay.replayed and replay.result["operation_id"] == first.result["operation_id"]
    assert graph(source) == after
    operations = resident_record_domain.lifecycle_operations()
    assert operations.run(first.result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == resident_id
    current = resident_record_domain.find_resident(resident_id)
    assert current["owned_workspace_id"] == original["owned_workspace_id"]
    assert resident_backend.record_session_id(current) == session_id
    assert get_agent_data_store().read_chat_messages(resident_id) == conversation
    assert (resident_record_domain.resident_folder_path(current) / "artifact.txt").read_text() == "Existing artifact"
    assert graph(source)["nodes"] == []
    finish_transfers()
    ok("DeleteLineage", {"lineage_id": source["lineage_id"]}, key="explicit-delete-empty-source")
    assert resident_record_domain.find_resident(resident_id)["lineage_id"] == target["lineage_id"]
    assert (resident_record_domain.resident_folder_path(current) / "artifact.txt").exists()


@pytest.mark.parametrize("side", ["source", "target"])
def test_lineage_delete_waits_for_outgoing_and_incoming_workspace_transfers(teams, side):
    """源或目标 Lineage 还有未完成的 Workspace 转移时 DeleteLineage 被拒绝"""
    singleton, call = empty_source_request(teams)
    source, target, _ = singleton
    first = execute(call)
    operations = resident_record_domain.lifecycle_operations()
    lineage = source if side == "source" else target
    before = graph(lineage)
    early = run("DeleteLineage", {"lineage_id": lineage["lineage_id"]}, key="premature-delete")
    assert early.status == "failed" and "pending workspace transfer" in early.error["message"]
    assert graph(lineage) == before
    # Finishing the last member's move must not hide an earlier member's outbox.
    assert operations.run(first.result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    again = run("DeleteLineage", {"lineage_id": lineage["lineage_id"]}, key="earlier-transfer-still-pending")
    assert again.status == "failed" and "pending workspace transfer" in again.error["message"]
    finish_transfers()
    assert not operations.pending_lineage_transfer(source["lineage_id"])
    assert not operations.pending_lineage_transfer(target["lineage_id"])


def test_direct_leader_deletion_cannot_archive_an_outgoing_workspace_transfer(teams):
    """Workspace 转出未完成时直接删除 Leader 所在 Lineage 被拒绝"""
    source, _target, _member = teams
    assert execute(request(teams)).status == "committed"
    before = graph(source)
    with pytest.raises(ValueError, match="pending workspace transfer"):
        resident_domain.delete_resident_lineage(source["resident_id"], ACTOR)
    assert graph(source) == before


@pytest.mark.parametrize("value", [0, 1, "true"])
def test_move_empty_source_flag_requires_boolean(teams, value):
    """allow_empty_source 不是布尔值时迁移以参数错误拒绝且两边不变"""
    call = request(teams)
    call["arguments"]["allow_empty_source"] = value
    before = [graph(teams[0]), graph(teams[1])]
    assert failure(call)["code"] == "instruction.invalid_arguments"
    unchanged(teams, before)


@pytest.mark.parametrize("blocker", ["flow", "pending_delivery"])
def test_move_empty_source_requires_a_singleton_without_flow_or_work(teams, monkeypatch, blocker):
    """源 Lineage 还有流程组件或待投递工作时不能迁走最后一个成员"""
    singleton, call = empty_source_request(teams)
    source = singleton[0]
    if blocker == "flow":
        ok("CreateComponent", {"lineage_id": source["lineage_id"], "kind": "event_source",
                               "title": "Disconnected flow", "config": {"event_type": "manual.triggered"}}, key="flow")
    elif blocker == "pending_delivery":
        from management_portal.society_service import lineage_program_resident_inputs
        runtime = lineage_program_resident_inputs.pending_resident_handoffs

        def with_pending(lineage_id):
            result = runtime(lineage_id)
            if lineage_id == source["lineage_id"]:
                result = [{"status": "queued", "resident_id": source["resident_id"]}]
            return result

        monkeypatch.setattr(lineage_program_resident_inputs, "pending_resident_handoffs", with_pending)
    before = [graph(singleton[0]), graph(singleton[1])]
    failure(call)
    unchanged(singleton, before)


@pytest.mark.parametrize("side", ["source", "target"])
def test_move_empty_source_keeps_the_permission_guard_on_both_lineages(teams, monkeypatch, side):
    """迁走最后成员时两边 Lineage 的权限检查都保留

    验证迁走源 Lineage 最后一个成员时，源和目标 Lineage 的所有者检查都生效，失败时两边都不改变。
    """
    singleton, call = empty_source_request(teams)
    before = [graph(singleton[0]), graph(singleton[1])]
    lineage_id = singleton[0 if side == "source" else 1]["lineage_id"]
    ensure_manager = lineage_aggregate.ensure_manager

    def check(lineage, actor):
        if lineage["lineage_id"] == lineage_id:
            raise PermissionError("No target permission")
        return ensure_manager(lineage, actor)

    monkeypatch.setattr(lineage_aggregate, "ensure_manager", check)
    assert failure(call)["code"] == "instruction.forbidden"
    unchanged(singleton, before)


def test_move_empty_source_rolls_back_leader_membership_and_outbox(teams, monkeypatch):
    """迁走最后成员在目标提交失败时回滚 Leader、成员关系和 outbox"""
    singleton, call = empty_source_request(teams)
    before = [graph(singleton[0]), graph(singleton[1])]

    def fail(*args, **kwargs):
        raise ValueError("destination commit failed")

    monkeypatch.setattr(lineage_resident_nodes, "_append_registered_resident_node_locked", fail)
    assert "destination commit failed" in failure(call)["message"]
    unchanged(singleton, before)


def test_concurrent_moves_of_the_last_member_move_it_once(teams):
    """并发迁走同一个最后成员只迁移一次

    验证两次并发迁走同一个最后成员时只有一次生效，另一次自然失败，两边 Lineage 各只提交一次。
    """
    singleton, call = empty_source_request(teams)
    revisions = [graph(singleton[0])["revision"], graph(singleton[1])["revision"]]

    def move(key):
        candidate = copy.deepcopy(call)
        candidate["key"] = key
        result = execute(candidate)
        return result.status if result.status == "committed" else result.error["code"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(move, ["last-member-one", "last-member-two"]))
    # The second move finds the Resident already in the target Lineage.
    assert sorted(results)[0] == "committed" and sorted(results)[1].startswith("instruction.")
    assert graph(singleton[0])["nodes"] == []
    assert [node["id"] for node in graph(singleton[1])["nodes"]].count(singleton[0]["resident_id"]) == 1
    assert graph(singleton[0])["revision"] == revisions[0] + 1
    assert graph(singleton[1])["revision"] == revisions[1] + 1


def test_a_resident_can_move_itself_as_the_last_member_in_open_mode(teams, monkeypatch):
    """开放模式下 Resident 可以把作为最后成员的自己迁到另一个 Lineage"""
    singleton, call = empty_source_request(teams)
    caller = singleton[0]["resident_id"]
    result = execute(call, actor=caller, entry="harness")
    assert result.status == "committed", result
    assert graph(singleton[0])["nodes"] == []
    assert caller in {node["id"] for node in graph(singleton[1])["nodes"]}
    assert resident_record_domain.find_resident(caller)["lineage_id"] == singleton[1]["lineage_id"]


@pytest.mark.parametrize("anchor", ["default", "explicit_target", "explicit_foreign"])
def test_cross_lineage_create_validates_target_placement(teams, monkeypatch, anchor):
    """跨 Lineage 创建 Resident 时校验放置锚点，锚点不在目标 Lineage 则不创建"""
    source, target, _ = teams
    caller = source["resident_id"]
    before = graph(target)
    registrations = []
    register = resident_domain.register_resident

    def observe_registration(*args, **kwargs):
        result = register(*args, **kwargs)
        registrations.append(result)
        return result

    monkeypatch.setattr(lineage_domain.resident_domain, "register_resident", observe_registration)
    arguments = {"lineage_id": target["lineage_id"], "title": "Paper Collector", "provider": "codex"}
    if anchor != "default":
        arguments["near_resident_id"] = target["resident_id"] if anchor == "explicit_target" else caller
    original = copy.deepcopy(arguments)
    result = run("CreateResident", arguments, actor=caller, entry="harness", key="cross-lineage-create-collector")
    if anchor == "explicit_foreign":
        assert result.status == "failed"
        assert registrations == []
        assert graph(target) == before
    else:
        assert result.status == "committed", result
        assert len(registrations) == 1
        created_id = registrations[0]["resident_id"]
        assert result.result["resident_id"] == created_id
        assert resident_record_domain.find_resident(created_id)["lineage_id"] == target["lineage_id"]
        assert created_id in {node["id"] for node in graph(target)["nodes"]}
        assert graph(target)["revision"] == before["revision"] + 1
    assert arguments == original
