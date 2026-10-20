from __future__ import annotations
from management_portal.society_service import business_fact_views

from tests.cases import society_service_test_support as lineage_test_support
from tests.cases.society_service_test_support import (
    install_resource_boundary,
    write_lineage_facts,
)

import copy
import json
import threading
from pathlib import Path

import pytest

from management_portal.society_service import (
    lineage_domain,
    lineage_read_cache,
    project_domain,
    resident_catalog,
    resident_conversation_stream,
    resident_directory_domain,
    resident_execution_domain,
    resident_fork_domain,
    resident_protocol,
    resource_client,
)
from management_portal.society_service.domain_ports import resident_terminal_port
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from management_portal.resource_refs import extract_resource_addresses


from management_portal.society_service import resident_workspace_domain

@pytest.fixture()
def lineage_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "lineages"
    monkeypatch.setattr(lineage_domain, "DATA_ROOT", root)
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", root / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(root / "lineages"))
    install_resource_boundary(monkeypatch)
    monkeypatch.setattr(resident_workspace_domain, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_TARGET_RESOLVER",
        lambda resident_ids: {
            resident_id: {
                "resident_id": resident_id,
                "lineage_id": "",
            }
            for resident_id in resident_ids
        },
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda _node, _actor: False,
    )
    return root


def test_project_root_reads_only_the_requested_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读取 Project 根目录时只按请求者身份查询该 Project 一项"""
    project_path = tmp_path / "Project"
    calls: list[tuple[str, str]] = []

    class ResourceService:
        def get(self, resource_id: str, *, actor: str = "") -> dict:
            calls.append((resource_id, actor))
            return {
                "resource_id": resource_id,
                "resource_type": "project",
                "attributes": {"path": str(project_path)},
            }

    monkeypatch.setattr(
        resource_client,
        "get_resource_service",
        lambda: ResourceService(),
    )

    assert resource_client.project_root(
        "project_direct",
        actor="project-reader",
    ) == project_path.resolve()
    assert calls == [("project_direct", "project-reader")]


def sample_lineage_payload() -> dict:
    return {
        "title": "HATG 交互探索",
        "description": "验证人类主导的多 Agent 工作流",
        "nodes": [
            {
                "id": "resident_0123456789ab",
                "title": "研究 Agent",
                "x": 20,
                "y": 35,
            },
            {
                "id": "resident_222222222222",
                "title": "设计 Agent",
                "x": 70,
                "y": 55,
            },
        ],
        "edges": [
            {
                "id": "edge_handoff",
                "source": "resident_0123456789ab",
                "target": "resident_222222222222",
                "type": "handoff",
                "label": "研究结论交接",
                "contract": {
                    "artifacts": ["research/summary.md"],
                    "acceptance": "包含证据和未决问题",
                },
            }
        ],
        "leader_resident_id": "resident_0123456789ab",
    }


def test_owner_can_create_more_than_forty_lineages(
    lineage_store: Path,
) -> None:
    """同一用户可以创建超过 40 个 Lineage"""
    created = [
        lineage_domain.create_lineage(
            {"title": f"Unlimited Lineage {index}", "nodes": [], "edges": []},
            "sample_admin",
        )
        for index in range(41)
    ]

    assert len({item["lineage_id"] for item in created}) == 41


@pytest.mark.parametrize("warm", [False, True])
def test_lineage_view_reads_canonical_state_and_view_once(
    lineage_store: Path, monkeypatch: pytest.MonkeyPatch, warm: bool,
) -> None:
    """读取 Lineage 视图时规范状态和业务事实视图各只读一次，缓存冷热都一样"""
    lineage = lineage_domain.create_lineage({"title": "One read snapshot", "nodes": [], "edges": []}, "sample_admin")
    lineage_id = lineage["lineage_id"]
    lineage_domain.invalidate_lineage_read_cache(lineage_id)
    if warm:
        lineage_domain.get_lineage(lineage_id, "sample_admin")
    counts = {"state": 0, "view": 0}
    read_state = lineage_domain._read_lineage_state
    canonical_view = business_fact_views.canonical_view

    def counted_state(*args, **kwargs):
        counts["state"] += 1
        return read_state(*args, **kwargs)

    def counted_view(*args, **kwargs):
        counts["view"] += 1
        return canonical_view(*args, **kwargs)

    monkeypatch.setattr(lineage_domain, "_read_lineage_state", counted_state)
    monkeypatch.setattr(business_fact_views, "canonical_view", counted_view)
    assert lineage_domain.get_lineage(lineage_id, "sample_admin")["lineage_id"] == lineage_id
    assert counts == {"state": 1, "view": 1}


def test_owner_cannot_move_lineage_back_to_no_project(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 移入 Project 后目录随之迁移，不能再移回无 Project 状态"""
    project_root = lineage_store / "projects" / "RobotCompany"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(
        project_domain.project_registry,
        "load",
        lambda: {"projects": [{"id": "project_robotcompany", "path": str(project_root)}]},
    )
    monkeypatch.setattr(
        resource_client,
        "project_roots",
        lambda **_kwargs: {"project_robotcompany": project_root},
    )
    projected_projects: list[str] = []
    sync_lineage = lineage_domain._sync_lineage_resource_identity

    def capture_project(lineage: dict) -> None:
        projected_projects.append(str(lineage.get("project_id") or ""))
        sync_lineage(lineage)

    monkeypatch.setattr(
        lineage_domain,
        "_sync_lineage_resource_identity",
        capture_project,
    )

    created = lineage_domain.create_lineage(
        {"title": "Project relocation", "nodes": [], "edges": []},
        "sample_admin",
    )
    lineage_id = created["lineage_id"]
    canonical = lineage_domain._lineage_dir(lineage_id)
    marker = canonical / "LINEAGE.md"
    marker.write_text("keep me\n", encoding="utf-8")

    moved = lineage_domain.update_lineage_metadata(
        lineage_id,
        {
            "expected_revision": created["revision"],
            "project_id": "project_robotcompany",
        },
        "sample_admin",
    )
    project_folder = (
        project_root / "knowledge_base" / "agents" / lineage_id
    )

    assert moved["project_id"] == "project_robotcompany"
    assert canonical.is_symlink()
    assert canonical.resolve() == project_folder.resolve()
    assert marker.read_text(encoding="utf-8") == "keep me\n"
    assert lineage_domain.list_lineages("sample_admin")["lineages"][0][
        "project_id"
    ] == "project_robotcompany"
    assert projected_projects[-1] == "project_robotcompany"

    with pytest.raises(ValueError, match="必须属于 Project"):
        lineage_domain.update_lineage_metadata(
            lineage_id,
            {
                "expected_revision": moved["revision"],
                "project_id": "",
            },
            "sample_admin",
        )

    assert canonical.is_symlink()
    assert canonical.resolve() == project_folder.resolve()
    assert project_folder.exists()
    assert marker.read_text(encoding="utf-8") == "keep me\n"
    assert projected_projects[-1] == "project_robotcompany"


