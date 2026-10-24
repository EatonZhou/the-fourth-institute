from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest

from management_portal.society_service import (
    lineage_domain, resident_catalog, resident_conversation_stream, resident_domain, resident_held_infos,
    resident_intake_domain, resident_lifecycle_domain, resident_workspace_domain,
)
from tests.cases.isa_test_support import reset_kernels, run
from tests.cases.test_lineage_member_projection import _family
from tests.cases.test_resident_folder_creation_contract import future_creation_store, _new_session_payload  # noqa: F401


@pytest.fixture
def deletion_set(future_creation_store):
    lineage_id, leader_id, first_id = _family()
    second_id = lineage_domain.create_lineage_resident(
        lineage_id, _new_session_payload("Second member"), "sample_admin",
    )["resident"]["resident_id"]
    component = lineage_domain.create_lineage_program_component(
        lineage_id, {"kind": "event_source", "title": "Batch event"}, "sample_admin",
    )["component"]
    store = resident_catalog.get_catalog().state_store
    reset_kernels()
    yield lineage_id, leader_id, [first_id, second_id], component["id"], store
    reset_kernels()


def _execute(lineage_id, args, key="batch-delete", actor="sample_admin"):
    """One ApplyLineageChanges call deleting Residents and components together."""

    operations = [{"op": "delete", "kind": "resident", "id": resident_id} for resident_id in args.get("resident_ids", [])]
    operations += [{"op": "delete", "kind": "component", "id": component_id} for component_id in args.get("component_ids", [])]
    return run("ApplyLineageChanges", {"lineage_id": lineage_id, "operations": operations},
               actor=actor, key=key)


def test_batch_deletion_commits_identity_set_graph_and_effects_once(deletion_set, monkeypatch):
    """批量删除 Resident 和组件一次提交身份、图和后续清理，重放返回同一结果"""
    lineage_id, leader_id, ids, component_id, store = deletion_set
    before = store.lineage_definition(lineage_id)
    catalog_store = resident_catalog.get_catalog()
    remove = Mock(wraps=catalog_store.remove)
    persist = Mock(wraps=lineage_domain._persist_lineage_state)
    monkeypatch.setattr(catalog_store, "remove", remove)
    monkeypatch.setattr(lineage_domain, "_persist_lineage_state", persist)
    args = {"resident_ids": ids, "component_ids": [component_id]}
    result = _execute(lineage_id, args)
    assert result.status == "committed", result
    replay = _execute(lineage_id, args)
    assert replay.replayed and replay.result["deleted_ids"] == result.result["deleted_ids"]
    remove.assert_called_once()
    assert {record["resident_id"] for record in remove.call_args.args[0]} == set(ids)
    assert all(store.resident_deletion(resident_id)["lineage_id"] == lineage_id for resident_id in ids)
    assert persist.call_count == 1
    assert set(ids) | {component_id} <= set(result.result["deleted_ids"])
    after = store.lineage_definition(lineage_id)
    assert after["revision"] == before["revision"] + 1
    assert {node["id"] for node in after["nodes"]} == {leader_id}
    assert {row["resident_id"] for row in store.residents_for_lineage(lineage_id)} == {leader_id}
    assert all(resident_lifecycle_domain._lifecycle_operations().pending(resident_id) for resident_id in ids)
    assert not any(item["id"] == component_id for item in after["program"]["components"])


@pytest.mark.parametrize("failure", ["running", "missing", "foreign", "duplicate", "component"])
def test_batch_validation_failure_keeps_every_identity_and_graph(deletion_set, monkeypatch, failure):
    """批次含运行中、不存在、外部或重复的 Resident 或缺失组件时整体拒绝，什么都不删"""
    lineage_id, leader_id, ids, component_id, store = deletion_set
    before = store.lineage_definition(lineage_id)
    args = {"resident_ids": list(ids), "component_ids": [component_id]}
    if failure == "running":
        monkeypatch.setattr(resident_lifecycle_domain, "_resident_has_pending_work", lambda resident_id: resident_id == ids[1])
    elif failure == "missing":
        args["resident_ids"].append("resident_123456789abc")
    elif failure == "foreign":
        _, other_leader, _ = _family()
        args["resident_ids"].append(other_leader)
    elif failure == "duplicate":
        args["resident_ids"].append(ids[0])
    else:
        args["component_ids"].append("event_source_missing")
    remove = Mock(wraps=resident_catalog.get_catalog().remove)
    monkeypatch.setattr(resident_catalog.get_catalog(), "remove", remove)
    with pytest.raises((ValueError, KeyError, lineage_domain.LineageConflictError)):
        lineage_domain.delete_lineage_graph_objects(lineage_id, args, "sample_admin")
    remove.assert_not_called()
    assert store.lineage_definition(lineage_id) == before
    assert {row["resident_id"] for row in store.residents_for_lineage(lineage_id)} == {leader_id, *ids}


