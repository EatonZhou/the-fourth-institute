""":importance: P3"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from knowledge_board_lan import knowledge_backend, knowledge_details, knowledge_source

from management_portal import resource_views
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources import ResourceService
from management_portal.resources.providers import knowledge
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.registry_support import write_projects


def test_knowledge_resolver_forwards_resource_actor_contracts(subtests, isolated_portal_case):
    """知识资源的解析、读取视图和注册都转发调用者，注册使用预留 ID"""
    with subtests.test(contract='test_knowledge_resolver_forwards_resource_actor'), isolated_portal_case() as case_path:
        resident_id = "resident_0123456789ab"
        experiment_id = "experiment_0123456789ab"
        backend_resolution = {
            "schema": "tfi.resource_resolution.v2",
            "address": experiment_id,
            "root_id": experiment_id,
            "resource_type": "experiment",
            "target_kind": "knowledge",
            "record": {"kb_id": experiment_id, "type": "experiment"},
            "resolved_path": "/tmp/experiment.md",
            "exists": True,
        }
        service = mock.Mock()
        captured: dict[str, object] = {}

        def register_resolver(_types, resolver, **_options) -> None:
            captured["resolver"] = resolver

        service.register_resolver.side_effect = register_resolver
        with mock.patch.object(
            knowledge_backend,
            "resolve_knowledge_address",
            return_value=backend_resolution,
        ) as resolve:
            knowledge.install_resolver(service)
            result = captured["resolver"](
                experiment_id,
                actor=resident_id,
            )

        assert result["record"]["resource_id"] == experiment_id
        resolve.assert_called_once_with(experiment_id, actor=resident_id)
    with subtests.test(contract='test_knowledge_view_preserves_resource_read_scope_and_actor'), isolated_portal_case() as case_path:
        resident_id = "resident_0123456789ab"
        skill_id = "skill_0123456789ab"
        service = mock.Mock()
        service.get.return_value = {
            "resource_id": skill_id,
            "resource_type": "skill",
            "title": "Shared skill",
            "summary": "",
            "facets": ["knowledge", "skill"],
            "attributes": {"type": "skill", "read_scope": "global"},
        }

        result = resource_views.knowledge_item(
            skill_id,
            service=service,
            actor=resident_id,
        )

        assert result["kb_id"] == skill_id
        assert result["read_scope"] == "global"
        service.get.assert_called_once_with(skill_id, actor=resident_id)
    with subtests.test(contract='test_knowledge_types_expose_update_and_delete_mutations'), isolated_portal_case() as case_path:
        service = ResourceService()
        knowledge.install_mutation_provider(service)

        capabilities = service.capabilities("information")

        assert capabilities["registerable"] is True
        assert capabilities["updatable"] is True
        assert capabilities["content_updatable"] is True
        assert capabilities["unregisterable"] is True

        skill_capabilities = service.capabilities("skill")
        assert skill_capabilities["registerable"] is False
        assert skill_capabilities["updatable"] is True
        assert skill_capabilities["content_updatable"] is True
        assert skill_capabilities["unregisterable"] is True
    with subtests.test(contract='test_knowledge_registration_forwards_reserved_identity_and_actor'), isolated_portal_case() as case_path:
        resident_id = "resident_0123456789ab"
        decision_id = "decision_0123456789ab"
        service = mock.Mock()
        captured: dict[str, object] = {}

        def register_mutation_provider(resource_type, **handlers) -> None:
            if resource_type == "decision":
                captured.update(handlers)

        service.register_mutation_provider.side_effect = (
            register_mutation_provider
        )
        knowledge.install_mutation_provider(service)
        payload = {
            "project_id": "AI员工",
            "title": "Resident 资源访问",
            "body": "正文",
        }
        with mock.patch.object(
            knowledge_backend,
            "register_item",
            return_value={"kb_id": decision_id, "created": True},
        ) as register:
            created = captured["register"](
                payload,
                actor=resident_id,
                resource_id=decision_id,
            )

        assert created == {"created": True, "resource_id": decision_id}
        service.validate_id.assert_called_once_with(decision_id, "decision")
        register.assert_called_once_with(
            "decision",
            decision_id,
            payload,
            actor=resident_id,
        )


def test_scoped_project_viewer_can_resolve_and_find_exact_knowledge_id(
    tmp_path,
) -> None:
    """只有 Project 查看权限的用户也能解析、读取并按精确 ID 检索知识条目"""
    information_id = "information_0123456789ab"
    write_projects("schema_version: 1\nprojects:\n  - id: RobotWorker\n")
    access = PortalAccessStore()
    access.upsert_user(
        "engineer",
        actor="CASE-A",

    )
    content_path = tmp_path / "information.md"
    content_path.write_text("# YOLO training metrics\n", encoding="utf-8")
    index = mock.Mock()
    index.get_resource.return_value = {
        "resource_id": information_id,
        "resource_type": "information",
        "title": "YOLO training metrics",
        "summary": "Training compute and branch metrics",
        "facets": ["knowledge"],
        "attributes": {
            "project_ids": ["RobotWorker"],
            "status": "active",
        },
    }
    service = ResourceService(index_provider=index, portal_access=access)
    with mock.patch.object(
        knowledge_backend,
        "resolve_item_address",
        return_value={
            "kb_id": information_id,
            "address": information_id,
            "type": "information",
            "title": "YOLO training metrics",
            "project": "RobotWorker",
            "path": str(content_path),
            "selected_path": str(content_path),
            "files": [],
        },
    ):
        knowledge.install_resolver(service)
        resolved = service.resolve(information_id, actor="engineer")
        content, _content_type, _content_resolution = service.read_content(
            information_id,
            actor="engineer",
        )
        result = service.query(q=information_id, actor="engineer")

    assert resolved["record"]["project_ids"] == ["RobotWorker"]
    assert content == b"# YOLO training metrics\n"
    assert result["total"] == 1
    assert result["resources"][0]["resource_id"] == information_id
    index.query.assert_not_called()


def test_knowledge_mutations_forward_actor_and_address() -> None:
    """知识资源的更新和删除把调用者和文件地址原样转给知识后端"""
    resident_id = "resident_0123456789ab"
    information_id = "information_0123456789ab"
    service = mock.Mock()
    captured: dict[str, object] = {}

    def register_mutation_provider(resource_type, **handlers) -> None:
        if resource_type == "information":
            captured.update(handlers)

    service.register_mutation_provider.side_effect = (
        register_mutation_provider
    )
    knowledge.install_mutation_provider(service)
    with (
        mock.patch.object(
            knowledge_backend,
            "update_item_text",
            return_value={
                "kb_id": information_id,
                "path": "/tmp/information.md",
            },
        ) as update,
        mock.patch.object(
            knowledge_backend,
            "delete_item",
            return_value={
                "kb_id": information_id,
                "deleted": True,
            },
        ) as delete,
    ):
        updated = captured["update"](
            information_id,
            {
                "address": f"{information_id}/file/notes.md",
                "text": "updated",
            },
            actor=resident_id,
        )
        deleted = captured["unregister"](
            information_id,
            actor=resident_id,
        )

    assert updated["resource_id"] == information_id
    assert deleted["resource_id"] == information_id
    update.assert_called_once_with(
        f"{information_id}/file/notes.md",
        "updated",
        actor=resident_id,
    )
    delete.assert_called_once_with(
        information_id,
        actor=resident_id,
    )


def test_skill_text_update_forwards_actor_to_skill_backend() -> None:
    """skill 文本更新把调用者身份转给 skill 后端保存"""
    skill_id = "skill_0123456789ab"
    actor = "sample_reader"
    with (
        mock.patch.object(
            knowledge_backend,
            "resolve_item_address",
            return_value={
                "kb_id": skill_id,
                "address": skill_id,
                "type": "skill",
                "project_ids": ["AI员工"],
                "selected_path": "/share/Skills/example/SKILL.md",
                "selected_file_id": "primary",
            },
        ),
        mock.patch.object(
            knowledge_backend.skill_backend,
            "save_address_text",
            return_value=(
                200,
                {
                    "skill_id": skill_id,
                    "path": "/share/Skills/example/SKILL.md",
                    "mtime": 123,
                },
            ),
        ) as save,
    ):
        updated = knowledge_backend.update_item_text(
            skill_id,
            "# Updated\n",
            actor=actor,
        )

    assert updated["kb_id"] == skill_id
    assert updated["project_ids"] == ["AI员工"]
    save.assert_called_once_with(
        skill_id,
        "# Updated\n",
        actor=actor,
        expected_version=None,
    )


@pytest.mark.parametrize("resource_type", ["concept", "insight", "plan"])
def test_knowledge_mutations_sync_canonical_project_and_creator_permissions(
    tmp_path, monkeypatch, permissions, resource_type,
) -> None:
    """创建、更新、删除知识资源时同步它在权限树中的 Project 位置，权限只随位置而来

    创建后知识资源在权限树中位于所属 Project 之下，Project 内的 Resident 对它为 admin，Project 外的创建者只能读；
    更新保持位置和权限；删除后它离开权限树，Project 内的 Resident 也不再是 admin。

    :importance: P2"""

    actor = "resident_aaaaaaaaaaaa"
    project_root = tmp_path / "AI员工"
    (project_root / "knowledge_base").mkdir(parents=True)
    monkeypatch.setattr(
        knowledge_backend, "_parse_projects_yaml",
        lambda: [{"id": "AI员工", "path": str(project_root)}],
    )
    monkeypatch.setattr(
        knowledge_source, "_parse_projects_yaml",
        lambda: [{"id": "AI员工", "path": str(project_root)}],
    )
    monkeypatch.setattr(
        knowledge_backend, "_get_tag_config",
        lambda: ([resource_type], {resource_type: resource_type},
                 {resource_type: resource_type}, "test"),
    )
    monkeypatch.setattr(
        knowledge_source, "_get_tag_config",
        lambda: ([resource_type], {resource_type: resource_type},
                 {resource_type: resource_type}, "test"),
    )
    member, other_project = "resident_bbbbbbbbbbbb", "RobotLab"
    permissions.place(Fact("project", "AI员工"))
    permissions.place(Fact("lineage", "lineage_aaaaaaaaaaaa", {"project": "AI员工"}))
    permissions.place(Fact("resident", member, {"lineage": "lineage_aaaaaaaaaaaa"}))
    # The creator is a live Resident of another Project: creating gives it no right on the entry.
    permissions.place(Fact("project", other_project))
    permissions.place(Fact("lineage", "lineage_bbbbbbbbbbbb", {"project": other_project}))
    permissions.place(Fact("resident", actor, {"lineage": "lineage_bbbbbbbbbbbb"}))
    store = permissions.store

    def placed(resource_id):
        path = store.paths("R", [resource_id]).get(resource_id)
        return path[-2:] if path else None

    index = mock.Mock()
    service = ResourceService(index_provider=index)
    knowledge.install_mutation_provider(service)
    # Access admission is separate; exercise real mutation + permission lifecycle.
    notify = mock.Mock(return_value=None)
    monkeypatch.setattr(service, "_notify_mutation", notify)

    created = service.register(resource_type, {
        "project_id": "AI员工", "title": "Canonical ownership", "body": "Initial body",
    }, actor=actor)
    index.project_registered.assert_called_once_with(resource_type, created)
    resource_id = created["resource_id"]
    assert created["project_ids"] == ["AI员工"]
    path = Path(created["path"])
    assert placed(resource_id) == ["AI员工", resource_id]
    assert permissions.holds(member, "@admin", resource_id)
    assert permissions.holds(actor, "@viewer", resource_id) and not permissions.holds(actor, "@contributor", resource_id)

    # The resolved root record is authoritative for subsequent mutations.
    record = {"kb_id": resource_id, "type": resource_type, "project": "AI员工",
              "project_ids": ["AI员工"], "address": resource_id,
              "path": str(path), "selected_path": str(path), "selected_file_id": "primary"}
    monkeypatch.setattr(knowledge_backend, "resolve_item_address", lambda *args, **kwargs: record)
    monkeypatch.setattr(knowledge_backend, "resolve_item_record", lambda *args, **kwargs: record)
    updated = service.update(resource_id, {
        "text": path.read_text(encoding="utf-8") + "\nUpdated body\n",
        "expected_version": {"number": 1},
    }, actor=actor)
    assert updated["project_ids"] == created["project_ids"]
    assert knowledge_source._yaml_front_matter(path.read_text(encoding="utf-8"))["version"]["number"] == 2
    assert placed(resource_id) == ["AI员工", resource_id]
    assert permissions.holds(member, "@admin", resource_id)

    deleted = service.unregister(resource_id, actor=actor)
    assert deleted["project_ids"] == ["AI员工"]
    assert not path.exists()
    assert Path(deleted["trash_path"]).exists()
    assert placed(resource_id) is None
    # Only the everyone-viewer on 共享 is left; the Project's admin left with the element.
    assert permissions.holds(member, "@viewer", resource_id) and not permissions.holds(member, "@contributor", resource_id)
    assert [call.kwargs["kind"] for call in notify.call_args_list] == ["updated", "deleted"]


def test_knowledge_update_rejects_stale_version_before_writing(tmp_path) -> None:
    """知识条目更新的期望版本过旧时报版本冲突，文件保持原样"""
    information_id = "information_0123456789ab"
    path = tmp_path / "information.md"
    original = """---