def test_lineage_project_move_rejects_active_resident_execution_work(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 中仍有 Resident 任务排队或处理时，拒绝把它移到其他 Project"""
    project_root = lineage_store / "projects" / "RobotCompany"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(
        resource_client,
        "project_roots",
        lambda **_kwargs: {"project_robotcompany": project_root},
    )
    monkeypatch.setattr(
        lineage_domain,
        "_lineage_relocation_blockers",
        lambda _lineage_id: ["resident_0123456789ab"],
    )
    created = lineage_domain.create_lineage(
        {"title": "Busy project relocation", "nodes": [], "edges": []},
        "sample_admin",
    )
    canonical = lineage_domain._lineage_dir(created["lineage_id"])

    with pytest.raises(ValueError, match="仍有任务正在排队或处理"):
        lineage_domain.update_lineage_metadata(
            created["lineage_id"],
            {
                "expected_revision": created["revision"],
                "project_id": "project_robotcompany",
            },
            "sample_admin",
        )

    assert canonical.is_dir()
    assert not canonical.is_symlink()
    assert lineage_domain.get_lineage(
        created["lineage_id"],
        "sample_admin",
    )["project_id"] == "project_test"


def test_create_lineage_in_project_binds_canonical_entry(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在 Project 中创建的 Lineage，其目录链接到该 Project 的 agents 目录下"""
    project_root = lineage_store / "projects" / "RobotCompany"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(
        project_domain.project_registry,
        "load",
        lambda: {"projects": [{"id": "project_robotcompany", "path": str(project_root)}]},
    )
    monkeypatch.setattr(
        resource_client,
        "project_roots",
        lambda **_kwargs: {"project_robotcompany": project_root},
    )

    created = lineage_domain.create_lineage(
        {
            "title": "Project-native Lineage",
            "project_id": "project_robotcompany",
            "nodes": [],
            "edges": [],
        },
        "sample_admin",
    )
    canonical = lineage_domain._lineage_dir(created["lineage_id"])
    expected = (
        project_root
        / "knowledge_base"
        / "agents"
        / created["lineage_id"]
    )

    assert created["project_id"] == "project_robotcompany"
    assert canonical.is_symlink()
    assert canonical.resolve() == expected.resolve()
    assert lineage_domain._lineage_catalog_store().get(created["lineage_id"])


def test_owner_reconcile_skips_a_lineage_deleted_before_notification(
    lineage_store: Path,
) -> None:
    """对账通知到达前 Lineage 已不存在时，服务对账返回 missing"""
    del lineage_store

    assert lineage_domain.reconcile_lineage_system_events_as_service(
        "lineage_123456789abc"
    ) == {
        "lineage_id": "lineage_123456789abc",
        "status": "missing",
        "checked": 0,
    }


def test_owner_reconcile_skips_a_lineage_deleted_after_catalog_read(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对账期间 Lineage 被删除时，服务对账返回 missing 而不报错"""
    del lineage_store
    created = lineage_domain.create_lineage(
        {
            **sample_lineage_payload(),
            "title": "Delete race",
            "run_as_principal": {"kind": "user", "id": "sample_admin"},
            "program": {"components": [{"id": "event_source_delete_race", "kind": "event_source",
                         "config": {"event_type": "time.reached", "schedule_mode": "countdown", "delay_seconds": 60}}], "wires": []},
        },
        "sample_admin",
    )
    lineage_id = created["lineage_id"]

    def delete_during_reconcile(_lineage_id: str, _actor: str) -> dict:
        lineage_domain._lineage_catalog_store().remove(lineage_id, deleted_by="sample_admin")
        raise KeyError("Lineage not found")

    monkeypatch.setattr(
        lineage_domain,
        "reconcile_lineage_system_events",
        delete_during_reconcile,
    )

    assert lineage_domain.reconcile_lineage_system_events_as_service(
        lineage_id
    ) == {
        "lineage_id": lineage_id,
        "status": "missing",
        "checked": 0,
    }


def test_scheduler_uses_service_without_configured_resident(lineage_store, monkeypatch):
    """无自动事件源的 Lineage 不观测，定时器未配置运行身份时以 portal 身份观测

    Neither a Resident executor nor a User is required to observe a timer."""
    created = lineage_domain.create_lineage({"title": "Service poll", "nodes": [], "edges": []}, "sample_admin")
    calls = []
    monkeypatch.setattr(lineage_domain, "reconcile_lineage_system_events",
                        lambda lid, actor, **kw: calls.append((lid, actor)) or {"event_count": 0})
    lineage_domain.reconcile_lineage_system_events_as_service(created["lineage_id"])
    assert calls == []  # A graph without automatic sources has nothing to observe.
    created = lineage_domain.create_lineage({"title": "Automatic poll", "program": {"components": [
        {"id": "event_source_poll", "kind": "event_source",
         "config": {"event_type": "time.reached", "schedule_mode": "countdown", "delay_seconds": 60}},
    ], "wires": []}}, "sample_admin")
    result = lineage_domain.reconcile_lineage_system_events_as_service(created["lineage_id"])
    assert result["event_count"] == 0
    assert calls == [(created["lineage_id"], "portal")]
    lineage_domain.update_lineage_metadata(created["lineage_id"], {
        "run_as_principal": {"kind": "user", "id": "sample_admin"},
    }, "sample_admin")
    lineage_domain.reconcile_lineage_system_events_as_service(created["lineage_id"])
    assert calls == [(created["lineage_id"], "portal"), (created["lineage_id"], "sample_admin")]


def test_configured_automation_still_checks_principal(lineage_store, monkeypatch):
    """配置了 run_as_principal 的 Lineage 对账时仍检查运行身份是否获准"""
    created = lineage_domain.create_lineage(
        {**sample_lineage_payload(), "run_as_principal": {"kind": "user", "id": "sample_admin"},
         "program": {"components": [{"id": "event_source_principal", "kind": "event_source",
                      "config": {"event_type": "time.reached", "schedule_mode": "countdown", "delay_seconds": 60}}], "wires": []}},
        "sample_admin",
    )

    def denied(_lineage):
        raise PermissionError("Configured automation principal is not authorized")

    monkeypatch.setattr(business_fact_views, "canonical_views", lambda records, **kwargs: list(records))
    monkeypatch.setattr(lineage_domain, "_lineage_runtime_actor", denied)
    with pytest.raises(PermissionError, match="not authorized"):
        lineage_domain.reconcile_lineage_system_events_as_service(created["lineage_id"])


def test_lineage_topology_can_exceed_previous_node_and_edge_caps(
    lineage_store: Path,
) -> None:
    """Lineage 节点数和边数不受旧上限限制，65 个节点或 190 条边都能创建"""
    node_ids = [f"resident_{index:012x}" for index in range(1, 66)]
    nodes = [
        {
            "id": resident_id,
            "title": f"Resident {index}",
        }
        for index, resident_id in enumerate(node_ids, start=1)
    ]
    node_heavy = lineage_domain.create_lineage(
        {"title": "Unbounded nodes", "nodes": nodes, "edges": []},
        "sample_admin",
    )

    assert len(node_heavy["nodes"]) == 65

    edge_node_ids = [f"resident_{index:012x}" for index in range(100, 120)]
    edge_nodes = [{"id": rid, "title": rid} for rid in edge_node_ids]
    edges = [
        {
            "id": f"edge_unbounded_{index}",
            "source": source,
            "target": target,
            "type": "handoff",
        }
        for index, (source, target) in enumerate(
            (
                (source, target)
                for source_index, source in enumerate(edge_node_ids)
                for target in edge_node_ids[source_index + 1 :]
            ),
            start=1,
        )
    ]
    edge_heavy = lineage_domain.create_lineage(
        {
            "title": "Unbounded edges",
            "nodes": edge_nodes,
            "edges": edges,
        },
        "sample_admin",
    )

    assert len(edge_heavy["edges"]) == 190


def test_lineage_authoring_collections_have_no_aggregate_size_caps(
    lineage_store: Path,
) -> None:
    """Lineage 的 Seed 组件和片段数量没有总数上限"""
    del lineage_store
    source_id = "resident_aaaaaaaaaaaa"
    seed_count = 257
    fragment_count = 161
    lineage = lineage_domain.create_lineage(
        {
            "title": "Unbounded authoring collections",
            "nodes": [{
                "id": source_id,
                "title": "Source Resident",
            }],
            "edges": [],
            "program": {
                "schema": "tfi.lineage-program.v2",
                "components": [
                    {
                        "id": f"seed_unbounded_{index:03d}",
                        "kind": "seed",
                        "title": f"Seed {index}",
                        "config": {
                            "source_resident_id": source_id,
                            "artifact_path": f"seeds/seed-{index:03d}.md",
                        },
                    }
                    for index in range(seed_count)
                ],
                "wires": [],
            },
            "fragments": [
                {
                    "id": f"fragment_unbounded_{index:03d}",
                    "content": f"Fragment {index}",
                }
                for index in range(fragment_count)
            ],
        },
        "sample_admin",
    )

    assert len(lineage["program"]["components"]) == seed_count
    assert len(lineage["fragments"]) == fragment_count


def test_lineage_refresh_and_absorption_contracts(subtests, isolated_portal_case):
    """Lineage 刷新签名忽略运行时投影变化，吸收边只保留投递状态和两端 Resident"""
    with subtests.test(contract='test_lineage_refresh_signature_ignores_runtime_projection_changes'), isolated_portal_case() as case_path:
        lineage = {
            "lineage_id": "lineage_0123456789ab",
            "revision": 7,
            "layout_revision": 3,
            "runtime_revision": 11,
            "nodes": [{
                "id": "resident_0123456789ab",
                "title": "Worker",
                "status": "idle",
                "x": 20,
                "y": 30,
            }],
            "edges": [],
            "program": {"components": [], "wires": []},
            "program_invoke_wire_runtime": {"wire_1": {"pending_count": 1}},
            "program_execution_explanations": {"wire_1": {"state": "queued"}},
        }
        baseline = lineage_read_cache.definition_refresh_signature(lineage)

        runtime_changed = copy.deepcopy(lineage)
        runtime_changed["runtime_revision"] = 12
        runtime_changed["nodes"][0]["status"] = "running"
        runtime_changed["program_invoke_wire_runtime"]["wire_1"]["pending_count"] = 9
        runtime_changed["program_execution_explanations"]["wire_1"]["state"] = "running"
        assert lineage_read_cache.definition_refresh_signature(runtime_changed) == baseline

        definition_changed = copy.deepcopy(lineage)
        definition_changed["nodes"][0]["title"] = "Renamed worker"
        assert lineage_read_cache.definition_refresh_signature(definition_changed) != baseline
    with subtests.test(contract='test_absorption_edges_only_keep_prompt_delivery_state'), isolated_portal_case() as case_path:
        normalized = lineage_domain._normalize_edge(
            {
                "id": "edge_absorb",
                "source": "source",
                "target": "target",
                "type": "absorption",
                "absorption": {
                    "status": "queued",
                    "source_resident_id": "resident_0123456789ab",
                    "target_resident_id": "resident_222222222222",
                    "access_mode": "agent_pull",
                    "context_mode": "full_reference",
                    "lookup_command": "obsolete command",
                },
            },
            {"source", "target"},
        )
        assert normalized["absorption"]["status"] == "queued"
        assert normalized["absorption"]["source_resident_id"] == (
            "resident_0123456789ab"
        )
        assert normalized["absorption"]["target_resident_id"] == (
            "resident_222222222222"
        )
        assert "access_mode" not in normalized["absorption"]
        assert "context_mode" not in normalized["absorption"]
        assert "lookup_command" not in normalized["absorption"]


def node_owned_workspace(lineage: dict, resident_id: str) -> Path:
    node = next(item for item in lineage["nodes"] if item["id"] == resident_id)
    resident_id = str(node.get("id") or "")
    return (
        lineage_domain._lineage_dir(lineage["lineage_id"])
        / "residents"
        / resident_id
        / "workspace"
    )


def test_lineage_lifecycle_revision_and_recoverable_delete(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 每次更新 revision 递增且不要求版本前置条件，删除后移除目录并记录删除时间"""
    synced: list[dict] = []
    original_sync = lineage_domain._sync_lineage_resource_identity

    def capture_sync(lineage: dict) -> None:
        synced.append(dict(lineage))
        original_sync(lineage)

    monkeypatch.setattr(
        lineage_domain,
        "_sync_lineage_resource_identity",
        capture_sync,
    )
    created = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")

    assert created["lineage_id"].startswith("lineage_")
    assert created["revision"] == 1
    assert "owner_ids" not in created
    assert lineage_domain._lineage_dir(created["lineage_id"]).is_dir()
    assert lineage_domain.list_lineages("sample_admin")["lineages"][0]["node_count"] == 2

    updated = lineage_domain.update_lineage(
        created["lineage_id"],
        {"lineage": {**created, "description": "已更新说明"}},
        "sample_admin",
    )
    assert updated["revision"] == 2
    assert updated["description"] == "已更新说明"

    # A Lineage has no version precondition: a body built from an older
    # read still commits, and the change counter keeps advancing.
    renamed = lineage_domain.update_lineage(
        created["lineage_id"],
        {"lineage": {**updated, "title": "改名后"}},
        "sample_admin",
    )
    assert (renamed["revision"], renamed["title"]) == (3, "改名后")

    result = lineage_domain.delete_lineage(created["lineage_id"], "sample_admin")
    # Deletion removes the Lineage folder; nothing stays in the deleted root.
    assert "recoverable_path" not in result
    assert not any(lineage_domain._deleted_root().glob(f"{created['lineage_id']}_*"))
    assert lineage_domain.list_lineages("sample_admin")["lineages"] == []
    assert synced[-1]["lineage_id"] == created["lineage_id"]
    assert result["deleted_at"]
    with lineage_domain._lineage_catalog_store().state_store.database.read() as connection:
        assert connection.execute("SELECT deleted_at FROM lineages WHERE lineage_id=%s", (created["lineage_id"],)).fetchone()["deleted_at"]


def test_empty_lineage_delete_preserves_retryable_intent_when_cleanup_fails(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空 Lineage 原位清理失败时目录保留，删除意图可重试且不移动存活路径"""
    from management_portal.society_service import resident_workspace_retirement

    created = lineage_domain.create_lineage(
        {"title": "Cleanup failure", "nodes": [], "edges": []},
        "sample_admin",
    )
    synced: list[dict] = []
    original_sync = lineage_domain._sync_lineage_resource_identity

    def capture_sync(lineage: dict) -> None:
        synced.append(dict(lineage))
        original_sync(lineage)

    monkeypatch.setattr(
        lineage_domain,
        "_sync_lineage_resource_identity",
        capture_sync,
    )
    monkeypatch.setattr(
        resident_workspace_retirement,
        "remove_retired_folder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup failed")),
    )

    with pytest.raises(OSError, match="cleanup failed"):
        lineage_domain.delete_lineage(created["lineage_id"], "sample_admin")

    assert lineage_domain._lineage_dir(created["lineage_id"]).is_dir()
    assert len(synced) == 1
    assert synced[0]["deleted_at"]
    intent = lineage_domain._lineage_deletion_intent(created["lineage_id"])
    assert intent["phase"] == "retryable"
    assert intent["operation_id"].startswith("lineage-delete_")


def test_lineage_list_includes_every_owned_lineage(
    lineage_store: Path,
) -> None:
    """Lineage 列表包含所有者的正式和个人 Lineage，个人 Lineage 其他用户看不到"""
    formal = lineage_domain.create_lineage(
        {"title": "Formal HATG"},
        "sample_admin",
    )
    with lineage_domain._lineage_scope("lineage_aaaaaaaaaaaa"):
        personal = lineage_domain._create_lineage_in_scope(
            {
                "title": "Personal Resident Lineage",
                "lineage_membership": "personal",
            },
            "sample_admin",
            "lineage_aaaaaaaaaaaa",
        )

    listed = lineage_domain.list_lineages("sample_admin")["lineages"]

    expected = {formal["lineage_id"], personal["lineage_id"]}
    assert {item["lineage_id"] for item in listed} == expected
    # A personal Lineage is its owner's alone (sample_admin 2026-10-12).
    assert {
        item["lineage_id"]
        for item in lineage_domain.list_lineages("CASE-A")["lineages"]
    } == {formal["lineage_id"]}
    assert personal["lineage_membership"] == "personal"


def test_lineage_list_offers_changes_only_where_the_permission_system_allows(
    lineage_store: Path, permissions,
) -> None:
    """权限系统开启时，Lineage 列表按每个按钮自己的指令报告 allows，can_manage 只对能修改它的人为真

    contributor 能修改 Lineage、新建和修改 Resident，删除 Lineage 要 admin。
    """
    created = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")
    lineage_id = created["lineage_id"]
    permissions.users("sample_admin", "CASE-A", category="intern")
    # sample_admin manages the Project he created, and with it the Lineage; creating the Lineage alone gives nothing.
    permissions.place(Fact("project", "Demo", creator="sample_admin"), Fact("lineage", lineage_id, {"project": "Demo"}, creator="CASE-A"))

    def listed(actor):
        return {item["lineage_id"]: item for item in lineage_domain.list_lineages(actor)["lineages"]}[lineage_id]

    assert all(listed("sample_admin")["allows"].values()) and "can_manage" not in listed("sample_admin")
    assert not any(listed("CASE-A")["allows"].values())
    permissions.grant("CASE-A", lineage_id)
    assert listed("CASE-A")["allows"] == {"UpdateLineage": True, "DeleteLineage": False, "CreateResident": True,
                                      "UpdateResident": True, "CreateWorkspaceHandoff": True}


def test_resident_pages_offer_changes_only_where_the_permission_system_allows(
    lineage_store: Path, permissions,
) -> None:
    """权限系统开启时，Resident 页面的修改入口只对能修改它的人开放，个人 AMA 只对本人开放"""
    from management_portal.society_service.resident_access import may_change_resident

    resident_id = "resident_123456789abc"
    permissions.users("sample_admin", "CASE-A", category="intern")
    permissions.place(Fact("project", "Demo", creator="sample_admin"), Fact("lineage", "lineage_123456789abc", {"project": "Demo"}),
                      Fact("resident", resident_id, {"lineage": "lineage_123456789abc"}, creator="CASE-A"))
    record = {"resident_id": resident_id}
    assert may_change_resident(record, "sample_admin") is True
    assert may_change_resident(record, "CASE-A") is False
    # A personal AMA sits under its owner in the scheme's trees (decision_b820faf42218 §5.5).
    ama = "resident_abcdefabcdef"
    permissions.place(Fact("lineage", "lineage_abcdefabcdef", {"personal_space": "personal_space:CASE-A", "user": "CASE-A"}),
                      Fact("resident", ama, {"lineage": "lineage_abcdefabcdef"}))
    personal = {"resident_id": ama, "personal_owner_id": "CASE-A"}
    assert may_change_resident(personal, "CASE-A") is True
    assert may_change_resident(personal, "sample_admin") is False


def test_lineage_fragments_persist_edit_and_delete_with_lineage_revision(
    lineage_store: Path,
) -> None:
    """Lineage 片段随 revision 保存、编辑和删除，附件和创建时间保留，type 被去掉"""
    payload = sample_lineage_payload()
    payload["fragments"] = [{
        "id": "fragment_idea_01",
        "type": "idea",
        "content": "先验证右下角入口是否足够自然。\n\n[image: /srv/fragment-image.png]",
        "attachments": [{
            "id": "fragment-image-01",
            "file_id": "sample_admin/20260730/fragment-image.png",
            "kind": "image",
            "name": "fragment-image.png",
            "path": "/srv/fragment-image.png",
            "preview_url": "/api/agent-workbench/chat-attachments/image?file_id=sample_admin/20260730/fragment-image.png",
            "content_type": "image/png",
            "size": 128,
        }],
    }]
    created = lineage_domain.create_lineage(payload, "sample_admin")

    assert created["fragments"] == [{
        "id": "fragment_idea_01",
        "content": "先验证右下角入口是否足够自然。\n\n[image: /srv/fragment-image.png]",
        "attachments": [{
            "id": "fragment-image-01",
            "file_id": "sample_admin/20260730/fragment-image.png",
            "kind": "image",
            "name": "fragment-image.png",
            "path": "/srv/fragment-image.png",
            "preview_url": "/api/agent-workbench/chat-attachments/image?file_id=sample_admin/20260730/fragment-image.png",
            "content_type": "image/png",
            "size": 128,
        }],
        "created_by": "sample_admin",
        "created_at": created["fragments"][0]["created_at"],
        "updated_by": "sample_admin",
        "updated_at": created["fragments"][0]["updated_at"],
    }]

    edited_payload = {**created}
    edited_payload["fragments"] = [{
        **created["fragments"][0],
        "type": "plan",
        "content": "先实现卡片编辑，再验证原文与图片拖放。\n\n[image: /srv/fragment-image.png]",
    }]
    edited = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "lineage": edited_payload,
        },
        "sample_admin",
    )
    assert edited["revision"] == created["revision"] + 1
    assert "type" not in edited["fragments"][0]
    assert edited["fragments"][0]["content"] == "先实现卡片编辑，再验证原文与图片拖放。\n\n[image: /srv/fragment-image.png]"
    assert edited["fragments"][0]["attachments"] == created["fragments"][0]["attachments"]
    assert edited["fragments"][0]["created_at"] == created["fragments"][0]["created_at"]

    deleted = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": edited["revision"],
            "lineage": {**edited, "fragments": []},
        },
        "sample_admin",
    )
    assert deleted["fragments"] == []


def test_lineage_creation_uses_canonical_folder_layout(lineage_store: Path) -> None:
    """新建 Lineage 使用 v3 目录结构，含 residents 目录、LINEAGE.md 和 README.md"""
    created = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")

    lineage_folder = lineage_store / "lineages" / created["lineage_id"]
    assert created["schema"] == "lineage-folder-v3"
    assert (lineage_folder / "residents").is_dir()
    assert lineage_domain._lineage_catalog_store().get_state(created["lineage_id"])[1]
    assert (lineage_folder / "LINEAGE.md").is_file()
    assert (lineage_folder / "README.md").is_file()


def test_lineage_creation_attaches_canonical_resident_and_workspace_ids(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建和更新 Lineage 时节点使用规范 Resident ID，不带 fork_id"""
    def targets(requested: list[str]) -> dict[str, dict[str, str]]:
        mappings = {
            "resident_0123456789ab": {
                "resident_id": "resident_0123456789ab",
            },
            "resident_222222222222": {
                "resident_id": "resident_222222222222",
                "source_resident_id": "resident_0123456789ab",
            },
        }
        return {key: mappings[key] for key in requested}

    monkeypatch.setattr(lineage_test_support, "RESIDENT_TARGET_RESOLVER", targets)
    created = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")

    assert created["nodes"][0]["id"] == "resident_0123456789ab"
    assert created["nodes"][1]["id"] == "resident_222222222222"
    assert "fork_id" not in created["nodes"][1]

    updated = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "lineage": {**created, "description": "identity-preserving update"},
        },
        "sample_admin",
    )
    assert [node["id"] for node in updated["nodes"]] == [
        "resident_0123456789ab",
        "resident_222222222222",
    ]


def test_lineage_node_metadata_authorizes_the_graph_node_and_uses_light_projection(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读取 Lineage 节点元数据只校验节点属于该图，不进写作用域也不读运行时状态"""
    del lineage_store
    created = lineage_domain.create_lineage(
        sample_lineage_payload(),
        "sample_admin",
    )
    calls: list[tuple[str, str]] = []

    def detail(resident_id: str, actor: str):
        calls.append((resident_id, actor))
        return {"schema": "tfi.lineage-node-metadata.v1"}

    monkeypatch.setattr(
        resident_directory_domain,
        "get_lineage_node_metadata",
        detail,
    )
    monkeypatch.setattr(
        lineage_domain,
        "_lineage_scope",
        lambda *_args, **_kwargs: pytest.fail("metadata entered a Lineage write scope"),
    )
    monkeypatch.setattr(
        lineage_domain,
        "_read_lineage_state",
        lambda *_args, **_kwargs: pytest.fail("metadata materialized runtime state"),
    )
    node = created["nodes"][1]

    payload = lineage_domain.get_lineage_node_metadata(
        created["lineage_id"],
        node["id"],
        "sample_admin",
    )

    assert calls == [(
        node["id"],
        "sample_admin",
    )]
    assert payload == {
        "schema": "tfi.lineage-node-metadata.v1",
        "lineage_id": created["lineage_id"],
        "lineage_resident_id": node["id"],
    }
    with pytest.raises(KeyError, match="Lineage node not found"):
        lineage_domain.get_lineage_node_metadata(
            created["lineage_id"],
            "resident_ffffffffffff",
            "sample_admin",
        )






def test_node_workspaces_list_the_resident_bindings_with_its_own_first(
    lineage_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """节点 Workspace 列出 Resident 的全部绑定，自有 Workspace 排在最前并作为默认启动目录"""
    created = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")
    owned = node_owned_workspace(created, "resident_0123456789ab")
    owned.mkdir(parents=True)
    reference = tmp_path / "reference"
    reference.mkdir()

    def roots_resolver(resident_id: str, actor: str) -> dict:
        assert (resident_id, actor) == ("resident_0123456789ab", "sample_admin")
        return {
            "default_cwd": str(owned),
            "workspace_id": "workspace_000000000001",
            "roots": [
                {"workspace_id": "workspace_000000000002", "label": "Evidence", "path": str(reference), "purpose": "证据"},
                {"workspace_id": "workspace_000000000001", "label": "workspace", "path": str(owned), "purpose": ""},
            ],
        }

    monkeypatch.setattr(lineage_test_support, "NODE_WORKSPACE_FOLDER_RESOLVER", roots_resolver)

    workspace = lineage_domain.get_node_workspaces(created["lineage_id"], "resident_0123456789ab", "sample_admin")
    assert (workspace["node_id"], workspace["resident_id"]) == ("resident_0123456789ab", "resident_0123456789ab")
    assert workspace["default_cwd"] == str(owned)
    assert [item["workspace_id"] for item in workspace["workspaces"]] == ["workspace_000000000001", "workspace_000000000002"]
    assert workspace["default_workspace"] == workspace["workspaces"][0]
    assert workspace["workspaces"][0]["is_default_cwd"] and not workspace["workspaces"][1]["is_default_cwd"]
    assert workspace["workspaces"][1] == {
        "workspace_id": "workspace_000000000002", "purpose": "证据", "label": "Evidence",
        "path": str(reference), "is_default_cwd": False, "exists": True, "is_dir": True,
    }

def test_node_primary_workspace_binding_requires_resident_owner(
    lineage_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """节点绑定参考 Workspace 不改 revision 和主 Workspace，非 owner 被拒绝"""
    created = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")
    source = tmp_path / "portal-source"
    fallback = tmp_path / "resident-default"
    source.mkdir()
    fallback.mkdir()
    (source / "app.py").write_text("print('baseline')\n", encoding="utf-8")
    (source / "docs").mkdir()
    (source / "docs" / "decision.md").write_text("# Decision\n", encoding="utf-8")
    (source / ".env").write_text("SECRET=do-not-copy\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("private git metadata", encoding="utf-8")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "cached.js").write_text("cache", encoding="utf-8")
    (source / "outside-link").symlink_to(tmp_path)

    monkeypatch.setattr(
        lineage_test_support,
        "NODE_WORKSPACE_FOLDER_RESOLVER",
        lambda resident_id, actor: {
            "default_cwd": str(source),
            "workspace_id": "workspace_000000000001",
            "roots": [{
                "workspace_id": "workspace_000000000001",
                "label": "Node Workspace",
                "path": str(source),
            }],
        },
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_REFERENCE_WORKSPACE_MOUNTER",
        lambda resident_id, path, actor: {
            "path": path,
            "added": True,
        },
    )
    bound = lineage_domain.bind_node_workspace(
        created["lineage_id"],
        {
            "resident_id": "resident_0123456789ab",
            "expected_revision": created["revision"],
            "path": str(fallback),
            "label": "Attached workspace",
        },
        "sample_admin",
    )
    assert bound["lineage"]["revision"] == created["revision"]
    assert bound["workspace"]["default_cwd"] == str(source)
    assert bound["workspace"]["default_workspace"]["workspace_id"] == "workspace_000000000001"

    monkeypatch.setattr(
        lineage_test_support,
        "NODE_WORKSPACE_FOLDER_RESOLVER",
        lambda resident_id, actor: {
            "default_cwd": str(fallback),
            "workspace_id": "workspace_000000000001",
            "roots": [],
        },
    )
    def denied_mount(*args, **kwargs):
        raise PermissionError("只有 Resident 的 owner 可以绑定")
    monkeypatch.setattr(lineage_test_support, "NODE_REFERENCE_WORKSPACE_MOUNTER", denied_mount)
    with pytest.raises(PermissionError, match="Resident 的 owner"):
        lineage_domain.bind_node_workspace(
            created["lineage_id"],
            {
                "resident_id": "resident_0123456789ab",
                "expected_revision": bound["lineage"]["revision"],
                "path": str(source),
            },
            "sample_admin",
        )


def test_workspace_binding_repairs_before_projecting_default_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """添加参考 Workspace 时先完成挂载，再投影节点的默认工作目录"""
    resident_id = "resident_0123456789ab"
    lineage_id = "lineage_0123456789ab"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[str] = []
    lineage = {
        "lineage_id": lineage_id,
        "nodes": [{"id": resident_id, "title": "Repair target"}],
    }
    monkeypatch.setattr(
        lineage_domain,
        "_node_workspace_context",
        lambda *_args: (lineage, lineage["nodes"][0]),
    )

    def mount(*_args, **_kwargs):
        events.append("mount")
        return {
            "resident_id": resident_id,
            "path": str(workspace),
            "added": True,
        }

    def project(*_args, **_kwargs):
        assert events == ["mount"]
        return {
            "workspaces": [{"path": str(workspace), "is_default_cwd": True}],
            "default_cwd": str(workspace),
        }

    monkeypatch.setattr(lineage_domain, "_mount_resident_reference_workspace", mount)
    monkeypatch.setattr(lineage_domain, "get_node_workspaces", project)

    result = lineage_domain.add_node_reference_workspace(
        lineage_id,
        resident_id,
        "resident_aaaaaaaaaaaa",
        str(workspace),
    )

    assert result["mount"]["added"] is True
    assert result["workspace"]["default_cwd"] == str(workspace)


def test_canonical_handoff_prepares_copy_binds_target_without_dispatch(
    lineage_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handoff 准备并绑定 Workspace，不隐式派发任务

    :module: resident.fork-handoff
    :design: decision_5bf5bfbd52cb, decision_7ef392b5f60d
    :design-point:
    """
    payload = sample_lineage_payload()
    payload["edges"] = []
    created = lineage_domain.create_lineage(payload, "sample_admin")
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("handoff\n", encoding="utf-8")
    target_default = tmp_path / "target-default"
    target_default.mkdir()
    copied = lineage_domain._lineage_dir(created["lineage_id"]) / "handoff-workspaces" / "copy-one"
    copied_file = lineage_domain._lineage_dir(created["lineage_id"]) / "handoff-workspaces" / "copy-file"
    mounted = {
        "resident_0123456789ab": [source],
        "resident_222222222222": [target_default],
    }
    sent: list[tuple[str, str]] = []
    released: list[tuple[str, str]] = []

    def workspace_view(lineage_id: str, resident_id: str, actor: str) -> dict:
        roots = []
        for index, path in enumerate(mounted[resident_id], start=1):
            roots.append({
                "workspace_id": f"workspace_{resident_id[-4:]}_{index}",
                "label": path.name,
                "path": str(path),
                "is_default_cwd": index == 1,
            })
        return {
            "lineage_id": lineage_id,
            "resident_id": resident_id,
            "default_cwd": str(mounted[resident_id][0]),
            "workspaces": roots,
        }

    def add_workspace(
        lineage_id: str,
        resident_id: str,
        actor: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        mounted[resident_id].append(Path(path))
        return {
            "workspace": workspace_view(lineage_id, resident_id, actor),
            "added": True,
        }

    monkeypatch.setattr(lineage_domain, "get_node_workspaces", workspace_view)
    monkeypatch.setattr(lineage_domain, "add_node_reference_workspace", add_workspace)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, prompt, actor: (
            sent.append((str(node["id"]), prompt))
            or {"message_id": "job_handoff"}
        ),
    )

    response = lineage_domain.create_handoff(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "source_resident_id": "resident_0123456789ab",
            "target_resident_id": "resident_222222222222",
            "purpose": "把源代码交给设计节点独立修改",
            "acceptance": "README 可读且 Agent 已启动",
            "explanation": "README.md 说明项目入口；Portal 源码是目标节点需要继续修改的主体。",
            "items": [
                {
                    "item_id": "portal",
                    "source": {"root_id": "work_1", "scope": "."},
                    "transfer": {"mode": "copy", "method": "filesystem_copy"},
                    "target": {"path": str(copied), "scope": ".", "label": "独立 Portal"},
                },
                {
                    "item_id": "readme",
                    "source": {"root_id": "work_1", "scope": "README.md"},
                    "transfer": {"mode": "copy", "method": "filesystem_copy"},
                    "target": {"path": str(copied_file), "scope": ".", "label": "README 交接"},
                },
            ],
            "execute_now": True,
        },
        "sample_admin",
    )

    handoff = response["handoff"]
    assert handoff["status"] == "ready"
    assert len(handoff["items"]) == 2
    assert all(item["attempts"][0]["status"] == "ready" for item in handoff["items"])
    assert handoff["items"][0]["result"]["target_path"] == str(copied)
    assert handoff["items"][1]["result"]["target_path"] == str(copied_file)
    assert copied.joinpath("README.md").read_text(encoding="utf-8") == "handoff\n"
    assert copied_file.joinpath("README.md").read_text(encoding="utf-8") == "handoff\n"
    assert copied in mounted["resident_222222222222"]
    assert copied_file in mounted["resident_222222222222"]
    assert "purpose" not in handoff
    assert "acceptance" not in handoff
    assert handoff["explanation"] == "README.md 说明项目入口；Portal 源码是目标节点需要继续修改的主体。"
    assert sent == []
    assert released == []
    assert "handoff_deliveries" not in response["lineage"]
    assert [edge["type"] for edge in response["lineage"]["edges"]] == ["handoff"]


def test_canonical_handoff_failure_is_recorded_on_same_edge(
    lineage_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交接目标路径不在允许范围时执行失败，失败记在同一条交接边上且不发消息"""
    payload = sample_lineage_payload()
    payload["edges"] = []
    created = lineage_domain.create_lineage(payload, "sample_admin")
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside" / "copy"
    sent: list[str] = []

    def workspace_view(lineage_id: str, resident_id: str, actor: str) -> dict:
        path = source if resident_id == "resident_0123456789ab" else tmp_path / "target"
        path.mkdir(exist_ok=True)
        return {
            "lineage_id": lineage_id,
            "resident_id": resident_id,
            "default_cwd": str(path),
            "workspaces": [{
                "workspace_id": f"workspace_{resident_id[-4:]}",
                "label": "Workspace",
                "path": str(path),
                "is_default_cwd": True,
            }],
        }

    monkeypatch.setattr(lineage_domain, "get_node_workspaces", workspace_view)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: sent.append(str(node["id"])),
    )
    draft = lineage_domain.create_handoff(
        created["lineage_id"],
        {
            "source_resident_id": "resident_0123456789ab",
            "target_resident_id": "resident_222222222222",
            "items": [{
                "source": {"root_id": "work_1"},
                "transfer": {"mode": "copy", "method": "filesystem_copy"},
                "target": {"path": str(outside)},
            }],
            "execution": {"enabled": True, "prompt": "不应启动"},
        },
        "sample_admin",
    )

    with pytest.raises(PermissionError, match="允许"):
        lineage_domain.execute_handoff(
            created["lineage_id"],
            draft["handoff"]["id"],
            "sample_admin",
        )
    current = lineage_domain.get_lineage(created["lineage_id"], "sample_admin")
    handoff = next(edge for edge in current["edges"] if edge["id"] == draft["handoff"]["id"])
    assert handoff["status"] == "failed"
    assert handoff["items"][0]["attempts"][0]["status"] == "failed"
    assert "允许" in handoff["error"]
    assert not outside.exists()
    assert sent == []


def test_ready_handoff_keeps_items_but_accepts_explanation_changes(
    lineage_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已就绪的交接不能再改交接项，但可以修改说明"""
    payload = sample_lineage_payload()
    payload["edges"] = []
    created = lineage_domain.create_lineage(payload, "sample_admin")
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("handoff\n", encoding="utf-8")

    def workspace_view(lineage_id: str, resident_id: str, actor: str) -> dict:
        path = source if resident_id == "resident_0123456789ab" else tmp_path / "target"
        path.mkdir(exist_ok=True)
        return {
            "lineage_id": lineage_id,
            "resident_id": resident_id,
            "default_cwd": str(path),
            "workspaces": [{
                "workspace_id": f"workspace_{resident_id[-4:]}",
                "label": "Workspace",
                "path": str(path),
                "is_default_cwd": True,
            }],
        }

    monkeypatch.setattr(lineage_domain, "get_node_workspaces", workspace_view)
    copied = lineage_domain._lineage_dir(created["lineage_id"]) / "handoff-workspaces" / "copy-ready"

    def add_workspace(lineage_id: str, resident_id: str, actor: str, path: str, **_kwargs: object) -> dict:
        view = workspace_view(lineage_id, resident_id, actor)
        view["workspaces"].append({"workspace_id": "workspace_copy_ready", "path": path})
        return {"workspace": view, "added": True}

    monkeypatch.setattr(lineage_domain, "add_node_reference_workspace", add_workspace)
    ready = lineage_domain.create_handoff(
        created["lineage_id"],
        {
            "source_resident_id": "resident_0123456789ab",
            "target_resident_id": "resident_222222222222",
            "items": [{
                "item_id": "portal",
                "source": {"root_id": "work_1"},
                "transfer": {"mode": "copy", "method": "filesystem_copy"},
                "target": {"path": str(copied)},
            }],
            "execute_now": True,
        },
        "sample_admin",
    )
    handoff_id = ready["handoff"]["id"]
    assert ready["handoff"]["status"] == "ready"

    with pytest.raises(ValueError, match="不能再修改交接项"):
        lineage_domain.update_handoff(
            created["lineage_id"],
            handoff_id,
            {"items": [{"item_id": "other", "source": {"root_id": "work_1"}}]},
            "sample_admin",
        )
    updated = lineage_domain.update_handoff(
        created["lineage_id"], handoff_id, {"explanation": "README 说明入口"}, "sample_admin",
    )["handoff"]
    assert updated["status"] == "ready"
    assert updated["explanation"] == "README 说明入口"
    assert [item["item_id"] for item in updated["items"]] == ["portal"]
    assert updated["items"][0]["result"]["target_path"] == str(copied)


def test_handoff_execution_projects_exact_resident_message_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交接执行状态按对应消息的完成状态投影为 completed，存储记录不变"""
    lineage = sample_lineage_payload()
    handoff = lineage["edges"][0]
    handoff["execution"] = {
        "enabled": True,
        "status": "queued",
        "message_id": "message_handoff_exact",
    }
    lineage["nodes"][1]["status"] = "idle"
    monkeypatch.setattr(
        resident_protocol,
        "get_resident_input_state",
        lambda resident_id, actor, message_id: {
            "resident_id": resident_id,
            "message_id": message_id,
            "state": "completed",
            "updated_at": "2026-07-26T13:18:00Z",
        },
    )

    view = lineage_domain._verified_lineage_view(lineage, "sample_admin")

    execution = view["edges"][0]["execution"]
    assert execution["status"] == "completed"
    assert execution["completed_at"] == "2026-07-26T13:18:00Z"
    assert lineage["edges"][0]["execution"]["status"] == "queued"


@pytest.mark.parametrize("input_status", ["completed", "failed", "cancelled"])
def test_graph_resident_status_uses_native_state_after_terminal_input(
    lineage_store: Path, monkeypatch: pytest.MonkeyPatch, input_status: str,
) -> None:
    """输入结束后图中 Resident 状态取原生的 idle，保存的节点记录不变"""
    lineage = sample_lineage_payload()
    node = lineage["nodes"][0]
    resident_id = node["id"]
    node["status"] = "queued"
    node["last_execution"] = {"message_id": "message_finished", "status": input_status, "execution_id": "execution-preserved"}
    monkeypatch.setattr(resident_protocol, "read_resident_states", lambda ids: {
        resident_id: {"work_state": "idle", "active_message_id": "", "queued_count": 0, "intake_state": "open"},
    })
    monkeypatch.setattr(resident_protocol, "get_resident_input_state", lambda resident, actor, message: {
        "message_id": message, "state": input_status, "updated_at": "2026-09-12T08:04:09Z",
    })
    view = lineage_domain._verified_lineage_view(lineage, "sample_admin")
    projected = view["nodes"][0]
    assert projected["status"] == "idle"
    assert projected["last_execution"] == node["last_execution"]
    assert node["status"] == "queued"
    assert node["last_execution"]["status"] == input_status


def test_graph_resident_runtime_cache_tracks_native_cursor_without_graph_write(
    lineage_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图中 Resident 状态随原生状态游标刷新，不写图也不改 revision"""
    resident_id = "resident_111111111111"
    snapshots = {resident_id: {"state": {"work_state": "queued", "active_message_id": "message_running", "queued_count": 0, "intake_state": "open"}, "cursor": 1}}
    monkeypatch.setattr(resident_protocol, "read_resident_states", lambda ids: {
        identity: snapshots[identity]["state"] for identity in ids if identity in snapshots
    })
    monkeypatch.setattr(resident_protocol, "read_resident_state_snapshots", lambda ids: {
        identity: snapshots[identity] for identity in ids if identity in snapshots
    })
    lineage = lineage_test_support.create_lineage_with_resident_members({
        "title": "Native state authority", "nodes": [{"id": resident_id, "title": "Worker"}], "edges": [],
    }, "sample_admin")
    first = lineage_domain.get_lineage(lineage["lineage_id"], "sample_admin")
    assert first["nodes"][0]["status"] == "queued"
    snapshots[resident_id] = {"state": {"work_state": "idle", "active_message_id": "", "queued_count": 0, "intake_state": "open"}, "cursor": 2}
    second = lineage_domain.get_lineage(lineage["lineage_id"], "sample_admin")
    assert second["nodes"][0]["status"] == "idle"
    assert second["revision"] == first["revision"]
    assert second["runtime_revision"] == first["runtime_revision"]
    assert second["refresh_signature"] == first["refresh_signature"]


@pytest.mark.parametrize("work_state", ["queued", "running"])
def test_graph_keeps_new_work_state_after_previous_run_completed(
    lineage_store: Path, monkeypatch: pytest.MonkeyPatch, work_state: str,
) -> None:
    """上一轮完成后 Resident 又有输入排队或运行时，图中节点显示新的工作状态"""
    lineage = sample_lineage_payload()
    node = lineage["nodes"][0]
    node["last_execution"] = {"message_id": "previous_input", "status": "completed", "execution_id": "execution-previous"}
    monkeypatch.setattr(resident_protocol, "read_resident_states", lambda ids: {
        node["id"]: {"work_state": work_state, "active_message_id": "next_input", "queued_count": 0, "intake_state": "open"},
    })
    monkeypatch.setattr(resident_protocol, "get_resident_input_state", lambda resident, actor, message: {
        "state": "completed", "updated_at": "2026-09-12T08:04:09Z",
    })
    view = lineage_domain._verified_lineage_view(lineage, "sample_admin")
    assert view["nodes"][0]["status"] == work_state
    assert view["nodes"][0]["last_execution"]["status"] == "completed"


def test_seed_dismissal_updates_program_state_without_touching_its_markdown(
    lineage_store: Path,
) -> None:
    """驳回 Seed 只把组件状态改为 dismissed，Seed 的 Markdown 文件保留"""
    created = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")
    artifact_path = "seeds/resident_222222222222/idea-01.md"
    # A Seed's Markdown is in the Lineage folder (decision_5b5653c3eaa5).
    artifact_file = lineage_domain._lineage_dir(created["lineage_id"]) / artifact_path
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text("# 重复的候选方向\n\n这份内容仍需保留用于审计。\n", encoding="utf-8")
    candidate = json.loads(json.dumps(created, ensure_ascii=False))
    candidate.setdefault("program", {}).setdefault("components", []).append({
        "id": "seed_idea_dismiss_01",
        "kind": "seed",
        "title": "重复的候选方向",
        "x": 46,
        "y": 44,
        "config": {
            "seed_type": "idea",
            "summary": "等待确认",
            "artifact_path": artifact_path,
            "source_resident_id": "resident_0123456789ab",
            "status": "active",
            "created_by": "sample_admin",
        },
    })
    seeded = lineage_domain.update_lineage(
        created["lineage_id"],
        {"expected_revision": created["revision"], "lineage": candidate},
        "sample_admin",
    )
    # Dismissing a Seed is a component update (decision_4637ca4a69e2).
    current = next(item for item in seeded["program"]["components"] if item["id"] == "seed_idea_dismiss_01")
    dismissed = lineage_domain.save_lineage_program_component(
        created["lineage_id"],
        {
            "expected_revision": seeded["revision"],
            "component_id": "seed_idea_dismiss_01",
            "component": {"config": {**current["config"], "status": "dismissed"}},
        },
        "sample_admin",
    )

    seed = next(item for item in dismissed["lineage"]["program"]["components"] if item["id"] == "seed_idea_dismiss_01")
    assert seed["config"]["status"] == "dismissed"
    assert artifact_file.read_text(encoding="utf-8") == "# 重复的候选方向\n\n这份内容仍需保留用于审计。\n"
    assert not [
        item for item in dismissed["lineage"]["program"]["components"]
        if item.get("kind") == "seed"
        and item.get("config", {}).get("status") == "active"
    ]
    assert dismissed["lineage"]["revision"] == seeded["revision"] + 1

def test_seed_promotion_creates_an_idle_resident_without_starting_work(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """采纳 Seed 分叉出空闲 Resident 且不启动工作，候选分叉边转为继承边"""
    created = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")
    candidate = json.loads(json.dumps(created, ensure_ascii=False))
    candidate.setdefault("program", {}).setdefault("components", []).append({
        "id": "seed_idea_promote_01",
        "kind": "seed",
        "title": "事件驱动协作",
        "x": 30,
        "y": 40,
        "config": {
            "seed_type": "idea",
            "summary": "用持久化事件驱动协作。",
            "artifact_path": "seeds/resident_222222222222/idea-01.md",
            "source_resident_id": "resident_0123456789ab",
            "status": "active",
            "created_by": "sample_admin",
        },
    })
    seeded = lineage_domain.update_lineage(
        created["lineage_id"],
        {"expected_revision": created["revision"], "lineage": candidate},
        "sample_admin",
    )
    # The Seed's source is recorded as its candidate fork, not in its config.
    candidate_fork = next(edge for edge in seeded["edges"] if edge["type"] == "candidate_fork")
    assert (candidate_fork["source"], candidate_fork["target"]) == ("resident_0123456789ab", "seed_idea_promote_01")
    seed_config = next(item for item in seeded["program"]["components"] if item["id"] == "seed_idea_promote_01")["config"]
    assert "source_resident_id" not in seed_config
    forks: list[tuple[str, dict]] = []

    def fork(source_id: str, actor: str, _context: str, **options: object) -> dict:
        forks.append((source_id, options))
        return {"resident_id": "resident_100000000007"}

    monkeypatch.setattr(resident_fork_domain, "create_resident_full_context_fork", fork)
    sent: list[str] = []
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER",
                        lambda node, _prompt, _actor: sent.append(node["id"]) or {"message_id": "job"})

    promoted = lineage_domain.promote_seed_node(
        created["lineage_id"],
        {
            "expected_revision": seeded["revision"],
            "seed_id": "seed_idea_promote_01",
            "action_purpose": "把这个候选实现成可验证原型。",
        },
        "sample_admin",
    )
    assert forks == [("resident_0123456789ab", {"defer_backend_start": True})]
    child = promoted["resident"]
    assert child["id"] == "resident_100000000007"
    assert child["origin_seed_id"] == "seed_idea_promote_01"
    assert child["status"] == "idle"
    inheritance = next(
        edge for edge in promoted["lineage"]["edges"]
        if edge["type"] == "inheritance" and edge["target"] == child["id"]
    )
    assert inheritance["source"] == "resident_0123456789ab"
    # Adopting turns the candidate fork into that inheritance relation.
    assert inheritance["id"] == candidate_fork["id"] and inheritance["label"] == "Seed 采纳"
    assert not [edge for edge in promoted["lineage"]["edges"] if edge["type"] == "candidate_fork"]
    assert promoted["seed"]["config"]["status"] == "promoted"
    assert promoted["seed"]["config"]["promoted_resident_id"] == child["id"]
    assert promoted["started"] is False and sent == []
    with pytest.raises(ValueError, match="已经被采纳"):
        lineage_domain.promote_seed_node(
            created["lineage_id"],
            {"expected_revision": promoted["lineage"]["revision"], "seed_id": "seed_idea_promote_01"},
            "sample_admin",
        )


def test_lineage_graph_validation(lineage_store: Path) -> None:
    """Lineage 图含非规范 Resident ID 或边指向不存在的节点时创建失败"""
    invalid = sample_lineage_payload()
    invalid["nodes"][1]["id"] = "fork_invalid"
    invalid["nodes"][1]["id"] = "fork_invalid"
    with pytest.raises(ValueError, match="规范的 Resident ID"):
        lineage_domain.create_lineage(invalid, "sample_admin")

    invalid = sample_lineage_payload()
    invalid["edges"][0]["target"] = "resident_ffffffffffff"
    with pytest.raises(ValueError, match="不存在的节点"):
        lineage_domain.create_lineage(invalid, "sample_admin")


def test_inheritance_edge_is_independent_from_resident_provenance(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """继承边按图中定义的端点保存，不受 Resident 来源记录影响"""
    payload = sample_lineage_payload()
    root_id = payload["nodes"][0]["id"]
    target_id = payload["nodes"][1]["id"]
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_TARGET_RESOLVER",
        lambda resident_ids: {
            resident_id: {
                "resident_id": resident_id,
                "lineage_id": "",
                "source_resident_id": "resident_333333333333",
            }
            for resident_id in resident_ids
        },
    )
    payload["edges"] = [{
        "id": "edge_inherit",
        "source": "resident_0123456789ab",
        "target": "resident_222222222222",
        "type": "inheritance",
        "label": "完整研究轨迹",
        "contract": {"context_scope": "完整 Resident Session 上下文"},
    }]

    created = lineage_domain.create_lineage(payload, "sample_admin")
    edge = created["edges"][0]
    assert edge["source"] == root_id
    assert edge["target"] == target_id
    assert "truth" not in edge
    normalized = lineage_domain._normalize_lineage_payload(
        created,
        created,
        "sample_admin",
    )
    assert normalized["edges"][0]["source"] == root_id
    assert normalized["edges"][0]["target"] == target_id


def test_existing_lineage_accepts_canonical_inheritance_edges(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建 Lineage 时接受连接已有 Resident 的继承边，边上不附加 truth 字段"""
    payload = sample_lineage_payload()
    root_id = payload["nodes"][0]["id"]
    target_id = payload["nodes"][1]["id"]
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_TARGET_RESOLVER",
        lambda resident_ids: {
            resident_id: {
                "resident_id": resident_id,
                "lineage_id": "",
            }
            for resident_id in resident_ids
        },
    )
    payload["edges"] = [{
        "id": "edge_imported_parent",
        "source": "resident_0123456789ab",
        "target": "resident_222222222222",
        "type": "inheritance",
        "label": "从既有 Resident 轨迹分支",
        "contract": {},
    }]
    created = lineage_domain.create_lineage(payload, "sample_admin")
    assert created["edges"][0]["source"] == root_id
    assert created["edges"][0]["target"] == target_id
    assert "truth" not in created["edges"][0]


@pytest.mark.parametrize(
    ("mode", "creator_name"),
    [
        ("full", "create_resident_full_context_fork"),
        ("message", "create_resident_fork_from_message"),
        ("compacted", "create_resident_compacted_context_fork"),
        ("recent-5", "create_resident_context_fork"),
        ("recent-10", "create_resident_context_fork"),
    ],
)
def test_lineage_branch_passes_selected_codex_account_to_every_fork_mode(
    mode: str,
    creator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每种分叉模式都把所选 backend_route_id 原样传给 Resident 分叉"""
    captured: dict[str, object] = {}

    def create_fork(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"resident_id": "resident_100000000099"}

    monkeypatch.setattr(
        resident_fork_domain,
        creator_name,
        create_fork,
    )

    result = lineage_domain._create_derived_resident(
        {
            "id": "resident_200000000099",
        },
        {
            "mode": mode,
            "backend_route_id": "ONE",
            "message": {},
        },
        "sample_admin",
        "lineage:test",
    )

    assert result["resident_id"] == "resident_100000000099"
    # Lineage forwards opaque provider route IDs; validation belongs to the
    # selected provider's Fork authority (Model API routes may be case-sensitive).
    assert captured["kwargs"]["backend_route_id"] == "ONE"
    assert "result_format" not in captured["kwargs"]


def test_edge_normalization_preserves_stored_inheritance_label() -> None:
    """规范化继承边时保留已存的标签并去掉 contract"""
    edge = lineage_domain._normalize_edge(
        {
            "id": "edge_compressed_0123456789abcdef",
            "source": "resident_aaaaaaaaaaaa",
            "target": "resident_bbbbbbbbbbbb",
            "type": "inheritance",
            "label": "压缩继承 · 下游节点",
            "contract": {
                "context_scope": "图中间节点移除后，沿既有 HATG inheritance 路径自动压缩连接。",
            },
        },
        {"resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"},
    )

    assert edge["label"] == "压缩继承 · 下游节点"
    assert "contract" not in edge


def test_lineage_message_branch_separates_graph_parent_from_context_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """按消息分叉时，图中父节点和上下文来源 Resident 分别传给分叉创建"""
    captured: dict[str, object] = {}

    def create_fork(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"resident_id": "resident_100000000098"}

    monkeypatch.setattr(
        resident_fork_domain,
        "create_resident_fork_from_message",
        create_fork,
    )
    graph_parent_id = "resident_200000000098"
    context_source_id = "resident_300000000098"

    result = lineage_domain._create_derived_resident(
        {"id": graph_parent_id},
        {
            "mode": "message",
            "context_source_resident_id": context_source_id,
            "message": {"message_id": "message_external"},
        },
        "sample_admin",
        "lineage:test",
    )

    assert result["resident_id"] == "resident_100000000098"
    assert captured["args"][:5] == (
        context_source_id,
        "sample_admin",
        {"message_id": "message_external"},
        graph_parent_id,
        "lineage:test",
    )


def test_lineage_branch_merges_latest_revision_and_is_idempotent(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 分支基于最新 revision 合并保存，重复请求不再创建 Resident"""
    resident_id = "resident_200000000010"
    created_resident_id = "resident_100000000010"
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_TARGET_RESOLVER",
        lambda resident_ids: {
            item: {
                "resident_id": item,
                "lineage_id": "",
            }
            for item in resident_ids
        },
    )
    snapshot = {
        "resident_id": resident_id,
        "title": "原子分支 Lineage",
        "backend": "codex",
        "session_id": "session-source",
        "captured_at": "2026-07-24T06:00:00Z",
    }
    creation_calls: list[tuple[dict, dict, str, str]] = []

    monkeypatch.setattr(
        lineage_test_support,
        "LINEAGE_SNAPSHOT_RESOLVER",
        lambda _resident_id, _actor: json.loads(json.dumps(snapshot)),
    )

    def execute_fork(source: dict, payload: dict, actor: str, lineage_source: str) -> dict:
        creation_calls.append((source, payload, actor, lineage_source))
        created_resident = {
            "resident_id": created_resident_id,
            "title": "Console 默认分支名",
            "source_resident_id": resident_id,
            "context_source_resident_id": resident_id,
            "created_at": "2026-07-24T06:01:00Z",
        }
        return {
            "resident_id": created_resident_id,
            "state": "active",
            "created_at": created_resident["created_at"],
            "pending_job": None,
        }

    def sync_resident_title(node: dict, title: str, _actor: str) -> None:
        node["title"] = title

    monkeypatch.setattr(lineage_test_support, "LINEAGE_BRANCH_FORK_CREATOR", execute_fork)
    monkeypatch.setattr(lineage_test_support, "LINEAGE_NODE_RENAME_RESOLVER", sync_resident_title)

    created = lineage_domain.create_lineage({
        "title": "原子分支测试",
        "description": "初始说明",
        "nodes": [{
            "id": resident_id,
            "title": "主轨迹",
            "x": 20,
            "y": 40,
        }],
        "edges": [],
        "leader_resident_id": resident_id,
    }, "sample_admin")
    latest = lineage_domain.update_lineage(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "lineage": {**created, "description": "由另一页面先保存的说明"},
        },
        "sample_admin",
    )

    request = {
        "expected_revision": created["revision"],
        "source_resident_id": resident_id,
        "context_source_resident_id": "resident_300000000010",
        "edge_id": "edge_atomic_branch",
        "mode": "full",
        "title": "并发安全分支",
        "x": 45,
        "y": 55,
    }
    result = lineage_domain.create_lineage_branch(
        created["lineage_id"],
        request,
        "sample_admin",
    )

    assert result["created"] is True
    assert result["lineage"]["revision"] == latest["revision"] + 1
    assert result["lineage"]["description"] == "由另一页面先保存的说明"
    assert result["created_resident"]["id"] == created_resident_id
    assert result["created_resident"]["title"] == "并发安全分支"
    assert result["inheritance_edge"]["source"] == resident_id
    assert result["inheritance_edge"]["target"] == created_resident_id
    assert "contract" not in result["inheritance_edge"]
    assert "truth" not in result["inheritance_edge"]
    assert result["title_sync"] == {"status": "synced", "error": ""}
    assert len(creation_calls) == 1
    assert creation_calls[0][1]["context_source_resident_id"] == "resident_300000000010"

    repeated = lineage_domain.create_lineage_branch(
        created["lineage_id"],
        request,
        "sample_admin",
    )
    assert repeated["created"] is False
    assert repeated["lineage"]["revision"] == result["lineage"]["revision"]
    assert repeated["created_resident"]["id"] == created_resident_id
    assert len(creation_calls) == 1


@pytest.mark.parametrize("measured", [False, True])
def test_lineage_forks_allocate_against_committed_graph_before_provider_creation(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured: bool,
) -> None:
    """连续创建分支时按已提交图和节点尺寸摆放新节点互不重叠，非法尺寸被拒绝"""
    payload = sample_lineage_payload()
    source = payload["nodes"][0]
    source.update(x=300, y=-250)
    lineage = lineage_domain.create_lineage(payload, "sample_admin")
    calls = []

    def create_child(_source, request, _actor, _lineage_source):
        calls.append(request)
        return {
            "resident_id": f"resident_{len(calls) + 500:012x}",
            "label": request["title"],
            "state": "active",
        }

    monkeypatch.setattr(lineage_test_support, "LINEAGE_BRANCH_FORK_CREATOR", create_child)
    placed = [source]
    width, height = (32, 28) if measured else (154 / 12.8, 94 / 9)
    for index in range(12):
        request = {
            "source_resident_id": source["id"],
            "edge_id": f"edge_placement_{index}",
            "x": source["x"],
            "y": source["y"],
        }
        if measured:
            request["placement"] = {
                "node_sizes": {node["id"]: {"width": width, "height": height} for node in placed},
                "child_size": {"width": width, "height": height},
            }
        result = lineage_domain.create_lineage_branch(lineage["lineage_id"], request, "sample_admin")
        child = result["created_resident"]
        for node in placed:
            assert (
                abs(node["x"] - child["x"]) >= width + 24 / 12.8 - 1e-9
                or abs(node["y"] - child["y"]) >= height + 24 / 9 - 1e-9
            )
        placed.append(child)
    persisted = lineage_domain.get_lineage(lineage["lineage_id"], "sample_admin")
    assert len(persisted["nodes"]) == len(payload["nodes"]) + 12
    assert next(node for node in persisted["nodes"] if node["id"] == source["id"])["x"] == 300
    with pytest.raises(ValueError, match="尺寸"):
        lineage_domain.create_lineage_branch(lineage["lineage_id"], {
            "source_resident_id": source["id"],
            "edge_id": "edge_invalid_placement",
            "placement": {"child_size": {"width": 0, "height": 20}},
        }, "sample_admin")
    assert len(calls) == 12


def test_regular_lineage_branch_uses_graph_title_as_the_shared_fork_name(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通 Lineage 分支直接以图中标题命名新 Resident，不再另行改名"""
    root_id = "resident_200000000020"
    created_resident_id = "resident_100000000020"
    renamed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_TARGET_RESOLVER",
        lambda resident_ids: {
            item: {
                "resident_id": item,
                "lineage_id": "",
            }
            for item in resident_ids
        },
    )
    created = lineage_domain.create_lineage({
        "title": "普通 Lineage",
        "nodes": [{
            "id": root_id,
            "title": "方案讨论",
        }],
        "edges": [],
        "leader_resident_id": root_id,
    }, "sample_admin")

    def create_named_fork(_source, payload, _actor, _lineage_source):
        return {
            "resident_id": created_resident_id,
            "label": payload["title"],
            "state": "active",
            "created_at": "2026-08-03T01:00:00Z",
        }

    monkeypatch.setattr(
        lineage_test_support,
        "LINEAGE_BRANCH_FORK_CREATOR",
        create_named_fork,
    )
    monkeypatch.setattr(
        lineage_test_support,
        "LINEAGE_NODE_RENAME_RESOLVER",
        lambda node, title, _actor: renamed.append((node["id"], title)),
    )
    result = lineage_domain.create_lineage_branch(
        created["lineage_id"],
        {
            "source_resident_id": root_id,
            "edge_id": "edge_regular_branch",
            "mode": "full",
            "title": "证据核对",
        },
        "sample_admin",
    )

    assert renamed == []
    assert result["created_resident"]["title"] == "证据核对"
    assert result["created_resident"]["status"] == "idle"
    assert result["title_sync"] == {"status": "synced", "error": ""}


def test_lineage_branch_validates_existing_graph_before_provider_fork(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """现有图含非规范 Resident ID 时，创建分支在调用 Provider 分叉前即报错"""
    resident_id = "resident_200000000021"
    created = lineage_domain.create_lineage({
        "title": "Broken Resident identity",
        "nodes": [{
            "id": resident_id,
            "title": "Main",
        }],
        "edges": [],
        "leader_resident_id": resident_id,
    }, "sample_admin")
    broken = lineage_domain._read_lineage(
        created["lineage_id"], actor="sample_admin"
    )
    broken["nodes"][0]["id"] = "fork_invalid"
    monkeypatch.setattr(lineage_domain, "_locked_branch_definition", lambda *args, **kwargs: broken)
    provider_calls: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "LINEAGE_BRANCH_FORK_CREATOR",
        lambda *_args, **_kwargs: provider_calls.append("called") or {},
    )

    with pytest.raises(ValueError, match="规范的 Resident ID"):
        lineage_domain.create_lineage_branch(
            created["lineage_id"],
            {
                "source_resident_id": resident_id,
                "edge_id": "edge_must_not_create_provider_fork",
                "mode": "full",
            },
            "sample_admin",
        )

    assert provider_calls == []


@pytest.mark.parametrize("terminal_status", ["completed", "cancelled"])
def test_absorption_edge_is_removed_when_target_turn_becomes_terminal(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    """目标 Resident 的轮次完成或取消后，对应吸收边从图中移除"""
    payload = sample_lineage_payload()
    payload["edges"] = []
    payload["program"] = {}
    created = lineage_domain.create_lineage(payload, "sample_admin")
    target_message_id = f"job_absorption_{terminal_status}"
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda _node, _prompt, _actor: {"message_id": target_message_id},
    )

    absorption = lineage_domain.absorb_lineage_node_context(
        created["lineage_id"],
        {
            "source_resident_id": "resident_0123456789ab",
            "target_resident_id": "resident_222222222222",
        },
        "sample_admin",
    )
    assert absorption["edge"]["absorption"]["target_message_id"] == target_message_id

    terminal = lineage_domain.handle_resident_message_terminal(
        "resident_222222222222",
        "sample_admin",
        target_message_id,
        lineage_id=created["lineage_id"],
        terminal_status=terminal_status,
    )

    assert terminal["removed_absorption_count"] == 1
    current = lineage_domain.get_lineage(created["lineage_id"], "sample_admin")
    assert all(edge["type"] != "absorption" for edge in current["edges"])


@pytest.mark.parametrize(
    ("terminal_status", "expected_removed"),
    [("cancelled", 1), ("failed", 0)],
)
def test_reconcile_removes_stale_cancelled_absorption_edge_but_keeps_failure(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
    expected_removed: int,
) -> None:
    """对账移除目标轮次已取消的吸收边，失败的吸收边保留"""
    payload = sample_lineage_payload()
    payload["edges"] = []
    payload["program"] = {}
    created = lineage_domain.create_lineage(payload, "sample_admin")
    target_message_id = f"job_stale_absorption_{terminal_status}"
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda _node, _prompt, _actor: {"message_id": target_message_id},
    )
    lineage_domain.absorb_lineage_node_context(
        created["lineage_id"],
        {
            "source_resident_id": "resident_0123456789ab",
            "target_resident_id": "resident_222222222222",
        },
        "sample_admin",
    )
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_INPUT_STATE_RESOLVER",
        lambda _node, job_id, _actor: {
            "status": terminal_status,
            'execution_id': job_id,
            "evidence": {},
        },
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda _node, _actor: False,
    )
    monkeypatch.setattr(
        lineage_test_support,
        "LATEST_COMPLETED_MESSAGE_RESOLVER",
        lambda _resident_id, _actor: {},
    )

    reconciled = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        "sample_admin",
    )

    assert reconciled["removed_absorption_count"] == expected_removed
    current = lineage_domain.get_lineage(created["lineage_id"], "sample_admin")
    absorption_edges = [
        edge for edge in current["edges"] if edge["type"] == "absorption"
    ]
    assert len(absorption_edges) == 1 - expected_removed


