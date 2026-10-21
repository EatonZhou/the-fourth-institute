"""
:importance: P2
:design: decision_f67ae5e70793
"""
from urllib.parse import urlsplit
from contextlib import contextmanager

import pytest




from management_portal.workbench_projections import service
from management_portal.workbench_projections.store import ProjectionError, Store
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.workbench_support import bind_resource_service


@pytest.fixture
def resources(store, tmp_path, monkeypatch):
    return bind_resource_service(store, tmp_path, monkeypatch)


def test_workbench_http_uses_public_operation_error_contract(monkeypatch, subtests, isolated_portal_case):
    """工作台 HTTP 接口把本地或远端的 ProjectionError 按公共操作错误格式返回

    :design-point:"""
    for status in [400, 404, 409]:
        for remote in [False, True]:
            with subtests.test(status=status, remote=remote), isolated_portal_case() as case_path:
                with monkeypatch.context() as monkeypatch:
                    from management_portal import serve_management_portal
                    from management_portal.resources.errors import (
                        resource_error_from_payload,
                        resource_error_http_response,
                        resource_error_payload,
                    )
                    from management_portal.workbench_projections import http

                    error = ProjectionError("projection.rejected", "rejected", status, details={"field": "title"})
                    if remote:
                        error = resource_error_from_payload(resource_error_payload(error))
                    def reject(*args, **kwargs):
                        raise error
                    monkeypatch.setattr(http, "get", reject)
                    monkeypatch.setattr(serve_management_portal, "_serve_json", lambda handler, code, payload: (code, payload))
                    result = http.handle(object(), urlsplit("/api/workbenches/detail?id=unused"), "test-user")
                    assert result == resource_error_http_response(error)


def test_workbench_http_uses_shared_builtin_errors(monkeypatch, subtests, isolated_portal_case):
    """工作台 HTTP 读写接口把权限、缺失、无效和意外异常按共享错误格式返回

    :design-point:"""
    for error in [
    PermissionError("forbidden"),
    KeyError("missing"),
    ValueError("invalid"),
    RuntimeError("unexpected"),
]:
        for write in [False, True]:
            with subtests.test(error=error, write=write), isolated_portal_case() as case_path:
                with monkeypatch.context() as monkeypatch:
                    from management_portal import serve_management_portal
                    from management_portal.resources.errors import resource_error_http_response
                    from management_portal.workbench_projections import http

                    def reject(*args, **kwargs):
                        raise error
                    monkeypatch.setattr(http, "post" if write else "get", reject)
                    monkeypatch.setattr(serve_management_portal, "_read_bounded_json_object", lambda *args, **kwargs: {})
                    monkeypatch.setattr(serve_management_portal, "_serve_json", lambda handler, code, payload: (code, payload))
                    result = http.handle(object(), urlsplit("/api/workbenches/detail?id=unused"), "test-user", write=write)
                    assert result == resource_error_http_response(error)


APP = "workbench_111111111111"
LINEAGE = "lineage_111111111111"
RESIDENT = "resident_111111111111"


def test_workbench_http_update_separates_target_from_command(monkeypatch, subtests, isolated_portal_case):
    """工作台更新接口以 id 为目标，只把其余字段作为更新命令传给服务

    :importance: P1
    :design: decision_4bc03b20c30f, decision_34158fdd4078
    :design-point:
    """
    for command in [{"navigation": "top"}, {"title": "Updated"}]:
        with subtests.test(command=command), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                from unittest.mock import Mock
                from management_portal.workbench_projections import http
                service_mock = Mock()
                monkeypatch.setattr(http, "get_service", lambda: service_mock)
                payload = {"id": APP, "expected_revision": 1, **command}
                http.post("/api/workbenches/update", payload, "alice")
                service_mock.update.assert_called_once_with(APP, {"expected_revision": 1, **command}, "alice")
                assert payload["id"] == APP


@pytest.fixture
def store():
    result = Store()
    result.create(APP, {"title": "试验台", "bindings": {"main": {"lineage_id": LINEAGE, "residents": {"coordinator": RESIDENT}}}, "html": "<h1>第一版</h1>"}, "alice")
    return result


def place_workbenches(permissions, *app_ids, creator="alice"):
    """Turn the permission system on for workbenches of a Lineage in the Project ``creator`` made (so ``creator`` manages them).

    Both Users are 实习用户: a 一般用户 would manage every Project anyway.
    """
    permissions.users("alice", "bob", category="intern")
    permissions.place(Fact("project", "WorkbenchProject", creator=creator), Fact("lineage", LINEAGE, {"project": "WorkbenchProject"}),
                      *(Fact("workbench", app_id, {"lineage": LINEAGE}, creator=creator) for app_id in app_ids or (APP,)))


def fake_team(monkeypatch, nodes, *, create=None, reads=None):
    """Graph reads and Resident creation the Workbench service performs."""
    from types import SimpleNamespace

    from management_portal import society_service

    def graph(actor, lineage_id):
        if reads is not None:
            reads.append(lineage_id)
        return {"lineage_id": lineage_id, "nodes": list(nodes), "revision": len(nodes), "program": {"components": []}}

    def refuse(*args, **kwargs):
        raise AssertionError("no Resident may be created")

    monkeypatch.setattr(service, "lineage_graph", graph)
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        lineage=SimpleNamespace(create_lineage_resident=create or refuse)))


def test_store_connection_commits(store):
    """工作台 Store 连接正常退出时提交更改

    :design-point:"""
    with store.connect() as connection:
        connection.execute("UPDATE apps SET navigation='hidden' WHERE id=%s", (APP,))

    assert store.get(APP, "alice")["navigation"] == "hidden"


def test_store_connection_rolls_back(store):
    """工作台 Store 连接内抛出异常时回滚更改

    :design-point:"""
    with pytest.raises(RuntimeError, match="expected failure"):
        with store.connect() as connection:
            connection.execute('UPDATE apps SET revision=2 WHERE id=%s', (APP,))
            raise RuntimeError("expected failure")

    assert store.get(APP, "alice")["revision"] == 1


def test_versions_publish_is_explicit_and_conflicts_preserve_source(store):
    """工作台修改须显式发布才进入已发布版本，按旧 revision 保存被拒且不覆盖当前内容

    :design-point:"""
    store.publish(APP, "alice", 1)
    store.update(APP, {"html": "<h1>第二版</h1>"}, "alice", 1)
    assert "第一版" in store.get(APP, "alice", published=True)["html"]
    with pytest.raises(ProjectionError, match="刷新"):
        store.update(APP, {"html": "stale"}, "alice", 1)
    assert "第二版" in store.get(APP, "alice")["html"]


def test_shared_access_keeps_user_state_separate(store, permissions):
    """其他用户可打开工作台，个人状态按用户分开保存，只有能修改的人能删除

    权限系统开启时，权限系统没给到 contributor 的用户删除工作台被拒绝，管理工作台所在 Project 的用户可以删除。

    :design-point:"""
    place_workbenches(permissions)
    assert store.get(APP, "bob")["workbench_id"] == APP
    store.state(APP, "bob", {"models": ["model-b"]})
    store.state(APP, "alice", {"models": ["PI 0.5"]})
    assert store.state(APP, "alice")["models"] == ["PI 0.5"]
    assert store.state(APP, "bob")["models"] == ["model-b"]
    assert "models" not in store.get(APP, "alice")
    with pytest.raises(PermissionError, match="无权修改"):
        store.delete(APP, "bob")
    assert store.delete(APP, "alice")["deleted"] == APP


def test_project_deny_hides_native_workbench_and_blocks_direct_routes(store, permissions, monkeypatch):
    """项目拒绝覆盖内置工作台目录、页面、历史版本与用户状态入口"""
    from management_portal.workbench_projections import http

    place_workbenches(permissions)
    store.publish(APP, "alice", 1)
    with store.connect() as connection:
        connection.execute("INSERT INTO native_entrypoints (name, app_id) VALUES ('development', %s)", (APP,))
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(http, "get_service", lambda: runtime)
    assert [app["workbench_id"] for app in store.list("bob")] == [APP]
    permissions.deny("bob", "WorkbenchProject", "@admin")
    assert http.get("/api/workbenches", {}, "bob")["workbenches"] == []
    for read in (
        lambda: http.get("/api/workbenches/native", {"name": ["development"]}, "bob"),
        lambda: http.get("/api/workbenches/detail", {"id": [APP], "published": ["true"]}, "bob"),
        lambda: http.get("/api/workbenches/state", {"id": [APP]}, "bob"),
        lambda: store.get_revision(APP, "bob", 1),
    ):
        with pytest.raises(PermissionError, match="无权访问"):
            read()
    assert store.builtin("development", "alice")["workbench_id"] == APP