def test_batch_commit_failure_rolls_back_identities_graph_and_cleanup(deletion_set, monkeypatch):
    """批量删除在图写入失败时整体回滚 Resident 身份、图定义和待执行的清理"""
    lineage_id, leader_id, ids, component_id, store = deletion_set
    before = store.lineage_definition(lineage_id)
    pending = resident_domain.ready_resident_lifecycle_operations()
    def fail(*args, **kwargs):
        assert store.database.write(lambda connection: connection.execute(
            'SELECT count(*) FROM resident_lifecycle_operations WHERE resident_id IN (%s,%s)', ids,
        ).fetchone()[0]) == len(ids)
        raise RuntimeError("graph write failed")
    monkeypatch.setattr(lineage_domain, "_persist_lineage_state", fail)
    with pytest.raises(RuntimeError, match="graph write failed"):
        lineage_domain.delete_lineage_graph_objects(lineage_id, {
            "resident_ids": ids, "component_ids": [component_id],
        }, "sample_admin")
    assert store.lineage_definition(lineage_id) == before
    assert {row["resident_id"] for row in store.residents_for_lineage(lineage_id)} == {leader_id, *ids}
    assert resident_domain.ready_resident_lifecycle_operations() == pending


def test_a_resident_member_can_delete_through_the_isa_in_open_mode(deletion_set, monkeypatch):
    """开放模式下 Lineage 成员 Resident 可经 ApplyLineageChanges 删除其他成员和组件"""

    lineage_id, leader_id, ids, component_id, store = deletion_set
    result = _execute(lineage_id, {"resident_ids": [ids[1]], "component_ids": [component_id]},
                      key="member-with-components", actor=ids[0])
    assert result.status == "committed", result
    assert {ids[1], component_id} <= set(result.result["deleted_ids"])


def test_component_only_batch_does_not_delete_resident_identities(deletion_set, monkeypatch):
    """只删组件的批次不删除任何 Resident 身份"""
    lineage_id, leader_id, ids, component_id, store = deletion_set
    before = store.lineage_definition(lineage_id)
    remove = Mock(wraps=resident_catalog.get_catalog().remove)
    monkeypatch.setattr(resident_catalog.get_catalog(), "remove", remove)
    result = _execute(lineage_id, {"component_ids": [component_id]})
    remove.assert_not_called()
    assert result.result["deleted_ids"] == [component_id]
    assert store.lineage_definition(lineage_id)["revision"] == before["revision"] + 1
    assert {row["resident_id"] for row in store.residents_for_lineage(lineage_id)} == {leader_id, *ids}


@pytest.mark.parametrize("entrypoint", ["DeleteResident", "ApplyLineageChanges", "rollback"])
def test_resident_deletion_removes_only_its_watchboard(deletion_set, monkeypatch, entrypoint):
    """Resident 删除与自身 Watchboard 图清理原子提交，保留其他面板和普通组件。"""
    from management_portal.society_service import watchboard_lineage

    lineage_id, leader_id, ids, component_id, store = deletion_set
    installed = {}
    for resident_id in ids:
        record = resident_catalog.get_catalog().get(resident_id)
        plan = watchboard_lineage.installation_plan(record, store.lineage_definition(lineage_id))
        result = run("ApplyLineageChanges", {
            "lineage_id": lineage_id, "operations": plan["install"],
        }, key="install-" + resident_id)
        assert result.status == "committed", result
        installed[resident_id] = {op["value"]["id"] for op in plan["install"]}
    before = store.lineage_definition(lineage_id)
    if entrypoint == "rollback":
        def fail(*_args, **_kwargs):
            raise RuntimeError("watchboard deletion rollback")
        monkeypatch.setattr(lineage_domain, "_persist_lineage_state", fail)
        with pytest.raises(RuntimeError, match="watchboard deletion rollback"):
            lineage_domain.delete_lineage_graph_objects(lineage_id, {"resident_ids": [ids[0]]}, "sample_admin")
        assert store.lineage_definition(lineage_id) == before
        assert resident_catalog.get_catalog().get(ids[0])["resident_id"] == ids[0]
        return
    if entrypoint == "DeleteResident":
        result = run(entrypoint, {"lineage_id": lineage_id, "resident_id": ids[0]}, key="delete-with-board")
    else:
        result = _execute(lineage_id, {"resident_ids": [ids[0]]}, key="delete-with-board")
        assert installed[ids[0]] <= set(result.result["deleted_ids"])
    assert result.status == "committed", result
    after = store.lineage_definition(lineage_id)
    remaining = {item["id"] for field in ("components", "wires") for item in after["program"][field]}
    assert not installed[ids[0]] & remaining
    assert installed[ids[1]] | {component_id} <= remaining
    assert after["revision"] == before["revision"] + 1
    assert {node["id"] for node in after["nodes"]} == {leader_id, ids[1]}


