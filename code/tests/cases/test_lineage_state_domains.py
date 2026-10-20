from __future__ import annotations

from tests.cases.society_service_test_support import (
    create_lineage_with_resident_members,
    install_resource_boundary,
)

from pathlib import Path

import pytest

from management_portal.society_service import lineage_domain, lineage_state_domains
from management_portal.society_service import lineage_definition_normalization


ACTOR = "sample_admin"
FIRST = "resident_111111111111"
SECOND = "resident_222222222222"


@pytest.fixture()
def lineage_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    root = tmp_path / "lineages"
    monkeypatch.setattr(lineage_domain, "DATA_ROOT", root)
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", root / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(root / "lineages"))
    install_resource_boundary(monkeypatch)
    return root


def _create() -> dict:
    return lineage_domain.create_lineage(
        {
            "title": "State domains",
            "project_id": "project_test",
            "nodes": [
                {
                    "id": FIRST,
                    "title": "First",
                    "x": 10,
                    "y": 20,
                },
                {
                    "id": SECOND,
                    "title": "Second",
                    "x": 30,
                    "y": 40,
                },
            ],
            "edges": [],
            "program": {
                "components": [
                    {
                        "id": "event_source_manual_a",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                        "x": 50,
                        "y": 60,
                    }
                ],
                "wires": [],
            },
        },
        ACTOR,
    )


def _node(lineage: dict, resident_id: str) -> dict:
    return next(
        item
        for item in lineage["nodes"]
        if item["id"] == resident_id
    )


def test_lineage_definition_and_runtime_are_persisted_separately_contracts(subtests, isolated_portal_case):
    """Lineage 定义与运行时分开持久化，节点运行状态里没有 paused"""
    with subtests.test(contract='test_lineage_definition_and_runtime_are_persisted_separately'), isolated_portal_case() as case_path:
        aggregate = {
            "schema": "lineage-folder-v3",
            "lineage_id": "lineage_111111111111",
            "nodes": [{
                "id": FIRST,
                "status": "running",
                "last_execution": {"job_id": "job_1"},
            }],
            "edges": [],
            "program": {
                "schema": "tfi.hatg-program.v2",
                "components": [{
                    "id": "seed_111111111111",
                    "kind": "seed",
                    "config": {"status": "active", "title": "Candidate"},
                }],
                "wires": [],
            },
            "runtime_revision": 7,
            "runtime_updated_at": "2026-08-03T00:00:00Z",
        }

        definition = lineage_state_domains.definition_projection(aggregate)
        runtime = lineage_state_domains.persisted_runtime_state(aggregate)
        merged = lineage_state_domains.merge_runtime_state(definition, runtime)

        assert "runtime_revision" not in definition
        assert definition["program"]["components"][0]["kind"] == "seed"
        assert "status" not in definition["nodes"][0]
        assert runtime["runtime_revision"] == 7
        assert runtime["nodes"][FIRST]["status"] == "running"
        assert merged["runtime_revision"] == 7
        assert merged["nodes"][0]["status"] == "running"
        assert set(runtime) <= lineage_state_domains.RUNTIME_STATE_FIELDS
    with subtests.test(contract='test_agent_node_runtime_status_vocabulary_has_no_pause'), isolated_portal_case() as case_path:
        assert lineage_definition_normalization.NODE_STATES == {
            "idle",
            "queued",
            "running",
            "blocked",
            "done",
        }
        assert lineage_domain._lineage_node_status("paused") == "idle"


def test_layout_updates_merge_by_object_without_graph_conflicts(
    lineage_store: Path,
) -> None:
    """布局更新按对象合并，不校验也不增加图 revision"""
    created = _create()
    graph_revision = created["revision"]

    first = lineage_domain.update_lineage_layout(
        created["lineage_id"],
        {
            "expected_revision": graph_revision - 1,
            "positions": [
                {"object_id": FIRST, "x": -120.125, "y": 88.875},
            ],
        },
        ACTOR,
    )
    second = lineage_domain.update_lineage_layout(
        created["lineage_id"],
        {
            "expected_revision": 999_999,
            "positions": [
                {"object_id": SECOND, "x": 81.5, "y": -44.5},
                {
                    "object_id": "event_source_manual_a",
                    "x": 72.25,
                    "y": 12.75,
                },
            ],
        },
        ACTOR,
    )
    current = lineage_domain.get_lineage(created["lineage_id"], ACTOR)

    assert first["revision"] == graph_revision
    assert second["revision"] == graph_revision
    assert second["layout_revision"] == 2
    assert (_node(current, FIRST)["x"], _node(current, FIRST)["y"]) == (
        -120.12,
        88.88,
    )
    assert (_node(current, SECOND)["x"], _node(current, SECOND)["y"]) == (
        81.5,
        -44.5,
    )
    component = current["program"]["components"][0]
    assert (component["x"], component["y"]) == (72.25, 12.75)