kb_id: information_0123456789ab
type: information
project: AI员工
version:
  number: 3
  created_at: '2026-09-01'
  updated_at: '2026-09-02T10:00:00Z'
---
# Original
"""
    path.write_text(original, encoding="utf-8")
    with mock.patch.object(
        knowledge_backend,
        "resolve_item_address",
        return_value={
            "kb_id": information_id,
            "address": information_id,
            "type": "information",
            "path": str(path),
            "selected_path": str(path),
            "selected_file_id": "primary",
        },
    ):
        with pytest.raises(knowledge_backend.KnowledgeVersionConflict) as raised:
            knowledge_backend.update_item_text(
                information_id,
                "# Stale edit\n",
                actor="CASE-A",
                expected_version={"number": 2},
            )

    assert raised.value.current_version["number"] == 3
    assert path.read_text(encoding="utf-8") == original


def test_register_knowledge_item_writes_canonical_versioned_markdown(tmp_path, monkeypatch, subtests, isolated_portal_case):
    """注册知识条目写出带版本号、创建者和可选 metaness 的 Markdown，重复注册不再新建"""
    for metaness in [None, 1, 3, 10]:
        with subtests.test(metaness=metaness), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                tmp_path = case_path
                project_root = tmp_path / "AI员工"
                (project_root / "knowledge_base").mkdir(parents=True)
                monkeypatch.setattr(
                    knowledge_backend,
                    "_parse_projects_yaml",
                    lambda: [{"id": "AI员工", "path": str(project_root)}],
                )
                monkeypatch.setattr(
                    knowledge_source,
                    "_parse_projects_yaml",
                    lambda: [{"id": "AI员工", "path": str(project_root)}],
                )
                monkeypatch.setattr(
                    knowledge_backend,
                    "_get_tag_config",
                    lambda: (
                        ["decision"],
                        {"decision": "decision"},
                        {"decision": "decision"},
                        "test",
                    ),
                )
                monkeypatch.setattr(
                    knowledge_source,
                    "_get_tag_config",
                    lambda: (
                        ["decision"],
                        {"decision": "decision"},
                        {"decision": "decision"},
                        "test",
                    ),
                )
                payload = {
                    "project_id": "AI员工",
                    "title": "Resident 资源访问",
                    "body": "所有 active Resident 都可以创建普通资源。",
                    "importance": 9,
                }
                if metaness is not None:
                    payload["metaness"] = metaness

                created = knowledge_backend.register_item(
                    "decision",
                    "decision_0123456789ab",
                    payload,
                    actor="resident_aaaaaaaaaaaa",
                )
                repeated = knowledge_backend.register_item(
                    "decision",
                    "decision_0123456789ab",
                    payload,
                    actor="resident_aaaaaaaaaaaa",
                )

                path = project_root / "knowledge_base" / "decision" / created["path"].split("/")[-1]
                text = path.read_text(encoding="utf-8")
                metadata = knowledge_source._yaml_front_matter(text)
                assert created["created"] is True
                assert repeated["created"] is False
                assert created["project_ids"] == ["AI员工"]
                assert repeated["project_ids"] == ["AI员工"]
                assert metadata["kb_id"] == "decision_0123456789ab"
                assert metadata["project"] == "AI员工"
                assert metadata.get("metaness") == metaness
                assert metadata["version"]["number"] == 1
                assert metadata["version"]["created_by"]["agent"] == (
                    "resident_aaaaaaaaaaaa"
                )
                assert text.endswith(
                    "# Resident 资源访问\n\n所有 active Resident 都可以创建普通资源。\n"
                )


def test_register_knowledge_rejects_invalid_optional_metaness(subtests, isolated_portal_case):
    """注册知识条目时拒绝不是 1 到 10 整数的 metaness，显式传空值也拒绝"""
    for metaness in [True, False, 0, 11, 2.5, "3", None]:
        with subtests.test(metaness=metaness), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match="metaness"):
                knowledge_backend.register_item("plan", "plan_0123456789ab", {
                    "project_id": "AI员工", "title": "Significant change", "body": "Context and benefit.", "metaness": metaness,
                })


@pytest.mark.parametrize("kind", ["todo", "review"])
def test_indexed_knowledge_documents_open_with_real_content_and_keep_paper_reviews_separate(tmp_path, monkeypatch, kind):
    """已索引的 todo 和 review 知识文档打开时显示原文，论文的 review 对象仍走论文页面

    :importance: P2"""
    from management_portal import resource_index_backend, resource_identity_backend, resource_views
    resource_id = kind + "_0123456789ab"
    root = tmp_path / "Example" / "knowledge_base" / kind
    root.mkdir(parents=True)
    path = root / (resource_id + ".md")
    path.write_text(f"---\n{'id' if kind == 'todo' else 'kb_id'}: {resource_id}\ntype: {kind}\nproject: Example\n---\n# Preserved knowledge\n\nOriginal document.\n")
    monkeypatch.setattr(knowledge_backend, "_parse_projects_yaml", lambda: [
        {"id": "Example", "status": "active", "path": str(tmp_path / "Example")},
    ])
    monkeypatch.setattr(knowledge_source, "_parse_projects_yaml", lambda: [
        {"id": "Example", "status": "active", "path": str(tmp_path / "Example")},
    ])
    record = resource_index_backend.ProjectKnowledgeAdapter().scan_changes([str(path)], [], {}).upserts[0]
    index = mock.Mock()
    index.get_resource.return_value = record
    index.indexed_types.return_value = {kind}
    service = ResourceService(index_provider=index)
    from management_portal.resources.providers import indexed
    indexed.install_resolver(service)
    monkeypatch.setattr(knowledge_backend, "get_resource_service", lambda: service)
    monkeypatch.setattr(knowledge_source, "get_resource_service", lambda: service)
    monkeypatch.setattr(knowledge_details, "get_resource_service", lambda: service)
    detail = resource_views.knowledge_detail(resource_id, service=service, actor="tester")
    assert detail["kind"] == "markdown"
    assert detail["text"] == path.read_text()
    assert detail["kb_id"] == resource_id
    assert knowledge_backend.knowledge_resource_id_for_path(str(path)) == resource_id
    resolved = service.resolve(resource_id, actor="tester")
    assert resolved["portal_url"] == f"/resource/knowledge?item={resource_id}"
    # Paper's review objects lack the knowledge facet and retain their own projection.
    if kind == "review":
        paper_record = {**record, "facets": ["paper", "review"], "url": "/paper?paper=paper_0123456789ab"}
        index.get_resource.return_value = paper_record
        paper = resource_identity_backend.resolve_indexed_resource_address(resource_id, actor="tester", resource_service=service)
        assert "detail" not in paper
        assert paper["portal_url"] == paper_record["url"]
