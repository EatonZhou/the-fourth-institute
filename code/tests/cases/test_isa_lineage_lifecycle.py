"""Leader re-pick and Function Block instances through the ISA (decision_6fc653d55c79, decision_5b5653c3eaa5).

:module: isa.instructions
"""

from __future__ import annotations

import pytest

from management_portal.society_service import resident_catalog, resident_record_domain
from management_portal.society_service import project_folders
from tests.cases.isa_test_support import ACTOR, ok, reset_kernels, run
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401 - fixture


@pytest.fixture
def lineage(future_creation_store, monkeypatch):  # noqa: F811 - pytest fixture
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    reset_kernels()
    created = ok("CreateLineage", {"title": "Team", "project_id": "project_test"}, key="lineage")
    yield created["lineage_id"]
    reset_kernels()


def resident(lineage_id: str, title: str) -> str:
    return ok("CreateResident", {"lineage_id": lineage_id, "title": title, "provider": "codex"}, key=f"r-{title}")["resident_id"]


def graph(lineage_id: str) -> dict:
    return ok("GetLineage", {"lineage_id": lineage_id})["lineage"]


def test_diagnostics_use_definition_without_full_lineage_view(lineage, monkeypatch):
    """Lineage 诊断只读取定义

    :module: lineage.service"""
    from management_portal.society_service import lineage_domain

    monkeypatch.setattr(lineage_domain, "get_lineage", lambda *_: pytest.fail("full Lineage view is unnecessary"))
    assert isinstance(ok("GetLineageDiagnostics", {"lineage_id": lineage})["diagnostics"], list)


def test_effect_script_patch_keeps_existing_endpoints(lineage):
    """UpdateComponent 只改 Effect 脚本时保留原有 endpoints

    :module: lineage.service"""
    created = ok("CreateComponent", {
        "lineage_id": lineage, "kind": "effect", "title": "Send",
        "config": {"script": "ListComponentTypes", "endpoints": ["target"]},
    }, key="effect-patch-create")
    updated = ok("UpdateComponent", {
        "lineage_id": lineage, "component_id": created["component_id"],
        "changes": {"script": "GetPulse"},
    }, key="effect-patch-update")
    assert updated["component"]["config"] == {
        **created["component"]["config"], "script": "GetPulse",
    }


def test_a_lineage_with_a_resident_always_has_a_leader(lineage):
    """有 Resident 的 Lineage 始终有 Leader，清空指定 Leader 后由剩余成员补上"""
    assert graph(lineage)["leader_resident_id"] == ""
    first = resident(lineage, "First")
    assert graph(lineage)["leader_resident_id"] == first
    second = resident(lineage, "Second")
    assert graph(lineage)["leader_resident_id"] == first
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": second}}, key="leader-second")
    assert graph(lineage)["leader_resident_id"] == second
    # With no explicit leader, any remaining member may fill the seat.
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": ""}}, key="leader-clear")
    assert graph(lineage)["leader_resident_id"] in {first, second}


def test_deleting_the_leader_picks_a_new_leader_from_the_remaining_residents(lineage):
    """删除 Leader 时从剩余 Resident 中选出新 Leader，删除其他成员不改变 Leader"""
    first = resident(lineage, "First")
    second = resident(lineage, "Second")
    third = resident(lineage, "Third")
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": first}}, key="leader")
    assert graph(lineage)["leader_resident_id"] == first
    deleted = ok("DeleteResident", {"lineage_id": lineage, "resident_id": first}, key="delete-first")
    assert deleted["new_leader_resident_id"] in {second, third}
    assert graph(lineage)["leader_resident_id"] == deleted["new_leader_resident_id"]
    # Deleting a Resident that is not the Leader keeps the Leader.
    other = ({second, third} - {deleted["new_leader_resident_id"]}).pop()
    kept = ok("DeleteResident", {"lineage_id": lineage, "resident_id": other}, key="delete-other")
    assert graph(lineage)["leader_resident_id"] == deleted["new_leader_resident_id"]
    assert not kept.get("new_leader_resident_id")
    last = ok("DeleteResident", {"lineage_id": lineage, "resident_id": deleted["new_leader_resident_id"]}, key="delete-last")
    assert graph(lineage)["leader_resident_id"] == ""
    assert not last.get("new_leader_resident_id")


def test_a_repeated_creation_key_returns_the_resident_it_already_created(lineage):
    """相同 creation_key 再次创建 Resident 时返回已创建的那一个"""
    from management_portal.society_service import lineage_domain

    payload = {"title": "Application worker", "backend": "codex", "creation_key": "invocation-create:fixture"}
    first = lineage_domain.create_lineage_resident(lineage, dict(payload), ACTOR)
    again = lineage_domain.create_lineage_resident(lineage, dict(payload), ACTOR)
    assert again["resident"]["resident_id"] == first["resident"]["resident_id"]
    assert [node["id"] for node in graph(lineage)["nodes"]] == [first["resident"]["resident_id"]]
    other = lineage_domain.create_lineage_resident(lineage, {**payload, "creation_key": "invocation-create:other"}, ACTOR)
    assert other["resident"]["resident_id"] != first["resident"]["resident_id"]


