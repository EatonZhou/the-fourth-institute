""":module: workbench.chat"""
from __future__ import annotations

import unittest
from unittest import mock

from management_portal.execution import resident_runtime_settings, codex_runtime_settings, slash_commands
from management_portal.execution.harness_settings import normalize_agent_harness_settings


MODEL_CATALOG = {
    "source": "model/list",
    "account_id": "bound-account",
    "models": ["gpt-6.1-sol", "gpt-5.6-sol", "gpt-5.6-luna", "limited-model"],
    "default_model": "gpt-5.6-sol",
    "default_reasoning_effort": "low",
    "efforts_by_model": {
        "gpt-6.1-sol": ["low", "medium", "high", "xhigh", "max"],
        "gpt-5.6-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
        "gpt-5.6-luna": ["low", "medium", "high", "xhigh", "max"],
        "limited-model": ["low"],
    },
    "default_efforts_by_model": {
        "gpt-6.1-sol": "medium",
        "gpt-5.6-sol": "low",
        "gpt-5.6-luna": "medium",
        "limited-model": "low",
    },
    "service_tiers_by_model": {
        "gpt-6.1-sol": [{"id": "priority"}],
        "gpt-5.6-sol": [{"id": "priority"}],
        "gpt-5.6-luna": [],
        "limited-model": [],
    },
}


