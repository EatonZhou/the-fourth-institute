from management_portal.society_service.resident_settings_contract import stage_runtime_settings
from management_portal.society_service import resident_backend
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import claude_provider_catalog
from management_portal.execution import backends
from management_portal.execution.accounts import default_execution_accounts
from management_portal.execution.contracts import ExecutionRequest, SessionOptions, InputMedia
from management_portal.society_service import resident_domain
from management_portal.society_service import resident_restart_domain
from management_portal.society_service import resident_session_backend_domain
from tests.cases.test_session_restart import _restart_domain
from management_portal.society_service import resident_runtime_domain


@pytest.fixture
def native_routes(monkeypatch, tmp_path):
    from management_portal.execution import account_registrations as registrations
    from management_portal.execution.account_hosting import account_home
    monkeypatch.setenv("PORTAL_ACCOUNTS_ROOT", str(tmp_path / "accounts"))
    profiles = {name: {
        "id": name, "kind": "subscription_login", "provider": "claude", "enabled": True,
        "config_dir": str(account_home("claude", name)), "default_model": "sonnet",
        "models": [{"id": "sonnet", "enabled": True, "support_efforts": ["high"]}],
    } for name in ("claude_one", "claude_two")}
    for name, profile in profiles.items():
        row = registrations.registration(name)
        config = {"default_model": profile["default_model"], "models": profile["models"]}
        if row:
            registrations.update_registration(name, {"config": config}, row["version"], "test")
        else:
            registrations.create_registration({"account_id": name, "provider": "claude",
                                               "kind": "subscription_login", "route_id": name,
                                               "config": config}, "test")
    monkeypatch.setattr(claude_provider_catalog, "profile", lambda key, **_: profiles[key])
    return profiles


def test_claude_route_is_accepted_by_shared_execution_contract():
    """共享执行请求接受 Claude 路由作为会话的 backend_route_id"""
    request = ExecutionRequest.new(
        service_type="agent_session", provider="claude", operation="turn", actor="test", agent_id="resident:test",
        cwd="/workspace", prompt="hello",
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="claude_one"),
    )
    assert request.validate().session.backend_route_id == "claude_one"


def test_claude_route_override_is_rejected_before_native_execution():
    """执行时改用其他 Claude 路由的请求在调用原生客户端前被拒绝"""
    invocation = SimpleNamespace(
        request=SimpleNamespace(runtime_options={"provider_profile": "claude_two"}, operation="turn", input_media=()),
        session=SimpleNamespace(backend_route_id="claude_one"),
    )
    with mock.patch.object(backends, "shared_claude_client") as client:
        with pytest.raises(ValueError, match="migration"):
            backends.ClaudeBackend().execute(invocation, mock.Mock())
    client.assert_not_called()