def test_only_who_the_permission_system_lets_change_a_workbench(store, permissions, monkeypatch):
    """工作台只允许权限系统给到 contributor 的人修改、发布、删除

    其他人可以打开工作台，但保存、发布、删除和发起 AI 生成都被拒绝且不留下修订；管理工作台所在 Project 的用户和管理员可以修改；权限系统关闭时任何登录身份都可以修改。

    :importance: P1
    :aspects: functional, behavior
    :design-point:
    """
    from management_portal import permission_system

    place_workbenches(permissions)
    permissions.users("admin")
    permissions.administrator("admin")
    fake_team(monkeypatch, [{"id": RESIDENT}])
    runtime = service.ProjectionService(store)
    assert store.get(APP, "bob")["workbench_id"] == APP  # anyone may open it
    for change in (
        lambda: store.update(APP, {"title": "bob"}, "bob", 1),
        lambda: store.publish(APP, "bob", 1),
        lambda: store.delete(APP, "bob"),
        lambda: runtime.start_generation({"request_id": "visitor-request", "lineage_id": LINEAGE, "workbench_id": APP}, "bob"),
    ):
        with pytest.raises(PermissionError, match="无权修改"):
            change()
    assert store.get(APP, "alice")["revision"] == 1
    assert store.update(APP, {"title": "Alice"}, "alice", 1)["title"] == "Alice"
    assert store.publish(APP, "admin", 2)["published_revision"] == 2
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    assert store.update(APP, {"title": "bob"}, "bob", 2)["revision"] == 3


def test_directory_lists_published_workbenches_of_readable_projects(store, permissions, monkeypatch):
    """工作台目录按可读 Project 列出

    目录列出用户能修改的、内置的，以及其有权读取的 Project 下已发布的工作台；无权读取的 Project 和无权修改的草稿不列出；目录条目标明当前用户能否修改。

    :importance: P1
    :aspects: functional, behavior
    :design-point:
    """
    from management_portal.workbench_projections import store as store_module

    asked = []

    def readable(actor, projects):
        projects = set(projects)
        asked.append((actor, projects))
        return projects & {"Readable"}

    monkeypatch.setattr(store_module, "readable_projects", readable)
    shared, closed, draft = "workbench_222222222222", "workbench_333333333333", "workbench_444444444444"
    place_workbenches(permissions, APP, shared, closed, draft)
    for app_id, project in ((shared, "Readable"), (closed, "Closed"), (draft, "Readable")):
        created = store.create(app_id, {"title": app_id, "html": "<p>page</p>", "project_ids": [project]}, "alice")
        if app_id != draft:
            store.publish(app_id, "alice", created["revision"])
    # A visitor sees the published page of a Project it may read; nobody else can use a draft.
    assert [app["workbench_id"] for app in store.list("bob")] == [shared]
    assert asked[-1][0] == "bob" and {"Readable", "Closed"} <= asked[-1][1]
    assert {app["workbench_id"] for app in store.list("alice")} == {APP, shared, closed, draft}
    catalog = service.ProjectionService(store).catalog("bob")["workbenches"]
    assert [(item["workbench_id"], item["allows"]["UpdateResource"]) for item in catalog] == [(shared, False)]


def test_directory_stars_are_personal_and_leave_with_the_workbench(store, permissions, monkeypatch):
    """工作台收藏属于个人且随工作台删除

    收藏按先后顺序返回在目录中，只影响当前用户；目录条目标出当前承担内置入口的工作台；收藏前须能读取工作台，非登录身份不能收藏；工作台删除后从所有人的收藏中移除；内置入口查询返回当前用户的修改权。

    :importance: P1
    :aspects: functional, behavior
    :design-point:
    """
    from management_portal import presentation_store
    from management_portal.workbench_projections import http, store as store_module

    monkeypatch.setattr(store_module, "readable_projects", lambda actor, projects: set(projects) & {"Readable"})
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(http, "get_service", lambda: runtime)
    paper, other = "workbench_222222222222", "workbench_333333333333"
    place_workbenches(permissions, APP, paper, other)
    for app_id in (paper, other):
        store.publish(app_id, "alice", store.create(app_id, {"title": app_id, "html": "<p>page</p>", "project_ids": ["Readable"]}, "alice")["revision"])
    with store.connect() as connection:
        connection.execute("INSERT INTO native_entrypoints (name, app_id) VALUES ('paper', %s)", (paper,))
    assert http.post("/api/workbenches/favorite", {"id": other, "favorite": True}, "bob") == {"favorites": [other]}
    assert runtime.set_favorite(paper, True, "bob") == {"favorites": [other, paper]}
    catalog = runtime.catalog("bob")
    # Stars keep their order; the server says which entry serves a built-in page now.
    assert catalog["favorites"] == [other, paper]
    assert {item["workbench_id"]: (item["favorite"], item["native"]) for item in catalog["workbenches"]} == {
        paper: (True, True), other: (True, False)}
    assert runtime.catalog("alice")["favorites"] == []
    assert http.get("/api/workbenches/native", {"name": ["paper"]}, "bob")["workbench"]["allows"] == {"UpdateResource": False}
    assert http.get("/api/workbenches/native", {"name": ["paper"]}, "alice")["workbench"]["allows"] == {"UpdateResource": True}
    with pytest.raises(ProjectionError):
        runtime.set_favorite("workbench_999999999999", True, "bob")
    with pytest.raises(PermissionError):
        runtime.set_favorite(other, True, "")
    store.delete(other, "alice")
    assert presentation_store.favorite_ids("bob", "workbench") == [paper]
    assert runtime.set_favorite(paper, False, "bob") == {"favorites": []}


def test_designer_saves_on_the_requesters_right_to_change_the_page(store, resources, permissions, monkeypatch):
    """设计师按发起人的修改权保存生成页面

    设计师 Resident 自己不能修改页面，保存 AI 生成的页面时按发起生成的人校验修改权：能修改的人发起的任务可以保存，无权修改的人发起的任务被拒绝。

    :importance: P1
    :aspects: functional, behavior
    :design-point:
    """
    place_workbenches(permissions)
    runtime = service.ProjectionService(store)
    page = {"title": "新界面", "html": "<button>刷新</button>"}
    store.job("by_owner", "alice", {"id": "by_owner", "status": "running", "workbench_id": APP, "base_revision": 1,
                                    "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_owner"})
    assert runtime.save_generation_result(APP, {"job_id": "by_owner", "base_revision": 1, **page}, RESIDENT)["revision"] == 2
    store.job("by_visitor", "bob", {"id": "by_visitor", "status": "running", "workbench_id": APP, "base_revision": 2,
                                    "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_visitor"})
    with pytest.raises(PermissionError, match="无权修改"):
        runtime.save_generation_result(APP, {"job_id": "by_visitor", "base_revision": 2, **page}, RESIDENT)
    assert store.get(APP, "alice")["revision"] == 2


def test_delete_page_does_not_touch_lineage(store, monkeypatch):
    """删除工作台页面不操作其绑定的 Lineage

    :design-point:"""
    monkeypatch.setattr(service, "isa", lambda *args, **kwargs: pytest.fail("删除页面不应操作 Lineage"))
    store.delete(APP, "alice")
    with pytest.raises(ProjectionError):
        store.get(APP, "alice")


def test_workbench_updates_reject_unknown_fields(store):
    """工作台更新含未知字段（包括已删除的 owner_ids）时被拒绝，页面定义不变

    :design-point:"""
    runtime = service.ProjectionService(store)
    for field in ("unknown_field", "owner_ids"):
        with pytest.raises(ProjectionError, match="未知工作台更新字段"):
            runtime.apply_update(APP, {"expected_revision": 1, field: ["bob"]}, "alice")
    current = store.get(APP, "alice")
    assert current["revision"] == 1 and current["created_by"] == "alice" and "owner_ids" not in current


def test_unpublished_page_and_factory_use_workbenches_modules(store):
    """未发布工作台链接指向 /workbenches 设置面板，工作室入口指向 studio 面板

    :design-point:"""
    runtime = service.ProjectionService(store)
    summary = runtime.summary(store.get(APP, "alice"))
    assert summary["url"].startswith("/workbenches?panel=settings&")
    assert "#" not in summary["url"]
    assert service.builtins()["studio"]["path"] == "/workbenches?panel=studio"


@pytest.mark.parametrize("source", [
    "<!doctype html><html><body>独立页面</body></html>",
    '<nav>独立导航</nav>',
    '<div class="portalBrand">自建品牌</div>',
    '<style>:root { --bg: black }</style><p>替换主题</p>',
    '<style>body { margin: 0 }</style><p>替换页面布局</p>',
])
def test_projection_rejects_independent_page_templates(store, source):
    """工作台页面含完整 HTML 文档、独立导航、自建品牌或改写主题与布局时拒绝保存

    :design-point:"""
    with pytest.raises(ProjectionError) as error:
        store.update(APP, {"html": source}, "alice", 1)
    assert error.value.code == "projection.template"
    assert store.get(APP, "alice")["revision"] == 1


