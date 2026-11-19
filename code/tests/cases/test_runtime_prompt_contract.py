from __future__ import annotations

import re

import pytest

from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.harness_native_loop import NativeToolLoopRuntime
from management_portal.society_service.lineage_effect_script import (
    deliver_message_script,
    deliver_script_parts,
)
from management_portal.society_service.templates.builtins import definitions

FORBIDDEN_GUIDANCE = re.compile(
    r"用户.{0,8}(?:中断|打断|暂停)|(?:中断|打断|暂停).{0,8}用户|"
    r"下一轮|下轮|下个回合|下一条消息|等待用户|交接|压缩后|"
    r"(?:next|another)\s+(?:turn|execution|LLM|model)|"
    r"user.{0,30}(?:interrupt|abort)|(?:interrupt|abort).{0,30}(?:by\s+)?user|handoff",
    re.IGNORECASE,
)
EXECUTION_DEFERRAL = re.compile(
    r"(?<!不)(?<!无需)(?<!不要)等待用户|"
    r"(?:留(?:到|给)|推迟到|等到|等|等着|放到)(?:下一轮|下轮|下个回合|下一条消息|压缩后)|"
    r"(?:下一轮|下轮|下个回合|下一条消息|压缩后)(?:再|才)(?:继续|执行|处理|完成|做)|"
    r"(?:将|把)(?:当前)?(?:任务|工作)(?:交接|交还|交给|转交)(?:给)?(?:用户|下一轮|下个模型)|"
    r"(?<!do not )(?<!don't )(?<!without )\bwait for (?:the )?user\b|"
    r"\b(?:defer|postpone|leave|hand\s*off)\b[^.!?;]{0,60}"
    r"\b(?:to|until|for)\s+(?:(?:the )?(?:next|another)\s+(?:turn|execution|LLM|model)|(?:the )?user)\b|"
    r"\b(?:next|another)\s+(?:turn|execution|LLM|model)\b[^.!?;]{0,20}\b(?:will|should|can) (?:finish|continue|execute)\b",
    re.IGNORECASE,
)
PARAMETER = re.compile(r"\{\{param\.([\w]+)\}\}")


def test_generated_runtime_control_describes_current_task(
    subtests, isolated_portal_case
):
    """各类运行时纠偏提示都声明原目标不变，且不提下一轮、交接或用户中断"""
    for reason in [
        "duplicate_read_pressure",
        "large_output_pressure",
        "native_inline_media_pressure",
        "provider_latency_pressure",
        "failure_loop_pressure",
        "context_pressure",
        "tool_call_checkpoint",
        "auto_confirmed_fragmentation",
        "auto_serial_fragmentation",
        "fragmented_read_sequence",
        "discovery_fragmentation",
        "read_checkpoint",
    ]:
        with subtests.test(reason=reason), isolated_portal_case():
            runtime = NativeToolLoopRuntime(
                checkpoint_interval=80,
                max_checkpoints=4,
                duplicate_read_threshold=2,
                large_output_bytes=65536,
                granularity_strategy="auto",
                fragmented_read_batch_threshold=3,
                discovery_target_threshold=4,
                confirm_singleton_reads=1,
                max_granularity_steers=2,
                emit=lambda _event: None,
            )
            if reason.endswith("pressure") or reason == "tool_call_checkpoint":
                runtime.controls.queue_safety(reason)
            else:
                runtime.granularity.request_steer(reason, discovery_targets=5)
            control = runtime.next_control()
            assert control is not None
            prompt = "\n".join(
                item["text"] for item in control["input"] if item["type"] == "text"
            )
            assert "原有目标与完成条件保持不变" in prompt
            assert not FORBIDDEN_GUIDANCE.search(prompt)
            assert control.get("action", "steer") == "steer"
            assert control["metadata"]["kind"] != "provider_transition"


def test_default_provider_prompts_describe_current_task_contracts(
    subtests, isolated_portal_case
):
    """codex 默认的压缩提示词不提下一轮、交接或用户中断"""
    with (
        subtests.test(contract="test_default_provider_prompts_describe_current_task"),
        isolated_portal_case(),
    ):
        prompt = CodexBackend._thread_config({})["compact_prompt"]
        assert prompt
        assert not FORBIDDEN_GUIDANCE.search(prompt)


def _prompt_literal(expression):
    if isinstance(expression, str):
        return expression
    if "concat" in expression:
        return expression.get("separator", "").join(map(_prompt_literal, expression["concat"]))
    return expression.get("value", expression.get("default", ""))


def _template_guidance(definition):
    body = definition["body"]
    defaults = {item["name"]: item.get("default", "") for item in definition["parameters"]}
    expressions = [body[key] for key in ("guide", "prompt", "message", "task") if key in body]
    for wire in body.get("wires", []):
        for profile in wire.get("config", {}).get("invoke_profiles", {}).values():
            if "prompt" in profile.get("arguments", {}):
                expressions.append(profile["arguments"]["prompt"])
    for component in body.get("components", []):
        if component["kind"] == "effect":
            delivery = deliver_script_parts(component["config"]["script"])
            if delivery is not None:
                expressions.append(delivery[1])
    for expression in expressions:
        yield PARAMETER.sub(lambda match: defaults[match[1]], _prompt_literal(expression))


@pytest.mark.parametrize(
    "definition", definitions(), ids=lambda item: item["template_id"]
)
def test_builtin_template_prompts_have_no_execution_deferral(definition: dict) -> None:
    """内置模板的提示词不包含推迟到下一轮或等待用户的措辞"""
    for text in _template_guidance(definition):
        assert not EXECUTION_DEFERRAL.search(text), text


@pytest.mark.parametrize(
    ("text", "defers"),
    [
        ("涉及组织职责交接时先完成交接。", False),
        ("Complete the team handoff and continue the current work.", False),
        ("平台会在下一轮执行前迁移到安全账号。", False),
        ("不等待用户选择，继续原任务。", False),
        ("把当前任务交接给用户。", True),
        ("把剩余工作留到下一轮。", True),
        ("下一条消息再继续处理。", True),
        ("等待用户回复再执行。", True),
        ("Hand off the current task to the next model.", True),
        ("Wait for the user before continuing.", True),
    ],
)
def test_template_guidance_distinguishes_org_handoff_from_deferral(text, defers):
    assert bool(EXECUTION_DEFERRAL.search(text)) is defers


def test_template_guidance_scans_only_delivered_text():
    definition = {
        "title": "等待用户",
        "parameters": [
            {"name": "task", "default": "下一轮再继续。"},
            {"name": "calendar", "default": "等待用户"},
        ],
        "body": {
            "guide": "先做组织交接。",
            "components": [
                {"kind": "effect", "config": {"script": deliver_message_script("仍执行原任务。")}},
            ],
            "wires": [{"config": {"invoke_profiles": {"agent": {"arguments": {
                "prompt": {"concat": [{"value": "继续。"}, {"from": "packet.text", "default": "{{param.task}}"}]},
            }}}}}],
        },
    }
    assert list(_template_guidance(definition)) == [
        "先做组织交接。", "继续。下一轮再继续。", "仍执行原任务。",
    ]
