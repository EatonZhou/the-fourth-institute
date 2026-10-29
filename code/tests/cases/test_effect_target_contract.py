"""固定 Effect 目标必须通过具名端点及 target 连线公开。

:module: lineage.hatg
"""

from types import SimpleNamespace
import multiprocessing

import pytest

from management_common.principal import OperationIdentity
from management_portal.society_service import (
    lineage_instruction_context,
    lineage_primitives,
    lineage_program_effect_execution as execution,
    state_store,
)
from management_portal.society_service.lineage_program import effect_script_analysis


TARGET = "resident_bbbbbbbbbbbb"
EFFECT = "effect_aaaaaaaaaaaa"
MESSAGE = 'message:={"content_parts":[{"type":"text","text":"请核查"}]}'


def _definition(script, *, declared=(), endpoints=None):
    return {
        "effect_id": EFFECT,
        "script": script,
        "endpoint_names": list(declared),
        "endpoints": dict(endpoints or {}),
    }


def _analyze(script, *, declared=(), endpoints=None):
    result = effect_script_analysis(_definition(script, declared=declared, endpoints=endpoints))
    assert result.script is not None, result.diagnostics
    return result


def test_literal_resident_target_requires_a_declared_connected_endpoint():
    """正式分析入口拒绝现场同型的隐藏固定投递目标。"""
    result = effect_script_analysis({
        "effect_id": "effect_aaaaaaaaaaaa",
        "script": (
            'DeliverMessage resident_id="resident_bbbbbbbbbbbb" '
            'message:={"content_parts":[{"type":"text","text":"请核查完成状态"}]}'
        ),
        "endpoint_names": [],
        "endpoints": {},
    })

    assert result.script is not None, result.diagnostics
    assert any(code == "effect.target_endpoint_required" for code, _, _ in result.diagnostics), (
        "固定 Resident 目标未声明端点、未连 target，却通过正式 Effect 分析", result.diagnostics
    )


@pytest.mark.parametrize("script", [
    f'recipient="{TARGET}"\nDeliverMessage resident_id="$recipient" {MESSAGE}',
    f'recipient="{TARGET}"\nnext="$recipient"\nDeliverMessage resident_id="$next" {MESSAGE}',
    f'config:={{"recipient":"{TARGET}"}}\nDeliverMessage resident_id="${{config.recipient}}" {MESSAGE}',
    f'suffix="bbbbbbbbbbbb"\nDeliverMessage resident_id="resident_$suffix" {MESSAGE}',
    f'for recipient in "{TARGET}"; do\nDeliverMessage resident_id="$recipient" {MESSAGE}\ndone',
    f'if [ "${{arguments.ready}}" = "true" ]; then\nrecipient="{TARGET}"\n'
    f'else\nrecipient="${{event.subject_resident_id}}"\nfi\nDeliverMessage resident_id="$recipient" {MESSAGE}',
], ids=["alias", "alias_chain", "json_alias", "assembled_constant", "constant_loop", "branch_constant"])
def test_local_constants_cannot_hide_a_fixed_target(script):
    result = _analyze(script)
    assert any(code == "effect.target_endpoint_required" for code, _, _ in result.diagnostics), result.diagnostics
    assert not any(code == "effect.undefined_variable" for code, _, _ in result.diagnostics)


@pytest.mark.parametrize("reference", [
    "${arguments.recipient}", "${event.subject_resident_id}", "${packet.message.data.target}",
])
def test_dynamic_inputs_remain_valid(reference):
    result = _analyze(f'DeliverMessage resident_id="{reference}" {MESSAGE}')
    assert result.diagnostics == []


def test_dynamic_loop_targets_remain_valid():
    result = _analyze(
        'for recipient in ${arguments.recipients}; do\n'
        f'DeliverMessage resident_id="$recipient" {MESSAGE}\ndone'
    )
    assert result.diagnostics == []


def test_newly_forked_resident_is_a_valid_dynamic_target():
    result = _analyze(
        'child=$(ForkResident lineage_id="lineage_aaaaaaaaaaaa" source_resident_id="${endpoint.source}" title="定向评审")\n'
        f'DeliverMessage resident_id="${{child.resident_id}}" {MESSAGE}',
        declared=["source"], endpoints={"source": TARGET},
    )
    assert result.diagnostics == []


@pytest.mark.parametrize("prefix,reference", [
    ("", "${endpoint.facade}"),
    ('recipient="${endpoint.facade}"\n', "$recipient"),
    ('config:={"recipient":${endpoint.facade}}\n', "${config.recipient}"),
])
def test_connected_endpoint_and_its_aliases_remain_valid(prefix, reference):
    result = _analyze(
        f'{prefix}DeliverMessage resident_id="{reference}" {MESSAGE}',
        declared=["facade"], endpoints={"facade": TARGET},
    )
    assert result.diagnostics == []


