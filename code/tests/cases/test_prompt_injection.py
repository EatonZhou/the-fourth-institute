"""A Resident chooses which Portal-added input its turns carry, and when.

Presets and per-category timings (decision_ef1d6c925955): ExecutionService
decides per turn which parts are due, the Provider decides where they go.

:module: lineage.service
:importance: P1
"""
from __future__ import annotations

from pathlib import Path

import pytest

from management_portal import prompt_injection
from management_portal.execution.backend_contracts import BackendResult
from management_portal.execution.contracts import (
    ExecutionRequest,
    InjectionPart,
    SessionOptions,
    SystemInstructions,
)
from management_portal.execution.injection_plan import plan_injection
from management_portal.execution.runtime import ExecutionRuntime
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


def test_presets_resolve_and_single_changes_become_custom() -> None:
    """Prompt 注入预设与单类修改

    预设解析为六类时机；改单个类别记为自定义，与预设相同时记回该预设。

    :design: decision_ef1d6c925955
    """
    assert prompt_injection.resolve(None) == prompt_injection.PRESETS["standard"]
    assert prompt_injection.PRESETS["standard"]["core_rules"] == "session_start_and_after_compaction"
    assert set(prompt_injection.PRESETS["pure"].values()) == {"off"}

    changed = prompt_injection.apply_changes({"preset": "standard"}, parts={"runtime_identity": "off"})
    assert changed["preset"] == "custom"
    assert changed["parts"]["runtime_identity"] == "off"
    assert changed["parts"]["core_rules"] == "session_start_and_after_compaction"
    # Changing it back gives the preset again.
    assert prompt_injection.apply_changes(changed, parts={"runtime_identity": "every_turn"}) == {"preset": "standard"}
    # Categories change on top of a named preset when one is given.
    assert prompt_injection.apply_changes(
        {"preset": "standard"}, preset="pure", parts={"authorization_context": "every_turn"},
    )["parts"] == {**prompt_injection.PRESETS["pure"], "authorization_context": "every_turn"}
    assert prompt_injection.apply_changes(changed, preset="full") == {"preset": "full"}


@pytest.mark.parametrize("value", [
    {"preset": "loud"},
    {"preset": "custom", "parts": {"runtime_identity": "sometimes"}},
    {"preset": "custom", "parts": {"message_envelope": "session_start"}},
    {"preset": "custom", "parts": {"memory": "every_turn"}},
    {"preset": "standard", "parts": {"runtime_identity": "off"}},
    {"mode": "pure"},
])
def test_invalid_settings_are_rejected(value) -> None:
    """Prompt 注入拒绝非法设置

    非法预设、类别、时机以及预设与自定义类别混用都在保存前报错。

    :design: decision_ef1d6c925955
    """
    with pytest.raises(ValueError):
        prompt_injection.normalize(value)


def test_placement_is_the_providers() -> None:
    """Prompt 注入的位置由 Provider 决定

    Claude Code 把系统部分放在每次进程启动的系统提示，Codex 把 resident.md 放在消息前、授权上下文放在消息后。

    :design: decision_ef1d6c925955
    """
    claude = prompt_injection.placements("claude")
    codex = prompt_injection.placements("codex")
    assert "每次进程启动" in claude["core_rules"]
    assert claude["resident_md"] == claude["core_rules"]
    assert codex["resident_md"] == "本轮消息前"
    assert codex["authorization_context"] == "本轮消息后"
    view = prompt_injection.describe({"preset": "pure"}, provider="kimi")
    assert view["preset_label"] == "纯 TUI"
    assert {part["id"]: part["choice"] for part in view["parts"]} == prompt_injection.PRESETS["pure"]


PARTS = (
    InjectionPart("rules", "RULES", "session_start_and_after_compaction", "system"),
    InjectionPart("refresh", "REFRESH", "every_turn", "system"),
    InjectionPart("identity", "IDENTITY", "session_start", "prompt_prefix"),
    InjectionPart("authorization", "AUTH", "every_turn", "prompt_suffix"),
)