def test_claude_images_reach_native_client_and_common_composer(native_routes):
    """Claude 路由把图片交给原生客户端并允许 Chat 附件，纯文本路由不支持图片"""
    from management_portal.society_service import resident_execution_provider, resident_media_domain
    from management_portal import resident_chat_projection

    image = InputMedia('image', 'local_path', '/authorized/image.png', 'image/png', 'image.png')
    request = ExecutionRequest.new(service_type='agent_session', provider='claude', operation='turn',
        actor='test', agent_id='resident:test', cwd='/workspace', prompt='Look', input_media=(image,),
        session=SessionOptions(mode='persistent', binding_id='resident:test', backend_route_id='claude_one'))
    invocation = SimpleNamespace(request=request,
        session=SimpleNamespace(backend_route_id='claude_one', backend_session_id='native'),
        user_prompt='Look', system_context='', attach_system_context=False)
    fake = mock.Mock()
    fake.run_turn.return_value = SimpleNamespace(response='seen', session_id='native', turn_id='turn', usage={})
    with mock.patch.object(backends, 'shared_claude_client', return_value=fake), \
            mock.patch('management_portal.execution.native_harness_tools.prepare_tools', return_value=([], None)):
        backends.ClaudeBackend().execute(invocation, mock.Mock())
    assert fake.run_turn.call_args.kwargs['input_media'] == [image.to_payload()]
    with pytest.raises(ValueError, match='does not support'):
        backends.DshBackend().execute(invocation, mock.Mock())
    record = {'backend': 'claude', 'provider_profile': 'claude_one'}
    capabilities = resident_execution_provider.capability_projection(record)
    assert capabilities['image_input'] is True
    native_routes['deepseek_primary'] = {
        'id': 'deepseek_primary', 'kind': 'openai_compatible_chat', 'provider': 'deepseek',
        'enabled': True, 'default_model': 'text', 'models': [{'id': 'text', 'enabled': True}],
    }
    with mock.patch('management_portal.society_service.resident_backend.execution_account',
                    return_value={'backend_route_id': 'claude_one'}) as account:
        bound = {**record, 'resident_id': 'resident_111111111111'}
        assert resident_execution_provider.capability_projection(bound)['image_input'] is True
        account.return_value = {'backend_route_id': 'deepseek_primary'}
        assert resident_execution_provider.capability_projection(bound)['image_input'] is False
    projected = resident_chat_projection.project_session({
        'resident_id': 'resident_111111111111', 'resident': record,
        'backend_capabilities': capabilities, 'allows': {'DeliverMessage': True}}, 'test')
    assert projected['chat_workspace']['composer']['can_attach'] is True
    assert resident_media_domain.provider_message_with_native_media(record, '[image: /authorized/image.png]', [image.to_payload()]) == '[attached image: image.png]'
    assert '/authorized/image.png' in resident_media_domain.provider_message_with_native_media({'backend': 'kimi'}, 'Look', [image.to_payload()])


def test_native_runtime_settings_use_route_capabilities(native_routes):
    """Claude 运行设置按路由声明的模型、推理强度和权限预设校验"""
    record = {"backend": "claude", "backend_route_id": "claude_one"}
    settings = resident_runtime_domain.validate_backend_runtime_updates(record, {}, {"model": "sonnet", "reasoning_effort": "high"})
    assert settings["reasoning_effort"] == "high"
    # Claude declares the Restricted/Full Access presets; Codex's auto-review is not one of them.
    assert resident_runtime_domain.validate_backend_runtime_updates(
        record, {"model": "sonnet"}, {"permission_preset": "full-access"})["permission_preset"] == "full-access"
    for updates in ({"model": "deepseek-v4-pro"}, {"reasoning_effort": "xhigh"},
                    {"service_tier": "priority"}, {"permission_preset": "auto-review"}):
        with pytest.raises(ValueError):
            resident_runtime_domain.validate_backend_runtime_updates(record, {"model": "sonnet"}, updates)
    # A model switch keeps the current effort; a model without it needs an explicit choice, which the error names.
    native_routes["claude_one"]["models"].append({"id": "haiku", "enabled": True, "support_efforts": []})
    with pytest.raises(ValueError, match=r"set reasoning_effort with the model to one of \[''\]"):
        resident_runtime_domain.validate_backend_runtime_updates(
            record, {"model": "sonnet", "reasoning_effort": "high"}, {"model": "haiku"})
    assert resident_runtime_domain.validate_backend_runtime_updates(
        record, {"model": "sonnet", "reasoning_effort": "high"}, {"model": "haiku", "reasoning_effort": ""})["reasoning_effort"] == ""


