"""Messages and immutable facts share routing, selection and aggregation."""
import json

import pytest

from management_portal.society_service import lineage_primitives as packets
from management_portal.society_service import lineage_operators, lineage_program
from management_portal.society_service.lineage_program_packet_routing import packet_envelopes
from tests.cases.test_lineage_generic_primitives import primitive_store  # noqa: F401
from tests.cases import society_service_test_support as support
from management_portal.society_service import lineage_domain

RESIDENT = "resident_111111111111"


@pytest.mark.parametrize("closing_tag", ["</packet>", "", "   \n"])
def test_reply_packet_decodes_complete_json_even_without_final_closing_tag(closing_tag):
    """回复末尾的 packet 只要 JSON 完整，缺少结束标签也能解析"""
    # A complete final JSON object is sufficient; the closing tag is optional at EOF.
    value = {"key": "route", "schema": "tfi.hatg-packet.v1",
             "message": {"text": "负责 Router", "data": {"target": RESIDENT, "confidence": 99, "scope": "in"}},
             "events": [], "parent_packet_ids": []}
    result = packets.parse_reply_packets("<packet>" + json.dumps(value) + closing_tag)
    assert result is not None and len(result) == 1
    assert result[0].key == "route"
    assert result[0].message == value["message"]


def test_reply_packet_json_strings_can_contain_packet_tags():
    """packet JSON 字符串中出现的 packet 标签不影响解析"""
    text = 'nested {"text":"literal </packet> and <packet>"}'
    reply = '<PACKET> ' + json.dumps({"key": "route", "message": {"text": text}}) + '</PACKET>'
    result = packets.parse_reply_packets(reply)
    assert result is not None and result[0].message["text"] == text


@pytest.mark.parametrize("reply", [
    "ordinary reply", '{"key":"route","message":{"text":"no opening tag"}}',
    '<packet>{"key":"route","message":{"data":{"target":"resident_111111111111"}',
    '<packet>{"key":"route","message":{"text":"ok"}} trailing text',
    '<packet>{"key":"route","message":{"text":"ok"}}</pack',
    '<packet>[]</packet>', '<packet>{}</packet>',
    '<packet>{"key":"a","message":{"text":"ok"}}</packet><packet>{"key":',
    '<packet>{"key":"a","message":{"text":"ok"}}<packet>{"key":"b","message":{"text":"other"}}</packet>',
    '<packet>{"key":"a","message":{"text":"ok"}}</packet><packet>{"key":"a","message":{"text":"duplicate"}}',
])
def test_reply_packet_does_not_guess_incomplete_or_ambiguous_content(reply):
    """不完整、有多余内容或键重复的回复 packet 一律不解析"""
    assert packets.parse_reply_packets(reply) is None


def test_reply_packet_limits_and_event_provenance_still_apply():
    """回复 packet 数量超过上限或自带事件时不被接受"""
    def block(index):
        return '<packet>' + json.dumps({"key": f"part{index}", "message": {"text": "ok"}})

    reply = ''.join(block(i) + '</packet>' for i in range(packets.MAX_PACKETS - 1)) + block(packets.MAX_PACKETS - 1)
    assert len(packets.parse_reply_packets(reply)) == packets.MAX_PACKETS
    assert packets.parse_reply_packets(reply + '</packet>' + block(packets.MAX_PACKETS)) is None
    forged = {"key": "route", "events": [packets.make_event("manual.triggered").to_dict()]}
    assert packets.parse_reply_packets('<packet>' + json.dumps(forged)) is None


@pytest.mark.parametrize("text,with_event", [("hello", False), (None, True), ("hello", True)])
def test_packet_content_survives_serialization_filter_and_branch(text, with_event):
    """packet 的消息和事件在序列化、过滤条件和连线转发后保持不变"""
    event = packets.make_event("manual.triggered", correlation="test-packet")
    packet = packets.make_packet("sample", text, events=[event.to_dict()] if with_event else [])
    value = packets.packet_envelope(packet, event_id=event.id, from_node_id=RESIDENT, source_wire_id="wire_source")
    condition = {"conditions": [{"from": "packet.events.0.type", "operator": "equals", "value": "manual.triggered"}]}
    assert packets.select_packets([value], condition) == ([value] if with_event else [])
    forwarded = packet_envelopes({"id": "wire_forward"}, event, "filter_test", [value])
    assert forwarded == [value]
    assert forwarded[0]["message"] == ({"text": text} if text else None)