def test_apply_lineage_changes_returns_ids_without_building_full_lineage_view(deletion_set, monkeypatch):
    """ApplyLineageChanges 只返回删除的 ID，不构建完整 Lineage 视图"""
    lineage_id, _leader_id, _ids, component_id, _store = deletion_set

    def fail_if_projected(*_args, **_kwargs):
        raise AssertionError("ApplyLineageChanges should not build a full Lineage view")

    monkeypatch.setattr(lineage_domain, "_verified_lineage_view", fail_if_projected)
    result = _execute(lineage_id, {"component_ids": [component_id]}, key="ids-only-batch")

    assert result.status == "committed", result
    assert result.result["deleted_ids"] == [component_id]


@pytest.mark.parametrize("args", [
    {},
    {"resident_ids": []},
    {"resident_ids": None},
    {"resident_ids": "resident_123456789abc"},
    {"resident_ids": [""]},
    {"resident_ids": [" resident_123456789abc"]},
    {"component_ids": [42]},
    {"component_ids": ["duplicate", "duplicate"]},
])
def test_malformed_batch_cannot_mutate_graph(deletion_set, args):
    """格式错误的批量删除参数被拒绝，图和 Resident 保持不变"""
    lineage_id, leader_id, ids, _, store = deletion_set
    before = store.lineage_definition(lineage_id)
    with pytest.raises(ValueError):
        lineage_domain.delete_lineage_graph_objects(lineage_id, args, "sample_admin")
    assert store.lineage_definition(lineage_id) == before
    assert {row["resident_id"] for row in store.residents_for_lineage(lineage_id)} == {leader_id, *ids}


def test_deleting_a_resident_cancels_what_still_waits_in_its_mailbox(deletion_set):
    """删除 Resident 时关闭它的 Mailbox

    Resident 删除后不再有人能处理或撤回它 Mailbox 中的消息。验证删除在同一事务中把仍为 queued 的输入（等待中的 info、队列丢下的请求）结束为 cancelled（错误码 resident_deleted），并删除它在 resident_mailbox 中的全部行。

    :module: resident.conversation
    :importance: P1
    :design: decision_b5d349190743, decision_0f57656a2acd
    """
    # Until 10-10 deletion left these rows and inputs behind; nothing could
    # process or withdraw them once the Resident was gone.
    lineage_id, _leader_id, ids, _component_id, store = deletion_set
    resident_id = ids[0]
    repository = resident_conversation_stream.get_repository()
    waiting = []
    for text in ("队列丢下的请求", "等待下一条请求的 info"):
        message = resident_intake_domain._message("user", text, "sample_admin", message_id="msg_" + uuid.uuid4().hex)
        resident_lifecycle_domain._accept_resident_user_message(resident_id, message)
        repository.set_input_state(resident_id, message["message_id"], "queued")
        waiting.append(message)
    resident_held_infos.hold(resident_workspace_domain._find_resident(resident_id), waiting[1])
    assert [entry["place"] for entry in store.mailbox_entries([resident_id], places=("pending", "info"))] == ["info", "pending"]

    result = _execute(lineage_id, {"resident_ids": [resident_id]}, key="delete-with-mailbox")

    assert result.status == "committed", result
    assert store.mailbox_entries([resident_id]) == []
    for message in waiting:
        state = repository.input_state(resident_id, message["message_id"])
        assert state["state"] == "cancelled" and state["error"]["code"] == "resident_deleted"
