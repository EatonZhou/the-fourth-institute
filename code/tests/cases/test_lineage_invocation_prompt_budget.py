"""Long runtime input reaches the Resident whole; over-budget input is refused, never truncated."""

from copy import deepcopy
import json

import pytest

from management_portal.society_service import lineage_program_backlog
from management_portal.society_service import (
    lineage_domain,
    lineage_graph_edits,
    lineage_operators,
    lineage_primitives as primitives,
    lineage_program,
    lineage_program_packet_routing,
    lineage_program_runtime,
    lineage_reads,
)
from management_portal.society_service import lineage_program_execution
from management_portal.hatg.catalog.lineage_run import INPUT_MAX_CHARS
from tests.cases.effect_script_support import effect_config, lower_effect_call
from tests.cases import society_service_test_support as lineage_test_support
from tests.cases.isa_test_support import ACTOR, ok, publish_test_members, run
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401 - fixture


WORK_A = "resident_aaaaaaaaaaaa"
LONG_PROMPT = "BEGIN\n" + "Full input: 数据\U0001f680\n" * 6000 + "END"
# A triggered input is at most 16000 characters (decision_68215b3a825b); longer
# text reaches a Resident through runtime paths such as packets.
TRIGGER_PROMPT = "BEGIN\n" + "Full input: 数据\U0001f680\n" * 700 + "END"


def deliver_config(fixed):
    return effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}, "message": {"value": fixed}}, ["target"])


def _create(title, program):
    created = ok("CreateLineage", {"title": title, "project_id": "project_test"}, key=f"create:{title}")
    lineage = lineage_reads.get_lineage(created["lineage_id"], ACTOR)
    publish_test_members({**lineage, "nodes": [{"id": WORK_A, "title": "Worker"}]})
    lineage_graph_edits.apply_lineage_graph_patch(created["lineage_id"], {"patch": {"program": program}}, ACTOR)
    return created["lineage_id"]


def _graph():
    return _create("Runtime input budget", {
        "components": [
            {"id": "event_source_input", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
            {"id": "effect_input", "kind": "effect", "config": deliver_config("FIXED")},
        ],
        "wires": [
            {'id': 'wire_input', 'kind': 'route', 'source': 'event_source_input', 'target': 'effect_input', 'config': {'include_envelope': False, 'unavailable_policy': 'queue', 'queue_limit': 5}},
            {"id": "wire_target", "kind": "target", "source": "effect_input", "source_port": "target", "target": WORK_A},
        ],
    })


def trigger(lineage_id, prompt, key=""):
    return run("TriggerEvent", {"lineage_id": lineage_id, "event_source_id": "event_source_input", "prompt": prompt}, key=key)


@pytest.mark.parametrize("queued", [False, True])
def test_triggered_input_survives_dispatch_queue_edit_and_replay(isolated_instruction_store, monkeypatch, queued):
    """触发输入经直接投递或排队后编辑完整送达，重放不重复投递也不改定义"""
    sent = []
    busy = {"value": queued}
    availability = lineage_program_execution.program_event_route_can_consume
    monkeypatch.setattr(lineage_program_execution, "program_event_route_can_consume", lambda *args, **kwargs: (False, "resident_running") if busy["value"] else availability(*args, **kwargs))
    monkeypatch.setattr(lineage_domain, "_node_has_active_execution", lambda *args, **kwargs: False)
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: sent.append(prompt) or {"message_id": "msg_long_input"})
    lineage_id = _graph()
    before = lineage_reads.get_lineage(lineage_id, ACTOR)
    result = trigger(lineage_id, TRIGGER_PROMPT, key="long-runtime-input").raise_for_error()
    expected = TRIGGER_PROMPT
    if queued:
        assert sent == []
        pending = lineage_domain.get_pending_deliveries(lineage_id, {"wire_id": "wire_input"}, ACTOR)["deliveries"]
        assert len(pending) == 1
        stored = lineage_program_runtime.read_runtime(lineage_id)["event_wires"]["wire_input"]["pending_events"]
        assert stored[0]["event"]["payload"]["prompt"] == TRIGGER_PROMPT
        expected = TRIGGER_PROMPT + "\nEDITED"
        edit = {"lineage_id": lineage_id, "wire_id": "wire_input", "delivery_id": pending[0]["delivery_id"],
                "prompt": expected}
        ok("UpdatePendingDelivery", edit, key="edit")
        assert lineage_program_runtime.read_runtime(lineage_id)["event_wires"]["wire_input"]["pending_events"][0]["prompt_override"] == expected
        busy["value"] = False
        drained = lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=lineage_domain._program_control_authority())
        assert drained["delivered_count"] == 1, [item.get("error") or item.get("dispatch", {}).get("results") for item in drained["results"]]
    replay = trigger(lineage_id, TRIGGER_PROMPT, key="long-runtime-input")
    assert replay.replayed and replay.result["event_id"] == result["event_id"]
    assert sent == ["FIXED\n\n" + expected]
    after = lineage_reads.get_lineage(lineage_id, ACTOR)
    assert after["revision"] == before["revision"]
    assert after["program"] == before["program"]


