""":module: resident.conversation"""
from management_portal import discussion_residents, application_bindings
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import knowledge_item_chat_backend as knowledge
from management_portal import resident_handoff as invocation, resident_workspace_adapter as workspace
from management_portal.portal_ui_state import PortalUIStateStore


def test_shared_discussions_reuse_per_user_identity_and_never_create_a_lineage(monkeypatch, isolated_portal_case):
    """共享讨论按用户与资源复用身份且不新建 Lineage"""
    with isolated_portal_case():
        store = PortalUIStateStore()
        records = {}
        created = []
        lineage_id = "lineage_111111111111"

        def create_lineage_resident(target_lineage_id, payload, actor):
            # Invocation Residents are created in the bound Lineage with a stable creation key.
            assert payload["creation_key"]
            created.append(({**payload, "lineage_id": target_lineage_id}, actor))
            resident_id = f"resident_{len(created):012x}"
            record = {"resident_id": resident_id, "lineage_id": target_lineage_id}
            records[resident_id] = record
            return {"resident": record}

        service = SimpleNamespace(
            resident=SimpleNamespace(get_resident=lambda rid, actor: records[rid]),
            lineage=SimpleNamespace(get_lineage=lambda rid, actor: {"lineage_id": rid},
                                    create_lineage_resident=create_lineage_resident),
        )
        monkeypatch.setattr(invocation, "get_society_service", lambda: service)
        monkeypatch.setattr(invocation, "get_portal_ui_state_store", lambda: store)
        monkeypatch.setattr(invocation, "_configure_invocation_resident", lambda service, record, actor, **kwargs: record)

        def ensure(actor="alice", item="article_a"):
            return invocation.ensure_invocation_resident(actor=actor, resident_scope=f"resource-chat:{actor}:{item}", lineage_binding=lineage_id)

        assert invocation.lookup_invocation_resident(actor="alice", resident_scope="resource-chat:alice:article_a") == {}
        assert not store.list("alice", invocation.INVOCATION_ASSOCIATION_NAMESPACE)
        with ThreadPoolExecutor(max_workers=4) as executor:
            identities = list(executor.map(lambda _: ensure(), range(8)))
        assert len({item["resident_id"] for item in identities}) == 1
        assert len(created) == 1
        assert ensure("bob")["resident_id"] != identities[0]["resident_id"]
        assert ensure(item="article_b")["resident_id"] != identities[0]["resident_id"]
        assert {payload["lineage_id"] for payload, actor in created} == {lineage_id}
        assert all(not store.list(actor, "resident-invocation-lineages") for actor in ("alice", "bob"))
        # A moved Resident retains its identity and messages; a stale designation fails closed.
        records[identities[0]["resident_id"]]["lineage_id"] = "lineage_222222222222"
        assert invocation.lookup_invocation_resident(actor="alice", resident_scope="resource-chat:alice:article_a")["lineage_id"] == "lineage_222222222222"
        with pytest.raises(ValueError, match="different Lineage"):
            ensure()
        assert len(created) == 3


def test_discussion_lineages_use_explicit_configuration_and_reads_are_authorized(monkeypatch):
    """讨论 Lineage 由显式配置解析且读取检查调用者权限"""
    from types import SimpleNamespace

    from management_portal import application_targets
    from management_portal.hatg.catalog import instruction_catalog

    assert not {"GetDiscussionLineages", "PublishDiscussionLineage"} & set(instruction_catalog())
    duties = {}

    def configured(resource_type):
        if resource_type not in duties:
            raise application_targets.ApplicationUnconfigured(resource_type)
        return duties[resource_type]

    monkeypatch.setattr(application_targets, "resource_chat_lineage", configured)
    calls = Mock()
    monkeypatch.setattr(application_bindings, "get_society_service", lambda: SimpleNamespace(lineage=calls))
    with pytest.raises(application_targets.ApplicationUnconfigured, match="article"):
        application_bindings.resolve_application_lineage(actor="alice", application="resource-chat:article")
    calls.get_lineage.assert_not_called()
    duties.update({"article": "lineage_111111111111", "project": "lineage_222222222222"})
    for scope, target in (("resource-chat:article", "lineage_111111111111"), ("project-chat", "lineage_222222222222")):
        for actor in ("alice", "bob"):
            assert application_bindings.resolve_application_lineage(actor=actor, application=scope) == target
            calls.get_lineage.assert_called_with(target, actor)
    calls.get_lineage.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError, match="revoked"):
        application_bindings.resolve_application_lineage(actor="bob", application="resource-chat:article")


@pytest.mark.parametrize("surface", ["paper", "project"])
def test_workspace_reads_are_non_creating_and_first_send_preparation_is_explicit(monkeypatch, surface):
    """讨论工作区读取不创建身份且首次发送须显式准备"""
    calls = []
    monkeypatch.setattr(workspace, "_context_for", lambda *args, **kwargs: {"title": "Chat"})
    def ensure(actor, surface, key, context, *, create):
        calls.append(create)
        return {"resident_id": "resident_111111111111", "lineage_id": "lineage_111111111111"} if create else {}
    monkeypatch.setattr(workspace, "_ensure", ensure)
    for _ in range(2):
        result = workspace.get_workspace("alice", surface, "item")
        assert result["binding_state"] == "unbound"
        assert result["chat_workspace"]["composer"]["can_send"]
    with pytest.raises(ValueError, match="message required"):
        workspace.prepare_workspace("alice", {"surface": surface, "message": "  "})
    result = workspace.prepare_workspace("alice", {"surface": surface, "workspace_key": "item", "message": "hello"})
    assert result["binding_state"] == "bound"
    assert calls == [False, False, True]


