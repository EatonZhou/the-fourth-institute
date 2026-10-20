""":module: spec.design-governance"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.execution import resident_runtime_settings as runtime
from management_portal.execution.resident_configuration import (
    normalize_backend_options, validate_api_settings,
)
from management_portal.society_service.resident_settings_contract import split_runtime_settings, runtime_settings_from_parts


def test_api_configuration_roundtrip_and_partial_updates():
    """模型 API Resident 配置拆分后原样还原，只改模型时保留提示与后端参数"""
    value = {"backend": "provider_api", "model": "model-a", "prompt_context": {"system_prompt": "Review"},
             "backend_options": {"parameters": {"temperature": 0.2, "thinking": {"type": "disabled"}}, "max_retries": 1}}
    execution, harness = split_runtime_settings(value)
    restored = runtime_settings_from_parts(execution, harness)
    changed = runtime.merge_settings(restored, {"model": "model-b"})
    assert changed["model"] == "model-b"
    assert changed["prompt_context"] == restored["prompt_context"]
    assert changed["backend_options"] == restored["backend_options"]
    assert "provider_id" not in changed and "execution_profile" not in changed


@pytest.mark.parametrize("backend", ["codex", "claude", "kimi", "dsh"])
def test_native_backends_share_business_prompt_and_reject_api_parameters(backend):
    """原生 Provider 共用业务提示设置，拒绝 backend_options 与预设结构化消息"""
    settings = runtime.normalize_settings({"backend": backend, "prompt_context": {"system_prompt": "Review"}})
    assert settings["prompt_context"]["system_prompt"] == "Review"
    with pytest.raises(ValueError, match="backend_options"):
        runtime.normalize_settings({"backend": backend, "backend_options": {"parameters": {}}})
    with pytest.raises(ValueError, match="preset structured messages"):
        runtime.normalize_settings({"backend": backend, "prompt_context": {"messages": [{"role": "user", "content": "old"}]}})


@pytest.mark.parametrize("options", [
    {"model": "shadow"}, {"provider_id": "shadow"}, {"parameters": {"model": "shadow"}},
    {"parameters": {"api_key": "invalid"}}, {"parameters": {"temperature": "wrong"}},
    {"parameters": {"unexpected": 1}}, {"max_retries": True}, {"timeout_seconds": float("nan")},
])
def test_unknown_and_invalid_backend_parameters_are_rejected(options):
    """模型 API 后端参数中的未知字段、密钥与类型错误的取值被拒绝"""
    with pytest.raises(ValueError):
        normalize_backend_options(options, "provider_api")


def test_api_settings_validate_the_selected_registry_route(monkeypatch):
    """模型 API 设置只接受所选路线中已启用的模型，拒绝原生 Provider 专用选项"""
    from management_portal import ai_provider_registry
    monkeypatch.setattr(ai_provider_registry, "shared_ai_provider_registry", SimpleNamespace(
        get=lambda route: {"id": route, "kind": "openai_compatible_chat", "enabled": True, "models": [{"id": "model-a", "enabled": True}, {"id": "model-b", "enabled": True}, {"id": "disabled", "enabled": False}]}))
    assert validate_api_settings("api-a", {}, {"model": "model-a"}) == {"model": "model-a"}
    assert validate_api_settings("api-a", {}, {"model": "model-b"}) == {"model": "model-b"}
    for updates in ({"model": "disabled"}, {"model": "other"}, {"allow_subagents": True}, {"reasoning_effort": "high"}):
        with pytest.raises(ValueError):
            validate_api_settings("api-a", {}, updates)


def test_jev_settings_preserve_typed_questions_and_reject_wrong_protocol(monkeypatch):
    """Jev 设置保留结构化问题，拒绝生成参数、未知题型和非 typesafe_system_one 路线

    :module: resident.identity"""
    from management_portal import ai_provider_registry
    questions = {"ready": {"type": "noul", "instructions": "Is the work ready?"}}
    options = {"parameters": {"questions": questions}, "max_retries": 0}
    settings = runtime.normalize_settings({"backend": "provider_api", "backend_options": options})
    assert settings["backend_options"]["parameters"]["questions"] == questions
    monkeypatch.setattr(ai_provider_registry, "shared_ai_provider_registry", SimpleNamespace(
        get=lambda route: {"id": route, "kind": "typesafe_system_one", "models": [{"id": "jev-latest"}]}))
    assert validate_api_settings("jev", {}, {"backend_options": options})["backend_options"]["max_retries"] == 0
    with pytest.raises(ValueError, match="chat generation"):
        validate_api_settings("jev", {"backend_options": {"parameters": {"temperature": 1}}}, {"model": "jev-latest"})
    with pytest.raises(ValueError, match="chat generation"):
        normalize_backend_options({"parameters": {"questions": questions, "temperature": 1}}, "provider_api")
    with pytest.raises(ValueError, match="choice, score, or noul"):
        normalize_backend_options({"parameters": {"questions": {"bad": {"type": "text", "instructions": "Answer"}}}}, "provider_api")
    monkeypatch.setattr(ai_provider_registry, "shared_ai_provider_registry", SimpleNamespace(
        get=lambda route: {"id": route, "kind": "openai_compatible_chat"}))
    with pytest.raises(ValueError, match="typesafe_system_one"):
        validate_api_settings("chat", {}, {"backend_options": options})


def test_api_request_uses_unified_model_and_appends_current_input(monkeypatch):
    """模型 API Resident 的请求使用所选模型与路线，在预设消息后追加本次输入"""
    from management_portal.society_service import resident_execution_domain as execution, resident_runtime_domain
    from management_portal.execution.contracts import SystemInstructions
    settings = runtime.normalize_settings({"backend": "provider_api", "model": "selected",
        "prompt_context": {"system_prompt": "Review", "messages": [{"role": "user", "content": "old input"}]},
        "backend_options": {"max_retries": 2, "parameters": {"temperature": 0.3}}})
    monkeypatch.setattr(resident_runtime_domain, '_read_resident_runtime_settings', lambda record: deepcopy(settings))
    monkeypatch.setattr(execution.resident_input_scope, 'get', lambda *a: {})
    monkeypatch.setattr(execution, '_lineage_delivery_profile', lambda job: {})
    monkeypatch.setattr(execution, '_resident_system_instructions', lambda *a: SystemInstructions())
    request = execution._build_resident_execution_request(
        {'resident_id': 'resident_aaaaaaaaaaaa', 'lineage_id': 'lineage_bbbbbbbbbbbb', 'backend': 'provider_api', 'backend_route_id': 'api-a'},
        {'actor': 'alice', 'job_id': 'job-a', 'raw_message': 'new input'})
    assert request.provider == 'api-a'
    assert request.service_type == 'chat_completion'
    assert request.operation == 'generate'
    assert request.session.mode == 'stateless'
    assert request.runtime_options['model'] == 'selected'
    assert request.runtime_options['messages'] == [{'role': 'user', 'content': 'old input'}, {'role': 'user', 'content': 'new input'}]
    assert request.runtime_options['max_retries'] == 2
    assert request.runtime_options['runtime_settings']['prompt_context']['system_prompt'] == 'Review'


def test_api_history_settings_roundtrip_and_native_rejection():
    """模型 API 对话历史设置可保存还原，非法取值与原生 Provider 上的历史设置被拒绝

    :module: resident.identity"""
    value = {'backend': 'provider_api', 'prompt_context': {'history': {'enabled': True, 'max_tokens': 8000}}}
    execution, harness = split_runtime_settings(value)
    assert runtime_settings_from_parts(execution, harness)['prompt_context']['history']['enabled'] is True
    for history in ({'enabled': 'true'}, {'enabled': True, 'max_tokens': 0}, {'max_tokens': True}):
        with pytest.raises(ValueError):
            runtime.normalize_settings({'backend': 'provider_api', 'prompt_context': {'history': history}})
    with pytest.raises(ValueError, match='API conversation history'):
        runtime.normalize_settings({**value, 'backend': 'codex'})


@pytest.mark.parametrize('protocol,requested,expected', [
    ('openai_compatible_chat', {}, True), ('typesafe_system_one', {}, False),
    ('openai_compatible_chat', {'prompt_context': {'history': {'enabled': False}}}, False),
])
def test_new_api_resident_history_default_is_registration_only(monkeypatch, protocol, requested, expected):
    """只有新注册的对话型模型 API Resident 默认开启历史，旧配置读取时不开启

    :module: resident.identity"""
    from management_portal import ai_provider_registry
    from management_portal.society_service import resident_runtime_domain as domain
    monkeypatch.setattr(ai_provider_registry, 'shared_ai_provider_registry', SimpleNamespace(get=lambda route: {
        'id': route, 'kind': protocol, 'default_model': 'm', 'models': [{'id': 'm'}]}))
    monkeypatch.setattr(domain, '_resident_runtime_defaults', lambda record: runtime.normalize_settings({'backend': 'provider_api', 'model': 'm'}))
    result = domain._resident_runtime_settings_from_register_payload(
        {'backend': 'provider_api', 'backend_route_id': 'api'}, {'runtime_settings': requested}, 'CASE-A', 'now')
    assert result['prompt_context']['history']['enabled'] is expected
    # Reading/normalizing a saved pre-feature configuration never enables history.
    assert 'history' not in runtime.normalize_settings({'backend': 'provider_api', 'prompt_context': {}})['prompt_context']


def test_protocol_specific_capability_metadata_and_effort_validation(monkeypatch):
    """模型 API 按协议和模型给出可用参数，不支持 reasoning_effort 的模型拒绝该参数

    :module: resident.identity"""
    from management_portal import ai_provider_registry
    from management_portal.execution.model_api_protocols import protocol_settings
    entry = {'id': 'api', 'kind': 'openai_compatible_chat', 'default_model': 'm',
             'models': [{'id': 'm', 'reasoning_efforts': ['low', 'high']}, {'id': 'plain'}]}
    metadata = protocol_settings(entry)
    assert metadata['view'] == 'chat' and metadata['default_history'] is True
    assert 'reasoning_effort' in metadata['parameters_by_model']['m']
    assert 'reasoning_effort' not in metadata['parameters_by_model']['plain']
    monkeypatch.setattr(ai_provider_registry, 'shared_ai_provider_registry', SimpleNamespace(get=lambda route: entry))
    settings = {'model': 'm', 'backend_options': {'parameters': {'reasoning_effort': 'high'}}}
    assert validate_api_settings('api', {}, settings)['backend_options']['parameters']['reasoning_effort'] == 'high'
    with pytest.raises(ValueError, match='reasoning_effort'):
        validate_api_settings('api', settings, {'model': 'plain'})
