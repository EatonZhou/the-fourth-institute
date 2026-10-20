"""
:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import pytest

from management_portal import model_api_store
from management_portal.ai_provider_registry import AIProviderRegistry


def test_provider_registry_reads_registrations_from_postgres_in_order():
    """Provider 登记表按登记顺序从 PostgreSQL 读取

    AIProviderRegistry 读出 PostgreSQL 中的模型接口登记，顺序是登记顺序而不是 ID 顺序（路由选择取第一个匹配项）；空库时为空列表；同 ID 再次登记被拒绝且不改变顺序。
    """
    registry = AIProviderRegistry()
    assert registry.entries() == []

    for entry in (
        {"id": "second_registered", "kind": "openai_compatible_chat", "api_key_file": "/secrets/second"},
        {"id": "first_registered", "enabled": False, "models": [{"id": "m", "enabled": True}]},
    ):
        assert model_api_store.create_api(entry, "alice")

    # Registration order is presentation only; service routes bind an API ID.
    assert registry.entries() == [
        {"id": "second_registered", "kind": "openai_compatible_chat", "api_key_file": "/secrets/second"},
        {"id": "first_registered", "enabled": False, "models": [{"id": "m", "enabled": True}]},
    ]
    assert not model_api_store.create_api({"id": "first_registered"}, "bob")
    assert [entry["id"] for entry in registry.entries()] == ["second_registered", "first_registered"]


def test_provider_lookup_requires_an_enabled_registration():
    """按 ID 查找 Provider 登记只返回启用的条目

    AIProviderRegistry.get 返回 PostgreSQL 中启用的登记；停用或不存在的 ID 报“not available”，不回退到其他登记。
    """
    registry = AIProviderRegistry()
    model_api_store.create_api({"id": "healthy", "default_model": "fixed"}, "alice")
    model_api_store.create_api({"id": "disabled", "enabled": False}, "alice")

    assert registry.get("healthy") == {"id": "healthy", "default_model": "fixed"}
    for unavailable in ("disabled", "missing"):
        with pytest.raises(ValueError, match="not available"):
            registry.get(unavailable)


def test_model_ids_and_default_enablement_use_one_registry_rule():
    """模型接口未写 enabled 时默认启用，模型列表只含启用的模型

    :module: platform.core
    :importance: P2
    """
    entry = {"models": [{"id": "first"}, {"id": "second", "enabled": True}, {"id": "disabled", "enabled": False}]}
    assert AIProviderRegistry.is_enabled(entry)
    assert not AIProviderRegistry.is_enabled({"enabled": False})
    assert AIProviderRegistry.models(entry) == ["first", "second"]


@pytest.mark.parametrize("url", ["https://api.deepseek.com", "https://api.deepseek.com/v1/", "https://api.deepseek.com/anthropic"])
def test_messages_base_url_normalizes_the_openai_suffix(url):
    """anthropic 协议的 base_url 去掉 OpenAI 的 /v1 后缀并统一指向 /anthropic

    :module: platform.core
    :importance: P2
    """
    assert AIProviderRegistry.base_url({"base_url": url}, protocol="anthropic") == "https://api.deepseek.com/anthropic"
