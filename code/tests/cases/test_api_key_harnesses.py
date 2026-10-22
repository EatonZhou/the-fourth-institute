"""API-key execution accounts on agent harnesses: Codex, Claude Code and OpenCode.

A Model API declares the wire protocols it speaks; an ``api_profile`` account
of a harness runs on any registration that speaks the harness's protocol.

:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import claude_provider_catalog, opencode_provider_catalog
from management_portal.execution import account_hosting, account_login, codex_api_accounts, isa
from management_portal.execution.account_adapters import CodexAccounts
from management_portal.execution.account_hosting import account_home
from management_portal.execution.account_monitor import _subscription
from management_portal.execution.harness_capabilities import NATIVE_IMAGE_INPUT
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.hatg.contracts import InstructionError
from tests.cases.claude_sdk_support import load_claude_module

KEY = "sk-gateway-test"


def _call(handler, caller="alice", **arguments):
    invocation = SimpleNamespace(caller=caller, argument=lambda name, default=None: arguments.get(name, default),
                                 expected_version_for=lambda object_id: None)
    return handler(invocation)


def _failure(handler, **arguments) -> InstructionError:
    with pytest.raises(InstructionError) as error:
        _call(handler, **arguments)
    return error.value


def _register(account_id: str, provider: str, api_id: str = "gateway", **config):
    return _call(isa.register_provider_account, account_id=account_id, provider=provider, kind="api_profile",
                 config={"api_id": api_id, **config}, max_concurrency=2)["registration"]


@pytest.fixture
def gateway(tmp_path):
    """A gateway that speaks Chat Completions, Responses and Anthropic Messages (x-api-key)."""
    key = tmp_path / "gateway.key"
    key.write_text(KEY + "\n")
    key.chmod(0o600)
    entry = {"api_id": "gateway", "base_url": "https://gateway.invalid/v1", "default_model": "gpt-test",
             "secret_file": str(key), "provider": "example", "headers": {"X-Team": "lab"},
             "models": [{"id": "gpt-test", "reasoning_efforts": ["low", "high", "max", "ultra"], "context_window": 200000},
                        {"id": "gpt-mini"}],
             "protocols": {"openai_responses": {}, "anthropic_messages": {"auth": "x-api-key"}}}
    _call(isa.create_model_api, **entry)
    return SimpleNamespace(entry=entry, key_file=key)


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    """A Codex CLI whose API-key login records its call; hosting calls are recorded instead of run."""
    log = tmp_path / "codex-calls.jsonl"
    script = tmp_path / "bin" / "codex"
    script.parent.mkdir()
    script.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
key = sys.stdin.read().strip()
home = Path(os.environ["CODEX_HOME"])
(home / "auth.json").write_text(json.dumps({{"auth_mode": "apikey", "OPENAI_API_KEY": key}}))
with open({str(log)!r}, "a") as stream:
    stream.write(json.dumps({{"argv": sys.argv[1:], "home": str(home),
                             "proxies": sorted(k for k in os.environ if k.lower().endswith("_proxy"))}}) + "\\n")
""")
    script.chmod(0o755)
    monkeypatch.setattr(account_login, "provider_binary", lambda provider: str(script))
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    hosting = []
    for name in ("start_codex_instance", "stop_codex_instance", "remove_codex_instance"):
        monkeypatch.setattr(account_hosting, name, lambda account_id, name=name: hosting.append((name, account_id)) or {})
    monkeypatch.setattr("management_portal.execution.provider_updates.runtime_available", lambda provider: True)
    monkeypatch.setattr(isa, "account_busy_counts", lambda row: {"session_count": 0, "execution_count": 0})

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return SimpleNamespace(calls=calls, hosting=hosting)