@pytest.mark.parametrize('requested,expected', [('', 'claude_one'), ('claude_one', 'claude_one'), ('claude_two', 'claude_two')])
def test_context_fork_preserves_or_selects_native_account(monkeypatch, native_routes, requested, expected):
    """上下文 fork 默认沿用源 Resident 的 Claude 账户，也可指定其他账户"""
    source = {'backend': 'claude', 'provider_profile': 'stale_profile'}
    child = {'backend': 'claude'}
    stage_runtime_settings(child, {'backend': 'claude', 'model': 'sonnet', 'reasoning_effort': 'high'})
    monkeypatch.setattr(resident_backend, 'execution_account',
                        lambda _: {'backend_route_id': 'claude_one'})

    monkeypatch.setattr(resident_runtime_domain, 'default_execution_accounts',
                        lambda: default_execution_accounts(provider_registry=SimpleNamespace(entries=lambda: list(native_routes.values()))))
    selected = resident_runtime_domain.assign_derived_resident_account_route(source, child, source, requested)
    assert selected == expected
    assert child['backend_route_id'] == expected
    assert 'provider_profile' not in child and 'runtime_settings' not in child
    assert 'source_backend_route_id' not in child
    assert child['execution_settings']['model'] == 'sonnet'
    assert child['execution_settings']['reasoning_effort'] == 'high'


def test_context_fork_rejects_unknown_native_account(monkeypatch, native_routes):
    """上下文 fork 指定未知执行账户时报错且不写入路由"""
    source = {'backend': 'claude', 'provider_profile': 'claude_one'}
    child = {'backend': 'claude'}
    stage_runtime_settings(child, {'backend': 'claude', 'model': 'sonnet', 'reasoning_effort': 'high'})
    monkeypatch.setattr(resident_backend, 'execution_account',
                        lambda _: {'backend_route_id': 'claude_one'})
    monkeypatch.setattr(resident_runtime_domain, 'default_execution_accounts',
                        lambda: default_execution_accounts(provider_registry=SimpleNamespace(entries=lambda: list(native_routes.values()))))
    with pytest.raises(ValueError, match='unknown Execution account route'):
        resident_runtime_domain.assign_derived_resident_account_route(source, child, source, 'unknown')
    assert 'backend_route_id' not in child


def _native_command(content, settings=None):
    return resident_session_backend_domain.handle_resident_native_command(
        {"backend": "claude", "provider_profile": "claude_one", "model": "sonnet"},
        content, settings or {"model": "sonnet"}, session_id="native-session", cwd="/workspace",
        actor="test", can_update_runtime=True, can_manage_permissions=False,
        scope_label="native", updated_at="2026-09-26T00:00:00Z", token_usage={}, goal={},
    )


def test_native_slash_model_uses_route_catalog_and_validates_effort(native_routes):
    """原生 /model 命令从路由目录列出模型并校验推理强度"""
    picker = _native_command("/model")["command_payload"]
    assert picker["models"] == ["sonnet"]
    assert picker["efforts_by_model"] == {"sonnet": ["high"]}
    assert _native_command("/model sonnet high")["updates"]["reasoning_effort"] == "high"
    assert not _native_command("/model deepseek-v4-pro")["updates"]
    assert not _native_command("/model sonnet xhigh")["updates"]
    assert _native_command("/model sonnet", {"model": "sonnet", "reasoning_effort": "xhigh"})["updates"]["reasoning_effort"] == ""


def test_a_command_claude_code_does_not_declare_is_answered_not_sent_to_the_model():
    """Claude Code 未声明的斜杠命令由 Portal 直接答复

    Provider 声明中没有的原生命令（如 /fast、/goal）不会作为提示词交给模型，Portal 回复该 Provider 没有这条命令。

    :module: workbench.chat
    :importance: P1
    :design: decision_5c62deb8253a
    """
    for command in ("/fast", "/goal", "/plan"):
        result = _native_command(command)
        assert result["handled"] and result["title"] == f"{command} unavailable"
        assert not result.get("command_payload") and not result.get("updates")
    assert _native_command("/unknown") == {"handled": False}


def test_native_compact_and_status_do_not_require_online_route():
    """原生 /compact 和 /status 命令不需要在线查询路由"""
    # Claude Code compacts on demand with its own /compact (session/compact).
    with mock.patch.object(claude_provider_catalog, "catalog", side_effect=AssertionError("online lookup")):
        result = _native_command("/compact")
        assert result["handled"]
        assert result["native_compact"] is True
        assert result["command_payload"]["command"] == "compact"
        assert "sonnet" in _native_command("/status")["content"]