@pytest.mark.parametrize("source", [
    '<script>Projection.hatg("TriggerEvent", {prompt: text, flow: "analyze", idempotency_key: key}, {binding: "main"})</script>',
    '<script>Projection.hatg("TriggerEvent", {prompt, flow}, {binding: "main"})</script>',
    "<script>Projection.hatg('GetLineage', {'binding': 'main'})</script>",
])
def test_projection_rejects_host_options_inside_instruction_arguments(store, source):
    """页面把宿主选项（binding/role/flow/idempotency_key）放进指令参数时拒绝保存

    :design: decision_898b8911a2ff
    :design-point:
    """
    # binding / role / flow / idempotency_key belong to the host (third argument); inside the
    # instruction arguments they are undeclared parameters and the page's real calls would fail.
    with pytest.raises(ProjectionError) as error:
        store.update(APP, {"html": source}, "alice", 1)
    assert error.value.code == "projection.template"
    assert "第三个参数" in str(error.value)
    assert store.get(APP, "alice")["revision"] == 1


def test_projection_accepts_host_options_in_the_third_argument(store):
    """宿主选项在第三个参数、注释或字符串中出现时页面正常保存

    :design: decision_898b8911a2ff
    :design-point:
    """
    source = (
        '<script>const r = await Projection.hatg("TriggerEvent", {prompt: text}, '
        '{binding: "main", flow: "analyze", idempotency_key: key});'
        ' await Projection.hatg("DeliverMessage", {message: {content_parts: [{type: "text", text: "flow: role:"}]}}, {role: "coordinator"});'
        ' // Projection.hatg("Example", {flow: 1})\n</script>'
    )
    assert store.update(APP, {"html": source}, "alice", 1)["revision"] == 2


def test_projection_accepts_portal_business_content_fragment(store):
    """使用 Portal 组件样式的业务内容片段可原样保存为工作台页面

    :design-point:"""
    source = '<section><label class="portalField">模型<input></label><button class="portalButton">刷新</button></section><script>const example="<html>";</script>'
    assert store.update(APP, {"html": source}, "alice", 1)["html"] == source


def test_missing_resident_is_explicit_and_never_recreated(store, monkeypatch):
    """工作台角色绑定的 Resident 不在 Lineage 中时报 resident_unbound，不重新创建

    :design-point:"""
    reads = []
    fake_team(monkeypatch, [], reads=reads)
    runtime = service.ProjectionService(store)
    with pytest.raises(ProjectionError) as caught:
        runtime.require_role(store.get(APP, "alice"), "alice", "coordinator")
    assert caught.value.code == "projection.resident_unbound"
    assert reads == [LINEAGE]


def test_binding_update_validates_new_identity_without_rejecting_unchanged_stale_role(store, monkeypatch):
    """更新工作台绑定时只校验新增的 Resident，未改动的失效角色不导致拒绝

    :design-point:"""
    stale = "resident_aaaaaaaaaaaa"
    added = "resident_bbbbbbbbbbbb"
    current = store.get(APP, "alice")
    bindings = current["bindings"]
    bindings["main"]["residents"]["stale-work"] = stale
    current = store.update(APP, {"bindings": bindings}, "alice", current["revision"])
    fake_team(monkeypatch, [{"id": RESIDENT}, {"id": added}])
    prepared = current["bindings"]
    prepared["main"]["residents"]["new-work"] = added
    saved = service.ProjectionService(store).apply_update(
        APP, {"expected_revision": current["revision"], "bindings": prepared}, "alice",
    )
    assert saved["bindings"]["main"]["residents"] == {
        "coordinator": RESIDENT, "stale-work": stale, "new-work": added,
    }


def test_binding_update_still_rejects_a_new_missing_identity(store, monkeypatch):
    """更新工作台绑定时新增的 Resident 不在 Lineage 中则报 resident_unbound

    :design-point:"""
    current = store.get(APP, "alice")
    prepared = current["bindings"]
    prepared["main"]["residents"]["new-work"] = "resident_bbbbbbbbbbbb"
    fake_team(monkeypatch, [{"id": RESIDENT}])
    with pytest.raises(ProjectionError) as caught:
        service.ProjectionService(store).apply_update(
            APP, {"expected_revision": current["revision"], "bindings": prepared}, "alice",
        )
    assert caught.value.code == "projection.resident_unbound"


def creator(nodes, calls, *, fail=None):
    """A SocietyService that creates each creation key's Resident once.

    ``fail="permission"`` refuses the first call; ``fail="transport"`` loses
    the reply of the first call after the Resident was created.
    """
    created = {}

    def create(lineage_id, payload, actor):
        calls.append(dict(payload))
        first = len(calls) == 1
        if fail == "permission" and first:
            raise PermissionError("denied")
        key = payload["creation_key"]
        if key not in created:
            created[key] = f"resident_{222222222222 + len(created)}"
            nodes.append({"id": created[key]})
        if fail == "transport" and first:
            raise TimeoutError("response lost after commit")
        return {"resident": {"resident_id": created[key], "lineage_id": lineage_id}}

    return create


def test_provider_role_creation_carries_execution_configuration(store, resources, monkeypatch):
    """为工作台角色创建 Resident 时带上后端、路由与运行设置并使用创建键

    :design-point:"""
    calls = []
    nodes = [{"id": RESIDENT}]
    fake_team(monkeypatch, nodes, create=creator(nodes, calls))
    runtime = service.ProjectionService(store)
    settings = {"model": "model-test"}
    app, resident = runtime.require_role(store.get(APP, "alice"), "alice", "review", backend="provider_api", backend_route_id="provider-test", settings=settings)
    assert calls[0]["runtime_settings"] == settings
    assert calls[0]["backend_route_id"] == "provider-test"
    assert calls[0]["backend"] == "provider_api"
    assert calls[0]["creation_key"].startswith("invocation-create:")
    assert app["bindings"]["main"]["residents"]["review"] == resident


def test_role_creation_holds_the_shared_lineage_workflow_lock(store, resources, monkeypatch):
    """创建工作台角色 Resident 期间持有该 Lineage 的共享工作流锁，完成后释放

    :design-point:"""
    from management_portal import lineage_workflows

    held = []
    nodes = [{"id": RESIDENT}]

    @contextmanager
    def lineage_lock(lineage_id):
        held.append(lineage_id)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "lineage_lock", lambda self, lineage_id: lineage_lock(lineage_id))

    calls = []
    create = creator(nodes, calls)

    def create_while_locked(lineage_id, payload, actor):
        assert held == [LINEAGE]
        return create(lineage_id, payload, actor)

    fake_team(monkeypatch, nodes, create=create_while_locked)
    runtime = service.ProjectionService(store)
    app, resident = runtime.require_role(store.get(APP, "alice"), "alice", "review")
    assert resident == "resident_222222222222"
    assert app["bindings"]["main"]["residents"]["review"] == resident
    assert held == []


def test_binding_validation_reads_the_graph_and_never_patches_it(store, resources, monkeypatch):
    """校验工作台绑定只读取 Lineage 图，不修改它

    :design-point:"""
    reads = []
    fake_team(monkeypatch, [{"id": RESIDENT}], reads=reads)
    runtime = service.ProjectionService(store)
    runtime.update(APP, {"expected_revision": 1, "bindings": {"team": {"lineage_id": LINEAGE, "residents": {"worker": RESIDENT}}}}, "alice")
    assert reads == [LINEAGE]
    assert "team" in store.get(APP, "alice")["bindings"]


@pytest.mark.parametrize("failure", ["transport", "permission"])
def test_role_creation_resends_the_frozen_request_after_a_failure(store, resources, monkeypatch, failure):
    """创建角色 Resident 失败后重试原样重发首次请求，最多只建一个 Resident

    :design-point:"""
    calls = []
    nodes = [{"id": RESIDENT}]
    fake_team(monkeypatch, nodes, create=creator(nodes, calls, fail=failure))
    runtime = service.ProjectionService(store)
    with pytest.raises(TimeoutError if failure == "transport" else PermissionError):
        runtime.require_role(store.get(APP, "alice"), "alice", "review", title="Original")
    result, resident_id = runtime.require_role(store.get(APP, "alice"), "alice", "review", title="Changed")
    assert result["bindings"]["main"]["residents"]["review"] == resident_id
    # The retry resends the original request with its creation key, so at most one Resident exists.
    assert calls[0] == calls[1] and calls[0]["title"] == "Original"
    assert [node["id"] for node in nodes] == [RESIDENT, resident_id]