def test_a_codex_api_account_keeps_its_key_in_its_home_and_points_codex_at_the_model_api(gateway, fake_codex):
    """Codex API 账号的密钥只在账号目录里，整个 app-server 进程只访问模型 API 的 Responses 地址

    登记 Codex 的 api_profile 账号时，Portal 用 codex login --with-api-key（密钥走 stdin、不带代理）把密钥写入账号目录的 auth.json，并在 config.toml 中把模型服务指向该模型 API、关闭联网搜索、应用连接器、插件和遥测，然后启动实例；Codex 自己写入的项目信任设置保留。登记和配置不变时不再登录或改写；地址或密钥改变时重新写入并重启实例。
    """
    registration = _register("gateway-codex", "codex")
    home = account_home("codex", "gateway-codex")
    assert registration["home_path"] == str(home)
    assert json.loads((home / "auth.json").read_text()) == {"auth_mode": "apikey", "OPENAI_API_KEY": KEY}
    (login,) = fake_codex.calls()
    assert login == {"argv": ["login", "--with-api-key"], "home": str(home), "proxies": []}
    config_path = home / "config.toml"
    assert config_path.stat().st_mode & 0o777 == 0o600
    config = tomllib.loads(config_path.read_text())
    assert config["model_provider"] == "portal_api" and config["model"] == "gpt-test"
    assert config["model_providers"]["portal_api"] == {
        "name": "gateway", "base_url": "https://gateway.invalid/v1", "wire_api": "responses",
        "requires_openai_auth": True, "http_headers": {"X-Team": "lab"}}
    assert (config["forced_login_method"], config["web_search"]) == ("api", "disabled")
    assert config["features"] == {"apps": False, "plugins": False, "memories": False}
    assert config["analytics"] == {"enabled": False}
    assert set(config["otel"].values()) == {"none"}
    assert KEY not in config_path.read_text()
    assert fake_codex.hosting == [("start_codex_instance", "gateway-codex")]

    row = codex_api_accounts.api_accounts("gateway")[0]
    assert codex_api_accounts.prepare(row) is False and len(fake_codex.calls()) == 1

    # Codex records trusted projects in the same file.
    config_path.write_text(config_path.read_text() + '\n[projects."/work/project"]\ntrust_level = "trusted"\n')
    _call(isa.update_model_api, api_id="gateway", changes={"base_url": "https://gateway-2.invalid/v1"})
    config = tomllib.loads(config_path.read_text())
    assert config["model_providers"]["portal_api"]["base_url"] == "https://gateway-2.invalid/v1"
    assert config["projects"] == {"/work/project": {"trust_level": "trusted"}}
    assert fake_codex.hosting[-2:] == [("stop_codex_instance", "gateway-codex"), ("start_codex_instance", "gateway-codex")]
    assert len(fake_codex.calls()) == 1

    gateway.key_file.write_text("sk-rotated\n")
    assert codex_api_accounts.refresh(codex_api_accounts.api_accounts("gateway")) == ["gateway-codex"]
    assert json.loads((home / "auth.json").read_text())["OPENAI_API_KEY"] == "sk-rotated"
    assert len(fake_codex.calls()) == 2


def test_a_codex_api_account_is_billed_by_its_model_api_and_lists_its_declared_models(gateway, fake_codex):
    """Codex API 账号按模型 API 余额计费，模型目录来自模型 API 的声明，不读取 ChatGPT 额度

    API 账号的额度策略为 none、计费账号是模型 API；模型、推理强度（只保留 Codex 支持的取值）来自登记；额度观测不读 rateLimits，用量统计不轮询；路线的上游服务是没有探测的 model_api，不接受原生图片输入；订阅账号保持原样。
    """
    _register("gateway-codex", "codex")
    adapter = CodexAccounts()
    accounts = {account.account_id: account for account in adapter.accounts()}
    api, subscription = accounts["gateway-codex"], accounts["codex_one"]
    assert (api.quota_policy, api.billing_account_id, api.kind) == ("none", "gateway", "api_profile")
    assert (subscription.quota_policy, subscription.kind) == ("reserve", "subscription_login")
    assert not _subscription(api) and _subscription(subscription)
    catalog = adapter.catalog(api)
    assert catalog["models"] == ["gpt-test", "gpt-mini"] and catalog["default_model"] == "gpt-test"
    assert catalog["efforts_by_model"] == {"gpt-test": ["low", "high", "max"], "gpt-mini": []}
    assert catalog["default_reasoning_effort"] == "high"
    assert adapter.observe(api) == {"status": "ok", "rate_limits": []}
    codex = AGENT_PROVIDERS["codex"]
    assert (codex.upstream_for("gateway-codex"), codex.upstream_for("one")) == ("model_api", "openai")
    assert NATIVE_IMAGE_INPUT not in codex.harness_capabilities("gateway-codex")
    assert NATIVE_IMAGE_INPUT in codex.harness_capabilities("one")


