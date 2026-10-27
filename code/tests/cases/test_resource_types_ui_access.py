from types import SimpleNamespace

from management_portal.http_portal_session import PortalSessionHttp


def test_resource_type_page_access_uses_separate_read_and_write_permissions(monkeypatch):
    """资源类型管理入口分别取得查看与修改权限。

    共享 viewer 可以读取类型目录，但没有 Portal 运维 admin 时不可修改；页面身份接口保留两个独立判定。

    :module: resource.service
    :design: decision_355ed742bca5
    :aspects: behavior
    """
    from management_portal import permission_system

    monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, arguments=None: actor == "reader" and instruction == "ListResourceTypes")
    monkeypatch.setattr(permission_system, "user_category", lambda actor: "member")
    monkeypatch.setattr(permission_system, "mode", lambda: "par")
    http = PortalSessionHttp(
        respond=lambda request, status, payload: payload, exception_response=None, read_json=None,
        session_actor=lambda request: "reader", access=lambda: SimpleNamespace(user=lambda actor: {}),
        avatar=lambda actor: None, instance="dev", build_id="",
        preferences=lambda actor: {}, set_preferences=None, conversation_payload=None, workspace_adapter=None,
    )
    flags = http.me(None, None, "")["access"]["allows"]
    assert flags["ListResourceTypes"] is True
    assert flags["UpdateResourceType"] is False
    assert flags["CreateUser"] is False


def test_resource_type_details_keep_original_declaration_apart_from_capabilities(monkeypatch):
    """声明全文保留声明值，运行能力单独返回。

    提供者暂不支持登记时，管理页仍可查看声明文件的原始 registerable 值。

    :module: resource.service
    :design: decision_355ed742bca5
    :aspects: behavior
    """
    from management_portal.resources import isa

    declaration = {"type": "paper", "registerable": True, "title": {"zh": "论文", "en": "Paper"}}
    row = {**declaration, "installed": True, "enabled": True, "state": "enabled"}
    service = SimpleNamespace(
        resource_types=SimpleNamespace(get=lambda kind: row, identity=SimpleNamespace(declaration=lambda kind: declaration.copy())),
        capabilities=lambda kind: {"registerable": False, "actions": []},
    )
    monkeypatch.setattr(isa, "_service", lambda: service)
    result = isa._resource_type_details("paper", counts={"paper": 2})
    assert result["registerable"] is False
    assert result["declaration"] == declaration
    assert "state" not in result["declaration"]
    assert result["instance_count"] == 2