@pytest.mark.parametrize("record,settings,expected,absent", [
    ({"backend": "claude", "provider_profile": "claude_one"}, {"model": "sonnet", "reasoning_effort": "high"},
     ["- Provider: `Claude Code`", "- Route: `claude_one`", "- Model: `sonnet`", "- Reasoning effort: `high`",
      "- Permissions: `Full Access`"], []),
    # DSH has no permission modes.
    ({"backend": "dsh", "provider_profile": "deepseek_primary"}, {"model": "deepseek-v4-flash", "reasoning_effort": "max"},
     ["- Provider: `DeepSeek Harness`", "- Route: `deepseek_primary`", "- Model: `deepseek-v4-flash`",
      "- Reasoning effort: `max`"], ["Permissions"]),
    # Kimi Code now has account routes; it still has no reasoning effort.
    ({"backend": "kimi"}, {"model": "kimi-for-coding", "permission_preset": "default"},
     ["- Provider: `Kimi Code`", "- Model: `kimi-for-coding`", "- Permissions: `Manual`"], ["Reasoning effort"]),
], ids=["claude", "dsh", "kimi"])
def test_native_status_names_the_provider_its_settings_and_the_sessions_tokens(record, settings, expected, absent):
    """原生会话 /status 列出 Provider、设置和 token 统计

    Claude Code、Kimi Code、DSH 的 /status 列出 Provider 名称、路线、模型、推理强度、权限模式（有的 Provider），并附上与 /portal-tokens 相同的上一轮 token 统计。

    :module: execution.providers
    :design: decision_8b2fa224616b
    """
    # The Resident's token statistics, as the intake passes them (_session_token_usage_payload).
    token_usage = {"summary": {"last_turn_usage": {"input_tokens": 55_850, "output_tokens": 913, "total_tokens": 56_763}}}
    with mock.patch.object(resident_runtime_domain, "resident_runtime_defaults", return_value={}):
        result = resident_session_backend_domain.handle_resident_native_command(
            record, "/status", settings, session_id="native-session", cwd="/workspace",
            actor="test", can_update_runtime=True, can_manage_permissions=False,
            scope_label="native", updated_at="2026-09-26T00:00:00Z", token_usage=token_usage, goal={},
        )
    lines = result["content"].splitlines()
    assert all(line in lines for line in expected), lines
    assert not any(word in result["content"] for word in absent)
    assert "- Last turn: `56763`" in lines


def test_codex_status_names_its_provider_first_like_the_other_providers():
    """Codex /status 首行列出 Provider

    Codex 的 /status 与其他 Provider 一样先列出 Provider 名称。

    :module: execution.providers
    :importance: P3
    :design: decision_8b2fa224616b
    """
    from management_portal.execution import codex_runtime_settings
    assert codex_runtime_settings._codex_status_content({}, "native", {}).splitlines()[2] == "- Provider: `Codex`"


def _permissions_command(backend, content, *, can_manage=True, settings=None):
    with mock.patch.object(resident_runtime_domain, "resident_runtime_defaults", return_value={}):
        return resident_session_backend_domain.handle_resident_native_command(
            {"backend": backend}, content, settings or {}, session_id="native-session", cwd="/workspace",
            actor="alice", can_update_runtime=True, can_manage_permissions=can_manage,
            scope_label="native", updated_at="2026-10-11T00:00:00Z", token_usage={}, goal={},
        )