def test_api_key_accounts_need_a_model_api_that_speaks_their_harness_protocol(gateway, fake_codex):
    """API 账号的模型 API 必须提供其 Harness 使用的协议，使用中的协议不能撤下

    Codex 需要 openai_responses，Claude Code 需要 anthropic_messages，OpenCode 用 Chat Completions；不满足时登记返回 invalid_arguments。账号仍在使用时，撤下它需要的协议返回 conflict（account.api_in_use）。Codex API 账号不能设置备用账号，订阅账号也不能把 API 账号设为备用。
    """
    _call(isa.create_model_api, api_id="chat_only", base_url="https://chat.invalid/v1", default_model="m",
          secret_file=str(gateway.key_file))
    for provider in ("codex", "claude"):
        assert _failure(isa.register_provider_account, account_id=f"chat-{provider}", provider=provider,
                        kind="api_profile", config={"api_id": "chat_only"}).code == "instruction.invalid_arguments"
    assert _register("chat-opencode", "opencode", "chat_only")["provider"] == "opencode"
    _register("gateway-codex", "codex")
    error = _failure(isa.update_model_api, api_id="gateway", changes={"protocols": {"anthropic_messages": {}}})
    assert error.details == {"reason": "account.api_in_use", "account_ids": ["gateway-codex"]}
    assert _failure(isa.register_provider_account, account_id="spare-codex", provider="codex", kind="api_profile",
                    config={"api_id": "gateway", "fallback_account_ids": ["codex_one"]}).code == "instruction.invalid_arguments"
    assert _failure(isa.update_provider_account_registration, account_id="codex_one", changes={
        "config": {"is_default": True, "fallback_account_ids": ["gateway-codex"]}}).code == "instruction.invalid_arguments"


def test_a_codex_api_account_holds_back_restarts_only_while_its_work_runs(gateway, fake_codex, monkeypatch):
    """Codex API 账号有排队或运行中的执行时，会重启它的模型 API 修改被拒绝；只有会话时照常保存

    修改地址、密钥等会改写配置并重启 app-server：账号有执行时返回 conflict（account.running）且不修改登记；只有已绑定的会话时照常保存并重启，会话在重启后继续。编辑表单连同未改动的字段一起提交、只改模型目录时不重启，执行中也能保存。删除空闲的 Codex API 账号会停用实例并删除账号目录（auth.json 存有密钥）。
    """
    _register("gateway-codex", "codex")
    monkeypatch.setattr(isa, "account_busy_counts", lambda row: {"session_count": 1, "execution_count": 1})
    error = _failure(isa.update_model_api, api_id="gateway", changes={"base_url": "https://moved.invalid/v1"})
    assert (error.code, error.details["reason"]) == ("instruction.conflict", "account.running")
    assert _call(isa.list_model_apis)["apis"][0]["base_url"] == "https://gateway.invalid/v1"
    # The edit form sends every field along with the changed model list.
    form = {"base_url": "https://gateway.invalid/v1", "default_model": "gpt-test", "provider": "example",
            "headers": {"X-Team": "lab"}, "secret_file": str(gateway.key_file), "enabled": True,
            "models": [{"id": "gpt-test", "reasoning_efforts": ["high"]}]}
    restarts = len(fake_codex.hosting)
    _call(isa.update_model_api, api_id="gateway", changes=form)
    assert len(fake_codex.hosting) == restarts
    monkeypatch.setattr(isa, "account_busy_counts", lambda row: {"session_count": 1, "execution_count": 0})
    _call(isa.update_model_api, api_id="gateway", changes={**form, "base_url": "https://moved.invalid/v1"})
    assert fake_codex.hosting[-2:] == [("stop_codex_instance", "gateway-codex"), ("start_codex_instance", "gateway-codex")]
    monkeypatch.setattr(isa, "account_busy_counts", lambda row: {"session_count": 0, "execution_count": 0})
    home = account_home("codex", "gateway-codex")
    assert home.is_dir()
    _call(isa.delete_provider_account, account_id="gateway-codex")
    assert not home.exists() and ("remove_codex_instance", "gateway-codex") in fake_codex.hosting