def test_reducer_combines_messages_and_keeps_unique_event_facts_and_parents():
    """Reducer 合并多个 packet 的消息，事件去重并保留全部父 packet"""
    event = packets.make_event("manual.triggered", correlation="shared-fact")
    values = [packets.packet_envelope(packets.make_packet("part", text, data={"value": index}, events=[event.to_dict()]),
                                    event_id=event.id, from_node_id=RESIDENT, source_wire_id=f"wire_input_{index}")
              for index, text in enumerate(["first", "second"])]
    result = lineage_operators.rule_reducer_result({"reduce_strategy": "join"}, values)
    assert result.message == {"text": "first\n\nsecond", "data": {"items": [{"value": 0}, {"value": 1}]}}
    assert result.events == [event.to_dict()]
    assert result.parent_packet_ids == [value["id"] for value in values]
    pure = lineage_operators.rule_reducer_result({}, [packets.make_packet("fact", events=[event.to_dict()]).to_dict()])
    assert pure.message is None and pure.events == [event.to_dict()]


def test_packets_reject_empty_content_and_conflicting_facts():
    """packet 没有任何内容或同一事件带不同内容时被拒绝"""
    with pytest.raises(ValueError, match="至少包含"):
        packets.normalize_packet({})
    event = packets.make_event("manual.triggered", correlation="conflict").to_dict()
    with pytest.raises(ValueError, match="不同事实"):
        packets.make_packet("facts", events=[event, {**event, "payload": {"data": {"other": True}}}])


def test_route_compiles_the_same_output_port_for_resident_and_event_source():
    """Resident 和事件源发出的 route 连线编译为同一个 output 端口"""
    program = {"components": [{"id": "event_source_start", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
                              {"id": "filter_pass", "kind": "filter", "config": {}}],
               "wires": [{"id": "wire_event", "kind": "route", "source": "event_source_start", "target": "filter_pass"},
                         {"id": "wire_resident", "kind": "route", "source": RESIDENT, "target": "filter_pass"}]}
    plan = lineage_program.compile_program(program, [{"id": RESIDENT}])
    assert {wire["source_port"] for wire in plan["wires"]} == {"output"}
    assert set(plan["packet_operators"]["filter_pass"]["incoming_packet_wire_ids"]) == {"wire_event", "wire_resident"}
    event = packets.make_event("manual.triggered")
    value = packets.event_packet(event).to_dict()
    assert lineage_program.resolve_invocation_argument_mapping({"kind": {"from": "packet.events.0.type"}}, event, packet=value) == {"kind": "manual.triggered"}


@pytest.mark.parametrize("drop_all", [False, True])
def test_event_fanout_waits_for_both_packet_branches_before_reducing(primitive_store, monkeypatch, drop_all):
    """事件分叉到两个过滤分支后，Reducer 等两支都到齐才合并投递"""
    sent = []
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: sent.append(prompt) or {"message_id": "packet_joined"})
    components = [{"id": "event_source_start", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
                  *({"id": name, "kind": "filter", "config": {"selection": {"conditions": [
                      {"from": "packet.events.0.type", "operator": "equals", "value": "ignored" if drop_all else "manual.triggered"}
                  ]}}} for name in ["filter_left", "filter_right"]),
                  {"id": "reducer_join", "kind": "reducer", "config": {"operator_implementation": "rules"}}]
    pairs = [("event_source_start", "filter_left"), ("event_source_start", "filter_right"),
             ("filter_left", "reducer_join"), ("filter_right", "reducer_join"), ("reducer_join", RESIDENT)]
    wires = [{"id": f"wire_branch_{i}", "kind": "route", "source": source, "target": target, "config": {"include_envelope": False}}
             for i, (source, target) in enumerate(pairs)]
    lineage = lineage_domain.create_lineage({"title": "Packet join", "nodes": [{"id": RESIDENT, "title": "Consumer", "provider": "codex"}],
                                             "program": {"components": components, "wires": wires}}, "sample_admin")
    result = lineage_domain.emit_program_manual_event(lineage["lineage_id"], {"event_source_id": "event_source_start", "prompt": "Input"}, "sample_admin")
    assert result["matched_count"] == 2
    assert sent == ([] if drop_all else ["Input\n\nInput"]), result


def test_queued_mixed_packet_preserves_large_message_and_fact():
    """排队中的大消息 packet 保留完整提示词和事件"""
    from management_portal.society_service.lineage_program_runtime import _normalize_pending_delivery
    text = "long input " * 10000
    event = packets.make_event("manual.triggered", payload={"prompt": text})
    value = packets.packet_envelope(packets.event_packet(event), event_id=event.id,
                                    from_node_id="event_source_test", source_wire_id="wire_test")
    result = _normalize_pending_delivery({"prompt": text, "packet": value})
    assert result["packet"] == value
    assert result["prompt"] == text.strip()