def test_same_object_layout_is_last_write_wins(
    lineage_store: Path,
) -> None:
    """同一对象的多次布局更新以最后一次写入为准"""
    created = _create()
    for x, y in ((25, 35), (75, 85)):
        lineage_domain.update_lineage_layout(
            created["lineage_id"],
            {
                "positions": [
                    {"object_id": FIRST, "x": x, "y": y},
                ]
            },
            ACTOR,
        )

    current = lineage_domain.get_lineage(created["lineage_id"], ACTOR)
    assert (_node(current, FIRST)["x"], _node(current, FIRST)["y"]) == (
        75.0,
        85.0,
    )
    assert current["revision"] == created["revision"]
    assert current["layout_revision"] == 2


def test_stale_graph_form_cannot_overwrite_layout_or_runtime(
    lineage_store: Path,
) -> None:
    """旧图表单保存时只改定义，不覆盖较新的布局坐标和节点运行状态"""
    created = _create()
    stale_form = lineage_domain._clone_json(created)
    lineage_domain.update_lineage_layout(
        created["lineage_id"],
        {
            "positions": [
                {"object_id": FIRST, "x": 91, "y": 92},
            ]
        },
        ACTOR,
    )
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution import execution_inputs
    agent = get_agent_data_store()
    agent.reserve_execution(execution_id="execution-live", agent_id=FIRST, actor_ref=ACTOR,
                            provider="codex", created_at="2026-10-11T00:00:00Z")
    agent.queue_execution("execution-live")
    execution_inputs.attach(agent, "execution-live", [
        {"message_id": "input-live", "role": "primary", "accepted_at": "2026-10-11T00:00:00Z"},
    ])

    stale_form["title"] = "Renamed from an old form"
    stale_form["nodes"][0]["x"] = -999
    stale_form["nodes"][0]["status"] = "idle"
    stale_form["nodes"][0]["last_execution"] = {}
    updated = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "lineage": stale_form,
        },
        ACTOR,
    )

    assert updated["title"] == "Renamed from an old form"
    assert updated["revision"] == created["revision"] + 1
    assert updated["layout_revision"] == 1
    assert updated["runtime_revision"] == 0
    assert (_node(updated, FIRST)["x"], _node(updated, FIRST)["y"]) == (
        91.0,
        92.0,
    )
    assert _node(updated, FIRST)["last_execution"]["message_id"] == "input-live"
    assert _node(updated, FIRST)["last_execution"]["status"] == "queued"


def test_layout_update_ignores_an_object_deleted_by_another_page(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """布局更新跳过已被其他页面删除的对象，并在 missing_object_ids 中列出"""
    created = create_lineage_with_resident_members({
        "title": "Concurrent identity deletion and layout",
        "project_id": "project_test",
        "nodes": [
            {"id": FIRST, "title": "First", "x": 10, "y": 20},
            {"id": SECOND, "title": "Second", "x": 30, "y": 40},
        ],
    }, ACTOR)
    store = lineage_domain._lineage_catalog_store().state_store

    def delete_identities(lineage_id, resident_ids, actor):
        # This layout race exercises the graph transaction; filesystem/session
        # cleanup belongs to the separately tested Resident lifecycle port.
        assert lineage_id == created["lineage_id"]
        assert resident_ids == [SECOND]
        assert actor == ACTOR
        tombstones = [{
            "resident_id": resident_id,
            "deleted_at": "2026-10-08T00:00:00Z",
            "deleted_by": actor,
        } for resident_id in resident_ids]
        store.remove_residents(tombstones)
        return tombstones

    monkeypatch.setattr(
        lineage_domain.resident_domain,
        "delete_resident_identities",
        delete_identities,
    )
    deleted = lineage_domain.delete_lineage_graph_objects(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "resident_ids": [SECOND],
        },
        ACTOR,
    )
    updated = deleted["lineage"]

    result = lineage_domain.update_lineage_layout(
        created["lineage_id"],
        {
            "positions": [
                {"object_id": SECOND, "x": 1, "y": 2},
                {"object_id": FIRST, "x": 3, "y": 4},
            ]
        },
        ACTOR,
    )

    assert result["revision"] == updated["revision"]
    assert result["missing_object_ids"] == [SECOND]
    assert [item["object_id"] for item in result["applied"]] == [FIRST]