def test_portal_writes_codex_configuration_that_reads_back_unchanged():
    """Portal 改写 Codex 的 config.toml 时，原有内容按 TOML 原样保留"""
    document = {"model": "gpt-test", "approval": None, "n": 3, "ratio": 0.5, "flag": False,
                "when": datetime.datetime(2026, 10, 21, 8, 0, tzinfo=datetime.timezone.utc),
                "list": ["a", 'quote "b"', "汉字", "\x7f"], "mcp": [{"name": "x", "args": ["--a"]}],
                "projects": {"/work/a b": {"trust_level": "trusted"}, "empty": {}},
                "model_providers": {"portal_api": {"base_url": "https://x.invalid", "http_headers": {"X-A": "1"}}}}
    document.pop("approval")
    assert tomllib.loads(codex_api_accounts.render_toml(document)) == document
    merged = codex_api_accounts.merged_config({"features": {"other": True}, "projects": {"/p": {"trust_level": "trusted"}}},
                                              {"features": {"apps": False}, "model": "m"})
    assert merged == {"features": {"other": True, "apps": False}, "projects": {"/p": {"trust_level": "trusted"}}, "model": "m"}


daemon_module = load_claude_module("claude_code_daemon")


def test_claude_code_runs_on_any_declared_anthropic_gateway(gateway, tmp_path):
    """Claude Code 的 API 账号可以使用任何声明了 Anthropic Messages 的网关

    声明了协议的网关路线：模型来自模型 API（推理强度取 reasoning_efforts 中 Claude 支持的值），上游服务为 model_api；会话按声明的认证方式（x-api-key）带密钥，附加请求头，后台、标题和子代理请求都使用所选模型，而不是 Claude 的模型名。
    """
    _register("gateway-claude", "claude")
    catalog = claude_provider_catalog.catalog("gateway-claude")
    assert catalog["models"] == ["gpt-test", "gpt-mini"] and catalog["upstream_provider"] == "model_api"
    assert catalog["efforts_by_model"] == {"gpt-test": ["low", "high", "max"], "gpt-mini": []}
    assert AGENT_PROVIDERS["claude"].upstream_for("gateway-claude") == "model_api"
    env, _profile = daemon_module._provider_environment("gateway-claude")
    assert env["ANTHROPIC_BASE_URL"] == "https://gateway.invalid"
    assert (env["ANTHROPIC_API_KEY"], env["ANTHROPIC_AUTH_TOKEN"]) == (KEY, "")
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == "X-Team: lab"

    fake = mock.Mock(connect=mock.AsyncMock())
    params = {"session_key": "resident:one", "cwd": str(tmp_path), "config_dir": str(tmp_path / "claude"),
              "provider_profile": "gateway-claude", "model": "gpt-mini", "is_owner": True}
    with mock.patch.object(daemon_module, "ClaudeSDKClient", return_value=fake) as factory:
        asyncio.run(daemon_module.ClaudeCodeDaemon()._create_handle(params))
    options = factory.call_args.args[0]
    assert options.model == "gpt-mini"
    assert {name: options.env[name] for name in daemon_module.GATEWAY_MODEL_VARIABLES} == dict.fromkeys(
        daemon_module.GATEWAY_MODEL_VARIABLES, "gpt-mini")