def test_runtime_budget_is_separate_from_authored_text_and_structured_arguments():
    """运行时文本预算与静态 Effect 及普通参数预算分离

    :aspects: functional, behavior
    """
    maximum = "\U0001f680" * primitives.MAX_INVOCATION_PROMPT_CHARS
    args = primitives.normalize_invocation_arguments({"prompt": maximum, "metadata": {"key": "value"}})
    assert args["prompt"] == maximum
    event = primitives.make_event("manual.triggered", payload={"prompt": LONG_PROMPT, "arguments": args})
    assert primitives.normalize_event(json.loads(json.dumps(event.to_dict()))).payload["arguments"] == args
    with pytest.raises(ValueError, match="prompt"):
        primitives.normalize_invocation_arguments({"prompt": maximum + "x"})
    for invalid in (True, 1, [], {"from": "event.payload.prompt"}):
        with pytest.raises(ValueError, match="prompt"):
            primitives.normalize_invocation_arguments({"prompt": invalid})
    with pytest.raises(ValueError, match="bytes"):
        primitives.normalize_invocation_arguments({"prompt": "valid", "metadata": "x" * primitives.MAX_INVOCATION_ARGUMENT_BYTES})
    mapping = {"prompt": {"from": "event.payload.prompt"}}
    assert lineage_program.normalize_invocation_argument_mapping(mapping) == mapping
    assert lineage_program.resolve_invocation_argument_mapping(mapping, event)["prompt"] == LONG_PROMPT
    with pytest.raises(ValueError, match="bytes"):
        lineage_program.normalize_invocation_argument_mapping({"prompt": {"value": LONG_PROMPT}})


def test_fixed_plus_runtime_composition_preserves_exact_boundary_and_definition():
    """固定文本与运行时输入组合的精确边界和定义不可变

    :aspects: functional, behavior
    """
    fixed = "F" * primitives.MAX_PROMPT_CHARS
    prompt = "R" * (primitives.MAX_INVOCATION_PROMPT_CHARS - len(fixed) - 2)
    definition = {**deliver_config(fixed), "endpoints": {"target": WORK_A}}
    original = deepcopy(definition)
    event = primitives.make_event("manual.triggered")
    invocation = primitives.make_effect_invocation(event, arguments={"prompt": prompt})
    call = lower_effect_call("lineage_aaaaaaaaaaaa", "effect_input", definition, invocation)
    assert call["arguments"]["message"]["content_parts"][0]["text"] == fixed + "\n\n" + prompt
    assert definition == original
    assert invocation.arguments == {"prompt": prompt}
    overflow = primitives.make_effect_invocation(event, arguments={"prompt": prompt + "!"})
    with pytest.raises(ValueError, match=str(primitives.MAX_INVOCATION_PROMPT_CHARS)):
        lower_effect_call("lineage_aaaaaaaaaaaa", "effect_input", definition, overflow)
    assert definition == original


def test_an_over_long_trigger_is_refused_without_delivery_or_definition_mutation(isolated_instruction_store, monkeypatch):
    """TriggerEvent 输入超过投递上限时被拒绝，不投递也不改定义"""
    sent = []
    monkeypatch.setattr(lineage_domain, "_node_has_active_execution", lambda *args, **kwargs: False)
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", lambda *args: sent.append(args))
    lineage_id = _graph()
    before = lineage_reads.get_lineage(lineage_id, ACTOR)
    # TriggerEvent input shares the 1,000,000-character delivery budget (decision_68215b3a825b).
    result = trigger(lineage_id, "x" * (INPUT_MAX_CHARS + 1))
    assert result.status == "failed" and result.error["code"] == "instruction.invalid_arguments"
    assert sent == []
    after = lineage_reads.get_lineage(lineage_id, ACTOR)
    assert after["revision"] == before["revision"]
    assert after["program"] == before["program"]


def test_runtime_route_and_packet_envelopes_preserve_long_text_and_reject_overflow():
    """路由和 packet 信封保留长文本，超过上限时拒绝"""
    event = primitives.make_event("manual.triggered", payload={"prompt": LONG_PROMPT})
    packet = primitives.normalize_packet({'key': 'long', 'message': {'text': LONG_PROMPT}})
    assert packet.message["text"] == LONG_PROMPT
    envelope = lineage_program_packet_routing.packet_invoke_prompt(event, source_node_id=WORK_A, source_resident_id=WORK_A, routed_by_node_id="", prompt=LONG_PROMPT, include_envelope=True)
    assert LONG_PROMPT in envelope
    full = primitives.make_event("manual.triggered", payload={"prompt": "x" * primitives.MAX_INVOCATION_PROMPT_CHARS})
    with pytest.raises(ValueError, match="prompt"):
        lineage_program_packet_routing.packet_invoke_prompt(full, source_node_id=WORK_A, source_resident_id=WORK_A, routed_by_node_id="", prompt=full.payload["prompt"], include_envelope=True)