@pytest.mark.parametrize("surface", ["paper", "project"])
def test_workspace_prepare_reuses_bound_resident_and_keeps_project_in_metadata(monkeypatch, isolated_portal_case, surface):
    """Paper 与 Project 首次准备复用讨论绑定并保留项目来源

    首次准备经过实际 Resident 调用接口创建绑定，重复准备与只读刷新复用同一成员；项目来源保存在 metadata，不传入未声明的 Resident 创建参数。

    :importance: P1
    :design: decision_f560803947c6
    :design-point:
    """
    from management_portal.workbench_projections import native

    with isolated_portal_case():
        store = PortalUIStateStore()
        records = {}
        lineage_id = "lineage_111111111111"
        resident_id = "resident_111111111111"
        context = {"title": "Research Chat", "project_id": "test-project", "paper_id": "paper_111111111111"}

        def create_resident(*_args, **_kwargs):
            assert not records, "Repeated prepare must reuse its existing discussion"
            records[resident_id] = {"resident_id": resident_id, "lineage_id": lineage_id}
            return {"resident": records[resident_id]}

        def binding(*_args, **_kwargs):
            if not records:
                create_resident()
            return records[resident_id]

        service = SimpleNamespace(
            resident=SimpleNamespace(get_resident=lambda rid, actor: records[rid]),
            lineage=SimpleNamespace(get_lineage=lambda rid, actor: {"lineage_id": rid},
                                    create_lineage_resident=create_resident),
        )
        monkeypatch.setattr(invocation, "get_society_service", lambda: service)
        monkeypatch.setattr(invocation, "get_portal_ui_state_store", lambda: store)
        monkeypatch.setattr(invocation, "_configure_invocation_resident", lambda service, record, actor, **kwargs: record)
        monkeypatch.setattr(workspace, "_context_for", lambda *args, **kwargs: context)
        monkeypatch.setattr(workspace, "_cwd", lambda context: "")
        monkeypatch.setattr(workspace, "_runtime_context", lambda **kwargs: "Paper context")
        monkeypatch.setattr(native, "binding", binding)
        monkeypatch.setattr(native, "lookup", lambda *args, **kwargs: records.get(resident_id, {}))
        monkeypatch.setattr(native, "get_service", lambda: SimpleNamespace(
            builtin_application=lambda name, actor: {"workbench_id": "workbench_111111111111"}))
        monkeypatch.setattr(discussion_residents, "resolve_application_lineage", lambda **kwargs: lineage_id)

        assert workspace.get_workspace("alice", surface, "item")["binding_state"] == "unbound"
        assert not records
        for _ in range(2):
            prepared = workspace.prepare_workspace("alice", {
                "surface": surface, "workspace_key": "item", "message": "Compare", "context": context,
            })
            assert prepared["resident_id"] == resident_id
            assert prepared["binding_state"] == "bound"
        assert workspace.get_workspace("alice", surface, "item")["resident_id"] == resident_id
        association = store.get("alice", invocation.INVOCATION_ASSOCIATION_NAMESPACE, f"surface-chat:alice:{surface}:item")
        assert association["value"]["metadata"]["project_id"] == "test-project"
        assert len(records) == 1


def test_resource_reads_and_first_send_revalidate_resource_access(tmp_path, monkeypatch):
    """资源讨论读取及首次发送重新检查资源访问权限"""
    resource = Mock()
    source = tmp_path / "article.md"
    source.write_text("# Article")
    resource.read_content.return_value = (b"# Article", "text/markdown", {
        "record": {"resource_id": "article_111111111111", "resource_type": "article"}, "resolved_path": str(source)})
    backend = knowledge.KnowledgeItemChatBackend(resource_service=resource)
    create = Mock(return_value={"resident_id": "resident_111111111111", "lineage_id": "lineage_111111111111"})
    monkeypatch.setattr(discussion_residents, "resolve_resource_lineage", lambda **kwargs: "lineage_111111111111")
    monkeypatch.setattr(discussion_residents.resident_handoff, "ensure_invocation_resident", create)
    monkeypatch.setattr(discussion_residents.resident_handoff, "lookup_invocation_resident", lambda **kwargs: {})
    assert backend.get_state("alice", "article_111111111111")["binding_state"] == "unbound"
    create.assert_not_called()
    resource.read_content.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError, match="revoked"):
        backend.prepare("alice", "article_111111111111", "hello")
    create.assert_not_called()


def test_wall_discussion_separates_users_in_the_shared_lineage(monkeypatch):
    """WALL 讨论在共享 Lineage 内为不同用户分配独立身份"""
    from management_portal import wall_backend as wall
    from management_portal.workbench_projections import native
    scopes = []
    monkeypatch.setattr(wall, "project", lambda *args, **kwargs: {"title": "Project", "project_id": "project", "root": "/tmp"})
    monkeypatch.setattr(wall, "_point", lambda *args: (None, {"title": "Point"}, None, None))
    monkeypatch.setattr(wall, "_read_state", lambda: {"discussions": {}})
    monkeypatch.setattr(wall, "_change", lambda fn: None)
    def ensure(name, **options):
        scopes.append(options["resident_scope"])
        return {"resident_id": f"resident_{len(scopes):012x}", "lineage_id": "lineage_111111111111"}
    monkeypatch.setattr(native, "ensure", ensure)
    a = wall.discuss("project", "point", "alice")
    b = wall.discuss("project", "point", "bob")
    assert a["lineage_id"] == b["lineage_id"]
    assert scopes[0] != scopes[1]
    assert a["resident_id"] != b["resident_id"]