def test_runtime_persistence_ignores_resident_observations_without_compiling_authoring(
    lineage_store: Path, monkeypatch,
) -> None:
    """保存运行时状态不编译图定义，也不把 Resident 观测状态写回定义"""
    created = lineage_domain.create_lineage(
        sample_lineage_payload(),
        "sample_admin",
    )
    persisted = lineage_domain._read_lineage(
        created["lineage_id"], actor="sample_admin"
    )
    persisted["program"] = {
        "schema": "tfi.hatg-program.v2",
        "components": [{
            "id": "event_source_unrelated_flat",
            "kind": "event_source",
            "config": {"event_type": "manual.triggered"},
        }],
        "wires": [],
    }
    write_lineage_facts(persisted)
    runtime = lineage_domain._clone_json(persisted)
    runtime["nodes"][0]["status"] = "queued"

    with monkeypatch.context() as scope:
        scope.setattr(lineage_domain, "_compiled_program", lambda *a, **k: pytest.fail("runtime persistence compiled authoring"))
        lineage_domain._persist_lineage_runtime_state(persisted, runtime, "Runtime boundary test")

    saved = lineage_domain._read_lineage(
        created["lineage_id"], actor="sample_admin"
    )
    assert saved["revision"] == persisted["revision"]
    assert saved["runtime_revision"] == persisted["runtime_revision"]
    assert saved["nodes"][0]["status"] == "idle"
    assert saved["program"]["components"][0]["config"]["event_type"] == (
        "manual.triggered"
    )