@pytest.mark.parametrize("backend,restricted", [("claude", "Restricted"), ("kimi", "Manual")])
def test_native_permissions_shows_and_switches_the_providers_permission_mode(backend, restricted):
    """原生会话 /permissions 显示并切换权限模式

    Claude Code 与 Kimi Code 的 /permissions 显示当前权限模式和可选模式，有权限的用户可用 /permissions <模式> 切换，后续 turn 生效。

    :module: execution.providers
    :design: decision_8b2fa224616b
    """
    # As in the Resident's settings and Codex's /permissions: the Provider's own modes.
    shown = _permissions_command(backend, "/permissions")
    assert "当前为 `Full Access`" in shown["content"] and f"`default`（{restricted}）" in shown["content"]
    assert not shown["updates"]
    switched = _permissions_command(backend, "/permissions default")
    assert switched["updates"]["permission_preset"] == "default"
    assert restricted in switched["content"]
    assert _permissions_command(backend, "/permissions auto-review")["title"] == "Invalid permissions"
    denied = _permissions_command(backend, "/permissions default", can_manage=False)
    assert denied["title"] == "Permission denied" and not denied["updates"]


@pytest.mark.parametrize("target_route,strategy,expected_model,expected_effort", [
    ("claude_one", "smart", "opus", "high"),
    # Another account cannot keep the full context; only an explicit handover moves it.
    # claude_two offers no opus, so its default model replaces it; sonnet there offers high.
    ("claude_two", "compact", "sonnet", "high"),
])
def test_claude_restart_preserves_settings_only_on_the_same_route(
    monkeypatch, native_routes, target_route, strategy, expected_model, expected_effort,
):
    """重启 Claude 会话时模型和推理强度按目标账号校正

    同一路由原样保留模型和推理强度；换到另一账号时沿用原设置并按目标账号目录校正：目标没有的模型换成它的默认模型，目标模型支持的推理强度保留。

    :design: decision_ccbb1cac24aa
    """
    native_routes["claude_one"]["models"].append({"id": "opus", "enabled": True, "support_efforts": ["high"]})
    record, _, _ = _restart_domain(monkeypatch, source_provider="claude", source_route="claude_one")
    monkeypatch.setattr(resident_runtime_domain, "read_resident_runtime_settings",
                        lambda *_: {"model": "opus", "reasoning_effort": "high"})
    state = resident_domain.restart_resident_session(record["resident_id"], "alice", {
        "target_provider": "claude", "target_account": target_route, "context_strategy": strategy,
    })
    assert state["target_runtime_settings"]["model"] == expected_model
    assert state["target_runtime_settings"]["reasoning_effort"] == expected_effort


@pytest.mark.parametrize("source_provider,source_route,expected_strategy", [
    ("codex", "zero", "compact"), ("claude", "claude_two", "compact"),
    ("claude", "claude_one", "native"),
])
def test_native_restart_route_and_strategy(monkeypatch, native_routes, source_provider, source_route, expected_strategy):
    """重启到 Claude 时同一账户用原生 Fork，跨 Provider 或跨账户明确选择后改为压缩上下文

    智能选择只是完整保留的兼容写法：跨 Provider 或跨账户无法完整保留时拒绝并保留原会话，不隐式压缩；调用方明确选择压缩交接后才压缩上下文。

    :design: decision_ccbb1cac24aa
    """
    record, operations, saved = _restart_domain(monkeypatch, source_provider=source_provider, source_route=source_route)
    request = {"target_provider": "claude", "target_account": "claude_one", "context_strategy": "smart"}
    if source_provider != "claude":
        request["model"] = "sonnet"  # A cross-Provider switch names its target model.
    if expected_strategy == "compact":
        with pytest.raises(ValueError, match="原会话已保留"):
            resident_domain.restart_resident_session(record["resident_id"], "alice", request)
        assert not operations
        request["context_strategy"] = "compact"
    result = resident_domain.restart_resident_session(record["resident_id"], "alice", request)
    assert result["context_strategy"] == expected_strategy
    assert operations[0]["backend_route_id"] == "claude_one"
    assert operations[0]["operation"] == ("fork" if expected_strategy == "native" else "turn")
    assert result["target_runtime_settings"]["model"] == "sonnet"
    resident_restart_domain.resume_resident_session_restart(operations[0]["step"])
    assert saved[-1]["backend_route_id"] == "claude_one"
    assert "provider_profile" not in saved[-1]
    assert "backend_binding" not in saved[-1]


