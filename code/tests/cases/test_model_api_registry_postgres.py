"""Model API registrations and relay resources live in PostgreSQL only.

The model API instructions write versioned rows; every reader (Provider
routes, the AI relay) reads the same rows. No registry file is involved.

:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from management_common.portal_database import portal_database
from management_portal import ai_socket_relay as relay
from management_portal import model_api_store
from management_portal.ai_provider_registry import AIProviderRegistry
from management_portal.execution import isa as execution_isa
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import InstructionKernel
from management_portal.hatg.store import ExecutionStore
from tests.cases.model_api_test_support import register_relay_resources


MOONSHOT = {"api_id": "moonshot_test", "base_url": "https://moonshot.invalid/v1", "default_model": "kimi",
            "secret_file": "/secrets/moonshot", "provider": "moonshot"}
DEEPSEEK = {"api_id": "deepseek_test", "base_url": "https://deepseek.invalid", "default_model": "chat",
            "secret_file": "/secrets/deepseek"}


def _call(handler, caller="alice", **arguments):
    invocation = SimpleNamespace(caller=caller, argument=lambda name, default=None: arguments.get(name, default))
    return handler(invocation)


def _failure(handler, **arguments) -> str:
    with pytest.raises(InstructionError) as error:
        _call(handler, **arguments)
    return error.value.code


def test_model_api_instructions_keep_versioned_rows_in_postgres():
    """模型接口登记的增删改只写 PostgreSQL 的带版本行

    CreateModelApi/UpdateModelApi/DeleteModelApi 在 PostgreSQL 中新建、修改（版本递增、记录修改人）和删除登记；重复 ID 冲突、空 ID 无效、不存在的 ID 报 not_found；Provider 路由读到的是同一行，不涉及任何登记文件。
    """
    created = _call(execution_isa.create_model_api, **MOONSHOT)
    assert created["api"] == {**MOONSHOT, "kind": "openai_compatible_chat", "enabled": True, "version": 1}
    _call(execution_isa.create_model_api, **DEEPSEEK)
    assert _failure(execution_isa.create_model_api, **MOONSHOT) == "instruction.conflict"
    assert _failure(execution_isa.create_model_api, **{**DEEPSEEK, "api_id": "  "}) == "instruction.invalid_arguments"

    updated = _call(execution_isa.update_model_api, caller="bob", api_id="moonshot_test",
                    changes={"default_model": "kimi-k2", "enabled": False})
    assert updated["version"] == 2
    assert updated["api"] == {**MOONSHOT, "kind": "openai_compatible_chat", "default_model": "kimi-k2",
                              "enabled": False, "version": 2}
    assert [(api["api_id"], api["version"]) for api in _call(execution_isa.list_model_apis)["apis"]] == [
        ("moonshot_test", 2), ("deepseek_test", 1)]
    from management_portal.hatg.catalog import declaration
    on_api = SimpleNamespace(declaration=declaration("UpdateModelApi"))
    assert execution_isa.version_of(on_api, "moonshot_test") == 2
    assert execution_isa.version_of(on_api, "absent") is None
    # Provider routes read the same row; version and authorship stay columns.
    assert AIProviderRegistry().entries()[0] == {
        "id": "moonshot_test", "kind": "openai_compatible_chat", "enabled": False,
        "base_url": "https://moonshot.invalid/v1", "default_model": "kimi-k2",
        "api_key_file": "/secrets/moonshot", "provider": "moonshot",
    }
    with model_api_store.database().read() as connection:
        assert connection.execute("SELECT updated_by FROM apis WHERE api_id='moonshot_test'").fetchone()[0] == "bob"

    assert _call(execution_isa.delete_model_api, api_id="moonshot_test") == {"api_id": "moonshot_test"}
    assert _failure(execution_isa.update_model_api, api_id="moonshot_test", changes={}) == "instruction.not_found"
    assert _failure(execution_isa.delete_model_api, api_id="moonshot_test") == "instruction.not_found"
    assert [api["api_id"] for api in _call(execution_isa.list_model_apis)["apis"]] == ["deepseek_test"]


def test_model_api_update_checks_the_stored_version():
    """修改模型接口时按数据库中的版本做并发检查

    经指令内核调用 UpdateModelApi 并带 expected_version：版本相符时提交并递增版本；api_id 是指令目标，单个版本号和以 api_id 为键的版本号用旧版本修改时都返回 instruction.version_conflict，登记内容保持第一次修改的结果；DeleteModelApi 同样按版本拒绝过期删除。
    """
    kernel = InstructionKernel(
        execution_isa.binding(),
        store=ExecutionStore(portal_database(schema=f"isa_model_api_{uuid.uuid4().hex[:8]}")),
        authorize=lambda *args: None,
    )
    context = InstructionContext(caller="alice", entry="web")
    _call(execution_isa.create_model_api, **DEEPSEEK)

    def update(model: str, expected):
        call = InstructionCall("UpdateModelApi", {"api_id": "deepseek_test", "changes": {"default_model": model}},
                               uuid.uuid4().hex, expected_version=expected)
        return kernel.execute(call, context)

    first = update("reasoner", 1)
    assert first.status == "committed", first.error
    # The API ID is the instruction's target: a bare token and a token keyed
    # by the API ID are both compared with the stored version.
    for stale_token in (1, {"deepseek_test": 1}):
        stale = update("chat", stale_token)
        assert stale.status == "failed" and stale.error["code"] == "instruction.version_conflict"
    assert AIProviderRegistry().get("deepseek_test")["default_model"] == "reasoner"
    assert update("chat", {"deepseek_test": 2}).status == "committed"
    delete = kernel.execute(InstructionCall("DeleteModelApi", {"api_id": "deepseek_test"}, uuid.uuid4().hex,
                                            expected_version=2), context)
    assert delete.status == "failed" and delete.error["code"] == "instruction.version_conflict"
    assert AIProviderRegistry().get("deepseek_test")["default_model"] == "chat"


def test_relay_admits_the_resources_stored_in_postgres():
    """AI 中转只放行 PostgreSQL 中登记且启用的资源

    AI Socket 中转从 PostgreSQL 的 relay_resources 表读取可用资源：没有登记时一个也不放行；资源 ID 与 provider 不区分大小写；停用的资源被拒绝。

    :module: execution.service
    """
    assert relay._read_resource_registry() == {}
    register_relay_resources(
        {"id": "Gemini", "provider": "OpenRouter", "model": "google/gemini"},
        {"id": "kimi", "provider": "moonshot", "model": "kimi-k2", "enabled": False},
    )
    assert relay._resolve_resource("gemini")["model"] == "google/gemini"
    assert relay._resolve_resource("gemini")["provider"] == "openrouter"
    with pytest.raises(PermissionError):
        relay._resolve_resource("kimi")


def test_explicit_routes_are_validated_and_follow_the_api_lifecycle():
    """模型 API 的路由绑定须与 Provider 匹配且不重复，随 API 更新或删除转移、释放

    :module: platform.core
    :importance: P2
    """
    route = {"backend": "dsh", "route_id": "deepseek"}
    first = _call(execution_isa.create_model_api, **DEEPSEEK, provider="deepseek", routes=[route])
    assert first["api"]["routes"] == [route]
    assert model_api_store.route_api_id("dsh", "deepseek") == "deepseek_test"
    # A rejected binding rolls back both the API write and the relationship.
    assert _failure(execution_isa.create_model_api, **MOONSHOT, routes=[route]) == "instruction.invalid_arguments"
    assert model_api_store.api_version("moonshot_test") is None
    assert _failure(execution_isa.update_model_api, api_id="deepseek_test",
                    changes={"provider": "moonshot"}) == "instruction.invalid_arguments"
    assert AIProviderRegistry().get("deepseek_test")["provider"] == "deepseek"
    _call(execution_isa.create_model_api, **{**DEEPSEEK, "api_id": "deepseek_second"}, provider="deepseek")
    assert _failure(execution_isa.update_model_api, api_id="deepseek_second",
                    changes={"routes": [route]}) == "instruction.invalid_arguments"
    _call(execution_isa.update_model_api, api_id="deepseek_test", changes={"routes": []})
    _call(execution_isa.update_model_api, api_id="deepseek_second", changes={"routes": [route]})
    assert model_api_store.route_api_id("dsh", "deepseek") == "deepseek_second"
    _call(execution_isa.delete_model_api, api_id="deepseek_second")
    assert model_api_store.route_api_id("dsh", "deepseek") is None
