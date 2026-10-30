from __future__ import annotations

import re

import pytest

from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.harness_native_loop import NativeToolLoopRuntime
from management_portal.society_service.templates.builtins import definitions

FORBIDDEN_GUIDANCE = re.compile(
    r"用户.{0,8}(?:中断|打断|暂停)|(?:中断|打断|暂停).{0,8}用户|"
    r"下一轮|下轮|下个回合|下一条消息|等待用户|交接|压缩后|"
    r"(?:next|another)\s+(?:turn|execution|LLM|model)|"
    r"user.{0,30}(?:interrupt|abort)|(?:interrupt|abort).{0,30}(?:by\s+)?user|handoff",
    re.IGNORECASE,
)


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


def _texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


@pytest.mark.parametrize(
    "definition", definitions(), ids=lambda item: item["template_id"]
)
def test_builtin_template_prompts_have_no_execution_deferral(definition: dict) -> None:
    """内置模板的提示词不包含推迟到下一轮或等待用户的措辞"""
    for text in _texts(definition):
        assert not FORBIDDEN_GUIDANCE.search(
            text.replace("平台会在下一轮执行前迁移到安全账号", "平台执行前检查账号")
        ), text