def test_cross_account_native_fork_is_refused(monkeypatch, native_routes):
    """跨 Claude 账户的原生 Fork 重启被拒绝"""
    record, operations, _ = _restart_domain(monkeypatch, source_provider="claude", source_route="claude_two")
    with pytest.raises(ValueError, match="原生 Fork"):
        resident_domain.restart_resident_session(record["resident_id"], "alice", {
            "target_provider": "claude", "target_account": "claude_one", "context_strategy": "native",
        })
    assert not operations


def test_native_restart_options_do_not_borrow_source_credentials(native_routes, monkeypatch, tmp_path):
    """原生重启选项不沿用源会话凭据，config_dir 由目标账户适配器填入"""
    from management_portal.society_service import resident_record_domain

    # The new session lives under the Resident's session directory; this test
    # is about credentials, so a synthetic record gets a temporary directory.
    monkeypatch.setattr(resident_record_domain, "sessions_path", lambda _record: tmp_path / "sessions")
    options = resident_restart_domain._session_restart_runtime_options(
        {"resident_id": "resident_test", "backend": "codex", "provider_profile": "unrelated"},
        "claude", "alice", target_route="claude_one", runtime_settings={"model": "sonnet"},
    )
    assert "config_dir" not in options  # Account credentials are chosen by the adapter.
    from tests.cases.test_dsh_backend import invocation
    from management_portal.execution.native_options import prepare_invocation
    translated = prepare_invocation(invocation("claude", options, "claude_one")).request.runtime_options
    assert translated["config_dir"] == native_routes["claude_one"]["config_dir"]
    assert translated["provider_profile"] == "claude_one"
    assert translated["model"] == "sonnet"


def test_historical_defaults_do_not_require_live_auth_or_catalog(monkeypatch):
    """读取历史记录的运行默认值不查询在线 Provider 模型目录"""
    monkeypatch.setattr(claude_provider_catalog, "catalog", lambda *_: pytest.fail("history must not query provider"))
    record = {"backend": "claude", "provider_profile": "claude_one", "model": "sonnet"}
    assert resident_runtime_domain.resident_runtime_defaults(record)["model"] == "sonnet"


def test_account_helper_uses_native_subscription_login(monkeypatch, tmp_path):
    """Claude 账户脚本用订阅方式登录，清空 ANTHROPIC_API_KEY 并新建 0700 配置目录"""
    from scripts import claude_code_account

    directory = tmp_path / "new-account"
    monkeypatch.setattr("sys.argv", ["account", "--config-dir", str(directory), "login"])
    monkeypatch.setattr(claude_code_account.shutil, "which", lambda _: "/pinned/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-account")
    with mock.patch.object(claude_code_account.subprocess, "call", return_value=0) as run:
        assert claude_code_account.main() == 0
    assert run.call_args.args[0] == ["/pinned/claude", "auth", "login", "--claudeai"]
    assert run.call_args.kwargs["env"]["ANTHROPIC_API_KEY"] == ""
    assert run.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(directory)
    assert directory.is_dir()
    assert directory.stat().st_mode & 0o777 == 0o700


def test_background_invocation_leaves_the_claude_route_to_the_resident(monkeypatch):
    """后台调用沿用 Resident 自己的 Claude 路由和设置，不修改 Resident 记录

    Provider, account and settings belong to the Resident: a background request
    runs with them and changes none of them (RestartSession and
    UpdateResidentSettings do)."""
    from management_portal import isa_client, resident_handoff

    monkeypatch.setattr(isa_client, "call", mock.Mock(side_effect=AssertionError("no HATG instruction")))
    service = mock.Mock()
    record = {"resident_id": "resident_test", "lineage_id": "lineage_test", "backend": "claude",
              "backend_route_id": "claude_one", "title": "Embedded Resident", "workspace_paths": ["/work"]}
    result = resident_handoff._configure_invocation_resident(
        service, record, "alice", backend="claude", cwd="/work")
    assert result == record
    service.resident.update_resident.assert_not_called()