def test_get_resident_lists_every_bound_workspace(lineage, tmp_path):
    """GetResident 列出 Resident 绑定的每个 Workspace 且不重复"""
    from management_portal.society_service import get_society_service

    worker = resident(lineage, "Worker")
    development, extra = tmp_path / "agent", tmp_path / "extra"
    development.mkdir()
    extra.mkdir()
    ok("BindWorkspace", {"resident_id": worker, "path": str(development)})
    ok("BindWorkspace", {"resident_id": worker, "path": str(extra)})

    paths = [item["path"] for item in ok("GetResident", {"resident_id": worker})["resident"]["workspaces"]]
    assert str(development) in paths and str(extra.resolve()) in paths
    assert len(paths) == len(set(paths))


def test_resident_settings_read_back_in_the_shape_they_are_written(lineage):
    """GetResidentSettings 读回的设置与 UpdateResidentSettings 写入的结构一致"""
    worker = resident(lineage, "Settings")
    written = ok("UpdateResidentSettings", {"resident_id": worker, "changes": {"collaboration_mode": "plan"}})["settings"]
    read = ok("GetResidentSettings", {"resident_id": worker})
    assert read["settings"]["collaboration_mode"] == written["collaboration_mode"] == "plan"
    assert "runtime_settings" not in read["settings"]


def test_basic_resident_reads_do_not_ensure_agent(lineage, monkeypatch):
    """读取 Resident、对话、状态、队列和设置的指令不初始化 Agent 也不写 AgentData

    :module: lineage.service"""
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.society_service import resident_lifecycle_domain

    worker = resident(lineage, "Read only")
    monkeypatch.setattr(
        get_agent_data_store().database,
        "write",
        lambda _operation: pytest.fail("read instruction entered an AgentData write transaction"),
    )
    monkeypatch.setattr(
        resident_catalog, "ensure_resident_agent",
        lambda *_args, **_kwargs: pytest.fail("read instruction initialized an Agent"),
    )
    assert ok("GetResident", {"resident_id": worker})["resident"]["resident_id"] == worker
    assert ok("GetConversation", {"resident_id": worker})["messages"] == []
    assert ok("ListMessages", {"resident_id": worker, "cursor": "1"})["messages"] == []
    assert [item["resident_id"] for item in ok("ListResidentStates", {"resident_ids": [worker]})["states"]] == [worker]
    assert "pending" in ok("GetMailbox", {"resident_id": worker})
    assert "settings" in ok("GetResidentSettings", {"resident_id": worker})


def test_list_resident_states_returns_a_snapshot_for_each_visible_resident(lineage):
    """ListResidentStates 为每个可见 Resident 返回状态快照，可选择不读取查看者状态"""
    from unittest.mock import patch
    from management_portal.society_service import resident_workspace_domain

    first = resident(lineage, "First")
    second = resident(lineage, "Second")
    from management_portal.society_service import resident_protocol
    from tests.cases.isa_test_support import ACTOR
    controls = {rid: resident_protocol.get_resident_state(rid, ACTOR)["controls"] for rid in (first, second)}
    with patch.object(resident_record_domain, "resolve_resident_runtime_target",
                      side_effect=AssertionError("A state batch must not reload full runtime targets per Resident")):
        states = ok("ListResidentStates", {"resident_ids": [first, second, "resident_ffffffffffff"]})["states"]
    by_id = {item["resident_id"]: item for item in states}
    assert set(by_id) == {first, second}
    for item in by_id.values():
        assert isinstance(item["state"], dict) and "work_state" in item["state"]
        assert int(item["cursor"]) >= 0
        assert item["controls"] == controls[item["resident_id"]]
        assert isinstance(item["viewer_state"]["unread"], bool)

    # Runtime-only readers must not scan the Conversation index for hundreds
    # of members merely to paint a Lineage card.
    from management_portal import resident_view_projection
    with patch.object(resident_view_projection, "attach_viewer_state",
                      side_effect=AssertionError("runtime-only state read scanned Conversation")):
        runtime = ok("ListResidentStates", {
            "resident_ids": [first, second], "include_viewer_state": False,
        })["states"]
    assert {item["resident_id"] for item in runtime} == {first, second}
    assert all("viewer_state" not in item for item in runtime)


