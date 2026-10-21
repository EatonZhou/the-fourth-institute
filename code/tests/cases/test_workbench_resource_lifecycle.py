""":importance: P2"""
import ast
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from management_portal import serve_management_portal
from management_portal.resources import ResourceOperationError
from management_portal.resources.server import ResourceServiceServer
from management_portal.resources.transport import ResourceServiceClient
from management_portal.workbench_projections import http, service
from management_portal.workbench_projections.store import Store
from tests.cases.workbench_support import bind_resource_service


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    store = Store()
    resources = bind_resource_service(store, tmp_path, monkeypatch)
    key = tmp_path / "rpc.key"
    key.write_bytes(b"workbench-lifecycle-isolated-test-key-32-bytes")
    key.chmod(0o600)
    monkeypatch.setenv("PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE", str(key))
    server = ResourceServiceServer(tmp_path / "resource.sock", service=resources, publisher=mock.Mock())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(2)
    client = ResourceServiceClient(server.socket_path, timeout=2)
    monkeypatch.setattr(service, "get_resource_service", lambda: client)
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(http, "get_service", lambda: runtime)
    try:
        yield runtime, store, resources, client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def create_page():
    return http.post("/api/workbenches/create", {"title": "Lifecycle", "html": "<h1>Draft</h1>", "project_ids": ["@public"]}, "alice")


