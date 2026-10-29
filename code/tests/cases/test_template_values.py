import pytest

from management_portal.society_service.templates import kinds


def test_registry_contract_and_typed_parameters():
    """模板参数按类型填默认值并渲染时长，类型不符或引用缺失时报错"""
    from management_portal.society_service.templates.registry import (
        ApplyContext,
        TemplateError,
    )
    from management_portal.society_service.templates.values import parameters, render

    definitions = [
        {"name": "seconds", "type": "duration", "default": 60},
        {"name": "role", "type": "enum", "options": ["critic"], "default": "critic"},
    ]
    supplied = parameters(definitions, {})
    assert supplied == {"seconds": 60, "role": "critic"}
    assert render(
        {"period": "{{param.seconds}}", "text": "每 {{param.seconds|duration}}"},
        supplied,
    ) == {"period": 60, "text": "每 1 分钟"}
    with pytest.raises(TemplateError):
        parameters(definitions, {"seconds": True})
    with pytest.raises(TemplateError):
        render("{{param.missing}}", supplied)
    assert (
        ApplyContext("lineage_test", {}, supplied, {}, "test").resident_settings == {}
    )


def test_parameter_names_values_and_role_references():
    """模板参数拒绝重名和非法枚举值，角色引用渲染为 ID 列表和首个标题"""
    from management_portal.society_service.templates.registry import TemplateError
    from management_portal.society_service.templates.values import (
        normalize_parameters,
        parameters,
        render,
    )

    with pytest.raises(TemplateError):
        normalize_parameters(
            [{"name": "x", "type": "text"}, {"name": "x", "type": "text"}]
        )
    with pytest.raises(TemplateError):
        parameters(
            [{"name": "role", "type": "enum", "options": ["critic"]}], {"role": "other"}
        )
    assert (
        render(
            "{{role.target.ids}} / {{role.target.title}}",
            {},
            roles={"target": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]},
        )
        == "a,b / A"
    )


def test_template_policy_resolves_optional_selection_lineage():
    """默认权限方案中 CreateTemplate 要求“公共内容”的 contributor，从选区创建时另外要求源 Lineage 的 viewer 权限

    :design: decision_b820faf42218, decision_b04cac4188bb
    """
    from management_portal.permission_system.requests import requests
    from management_portal.permission_system.scheme.compiler import compile_scheme
    from management_portal.permission_system.scheme.repository import builtin_documents

    scheme = compile_scheme(builtin_documents()["default"])
    # Templates are global objects; they sit on the "公共内容" node like UpdateTemplate.
    assert [(item.resource, item.slot.a) for item in requests(scheme, "CreateTemplate", {"template": {}})] == [("@public", "@contributor")]
    found = requests(
        scheme,
        "CreateTemplate",
        {"from_selection": {"lineage_id": "lineage_000000000001"}},
    )
    assert [(item.resource, item.slot.a) for item in found] == [
        ("@public", "@contributor"), ("lineage_000000000001", "@viewer")
    ]


def test_template_rejects_unknown_parameter_reference():
    """模板引用未定义的参数时规范化失败"""
    from management_portal.society_service.templates.registry import TemplateError

    with pytest.raises(TemplateError):
        kinds.normalize(
            {
                "kind": "prompt",
                "title": "Broken",
                "body": {"message": "{{param.missing}}"},
            }
        )