def test_ordinary_body_containing_resident_id_is_not_a_target():
    result = _analyze(
        'DeliverMessage resident_id="${endpoint.facade}" '
        f'message:={{"content_parts":[{{"type":"text","text":"请检查 {TARGET}"}}]}}',
        declared=["facade"], endpoints={"facade": TARGET},
    )
    assert result.diagnostics == []


@pytest.mark.parametrize("script", [
    f'DeliverMessage user_id="huanyu" {MESSAGE}',
    'GetLineage lineage_id="lineage_aaaaaaaaaaaa"',
])
def test_targets_not_bindable_by_target_wire_keep_their_contract(script):
    assert _analyze(script).diagnostics == []


@pytest.mark.parametrize("declared,endpoints,code", [
    ([], {}, "effect.unknown_endpoint"),
    (["facade"], {}, "effect.missing_target"),
    (["facade"], {"different": TARGET}, "effect.missing_target"),
])
def test_missing_or_unbound_endpoint_is_diagnostic(declared, endpoints, code):
    result = _analyze(
        f'DeliverMessage resident_id="${{endpoint.facade}}" {MESSAGE}',
        declared=declared, endpoints=endpoints,
    )
    assert any(item[0] == code for item in result.diagnostics), result.diagnostics


def test_component_endpoint_cannot_be_used_as_a_resident_target():
    result = _analyze(
        f'DeliverMessage resident_id="${{endpoint.facade}}" {MESSAGE}',
        declared=["facade"], endpoints={"facade": "effect_cccccccccccc"},
    )
    assert any(code == "effect.target_type_mismatch" for code, _, _ in result.diagnostics), result.diagnostics


def test_declaring_an_endpoint_does_not_authorize_a_literal_bypass():
    result = _analyze(
        f'DeliverMessage resident_id="{TARGET}" {MESSAGE}',
        declared=["facade"], endpoints={"facade": TARGET},
    )
    assert any(code == "effect.target_endpoint_required" for code, _, _ in result.diagnostics), result.diagnostics


def test_graph_component_has_its_own_valid_target_endpoint():
    result = _analyze(
        'GetComponentRuntime lineage_id="lineage_aaaaaaaaaaaa" component_id="${endpoint.observed}"',
        declared=["observed"], endpoints={"observed": "effect_cccccccccccc"},
    )
    assert result.diagnostics == []


def test_overwritten_constant_does_not_taint_a_later_dynamic_target():
    result = _analyze(
        f'recipient="{TARGET}"\nrecipient="${{event.subject_resident_id}}"\n'
        f'DeliverMessage resident_id="$recipient" {MESSAGE}'
    )
    assert result.diagnostics == []


@pytest.mark.parametrize("script", [
    f'recipient="${{endpoint.bad}}"\nDeliverMessage resident_id="$recipient" {MESSAGE}',
    f'config:={{"recipient":${{endpoint.bad}},"note":"hello"}}\n'
    f'DeliverMessage resident_id="${{config.recipient}}" {MESSAGE}',
    f'if [ "${{arguments.ready}}" = "true" ]; then\nrecipient="${{endpoint.bad}}"\n'
    f'else\nrecipient="${{event.subject_resident_id}}"\nfi\nDeliverMessage resident_id="$recipient" {MESSAGE}',
    f'targets:=[${{endpoint.bad}}]\nfor recipient in $targets; do\n'
    f'DeliverMessage resident_id="$recipient" {MESSAGE}\ndone',
])
def test_endpoint_type_survives_alias_json_branch_and_loop(script):
    result = _analyze(script, declared=["bad"], endpoints={"bad": "effect_cccccccccccc"})
    assert any(code == "effect.target_type_mismatch" for code, _, _ in result.diagnostics), result.diagnostics


def test_resident_endpoint_is_not_a_component_target():
    result = _analyze(
        'GetComponentRuntime lineage_id="lineage_aaaaaaaaaaaa" component_id="${endpoint.bad}"',
        declared=["bad"], endpoints={"bad": TARGET},
    )
    assert any(code == "effect.target_type_mismatch" for code, _, _ in result.diagnostics), result.diagnostics


def test_sliced_endpoint_does_not_represent_the_bound_object():
    result = _analyze(
        f'DeliverMessage resident_id="${{endpoint.facade:0:21}}" {MESSAGE}',
        declared=["facade"], endpoints={"facade": TARGET},
    )
    assert any(code == "effect.target_endpoint_required" for code, _, _ in result.diagnostics), result.diagnostics


def _analyze_recursive_loop(sender):
    try:
        result = _analyze(
            f'value="{TARGET}"\nfor item in ${{arguments.items}}; do\n'
            'value:={"child":${value}}\ndone\n'
            f'DeliverMessage resident_id="${{value.child}}" {MESSAGE}'
        )
        sender.send(result.diagnostics)
    finally:
        sender.close()