def test_a_shared_state_cell_can_be_read_with_only_some_of_its_fields(lineage):
    """GetSharedState 可以只读取共享状态值的部分字段

    GetSharedState 带 fields 时只返回对象值中列出的顶层字段；fields 需要与 key 一起使用；不存在的共享状态返回空状态。
    """
    ok("UpdateSharedState", {"lineage_id": lineage, "key": "guide", "value": {
        "members": [{"resident_id": "resident_aaaaaaaaaaaa"}], "routing_rules": ["登录交给 a"], "notes": "x" * 500,
    }}, key="guide")
    assert ok("GetSharedState", {"lineage_id": lineage, "key": "guide", "fields": ["members", "routing_rules", "absent"]})[
        "state"] == {"guide": {"members": [{"resident_id": "resident_aaaaaaaaaaaa"}], "routing_rules": ["登录交给 a"]}}
    ok("UpdateSharedState", {"lineage_id": lineage, "key": "count", "value": 3}, key="count")
    assert ok("GetSharedState", {"lineage_id": lineage, "key": "count", "fields": ["members"]})["state"] == {"count": 3}
    assert ok("GetSharedState", {"lineage_id": lineage, "key": "absent", "fields": ["members"]})["state"] == {}
    # One cell also comes bare, for an Effect script that cannot name a computed key in a path.
    assert ok("GetSharedState", {"lineage_id": lineage, "key": "guide", "fields": ["routing_rules"]})["value"] == {
        "routing_rules": ["登录交给 a"]}
    assert ok("GetSharedState", {"lineage_id": lineage, "key": "count"})["value"] == 3
    assert ok("GetSharedState", {"lineage_id": lineage, "key": "absent"})["value"] is None
    assert "value" not in ok("GetSharedState", {"lineage_id": lineage})
    whole = run("GetSharedState", {"lineage_id": lineage, "fields": ["members"]})
    assert whole.error["code"] == "instruction.invalid_arguments"


def test_shared_state_compare_and_set_can_claim_an_unset_cell_with_null(lineage):
    """UpdateSharedState 以 null 为期望值可抢占未设置的共享状态，只有一方成功"""
    claimed = ok("UpdateSharedState", {"lineage_id": lineage, "key": "owner", "expected_value": None, "value": "first"},
                 key="claim-1")
    assert claimed["matched"] is True
    lost = ok("UpdateSharedState", {"lineage_id": lineage, "key": "owner", "expected_value": None, "value": "second"},
              key="claim-2")
    assert lost["matched"] is False
    assert ok("GetSharedState", {"lineage_id": lineage, "key": "owner"})["state"] == {"owner": "first"}
    cleared = ok("UpdateSharedState", {"lineage_id": lineage, "key": "owner", "value": None}, key="clear")
    assert (cleared["previous_value"], cleared["value"], cleared["changed"]) == ("first", None, True)
    missing = run("UpdateSharedState", {"lineage_id": lineage, "key": "owner"}, key="missing")
    assert missing.error["code"] == "instruction.invalid_arguments"


def test_fragments_are_saved_through_update_lineage_under_the_definition_revision(lineage):
    """UpdateLineage 按定义版本保存 Lineage 碎片

    :design: decision_4637ca4a69e2
    """
    revision = graph(lineage)["revision"]
    saved = ok("UpdateLineage", {"lineage_id": lineage, "changes": {"fragments": [{"content": "- [ ] 第一件事"}]}},
               key="fragments-create", version=revision)
    [fragment] = saved["lineage"]["fragments"]
    assert fragment["content"] == "- [ ] 第一件事" and fragment["created_by"] == ACTOR
    current = graph(lineage)
    assert current["fragments"] == [fragment] and current["revision"] > revision
    # A page that read the old definition cannot overwrite the newer notes.
    stale = run("UpdateLineage", {"lineage_id": lineage, "changes": {"fragments": []}}, key="fragments-stale", version=revision)
    assert stale.error["code"] == "instruction.conflict"
    edited = ok("UpdateLineage", {"lineage_id": lineage, "changes": {"fragments": [{**fragment, "content": "- [x] 第一件事"}]}},
                key="fragments-edit", version=current["revision"])
    assert [(item["id"], item["content"]) for item in edited["lineage"]["fragments"]] == [(fragment["id"], "- [x] 第一件事")]
    assert ok("UpdateLineage", {"lineage_id": lineage, "changes": {"fragments": []}}, key="fragments-clear")["lineage"]["fragments"] == []
    invalid = run("UpdateLineage", {"lineage_id": lineage, "changes": {"fragments": "note"}}, key="fragments-invalid")
    assert invalid.error["code"] == "instruction.invalid_arguments"


def test_layout_is_saved_through_its_own_instruction_without_touching_the_definition(lineage):
    """UpdateLineageLayout 保存画布坐标，不推进定义版本

    :design: decision_4637ca4a69e2
    """
    member = resident(lineage, "Placed")
    before = graph(lineage)
    saved = ok("UpdateLineageLayout", {"lineage_id": lineage, "positions": [
        {"object_id": member, "x": 40, "y": 60}, {"object_id": "resident_000000000000", "x": 1, "y": 1},
    ]}, key="layout-move", version=before["revision"] - 1)
    assert [(item["object_id"], item["x"], item["y"]) for item in saved["applied"]] == [(member, 40, 60)]
    assert saved["missing_object_ids"] == ["resident_000000000000"]
    assert saved["layout_revision"] == int(before.get("layout_revision") or 0) + 1
    after = graph(lineage)
    node = next(item for item in after["nodes"] if item["id"] == member)
    assert (node["x"], node["y"], after["revision"]) == (40, 60, before["revision"])
    too_many = run("UpdateLineageLayout", {"lineage_id": lineage, "positions": [{"object_id": member, "x": 0, "y": 0}] * 513},
                   key="layout-too-many")
    assert too_many.error["code"] == "instruction.invalid_arguments"
