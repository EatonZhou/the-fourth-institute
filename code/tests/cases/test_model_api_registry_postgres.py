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


def _remove_api_accounts(api_id):
    from management_portal.execution import account_registrations
    for row in account_registrations.registrations():
        if row['config'].get('api_id') == api_id:
            account_registrations.delete_registration(row['account_id'])


def test_model_api_instructions_keep_versioned_rows_in_postgres():
    """模型接口登记的增删改只写 PostgreSQL 的带版本行

    CreateModelApi/UpdateModelApi/DeleteModelApi 在 PostgreSQL 中新建、修改（版本递增、记录修改人）和删除登记；重复 ID 冲突、空 ID 无效、不存在的 ID 报 not_found；Provider 路由读到的是同一行，不涉及任何登记文件。
    """
    created = _call(execution_isa.create_model_api, **MOONSHOT)
    assert created["api"] == {**MOONSHOT, "kind": "openai_compatible_chat", "enabled": True,
                              "harness_protocols": ["openai_chat"], "version": 1}
    _call(execution_isa.create_model_api, **DEEPSEEK)
    assert _failure(execution_isa.create_model_api, **MOONSHOT) == "instruction.conflict"
    assert _failure(execution_isa.create_model_api, **{**DEEPSEEK, "api_id": "  "}) == "instruction.invalid_arguments"

    updated = _call(execution_isa.update_model_api, caller="bob", api_id="moonshot_test",
                    changes={"default_model": "kimi-k2", "enabled": False})
    assert updated["version"] == 2
    assert updated["api"] == {**MOONSHOT, "kind": "openai_compatible_chat", "default_model": "kimi-k2",
                              "enabled": False, "harness_protocols": ["openai_chat"], "version": 2}
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

    _remove_api_accounts("moonshot_test")
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


def test_model_api_models_declare_reasoning_efforts():
    """模型接口可以登记模型目录，声明每个模型接受的推理强度

    CreateModelApi 与 UpdateModelApi 接受 models：每项须是含唯一、非空且无首尾空白 id 的对象，reasoning_efforts 须是非空字符串数组，否则返回 instruction.invalid_arguments 且不写入；声明了推理强度的模型在直连协议设置中提供 reasoning_effort 参数，未声明的模型不提供。
    """
    from management_portal.execution.model_api_protocols import protocol_settings
    from management_portal.hatg.catalog import declaration

    models = [{"id": "kimi", "reasoning_efforts": ["low", "high"]}, {"id": "kimi-mini"}]
    declaration("CreateModelApi").validate_arguments({**MOONSHOT, "models": models})
    created = _call(execution_isa.create_model_api, **MOONSHOT, models=models)
    assert created["api"]["models"] == models
    settings = protocol_settings(AIProviderRegistry().get("moonshot_test"))
    assert settings["reasoning_efforts_by_model"] == {"kimi": ["low", "high"], "kimi-mini": []}
    assert "reasoning_effort" in settings["parameters_by_model"]["kimi"]
    assert "reasoning_effort" not in settings["parameters_by_model"]["kimi-mini"]

    for rejected in ("kimi", ["kimi"], [{"reasoning_efforts": ["low"]}], [{"id": " kimi"}],
                     [{"id": "kimi"}, {"id": "kimi"}], [{"id": "kimi", "reasoning_efforts": "high"}],
                     [{"id": "kimi", "reasoning_efforts": [""]}]):
        assert _failure(execution_isa.update_model_api, api_id="moonshot_test",
                        changes={"models": rejected}) == "instruction.invalid_arguments"
        assert _failure(execution_isa.create_model_api, **{**DEEPSEEK, "models": rejected}) == \
            "instruction.invalid_arguments"
    assert [api["api_id"] for api in _call(execution_isa.list_model_apis)["apis"]] == ["moonshot_test"]
    assert AIProviderRegistry().get("moonshot_test")["models"] == models

    updated = _call(execution_isa.update_model_api, api_id="moonshot_test", changes={"models": []})
    assert updated["version"] == 2 and updated["api"]["models"] == []


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
                    changes={"provider": "moonshot"}) == "instruction.conflict"
    assert AIProviderRegistry().get("deepseek_test")["provider"] == "deepseek"
    _call(execution_isa.create_model_api, **{**DEEPSEEK, "api_id": "deepseek_second"}, provider="deepseek")
    assert _failure(execution_isa.update_model_api, api_id="deepseek_second",
                    changes={"routes": [route]}) == "instruction.invalid_arguments"
    _call(execution_isa.update_model_api, api_id="deepseek_test", changes={"routes": []})
    _call(execution_isa.update_model_api, api_id="deepseek_second", changes={"routes": [route]})
    assert model_api_store.route_api_id("dsh", "deepseek") == "deepseek_second"
    _remove_api_accounts("deepseek_second")
    _call(execution_isa.delete_model_api, api_id="deepseek_second")
    assert model_api_store.route_api_id("dsh", "deepseek") is None