def test_dispatch_read_and_mutation_confirmation(store, monkeypatch):
    """工作台调用会修改状态的指令须先确认，只读指令按角色填入 resident_id 直接执行

    :design-point:"""
    schemas = {
        "DeliverMessage": {"mutates": True, "parameters": [{"name": "resident_id"}]},
        "GetResident": {"mutates": False, "parameters": [{"name": "resident_id"}]},
    }
    monkeypatch.setattr(service, "instruction_schema", schemas.__getitem__)
    fake_team(monkeypatch, [{"id": RESIDENT}])
    calls = []
    monkeypatch.setattr(service, "isa", lambda actor, name, arguments=None, **kwargs: calls.append((name, arguments)) or {"state": {}})
    runtime = service.ProjectionService(store)
    store.publish(APP, "alice", 1)
    with pytest.raises(ProjectionError) as caught:
        runtime.dispatch(APP, {"instruction": "DeliverMessage", "role": "coordinator"}, "alice")
    assert caught.value.code == "projection.confirmation_required"
    assert not calls
    runtime.dispatch(APP, {"instruction": "GetResident", "role": "coordinator"}, "alice")
    assert calls[-1] == ("GetResident", {"resident_id": RESIDENT})


@pytest.mark.parametrize("status", [403, 404, 503])
def test_dispatch_reports_broken_binding_without_retrying_or_submitting(store, monkeypatch, status):
    """绑定的 Lineage 无权访问或不存在时提示到工作台设置修复，暂不可用时抛出可重试错误，都不提交指令

    :design-point:"""
    from management_portal.hatg.contracts import InstructionError

    monkeypatch.setattr(service, "instruction_schema", lambda name: {"mutates": False, "parameters": []})
    failure = InstructionError("instruction.unavailable" if status == 503 else "instruction.not_found",
                               "Lineage lookup failed", http_status=status, retryable=status == 503)

    def unavailable(*args):
        raise failure

    monkeypatch.setattr(service, "lineage_graph", unavailable)
    calls = []
    monkeypatch.setattr(service, "isa", lambda *args, **kwargs: calls.append(args))
    store.publish(APP, "alice", 1)
    runtime = service.ProjectionService(store)
    with pytest.raises((ProjectionError, InstructionError)) as caught:
        runtime.dispatch(APP, {"instruction": "GetInputState", "arguments": {"message_id": "msg_existing"}}, "alice")
    assert not calls
    if status == 503:
        assert caught.value is failure and caught.value.retryable
    else:
        assert caught.value.code == ("projection.lineage_forbidden" if status == 403 else "projection.lineage_unbound")
        assert "工作台设置" in str(caught.value) and "刷新页面无法修复" in str(caught.value)


def test_draft_binding_does_not_change_published_dispatch(store, monkeypatch):
    """草稿中修改的绑定不影响已发布工作台的指令调用

    :design-point:"""
    monkeypatch.setattr(service, "instruction_schema", lambda name: {"mutates": False, "parameters": [{"name": "lineage_id"}]})
    store.publish(APP, "alice", 1)
    other = "lineage_222222222222"
    store.update(APP, {"bindings": {"main": {"lineage_id": other, "residents": {}}}}, "alice", 1)
    reads = []
    fake_team(monkeypatch, [], reads=reads)
    calls = []
    monkeypatch.setattr(service, "isa", lambda actor, name, arguments=None, **kwargs: calls.append(arguments["lineage_id"]) or {})
    service.ProjectionService(store).dispatch(APP, {"instruction": "GetLineage"}, "alice")
    assert set(calls) == set(reads) == {LINEAGE}


@pytest.mark.parametrize("missing_path", [False, True])
def test_generation_receipt_migration_preserves_records_and_requires_proven_links(store, missing_path):
    """生成记录迁移把 run_id 换成对应 Trigger Path 并保留其余字段，找不到对应路径时拒绝迁移

    :design-point:"""
    from management_common.portal_database import _database_schema
    from scripts.migrate_workbench_trigger_paths import migrate

    source = {"id": "historical-generation", "workbench_id": APP, "lineage_id": LINEAGE,
              "event_id": "event-historical", "message_id": "msg_historical", "status": "completed",
              "trigger_path_id": "old-run", "response": "Saved draft", "save_receipt": {"revision": 2}}
    store.job(source["id"], "alice", source)
    with store.connect() as connection, connection.raw.transaction(force_rollback=True):
        connection.execute("ALTER TABLE generations RENAME COLUMN trigger_path_id TO run_id")
        connection.execute("ALTER TABLE generations ADD COLUMN invocation_id TEXT")
        connection.execute("CREATE TABLE trigger_paths (lineage_id TEXT,occurrence_id TEXT,trigger_path_id TEXT)")
        if not missing_path:
            connection.execute("INSERT INTO trigger_paths VALUES(%s,'event-historical','trigger_path-historical')", (LINEAGE,))
        schema = _database_schema("workbenches")
        if missing_path:
            with pytest.raises(ValueError, match="no migrated event Trigger Path"):
                migrate(connection.raw, workbench_schema=schema, lineage_schema=schema)
            assert connection.execute("SELECT run_id FROM generations").fetchone()["run_id"] == "old-run"
        else:
            assert migrate(connection.raw, workbench_schema=schema, lineage_schema=schema) == {
                "generations": 1, "trigger_path_links": 1}
            from management_portal.workbench_projections.generation_storage import decode
            result = decode(connection.execute("SELECT * FROM generations").fetchone())
            assert result == {**source, "created_by": "alice", "trigger_path_id": "trigger_path-historical"}
            columns = {row["column_name"] for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='generations'", (schema,))}
            assert not {"run_id", "invocation_id"}.intersection(columns)


def test_generation_persists_the_current_workflow_receipt(store, resources, monkeypatch):
    """发起页面生成先持久化待提交回执，提交后记录 TriggerEvent 回执，重复请求不再触发

    :design-point:"""
    from management_portal import lineage_workflows
    from management_portal.workbench_projections import studio

    runtime = service.ProjectionService(store)
    app = store.get(APP, "alice")
    binding = {"lineage_id": LINEAGE, "resident_id": RESIDENT,
               "event_source_id": "event_source_111111111111", "effect_id": "effect_111111111111"}
    calls = []
    monkeypatch.setattr(service, "lineage_graph", lambda *_: {
        "lineage_id": LINEAGE, "title": "Team", "nodes": [{"id": RESIDENT}], "revision": 1})
    monkeypatch.setattr(runtime, "initialize", lambda *_: app)
    monkeypatch.setattr(runtime, "require_role", lambda *_, **__: (app, RESIDENT))
    monkeypatch.setattr(studio, "install", lambda *_: {"generate": binding, "optimize": binding})

    def trigger(actor, instruction, arguments, **kwargs):
        calls.append((actor, instruction, arguments, kwargs))
        import hashlib
        identity = hashlib.sha256(b"alice:generation-request-current").hexdigest()[:32]
        pending = store.job(identity, actor)
        assert pending["receipt"]["submission_state"] == "pending"
        assert pending["receipt"]["idempotency_key"] == kwargs["key"]
        return {"event_id": "event-generation", "trigger_path_id": "trigger_path-generation",
                "delivery": {"results": [{"target_id": binding["effect_id"], "status": "executed",
                    "typed_outputs": {"resident_id": RESIDENT, "message_id": "msg_generation"}}]}}

    monkeypatch.setattr(lineage_workflows.isa_client, "call", trigger)
    request = {"request_id": "generation-request-current", "lineage_id": LINEAGE,
               "workbench_id": APP, "requirements": "Add a refresh button"}
    result = runtime.start_generation(request, "alice")
    assert result["status"] == "running"
    assert result["trigger_path_id"] == "trigger_path-generation"
    assert result["message_id"] == "msg_generation"
    assert result["receipt"]["submission_state"] == "submitted"
    assert result["receipt"]["request_id"] == result["id"]
    assert result["receipt"]["instruction"] == "TriggerEvent"
    assert Store().job(result["id"], "alice")["receipt"] == result["receipt"]
    assert store.job(result["id"], "alice") == result
    assert runtime.start_generation(request, "alice") == result
    assert len(calls) == 1 and calls[0][1] == "TriggerEvent"
    assert not {"run_id", "invocation_id"}.intersection(result)


@pytest.mark.parametrize("kind", ["generate", "reverse"])
@pytest.mark.parametrize("outcome", ["pending", "interrupted", "rejected"])
def test_generation_submission_recovery_preserves_handle_without_resending(store, resources, monkeypatch, kind, outcome):
    """生成任务提交结果不明时保留原回执并靠后续观察确认、不重发，提交被拒时记为失败

    :design-point:"""
    from management_portal import lineage_workflows
    from management_portal.workbench_projections import studio

    runtime = service.ProjectionService(store)
    app = store.get(APP, "alice")
    binding = {"lineage_id": LINEAGE, "resident_id": RESIDENT,
               "event_source_id": "event_source_111111111111", "effect_id": "effect_111111111111"}
    graph = lambda *_: {"lineage_id": LINEAGE, "title": "Team", "nodes": [{"id": RESIDENT}], "revision": 1}
    monkeypatch.setattr(service, "lineage_graph", graph)
    monkeypatch.setattr(lineage_workflows, "lineage_graph", graph)
    monkeypatch.setattr(runtime, "initialize", lambda *_: app)
    monkeypatch.setattr(runtime, "require_role", lambda *_, **__: (app, RESIDENT))
    monkeypatch.setattr(studio, "install", lambda *_: {"generate": binding, "optimize": binding, "reverse": binding})
    submitted, observed = [], []

    def trigger(_self, handle, **kwargs):
        submitted.append(dict(handle))
        # A process interruption must still leave a recoverable, durable locator.
        assert store.job(handle["request_id"], "alice")["receipt"] == handle
        if outcome == "interrupted":
            raise OSError("response lost")
        receipt = {"instruction_id": "inst_original", "event_id": "event_original",
                   "submission_reason": "waiting" if outcome == "pending" else "not committed",
                   "future_evidence": {"sequence": 17}}
        failure = lineage_workflows.WorkflowPending if outcome == "pending" else lineage_workflows.WorkflowFailed
        raise failure(handle, receipt)

    def observe(_self, handle, **kwargs):
        observed.append(dict(handle))
        assert kwargs["actor"] == "alice"
        if len(observed) == 1:
            raise lineage_workflows.WorkflowPending(handle, {"submission_reason": "still unconfirmed"})
        return {**handle, "submission_state": "submitted", "message_id": "msg_original", "status": "running"}

    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "trigger", trigger)
    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "observe", observe)
    inputs = []
    def read_input(**kwargs):
        inputs.append(kwargs["message_id"])
        return {"input_state": {"state": "running"}, "result": None}
    monkeypatch.setattr(service.resident_handoff, "observe_invocation", read_input)
    request = {"id": APP, "workbench_id": APP, "lineage_id": LINEAGE,
               "request_id": "recover-original-request", "requirements": "Keep the current page"}
    start = lambda: studio.start_reverse(runtime, request, "alice") if kind == "reverse" else runtime.start_generation(request, "alice")
    result = start()
    persisted = Store().job(result["id"], "alice")
    assert persisted["receipt"] == result["receipt"]
    assert start() == persisted
    assert len(submitted) == 1
    resumed = service.ProjectionService(Store())
    if outcome == "rejected":
        assert result["status"] == "failed"
        assert result["receipt"]["submission_state"] == "not_submitted"
        assert resumed.generation(result["id"], "alice") == result
        assert not observed and not inputs
        return
    assert result["status"] == "submitting"
    waiting = resumed.generation(result["id"], "alice")
    assert waiting["status"] == "submitting" and not waiting.get("message_id")
    confirmed = resumed.generation(result["id"], "alice")
    assert confirmed["status"] == "running"
    assert confirmed["message_id"] == "msg_original" and inputs == ["msg_original"]
    assert len(submitted) == 1 and len(observed) == 2
    assert all(handle["idempotency_key"] == submitted[0]["idempotency_key"] for handle in observed)
    if outcome == "pending":
        assert confirmed["receipt"]["future_evidence"] == {"sequence": 17}
        assert confirmed["receipt"]["instruction_id"] == "inst_original"


