""":module: execution.providers"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from management_portal import claude_provider_catalog as catalog
from management_portal.execution import account_registrations
from management_portal.execution.account_hosting import account_home

from tests.cases.claude_sdk_support import load_claude_module, sdk_of
from tests.cases.model_api_test_support import register_accounts, register_apis

daemon_module = load_claude_module("claude_code_daemon")

MODELS = {"default_model": "sonnet",
          "models": [{"id": "sonnet", "enabled": True, "support_efforts": ["low", "high"]},
                     {"id": "opus", "enabled": True}]}


def test_subscription_registration_accepts_named_and_detailed_models(tmp_path, monkeypatch):
    from management_portal.execution import account_registrations, account_hosting
    from management_portal import claude_provider_catalog as catalog
    row = {"account_id": "claude_names", "route_id": "names", "kind": "subscription_login", "enabled": True,
           "config": {"default_model": "sonnet", "models": ["sonnet", {"id": "opus", "enabled": False}]}}
    monkeypatch.setattr(account_registrations, "registrations", lambda provider: [row])
    monkeypatch.setattr(account_hosting, "account_home", lambda provider, account: tmp_path)
    resolved = catalog.profile("names")
    assert catalog.catalog("names")["models"] == ["sonnet"]
    assert catalog.validate_settings(resolved, "", "") == ("sonnet", "")
    assert row["config"]["models"][0] == "sonnet"
    assert resolved["models"][1]["enabled"] is False


def native_profile(config_dir: Path) -> dict:
    """The entry catalog.profile() derives from a Claude login registration."""
    return {"id": "claude_personal", "provider": "claude", "kind": "subscription_login",
            "enabled": True, "config_dir": str(config_dir), **MODELS}



class _EchoingClient:
    """A fake Claude client that echoes each prompt back first, as the CLI does.

    The daemon only accepts the result that follows its own prompt's echo
    (e7b16c49), so a client without the echo makes a turn read forever.
    """

    prompt_uuid = ""

    async def query(self, prompt):
        async for message in prompt:
            self.prompt_uuid = str(message.get("uuid") or "")

    def echo(self):
        return daemon_module.UserMessage(content="echo", uuid=self.prompt_uuid)

class ClaudeProviderCatalogTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        register_apis({
            "id": "deepseek_primary", "enabled": True, "kind": "openai_compatible_chat",
            "provider": "deepseek", "default_model": "deepseek-v4-flash",
            "api_key_file": str(self.root / "secret"),
            "models": [{"id": "deepseek-v4-flash", "enabled": True}],
        })
        # The isolated site seeds its own Claude routes; these cases own theirs.
        with account_registrations.database().transaction("fixture") as connection:
            connection.execute("DELETE FROM backend_roles WHERE account_id IN "
                               "(SELECT account_id FROM registrations WHERE provider='claude')")
            connection.execute("DELETE FROM registrations WHERE provider='claude'")
        register_accounts(
            {"account_id": "deepseek_primary", "provider": "claude", "kind": "api_profile",
             "config": {"api_id": "deepseek_primary"}},
            {"account_id": "claude_personal", "provider": "claude", "kind": "subscription_login",
             "config": MODELS},
        )
        self.native = catalog.profile("claude_personal")

    def test_native_and_legacy_routes_remain_distinct_and_public(self):
        """原生登录路由和 API key 路由分别列出，公开信息不含密钥文件和配置目录"""
        routes = catalog.routes()
        self.assertEqual([route["authentication"] for route in routes], ["api_key", "claude_login"])
        self.assertEqual(routes[0]["default_model"], "deepseek-v4-flash")
        self.assertEqual(routes[1]["default_model"], "sonnet")
        for route in routes:
            self.assertNotIn("api_key_file", route)
            self.assertNotIn("config_dir", route)
            self.assertTrue(route["capabilities"]["active_turn_input"])
            self.assertEqual(route["settings_apply"], "next_turn")

    def test_missing_or_disabled_route_never_falls_back(self):
        """Claude 路由缺失或停用时报不可用，不回退到其他路由"""
        self.assertEqual(self.native["config_dir"], str(account_home("claude", "claude_personal")))
        account_registrations.update_registration("claude_personal", {"enabled": False}, 1, "test")
        for route in (self.native["id"], "missing"):
            with self.subTest(route=route), self.assertRaisesRegex(ValueError, "unavailable"):
                catalog.profile(route)

    def test_subscription_route_rejects_api_credentials_and_relative_directory(self):
        """订阅路由拒绝 API 凭据、自定义 base_url 和不合法的配置目录"""
        for key, value in (("base_url", "https://proxy.invalid"), ("api_key_file", "/secret"),
                           ("api_key_env", "KEY"), ("config_dir", "/other")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                account_registrations.create_registration(
                    {"account_id": "claude_other", "provider": "claude", "kind": "subscription_login",
                     "config": {**MODELS, key: value}}, "test")
        for value in ("relative", "/", "/account/../other"):
            entry = {**self.native, "config_dir": value}
            with self.subTest(config_dir=value), self.assertRaises(ValueError):
                catalog.config_directory(entry)
            with self.subTest(config_dir=value), self.assertRaises(ValueError):
                catalog.login_environment(entry)

    def test_login_masks_conflicting_credentials_without_reading_them(self):
        """Claude 登录环境清空冲突的 API 密钥和令牌，且不读取凭据文件"""
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "do-not-use",
                                          "CLAUDE_CODE_OAUTH_TOKEN": "other-account"}), \
                mock.patch.object(Path, "read_text", side_effect=AssertionError("credential read")):
            env = catalog.login_environment(self.native)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], self.native["config_dir"])
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.anthropic.com")

    def test_models_and_efforts_are_route_specific(self):
        """模型和推理强度按路由校验，模型别名解析为带版本的模型 ID"""
        self.assertEqual(catalog.validate_settings(self.native, "", ""), ("sonnet", ""))
        self.assertEqual(catalog.validate_settings(self.native, "sonnet", "high"), ("sonnet", "high"))
        for model, effort in (("deepseek-v4-flash", ""), ("sonnet", "xhigh"), ("opus", "high")):
            with self.subTest(model=model, effort=effort), self.assertRaises(ValueError):
                catalog.validate_settings(self.native, model, effort)
        versioned = {**self.native, "default_model": "claude-opus-5-5", "models": [
            {"id": "claude-opus-5-5", "aliases": ["opus"], "enabled": True,
             "support_efforts": ["low", "medium", "high", "xhigh", "max"]},
            {"id": "claude-sonnet-5", "aliases": ["sonnet"], "enabled": True,
             "support_efforts": ["low", "high"]},
        ]}
        self.assertEqual(catalog.model_catalog(versioned)["models"],
                         ["claude-opus-5-5", "claude-sonnet-5"])
        self.assertEqual(catalog.validate_settings(versioned, "", "max"), ("claude-opus-5-5", "max"))
        self.assertEqual(catalog.validate_settings(versioned, "sonnet", "high"), ("claude-sonnet-5", "high"))
        self.assertEqual(catalog.validate_settings(versioned, "claude-opus-5-5", "xhigh"),
                         ("claude-opus-5-5", "xhigh"))
        with self.assertRaises(ValueError):
            catalog.validate_settings(versioned, "claude-sonnet-5", "max")
        for aliases in (["opus", "opus"], ["claude-sonnet-5"], [""], "opus", [None]):
            with self.subTest(aliases=aliases), self.assertRaises(ValueError):
                catalog.model_catalog({**versioned, "models": [
                    {**versioned["models"][0], "aliases": aliases}, versioned["models"][1]]})

    def test_invalid_model_contract_is_rejected(self):
        """默认模型不存在、模型列表为空或重复、推理强度未知时拒绝该模型目录"""
        for change in ({"default_model": "absent"}, {"models": []},
                       {"models": self.native["models"] * 2},
                       {"models": [{"id": "sonnet", "enabled": True, "support_efforts": ["invented"]}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                catalog.model_catalog({**self.native, **change})


class NativeClaudeDaemonTest(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_event_is_available_through_the_account_snapshot(self):
        """轮次中的限额事件进入账户用量快照，快照不含原始数据"""
        RateLimitInfo = sdk_of(daemon_module).RateLimitInfo
        class Client(_EchoingClient):
            async def receive_response(self):
                yield self.echo()
                yield daemon_module.RateLimitEvent(
                    rate_limit_info=RateLimitInfo(status="allowed_warning", raw={
                        "status": "allowed_warning", "rateLimitType": "five_hour", "utilization": .8,
                    }), uuid="event", session_id="session")
                yield daemon_module.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                    is_error=False, num_turns=1, session_id="session", result="OK")

        daemon = daemon_module.ClaudeCodeDaemon()
        handle = daemon_module.SessionHandle(key="key", signature=(), client=Client())
        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
            result = await daemon._run_turn_once({"prompt": "hi", "turn_id": "turn", "provider_profile": "one",
                                                  "config_dir": "/account"}, mock.AsyncMock())
        self.assertEqual(result["response"], "OK")
        with mock.patch.object(daemon_module, "_provider_profile", return_value={**native_profile(Path('/account')), "id": "one"}):
            with mock.patch.object(daemon.account_usage, "request_refresh"):
                snapshot = daemon.account_usage_snapshot({"provider_profile": "one"})
        self.assertEqual(snapshot["rate_limits"][0]["remaining_percent"], 20)
        self.assertNotIn("raw", snapshot["rate_limits"][0])

    async def test_result_is_terminal_and_text_alone_is_not_completion(self):
        """只有 ResultMessage 才结束轮次，只收到文本时轮次报错"""
        for has_result in (False, True):
            with self.subTest(has_result=has_result):
                class Client(_EchoingClient):
                    async def receive_response(self):
                        yield self.echo()
                        yield daemon_module.AssistantMessage(
                            content=[daemon_module.TextBlock("working commentary")],
                            model="sonnet", session_id="native-session")
                        if has_result:
                            yield daemon_module.ResultMessage(
                                subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id="native-session",
                                result="final answer", usage={"input_tokens": 3, "output_tokens": 2})

                daemon = daemon_module.ClaudeCodeDaemon()
                handle = daemon_module.SessionHandle(key="key", signature=(), client=Client())
                events = []

                async def emit(event):
                    events.append(event)

                with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
                    if has_result:
                        result = await daemon._run_turn_once({"prompt": "hello", "turn_id": "turn-a"}, emit)
                        self.assertEqual(result["response"], "final answer")
                    else:
                        with self.assertRaisesRegex(RuntimeError, "without a ResultMessage"):
                            await daemon._run_turn_once({"prompt": "hello", "turn_id": "turn-a"}, emit)
                text_event = next(event for event in events if event.get("method") == "item/completed")
                self.assertEqual(text_event["params"]["item"]["id"], "turn-a:final" if has_result else "turn-a:1:text:0")
                self.assertEqual(text_event["params"]["item"]["phase"], "final_answer" if has_result else "commentary")
                self.assertEqual(text_event["params"]["item"]["text"], "final answer" if has_result else "working commentary")
                self.assertEqual(text_event["params"]["turnId"], "turn-a")
                self.assertEqual(sum(event.get("method") == "turn/completed" for event in events), int(has_result))
                self.assertEqual(handle.active_turn_id, "")

    async def test_tool_commentary_and_final_answer_have_distinct_phases(self):
        """工具调用前的文本标为 commentary，最终回答标为 final_answer"""
        for result_text in ("", "authoritative result"):
            with self.subTest(result_text=result_text):
                class Client(_EchoingClient):
                    async def receive_response(self):
                        yield self.echo()
                        yield daemon_module.AssistantMessage(content=[
                            daemon_module.TextBlock("Checking file"),
                            daemon_module.ToolUseBlock(id="tool-1", name="Bash", input={"command": "pwd"}),
                        ], model="sonnet")
                        yield daemon_module.UserMessage(content=[
                            daemon_module.ToolResultBlock(tool_use_id="tool-1", content="/workspace"),
                        ])
                        yield daemon_module.AssistantMessage(content=[
                            daemon_module.TextBlock("Answer "), daemon_module.TextBlock("complete"),
                        ], model="sonnet")
                        yield daemon_module.ResultMessage(
                            subtype="success", duration_ms=1, duration_api_ms=1,
                            is_error=False, num_turns=2, session_id="session",
                            result=result_text, usage={})

                daemon = daemon_module.ClaudeCodeDaemon()
                handle = daemon_module.SessionHandle(key="key", signature=(), client=Client())
                events = []
                async def emit(event):
                    events.append(event)
                with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
                    result = await daemon._run_turn_once({"prompt": "hello", "turn_id": "turn"}, emit)
                items = [e["params"]["item"] for e in events if e.get("method") == "item/completed"]
                self.assertEqual([(i["phase"], i["text"]) for i in items], [
                    ("commentary", "Checking file"),
                    ("final_answer", result_text or "Answer complete"),
                ])
                self.assertEqual(items[-1]["text"], result["response"])
                self.assertEqual(items[-1]["id"], "turn:final")
                self.assertEqual(events[-1]["method"], "turn/completed")

    async def test_uses_account_login_with_separate_native_session_identity(self):
        """Claude 会话以所选账户的登录目录启动，非 owner 会话禁用 Bash"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = native_profile(root / "account")
            fake = mock.Mock(connect=mock.AsyncMock())
            params = {"session_key": "resident:one", "cwd": str(root),
                      "config_dir": profile["config_dir"], "provider_profile": profile["id"],
                      "model": "sonnet", "effort": "high", "is_owner": False}
            with mock.patch.object(daemon_module, "_provider_profile", return_value=profile), \
                    mock.patch.object(daemon_module, "ClaudeSDKClient", return_value=fake) as factory:
                handle = await daemon_module.ClaudeCodeDaemon()._create_handle(params)
            options = factory.call_args.args[0]
            self.assertEqual(options.env["CLAUDE_CONFIG_DIR"], profile["config_dir"])
            self.assertEqual(options.env["ANTHROPIC_AUTH_TOKEN"], "")
            self.assertEqual(options.permission_mode, "dontAsk")
            self.assertIn("Bash", options.disallowed_tools)
            self.assertEqual(options.setting_sources, [])
            self.assertEqual(options.model, "sonnet")
            self.assertEqual(handle.key, "resident:one")
            fake.connect.assert_awaited_once()

    async def test_cannot_resume_using_another_accounts_directory(self):
        """不能用其他账户的配置目录恢复 Claude 会话"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = native_profile(root / "account-a")
            params = {"session_key": "resident:one", "cwd": str(root),
                      "config_dir": str(root / "account-b"), "provider_profile": profile["id"]}
            with mock.patch.object(daemon_module, "_provider_profile", return_value=profile), \
                    mock.patch.object(daemon_module, "ClaudeSDKClient") as factory:
                with self.assertRaisesRegex(ValueError, "selected account"):
                    await daemon_module.ClaudeCodeDaemon()._create_handle(params)
            factory.assert_not_called()
            self.assertFalse((root / "account-b").exists())

    async def test_stale_cancel_cannot_interrupt_a_new_turn(self):
        """轮次或会话 ID 已过期的取消请求不能中断新轮次"""
        daemon = daemon_module.ClaudeCodeDaemon()
        client = mock.Mock(interrupt=mock.AsyncMock())
        daemon._sessions["key"] = daemon_module.SessionHandle(
            key="key", signature=(), client=client, session_id="session", active_turn_id="new-turn")
        for identity in ({"turn_id": "old-turn", "session_id": "session"},
                         {"turn_id": "new-turn", "session_id": "old-session"}):
            result = await daemon.interrupt({"session_key": "key", **identity})
            self.assertFalse(result["interrupted"])
        client.interrupt.assert_not_awaited()
        result = await daemon.interrupt({"session_key": "key", "session_id": "session", "turn_id": "new-turn"})
        self.assertTrue(result["interrupted"])
        self.assertEqual(result["turn_id"], "new-turn")
        client.interrupt.assert_awaited_once()