def test_plan_follows_each_parts_timing() -> None:
    """每个注入部分按自己的时机发送

    首轮发送全部；之后只发每轮部分；上下文压缩后再发首轮 + 压缩后部分；续跑不重发系统部分且消息部分不变。

    :module: execution.service
    :aspects: functional, behavior
    :design: decision_ef1d6c925955
    """
    first = plan_injection(PARTS, "MSG", generation=True, first_injection=True)
    assert first.attached_parts == ("rules", "refresh", "identity", "authorization")
    assert first.system_context == "RULES\n\nREFRESH"
    assert first.user_prompt == "IDENTITY\n\nMSG\n\nAUTH"

    later = plan_injection(
        PARTS, "MSG", generation=True, first_injection=False,
        injected_at="2026-10-09T10:00:00.000Z", compacted_at="2026-10-09T09:00:00Z",
    )
    assert later.attached_parts == ("refresh", "authorization")
    assert later.system_context == "REFRESH"
    assert later.session_system_context == "RULES\n\nREFRESH"
    assert later.user_prompt == "MSG\n\nAUTH"

    compacted = plan_injection(
        PARTS, "MSG", generation=True, first_injection=False,
        injected_at="2026-10-09T10:00:00.000Z", compacted_at="2026-10-09T10:05:00Z",
    )
    assert compacted.attached_parts == ("rules", "refresh", "authorization")

    resumed = plan_injection(PARTS, "MSG", generation=True, first_injection=True, system_already_attached=True)
    assert resumed.attach_system_context is False
    assert resumed.user_prompt == first.user_prompt

    nothing = plan_injection((), "MSG", generation=True, first_injection=True)
    assert (nothing.user_prompt, nothing.attached, nothing.system_context) == ("MSG", False, "")


@pytest.mark.parametrize("provider,expected", [("codex", [True, False, False, False]), ("kimi", [True, False, True, False])])
def test_runtime_sends_parts_again_after_the_context_was_compacted(tmp_path: Path, provider, expected) -> None:
    """上下文压缩后 ExecutionService 重发首轮 + 压缩后部分

    Provider 在一轮中压缩上下文后，下一轮重发首轮 + 压缩后部分，再下一轮不再重发。

    :module: execution.service
    :aspects: functional, behavior
    :design: decision_ef1d6c925955
    """
    seen = []

    class CompactingBackend:
        provider_id = provider

        def execute(self, invocation, context):
            seen.append(invocation)
            session_id = invocation.session.backend_session_id or "session-pi"
            if len(seen) == 2:
                # The Provider compacts the context during the second turn.
                from management_portal.execution.agent_data_store import utc_now
                context.record_compaction(
                    provider=provider, backend_session_id=session_id, backend_route_id="",
                    content={"replacement_history": [{"type": "message", "role": "user", "content": "summary"}]},
                    created_at=utc_now(), provider_compaction_ref="compaction-1",
                )
            return BackendResult(output={"message": "ok"}, backend_session_id=session_id)

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(agent_providers={provider: CompactingBackend()}, state_root=tmp_path)
    binding_id = "resident:resident_00000000a1a0"
    parts = (
        InjectionPart("rules", "RULES", "session_start_and_after_compaction", "system"),
        InjectionPart("identity", "IDENTITY", "every_turn", "prompt_prefix"),
        InjectionPart("once", "ONCE", "session_start", "prompt_suffix"),
    )
    for prompt in ("one", "two", "three", "four"):
        runtime.execute(ExecutionRequest.new(
            actor="sample_admin", prompt=prompt, service_type="agent_session", provider=provider, operation="turn",
            agent_id="resident_00000000a1a0",
            session=SessionOptions(mode="persistent", binding_id=binding_id),
            system_instructions=SystemInstructions(parts=parts),
        ))

    assert [item.attach_system_context for item in seen] == expected
    assert [item.user_prompt for item in seen] == [
        "IDENTITY\n\none\n\nONCE", "IDENTITY\n\ntwo", "IDENTITY\n\nthree", "IDENTITY\n\nfour",
    ]
    assert all(item.session_system_context == "RULES" for item in seen)


def _invocation(name, **arguments):
    from management_portal.hatg import catalog
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation

    return Invocation(
        declaration=catalog.instruction_catalog()[name],
        call=InstructionCall.from_payload({"instruction": name, "arguments": arguments}),
        context=InstructionContext(caller="CASE-A", entry="web"),
        instruction_id="inst_lin_promptinj",
    )


