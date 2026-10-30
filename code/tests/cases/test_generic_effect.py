"""Generic Effects: a script of instruction calls and named endpoints (decision_4adcc1ff2f2c).

:module: lineage.hatg
"""

from __future__ import annotations

import pytest

from management_portal.society_service import (
    lineage_primitives, lineage_program_components, lineage_program_diagnostics,
)
from management_portal.society_service.lineage_program_effect_execution import effect_outputs
from management_portal.society_service.lineage_program_effect_rules import (
    effect_call_key,
    effect_idempotency_key,
    effect_script_inputs,
    prepare_call_arguments,
)


LINEAGE = "lineage_aaaaaaaaaaaa"
TARGET = "resident_bbbbbbbbbbbb"


def invocation(invocation_id="invocation_1", event_id="event_1", **arguments):
    event = lineage_primitives.LineageEvent(
        id=event_id, type="manual.triggered", payload={"prompt": "from the event", "run_id": "run_aaaaaaaaaaaa"},
    )
    return lineage_primitives.EffectInvocation(id=invocation_id, activation=event, arguments=arguments)


def test_deliver_message_combines_the_fixed_prompt_with_the_activation_input():
    """DeliverMessage 合并固定提示词与激活输入

    :module: platform.core"""
    prepared = prepare_call_arguments(
        "DeliverMessage", {"resident_id": TARGET, "message": "Fixed task."}, invocation(prompt="Todays input."),
    )
    assert prepared["resident_id"] == TARGET
    assert prepared["message"]["content_parts"][0] == {"type": "text", "text": "Fixed task.\n\nTodays input."}
    assert prepared["message"]["role"] == "user"
    other = prepare_call_arguments("CreateNotification", {"message": "Fixed task."}, invocation(prompt="x"))
    assert other == {"message": "Fixed task."}


def test_script_inputs_expose_arguments_envelope_and_endpoints():
    """Effect 脚本输入提供参数、事件信封与已绑定端点"""
    definition = {"endpoints": {"target": TARGET, "unbound": ""}}
    inputs = effect_script_inputs(definition, invocation(note_id="note-1"))
    assert inputs["arguments"] == {"note_id": "note-1"}
    assert inputs["event"]["payload"]["prompt"] == "from the event"
    assert inputs["event"]["outputs"] == {}
    assert inputs["endpoint"] == {"target": TARGET}
    hidden = lineage_primitives.EffectInvocation(
        id="invocation_1", activation=invocation().activation, arguments={}, include_envelope=False,
    )
    assert effect_script_inputs(definition, hidden)["event"] == {}


def test_a_message_that_already_expands_the_input_prompt_gets_it_once():
    """已展开的激活输入在 DeliverMessage 中只出现一次"""
    # A TAU orchestration run authored concat(fixed, event.payload.prompt); as a
    # script the message expands ${event.payload.prompt}, and DeliverMessage
    # must not append the same input again.
    prepared = prepare_call_arguments(
        "DeliverMessage", {"resident_id": TARGET, "message": "Fixed task.\n\nfrom the event"},
        invocation(prompt="from the event"),
    )
    text = prepared["message"]["content_parts"][0]["text"]
    assert text == "Fixed task.\n\nfrom the event"
    assert text.count("from the event") == 1


def test_each_call_has_a_stable_key_from_the_activation_and_its_position():
    """Effect 调用按激活标识和脚本位置生成稳定幂等键"""
    first = effect_idempotency_key(LINEAGE, "effect_a", invocation())
    assert first == effect_idempotency_key(LINEAGE, "effect_a", invocation())
    assert len({
        first,
        effect_idempotency_key(LINEAGE, "effect_a", invocation(event_id="event_2")),
        effect_idempotency_key(LINEAGE, "effect_b", invocation()),
    }) == 3
    # The first call keeps the activation key: a one-line script uses the key
    # a single-instruction Effect used.
    assert effect_call_key(first, "c1") == first
    keys = {effect_call_key(first, position) for position in ("c1", "c2", "c2.0", "c2.1", "c1.0")}
    assert len(keys) == 5