def test_input_survives_emit_event_and_second_effect_composition(isolated_instruction_store, monkeypatch):
    """长输入经 TriggerEvent 和第二个 Effect 拼接后完整送达，重放不重复投递"""
    sent = []
    monkeypatch.setattr(lineage_domain, "_node_has_active_execution", lambda *args, **kwargs: False)
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: sent.append(prompt) or {"message_id": "msg_chain_input"})
    lineage_id = _create("Long Effect chain", {
        "components": [
            {"id": "event_source_start", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
            {"id": "effect_emit", "kind": "effect", "config": {"script": "TriggerEvent", "endpoints": ["next"]}},
            {"id": "event_source_next", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
            {"id": "effect_send", "kind": "effect", "config": deliver_config("SECOND")},
        ],
        "wires": [
            {'id': 'wire_start', 'kind': 'route', 'source': 'event_source_start', 'target': 'effect_emit'},
            {'id': 'wire_next', 'kind': 'route', 'source': 'event_source_next', 'target': 'effect_send', 'config': {'include_envelope': False}},
            {"id": "wire_target", "kind": "target", "source": "effect_send", "source_port": "target", "target": WORK_A},
            {"id": "wire_emit_next", "kind": "target", "source": "effect_emit", "source_port": "next", "target": "event_source_next"},
        ],
    })
    # The emitting Effect names its own Lineage; the Event Source that continues
    # the chain is a graph target, bound through its "next" endpoint.
    ok("UpdateComponent", {"lineage_id": lineage_id, "component_id": "effect_emit", "changes": effect_config("TriggerEvent", {
        "lineage_id": {"value": lineage_id}, "event_source_id": {"from": "endpoint.next"},
        "prompt": {"concat": [{"value": "FIRST"}, {"from": "event.payload.prompt"}], "separator": "\n\n"},
    }, ["next"])}, key="emit-arguments")
    result = run("TriggerEvent", {"lineage_id": lineage_id, "event_source_id": "event_source_start", "prompt": TRIGGER_PROMPT},
                 key="long-chain")
    assert result.status == "committed", result
    assert sent == ["SECOND\n\nFIRST\n\n" + TRIGGER_PROMPT]
    run("TriggerEvent", {"lineage_id": lineage_id, "event_source_id": "event_source_start", "prompt": TRIGGER_PROMPT},
        key="long-chain")
    assert len(sent) == 1


@pytest.mark.parametrize("staged", [False, True])
def test_pending_packet_text_uses_runtime_budget_and_failure_is_atomic(isolated_instruction_store, staged):
    """待投递 packet 文本按运行时上限校验，超限时暂存或排队都不写入"""
    lineage_id = _graph()
    event = primitives.make_event("manual.triggered", payload={"prompt": LONG_PROMPT}).to_dict()
    from management_portal.society_service import trigger_paths
    event["payload"]["trigger_path_id"] = trigger_paths.begin_event(lineage_id, "event_source_input", event, cause_kind="external", cause_id=event["id"])
    invocation = {"source_kind": "packet", "prompt": LONG_PROMPT, "packet": primitives.make_packet('full', LONG_PROMPT, data={'summary': 'metadata'}).to_dict()}
    original = deepcopy(invocation)

    def persist(cursor, value):
        kwargs = {"invocation": value}
        method = lineage_program_runtime.stage_event_delivery
        if not staged:
            method = lineage_program_runtime.defer_event_for_wire
            kwargs["policy"] = "queue"
        return method(lineage_id, "event_source_input", "wire_input", cursor, event, **kwargs)

    persist("event_long", invocation)
    before = lineage_program_runtime.read_runtime(lineage_id)
    assert before["event_wires"]["wire_input"]["pending_events"][0]["invocation"] == invocation
    assert invocation == original
    for bad in (
        {**invocation, "prompt": "x" * (primitives.MAX_INVOCATION_PROMPT_CHARS + 1)},
        {**invocation, "metadata": "x" * primitives.MAX_INVOCATION_ARGUMENT_BYTES},
    ):
        with pytest.raises(ValueError):
            persist("event_bad", bad)
        assert lineage_program_runtime.read_runtime(lineage_id) == before


@pytest.mark.parametrize("kind", ["filter", "selector", "reducer"])
def test_packet_operator_prompts_retain_full_runtime_input(kind):
    """Filter、Selector 和 Reducer 的 packet 提示保留完整的运行时输入"""
    event = primitives.make_event("manual.triggered")
    kwargs = {"lineage_id": "lineage_aaaaaaaaaaaa", "event": event,
              "packets": [{'id': 'packet_long', 'key': 'full', 'message': {'text': LONG_PROMPT}}]}
    kwargs[kind + "_node"] = {"id": WORK_A, "config": {"branches": [
        {"id": "accepted", "label": "Accepted"}, {"id": "rejected", "label": "Rejected"},
    ]}}
    if kind == "reducer":
        kwargs["target_node"] = {"id": WORK_A}
    prompt = getattr(lineage_operators, kind + "_event_prompt")(**kwargs)
    assert LONG_PROMPT in prompt