def test_a_deepseek_claude_route_without_declared_protocols_runs_as_before(tmp_path):
    """未声明协议的 DeepSeek 登记仍走原有的 /anthropic 地址和 Bearer 认证，不改模型变量"""
    from tests.cases.model_api_test_support import DEEPSEEK_PRIMARY, register_apis
    key = tmp_path / "deepseek.key"
    key.write_text("sk-deepseek\n")
    register_apis({**DEEPSEEK_PRIMARY, "api_key_file": str(key)})
    env, profile = daemon_module._provider_environment("deepseek_primary")
    assert env["ANTHROPIC_BASE_URL"] == "https://deepseek.invalid/anthropic"
    assert (env["ANTHROPIC_AUTH_TOKEN"], env["ANTHROPIC_API_KEY"]) == ("sk-deepseek", "")
    assert "ANTHROPIC_CUSTOM_HEADERS" not in env
    catalog = claude_provider_catalog.model_catalog(profile)
    assert catalog["upstream_provider"] == "deepseek"
    assert catalog["efforts_by_model"]["deepseek-v4-pro"] == ["low", "high", "max"]
    fake = mock.Mock(connect=mock.AsyncMock())
    params = {"session_key": "resident:one", "cwd": str(tmp_path), "config_dir": str(tmp_path / "claude"),
              "provider_profile": "deepseek_primary", "model": "deepseek-v4-pro", "is_owner": True}
    with mock.patch.object(daemon_module, "ClaudeSDKClient", return_value=fake) as factory:
        asyncio.run(daemon_module.ClaudeCodeDaemon()._create_handle(params))
    assert "ANTHROPIC_DEFAULT_HAIKU_MODEL" not in factory.call_args.args[0].env


def test_an_opencode_api_account_becomes_a_provider_of_its_own(gateway):
    """OpenCode 的 API 账号成为一个独立的 OpenAI 兼容 Provider，模型与上下文窗口来自模型 API

    非内置服务的 api_profile 路线以 portal-<路线> 为 Provider 名，使用模型 API 的 Chat Completions 地址和附加请求头，模型及上下文窗口来自登记；Provider 定义不含密钥，密钥由 api_key 单独取得；路线出现在 OpenCode 的路线列表中。
    """
    _register("gateway-opencode", "opencode")
    route = "gateway-opencode"
    assert opencode_provider_catalog.service(route) == "portal-gateway-opencode"
    assert opencode_provider_catalog.api_key(route) == KEY
    provider = opencode_provider_catalog.provider_config(route)
    assert provider == {"portal-gateway-opencode": {
        "npm": "@ai-sdk/openai-compatible", "name": "gateway",
        "options": {"baseURL": "https://gateway.invalid/v1", "headers": {"X-Team": "lab"}},
        "models": {"gpt-test": {"name": "gpt-test", "limit": {"context": 200000, "output": 32768}},
                   "gpt-mini": {"name": "gpt-mini"}}}}
    assert KEY not in json.dumps(provider)
    catalog = opencode_provider_catalog.catalog(route)
    assert (catalog["models"], catalog["default_model"]) == (["gpt-test", "gpt-mini"], "gpt-test")
    assert (catalog["provider_profile"], catalog["upstream_provider"]) == ("gateway", "model_api")
    assert route in {row["backend_route_id"] for row in opencode_provider_catalog.routes()}
    assert opencode_provider_catalog.provider_config(opencode_provider_catalog.ROUTE) == {}