def test_legacy_generation_recovery_only_observes_original_locator(store, monkeypatch):
    """旧版提交中的生成任务恢复时只用原幂等键查询执行记录，不重新提交

    :design-point:"""
    from management_portal import lineage_workflows

    job = {"id": "legacy_generation", "status": "submitting", "workbench_id": APP,
           "lineage_id": LINEAGE, "resident_id": RESIDENT, "request_prompt": "original prompt",
           "event_source_id": "event_source_111111111111", "effect_id": "effect_111111111111"}
    store.job(job["id"], "alice", job)
    calls = []
    def read_only(actor, instruction, arguments, **kwargs):
        calls.append((instruction, arguments))
        assert instruction == "GetInstructionExecution"
        raise KeyError("no execution record yet")
    monkeypatch.setattr(lineage_workflows.isa_client, "call", read_only)
    result = service.ProjectionService(Store()).generation(job["id"], "alice")
    assert result["status"] == "submitting"
    expected = lineage_workflows.WorkflowBindings.prepare(job, request_id=job["id"])
    assert result["receipt"]["idempotency_key"] == expected["idempotency_key"]
    assert calls and calls[0][1]["idempotency_key"] == expected["idempotency_key"]
    assert store.job(job["id"], "alice")["receipt"] == result["receipt"]


@pytest.mark.usefixtures("projection_input_reader")
def test_generation_results_remain_draft(store, resources, monkeypatch):
    """设计师保存的生成结果停留在草稿，任务完成后不自动发布

    :design-point:"""
    job = {"id": "generation", "status": "running", "workbench_id": APP, "base_revision": 1, "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_test"}
    store.job("generation", "alice", job)
    monkeypatch.setattr(Store, "authorized", staticmethod(lambda app, actor, operation: actor in {"alice", RESIDENT}))
    runtime = service.ProjectionService(store)
    receipt = runtime.save_generation_result(APP, {
        "job_id": job["id"], "base_revision": 1,
        "title": "新界面", "description": "已实现", "html": "<button>刷新</button>",
    }, RESIDENT)
    def isa(actor, name, *args, **kwargs):
        if name == "GetInputState":
            return {"state": "completed", "reply_message_ids": ["msg_reply"]}
        return {"content_parts": [{"type": "text", "text": "页面已经实现并保存为草稿，请预览后发布。"}]}
    monkeypatch.setattr(service, "isa", isa)
    result = runtime.generation("generation", "alice")
    assert result["status"] == "completed"
    assert result["revision"] == receipt["revision"] == 2
    assert result["response"].startswith("页面已经实现")
    assert store.get(APP, "alice")["published_revision"] is None


@pytest.mark.parametrize("changes", [
    {"owner_ids": ["alice", "unexpected-owner"]},
    {"bindings": {"other": {"lineage_id": "lineage_222222222222"}}},
    {"navigation": "top"},
])
def test_generation_action_cannot_modify_authority_or_bindings(store, monkeypatch, changes):
    """生成结果含拥有者、绑定或导航字段时拒绝保存，工作台不变

    :design-point:"""
    original = store.get(APP, "alice")
    job = {"id": "generation_boundary", "status": "running", "workbench_id": APP, "base_revision": 1, "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_request"}
    store.job(job["id"], "alice", job)
    monkeypatch.setattr(Store, "authorized", staticmethod(lambda app, actor, operation: actor in {"alice", RESIDENT}))
    with pytest.raises(ProjectionError) as error:
        service.ProjectionService(store).save_generation_result(APP, {
            "job_id": job["id"], "base_revision": 1,
            "title": "Generated", "html": "<section>Draft</section>", **changes,
        }, RESIDENT)
    assert error.value.code == "projection.generation_fields"
    assert store.get(APP, "alice") == original


@pytest.mark.usefixtures("projection_input_reader")
def test_generation_completion_requires_resource_action_receipt(store, monkeypatch):
    """设计师只在聊天中回复页面、未经 Workbench 资源动作保存时生成任务失败

    :design-point:"""
    job = {"id": "generation_missing_receipt", "status": "running", "workbench_id": APP, "base_revision": 1, "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_request"}
    store.job(job["id"], "alice", job)
    monkeypatch.setattr(service, "isa", lambda actor, name, *args, **kwargs: (
        {"state": "completed", "reply_message_ids": ["msg_reply"]}
        if name == "GetInputState"
        else {"content_parts": [{"type": "text", "text": '{"title":"不能再从聊天解析"}'}]}
    ))
    result = service.ProjectionService(store).generation(job["id"], "alice")
    assert result["status"] == "failed"
    assert "Workbench 资源动作" in result["error"]
    assert store.get(APP, "alice")["revision"] == 1


@pytest.mark.usefixtures("projection_input_reader")
def test_generation_that_found_the_page_changed_reports_a_conflict(store, monkeypatch):
    """生成期间工作台被修改时，任务以 conflict 结束并说明版本变化与重新生成

    设计师发现工作台已不是基线版本而拒绝覆盖时，生成任务显示基线与当前版本并提示在最新版本上重新生成，而不是笼统的未保存。

    :design: decision_898b8911a2ff
    :design-point:
    """
    job = {"id": "generation_drift", "status": "running", "workbench_id": APP, "base_revision": 1, "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_request"}
    store.job(job["id"], "alice", job)
    store.update(APP, {"html": "<h1>期间被修改</h1>"}, "alice", 1)
    monkeypatch.setattr(service, "isa", lambda actor, name, *args, **kwargs: (
        {"state": "completed", "reply_message_ids": ["msg_reply"]}
        if name == "GetInputState"
        else {"content_parts": [{"type": "text", "text": "工作台已是 revision 2，未保存。"}]}
    ))
    result = service.ProjectionService(store).generation(job["id"], "alice")
    assert result["status"] == "conflict"
    assert "v1 → 当前 v2" in result["error"] and "重新生成" in result["error"]
    assert store.get(APP, "alice")["revision"] == 2


