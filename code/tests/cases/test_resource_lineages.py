""":module: resident.conversation"""
from management_portal import discussion_residents
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import knowledge_item_chat_backend as chat
from management_portal import resident_handoff as invocation
from management_portal import resource_lineages as routing
from management_portal import repo_workspace_backend as repo


def test_resource_page_types_resolve_shared_ordinary_lineages(monkeypatch, subtests):
    """各资源页类型按分组解析到共享的普通 Lineage，不支持的类型直接拒绝"""
    resolve = Mock(return_value="lineage_111111111111")
    monkeypatch.setattr(routing, "resolve_application_lineage", resolve)
    expected = {kind: "knowledge" for kind in routing.KNOWLEDGE_ENTRY_TYPES}
    expected.update({kind: kind for kind in routing.RESOURCE_PAGE_TYPES})
    for kind, group in expected.items():
        with subtests.test(resource_type=kind):
            for actor in ("alice", "bob"):
                assert routing.resolve_resource_lineage(actor=actor, resource_type=kind) == "lineage_111111111111"
                resolve.assert_called_with(actor=actor, application=f"resource-chat:{group}")
    for kind in ("", "unknown", "resident", "project", "paper"):
        with subtests.test(unsupported=kind):
            resolve.reset_mock()
            with pytest.raises(ValueError, match="Unsupported"):
                routing.resolve_resource_lineage(actor="alice", resource_type=kind)
            resolve.assert_not_called()
    resolve.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError, match="revoked"):
        routing.resolve_resource_lineage(actor="alice", resource_type="repo")


def test_resource_discussion_routes_by_registered_type_without_changing_identity(monkeypatch, tmp_path):
    """资源讨论按登记的资源类型选择 Lineage，Resident 范围与归属保持不变"""
    resolver = Mock(return_value="lineage_111111111111")
    lookup = Mock(return_value={})
    create = Mock(return_value={"resident_id": "resident_111111111111"})
    monkeypatch.setattr(discussion_residents, "resolve_resource_lineage", resolver)
    monkeypatch.setattr(discussion_residents.resident_handoff, "lookup_invocation_resident", lookup)
    monkeypatch.setattr(discussion_residents.resident_handoff, "ensure_invocation_resident", create)
    backend = chat.KnowledgeItemChatBackend(resource_service=Mock())
    item = {"type": "repo", "title": "Misleading article title"}
    assert backend._ensure("alice", "repo_111111111111", item, create=False) == {}
    create.assert_not_called()
    lookup.assert_called_once_with(actor="alice", resident_scope="resource-chat:alice:repo_111111111111", lineage_binding="lineage_111111111111")
    backend._ensure("alice", "repo_111111111111", item)
    resolver.assert_called_with(actor="alice", resource_type="repo")
    args = create.call_args.kwargs
    assert args["resident_scope"] == "resource-chat:alice:repo_111111111111"
    assert args["lineage_binding"] == "lineage_111111111111"

    # Administrative migration diagnostics expose identities from all actors,
    # without changing ownership or returning prompt/credential metadata.
    from management_portal.portal_ui_state import PortalUIStateStore
    store = PortalUIStateStore()
    for owner, rid in (("alice", "resident_111111111111"), ("bob", "resident_222222222222")):
        store.put(owner, invocation.INVOCATION_ASSOCIATION_NAMESPACE, f"resource-chat:{owner}:repo_111111111111", {
            "resident_id": rid, "metadata": {"resource_id": "repo_111111111111", "resource_type": "repo", "private_prompt": "never expose"},
        })
    from management_portal import permission_system
    graph = {"lineage_id": "lineage_111111111111", "revision": 5, "nodes": [{"id": "resident_111111111111"}]}
    # Each management check asks the permission system once; queued answers model a revocation in between.
    answers = []
    monkeypatch.setattr(permission_system, "permitted", lambda actor, instruction, parameter, ids, owners=None, batch=500: [
        (resource_id, answers.pop(0) if answers else True) for resource_id in ids])
    lineage = Mock()
    lineage.get_lineage.return_value = graph
    access = Mock()
    monkeypatch.setattr(invocation, "get_portal_access_store", lambda: access)
    monkeypatch.setattr(invocation, "get_portal_ui_state_store", lambda: store)
    monkeypatch.setattr(invocation, "get_society_service", lambda: SimpleNamespace(lineage=lineage))
    access.is_active.return_value = False
    with pytest.raises(PermissionError, match="identity"):
        invocation.diagnose_lineage_invocations(actor="operator", lineage_id="lineage_111111111111")
    lineage.get_lineage.assert_not_called()
    access.is_active.return_value = True
    result = invocation.diagnose_lineage_invocations(actor="operator", lineage_id="lineage_111111111111")
    assert result["bindings"] == [{"resident_id": "resident_111111111111", "association_actor": "alice", "resident_scope": "resource-chat:alice:repo_111111111111", "resource_id": "repo_111111111111", "recorded_resource_type": "repo"}]
    lineage.get_lineage.assert_called_with("lineage_111111111111", "operator")
    assert len(store.list("bob", invocation.INVOCATION_ASSOCIATION_NAMESPACE)) == 1
    lineage.get_lineage.side_effect = [graph, graph]
    answers[:] = [True, False]
    with pytest.raises(PermissionError, match="revoked"):
        invocation.diagnose_lineage_invocations(actor="operator", lineage_id="lineage_111111111111")
    lineage.get_lineage.side_effect = [graph, {**graph, "revision": 6}]
    with pytest.raises(ValueError, match="changed"):
        invocation.diagnose_lineage_invocations(actor="operator", lineage_id="lineage_111111111111")


