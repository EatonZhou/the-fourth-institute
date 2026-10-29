"""Instructions on one Lineage run concurrently and each keeps its own changes.

:importance: P1
"""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.society_service import lineage_domain as graphs
from management_portal.society_service import resident_catalog
from management_portal.society_service.errors import LineageConflictError
from management_portal.society_service import lineage_aggregate
from tests.cases.isa_test_support import ACTOR
from tests.cases.isa_test_support import run, reset_kernels
from tests.cases.test_principal_hierarchy import family
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload, CANONICAL_RESIDENT_TARGETS,  # noqa: F401
)


def hold_definition_writes(monkeypatch, lineage_id, parties):
    """Let every write read the Lineage before any of them commits."""

    barrier = threading.Barrier(parties)
    original = graphs._persist_lineage_state

    def synchronized(previous, lineage, reason, **kwargs):
        if previous is not None and lineage.get("lineage_id") == lineage_id:
            barrier.wait(timeout=20)
        return original(previous, lineage, reason, **kwargs)

    monkeypatch.setattr(graphs, "_persist_lineage_state", synchronized)


def test_definition_expected_version_compares_current_revision(future_creation_store):
    """定义修改的期望版本按当前 revision 比较，过期则报冲突，不带版本时直接合并

    :importance: P2"""
    reset_kernels()
    lineage_id = family("Versioned")["lineage_id"]
    revision = graphs.get_lineage(lineage_id, ACTOR)["revision"]
    arguments = {"lineage_id": lineage_id, "changes": {"title": "accepted"}}
    assert run("UpdateLineage", arguments, version=revision).status == "committed"
    refused = run("UpdateLineage", {**arguments, "changes": {"title": "stale"}}, version=revision)
    assert refused.error["code"] == "instruction.conflict"
    current = graphs.get_lineage(lineage_id, ACTOR)
    assert refused.error["details"]["current_version"] == current["revision"]
    assert current["title"] == "accepted"
    assert run("CreateComponent", {"lineage_id": lineage_id, "kind": "event_source",
        "config": {"event_type": "manual.triggered"}}, version={lineage_id: current["revision"]}).status == "committed"
    assert run("UpdateLineage", {**arguments, "changes": {"description": "merged"}}).status == "committed"


def test_concurrent_resident_creations_in_one_lineage_all_land(future_creation_store, monkeypatch):
    """同一 Lineage 中并发创建的 Resident 全部保留"""
    # Registration references must survive commit-time merge validation; a
    # synthetic resolver that accepts any Resident ID would hide that failure.
    monkeypatch.setattr(graphs, "_resident_targets", CANONICAL_RESIDENT_TARGETS)
    root = family("Team")
    lineage_id = root["lineage_id"]
    hold_definition_writes(monkeypatch, lineage_id, 4)

    def create(number):
        return graphs.create_lineage_resident(
            lineage_id, _new_session_payload(f"Member {number}"), ACTOR,
        )["resident"]["resident_id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(create, range(4)))
    current = graphs.get_lineage(lineage_id, ACTOR)
    assert {root["resident_id"], *created} == {node["id"] for node in current["nodes"]}
    assert current["leader_resident_id"] == root["resident_id"]


def test_concurrent_deletions_of_different_residents_all_land(future_creation_store, monkeypatch):
    """同一 Lineage 中并发删除不同 Resident 都生效"""
    root = family("Team")
    lineage_id = root["lineage_id"]
    members = [
        graphs.create_lineage_resident(lineage_id, _new_session_payload(f"Member {number}"), ACTOR)["resident"]["resident_id"]
        for number in range(3)
    ]
    # Deletion serializes identity and graph changes in one transaction. Wait
    # before entering it, never hold a transaction while waiting for peers.
    start = threading.Barrier(3)
    def delete(resident_id):
        start.wait(timeout=20)
        return graphs.delete_lineage_graph_objects(lineage_id, {"resident_ids": [resident_id]}, ACTOR)
    # Each write restamps the members it keeps; make every stamp distinct.
    ticks = iter(range(10_000))
    monkeypatch.setattr(graphs, "_now_iso", lambda: f"2026-10-02T00:{next(ticks) // 60 % 60:02d}:{next(ticks) % 60:02d}Z")
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(
            delete,
            members,
        ))
    assert {node["id"] for node in graphs.get_lineage(lineage_id, ACTOR)["nodes"]} == {root["resident_id"]}