def test_outputs_are_the_called_instructions_declared_result_fields():
    """Effect 输出仅保留被调用指令声明的结果字段

    :module: platform.core"""
    outputs = effect_outputs("DeliverMessage", {
        "resident_id": TARGET, "message_id": "msg_1", "input_state": {"state": "queued"}, "internal": "dropped",
    })
    assert outputs == {"resident_id": TARGET, "message_id": "msg_1", "input_state": {"state": "queued"}}


def test_effect_configuration_accepts_only_script_and_endpoints():
    """Effect 配置仅接受脚本与端点"""
    normalized = lineage_program_components.normalize_effect_config({
        "script": 'ForkResident source_resident_id="${endpoint.source}"\r\n',
        "endpoints": ["source"],
    })
    assert normalized == {"script": 'ForkResident source_resident_id="${endpoint.source}"\n', "endpoints": ["source"]}
    assert lineage_program_components.normalize_effect_config({}) == {"script": "", "endpoints": []}
    with pytest.raises(ValueError, match="只包含"):
        lineage_program_components.normalize_effect_config({"instruction": "ForkResident", "arguments": {}, "endpoints": []})
    with pytest.raises(ValueError, match="必须是文本"):
        lineage_program_components.normalize_effect_config({"script": ["DeliverMessage"]})
    with pytest.raises(ValueError, match="最长"):
        lineage_program_components.normalize_effect_config({"script": "#" * 20_001})


def diagnostics_for(config, *, connect=()):
    program = {
        "components": [
            {"id": "event_source_aaaaaaaaaaaa", "kind": "event_source", "title": "Go", "config": {"event_type": "manual.triggered"}},
            {"id": "effect_aaaaaaaaaaaa", "kind": "effect", "title": "X", "config": config},
        ],
        "wires": [
            {'id': 'wire_aaaaaaaaaaaa', 'kind': 'route', 'source': 'event_source_aaaaaaaaaaaa', 'source_port': 'output', 'target': 'effect_aaaaaaaaaaaa', 'target_port': 'invoke'},
            *(
                {"id": f"wire_{index:012x}", "kind": "target", "source": "effect_aaaaaaaaaaaa", "source_port": port,
                 "target": TARGET}
                for index, port in enumerate(connect, start=1)
            ),
        ],
    }
    nodes = [{"id": TARGET, "kind": "agent", "title": "T"}] if connect else []
    return {item["code"] for item in lineage_program_diagnostics.program_diagnostics(program, nodes)}


def test_diagnostics_check_the_script_its_calls_and_endpoints():
    """Effect 诊断检查脚本语法、指令参数和端点"""
    assert "effect.missing_script" in diagnostics_for({"script": "", "endpoints": []})
    assert "effect.script_syntax" in diagnostics_for({"script": 'DeliverMessage message="open', "endpoints": []})
    assert "effect.script_unsupported" in diagnostics_for({"script": "GetLineage | GetTriggerPath", "endpoints": []})
    assert "effect.unknown_instruction" in diagnostics_for({"script": "NoSuchThing", "endpoints": []})
    assert "effect.streaming_instruction" in diagnostics_for({"script": "WatchTriggerPath lineage_id=x trigger_path_id=y", "endpoints": []})
    assert "effect.missing_argument" in diagnostics_for({"script": "ForkResident", "endpoints": []})
    assert "effect.unknown_argument" in diagnostics_for({"script": "ForkResident bogus=1", "endpoints": []})
    assert "effect.undefined_variable" in diagnostics_for({"script": 'ForkResident source_resident_id="$who"', "endpoints": []})
    assert "effect.unknown_endpoint" in diagnostics_for({
        "script": 'ForkResident source_resident_id="${endpoint.missing}"', "endpoints": [],
    })
    assert "effect.missing_target" in diagnostics_for({
        "script": 'ForkResident source_resident_id="${endpoint.source}"', "endpoints": ["source"],
    })
    # DeliverMessage may leave message unset: the runtime composes it from the input.
    assert not {code for code in diagnostics_for({
        "script": 'DeliverMessage resident_id="${endpoint.target}"', "endpoints": ["target"],
    }, connect=["target"]) if code.startswith("effect.")}