class PortalSlashCommandsTest(unittest.TestCase):
    def test_runtime_catalog_defaults_match_execution_for_every_model(self) -> None:
        """运行设置目录中每个模型的默认推理强度与实际执行时一致"""
        with mock.patch("management_portal.execution.get_execution_service") as service:
            service.return_value.runtime_catalog.return_value = codex_runtime_settings.resolve_codex_runtime_catalog(MODEL_CATALOG)
            catalog = codex_runtime_settings.codex_runtime_catalog(account_id="bound-account")
            for model in catalog["models"]:
                with self.subTest(model=model):
                    actual = codex_runtime_settings.resolve_codex_runtime_configuration(
                        {"model": model}, account_id="bound-account",
                    )
                    self.assertEqual(catalog["default_efforts_by_model"][model], actual["reasoning_effort"])
            self.assertEqual(MODEL_CATALOG["default_efforts_by_model"]["gpt-5.6-sol"], "low")

    def test_selecting_default_effort_resolves_supported_execution_value(self) -> None:
        """推理强度选“默认”时保存为该模型实际支持的值"""
        with mock.patch("management_portal.execution.get_execution_service") as service:
            service.return_value.runtime_catalog.return_value = codex_runtime_settings.resolve_codex_runtime_catalog(MODEL_CATALOG)
            saved = codex_runtime_settings.validate_codex_runtime_updates(
                {"model": "limited-model", "reasoning_effort": "low"},
                {"model": "limited-model", "reasoning_effort": ""},
                account_id="bound-account",
            )
        self.assertEqual(saved["reasoning_effort"], "low")

    def test_builtin_runtime_default_is_sol_high(self) -> None:
        """codex 内置默认模型为 gpt-6.1-sol、推理强度 high，空值回落到默认"""
        self.assertEqual(codex_runtime_settings.BUILTIN_PORTAL_DEFAULT_MODEL, "gpt-6.1-sol")
        self.assertEqual(codex_runtime_settings.BUILTIN_PORTAL_DEFAULT_REASONING_EFFORT, "high")
        with (
            mock.patch.object(codex_runtime_settings, "PORTAL_DEFAULT_MODEL", codex_runtime_settings.BUILTIN_PORTAL_DEFAULT_MODEL),
            mock.patch.object(
                codex_runtime_settings,
                "PORTAL_DEFAULT_REASONING_EFFORT",
                codex_runtime_settings.BUILTIN_PORTAL_DEFAULT_REASONING_EFFORT,
            ),
            mock.patch("management_portal.execution.get_execution_service") as service,
        ):
            service.return_value.runtime_catalog.return_value = codex_runtime_settings.resolve_codex_runtime_catalog(MODEL_CATALOG)
            effective = codex_runtime_settings.effective_codex_runtime_settings({})
            blank = codex_runtime_settings.effective_codex_runtime_settings({"model": "", "reasoning_effort": ""})
            explicit = codex_runtime_settings.effective_codex_runtime_settings({"model": "gpt-5.6-sol", "reasoning_effort": "high"})
            model_only = codex_runtime_settings.effective_codex_runtime_settings({"model": "gpt-6.1-sol"})
            catalog = codex_runtime_settings.codex_runtime_catalog()
        self.assertEqual(effective["model"], "gpt-6.1-sol")
        self.assertEqual(effective["reasoning_effort"], "high")
        self.assertEqual(blank["model"], "gpt-6.1-sol")
        self.assertEqual(blank["reasoning_effort"], "high")
        self.assertEqual(explicit["model"], "gpt-5.6-sol")
        self.assertEqual(explicit["reasoning_effort"], "high")
        self.assertEqual(model_only["reasoning_effort"], "high")
        self.assertEqual(catalog["default_model"], "gpt-6.1-sol")
        self.assertEqual(catalog["default_reasoning_effort"], "high")
        self.assertEqual(catalog["efforts_by_model"]["gpt-6.1-sol"], ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(catalog["default_efforts_by_model"]["gpt-6.1-sol"], "high")
        self.assertEqual(catalog["service_tiers_by_model"]["gpt-6.1-sol"], [{"id": "priority"}])
        provider = {
            "source": "model/list", "account_id": "bound-account", "models": ["gpt-6.1-sol", "limited-model"],
            "default_model": "limited-model", "default_reasoning_effort": "low",
            "efforts_by_model": {"gpt-6.1-sol": ["high", "xhigh"], "limited-model": ["low"]},
            "default_efforts_by_model": {"gpt-6.1-sol": "high", "limited-model": "low"},
        }
        with mock.patch("management_portal.execution.get_execution_service") as service:
            service.return_value.runtime_catalog.return_value = codex_runtime_settings.resolve_codex_runtime_catalog(provider)
            actual = codex_runtime_settings.codex_runtime_catalog(account_id="bound-account")
            service.return_value.runtime_catalog.assert_called_once_with("bound-account")
        effective = codex_runtime_settings.effective_codex_runtime_settings({})
        self.assertEqual(actual["default_model"], effective["model"])
        self.assertEqual(actual["default_reasoning_effort"], effective["reasoning_effort"])
        self.assertEqual(actual["default_efforts_by_model"]["limited-model"], "low")
        self.assertEqual(actual["efforts_by_model"], provider["efforts_by_model"])
        self.assertEqual(provider["default_reasoning_effort"], "low")
        self.assertEqual(actual["defaults_source"], "portal")
        actual["models"].append("not-from-provider")
        self.assertNotIn("not-from-provider", provider["models"])

    def test_slash_command_catalog_matches_supported_backends(self) -> None:
        """斜杠命令目录按 Provider 声明的能力列出命令"""
        codex_commands = [
            item["command"]
            for item in slash_commands.slash_command_catalog(backend="codex")
        ]
        self.assertEqual(codex_commands, [
            "/status",
            "/goal",
            "/plan",
            "/model",
            "/fast",
            "/permissions",
            "/compact",
            "/portal-help",
            "/portal-tokens",
        ])

        # The others offer what they declare: no Codex goal, Plan mode or
        # service tier; DSH has no permission setting.
        portal = ["/portal-help", "/portal-tokens"]
        self.assertEqual({
            backend: [item["command"] for item in slash_commands.slash_command_catalog(backend=backend)]
            for backend in ("claude", "kimi", "dsh", "portal")
        }, {
            "claude": ["/status", "/model", "/permissions", "/compact", *portal],
            "kimi": ["/status", "/model", "/permissions", "/compact", *portal],
            "dsh": ["/status", "/model", "/compact", *portal],
            "portal": portal,
        })

    def test_model_capabilities_come_from_bound_provider(self) -> None:
        """/model 选择器的模型和推理强度来自会话绑定账户的 Provider 目录"""
        with mock.patch("management_portal.execution.get_execution_service") as service:
            service.return_value.runtime_catalog.return_value = codex_runtime_settings.resolve_codex_runtime_catalog(MODEL_CATALOG)
            result = codex_runtime_settings.handle_codex_native_command("/model", {}, account_id="bound-account")
            service.return_value.runtime_catalog.assert_called_once_with("bound-account")
        picker = result["command_payload"]
        self.assertEqual(picker["models"], MODEL_CATALOG["models"])
        self.assertEqual(picker["current"]["model"], "gpt-6.1-sol")
        self.assertEqual(picker["current"]["reasoning_effort"], "high")
        self.assertIn("ultra", picker["efforts_by_model"]["gpt-5.6-sol"])
        self.assertNotIn("ultra", picker["efforts_by_model"]["gpt-5.6-luna"])

    def test_model_command_accepts_supported_ultra(self) -> None:
        """/model 对支持 ultra 的模型接受 ultra 推理强度"""
        with mock.patch("management_portal.execution.get_execution_service") as service:
            service.return_value.runtime_catalog.return_value = codex_runtime_settings.resolve_codex_runtime_catalog(MODEL_CATALOG)
            result = codex_runtime_settings.handle_codex_native_command(
                "/model gpt-5.6-sol ultra",
                {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
            )
        self.assertEqual(result["title"], "Codex model updated")
        self.assertEqual(result["updates"]["reasoning_effort"], "ultra")

    def test_model_command_rejects_unsupported_ultra(self) -> None:
        """/model 对不支持 ultra 的模型拒绝 ultra 推理强度且不更新设置"""
        with mock.patch("management_portal.execution.get_execution_service") as service:
            service.return_value.runtime_catalog.return_value = codex_runtime_settings.resolve_codex_runtime_catalog(MODEL_CATALOG)
            result = codex_runtime_settings.handle_codex_native_command(
                "/model gpt-5.6-luna ultra",
                {"model": "gpt-5.6-luna", "reasoning_effort": "high"},
            )
        self.assertEqual(result["title"], "Invalid reasoning effort")
        self.assertFalse(result.get("updates"))
        self.assertIn("不支持", result["content"])

    def test_runtime_updates_atomically_validate_model_effort_and_speed(self) -> None:
        """运行设置更新整体校验模型、推理强度和速度档，不支持的组合被拒绝"""
        current = {"model": "unlisted-current", "reasoning_effort": "xhigh"}
        with mock.patch("management_portal.execution.get_execution_service") as service:
            service.return_value.runtime_catalog.return_value = codex_runtime_settings.resolve_codex_runtime_catalog(MODEL_CATALOG)
            reconciled = codex_runtime_settings.validate_codex_runtime_updates(
                current,
                {"collaboration_mode": "plan"},
                account_id="bound-account",
            )
            self.assertEqual(reconciled, {"collaboration_mode": "plan"})
            service.return_value.runtime_catalog.assert_not_called()
            selected = codex_runtime_settings.validate_codex_runtime_updates(
                current,
                {"model": "limited-model"},
                account_id="bound-account",
            )
            self.assertEqual(selected["model"], "limited-model")
            self.assertEqual(selected["reasoning_effort"], "low")
            fast = codex_runtime_settings.validate_codex_runtime_updates(
                {},
                {"model": "gpt-5.6-sol", "service_tier": "priority"},
                account_id="bound-account",
            )
            self.assertEqual(fast["service_tier"], "priority")
            with self.assertRaisesRegex(ValueError, "不支持 reasoning effort"):
                codex_runtime_settings.validate_codex_runtime_updates(
                    current,
                    {"model": "limited-model", "reasoning_effort": "xhigh"},
                    account_id="bound-account",
                )
            with self.assertRaisesRegex(ValueError, "不支持 speed priority"):
                codex_runtime_settings.validate_codex_runtime_updates(
                    {},
                    {"model": "limited-model", "service_tier": "priority"},
                    account_id="bound-account",
                )
            self.assertEqual(current["reasoning_effort"], "xhigh")

    def test_plan_command_toggles_native_collaboration_mode(self) -> None:
        """不带参数的 /plan 在 plan 和 default 协作模式间切换"""
        enabled = codex_runtime_settings.handle_codex_native_command(
            "/plan",
            {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
            actor="sample_admin",
        )
        self.assertEqual(enabled["updates"]["collaboration_mode"], "plan")
        self.assertEqual(enabled["command_payload"]["mode"], "plan")

        disabled = codex_runtime_settings.handle_codex_native_command(
            "/plan",
            enabled["updates"],
            actor="sample_admin",
        )
        self.assertEqual(disabled["updates"]["collaboration_mode"], "default")

    def test_plan_command_supports_explicit_mode_and_permissions(self) -> None:
        """/plan on 显式开启 Plan 模式，无运行设置权限时拒绝修改"""
        explicit = codex_runtime_settings.handle_codex_native_command(
            "/plan on",
            {},
        )
        self.assertEqual(explicit["updates"]["collaboration_mode"], "plan")
        denied = codex_runtime_settings.handle_codex_native_command(
            "/plan off",
            explicit["updates"],
            can_update_runtime=False,
        )
        self.assertEqual(denied["title"], "Permission denied")
        self.assertFalse(denied.get("updates"))

    def test_goal_command_delegates_mutations_to_native_protocol(self) -> None:
        """/goal 的设置和恢复交给 codex 原生协议，无会话时暂停报不可用"""
        created = codex_runtime_settings.handle_codex_native_command(
            "/goal finish the Portal integration",
            {},
            actor="sample_admin",
        )
        self.assertFalse(created["handled"])
        self.assertTrue(created["native_goal"])
        self.assertEqual(created["goal_action"], {
            "action": "set",
            "objective": "finish the Portal integration",
        })

        resumed = codex_runtime_settings.handle_codex_native_command(
            "/goal resume",
            {},
            actor="sample_admin",
            session_id="019f0000-0000-7000-8000-000000000217",
        )
        self.assertEqual(resumed["goal_action"], {"action": "resume"})

        unavailable = codex_runtime_settings.handle_codex_native_command(
            "/goal pause",
            {},
            actor="sample_admin",
        )
        self.assertTrue(unavailable["handled"])
        self.assertEqual(unavailable["title"], "Codex goal unavailable")

    def test_goal_command_renders_native_state_and_has_strict_boundary(self) -> None:
        """/goal 显示原生目标状态和用量，/goalkeeper 不被识别为 /goal"""
        shown = codex_runtime_settings.handle_codex_native_command(
            "/goal",
            {},
            session_id="019f0000-0000-7000-8000-000000000218",
            goal={
                "objective": "ship it",
                "status": "paused",
                "tokensUsed": 42,
                "timeUsedSeconds": 3,
                "tokenBudget": 1000,
            },
        )
        self.assertTrue(shown["handled"])
        self.assertIn("`paused`", shown["content"])
        self.assertIn("`42`", shown["content"])
        self.assertIn("ship it", shown["content"])
        self.assertEqual(slash_commands.parse_goal_command("/goalkeeper"), {})

    def test_codex_overrides_include_collaboration_mode(self) -> None:
        """codex 命令行覆盖参数包含协作模式"""
        overrides = codex_runtime_settings.codex_cli_overrides(
            {"collaboration_mode": "plan"}
        )
        self.assertEqual(overrides["collaboration_mode"], "plan")

    def test_help_and_unknown_command_list_the_commands_of_the_sessions_provider(self) -> None:
        """/portal-help 和未知命令提示只列出当前会话 Provider 支持的命令"""
        help_text = slash_commands.handle_slash_command("/portal-help", {}, scope_label="s", backend="claude")["content"]
        # The heading names no Provider; the list is the session's (sample_admin 10-16).
        self.assertIn("当前会话命令：\n", help_text)
        self.assertNotIn("Claude", help_text)
        self.assertIn("`/compact`", help_text)
        self.assertNotIn("`/fast`", help_text)
        self.assertNotIn("Codex", help_text)
        self.assertIn("`/fast`", slash_commands.handle_slash_command("/portal-help", {}, backend="codex")["content"])
        # Browsers keep Ctrl/⌘+Tab and macOS keeps ⌘+` for switching tabs and
        # windows; the help names the key a page receives (sample_admin 10-13). Chat
        # Markdown cannot show a backtick in code, so the help names the key.
        shortcut = next(line for line in help_text.splitlines() if "上一个 Portal 页面" in line)
        self.assertTrue(shortcut.startswith("- `Ctrl` + 反引号键"), shortcut)
        self.assertNotIn("\\", help_text)
        # A session without a Provider (a Portal rules Resident) has Portal's own commands only.
        unknown = slash_commands.handle_slash_command("/status", {}, backend="portal")
        self.assertEqual(unknown["title"], "Unknown slash command")
        self.assertIn("`/portal-help`、`/portal-tokens`", unknown["content"])
        self.assertNotIn("`/status`", unknown["content"].split("支持", 1)[1])
        self.assertFalse(unknown["command_payload"])

    def test_portal_help_describes_input_paste_and_message_context_actions(self) -> None:
        """/portal-help 说明输入框右键粘贴和消息右键的引用、转发操作"""
        result = slash_commands.handle_slash_command("/portal-help", {}, scope_label="test session")

        self.assertIn("在输入框中点击右键：粘贴剪贴板内容。", result["content"])
        self.assertIn("右键一条消息", result["content"])
        self.assertIn("引用回复", result["content"])
        self.assertIn("Forward", result["content"])
        self.assertIn("多选消息并转发", result["content"])
        self.assertNotIn("右键消息里的网页链接", result["content"])
        self.assertNotIn("右键菜单", result["content"])
        self.assertNotIn("`Attach`", result["content"])
        self.assertNotIn("`Edit`", result["content"])

    def test_portal_help_lists_the_chat_and_lineage_canvas_keys_in_their_own_groups(self) -> None:
        """/portal-help 分组列出通用、Chat 与 Lineage 画布快捷键

        /portal-help 的快捷键分为通用、Chat、Lineage 画布三组：Chat 组包含发送、撤销、命令与提及菜单、提问卡片数字键、Mailbox 菜单键与排序键，不写选段的“添加到上下文”；画布组包含缩放、适配和多选，不写 Esc 和 Fork 方式菜单；标题不写 Provider 名称，键盘操作不写“焦点”。
        """
        # sample_admin 10-13: the Chat and Lineage canvas keys join the help;
        # tests/cases/ui/portal_help_keys.spec.js checks that they work.
        content = slash_commands.handle_slash_command("/portal-help", {}, scope_label="s")["content"]
        groups = {block.split("\n", 1)[0]: block for block in content.split("\n\n")}
        self.assertIn("全局搜索", groups["通用快捷键："])
        chat = groups["Chat 快捷键："]
        for key in ("`Ctrl/⌘ + Enter`", "`Ctrl/⌘ + Z`", "输入 `/`", "`@`", "`1`–`9`", "`Shift + F10`", "`Alt + ↑`"):
            self.assertIn(key, chat)
        # sample_admin 10-16: the help leaves out the selection's add-to-context action.
        self.assertNotIn("添加到上下文", content)
        canvas = groups["Lineage 画布快捷键（先点击一下画布空白处）："]
        # Keyboard steps say which key reaches the item, not "focus" (sample_admin 10-16).
        self.assertNotIn("焦点", content)
        for key in ("`+`", "`-`", "`0`", "`F`", "`Shift`"):
            self.assertIn(key, canvas)
        # sample_admin 10-16: the canvas group leaves out Esc and the quick Fork menu.
        for key in ("`Esc`", "Fork 方式菜单"):
            self.assertNotIn(key, canvas)

    def test_agent_harness_defaults_and_deep_merge(self) -> None:
        """AgentHarness 默认 auto 模式并按特性给出默认开关，部分更新按深度合并"""
        defaults = resident_runtime_settings.normalize_settings({})["agent_harness"]
        self.assertEqual(defaults["mode"], "auto")
        self.assertNotIn("milestone_rollover", defaults["features"])
        for feature_id in (
            "artifact_search_and_slice",
            "early_context_compaction",
            "hash_aware_read_dedup",
            "inline_media_history",
            "native_tool_loop_governor",
            "tool_output_offloading",
            "unified_resource_recall",
        ):
            with self.subTest(default_enabled_feature=feature_id):
                self.assertTrue(
                    defaults["features"][feature_id]["enabled"]
                )
        for feature_id in (
            "tool_call_granularity",
        ):
            with self.subTest(default_disabled_feature=feature_id):
                self.assertFalse(
                    defaults["features"][feature_id]["enabled"]
                )
        self.assertEqual(
            defaults["features"]["native_tool_loop_governor"],
            {
                "enabled": True,
                "checkpoint_interval": 80,
                "max_checkpoints": 3,
                "duplicate_read_threshold": 8,
                "large_output_bytes": 64 * 1024,
                "timing": "before_turn",
            },
        )
        partial = resident_runtime_settings.normalize_settings({
            "agent_harness": {
                "features": {
                    "inline_media_history": {"keep_recent_images": 3},
                    "tool_call_granularity": {},
                    "tool_output_offloading": {"enabled": False},
                },
            },
        })["agent_harness"]
        self.assertTrue(
            partial["features"]["inline_media_history"]["enabled"]
        )
        self.assertFalse(
            partial["features"]["tool_call_granularity"]["enabled"]
        )
        self.assertFalse(
            partial["features"]["tool_output_offloading"]["enabled"]
        )
        self.assertEqual(
            partial["features"]["inline_media_history"][
                "keep_recent_images"
            ],
            3,
        )
        enabled = resident_runtime_settings.merge_settings(
            {},
            {
                "agent_harness": {
                    "features": {
                        "inline_media_history": {"enabled": True},
                        "tool_output_offloading": {"enabled": False},
                    },
                }
            },
        )

        updated = resident_runtime_settings.merge_settings(
            enabled,
            {
                "agent_harness": {
                    "features": {
                        "inline_media_history": {"enabled": False},
                    },
                },
            },
        )

        harness = updated["agent_harness"]
        self.assertNotIn("enabled", harness)
        self.assertEqual(harness["mode"], "auto")
        self.assertFalse(
            harness["features"]["inline_media_history"]["enabled"]
        )
        self.assertEqual(
            harness["features"]["inline_media_history"][
                "max_inline_media_bytes"
            ],
            512 * 1024,
        )
        self.assertEqual(
            harness["features"]["inline_media_history"][
                "trigger_inline_media_bytes"
            ],
            512 * 1024,
        )
        self.assertEqual(
            harness["features"]["inline_media_history"][
                "keep_recent_images"
            ],
            1,
        )
        self.assertEqual(
            harness["features"]["inline_media_history"]["timing"],
            "after_tool_batch",
        )
        self.assertFalse(
            harness["features"]["tool_call_granularity"]["enabled"]
        )
        self.assertEqual(
            harness["features"]["tool_call_granularity"]["timing"],
            "before_turn",
        )
        self.assertEqual(
            harness["features"]["tool_output_offloading"],
            {
                "enabled": False,
                "offload_trigger_bytes": 64 * 1024,
                "preview_chars": 2_000,
                "max_outputs_per_turn": 8,
                "timing": "after_tool_batch",
            },
        )
        self.assertEqual(
            harness["features"]["artifact_search_and_slice"],
            {"enabled": True, "timing": "before_turn"},
        )
        self.assertEqual(
            harness["features"]["hash_aware_read_dedup"],
            {
                "enabled": True,
                "min_output_tokens": 256,
                "timing": "after_tool_batch",
            },
        )
        self.assertEqual(
            harness["features"]["unified_resource_recall"],
            {
                "enabled": True,
                "max_resource_bytes": 4 * 1024 * 1024,
                "timing": "before_turn",
            },
        )
        self.assertEqual(
            harness["features"]["early_context_compaction"],
            {
                "enabled": True,
            },
        )


    def test_agent_harness_omits_constant_compaction_setting(self) -> None:
        """AgentHarness 设置去掉提前压缩特性中固定不变的 timing 字段

        :module: platform.core"""
        harness = normalize_agent_harness_settings({
            "features": {
                "early_context_compaction": {
                    "enabled": True,
                    "timing": "before_turn",
                },
            },
        })

        self.assertEqual(
            harness["features"]["early_context_compaction"],
            {
                "enabled": True,
            },
        )

    def test_agent_harness_preserves_explicit_inline_media_trigger(self) -> None:
        """AgentHarness 保留显式设置的内联媒体触发阈值"""
        harness = normalize_agent_harness_settings({
            "features": {
                "inline_media_history": {
                    "enabled": True,
                    "max_inline_media_bytes": 512 * 1024,
                    "trigger_inline_media_bytes": 1024 * 1024,
                },
            },
        })

        self.assertEqual(
            harness["features"]["inline_media_history"][
                "trigger_inline_media_bytes"
            ],
            1024 * 1024,
        )

    def test_agent_harness_rejects_unknown_envelope_fields(self) -> None:
        """AgentHarness 设置含未知顶层字段时被拒绝"""
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            normalize_agent_harness_settings({
                "unexpected": False,
                "features": {},
            })

    def test_agent_harness_preserves_explicit_manual_mode(self) -> None:
        """AgentHarness 保留显式的 manual 模式和关闭的特性"""
        harness = normalize_agent_harness_settings({
            "mode": "manual",
            "features": {
                "inline_media_history": {"enabled": False},
            },
        })

        self.assertEqual(harness["mode"], "manual")
        self.assertFalse(
            harness["features"]["inline_media_history"]["enabled"]
        )


if __name__ == "__main__":
    unittest.main()