def test_concurrent_metadata_edits_keep_both_fields(future_creation_store, monkeypatch):
    """并发修改 Lineage 的不同字段都保留"""
    lineage_id = family("Team")["lineage_id"]
    hold_definition_writes(monkeypatch, lineage_id, 2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda change: graphs.update_lineage_metadata(lineage_id, change, ACTOR), [
            {"title": "Renamed"}, {"description": "Described"},
        ]))
    current = graphs.get_lineage(lineage_id, ACTOR)
    assert (current["title"], current["description"]) == ("Renamed", "Described")


def test_changing_an_object_another_write_deleted_is_a_conflict(future_creation_store, monkeypatch):
    """修改已被另一次写入删除的节点时报冲突，被删节点不会复活"""
    root = family("Team")
    lineage_id = root["lineage_id"]
    member = graphs.create_lineage_resident(lineage_id, _new_session_payload("Member"), ACTOR)["resident"]
    stale = lineage_aggregate.read_lineage_state(lineage_id)
    graphs.delete_lineage_graph_objects(lineage_id, {"resident_ids": [member["resident_id"]]}, ACTOR)
    renamed = copy.deepcopy(stale)
    next(node for node in renamed["nodes"] if node["id"] == member["resident_id"])["title"] = "Renamed"
    with pytest.raises(LineageConflictError, match=member["resident_id"]):
        resident_catalog.get_catalog().state_store.transaction(
            lambda: graphs._persist_lineage_state(stale, renamed, "test rename of a deleted node"),
        )
    assert member["resident_id"] not in {node["id"] for node in graphs.get_lineage(lineage_id, ACTOR)["nodes"]}


def test_same_revision_allows_only_one_concurrent_commit(future_creation_store):
    """基于同一 revision 的两个并发提交只有一个成功，另一个报冲突并给出当前版本

    :importance: P2"""
    reset_kernels()
    lineage_id = family("Concurrent revision")["lineage_id"]
    revision = graphs.get_lineage(lineage_id, ACTOR)["revision"]
    barrier = threading.Barrier(2)
    def update(title):
        barrier.wait(timeout=10)
        return run("UpdateLineage", {"lineage_id": lineage_id, "changes": {"title": title}}, version=revision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["one", "two"]))
    assert sorted(r.status for r in results) == ["committed", "failed"]
    refused = next(r for r in results if r.status == "failed")
    assert refused.error["code"] == "instruction.conflict"
    assert refused.error["details"]["current_version"] == revision + 1


def test_move_invalid_selection_does_not_create_destination(future_creation_store):
    """MoveLineageObjects 选中无效对象时失败，不创建目标 Lineage

    :importance: P2"""
    lineage_id = family("Move selection")["lineage_id"]
    before = {item["lineage_id"] for item in lineage_aggregate.lineage_catalog_store().snapshot()}
    result = run("MoveLineageObjects", {"lineage_id": lineage_id, "object_ids": ["effect_123456789abc"]})
    assert result.status == "failed"
    assert {item["lineage_id"] for item in lineage_aggregate.lineage_catalog_store().snapshot()} == before


def test_merge_revalidates_new_wire_against_changed_target(future_creation_store):
    """合并时按组件最新端口重新校验新增连线，端口已被删除则报冲突

    :importance: P2"""
    root = family("Merge port validation")
    lineage_id = root["lineage_id"]
    effect = run("CreateComponent", {"lineage_id": lineage_id, "kind": "effect",
        "config": {"script": "output ok=true", "endpoints": ["worker"]}}).result["component_id"]
    base = lineage_aggregate.read_lineage_state(lineage_id)
    ours = copy.deepcopy(base)
    ours["program"]["wires"].append({"id": "wire_123456789abc", "kind": "target", "source": effect,
        "source_port": "worker", "target": root["resident_id"]})
    assert run("UpdateComponent", {"lineage_id": lineage_id, "component_id": effect,
        "changes": {"endpoints": []}}).status == "committed"
    with pytest.raises(LineageConflictError):
        resident_catalog.get_catalog().state_store.transaction(lambda: graphs._persist_lineage_state(base, ours, "stale wire"))
