"""Default-only API registrations remain usable in Resident runtime settings."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from management_portal.execution.account_adapters import ApiProfileAccounts
from management_portal.execution.accounts import ExecutionAccounts
from management_portal.execution.resident_configuration import validate_api_settings


def catalog(entry):
    registration = {"account_id": entry["id"], "max_concurrency": 1}
    adapter = ApiProfileAccounts(entry, registration)
    registry = SimpleNamespace(get=lambda _: entry, entries=lambda: [entry])
    return ExecutionAccounts((adapter,), provider_registry=registry).runtime_catalog(
        entry["id"], backend="provider_api")


def test_default_only_api_can_replace_saved_foreign_model_in_real_dialog():
    """默认模型目录经真实弹窗选择与保存，空值和显式模型都可替换旧 GPT 模型。"""
    entry = {"id": "only-deepseek", "kind": "openai_compatible_chat",
             "default_model": "deepseek-v4-flash", "models": []}
    result = catalog(entry)
    assert result["models"] == ["deepseek-v4-flash"]
    assert result["api_routes"][0]["models"] == result["models"]
    subprocess.run(["node", str(Path(__file__).with_name("javascript") / "api_runtime_defaults.cjs")],
                   cwd=Path(__file__).resolve().parents[2], input=json.dumps(result), text=True, check=True)


def test_declared_api_catalog_does_not_expose_disabled_models():
    entry = {"id": "api", "kind": "openai_compatible_chat", "default_model": "available",
             "models": [{"id": "available"}, {"id": "disabled", "enabled": False}]}
    assert catalog(entry)["models"] == ["available"]


def test_clear_model_validates_parameters_against_api_default(monkeypatch):
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    entry = {"id": "api", "kind": "openai_compatible_chat", "default_model": "deepseek-v4-flash",
             "models": [{"id": "deepseek-v4-flash", "supported_parameters": ["temperature"]},
                        {"id": "old-model", "supported_parameters": ["top_p"]}]}
    monkeypatch.setattr(shared_ai_provider_registry, "get", lambda _: entry)
    updates = {"model": "", "backend_options": {"parameters": {"temperature": 0.5}}}
    assert validate_api_settings("api", {"model": "old-model"}, updates)["model"] == ""
    with pytest.raises(ValueError, match="does not declare parameters"):
        validate_api_settings("api", {"model": "old-model"},
                              {"model": "", "backend_options": {"parameters": {"top_p": 0.5}}})