def test_isa_reads_changes_and_previews_the_prompt_injection(resident_record) -> None:  # noqa: F811
    """HATG ISA 读取、修改与预览 Prompt 注入

    GetResidentSettings 返回 prompt_injection 视图，UpdateResidentSettings 的 changes.prompt_injection 在当前设置上修改并返回新视图，
    PreviewPromptInjection 读同一份设置，预览按轮次标出发送的部分，纯 TUI 只发消息原文。

    :aspects: functional, behavior
    :design: decision_ef1d6c925955
    """
    from management_portal.hatg.contracts import InstructionError
    from management_portal.society_service.isa import residents

    resident_id = resident_record["resident_id"]

    def update(**change):
        return residents.update_resident_settings(_invocation(
            "UpdateResidentSettings", resident_id=resident_id, changes={"prompt_injection": change},
        ))

    read = residents.get_resident_settings(_invocation("GetResidentSettings", resident_id=resident_id))
    view = read["prompt_injection"]
    assert view["preset"] == "standard" and "resident_id" not in view
    assert {part["id"]: part["placement"] for part in view["parts"]}["resident_md"] == "本轮消息前"

    result = update(parts={"runtime_identity": "session_start"})
    changed = result["prompt_injection"]
    assert changed["preset"] == "custom"
    assert {part["id"]: part["choice"] for part in changed["parts"]}["runtime_identity"] == "session_start"
    assert result["settings"]["prompt_injection"]["parts"]["runtime_identity"] == "session_start"
    assert "next_turn_fields" in result
    # GetResidentSettings reads the same setting and view.
    read = residents.get_resident_settings(_invocation("GetResidentSettings", resident_id=resident_id))
    assert read["settings"]["prompt_injection"]["parts"]["runtime_identity"] == "session_start"
    assert read["prompt_injection"] == changed

    first = residents.preview_prompt_injection(_invocation("PreviewPromptInjection", resident_id=resident_id))
    later = residents.preview_prompt_injection(_invocation(
        "PreviewPromptInjection", resident_id=resident_id, turn="later",
    ))
    sent = lambda preview: {part["id"]: part["sent"] for part in preview["parts"]}  # noqa: E731
    assert sent(first)["runtime_identity"] and not sent(later)["runtime_identity"]
    assert sent(later)["authorization_context"]
    assert "<resident-runtime-identity>" in first["prompt"] and "<resident-runtime-identity>" not in later["prompt"]
    assert "<portal-system-prompt" in first["system_prompt"] and not later["system_prompt"]
    assert "消息来源：CASE-A" in later["prompt"]

    # A later parts change applies on top of the current setting, keeping the earlier change.
    choices = {part["id"]: part["choice"] for part in update(parts={"core_rules": "every_turn"})["prompt_injection"]["parts"]}
    assert choices["core_rules"] == "every_turn" and choices["runtime_identity"] == "session_start"
    later = residents.preview_prompt_injection(_invocation(
        "PreviewPromptInjection", resident_id=resident_id, turn="later",
    ))
    assert "<portal-system-prompt" in later["prompt"]
    assert later["system_prompt"] == ""

    pure = update(preset="pure")["prompt_injection"]
    assert pure["preset"] == "pure"
    bare = residents.preview_prompt_injection(_invocation("PreviewPromptInjection", resident_id=resident_id))
    assert bare["parts"] == [] and bare["system_prompt"] == ""
    assert bare["prompt"] == "（本轮消息）"

    with pytest.raises(InstructionError):
        update(parts={"runtime_identity": "sometimes"})
    with pytest.raises(InstructionError):
        update(preset="pure", extra=True)


def test_compaction_preserves_declared_system_context_and_refreshes_prompt_parts():
    """system 上下文能撑过压缩时，压缩后只把提示前缀部分重新附加到消息前

    :module: platform.core
    :importance: P2
    """
    parts = (
        InjectionPart("rules", "RULES", "session_start_and_after_compaction", "system"),
        InjectionPart("identity", "IDENTITY", "session_start_and_after_compaction", "prompt_prefix"),
    )
    plan = plan_injection(parts, "MESSAGE", generation=True, first_injection=False,
                          injected_at="2026-10-09T10:00:00Z", compacted_at="2026-10-09T11:00:00Z",
                          system_context_survives_compaction=True)
    assert plan.attached_parts == ("identity",)
    assert plan.system_context == "" and plan.session_system_context == "RULES"
    assert plan.user_prompt == "IDENTITY\n\nMESSAGE"