def test_repo_background_calls_use_the_resource_repo_lineage(monkeypatch, subtests):
    """仓库提交信息和描述生成都在 repo 资源的 Lineage 上运行"""
    resolver = Mock(return_value="lineage_111111111111")
    monkeypatch.setattr(routing, "resolve_resource_lineage", resolver)
    monkeypatch.setattr(repo, "_commit_llm_config", lambda *_: {
        "api_id": "test-provider", "model": "test-model", "timeout_s": 120,
        "default_parameters": {},
    })
    completion = Mock()
    monkeypatch.setattr(invocation, "run_resident_completion", completion)
    for operation in ("commit", "description"):
        with subtests.test(operation=operation):
            if operation == "commit":
                completion.return_value = {"message": '{"subject":"Fix bug","reason":"Avoid duplicates."}'}
                repo._generate_commit_message(dict(repo_name="Repo", repo_id="repo_111111111111", branch="dev", change_count=1, status="M a.py", diff_stat="", diff=""), actor="alice")
                scope = "repo:commit-message"
            else:
                completion.return_value = {"message": '{"descriptions":[{"id":"repo_111111111111","description":"示例仓库。"}]}'}
                repo._generate_repo_description_batch([dict(id="repo_111111111111", name="Repo", project_ids=[], evidence="README")], actor="alice")
                scope = "repo:description"
            resolver.assert_called_with(actor="alice", resource_type="repo")
            assert completion.call_args.kwargs["lineage_binding"] == "lineage_111111111111"
            assert completion.call_args.kwargs["resident_scope"] == scope


def test_invocation_wrappers_forward_explicit_lineage_before_dispatch(monkeypatch):
    """Resident 补全调用在派发前把显式指定的 Lineage 交给调用 Resident"""
    ensure = Mock(side_effect=PermissionError("stop before dispatch"))
    monkeypatch.setattr(invocation, "get_society_service", lambda: SimpleNamespace(resident=Mock()))
    monkeypatch.setattr(invocation, "ensure_invocation_resident", ensure)
    with pytest.raises(PermissionError, match="stop before dispatch"):
        invocation.run_resident_completion(
            actor="alice", provider_id="test-provider", model="test-model",
            system_prompt="", user_prompt="hello", resident_scope="repo:description",
            lineage_binding="lineage_111111111111",
        )
    assert ensure.call_args.kwargs["lineage_binding"] == "lineage_111111111111"


def test_native_and_named_applications_share_read_only_lineage_resolution(monkeypatch, subtests):
    """paper、wall、studio、test 应用对各用户解析到同一 Lineage，未绑定时报错

    :module: lineage.service"""
    from management_portal import application_bindings
    from management_portal.workbench_projections import service
    app = {"bindings": {"main": {"lineage_id": "lineage_111111111111", "residents": {}}}}
    initialize = Mock(return_value=app)
    monkeypatch.setattr(service, "get_service", lambda: SimpleNamespace(initialize=initialize))
    for name in ("paper", "wall", "studio", "test"):
        with subtests.test(application=name):
            for actor in ("alice", "bob"):
                assert application_bindings.resolve_application_lineage(actor=actor, application=name) == "lineage_111111111111"
                initialize.assert_called_with(name, actor)
    initialize.return_value = {"bindings": {}}
    with pytest.raises(service.ProjectionError) as error:
        application_bindings.resolve_application_lineage(actor="alice", application="paper")
    assert error.value.code == "projection.lineage_unbound"