@pytest.mark.parametrize("failure", [
    {"code": "provider_usage_limit", "message": "Quota exhausted"},
    {"code": "unknown", "message": "Codex turn failed: You’ve hit your usage limit. Purchase more credits."},
])
@pytest.mark.usefixtures("projection_input_reader")
def test_generation_formats_usage_limit_failures_without_rendering_raw_dicts(store, monkeypatch, failure):
    """生成因模型额度用尽失败时显示额度不足说明，不显示原始错误字典

    :design-point:"""
    job = {"id": "generation_quota", "status": "running", "workbench_id": APP, "base_revision": 1, "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_request"}
    store.job(job["id"], "alice", job)
    monkeypatch.setattr(service, "isa", lambda *args, **kwargs: {"state": "failed", "error": failure})

    result = service.ProjectionService(store).generation(job["id"], "alice")

    assert result["status"] == "failed"
    assert result["input_error"] == failure
    assert result["error"].startswith("当前模型账号额度不足")
    assert "{'code'" not in result["error"]


@pytest.mark.usefixtures("projection_input_reader")
def test_reverse_plan_uses_resource_receipt_and_natural_reply(store, monkeypatch):
    """逆向分析任务以保存计划的回执判定完成，并采用设计师的自然语言回复

    :design-point:"""
    job = {
        "id": "reverse_receipt", "kind": "reverse", "status": "running",
        "workbench_id": APP, "base_revision": 1, "lineage_id": LINEAGE,
        "resident_id": RESIDENT, "message_id": "msg_request",
    }
    store.job(job["id"], "alice", job)
    runtime = service.ProjectionService(store)
    receipt = runtime.save_reverse_plan(APP, {
        "job_id": job["id"],
        "plan": {"summary": "复用现有成员，不新增自动入口", "steps": []},
    }, RESIDENT)
    monkeypatch.setattr(service, "isa", lambda actor, name, *args, **kwargs: (
        {"state": "completed", "reply_message_ids": ["msg_reply"]}
        if name == "GetInputState"
        else {"content_parts": [{"type": "text", "text": "分析完成：当前结构可复用，无需新增步骤。"}]}
    ))
    result = runtime.generation(job["id"], "alice")
    assert result["status"] == "completed"
    assert result["plan_hash"] == receipt["plan_hash"]
    assert result["response"].startswith("分析完成")


def test_generation_prompt_assigns_resource_action_not_json_reply(store):
    """页面生成提示要求经 generation.save 资源动作保存并用自然语言回复，不要求返回 JSON

    :design-point:"""
    app = store.get(APP, "alice")
    graph = {
        "lineage_id": LINEAGE,
        "title": "Team",
        "revision": 4,
        "nodes": [{"id": RESIDENT, "title": "Coordinator", "status": "idle"}],
    }
    prompt = service.ProjectionService(store).generation_prompt(app, graph, "实现交互", "job_contract")
    assert "generation.save" in prompt
    assert "最终只返回 JSON" not in prompt
    assert "自然语言" in prompt
    assert f'"id": "{RESIDENT}"' in prompt
    assert '"resident_id"' not in prompt
    assert "graph nodes do not expose a duplicate `resident_id` alias" in prompt


def test_invalid_generation_draft_does_not_poison_retry(store, monkeypatch):
    """不合规的生成草稿被拒且不留待保存状态，之后提交合规草稿可正常保存

    :design-point:"""
    job = {"id": "generation_retry", "status": "running", "workbench_id": APP, "base_revision": 1, "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_request"}
    store.job(job["id"], "alice", job)
    monkeypatch.setattr(Store, "authorized", staticmethod(lambda app, actor, operation: actor in {"alice", RESIDENT}))
    runtime = service.ProjectionService(store)
    with pytest.raises(ProjectionError) as invalid:
        runtime.save_generation_result(APP, {
            "job_id": job["id"], "base_revision": 1,
            "html": "<html><body>invalid</body></html>",
        }, RESIDENT)
    assert invalid.value.code == "projection.template"
    assert "pending_save" not in store.job(job["id"], "alice")
    receipt = runtime.save_generation_result(APP, {
        "job_id": job["id"], "base_revision": 1,
        "html": "<section>valid</section>",
    }, RESIDENT)
    assert receipt["revision"] == 2


def test_source_limits_and_binding_identity(store):
    """工作台页面源码超过长度上限或绑定非法 Lineage ID 时拒绝保存

    :design-point:"""
    with pytest.raises(ProjectionError):
        store.update(APP, {"html": "x" * 400001}, "alice", 1)
    with pytest.raises(ProjectionError):
        store.update(APP, {"bindings": {"main": {"lineage_id": "guessed-name"}}}, "alice", 1)


def test_publish_rejects_standalone_html_template(store, monkeypatch):
    """发布时页面是独立完整的 HTML 文档则被拒绝

    :design-point:"""
    standalone = {**store.get(APP, "alice"), "html": "<html><body>standalone page</body></html>"}
    monkeypatch.setattr(store, "get", lambda *args, **kwargs: standalone)
    with pytest.raises(ProjectionError) as error:
        store.publish(APP, "alice", 1)
    assert error.value.code == "projection.template"


def test_catalog_uses_published_manifest_and_resumes_durable_job(store):
    """工作台目录显示已发布版本的标题，最近一次生成任务可从存储中取回

    :design-point:"""
    store.publish(APP, "alice", 1)
    store.update(APP, {"title": "尚未发布的名称"}, "alice", 1)
    runtime = service.ProjectionService(store)
    assert runtime.catalog("alice")["workbenches"][0]["title"] == "试验台"
    job = {"id": "job-one", "workbench_id": APP, "status": "running"}
    store.job(job["id"], "alice", job)
    assert store.latest_generation(APP, "alice") == {**job, "created_by": "alice"}


def test_navigation_changes_without_publishing_source(store):
    """修改工作台导航位置立即生效，不连带发布未发布的页面源码

    :design-point:"""
    store.publish(APP, "alice", 1)
    store.update(APP, {"navigation": "top", "html": "<h1>未发布</h1>"}, "alice", 1)
    published = store.get(APP, "alice", published=True)
    assert published["navigation"] == "top"
    assert "第一版" in published["html"]
    store.update(APP, {"navigation": "menu"}, "alice", 2)
    assert service.ProjectionService(store).catalog("alice")["workbenches"][0]["navigation"] == "menu"


def test_a_resident_reads_any_workbench_and_updates_only_when_permitted(store, permissions):
    """Resident 可读取任意工作台，权限系统给到 contributor 后才能修改

    任何 Resident 都可以打开工作台；权限系统没给到 contributor 的 Resident 修改时被拒绝，获得授权后可以修改。

    :importance: P1
    :aspects: functional, behavior
    :design-point:
    """
    place_workbenches(permissions)
    assert store.get(APP, RESIDENT)["title"] == "试验台"
    with pytest.raises(PermissionError, match="无权修改"):
        store.update(APP, {"title": "updated"}, RESIDENT, 1)
    permissions.grant(RESIDENT, APP, "@contributor")
    assert store.update(APP, {"title": "updated"}, RESIDENT, 1)["title"] == "updated"


def test_resource_api_rejects_actor_override(store, monkeypatch):
    """工作台资源接口拒绝在参数中覆盖调用者 actor

    :design-point:"""
    from management_portal.workbench_projections import http
    monkeypatch.setattr(http, "get_service", lambda: service.ProjectionService(store))
    with pytest.raises(PermissionError):
        http.post("/api/workbenches/resource", {"id": APP, "operation": "query", "arguments": {"actor": "admin"}}, "alice")