def test_node_absorption_sends_plain_resource_reference_prompt_and_keeps_failure_auditable(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """节点吸收只发送源 Resident 的普通资源引用提示，投递失败记录在吸收边上"""
    source_resident_id = "resident_222222222222"
    target_resident_id = "resident_dddddddddddd"
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_TARGET_RESOLVER",
        lambda resident_ids: {
            item: {
                "resident_id": item,
                "lineage_id": "",
            }
            if item == source_resident_id
            else {
                "resident_id": item,
                "lineage_id": "",
            }
            for item in resident_ids
        },
    )
    payload = sample_lineage_payload()
    payload["nodes"][0]["id"] = source_resident_id
    payload["nodes"][1]["id"] = target_resident_id
    payload["edges"][0]["source"] = source_resident_id
    payload["edges"][0]["target"] = target_resident_id
    payload["leader_resident_id"] = source_resident_id
    created = lineage_domain.create_lineage(payload, "sample_admin")
    attempted_prompts: list[str] = []

    def fail_send(_node: dict, prompt: str, _actor: str) -> None:
        attempted_prompts.append(prompt)
        raise RuntimeError("target runtime unavailable")

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", fail_send)
    result = lineage_domain.absorb_lineage_node_context(
            created["lineage_id"],
            {
                "source_resident_id": source_resident_id,
                "target_resident_id": target_resident_id,
                "expected_revision": created["revision"],
            },
        "sample_admin",
    )

    assert result["reference"] == {
        "source_resident_id": source_resident_id,
    }
    assert source_resident_id in attempted_prompts[0]
    assert target_resident_id not in attempted_prompts[0]
    assert "普通统一资源引用" in attempted_prompts[0]
    assert extract_resource_addresses(attempted_prompts[0]) == [
        source_resident_id,
    ]
    for forbidden in (
        "resource_id.py",
        "resident_resource.py",
        "--actor",
        "--relative-to",
        "/home/",
        "/share/",
        "messages",
    ):
        assert forbidden not in attempted_prompts[0]
    assert "请比较两个交互方向" not in attempted_prompts[0]
    assert "研究结论：方向二更容易验收" not in attempted_prompts[0]
    assert result["delivery"]["status"] == "failed"
    assert result["delivery"]["error"] == "target runtime unavailable"
    assert result["edge"]["absorption"]["status"] == "failed"
    assert result["edge"]["absorption"]["source_resident_id"] == (
        source_resident_id
    )
    assert result["edge"]["absorption"]["target_resident_id"] == (
        target_resident_id
    )
    assert "lookup_command" not in result["edge"]["absorption"]
    persisted = lineage_domain.get_lineage(created["lineage_id"], "sample_admin")
    assert next(edge for edge in persisted["edges"] if edge["type"] == "absorption")["absorption"]["error"] == "target runtime unavailable"


