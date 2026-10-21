from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.workbench_projections import native
from tests.cases.effect_script_support import effect_config
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401


MAIN = "lineage_111111111111"
RESIDENT = "resident_111111111111"


@pytest.fixture
def scenario(monkeypatch):
    app = {
        "builtin": "test",
        "bindings": {
            "main": {"lineage_id": MAIN, "residents": {"coordinator": RESIDENT}}
        },
    }
    state = {
        "graphs": {MAIN: {"nodes": [{"id": RESIDENT}]}},
        "graph_reads": [],
    }

    def graph(actor, lineage_id):
        assert actor == "alice"
        state["graph_reads"].append(lineage_id)
        value = state["graphs"].get(lineage_id, PermissionError("private fixture"))
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    monkeypatch.setattr("management_portal.workbench_projections.service.lineage_graph", graph)
    return app, state


def test_deleted_required_resident_still_fails_explicitly(scenario):
    """必需角色的 Resident 已从图中删除时，解析角色明确报 projection.resident_unbound"""
    app, state = scenario
    state["graphs"][MAIN]["nodes"] = []
    result = native.validate_native_bindings(app, "alice")
    assert result["bindings"] == app["bindings"]
    with pytest.raises(native.ProjectionError) as failure:
        native.resolve_role({**app, **result}, "coordinator", "alice")
    assert failure.value.code == "projection.resident_unbound"


def test_transport_failure_is_not_reported_as_missing_authorization(scenario):
    """读取绑定 Lineage 时传输失败保持 503，不报告为缺少授权"""
    app, state = scenario
    state["graphs"][MAIN] = native.InstructionError(
        "transport.unavailable", "offline fixture", http_status=503
    )
    with pytest.raises(native.InstructionError) as failure:
        native.validate_native_bindings(app, "alice")
    assert failure.value.http_status == 503

def test_fixed_function_missing_binding_never_allocates(scenario, monkeypatch):
    """固定功能角色缺少绑定时直接报错，不走历史分配路径新建 Resident"""
    app, state = scenario
    monkeypatch.setattr(native, "projection_application", lambda name, actor: app)
    monkeypatch.setattr(native.resident_handoff, "ensure_invocation_resident", lambda **kwargs: pytest.fail("historical allocation path called"))
    with pytest.raises(native.ProjectionError) as failure:
        native.binding("test", {"actor": "alice", "resident_scope": "required-role"}, "codex")
    assert failure.value.code == "projection.resident_unbound"


def test_new_dynamic_slot_attaches_to_main_and_preserves_four_route(scenario, monkeypatch):
    """新建动态角色槽挂到主 Lineage 下，并沿用请求指定的 backend_route_id"""
    app, state = scenario
    app.update(workbench_id="workbench_111111111111", revision=3, published_revision=3)
    calls = []

    def create_role(current, actor, scope, **options):
        calls.append({"actor": actor, "scope": scope, **options})
        return current, "resident_222222222222"

    monkeypatch.setattr(native, "projection_application", lambda name, actor: app)
    monkeypatch.setattr(native, "get_service", lambda: SimpleNamespace(require_role=create_role))
    identity = native.binding("test", {"actor": "alice", "resident_scope": "portal-tau:test-actor:module:new-slot", "backend_route_id": "four"}, "codex", create_role=True)
    assert identity == {"resident_id": "resident_222222222222", "lineage_id": MAIN}
    assert calls[0]["backend_route_id"] == "four"
    assert calls[0]["actor"] == "alice"


def test_dynamic_slot_cannot_publish_unrelated_binding_draft(scenario, monkeypatch):
    """应用有未发布的绑定草稿时拒绝新建动态角色槽，不连带发布草稿"""
    app, state = scenario
    app.update(revision=4, published_revision=3)
    monkeypatch.setattr(native, "projection_application", lambda name, actor: app)
    with pytest.raises(native.ProjectionError) as failure:
        native.binding("test", {"actor": "alice", "resident_scope": "new-slot"}, "codex", create_role=True)
    assert failure.value.code == "projection.binding_draft"


def test_two_declared_targets_for_a_role_are_rejected(scenario):
    """同一角色在两个绑定目标中都有声明时，解析报 projection.role_ambiguous"""
    app, state = scenario
    app["bindings"]["other"] = deepcopy(app["bindings"]["main"])
    with pytest.raises(native.ProjectionError) as failure:
        native.resolve_role(app, "coordinator", "alice")
    assert failure.value.code == "projection.role_ambiguous"


