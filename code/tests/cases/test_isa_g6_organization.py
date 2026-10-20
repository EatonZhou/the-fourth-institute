"""Regression evidence for ISA audit G6's organization and deletion rules.

:module: spec.design-governance
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import (
    lineage_domain as graphs, principal_hierarchy_domain,
    project_domain, resident_catalog, resident_registration_domain, resource_client,
)
from tests.cases.isa_test_support import ACTOR, ok, reset_kernels
from tests.cases.test_hatg_move_resident import teams, request, execute, graph  # noqa: F401
from tests.cases.test_principal_hierarchy import family, make_project_graph
from tests.cases.test_project_deletion import _configure_project
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload,  # noqa: F401
)


@pytest.mark.parametrize("failure", [ValueError("busy"), PermissionError("disk permission")])
def test_partial_project_deletion_has_failed_code_and_progress(monkeypatch, tmp_path, failure):
    """删除 Project 中途失败时返回失败错误码并列出已删除的 Lineage"""
    first, second = "lineage_aaaaaaaaaaaa", "lineage_bbbbbbbbbbbb"
    root, _, _, deleted = _configure_project(monkeypatch, tmp_path, lineages=([first, second], []))
    original = graphs.delete_lineage

    def delete(identifier, actor, **options):
        if identifier == second:
            raise failure
        return original(identifier, actor, **options)

    monkeypatch.setattr(graphs, "delete_lineage", delete)
    with pytest.raises(InstructionError) as error:
        project_domain.delete_project("Example", "alice")
    assert error.value.code == "instruction.failed"
    assert error.value.details["deleted_lineage_ids"] == [first]
    assert deleted == [first] and root.exists()


def test_ensure_project_principal_serializes_concurrent_creation(future_creation_store, monkeypatch):
    """并发确保 Project 主 Resident 时只创建一个，且它以自有 Workspace 为默认工作目录"""

    project_id = "ConcurrentPrincipal"
    records, calls = [], []
    started = Barrier(2)
    concurrent_registration = Event()
    original_register = resident_registration_domain._register_resident
    project_root = future_creation_store.parent / project_id
    project_root.mkdir()
    original_project_root = graphs.resource_port.project_root
    monkeypatch.setattr(graphs.resource_port, "project_root", lambda identifier, *, actor:
                        project_root if identifier == project_id else original_project_root(identifier, actor=actor))
    monkeypatch.setattr(resource_client, "_query_all", lambda *args, **kwargs: [{"resource_id": project_id}])

    def register(payload, actor, **kwargs):
        selected = payload.get("project_id") == project_id and kwargs.get("project_principal")
        if selected:
            calls.append(payload)
            if len(calls) > 1:
                concurrent_registration.set()
            concurrent_registration.wait(0.2)
        record, item = original_register(payload, actor, **kwargs)
        if selected:
            records.append(item)
        return record, item

    monkeypatch.setattr(resident_registration_domain, "_register_resident", register)

    def ensure():
        started.wait()
        return principal_hierarchy_domain.ensure_project(project_id, ACTOR)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: ensure(), range(2)))
    assert results[0]["resident_id"] == results[1]["resident_id"]
    assert len(records) == len(calls) == 1
    assert sorted(item["created"] for item in results) == [False, True]
    canonical = resident_catalog.get_catalog().get(results[0]["resident_id"])
    assert canonical["project_id"] == project_id
    assert canonical["lineage_id"] == results[0]["lineage_id"]
    # The principal owns its node Workspace, which is its default cwd.
    assert canonical["owned_workspace"]["workspace_id"] == canonical["owned_workspace_id"]
    assert resident_catalog.default_cwd_path(canonical) == canonical["owned_workspace"]["path"]


def test_allow_empty_source_is_permission_for_an_ordinary_member(teams):
    """迁移普通成员时传 allow_empty_source 也能成功，源 Leader 不变"""
    call = request(teams)
    call["arguments"]["allow_empty_source"] = True
    result = execute(call)
    assert result.status == "committed", result.error
    assert graph(teams[0])["leader_resident_id"] == teams[0]["resident_id"]


@pytest.mark.parametrize("project_id", ["", "project_test"])
def test_personal_lineage_auto_move_preserves_membership(future_creation_store, project_id):
    """从个人 Lineage 迁出对象时自动新建的 Lineage 保持个人归属和原 Project"""
    reset_kernels()
    source = graphs.create_lineage({"title": "Personal", "lineage_membership": "personal", "project_id": project_id}, ACTOR)
    source = graphs.create_lineage_program_component(source["lineage_id"], {
        "kind": "event_source", "config": {"event_type": "manual.triggered"},
        "expected_revision": source["revision"],
    }, ACTOR)["lineage"]
    component = source["program"]["components"][0]["id"]
    moved = ok("MoveLineageObjects", {"lineage_id": source["lineage_id"], "object_ids": [component]})
    target = graphs.get_lineage(moved["target_lineage_id"], ACTOR)
    assert target["lineage_membership"] == "personal"
    assert target["project_id"] == project_id
    assert target["title"] == "Personal · 迁出"
    assert target["program"]["components"][0]["id"] == component


def project_with_child():
    project, child = family("Project"), family("Child")
    make_project_graph(project["lineage_id"])
    return project, child




@pytest.mark.parametrize("action", ["move", "delete"])
def test_project_leader_transition_chooses_own_member(future_creation_store, monkeypatch, action):
    """迁走或删除 Project 层 Leader 时从本 Lineage 自己的成员中选新 Leader"""
    from management_portal.society_service import lineage_subgraph

    project = family("Project")
    remaining = graphs.create_lineage_resident(project["lineage_id"], _new_session_payload("Own successor"), ACTOR)["resident"]
    family("Child")
    make_project_graph(project["lineage_id"])
    target = family("Target")
    # A deterministic adversarial choice makes reference candidates fail every time.
    monkeypatch.setattr(lineage_subgraph.secrets, "choice", lambda candidates: candidates[-1])
    if action == "move":
        result = graphs.move_lineage_subgraph(project["lineage_id"], {
            "object_ids": [project["resident_id"]], "target_lineage_id": target["lineage_id"],
        }, ACTOR)
        assert result["resident_ids"] == [project["resident_id"]]
    else:
        graphs.delete_lineage_graph_objects(project["lineage_id"], {"resident_ids": [project["resident_id"]]}, ACTOR)
    assert graphs.get_lineage(project["lineage_id"], ACTOR)["leader_resident_id"] == remaining["resident_id"]


def test_project_leader_move_without_own_successor_rolls_back(future_creation_store):
    """Project 层 Leader 没有本 Lineage 的接任者时迁移失败并回滚"""
    from management_portal.society_service import resident_catalog

    project, _ = project_with_child()
    target = family("Target")
    original = graphs.get_lineage(project["lineage_id"], ACTOR)
    with pytest.raises(ValueError, match="Project leader"):
        graphs.move_lineage_subgraph(project["lineage_id"], {
            "object_ids": [project["resident_id"]], "target_lineage_id": target["lineage_id"],
        }, ACTOR)
    assert resident_catalog.get_catalog().get(project["resident_id"])["lineage_id"] == project["lineage_id"]
    assert graphs.get_lineage(project["lineage_id"], ACTOR) == original


def test_the_only_resident_of_a_project_lineage_is_its_leader_and_stays(future_creation_store):
    """Project 层 Lineage 唯一的 Resident 是 Leader，不能被删除"""
    project, _ = project_with_child()
    original = graphs.get_lineage(project["lineage_id"], ACTOR)
    assert original["leader_resident_id"] == project["resident_id"]
    with pytest.raises(ValueError, match="Project 层 Lineage 必须有 Leader"):
        graphs.delete_lineage_graph_objects(project["lineage_id"], {"resident_ids": [project["resident_id"]]}, ACTOR)
    assert graphs.get_lineage(project["lineage_id"], ACTOR) == original






def test_retired_duty_instructions_are_not_in_the_catalog():
    """已退役的 Duty 指令不在 ISA 目录中"""
    from management_portal.hatg.catalog import instruction_catalog
    assert not {"GetDutyBindings", "UpdateDutyBindings", "ResolveDuty"} & set(instruction_catalog())