def test_resident_completion_fact_and_async_lineage_notification(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最近完成消息引用按请求者区分且不含正文，完成和结束通知异步送达 Lineage"""
    monkeypatch.setattr(
        resident_workspace_domain,
        "_find_resident",
        lambda resident_id: {"resident_id": resident_id, "registration_state": "ready"},
    )
    monkeypatch.setattr(resident_workspace_domain, "ensure_resident_visible_to_actor", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(resident_workspace_domain, "_resident_visible_to_actor", lambda *_args, **_kwargs: True)
    repository = resident_conversation_stream.get_repository()
    from tests.cases.isa_test_support import publish_test_members

    lineage = lineage_domain.create_lineage({
        "title": "Conversation facts",
        "nodes": [{"id": "resident_0123456789ab", "title": "Worker"}],
    }, "sample_admin")
    publish_test_members(lineage)
    for message in [
        {"message_id": "user_earlier", "role": "user", "author_id": "sample_admin", "content_parts": [{"type": "text", "text": "earlier request"}], "created_at": "2025-12-31T23:58:00Z"},
        {"message_id": "msg_earlier", "role": "agent", "reply_to_message_id": "user_earlier", "content_parts": [{"type": "text", "text": "earlier output"}], "created_at": "2025-12-31T23:59:00Z"},
        {"message_id": "user_other", "role": "user", "author_id": "CASE-A", "content_parts": [{"type": "text", "text": "other request"}], "created_at": "2025-12-31T23:59:30Z"},
        {"message_id": "msg_other_user", "role": "agent", "reply_to_message_id": "user_other", "content_parts": [{"type": "text", "text": "other output"}], "created_at": "2025-12-31T23:59:45Z"},
        {"message_id": "user_success", "role": "user", "author_id": "sample_admin", "content_parts": [{"type": "text", "text": "successful request"}], "created_at": "2026-01-01T00:00:30Z"},
        {"message_id": "msg_success", "role": "agent", "reply_to_message_id": "user_success", "content_parts": [{"type": "text", "text": "private output"}], "created_at": "2026-01-01T00:01:00Z"},
    ]:
        repository.append_message("resident_0123456789ab", message)
    repository.set_input_state("resident_0123456789ab", "user_earlier", "completed")
    repository.set_input_state("resident_0123456789ab", "user_other", "completed")
    repository.set_input_state("resident_0123456789ab", "user_success", "completed")
    completion = resident_protocol.get_latest_completed_message_reference(
        "resident_0123456789ab",
        "sample_admin",
    )
    assert completion == {
        "resident_id": "resident_0123456789ab",
        "message_id": "user_success",
        "reply_message_id": "msg_success",
        "completed_at": "2026-01-01T00:01:00Z",
        "request_actor": "sample_admin",
    }
    references = resident_protocol.list_completed_message_references(
        "resident_0123456789ab",
        "sample_admin",
        limit=2,
        requested_by="sample_admin",
    )
    assert [item["message_id"] for item in references] == ["user_success", "user_earlier"]
    assert all("content" not in item for item in references)
    other_user_references = resident_protocol.list_completed_message_references(
        "resident_0123456789ab",
        "sample_admin",
        limit=2,
        requested_by="CASE-A",
    )
    assert [item["message_id"] for item in other_user_references] == ["user_other"]

    received: list[tuple[tuple, dict]] = []
    notified = threading.Event()

    def handler(*args, **kwargs) -> None:
        received.append((args, kwargs))
        notified.set()

    monkeypatch.setattr(
        resident_terminal_port,
        "_handler",
        handler,
    )
    resident_execution_domain._notify_lineage_message_completed(
        "resident_0123456789ab",
        "sample_admin",
        "job_success",
        completed_at="2026-01-01T00:01:00Z",
    )
    assert notified.wait(1.0)
    assert received == [
        (
                ("resident_0123456789ab", "sample_admin", "job_success"),
                {
                    "completed_at": "2026-01-01T00:01:00Z",
                "lineage_id": "",
                "business_ref": "",
            },
        )
    ]

    received.clear()
    notified.clear()
    resident_execution_domain._notify_lineage_message_terminal(
        "resident_0123456789ab",
        "sample_admin",
        "job_failed",
        terminal_status="failed",
        completed_at="2026-01-01T00:02:00Z",
    )
    assert notified.wait(1.0)
    assert received == [
        (
                ("resident_0123456789ab", "sample_admin", "job_failed"),
                {
                    "completed_at": "2026-01-01T00:02:00Z",
                "terminal_status": "failed",
                "lineage_id": "",
                "business_ref": "",
            },
        )
    ]


def test_completed_references_read_a_bounded_window_of_newest_inputs(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读取最近完成的输入只读最新的一小段历史

    Program 轮询为每个被订阅成员读取最近一次完成的输入。历史很长时，每次读取都带上限，只读最新的几条；最新的几条被发起人筛掉时，按递增的窗口继续往前读，直到凑够所需条数或读完历史。
    """
    # Program polling asks for the latest completion of every watched member;
    # a long history must not be read in full for one reference.
    monkeypatch.setattr(
        resident_workspace_domain,
        "_find_resident",
        lambda resident_id: {"resident_id": resident_id, "registration_state": "ready"},
    )
    monkeypatch.setattr(resident_workspace_domain, "ensure_resident_visible_to_actor", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(resident_workspace_domain, "_resident_visible_to_actor", lambda *_args, **_kwargs: True)
    repository = resident_conversation_stream.get_repository()
    from tests.cases.isa_test_support import publish_test_members

    resident_id = "resident_0123456789ab"
    publish_test_members(lineage_domain.create_lineage({
        "title": "Long history",
        "nodes": [{"id": resident_id, "title": "Worker"}],
    }, "sample_admin"))
    # Oldest three were asked by sample_admin, the newest nine by CASE-A.
    for index in range(12):
        author = "sample_admin" if index < 3 else "CASE-A"
        repository.append_message(resident_id, {
            "message_id": f"user_{index:02d}", "role": "user", "author_id": author,
            "content_parts": [{"type": "text", "text": f"request {index}"}],
            "created_at": f"2026-01-01T00:{index:02d}:00Z",
        })
        repository.append_message(resident_id, {
            "message_id": f"msg_{index:02d}", "role": "agent", "reply_to_message_id": f"user_{index:02d}",
            "content_parts": [{"type": "text", "text": f"output {index}"}],
            "created_at": f"2026-01-01T00:{index:02d}:30Z",
        })
        repository.set_input_state(resident_id, f"user_{index:02d}", "completed")
    limits: list[int] = []
    read_states = repository.input_states

    def bounded_read(*args, **kwargs):
        limits.append(int(kwargs.get("limit") or 0))
        return read_states(*args, **kwargs)

    monkeypatch.setattr(repository, "input_states", bounded_read)

    latest = resident_protocol.get_latest_completed_message_reference(resident_id, "sample_admin")
    assert latest["message_id"] == "user_11"
    assert limits and all(0 < limit < 12 for limit in limits)

    # Filtered newest inputs widen the window until enough references match.
    limits.clear()
    references = resident_protocol.list_completed_message_references(
        resident_id, "sample_admin", limit=2, requested_by="sample_admin",
    )
    assert [item["message_id"] for item in references] == ["user_02", "user_01"]
    assert limits and all(limits)
    assert limits == sorted(limits)


def test_human_notification_records_its_creator_and_owner_recipients(
    lineage_store: Path,
) -> None:
    """Lineage 人工通知记录创建者，并以 owner 作为接收人"""
    lineage = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")

    result = lineage_domain.create_lineage_human_notification(
        lineage["lineage_id"],
        {
            "title": "Need approval",
            "message": "Please inspect the result.",
            "notification_id": "human_notice_request_actor",
        },
        "sample_admin",
    )

    notification = result["notification"]
    assert notification["created_by"] == "sample_admin"
    assert notification["recipients"] == ["sample_admin"]
    assert "request_actor" not in notification


def test_failed_resident_message_references_are_actor_scoped_and_content_free(
    lineage_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败消息引用只列请求者本人的消息，只给失败码，不含正文和错误详情"""
    resident_id = "resident_0123456789ab"
    items = [
        {
            "message_id": "message_failed",
            "role": "user",
            "author_id": "sample_admin",
            "content_parts": [{"type": "text", "text": "private prompt"}],
        },
        {
            "message_id": "message_other",
            "role": "user",
            "author_id": "another-user",
            "content_parts": [{"type": "text", "text": "other prompt"}],
        },
    ]
    repository = resident_conversation_stream.get_repository()
    from tests.cases.isa_test_support import publish_test_members

    lineage = lineage_domain.create_lineage({
        "title": "Conversation facts",
        "nodes": [{"id": resident_id, "title": "Worker"}],
    }, "sample_admin")
    publish_test_members(lineage)
    for item in items:
        repository.append_message(resident_id, item)
    repository.set_input_state(
        resident_id,
        "message_failed",
        "failed",
        error={"code": "timeout", "message": "private failure details"},
    )
    repository.set_input_state(
        resident_id,
        "message_other",
        "failed",
        error={"code": "network", "message": "private failure details"},
    )
    monkeypatch.setattr(
        resident_workspace_domain,
        "_find_resident",
        lambda _resident_id: {"resident_id": resident_id, "registration_state": "ready"},
    )
    monkeypatch.setattr(
        resident_workspace_domain,
        "_resident_visible_to_actor",
        lambda _record, _actor: True,
    )
    references = resident_protocol.list_failed_message_references(
        resident_id,
        "sample_admin",
        limit=20,
        requested_by="sample_admin",
    )
    assert len(references) == 1
    assert references[0]["resident_id"] == resident_id
    assert references[0]["message_id"] == "message_failed"
    assert references[0]["request_actor"] == "sample_admin"
    assert references[0]["failure_code"] == "timeout"
    assert all("error" not in item and "content" not in item for item in references)


def test_fork_resident_rejects_legacy_implicit_dispatch_parameters(monkeypatch, subtests, isolated_portal_case):
    """ForkResident 拒绝 launch_after_fork、prompt、message 等旧的隐式派发参数"""
    for field, value in [
        ("launch_after_fork", True),
        ("prompt", "Legacy implicit first task."),
        ("message", {"role": "user", "content": "Legacy task."}),
    ]:
        with subtests.test(field=field, value=value), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                sent: list[str] = []
                monkeypatch.setattr(
                    lineage_domain,
                    "create_lineage_branch",
                    lambda *_args, **_kwargs: {
                        "created": True,
                        "created_resident": {"id": "resident_111111111111"},
                        "lineage": {"lineage_id": "lineage_111111111111"},
                    },
                )
                monkeypatch.setattr(
                    lineage_domain,
                    "_send_node_message",
                    lambda _node, prompt, _actor, **_kwargs: (
                        sent.append(prompt)
                        or {"message": {"message_id": "message_prompt"}}
                    ),
                )

                with pytest.raises(ValueError, match="只创建普通 Resident"):
                    lineage_domain.fork_lineage_resident(
                        "lineage_111111111111",
                        {
                            "source": "resident_222222222222",
                            field: value,
                        },
                        "sample_admin",
                    )

                assert sent == []


def test_fork_resident_requires_a_history_selector_for_message_mode(monkeypatch, subtests, isolated_portal_case):
    """ForkResident 按消息分叉时必须给出只含非空 message_id 的历史截止点"""
    for selector in [
        None,
        {},
        "Send a new task",
        {"message_id": ""},
        {"message_id": "  "},
        {"message_id": 123},
        {"role": "user", "content": "Send a new task"},
        {"message_id": "message_cutoff", "content": "Send a new task"},
    ]:
        with subtests.test(selector=selector), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                monkeypatch.setattr(
                    lineage_domain, "create_lineage_branch",
                    lambda *_args, **_kwargs: pytest.fail("Invalid selector must not create a Resident"),
                )
                with pytest.raises(ValueError, match="历史截止点选择器"):
                    lineage_domain.fork_lineage_resident(
                        "lineage_111111111111",
                        {
                            "source": "resident_222222222222",
                            "fork_mode": "message",
                            "message": selector,
                        },
                        "sample_admin",
                    )


@pytest.mark.parametrize("failure", ["conflict", "invalid"])
def test_a_fork_that_cannot_attach_its_child_deletes_the_child(
    lineage_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """ForkResident 无法挂接子节点时删除已创建的子 Resident

    Fork 在创建子 Resident 后、写入继承边前失败（冲突或无效）时，删除该子 Resident 并原样报告错误，失败的 Fork 不留下额外节点。

    An HTTP error from ForkResident leaves no child behind (TAU resident.fork-handoff)."""
    lineage = lineage_domain.create_lineage(sample_lineage_payload(), "sample_admin")
    source = lineage["nodes"][0]["id"]
    monkeypatch.setattr(lineage_test_support, "LINEAGE_BRANCH_FORK_CREATOR", lambda *_args: {
        "resident_id": "resident_700000000001", "label": "分支", "state": "active"})
    error = (
        lineage_domain.LineageConflictError("edges 已被另一条指令删除")
        if failure == "conflict" else ValueError("Lineage 图无效")
    )
    persisted = lineage_domain._persist_lineage_state

    def persist(previous, current, reason, **kwargs):
        if reason == "创建轨迹分支":
            raise error
        return persisted(previous, current, reason, **kwargs)

    discarded = []
    monkeypatch.setattr(lineage_domain, "_persist_lineage_state", persist)
    monkeypatch.setattr(lineage_domain, "delete_lineage_graph_objects",
                        lambda lineage_id, payload, actor: discarded.append((lineage_id, payload, actor)))
    with pytest.raises(type(error)):
        lineage_domain.create_lineage_branch(lineage["lineage_id"], {
            "source_resident_id": source, "edge_id": "edge_failed_branch", "mode": "full",
        }, "sample_admin")
    assert discarded == [(lineage["lineage_id"], {"resident_ids": ["resident_700000000001"]}, "sample_admin")]
    assert "edge_failed_branch" not in {
        edge["id"] for edge in lineage_domain.get_lineage(lineage["lineage_id"], "sample_admin")["edges"]}