def test_binding_forbidden_is_not_misreported_as_deleted(scenario):
    """无权读取绑定的 Lineage 时报 lineage_forbidden 和 403，不误报为已删除"""
    app, state = scenario
    state["graphs"][MAIN] = PermissionError("private")
    result = native.validate_native_bindings(app, "alice")
    assert result["binding_issues"][0]["code"] == "projection.lineage_forbidden"
    assert result["bindings"] == app["bindings"]
    with pytest.raises(native.ProjectionError) as failure:
        native.resolve_role(app, "coordinator", "alice")
    assert failure.value.http_status == 403


@pytest.fixture
def entrypoints(tmp_path, monkeypatch):
    from management_portal.workbench_projections import service
    from management_portal.workbench_projections.store import Store
    from tests.cases.workbench_support import bind_resource_service

    store = Store()
    bind_resource_service(store, tmp_path, monkeypatch)
    # Anyone signed in reads a workbench; only its creator changes it.
    monkeypatch.setattr(store, "authorized", lambda app, actor, operation: Store.authorized(app, actor, operation)
                        if operation == "read" else actor == app["created_by"])
    return store, service.ProjectionService(store)


def _ready_activation(scenario, monkeypatch):
    app, state = scenario
    app["bindings"]["main"]["residents"] = {"portal-tau:test-actor:workflow.paper:activation": RESIDENT}
    app.update(workbench_id="workbench_111111111111", revision=2, published_revision=2)
    graph = state["graphs"][MAIN]
    graph["program"] = {
        "components": [
            {"id": "event_one", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
            {"id": "effect_one", "kind": "effect", "config": effect_config("DeliverMessage", {
                "resident_id": {"from": "endpoint.target"}, "message": {"value": "Fixed instructions"}}, ["target"])},
        ],
        "wires": [
            {"kind": "route", "source": "event_one", "target": "effect_one"},
            {"kind": "target", "source": "effect_one", "target": RESIDENT},
        ],
    }
    return app, state


def test_activation_accepts_only_explicit_functional_team_without_scanning(scenario, monkeypatch, entrypoints):
    """工作台激活只读取显式绑定的业务 Lineage，不扫描其他图也不改动应用，通过后成为内置入口"""
    app, state = _ready_activation(scenario, monkeypatch)
    before = deepcopy(app)
    report = native.activation_report(app, "alice")
    assert report["ready"] is True
    assert app == before
    assert set(state["graph_reads"]) == {MAIN}
    store, service = entrypoints
    stored = store.create(app["workbench_id"], {"title": "Test", "builtin": "test", "bindings": app["bindings"]}, "alice")
    stored = store.publish(stored["workbench_id"], "alice", stored["revision"])
    applied = service.apply_update(stored["workbench_id"], {"expected_revision": stored["revision"], "activate_builtin": True, "previous_app_id": ""}, "alice")
    assert store.builtin("test", "alice")["workbench_id"] == applied["workbench_id"]
    assert applied["revision"] == stored["revision"]


@pytest.mark.parametrize("broken,code", [
    ("draft", "projection.binding_draft"),
    ("wire", "projection.workflow_unbound"),
    ("empty", "projection.empty_business_team"),
    ("unclassified_member", "projection.member_role_unclassified"),
    ("system", "projection.system_role_in_business"),
])
def test_activation_blocks_incomplete_migration(scenario, monkeypatch, broken, code):
    """绑定草稿未发布、工作流未连线、业务团队为空、成员未分类或混入系统角色时阻止激活"""
    app, state = _ready_activation(scenario, monkeypatch)
    if broken == "draft":
        app["revision"] += 1
    elif broken == "wire":
        state["graphs"][MAIN]["program"]["wires"].pop()
    elif broken == "empty":
        app["bindings"]["main"]["residents"] = {}
    elif broken == "unclassified_member":
        app["bindings"]["main"]["residents"][f"member:{RESIDENT}"] = RESIDENT
    else:
        app["bindings"]["main"]["residents"]["system:semantic-search"] = RESIDENT
    report = native.activation_report(app, "alice")
    assert report["ready"] is False
    assert code in {issue["code"] for issue in report["issues"]}


def test_paper_activation_requires_fixed_roles_and_rejects_pooled_identity(scenario, monkeypatch):
    """论文工作台激活要求每个固定角色都已绑定，多个角色共用一个 Resident 时被拒"""
    from management_portal import semantic_search_surfaces
    from management_portal.paper_projection import ROLES

    monkeypatch.setattr(semantic_search_surfaces, "search_binding", lambda surface: {"surface": surface})

    app, state = _ready_activation(scenario, monkeypatch)
    app["builtin"] = "paper"
    report = native.activation_report(app, "alice")
    missing = {issue["role"] for issue in report["issues"] if issue["code"] == "projection.resident_unbound"}
    assert missing == set(ROLES)
    app["bindings"]["main"]["residents"] = {role: RESIDENT for role in ROLES}
    report = native.activation_report(app, "alice")
    assert "projection.shared_function_resident" in {issue["code"] for issue in report["issues"]}


def test_paper_activation_uses_the_system_service_authority_not_a_shadow_binding(scenario, monkeypatch):
    """论文工作台激活依赖系统语义检索服务，未配置时报 system_capability_unbound"""
    from management_portal import semantic_search_surfaces
    from management_portal.application_targets import ApplicationUnconfigured

    app, state = _ready_activation(scenario, monkeypatch)
    app["builtin"] = "paper"

    def unavailable(surface):
        assert surface == "paper"
        raise ApplicationUnconfigured("search.paper")

    monkeypatch.setattr(semantic_search_surfaces, "search_binding", unavailable)
    report = native.activation_report(app, "alice")
    assert not report["ready"]
    assert any(issue["code"] == "projection.system_capability_unbound" for issue in report["issues"])
    assert set(state["graph_reads"]) == {MAIN}


def test_activation_rejects_an_inactive_account_even_when_manifest_is_readable(scenario, monkeypatch):
    """激活检查拒绝已停用的账号，即使它仍能读取应用清单"""
    app, _state = _ready_activation(scenario, monkeypatch)
    monkeypatch.setattr(native, "is_active_user_or_resident", lambda actor: actor != "alice")
    with pytest.raises(PermissionError):
        native.activation_report(app, "alice")


def _published_page(store, app_id, actor):
    app = store.create(app_id, {"title": "Paper", "builtin": "paper", "bindings": {"main": {"lineage_id": MAIN, "residents": {"paper:summary": RESIDENT}}}}, actor)
    return store.publish(app_id, actor, app["revision"])


def test_personal_legacy_installation_is_not_an_authoritative_entrypoint(entrypoints):
    """个人旧安装的工作台不算内置入口，初始化报 projection.business_unbound"""
    store, service = entrypoints
    _published_page(store, "workbench_111111111111", "alice")
    assert store.builtin("paper", "alice") is None
    with pytest.raises(native.ProjectionError) as failure:
        service.initialize("paper", "alice")
    assert failure.value.code == "projection.business_unbound"
    assert len(store.list("alice")) == 1


def test_promote_published_projection_preserves_identity_and_is_not_personal(entrypoints, permissions, monkeypatch):
    """现有投影成为共享内置入口且保留内容与身份

    内置应用包通过权威入口对所有可读账号可见；保留修订、发布、源码、创建者与团队，拒绝改名和删除在用入口。

    :module: workbench.panels
    :importance: P1
    :design: decision_f67ae5e70793
    """
    store, service = entrypoints
    app_id = "workbench_333333333333"
    permissions.users("alice")
    permissions.users("bob", category="intern")
    permissions.place(Fact("project", "Demo", creator="alice"), Fact("lineage", MAIN, {"project": "Demo"}),
                      Fact("workbench", app_id, {"lineage": MAIN}, creator="alice"))
    app = store.create(app_id, {"title": "Portal 开发投影", "html": "<p>published</p>",
        "bindings": {"main": {"lineage_id": MAIN, "residents": {"facade": RESIDENT}}}}, "alice")
    store.publish(app_id, "alice", app["revision"])
    monkeypatch.setattr(service, "check_bindings", lambda *_: None)
    monkeypatch.setattr(native, "activation_report", lambda app, actor: {"ready": True})
    monkeypatch.setattr(store, "authorized", type(store).authorized)
    assert store.list("bob") == []
    payload = {"expected_revision": app["revision"], "activate_builtin": True, "builtin": "development", "previous_app_id": ""}
    promoted = service.apply_update(app_id, payload, "alice")
    assert promoted["builtin"] == "development"
    for key in ["workbench_id", "revision", "created_by", "bindings", "html"]:
        assert promoted[key] == app[key]
    assert service.summary(promoted)["url"] == "/development"
    assert store.builtin("development", "bob")["workbench_id"] == app_id
    assert [item["workbench_id"] for item in store.list("bob")] == [app_id]
    assert service.apply_update(app_id, payload, "alice")["revision"] == app["revision"]
    with pytest.raises(native.ProjectionError):
        service.apply_update(app_id, {**payload, "builtin": "paper"}, "alice")
    with pytest.raises(native.ProjectionError):
        store.delete(app_id, "alice")
    assert store.get_revision(app_id, "alice", app["revision"])["html"] == app["html"]
    monkeypatch.setattr(store, "authorized", lambda app, actor, operation: actor == "alice")
    assert store.list("bob") == []


def test_promote_projection_conflict_does_not_partially_change_classification(entrypoints):
    """内置入口登记冲突不留下部分归属

    未发布及原入口不匹配均拒绝，原工作台仍为自定义类型，无半迁移登记。

    :module: workbench.panels
    :importance: P1
    :design: decision_f67ae5e70793
    """
    store, _service = entrypoints
    app_id = "workbench_444444444444"
    app = store.create(app_id, {"title": "Portal", "html": "<p>published</p>"}, "alice")
    with pytest.raises(native.ProjectionError):
        store.activate_builtin(app_id, "alice", name="development", previous_app_id="")
    store.publish(app_id, "alice", app["revision"])
    with pytest.raises(native.ProjectionError):
        store.activate_builtin(app_id, "alice", name="development", previous_app_id="workbench_444444444444")
    assert store.get(app_id, "alice")["builtin"] == ""
    assert store.builtin("development", "alice") is None


def test_every_signed_in_identity_shares_the_same_native_entrypoint(entrypoints):
    """所有登录身份共用同一个内置入口，没有登录身份的请求无权取得"""
    store, service = entrypoints
    app = _published_page(store, "workbench_111111111111", "alice")
    store.activate_builtin(app["workbench_id"], "alice", previous_app_id="")
    assert store.builtin("paper", "alice")["workbench_id"] == store.builtin("paper", "bob")["workbench_id"]
    with pytest.raises(PermissionError):
        store.builtin("paper", "")


def test_entrypoint_replacement_requires_previous_owner_and_compare_and_swap(entrypoints):
    """替换内置入口须能修改原入口并给出当前入口 ID，否则拒绝或报冲突"""
    store, service = entrypoints
    first = _published_page(store, "workbench_111111111111", "alice")
    second = _published_page(store, "workbench_222222222222", "bob")
    store.activate_builtin(first["workbench_id"], "alice", previous_app_id="")
    with pytest.raises(PermissionError):
        store.activate_builtin(second["workbench_id"], "bob", previous_app_id=first["workbench_id"])
    with pytest.raises(native.ProjectionError) as failure:
        store.activate_builtin(second["workbench_id"], "bob", previous_app_id="")
    assert failure.value.code == "projection.activation_conflict"
    assert store.builtin("paper", "alice")["workbench_id"] == first["workbench_id"]


def test_ai_dispatch_requires_a_declared_workflow(scenario):
    """未声明工作流的业务 AI 派发报 projection.workflow_unbound"""
    with pytest.raises(native.ProjectionError) as failure:
        native.execution_bindings("paper", {"actor": "alice"}, "codex")
    assert failure.value.code == "projection.workflow_unbound"


@pytest.mark.parametrize("change", ["delete", "update"])
def test_activation_rechecks_candidate_inside_authority_transaction(entrypoints, monkeypatch, change):
    """启用入口拒绝已删除或已变更的候选版本

    初次校验后发生删除或编辑时，事务内重新校验候选状态，不写入悬空或未发布的权威入口。

    :importance: P1
    """
    store, service = entrypoints
    app = _published_page(store, "workbench_111111111111", "alice")
    original_require = store.require_write
    changed = False

    def change_after_validation(candidate, actor):
        nonlocal changed
        original_require(candidate, actor)
        if not changed:
            changed = True
            if change == "delete":
                store.delete(app["workbench_id"], actor)
            else:
                store.update(app["workbench_id"], {"title": "Changed"}, actor, app["revision"])

    monkeypatch.setattr(store, "require_write", change_after_validation)
    with pytest.raises(native.ProjectionError) as failure:
        store.activate_builtin(app["workbench_id"], "alice", previous_app_id="")
    assert failure.value.code == ("projection.not_found" if change == "delete" else "projection.activation_conflict")
    assert store.builtin("paper", "alice") is None


def test_paper_function_roles_are_stable_and_old_per_paper_keys_are_rejected(monkeypatch):
    """不同论文复用同一个论文功能角色，旧的按论文划分的 scope 被拒绝"""
    from management_portal import paper_projection

    captured = []
    monkeypatch.setattr(native, "turn", lambda name, **options: captured.append((name, options)) or {
        "message": "fixture", "resident_id": RESIDENT, "message_id": "msg_paper_input_fixture",
    })
    for paper in ("paper_one", "paper_two"):
        paper_projection.turn(actor="alice", resident_scope="paper:summary", prompt=paper)
    assert len(paper_projection.ROLES) == 9
    assert {options["resident_scope"] for name, options in captured} == {"paper:summary"}
    assert captured[0][1]["workflow"] == captured[1][1]["workflow"]
    with pytest.raises(ValueError, match="未定义"):
        paper_projection.turn(actor="alice", resident_scope="paper:summary:paper_one")