def test_browser_http_routes_complete_resource_lifecycle(lifecycle, monkeypatch):
    """浏览器经 HTTP 路由完成工作台创建、保存、发布和删除，删除后详情返回 404

    创建时必须给出位置：没有 project_ids 或 @public 与 Project 同时给出都返回 400；公共内容写 @public，保存为不属于任何 Project。
    """
    monkeypatch.setattr(serve_management_portal, "_check_session", lambda handler: True)
    monkeypatch.setattr(serve_management_portal, "_get_session_username", lambda handler: "alice")
    class Handler(serve_management_portal.Handler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    def post(action, payload):
        request = urllib.request.Request(
            f"{base}/api/workbenches/{action}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    try:
        with pytest.raises(urllib.error.HTTPError) as unplaced:
            post("create", {"title": "HTTP lifecycle", "html": "<h1>Draft</h1>"})
        assert unplaced.value.code == 400 and "@public" in unplaced.value.read().decode()
        with pytest.raises(urllib.error.HTTPError) as mixed:
            post("create", {"title": "HTTP lifecycle", "html": "<h1>Draft</h1>", "project_ids": ["@public", "AI员工"]})
        assert mixed.value.code == 400
        app = post("create", {"title": "HTTP lifecycle", "html": "<h1>Draft</h1>", "project_ids": ["@public"]})
        assert app["project_ids"] == []
        app_id = app["workbench_id"]
        updated = post("update", {"id": app_id, "expected_revision": 1, "title": "Saved"})
        assert updated["revision"] == 2
        published = post("publish", {"id": app_id, "expected_revision": 2})
        assert published["published_revision"] == 2
        assert post("delete", {"id": app_id}) == {"deleted": app_id}
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{base}/api/workbenches/detail?id={app_id}", timeout=5)
        assert raised.value.code == 404
        assert json.loads(raised.value.read())["code"] == "projection.not_found"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_page_mutations_use_real_rpc_and_one_resource_lifecycle(lifecycle, monkeypatch):
    """工作台创建、更新、发布和删除经 ResourceService RPC 完成登记、变更通知与注销"""
    runtime, store, resources, client = lifecycle
    app = create_page()
    app_id = app["workbench_id"]
    notify = resources.notify_source_changed
    resources._index().project_registered.assert_called_once_with("workbench", mock.ANY)
    assert resources._index().project_registered.call_args.args[1]["resource_id"] == app_id
    notify.assert_not_called()
    assert client.resolve(app_id, actor="alice")["detail"]["title"] == "Lifecycle"
    notify.reset_mock()

    updated = http.post("/api/workbenches/update", {
        "id": app_id, "expected_revision": app["revision"], "title": "Updated",
    }, "alice")
    assert updated["revision"] == 2
    assert updated["published_revision"] is None
    notify.assert_called_once_with(resource_id=app_id, resource_type="workbench", kind="updated")
    notify.reset_mock()

    published = http.post("/api/workbenches/publish", {"id": app_id, "expected_revision": 2}, "alice")
    assert published["published_revision"] == published["revision"] == 2
    assert store.get(app_id, "alice", published=True)["title"] == "Updated"
    notify.assert_called_once_with(resource_id=app_id, resource_type="workbench", kind="updated")
    notify.reset_mock()

    forget = mock.Mock(wraps=resources._registrations.forget)
    monkeypatch.setattr(resources._registrations, "forget", forget)
    monkeypatch.setattr(service, "isa", lambda *args, **kwargs: pytest.fail("Deleting a page must not change Lineage"))
    assert http.post("/api/workbenches/delete", {"id": app_id}, "alice") == {"deleted": app_id}
    forget.assert_called_once_with(app_id)
    notify.assert_called_once_with(resource_id=app_id, resource_type="workbench", kind="deleted")
    with pytest.raises(ResourceOperationError) as raised:
        client.resolve(app_id, actor="alice")
    assert raised.value.http_status == 404


@pytest.mark.parametrize("route,payload,actor,status", [
    ("update", {"expected_revision": 0, "title": "Stale"}, "alice", 409),
    ("update", {"expected_revision": 1, "title": ""}, "alice", 400),
    ("publish", {"expected_revision": 0}, "alice", 409),
    ("update", {"expected_revision": 1, "published_revision": 1, "html": "<h1>Unreviewed</h1>"}, "alice", 400),
    ("update", {"expected_revision": 1, "published_revision": 2}, "alice", 400),
])  # Another user's change is not a rejection in open mode (every valid User is let through).
def test_rejections_do_not_mutate_or_notify(lifecycle, route, payload, actor, status):
    """工作台更新或发布因版本过期、标题为空或直接改发布版本被拒时不修改也不通知"""
    runtime, store, resources, client = lifecycle
    app = create_page()
    resources.notify_source_changed.reset_mock()
    with pytest.raises((ResourceOperationError, PermissionError)) as raised:
        http.post(f"/api/workbenches/{route}", {"id": app["workbench_id"], **payload}, actor)
    actual_status = 403 if isinstance(raised.value, PermissionError) else raised.value.http_status
    assert actual_status == status
    assert store.get(app["workbench_id"], "alice") == app
    resources.notify_source_changed.assert_not_called()


def test_failed_provider_delete_does_not_release_registration(lifecycle, monkeypatch):
    """工作台存储删除失败时不注销资源登记，也不发出变更通知"""
    runtime, store, resources, client = lifecycle
    app = create_page()
    resources.notify_source_changed.reset_mock()
    forget = mock.Mock(wraps=resources._registrations.forget)
    monkeypatch.setattr(resources._registrations, "forget", forget)
    monkeypatch.setattr(store, "delete", mock.Mock(side_effect=RuntimeError("injected deletion failure")))
    with pytest.raises(RuntimeError, match="injected deletion failure"):
        runtime.delete(app["workbench_id"], "alice")
    forget.assert_not_called()
    resources.notify_source_changed.assert_not_called()
    assert client.resolve(app["workbench_id"], actor="alice")["exists"] is True


def test_generation_action_updates_draft_and_cannot_publish(lifecycle, monkeypatch):
    """generation.save 资源动作只更新草稿并发出变更通知，不发布页面"""
    runtime, store, resources, client = lifecycle
    app = create_page()
    store.job("generation", "alice", {
        "id": "generation", "status": "running", "workbench_id": app["workbench_id"],
        "base_revision": 1, "lineage_id": "unused", "resident_id": "alice", "message_id": "unused",
    })
    receipt = client.action(app["workbench_id"], "generation.save", {
        "job_id": "generation", "base_revision": 1,
        "title": "Generated", "description": "Implemented", "html": "<h1>Generated</h1>",
    }, actor="alice")
    assert receipt["revision"] == 2
    # The designer's input and reply are read through the Resident invocation observer.
    from management_portal import resident_handoff

    monkeypatch.setattr(resident_handoff, "observe_invocation", lambda **_kwargs: {
        "input_state": {"state": "completed", "reply_message_ids": ["reply"]},
        "result": {"message": "已直接实现页面并保存草稿。"},
    })
    job = runtime.generation("generation", "alice")
    current = store.get(app["workbench_id"], "alice")
    assert job["status"] == "completed"
    assert current["revision"] == 2
    assert current["published_revision"] is None
    assert "Generated" in current["html"]
    resources.notify_source_changed.assert_called_with(resource_id=app["workbench_id"], resource_type="workbench", kind="updated")


def test_generation_action_rejects_stale_or_privileged_fields(lifecycle):
    """generation.save 资源动作在基线过期时返回 409，带发布版本等特权字段时被拒绝"""
    runtime, store, resources, client = lifecycle
    app = create_page()
    store.job("generation", "alice", {
        "id": "generation", "status": "running", "workbench_id": app["workbench_id"],
        "base_revision": 1, "lineage_id": "unused", "resident_id": "alice", "message_id": "unused",
    })
    runtime.update(app["workbench_id"], {"expected_revision": 1, "title": "Human edit"}, "alice")
    with pytest.raises(ResourceOperationError) as stale:
        client.action(app["workbench_id"], "generation.save", {
            "job_id": "generation", "base_revision": 1, "html": "<h1>Generated</h1>",
        }, actor="alice")
    assert stale.value.http_status == 409
    with pytest.raises(ResourceOperationError) as privileged:
        client.action(app["workbench_id"], "generation.save", {
            "job_id": "generation", "base_revision": 1, "html": "<h1>Generated</h1>", "published_revision": 2,
        }, actor="alice")
    assert privileged.value.code == "projection.generation_fields"


def test_page_preferences_do_not_change_resource_revision(lifecycle):
    """保存工作台个人偏好不改变页面 revision，也不发出变更通知"""
    runtime, store, resources, client = lifecycle
    app = create_page()
    resources.notify_source_changed.reset_mock()
    http.post("/api/workbenches/state", {"id": app["workbench_id"], "value": {"filter": "active"}}, "alice")
    assert store.state(app["workbench_id"], "alice") == {"filter": "active"}
    assert store.get(app["workbench_id"], "alice")["revision"] == 1
    resources.notify_source_changed.assert_not_called()


def test_native_entrypoint_deletion_preserves_authority_until_replaced(lifecycle):
    """权威工作台删除保护与显式替换

    正式 Resource 注销拒绝删除活跃入口且不发布变更；非入口与完成替换的旧工作台可删除，权威绑定不受影响。

    :module: workflow.project
    :importance: P1
    """
    runtime, store, resources, client = lifecycle
    first = create_page()
    first_id = first["workbench_id"]
    second_id = "workbench_222222222222"
    second = store.create(second_id, {"title": "Replacement", "builtin": "paper"}, "alice")
    store.publish(second_id, "alice", second["revision"])
    store.activate_builtin(second_id, "alice", previous_app_id="")
    resources.notify_source_changed.reset_mock()
    with pytest.raises(ResourceOperationError) as failure:
        client.unregister(second_id, actor="alice")
    assert failure.value.code == "projection.active_entrypoint"
    assert failure.value.http_status == 409
    assert store.builtin("paper", "alice")["workbench_id"] == second_id
    assert store.get(second_id, "alice")["published_revision"] == second["revision"]
    resources.notify_source_changed.assert_not_called()
    assert http.post("/api/workbenches/delete", {"id": first_id}, "alice") == {"deleted": first_id}
    assert store.builtin("paper", "alice")["workbench_id"] == second_id
    replacement_id = "workbench_333333333333"
    replacement = store.create(replacement_id, {"title": "New authority", "builtin": "paper"}, "bob")
    replacement = store.update(replacement_id, {"title": "Shared authority"}, "bob", replacement["revision"])
    store.publish(replacement_id, "bob", replacement["revision"])
    store.activate_builtin(replacement_id, "alice", previous_app_id=second_id)
    assert http.post("/api/workbenches/delete", {"id": second_id}, "alice") == {"deleted": second_id}
    assert store.builtin("paper", "alice")["workbench_id"] == replacement_id


def test_only_provider_application_may_write_workbench_definitions_directly():
    """工作台投影代码中只有 apply_update 直接调用 Store 写入或发布定义"""
    root = Path(service.__file__).parent
    direct_writes = set()
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(function):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                if call.func.attr not in {"create", "update", "publish", "delete"}:
                    continue
                target = call.func.value
                if isinstance(target, ast.Attribute) and target.attr == "store":
                    direct_writes.add((path.name, function.name, call.func.attr))
    assert direct_writes == {("service.py", "apply_update", "update"), ("service.py", "apply_update", "publish")}