def test_resource_read_payload_preserves_text_and_binary(monkeypatch):
    """工作台资源读取按文本或 base64 返回内容，按实际字节重算 content_sha256 并可解析 YAML

    :design-point:"""
    import hashlib
    from types import SimpleNamespace
    from management_portal import resources
    from management_portal.resources.content_payload import content_payload
    from management_portal.workbench_projections import http
    text = content_payload(APP, "资源正文".encode(), "application/json; charset=utf-8")
    assert text["content"] == "资源正文"
    assert text["encoding"] == "utf-8"
    binary = content_payload(APP, b"\x00\xff", "image/png")
    assert binary["encoding"] == "base64"
    assert binary["content"] == "AP8="

    raw = "title: 架构\r\n".encode()
    metadata = {"record": {"version": {"number": 7}}, "content_sha256": "outdated"}
    callers = []

    def read_content(address, *, actor, max_bytes):
        from management_portal.workbench_projections.resource_access import RESOURCE_READ_MAX_BYTES
        assert max_bytes == RESOURCE_READ_MAX_BYTES
        callers.append((address, actor))
        return raw, "application/yaml", metadata

    monkeypatch.setattr(http, "get_service", lambda: SimpleNamespace(store=SimpleNamespace(get=lambda *_: {})))
    monkeypatch.setattr(resources, "get_resource_service", lambda: SimpleNamespace(read_content=read_content))
    payload = {"id": APP, "operation": "read", "arguments": {"address": "repo_111111111111/file/modules.yaml"}}
    plain = http.post("/api/workbenches/resource", payload, "alice")
    structured = http.post("/api/workbenches/resource", {**payload, "arguments": {**payload["arguments"], "format": "yaml"}}, "alice")
    assert structured["data"] == {"title": "架构"}
    assert plain["metadata"]["content_sha256"] == structured["metadata"]["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert structured["metadata"]["record"]["version"]["number"] == 7
    assert metadata["content_sha256"] == "outdated"  # Never mutate provider metadata.
    assert callers == [(payload["arguments"]["address"], "alice")] * 2
    raw += b"# changed between reads\n"
    changed = http.post("/api/workbenches/resource", payload, "alice")
    assert changed["metadata"]["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert changed["metadata"]["content_sha256"] != structured["metadata"]["content_sha256"]


def test_isa_delivery_keeps_call_controls_out_of_the_business_message(monkeypatch):
    """DeliverMessage 把 business_ref 等调用控制放进投递上下文，不混入业务消息

    :design-point:"""
    from types import SimpleNamespace

    from management_portal.society_service import resident_protocol
    from management_portal.society_service.isa import conversation
    observed = {}

    def deliver(payload, actor, **kwargs):
        observed.update(payload=payload, actor=actor, context=kwargs["delivery_context"])
        return {}

    monkeypatch.setattr(resident_protocol, "deliver_resident_input", deliver)
    monkeypatch.setattr(conversation, "lineage_of_resident", lambda _resident_id: LINEAGE)
    arguments = {"resident_id": RESIDENT, "business_ref": "generation-1",
                 "message": {"role": "user", "content_parts": [{"type": "text", "text": "hi"}]}}
    conversation.deliver_message(SimpleNamespace(
        argument=arguments.get, caller="alice", idempotency_key="stable-delivery", context=SimpleNamespace(trigger_path_id=""),
    ))
    assert set(observed["payload"]) == {"resident_id", "message", "activation_policy"}
    assert set(observed["payload"]["message"]) == {"message_id", "role", "content_parts"}
    assert observed["payload"]["message"]["message_id"]
    assert observed["context"].business_ref == "generation-1"
    assert observed["context"].lineage_id == LINEAGE


@pytest.fixture
def projection_input_reader(monkeypatch):
    from tests.cases.workbench_support import bind_resident_observation
    from management_portal.workbench_projections import service
    bind_resident_observation(monkeypatch, lambda *args, **kwargs: service.isa(*args, **kwargs))


def test_role_retry_reuses_logical_slot_after_parameters_change(store, resources, monkeypatch):
    """角色 Resident 创建应答丢失后，参数已变的重试仍复用同一角色槽位和原请求

    :design-point:"""
    calls = []
    nodes = [{"id": RESIDENT}]
    fake_team(monkeypatch, nodes, create=creator(nodes, calls, fail="transport"))
    runtime = service.ProjectionService(store)
    with pytest.raises(TimeoutError):
        runtime.require_role(store.get(APP, "alice"), "alice", "discussion", title="Original", backend_route_id="one")
    app, rid = runtime.require_role(store.get(APP, "alice"), "alice", "discussion", title="Changed", backend_route_id="another")
    assert rid == "resident_222222222222"
    assert calls[0] == calls[1]
    assert len(nodes) == 2
    assert app["bindings"]["main"]["residents"]["discussion"] == rid


def test_generation_keeps_observing_until_all_replies_are_readable(store, monkeypatch, subtests):
    """生成任务的回复为空或尚不可读时保持运行，可读后才完成，无权读取时报错

    :design-point:"""
    from types import SimpleNamespace
    job = {"id": "visibility", "status": "running", "workbench_id": APP, "base_revision": 1,
           "lineage_id": LINEAGE, "resident_id": RESIDENT, "message_id": "msg_request",
           "save_receipt": {"revision": 2}}
    store.job(job["id"], "alice", job)
    visibility = "empty"

    def read_message(rid, actor, message_id):
        assert rid == RESIDENT and actor == "alice" and message_id == "msg_reply"
        if visibility == "missing":
            raise KeyError(message_id)
        if visibility == "forbidden":
            raise PermissionError("reply access revoked")
        return {"message_id": message_id, "content_parts": [{"type": "text", "text": "saved"}]} if visibility == "ready" else {}

    monkeypatch.setattr(service.resident_handoff, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(
        get_resident_input_state=lambda *_: {"state": "completed", "reply_message_ids": ["msg_reply"]},
        get_resident_message=read_message)))
    runtime = service.ProjectionService(store)
    for visibility in ("empty", "missing", "forbidden"):
        with subtests.test(visibility=visibility):
            if visibility == "forbidden":
                with pytest.raises(PermissionError, match="reply access revoked"):
                    runtime.generation(job["id"], "alice")
            else:
                assert runtime.generation(job["id"], "alice")["status"] == "running"
            assert store.job(job["id"], "alice")["status"] == "running"
    visibility = "ready"
    assert runtime.generation(job["id"], "alice")["status"] == "completed"
    assert store.job(job["id"], "alice")["message_id"] == "msg_request"


def test_completed_role_request_keeps_stable_slot_before_binding_write(store, resources, monkeypatch):
    """角色 Resident 已创建但写入绑定失败时，重试沿用该 Resident 不再新建

    :design-point:"""
    import hashlib
    definition = {"title": "Original", "backend": "codex", "runtime_settings": {}}
    slot = hashlib.sha256("discussion".encode()).hexdigest()[:24]
    rid = "resident_222222222222"
    store.save_role_request(APP, LINEAGE, slot, "alice", {
        "request": {"lineage_id": LINEAGE, "request_id": "invocation-create:fixture", "arguments": definition},
        "resident_id": rid,
    })
    # An accepted creation is never replaced: no Resident may be created again.
    fake_team(monkeypatch, [{"id": RESIDENT}, {"id": rid}])
    runtime = service.ProjectionService(store)
    update = runtime.update
    monkeypatch.setattr(runtime, "update", lambda *args: (_ for _ in ()).throw(OSError("association failed")))
    with pytest.raises(OSError, match="association failed"):
        runtime.require_role(store.get(APP, "alice"), "alice", "discussion", title="Original")
    monkeypatch.setattr(runtime, "update", update)
    app, actual = runtime.require_role(store.get(APP, "alice"), "alice", "discussion", title="Changed")
    assert actual == rid
    assert app["bindings"]["main"]["residents"]["discussion"] == rid


def test_a_new_role_is_refused_before_creation_when_the_published_page_has_a_broken_binding(store, resources, monkeypatch):
    """已发布工作台存在失效绑定时，新角色在创建前被拒绝，不留下未发布草稿

    已发布页面的某个角色指向已删除成员时，创建新角色先检查绑定可发布并报告失效角色，不创建 Resident、不修改页面，避免之后所有角色创建都因未发布草稿失败。

    :design: decision_898b8911a2ff
    :design-point:
    """
    # The page's coordinator was deleted elsewhere (the role still names it). Creating another role must not
    # create a Resident and leave an unpublished draft that blocks every later role.
    store.publish(APP, "alice", 1)
    calls = []
    nodes = []
    fake_team(monkeypatch, nodes, create=creator(nodes, calls))
    runtime = service.ProjectionService(store)
    with pytest.raises(ProjectionError) as error:
        runtime.require_role(store.get(APP, "alice"), "alice", "discussion", title="Discussion")
    assert error.value.code == "projection.resident_unbound" and "coordinator" in str(error.value)
    assert calls == []
    app = store.get(APP, "alice")
    assert app["revision"] == 1 and app["published_revision"] == 1
    assert "discussion" not in app["bindings"]["main"]["residents"]


def test_a_deleted_resident_of_a_role_the_page_created_does_not_block_other_roles(store, resources, monkeypatch):
    """工作台按需创建的角色其 Resident 被删除后不阻断其他角色创建，并可按需重建

    :design-point:
    """
    # WALL creates one role per project; a TAU cleanup deleted one of those Residents and
    # every later WALL role, for every project, failed the binding check.
    import hashlib
    gone = "resident_999999999999"
    old_role = "wall-work:project-old:task"
    slot = hashlib.sha256(old_role.encode()).hexdigest()[:24]
    store.save_role_request(APP, LINEAGE, slot, "alice", {"request": {"lineage_id": LINEAGE}, "resident_id": gone})
    app = store.get(APP, "alice")
    app["bindings"]["main"]["residents"][old_role] = gone
    store.update(APP, {"bindings": app["bindings"]}, "alice", 1)
    store.publish(APP, "alice", 2)
    calls = []
    nodes = [{"id": RESIDENT}]
    fake_team(monkeypatch, nodes, create=creator(nodes, calls))
    runtime = service.ProjectionService(store)

    app, created = runtime.require_role(store.get(APP, "alice"), "alice", "wall-work:project-new:task", title="New")

    residents = app["bindings"]["main"]["residents"]
    assert residents["wall-work:project-new:task"] == created and len(calls) == 1
    assert old_role not in residents and residents["coordinator"] == RESIDENT
    assert app["published_revision"] == app["revision"]
    assert store.role_request(APP, LINEAGE, slot) == {}
    # Asked for again, the dropped role is created anew instead of returning the deleted Resident.
    app, recreated = runtime.require_role(store.get(APP, "alice"), "alice", old_role, title="Old")
    assert recreated not in {gone, created} and len(calls) == 2


@pytest.mark.parametrize("failure", ["deleted", "forbidden", "outage", None])
def test_workbench_detail_reports_bindings_as_they_are_now(monkeypatch, failure):
    """工作台详情报告绑定的当前状态

    绑定的 Lineage 已删除或无权访问时，详情返回 binding_issues 与 binding_notice；Lineage 服务不可用时详情照常返回。

    A Lineage deleted after binding shows as a binding issue, not as an earlier applied state.

    :design-point:
    """
    from types import SimpleNamespace

    from management_portal.workbench_projections import http, native

    app = {"workbench_id": APP, "revision": 3, "bindings": {"main": {"lineage_id": LINEAGE, "residents": {"coordinator": RESIDENT}}}}
    store = SimpleNamespace(get=lambda *_args, **_kwargs: dict(app), latest_generation=lambda *_args: {"kind": "reverse", "applied": True},
                            authorized=lambda *_args: True)
    monkeypatch.setattr(http, "get_service", lambda: service.ProjectionService(store))

    def graph(_actor, lineage_id):
        assert lineage_id == LINEAGE
        if failure == "deleted":
            raise KeyError(lineage_id)
        if failure == "forbidden":
            raise PermissionError(lineage_id)
        if failure == "outage":
            raise RuntimeError("SocietyService unavailable")
        return {"nodes": [{"id": RESIDENT}], "program": {"components": []}}

    monkeypatch.setattr(service, "lineage_graph", graph)
    monkeypatch.setattr(native, "members", lambda current: {node["id"] for node in current["nodes"]})
    detail = http.get("/api/workbenches/detail", {"id": [APP]}, "alice")
    assert detail["latest_generation"]["applied"] is True
    if failure in {"deleted", "forbidden"}:
        code = "projection.lineage_unbound" if failure == "deleted" else "projection.lineage_forbidden"
        assert [issue["code"] for issue in detail["binding_issues"]] == [code]
        assert LINEAGE in detail["binding_notice"]
    elif failure == "outage":
        # The status is advisory: the settings page still loads.
        assert "binding_issues" not in detail
    else:
        assert detail["binding_issues"] == [] and "binding_notice" not in detail


def test_release_business_role_retries_delete_and_cleans_owned_workflow(store, resources, monkeypatch):
    """释放工作台角色时删除失败可用同一幂等键重试，最终移除绑定及其工作流组件

    :design-point:"""
    from management_portal.workflow_bindings_store import get_workflow_binding_store
    nodes = [{'id': RESIDENT}]
    flow_store = get_workflow_binding_store()
    flow_store.save({'lineage_id': LINEAGE, 'resident_id': RESIDENT, 'name': 'work',
                     'steps': {'event': 'event-one', 'effect': 'effect-one'}, 'version': 0})
    components = {'event-one', 'effect-one'}
    monkeypatch.setattr(service, 'lineage_graph', lambda *a: {
        'nodes': nodes, 'program': {'components': [{'id': c} for c in components]}})
    calls, fail = [], [True]
    def invoke(actor, name, args, **kwargs):
        calls.append((name, args, kwargs.get('key')))
        if name == 'ListResidentStates':
            return {'states': [{'resident_id': RESIDENT, 'state': {'work_state': 'idle', 'queued_count': 0}}]}
        if name == 'GetResident':
            return {'resident': {'resident_id': RESIDENT, 'lineage_id': LINEAGE}}
        if name == 'GetLineage':
            return {'lineage': {'revision': 1, 'nodes': nodes, 'program': {'components': [{'id': c} for c in components]}}}
        if name == 'ApplyLineageChanges':
            if fail[0]:
                fail[0] = False
                raise RuntimeError('temporary failure')
            components.clear()
            nodes.clear()
        return {}
    monkeypatch.setattr(service, 'isa', invoke)
    monkeypatch.setattr(service.isa_client, 'call', invoke)
    runtime = service.ProjectionService(store)
    store.publish(APP, 'alice', 1)
    with pytest.raises(RuntimeError, match='temporary failure'):
        runtime.release_roles(store.get(APP, 'alice'), 'alice', {'coordinator': RESIDENT})
    assert store.get(APP, 'alice')['bindings']['main']['residents'] == {'coordinator': RESIDENT}
    assert runtime.release_roles(store.get(APP, 'alice'), 'alice', {'coordinator': RESIDENT}) == {RESIDENT}
    assert not store.get(APP, 'alice', published=True)['bindings']['main']['residents']
    assert not components
    deletes = [key for name, _, key in calls if name == 'ApplyLineageChanges']
    assert len(deletes) == 2 and len(set(deletes)) == 1


@pytest.mark.parametrize('state', [{'work_state': 'running'}, {'work_state': 'idle', 'queued_count': 1}])
def test_release_role_preflights_busy_and_preserves_other_references(store, resources, monkeypatch, state):
    """角色 Resident 仍在工作或有排队时拒绝释放，被其他角色引用时只解除该角色绑定

    :design-point:"""
    fake_team(monkeypatch, [{'id': RESIDENT}])
    monkeypatch.setattr(service, 'isa', lambda actor, name, args, **kwargs: (
        {'states': [{'resident_id': RESIDENT, 'state': state}]} if name == 'ListResidentStates' else pytest.fail('must not mutate busy Resident')))
    runtime = service.ProjectionService(store)
    with pytest.raises(ProjectionError, match='仍在工作'):
        runtime.release_roles(store.get(APP, 'alice'), 'alice', {'coordinator': RESIDENT})
    app = store.get(APP, 'alice')
    app['bindings']['main']['residents']['another-item'] = RESIDENT
    store.update(APP, {'bindings': app['bindings']}, 'alice', app['revision'])
    assert runtime.release_roles(store.get(APP, 'alice'), 'alice', {'coordinator': RESIDENT}) == set()
    assert store.get(APP, 'alice')['bindings']['main']['residents'] == {'another-item': RESIDENT}


def test_release_role_recovers_interrupted_publication(store, resources, monkeypatch):
    """释放角色时发布中断，重试后完成并从已发布版本中移除绑定

    :design-point:"""
    nodes = [{'id': RESIDENT}]
    fake_team(monkeypatch, nodes)
    def invoke(actor, name, args, **kwargs):
        if name == 'ListResidentStates':
            return {'states': [{'state': {'work_state': 'idle'}}]}
        if name == 'DeleteResident':
            nodes.clear()
        return {}
    monkeypatch.setattr(service, 'isa', invoke)
    monkeypatch.setattr(service.resident_handoff, 'release_resident', lambda **kw: nodes.clear())
    store.publish(APP, 'alice', 1)
    runtime = service.ProjectionService(store)
    publish = runtime.publish
    monkeypatch.setattr(runtime, 'publish', lambda *a: (_ for _ in ()).throw(RuntimeError('publish interrupted')))
    with pytest.raises(RuntimeError, match='publish interrupted'):
        runtime.release_roles(store.get(APP, 'alice'), 'alice', {'coordinator': RESIDENT})
    monkeypatch.setattr(runtime, 'publish', publish)
    runtime.release_roles(store.get(APP, 'alice'), 'alice', {'coordinator': RESIDENT})
    assert not store.get(APP, 'alice', published=True)['bindings']['main']['residents']