def test_effect_outputs_feed_downstream_argument_checks():
    """Effect 输出字段供下游参数检查使用"""
    effect = {"script": 'x=$(ForkResident source_resident_id="a")\noutput child="${x.resident_id}"', "endpoints": {}, "endpoint_names": []}
    assert lineage_program_diagnostics.effect_script_analysis(effect).output_fields == {"child"}
    single = {"script": 'ForkResident source_resident_id="a"', "endpoints": {}, "endpoint_names": []}
    assert "resident_id" in lineage_program_diagnostics.effect_script_analysis(single).output_fields


def payload_diagnostics(event_type, reference):
    watched = "resident_aaaaaaaaaaab"
    program = {
        "components": [
            {"id": "event_source_aaaaaaaaaaaa", "kind": "event_source", "title": "Done", "config": {"event_type": event_type}},
            {"id": "effect_aaaaaaaaaaaa", "kind": "effect", "title": "Hand over", "config": {
                "script": f'DeliverMessage resident_id="{TARGET}" message="继续：${{{reference}}}"', "endpoints": [],
            }},
        ],
        "wires": [
            *([{"id": "wire_watchaaaaaaa", "kind": "watch", "source": "event_source_aaaaaaaaaaaa", "target": watched}]
              if event_type.startswith("resident.") else []),
            {'id': 'wire_aaaaaaaaaaaa', 'kind': 'route', 'source': 'event_source_aaaaaaaaaaaa', 'source_port': 'output', 'target': 'effect_aaaaaaaaaaaa', 'target_port': 'invoke'},
        ],
    }
    nodes = [{"id": watched, "title": "A"}, {"id": TARGET, "title": "B"}]
    return [item for item in lineage_program_diagnostics.program_diagnostics(program, nodes)
            if item["code"] == "effect.unavailable_event_payload"]


def test_an_effect_reading_a_payload_field_its_event_never_carries_is_warned():
    """Effect 参数读取上游 Event 不提供的 payload 字段时诊断给出警告"""
    # A TAU run read event.payload.prompt after resident.work_finished; the
    # second stage failed at delivery and the diagnostics said nothing.
    [warning] = payload_diagnostics("resident.work_finished", "event.payload.prompt")
    assert warning["level"] == "warning" and "prompt" in warning["message"] and "message_id" in warning["message"]
    assert payload_diagnostics("resident.work_finished", "event.payload.message_id") == []
    assert payload_diagnostics("resident.work_finished", "event.payload.trigger_path_id") == []
    # An Event whose payload is not declared (a manual trigger carries the user's prompt) is not judged.
    assert payload_diagnostics("manual.triggered", "event.payload.prompt") == []


def test_a_new_effect_from_the_primitive_card_has_the_config_create_component_accepts():
    """原语卡创建的新 Effect 配置与 CreateComponent 缺省一致

    lineage_program.js 中 Effect 的 default_config 只含 script 与 endpoints，并与服务端 DEFAULT_EFFECT_CONFIG 相同，可通过 normalize_effect_config。
    """
    import re
    from pathlib import Path

    from management_portal.society_service import lineage_program_authoring

    default = lineage_program_authoring.DEFAULT_EFFECT_CONFIG
    assert lineage_program_components.normalize_effect_config(default) == default
    source = (Path(__file__).resolve().parents[2] / "management_portal/static/lineage_program.js").read_text()
    block = re.search(r"\n      effect: \{.*?default_config: \{(.*?)\n        \},", source, re.S).group(1)
    assert f"script: '{default['script']}'" in block
    assert 'endpoints: ["target"]' in block
    assert set(re.findall(r"^\s*(\w+):", block, re.M)) == {"script", "endpoints"}