def test_self_nested_loop_analysis_converges():
    """独立子进程限制坏候选的分析时间，不运行模型或脚本 ISA。"""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_analyze_recursive_loop, args=(sender,))
    process.start()
    sender.close()
    try:
        process.join(timeout=5)
        assert not process.is_alive(), "来源固定点分析未在有限深度内结束"
        assert process.exitcode == 0
        assert receiver.poll(0.2), "分析进程没有返回结果"
        diagnostics = receiver.recv()
        assert any(code == "effect.target_endpoint_required" for code, _, _ in diagnostics), diagnostics
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        receiver.close()


class _Host:
    def __init__(self):
        self.registered = []

    def register_program_agent_invocation(self, lineage_id, target_id, message_id, event, wire_id, actor):
        self.registered.append((target_id, message_id))
        return {"message_id": message_id}

    def dispatch_lineage_program_event(self, *args, **kwargs):
        return {}


def _execute(monkeypatch, definition, *, initialize=True):
    """真实 Effect 执行入口和隔离 journal，替换 ISA 外部指令执行边界。"""
    from management_portal.hatg import routing

    lineage_id = "lineage_aaaaaaaaaaaa"
    if initialize:
        state_store.get_lineage_data_store().put_lineage_definition(
            lineage_id,
            {"schema": "tfi.lineage.v1", "schema_version": 1, "lineage_id": lineage_id,
             "title": "Effect target test", "nodes": [], "edges": [], "program": {}},
            {"schema": "tfi.lineage-runtime.v1", "lineage_id": lineage_id},
        )
    monkeypatch.setattr(lineage_instruction_context, "get_portal_access_store", lambda: SimpleNamespace(
        is_configured=lambda actor: actor == "CASE-A", is_active=lambda actor: actor == "CASE-A"))
    calls = []

    def fake_execute(call, context):
        calls.append(call)
        return SimpleNamespace(status="committed", result={"resident_id": TARGET, "message_id": "msg_target"},
                               error={}, instruction_id=f"inst_target_{len(calls)}", replayed=False)

    monkeypatch.setattr(routing, "execute", fake_execute)
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **k: {})
    event = lineage_primitives.LineageEvent(
        id="event_target_contract", type="manual.triggered", payload={},
        identity=OperationIdentity.for_actor("CASE-A").to_dict(),
    )
    invocation = lineage_primitives.EffectInvocation(id="invocation_target_contract", activation=event, arguments={})
    host = _Host()
    result = execution._execute_program_effect_component(
        {"lineage_id": lineage_id}, {"effects": {EFFECT: definition}, "event_sources": {}},
        EFFECT, invocation, "CASE-A", host=host,
    )
    return result, calls, host


@pytest.mark.parametrize("definition", [
    _definition(f'GetLineage lineage_id="lineage_aaaaaaaaaaaa"\nDeliverMessage resident_id="{TARGET}" {MESSAGE}'),
    _definition(f'DeliverMessage resident_id="${{endpoint.facade}}" {MESSAGE}', declared=["facade"]),
    _definition(f'DeliverMessage resident_id="${{endpoint.facade}}" {MESSAGE}',
                declared=["facade"], endpoints={"facade": "effect_cccccccccccc"}),
], ids=["fixed_target", "disconnected", "wrong_kind"])
def test_invalid_target_stops_before_any_instruction(monkeypatch, definition):
    result, calls, host = _execute(monkeypatch, definition)
    assert result["status"] == "failed", result
    assert calls == [], "无效目标应在指令执行之前被拒绝"
    assert host.registered == []


def test_valid_endpoint_reaches_the_original_instruction_owner(monkeypatch):
    definition = _definition(
        f'DeliverMessage resident_id="${{endpoint.facade}}" {MESSAGE}',
        declared=["facade"], endpoints={"facade": TARGET},
    )
    result, calls, host = _execute(monkeypatch, definition)
    assert result["status"] == "executed", result
    assert len(calls) == 1
    assert calls[0]["instruction"] == "DeliverMessage"
    assert calls[0]["arguments"]["resident_id"] == TARGET
    assert host.registered == [(TARGET, "msg_target")]


def test_completed_legacy_activation_replays_without_new_calls(monkeypatch):
    """模拟旧策略已完成的真实 journal，新策略不追溯改写终态或重投。"""
    from management_portal.society_service import lineage_program

    definition = _definition(f'DeliverMessage resident_id="{TARGET}" {MESSAGE}')
    with monkeypatch.context() as legacy:
        # Only the new analysis policy is disabled to create a pre-policy terminal record.
        legacy.setattr(lineage_program, "effect_script_analysis", lambda _definition: SimpleNamespace(diagnostics=[]))
        first, calls, host = _execute(legacy, definition)
        assert first["status"] == "executed"
        assert len(calls) == 1 and len(host.registered) == 1

    assert any(code == "effect.target_endpoint_required" for code, _, _ in effect_script_analysis(definition).diagnostics)
    replay, calls, host = _execute(monkeypatch, definition, initialize=False)
    assert replay["status"] == "executed"
    assert replay["replayed"] is True
    assert replay["typed_outputs"] == first["typed_outputs"]
    assert replay["calls"] == first["calls"]
    assert calls == []
    assert host.registered == []
