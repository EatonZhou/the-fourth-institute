"""A Resident packet starts a new path and carries its explicit business input."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from management_portal.society_service import lineage_domain
from tests.cases import society_service_test_support as lineage_test_support
from tests.cases.test_lineage_program_runtime_integration import (  # noqa: F401 - fixture
    ACTOR,
    runtime_store,
)

ROUTER = "resident_aaaaaaaaaaaa"
MEMBER = "resident_bbbbbbbbbbbb"
ASK = '''asked=$(DeliverMessage resident_id="${arguments.router}" message="请选择成员：${event.payload.prompt}" business_ref="family-route/demo")
output request_message_id="${asked.message_id}"'''
RELAY = '''DeliverMessage resident_id="${packet.message.data.target}" message="${packet.message.data.prompt}"
output target="${packet.message.data.target}" confidence="${packet.message.data.confidence}" reason="${packet.message.text}"'''


@pytest.mark.parametrize("closing_tag", ["</packet>", ""], ids=["closed", "missing-closing-tag"])
def test_resident_packet_starts_a_new_path_with_explicit_payload(
    runtime_store: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811 - fixture
    closing_tag: str,
) -> None:
    """Resident 回复的 packet 开启新路径并显式携带业务输入，缺闭合标签时同样生效"""
    del runtime_store
    sent: list[tuple[str, str, str]] = []

    def send(node: dict, prompt: str, _actor: str) -> dict:
        job_id = f"job_{len(sent) + 1}"
        sent.append((str(node["id"]), job_id, prompt))
        return {"message_id": job_id}

    choice = {'key': 'route', 'message': {'text': '负责登录', 'data': {'target': MEMBER, 'confidence': 90, 'prompt': '登录页 500 了'}}}
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_test_support, "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
        lambda _resident_id, job_id, _actor: {
            "job_id": job_id, "reply": "<packet>" + json.dumps(choice, ensure_ascii=False) + closing_tag,
        },
    )
    created = lineage_test_support.create_lineage_with_resident_members({
        "title": "Router chain",
        "nodes": [{"id": ROUTER, "title": "Router"}, {"id": MEMBER, "title": "Member"}],
        "program": {
            "components": [
                {"id": "event_source_inbox", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
                {"id": "effect_ask", "kind": "effect", "config": {"script": ASK, "endpoints": []}},
                {"id": "effect_relay", "kind": "effect", "config": {"script": RELAY, "endpoints": []}},
            ],
            "wires": [
                {'id': 'wire_route', 'kind': 'route', 'source': 'event_source_inbox', 'target': 'effect_ask', 'config': {'invoke_profiles': {'effect': {'arguments': {'router': {'value': ROUTER}}}}}},
                # The Packet's prompt is the Router's reason, not text for the member.
                {'id': 'wire_relay', 'kind': 'route', 'source': ROUTER, 'target': 'effect_relay', 'config': {'invoke_profiles': {'effect': {'arguments': {'prompt': {'value': ''}}}}}},
            ],
        },
    }, ACTOR)
    lineage_id = created["lineage_id"]
    event = lineage_domain.lineage_primitives.make_event(
        "manual.triggered", payload={"prompt": "登录页 500 了"}, correlation="facade-message",
    )

    asked = lineage_domain.dispatch_lineage_program_event(
        lineage_id, "event_source_inbox", event, ACTOR, cursor="manual:facade-message",
    )
    assert asked["results"][0]["status"] == "executed"
    assert sent == [(ROUTER, "job_1", "请选择成员：登录页 500 了")]

    lineage_test_support.record_terminal_execution(ROUTER, "job_1", ACTOR)
    completed = lineage_domain.handle_resident_message_completion(ROUTER, ACTOR, "job_1", lineage_id=lineage_id)

    # Business context is explicitly carried by the packet.
    assert sent[1][0] == MEMBER and sent[1][2] == "登录页 500 了"
    packet_results = completed["agent_results"][0]["packet_results"]
    relayed = packet_results[0]["deliveries"][0]
    assert relayed["status"] == "executed"
    assert relayed["typed_outputs"]["target"] == MEMBER and relayed["typed_outputs"]["reason"] == "负责登录"


@pytest.mark.parametrize("keys", [("quote", "check", "noise"), ("quote", "noise"), ("noise",)])
@pytest.mark.parametrize("via_loop", [False, True], ids=["direct", "loop"])
def test_resident_output_joins_complete_selector_branches(runtime_store, monkeypatch, keys, via_loop):  # noqa: F811 - fixture
    """Resident 一次输出经 selector 分流后在 reducer 汇合，空分支也算完成

    One output occurrence joins both branches, including empty branch completion."""
    from management_portal.society_service import lineage_operator_runtime

    sent = []

    def send(node, prompt, _actor):
        message_id = f"job_{len(sent) + 1}"
        sent.append((node["id"], message_id, prompt))
        return {"message_id": message_id}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(lineage_test_support, "RESIDENT_MESSAGE_EVIDENCE_RESOLVER", lambda _node, message_id, _actor: {
        "job_id": message_id,
        "reply": "\n".join('<packet>' + json.dumps({"key": key, "message": {"text": key}}) + '</packet>' for key in keys),
    })
    components = [
        {"id": "event_source_start", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
        {"id": "selector_split", "kind": "selector", "config": {"operator_implementation": "rules", "branches": [
            {"id": key, "match": {"conditions": [{"from": "packet.key", "operator": "equals", "value": key}]}}
            for key in ("quote", "check")
        ]}},
        {"id": "reducer_join", "kind": "reducer", "config": {"operator_implementation": "rules"}},
    ]
    wires = [
        {"id": "wire_start", "kind": "route", "source": "event_source_start", "target": ROUTER},
        {"id": "wire_output", "kind": "route", "source": ROUTER, "target": "selector_split"},
        *({"id": "wire_" + key, "kind": "route", "source": "selector_split", "source_port": "branch:" + key, "target": "reducer_join"} for key in ("quote", "check")),
        {"id": "wire_consumer", "kind": "route", "source": "reducer_join", "target": MEMBER, "config": {"include_envelope": False}},
    ]
    if via_loop:
        # Each Loop round is a separate occurrence. Route the entire source
        # message through it so both selector branches belong to one round.
        monkeypatch.setattr(lineage_test_support, "RESIDENT_MESSAGE_EVIDENCE_RESOLVER", lambda _node, message_id, _actor: {
            "job_id": message_id, "reply": " ".join(keys),
        })
        for branch in components[1]["config"]["branches"]:
            branch["match"]["conditions"][0].update({"from": "packet.message.text", "operator": "contains"})
        components.append({"id": "loop_round", "kind": "loop", "config": {"max_rounds": 2}})
        wires[1]["target"] = "loop_round"
        wires.append({"id": "wire_round", "kind": "route", "source": "loop_round", "source_port": "continue", "target": "selector_split"})
    created = lineage_test_support.create_lineage_with_resident_members({
        "title": "Resident branch join", "nodes": [{"id": ROUTER}, {"id": MEMBER}],
        "program": {"components": components, "wires": wires},
    }, ACTOR)
    lineage_id = created["lineage_id"]
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": "event_source_start", "prompt": "Start"}, ACTOR)
    # The start occurrence ends at the Resident; its future output has its own occurrence.
    assert lineage_operator_runtime.read_operator_runtime(lineage_id, "reducer_join")["inbox"] == {}
    lineage_test_support.record_terminal_execution(ROUTER, "job_1", ACTOR)
    lineage_domain.handle_resident_message_completion(ROUTER, ACTOR, "job_1", lineage_id=lineage_id)
    expected = [key for key in ("check", "quote") if key in keys]
    assert [item[0] for item in sent[1:]] == ([MEMBER] if expected else [])
    if expected:
        assert sorted(sent[1][2].split("\n\n")) == ([" ".join(keys)] * len(expected) if via_loop else expected)
    for operator_id in ("selector_split", "reducer_join"):
        assert lineage_operator_runtime.read_operator_runtime(lineage_id, operator_id)["inbox"] == {}