def test_model_apis_declare_the_protocols_agent_harnesses_may_use():
    """模型接口声明 Agent Harness 可用的协议；未声明的 DeepSeek 登记保持原有 Anthropic 地址

    Chat Completions 跟随 kind；openai_responses（Codex）与 anthropic_messages（Claude Code）需要显式声明，后者可指定地址与认证方式，缺省为去掉 /v1 的基础地址和 Bearer。未声明 protocols 的 DeepSeek 登记仍按原规则提供 /anthropic；一旦声明，就只以声明为准。未知协议、chat 声明、错误选项、非 http(s) 地址、未知认证方式都返回 invalid_arguments；非 Chat 接口不能声明协议。
    """
    gateway = {"api_id": "gateway_test", "base_url": "https://gateway.invalid/v1", "default_model": "gpt-test",
               "secret_file": "/secrets/gateway", "provider": "example"}
    created = _call(execution_isa.create_model_api, **gateway, protocols={
        "openai_responses": {}, "anthropic_messages": {"auth": "x-api-key"}})
    assert created["api"]["harness_protocols"] == ["openai_chat", "openai_responses", "anthropic_messages"]
    entry = AIProviderRegistry().get("gateway_test")
    assert AIProviderRegistry.harness_endpoint(entry, "openai_responses") == ("https://gateway.invalid/v1", "bearer")
    assert AIProviderRegistry.harness_endpoint(entry, "anthropic_messages") == ("https://gateway.invalid", "x-api-key")
    assert AIProviderRegistry.harness_models(entry) == ["gpt-test"]
    from management_portal.execution import account_registrations
    account_registrations.delete_registration("gateway_test-codex")
    _call(execution_isa.update_model_api, api_id="gateway_test", changes={
        "protocols": {"anthropic_messages": {"base_url": "https://claude.gateway.invalid/"}}})
    entry = AIProviderRegistry().get("gateway_test")
    assert sorted(AIProviderRegistry.harness_protocols(entry)) == ["anthropic_messages", "openai_chat"]
    assert AIProviderRegistry.harness_endpoint(entry, "anthropic_messages") == ("https://claude.gateway.invalid", "bearer")
    with pytest.raises(ValueError):
        AIProviderRegistry.harness_endpoint(entry, "openai_responses")

    legacy = _call(execution_isa.create_model_api, **DEEPSEEK, provider="deepseek")
    assert legacy["api"]["harness_protocols"] == ["openai_chat", "anthropic_messages"]
    assert AIProviderRegistry.harness_endpoint(AIProviderRegistry().get("deepseek_test"), "anthropic_messages") == (
        "https://deepseek.invalid/anthropic", "bearer")
    _remove_api_accounts("deepseek_test")
    declared = _call(execution_isa.update_model_api, api_id="deepseek_test", changes={"protocols": {}})
    assert declared["api"]["harness_protocols"] == ["openai_chat"]

    for rejected in ("openai_responses", {"openai_chat": {}}, {"websocket": {}}, {"openai_responses": {"base_url": "x"}},
                     {"anthropic_messages": {"base_url": "ftp://gateway.invalid"}},
                     {"anthropic_messages": {"auth": "cookie"}}, {"anthropic_messages": []}):
        assert _failure(execution_isa.update_model_api, api_id="gateway_test",
                        changes={"protocols": rejected}) == "instruction.invalid_arguments"
    assert _failure(execution_isa.update_model_api, api_id="gateway_test",
                    changes={"kind": "typesafe_system_one"}) == "instruction.invalid_arguments"
    assert _failure(execution_isa.create_model_api, **{**gateway, "api_id": "non_chat_gateway"},
                    kind="typesafe_system_one", protocols={"openai_responses": {}}) == "instruction.invalid_arguments"
    assert model_api_store.api_version("non_chat_gateway") is None
    from management_portal.hatg.catalog import declaration
    declaration("CreateModelApi").validate_arguments({**gateway, "protocols": {"openai_responses": {}}})


def test_a_model_api_in_use_by_an_execution_account_is_not_deleted():
    """仍被执行账号引用的模型接口不能删除

    DeleteModelApi 在有执行账号的 config.api_id 指向该接口时返回 instruction.conflict（reason account.api_in_use，并列出账号），登记保持不变；账号删除后接口可以删除。
    """
    from management_portal.execution import account_registrations
    from tests.cases.model_api_test_support import register_accounts

    _call(execution_isa.create_model_api, **MOONSHOT)
    register_accounts({"account_id": "moonshot_direct", "provider": "provider_api", "kind": "api_profile",
                       "config": {"api_id": "moonshot_test"}, "max_concurrency": 1})
    with pytest.raises(InstructionError) as error:
        _call(execution_isa.delete_model_api, api_id="moonshot_test")
    assert error.value.code == "instruction.conflict"
    assert error.value.details == {"reason": "account.api_in_use", "account_ids": ["moonshot_test", "moonshot_test-kimi", "moonshot_test-opencode", "moonshot_direct"]}
    assert model_api_store.api_version("moonshot_test") == 1
    account_registrations.delete_registration("moonshot_direct")
    _remove_api_accounts("moonshot_test")
    assert _call(execution_isa.delete_model_api, api_id="moonshot_test") == {"api_id": "moonshot_test"}


def test_an_older_model_api_table_gains_the_protocols_column_once():
    """已有实例的模型接口表在首次使用时补上 protocols 列，之后的检查不锁表

    旧实例的 apis 表没有 protocols 列，而读取会解码全部字段；DDL 先查 pg_attribute，缺列时才 ALTER TABLE，列已存在时不对 apis 加任何锁。
    """
    database = model_api_store.database()
    database.ensure()
    table = database.database.schema + ".apis"

    def columns(connection):
        return {row["attname"] for row in connection.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid=to_regclass(%s) AND NOT attisdropped", (table,))}

    with database.database.transaction() as connection:
        connection.execute("ALTER TABLE apis DROP COLUMN protocols")
    with database.database.transaction() as connection:
        assert "protocols" not in columns(connection)
        connection.executescript(model_api_store.DDL)
        assert "protocols" in columns(connection)
    with database.database.transaction() as connection:
        connection.executescript(model_api_store.DDL)
        assert [row["mode"] for row in connection.execute(
            "SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND relation=to_regclass(%s)", (table,))] == []