def test_stale_graph_form_neither_erases_nor_resurrects_transient_absorption(
    lineage_store: Path,
) -> None:
    """旧图表单保存既不抹掉进行中的 absorption 边，也不恢复已完成的 absorption 边"""
    created = _create()
    stale_without_absorption = lineage_domain._clone_json(created)
    runtime = lineage_domain._clone_json(created)
    runtime["edges"].append({
        "id": "edge_absorption_runtime",
        "source": FIRST,
        "target": SECOND,
        "type": "absorption",
        "absorption": {
            "status": "queued",
            "target_message_id": "job_absorption",
        },
    })
    normalized_runtime = lineage_domain._normalize_lineage_payload(
        runtime,
        created,
        ACTOR,
    )
    lineage_domain._persist_lineage_runtime_state(
        created,
        normalized_runtime,
        "test absorption runtime",
    )

    stale_without_absorption["title"] = "Graph keeps live absorption"
    graph_with_absorption = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "lineage": stale_without_absorption,
        },
        ACTOR,
    )
    assert any(
        edge["id"] == "edge_absorption_runtime"
        for edge in graph_with_absorption["edges"]
    )

    stale_with_absorption = lineage_domain._clone_json(
        graph_with_absorption
    )
    completion_previous = lineage_domain.get_lineage(
        created["lineage_id"], ACTOR
    )
    completion = lineage_domain._clone_json(completion_previous)
    completion["edges"] = [
        edge
        for edge in completion.get("edges") or []
        if edge.get("id") != "edge_absorption_runtime"
    ]
    lineage_domain._persist_lineage_runtime_state(
        completion_previous,
        completion,
        "test authoritative absorption completion",
    )
    stale_with_absorption["title"] = "Graph cannot resurrect absorption"
    graph_after_completion = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": graph_with_absorption["revision"],
            "lineage": stale_with_absorption,
        },
        ACTOR,
    )
    assert not any(
        edge["type"] == "absorption"
        for edge in graph_after_completion["edges"]
    )


def test_stale_graph_form_preserves_handoff_preparation(
    lineage_store: Path,
) -> None:
    """旧图表单保存时保留 Handoff 已就绪的准备结果和目标 Workspace"""
    created = _create()
    graph_form = lineage_domain._clone_json(created)
    graph_form["edges"].append({
        "id": "handoff_state_domains",
        "source": FIRST,
        "target": SECOND,
        "type": "handoff",
        "items": [{
            "item_id": "workspace",
            "label": "Workspace",
            "source": {
                "workspace_id": "workspace_source",
                "path": "/tmp/source",
                "scope": ".",
            },
            "transfer": {
                "mode": "direct",
                "method": "direct",
            },
            "target": {
                "path": "/tmp/source",
                "scope": ".",
            },
        }],
        "execution": {
            "enabled": True,
            "prompt": "Continue",
        },
    })
    graph = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "lineage": graph_form,
        },
        ACTOR,
    )
    stale_graph = lineage_domain._clone_json(graph)

    runtime = lineage_domain._clone_json(graph)
    handoff = next(
        edge
        for edge in runtime["edges"]
        if edge["id"] == "handoff_state_domains"
    )
    handoff.update({
        "status": "ready",
        "error": "",
        "ready_at": "2026-07-30T12:00:00Z",
    })
    handoff["items"][0].update({
        "attempts": [{
            "number": 1,
            "status": "ready",
            "method": "direct",
        }],
        "result": {
            "target_workspace_id": "workspace_target",
            "target_path": "/tmp/ready",
            "target_scope": ".",
        },
    })
    handoff["items"][0]["target"].update({
        "workspace_id": "workspace_target",
        "path": "/tmp/ready",
    })
    normalized_runtime = lineage_domain._normalize_lineage_payload(
        runtime,
        graph,
        ACTOR,
    )
    lineage_domain._persist_lineage_runtime_state(
        graph,
        normalized_runtime,
        "test handoff runtime",
    )

    stale_graph["title"] = "Graph edit during Handoff"
    updated = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": graph["revision"],
            "lineage": stale_graph,
        },
        ACTOR,
    )
    saved_handoff = next(
        edge
        for edge in updated["edges"]
        if edge["id"] == "handoff_state_domains"
    )
    assert saved_handoff["status"] == "ready"
    assert "execution" not in saved_handoff
    assert saved_handoff["items"][0]["attempts"][-1]["status"] == "ready"
    assert (
        saved_handoff["items"][0]["result"]["target_workspace_id"]
        == "workspace_target"
    )
    assert saved_handoff["items"][0]["target"]["path"] == "/tmp/ready"
