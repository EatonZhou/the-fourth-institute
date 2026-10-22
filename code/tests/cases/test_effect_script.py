"""Effect scripts: parsing, checks, interpretation and resumable execution (decision_4adcc1ff2f2c).

:module: spec.design-governance
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from management_portal.hatg.catalog import instruction_catalog
from management_common.principal import OperationIdentity
from management_portal.society_service import (
    lineage_effect_script as script_module,
    lineage_effect_script_journal as journal,
    lineage_primitives,
    lineage_program_effect_execution as execution,
)
from management_portal.society_service.lineage_effect_script import (
    CallResult,
    Interpreter,
    ScriptSyntaxError,
    analyze,
    deliver_message_script,
    deliver_script_parts,
    parse,
)


@pytest.fixture(autouse=True)
def active_script_user(monkeypatch):
    from management_portal.society_service import lineage_instruction_context
    monkeypatch.setattr(lineage_instruction_context, "get_portal_access_store", lambda: SimpleNamespace(
        is_configured=lambda user: user == "CASE-A", is_active=lambda user: user == "CASE-A"))


def run(text, inputs=None, results=None, *, max_calls=32):
    calls = []

    def execute(request):
        calls.append(request)
        handler = (results or {}).get(request.instruction)
        if handler is None:
            return CallResult(True, {"ok": True}, instruction_id=f"inst_{len(calls)}")
        value = handler(request)
        return value if isinstance(value, CallResult) else CallResult(True, value, instruction_id=f"inst_{len(calls)}")

    outcome = Interpreter(parse(text), inputs or {}, execute, max_calls=max_calls).run()
    return outcome, calls


def test_fork_then_deliver_passes_results_between_calls():
    """ForkResident 的结果传给后续 DeliverMessage 和脚本输出"""
    outcome, calls = run(
        'child=$(ForkResident source_resident_id="${endpoint.source}" title="评审 · ${arguments.topic}")\n'
        'DeliverMessage resident_id="${child.resident_id}" message="请评审。"\n'
        'output resident_id="${child.resident_id}"',
        {"endpoint": {"source": "resident_aaaaaaaaaaaa"}, "arguments": {"topic": "T"}},
        {"ForkResident": lambda r: {"resident_id": "resident_bbbbbbbbbbbb"}},
    )
    assert outcome.status == "succeeded"
    assert outcome.outputs == {"resident_id": "resident_bbbbbbbbbbbb"}
    assert [(c.position, c.instruction) for c in calls] == [("c1", "ForkResident"), ("c2", "DeliverMessage")]
    assert calls[0].arguments == {"source_resident_id": "resident_aaaaaaaaaaaa", "title": "评审 · T"}
    assert calls[1].arguments["resident_id"] == "resident_bbbbbbbbbbbb"


def test_for_and_if_record_loop_indices_in_call_positions():
    """for 和 if 内的调用位置记录循环下标"""
    states = {"r1": "idle", "r2": "running", "r3": "idle"}
    outcome, calls = run(
        'for rid in ${arguments.ids}; do\n'
        '  st=$(ListResidentStates resident_ids:=[$rid])\n'
        '  if [ "${st.states.0.state.work_state}" = "idle" ]; then\n'
        '    DeliverMessage resident_id="$rid" message="继续"\n'
        '  fi\n'
        'done',
        {"arguments": {"ids": ["r1", "r2", "r3"]}},
        {"ListResidentStates": lambda r: {"states": [{"state": {"work_state": states[r.arguments["resident_ids"][0]]}}]}},
    )
    assert outcome.status == "succeeded"
    assert [(c.position, c.instruction) for c in calls] == [
        ("c1.0", "ListResidentStates"), ("c2.0", "DeliverMessage"),
        ("c1.1", "ListResidentStates"),
        ("c1.2", "ListResidentStates"), ("c2.2", "DeliverMessage"),
    ]


def test_values_keep_types_when_they_are_one_reference_and_json_placeholders_encode():
    """单个引用构成的参数保留原类型，JSON 参数中的占位符按 JSON 编码，缺失值为 null"""
    outcome, calls = run(
        'Echo ids="${arguments.ids}" n="${arguments.n}" text="n=${arguments.n} ids=${arguments.ids}" '
        'obj:={"list":${arguments.ids},"label":"$not_expanded","n":$n}',
        {"arguments": {"ids": ["a", "b"], "n": 3}},
    )
    # obj references $n which is undefined at run time: placeholders of missing values encode as null.
    assert calls[0].arguments == {
        "ids": ["a", "b"], "n": 3, "text": 'n=3 ids=["a","b"]',
        "obj": {"list": ["a", "b"], "label": "$not_expanded", "n": None},
    }


def test_a_reference_can_keep_part_of_its_text():
    """脚本引用可按字符偏移和长度截取，非文本值从紧凑 JSON 截取

    :module: platform.core"""
    outcome, calls = run(
        'Echo head="${arguments.text:0:3}" tail="${arguments.text:4}" ids="${arguments.ids:0:4}" '
        'none="${arguments.missing:0:5}" line="[${arguments.text:2:3}]"',
        {"arguments": {"text": "路由指引很长的一段", "ids": ["a", "b"]}},
    )
    # Characters, not bytes; a non-text value is cut from its compact JSON.
    assert calls[0].arguments == {"head": "路由指", "tail": "很长的一段", "ids": '["a"', "none": "", "line": "[指引很]"}


@pytest.mark.parametrize("text", ['Echo a="${arguments.x:-1}"', 'Echo a="${arguments.x:1:2:3}"', 'Echo a="${arguments.x:a}"'])
def test_only_offset_and_length_cuts_are_accepted(text):
    """引用截取只接受非负偏移和长度，其他写法解析时报语法错误

    :module: platform.core"""
    with pytest.raises(ScriptSyntaxError):
        parse(text)


def test_failed_call_stops_the_script_unless_it_is_a_condition():
    """调用失败终止脚本，作为 if 条件的调用失败只走否定分支"""
    failing = {"GetSharedState": lambda r: CallResult(False, error={"code": "instruction.not_found", "message": "no key"})}
    outcome, calls = run(
        'guide=""\nif g=$(GetSharedState key="k"); then\n  guide="${g.value}"\nfi\nDeliverMessage resident_id="r" message="$guide"',
        results=failing,
    )
    assert outcome.status == "succeeded"
    assert calls[-1].arguments["message"] == ""
    outcome, _ = run('GetSharedState key="k"\nDeliverMessage resident_id="r"', results=failing)
    assert outcome.status == "failed"
    assert (outcome.error.code, outcome.error.line) == ("instruction.not_found", 1)


def test_exit_fail_and_the_call_limit_end_the_script():
    """exit 以成功结束脚本，fail 以不可重试错误结束，超过调用上限时失败"""
    outcome, calls = run('if [ "${arguments.skip}" = "true" ]; then\n  exit\nfi\nDeliverMessage resident_id="r"', {"arguments": {"skip": True}})
    assert outcome.status == "succeeded" and calls == []
    outcome, _ = run('fail "停止：${arguments.why}"', {"arguments": {"why": "x"}})
    assert outcome.status == "failed"
    assert (outcome.error.code, outcome.error.message, outcome.error.retryable) == ("effect.script_failed", "停止：x", False)
    outcome, calls = run('for i in ${arguments.items}; do\n  Ping n="$i"\ndone', {"arguments": {"items": list(range(40))}})
    assert outcome.status == "failed" and outcome.error.code == "effect.script_call_limit"
    assert len(calls) == 32


def test_tests_compare_text_and_integers_and_combine_with_and_or_not():
    """条件测试支持文本与整数比较和 &&、||、! 组合，整数比较遇到文本时报错"""
    inputs = {"arguments": {"a": "x", "n": 5, "empty": ""}}
    script = (
        'if [ "${arguments.a}" = x ] && [ "${arguments.n}" -ge 4 ] && ! [ -n "${arguments.empty}" ]; then\n'
        '  Ok\nelif [ -z "${arguments.a}" ] || [ 1 -eq 1 ]; then\n  No\nelse\n  Never\nfi'
    )
    _, calls = run(script, inputs)
    assert [c.instruction for c in calls] == ["Ok"]
    outcome, _ = run('if [ "${arguments.a}" -lt 3 ]; then\n  Ok\nfi', inputs)
    assert outcome.status == "failed" and outcome.error.code == "effect.script_integer_expected"


@pytest.mark.parametrize("text, code", [
    ('DeliverMessage message="open', "effect.script_syntax"),
    ("A | B", "effect.script_unsupported"),
    ("A > out", "effect.script_unsupported"),
    ("while true; do\n  A\ndone", "effect.script_unsupported"),
    ("x=$((1+2))", "effect.script_unsupported"),
    ('Echo m="$(Get)"', "effect.script_unsupported"),
    ("event=1", "effect.readonly_variable"),
    ("if [ 1 = 1 ]\nA\nfi", "effect.script_syntax"),
    ("Echo notanargument", "effect.script_syntax"),
])
def test_unsupported_or_malformed_scripts_report_the_failing_line(text, code):
    """含管道、重定向、while、算术等不支持语法或格式错误的脚本报错并给出行号"""
    with pytest.raises(ScriptSyntaxError) as raised:
        parse(text)
    assert raised.value.code == code
    assert raised.value.line >= 1


def test_analysis_checks_instructions_arguments_variables_and_endpoints():
    """脚本分析报告未知指令、参数错误、未定义变量和缺少目标端点，并给出行号"""
    catalog = instruction_catalog()
    result = analyze(
        'x=$(GetSharedState key="k")\nDeliverMessage resident_id="${endpoint.target}" message="${x.value} $y"\n'
        'NoSuchInstruction\nForkResident bogus=1',
        declaration_for=catalog.get,
        declared_endpoints=["target"],
        connected_endpoints=[],
        runtime_supplied={"DeliverMessage": ["message"]},
    )
    codes = {code for code, _message, _line in result.diagnostics}
    assert codes >= {
        "effect.undefined_variable", "effect.missing_target", "effect.unknown_instruction",
        "effect.unknown_argument", "effect.missing_argument",
    }
    assert result.endpoint_names == {"target"}
    lines = {code: line for code, _message, line in result.diagnostics}
    assert lines["effect.unknown_instruction"] == 3


@pytest.mark.parametrize("argument", ["timeout_seconds=3600", "timeout_seconds:=0", "timeout_seconds:=false"])
def test_constant_arguments_follow_instruction_parameter_checks(argument):
    """脚本常量参数按指令参数规则检查，非法 timeout_seconds 报 invalid_argument

    :module: platform.core"""
    from management_portal.society_service.lineage_program_effect_rules import validate_effect_argument

    result = analyze(
        f"# maintenance\nRunScript path=/tmp/maintenance.sh {argument}",
        declaration_for=instruction_catalog().get,
        validate_argument=validate_effect_argument,
    )
    assert any(code == "effect.invalid_argument" and line == 2 for code, _, line in result.diagnostics)


def test_typed_effect_constants_and_message_shorthand_remain_valid():
    """带类型的常量参数和不加引号的消息简写通过脚本分析

    :module: platform.core"""
    from management_portal.society_service.lineage_program_effect_rules import validate_effect_argument

    result = analyze(
        'RunScript path=/tmp/maintenance.sh timeout_seconds:=3600\n'
        'DeliverMessage resident_id=resident_0123456789ab message=hello',
        declaration_for=instruction_catalog().get,
        validate_argument=validate_effect_argument,
    )
    assert result.diagnostics == []


def test_the_family_router_script_parses_and_routes(tmp_path):
    """Family Router 脚本可解析，经 CompleteChat 选出成员后转发原消息内容"""
    router = '''facade="${event.payload.resident_id}"
mid="${event.payload.message_id}"
if [ "${event.payload.relay_depth}" -ge 4 ]; then
  fail "经过的门面数超过上限"
fi
msg=$(GetMessage resident_id="$facade" message_id="$mid")
decision=$(CompleteChat api_id="${arguments.api_id}" \\
  messages:=[{"role":"system","content":"选出一个成员"},{"role":"user","content":${msg.message.content_parts}}] \\
  response_schema:={"type":"object","properties":{"target":{"enum":${arguments.members}}}})
DeliverMessage resident_id="${decision.structured.target}" \\
  message:={"content_parts":${msg.message.content_parts}} business_ref="$facade/message/$mid"
output target="${decision.structured.target}"'''
    parts = [{"type": "text", "text": "你好"}]
    outcome, calls = run(router, {
        "event": {"payload": {"resident_id": "resident_ffffffffffff", "message_id": "msg_1", "relay_depth": 1}},
        "arguments": {"api_id": "deepseek_primary", "members": ["resident_aaaaaaaaaaaa"]},
    }, {
        "GetMessage": lambda r: {"message": {"content_parts": parts}},
        "CompleteChat": lambda r: {"structured": {"target": r.arguments["response_schema"]["properties"]["target"]["enum"][0]}},
    })
    assert outcome.status == "succeeded", outcome.error
    assert outcome.outputs == {"target": "resident_aaaaaaaaaaaa"}
    assert calls[1].arguments["messages"][1]["content"] == parts
    assert calls[2].arguments == {
        "resident_id": "resident_aaaaaaaaaaaa", "message": {"content_parts": parts},
        "business_ref": "resident_ffffffffffff/message/msg_1",
    }


def test_written_scripts_round_trip_their_text_exactly():
    """生成的 DeliverMessage 脚本能原样还原消息文本，手写脚本不被当作生成脚本"""
    for message in ["检查节点状态", 'say "$HOME" `x` \\n', "{{param.prompt}}", "两行\n第二行"]:
        script = deliver_message_script(message, activation_policy="enqueue_and_trigger")
        assert deliver_script_parts(script) == ("target", message, {"activation_policy": "enqueue_and_trigger"})
    assert deliver_script_parts('DeliverMessage resident_id="r" message="x"') is None
    assert deliver_script_parts('A\nDeliverMessage resident_id="${endpoint.target}" message="x"') is None


def test_delivery_workflow_preserves_typed_message_credentials():
    """显式启用的流程把本次 Event 的凭证请求原样交给 DeliverMessage，普通流程不借权。"""
    credentials = [{"a": "@contributor", "resource_id": "workbench_aaaaaaaaaaaa"}]
    inputs = {"endpoint": {"target": "resident_aaaaaaaaaaaa"},
              "event": {"payload": {"temporary_credentials": credentials}}}
    script = deliver_message_script("Draft only", lend_temporary_credentials=True)
    assert deliver_script_parts(script) == ("target", "Draft only", {"lend_temporary_credentials": True})
    outcome, calls = run(script, inputs)
    assert outcome.status == "succeeded", outcome.error
    assert calls[0].arguments["temporary_credentials"] == credentials
    _, ordinary = run(deliver_message_script("Ordinary"), inputs)
    assert "temporary_credentials" not in ordinary[0].arguments
    with pytest.raises(ValueError, match="one source"):
        deliver_message_script("Invalid", lend_temporary_credentials=True, temporary_credentials=credentials)




# -- execution with the resume journal ------------------------------------------------------------


class _Outcome:
    def __init__(self, status, result=None, error=None, instruction_id=""):
        self.status, self.result, self.error, self.instruction_id, self.replayed = status, result, error, instruction_id, False


class _Host:
    def __init__(self):
        self.registered = []

    def register_program_agent_invocation(self, lineage_id, target_id, message_id, event, wire_id, actor):
        self.registered.append((target_id, message_id))
        return {"message_id": message_id}

    def dispatch_lineage_program_event(self, *args, **kwargs):
        return {}


def _lineage(lineage_id: str) -> dict:
    from management_portal.society_service import state_store

    state_store.get_lineage_data_store().put_lineage_definition(
        lineage_id,
        {"schema": "tfi.lineage.v1", "schema_version": 1, "lineage_id": lineage_id,
         "title": "Effect script test", "nodes": [], "edges": [], "program": {}},
        {"schema": "tfi.lineage-runtime.v1", "lineage_id": lineage_id},
    )
    return {"lineage_id": lineage_id}


def _plan(script):
    return {"effects": {"effect_aaaaaaaaaaaa": {
        "effect_id": "effect_aaaaaaaaaaaa", "script": script,
        "endpoint_names": ["target"], "endpoints": {"target": "resident_bbbbbbbbbbbb"},
    }}, "event_sources": {}}


def test_a_reclaimed_activation_reuses_completed_calls_and_registers_each_delivery(monkeypatch):
    """中断后重新认领的 Effect 激活复用已完成调用，每次投递登记一次，结束后重复激活直接回放"""
    from management_portal.hatg import routing

    lineage = _lineage("lineage_effectscript1")
    event = lineage_primitives.LineageEvent(id="event_resume_1", type="manual.triggered", payload={}, identity=OperationIdentity.for_actor("CASE-A").to_dict())
    activation = lineage_primitives.EffectInvocation(id="invocation_resume_1", activation=event, arguments={"prompt": "today"})
    plan = _plan(
        'DeliverMessage resident_id="${endpoint.target}" message="first"\n'
        'Crash\n'
        'DeliverMessage resident_id="${endpoint.target}" message="second"\n'
        'output done="yes"'
    )
    executed = []
    crash = {"on": True}

    def fake_execute(call, context):
        executed.append((call["instruction"], call["idempotency_key"]))
        if call["instruction"] == "Crash":
            if crash["on"]:
                raise SystemExit("worker handover")  # not an Exception: the activation is interrupted
            return _Outcome("committed", {}, instruction_id="inst_crash")
        number = len([item for item in executed if item[0] == "DeliverMessage"])
        return _Outcome("committed", {"resident_id": "resident_bbbbbbbbbbbb", "message_id": f"msg_{number}"}, instruction_id=f"inst_{number}")

    monkeypatch.setattr(routing, "execute", fake_execute)
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **k: {"calls": k.get("calls")})
    host = _Host()
    with pytest.raises(SystemExit):
        execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=host)
    assert [name for name, _key in executed] == ["DeliverMessage", "Crash"]
    assert host.registered == [("resident_bbbbbbbbbbbb", "msg_1")]

    crash["on"] = False
    result = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=host)
    assert result["status"] == "executed"
    assert result["typed_outputs"] == {"done": "yes"}
    # The first DeliverMessage was not called again; Crash and the second delivery ran once each.
    assert [name for name, _key in executed] == ["DeliverMessage", "Crash", "Crash", "DeliverMessage"]
    assert host.registered == [("resident_bbbbbbbbbbbb", "msg_1"), ("resident_bbbbbbbbbbbb", "msg_2")]
    assert [item["instruction"] for item in result["calls"]] == ["DeliverMessage", "Crash", "DeliverMessage"]
    keys = [key for _name, key in executed]
    assert keys[1] == keys[2] and len(set(keys)) == 3

    # A duplicate activation after the end reuses the recorded outcome without calling.
    again = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=host)
    assert again["replayed"] is True and again["typed_outputs"] == {"done": "yes"}
    assert len(executed) == 4
    key = execution.effect_idempotency_key(lineage["lineage_id"], "effect_aaaaaaaaaaaa", activation)
    finished = journal.trigger_paths.effect_finished(lineage["lineage_id"], key)
    calls = journal.path_calls(lineage["lineage_id"], finished["trigger_path_id"])
    assert [item["instruction"] for item in calls] == ["DeliverMessage", "Crash", "DeliverMessage"]
    # Read back from the finished record, each call keeps its outcome.
    assert [item["status"] for item in calls] == ["committed"] * 3
    assert [item["status"] for item in again["calls"]] == ["committed"] * 3
    # The finished activation is a run record; its resume journal is gone.
    from management_portal.society_service.state_store import get_lineage_data_store

    store = get_lineage_data_store()
    assert store.effect_work.list('lineage_effectscript1') == {}
    assert finished["status"] == "succeeded"
    observed = journal.trigger_paths.get(lineage["lineage_id"], finished["trigger_path_id"])
    assert observed["root_kind"] == "effect" and observed["occurrence_id"] == key
    assert observed["occurred_at"] == finished["started_at"]


def test_a_failed_script_reports_the_line_and_completed_calls(monkeypatch):
    """Effect 脚本失败时输出错误码、失败行号和已完成调用数"""
    from management_portal.hatg import routing

    lineage = _lineage("lineage_effectscript2")
    event = lineage_primitives.LineageEvent(id="event_fail_1", type="manual.triggered", payload={}, identity=OperationIdentity.for_actor("CASE-A").to_dict())
    activation = lineage_primitives.EffectInvocation(id="invocation_fail_1", activation=event, arguments={})
    plan = _plan('Ping\nBoom\nPing')

    def fake_execute(call, context):
        if call["instruction"] == "Boom":
            return _Outcome("failed", error={"code": "instruction.invalid_arguments", "message": "bad", "retryable": False})
        return _Outcome("committed", {}, instruction_id="inst_ping")

    monkeypatch.setattr(routing, "execute", fake_execute)
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **k: {})
    result = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=_Host())
    assert result["status"] == "failed"
    assert result["typed_outputs"] == {
        "error_code": "instruction.invalid_arguments", "diagnostic": "bad", "retryable": False,
        "failed_line": 2, "completed_calls": 1,
    }


def test_transient_call_failure_resumes_with_the_same_key_without_repeating_success(monkeypatch):
    """可重试的调用失败后用同一幂等键续跑，已成功的调用不重复

    :module: platform.core"""
    from management_portal.hatg import routing
    from management_portal.society_service.state_store import get_lineage_data_store

    lineage = _lineage("lineage_effectretry1")
    event = lineage_primitives.LineageEvent(id="event_retry_1", type="manual.triggered", payload={}, identity=OperationIdentity.for_actor("CASE-A").to_dict())
    activation = lineage_primitives.EffectInvocation(id="invocation_retry_1", activation=event, arguments={})
    plan = _plan('Ping\nDeliverMessage resident_id="${endpoint.target}" message="hi"')
    calls = []

    def fake_execute(call, context):
        calls.append((call["instruction"], call["idempotency_key"]))
        if len(calls) == 2:
            return _Outcome("failed", error={"code": "instruction.in_progress", "message": "busy", "retryable": True})
        return _Outcome("committed", {"resident_id": "resident_bbbbbbbbbbbb", "message_id": "msg_retry"})

    monkeypatch.setattr(routing, "execute", fake_execute)
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **k: {})
    host = _Host()
    first = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=host)
    assert first["status"] == "failed" and first["retryable"] is True
    store = get_lineage_data_store()
    key = execution.effect_idempotency_key(lineage["lineage_id"], "effect_aaaaaaaaaaaa", activation)
    assert journal.trigger_paths.effect_finished(lineage["lineage_id"], key) is None
    assert len(store.effect_work.list(lineage['lineage_id'])) == 1

    second = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=host)
    assert second["status"] == "executed"
    assert [name for name, _ in calls] == ["Ping", "DeliverMessage", "DeliverMessage"]
    assert calls[1] == calls[2]
    assert host.registered == [("resident_bbbbbbbbbbbb", "msg_retry")]
    assert store.effect_work.list(lineage['lineage_id']) == {}
    third = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=host)
    assert third["replayed"] and len(calls) == 3


def test_stored_terminal_failure_does_not_block_following_pending_events():
    """可重试失败的待投递事件仍保留，回放出的已存终态失败算作已消费

    :module: platform.core"""
    from management_portal.society_service.lineage_event_delivery import pending_event_delivery_consumed

    result = {"wire_id": "wire_retry", "status": "failed", "typed_outputs": {"retryable": True}}
    assert not pending_event_delivery_consumed({"results": [result]}, "wire_retry")
    assert pending_event_delivery_consumed({"results": [{**result, "replayed": True}]}, "wire_retry")


def test_a_script_changed_mid_activation_is_not_resumed_on_the_new_text(monkeypatch):
    """激活中途脚本被修改时不按新文本续跑，以 effect.script_changed 失败"""
    from management_portal.hatg import routing

    lineage = _lineage("lineage_effectscript3")
    event = lineage_primitives.LineageEvent(id="event_change_1", type="manual.triggered", payload={}, identity=OperationIdentity.for_actor("CASE-A").to_dict())
    activation = lineage_primitives.EffectInvocation(id="invocation_change_1", activation=event, arguments={})
    monkeypatch.setattr(routing, "execute", lambda call, context: (_ for _ in ()).throw(SystemExit("handover")))
    with pytest.raises(SystemExit):
        execution._execute_program_effect_component(lineage, _plan("Ping"), "effect_aaaaaaaaaaaa", activation, "CASE-A", host=_Host())
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **k: {})
    result = execution._execute_program_effect_component(lineage, _plan("Ping\nPing"), "effect_aaaaaaaaaaaa", activation, "CASE-A", host=_Host())
    assert result["status"] == "failed"
    assert "被修改" in result["error"]
    assert result["typed_outputs"]["error_code"] == "effect.script_changed"


def test_a_failed_invocation_registration_propagates_and_is_retried_on_reclaim(monkeypatch):
    """投递登记失败时 Effect 抛出错误，重新认领后只补登记，不重复投递"""
    from management_portal.hatg import routing

    lineage = _lineage("lineage_effectscript4")
    event = lineage_primitives.LineageEvent(id="event_register_1", type="manual.triggered", payload={}, identity=OperationIdentity.for_actor("CASE-A").to_dict())
    activation = lineage_primitives.EffectInvocation(id="invocation_register_1", activation=event, arguments={})
    plan = _plan('DeliverMessage resident_id="${endpoint.target}" message="hi"')
    executed = []

    def fake_execute(call, context):
        executed.append(call["instruction"])
        return _Outcome("committed", {"resident_id": "resident_bbbbbbbbbbbb", "message_id": "msg_reg"}, instruction_id="inst_reg")

    class _FlakyHost(_Host):
        fail = True

        def register_program_agent_invocation(self, *args):
            if self.fail:
                raise RuntimeError("run ledger unavailable")
            return super().register_program_agent_invocation(*args)

    monkeypatch.setattr(routing, "execute", fake_execute)
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **k: {})
    host = _FlakyHost()
    with pytest.raises(RuntimeError, match="ledger"):
        execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=host)
    host.fail = False
    result = execution._execute_program_effect_component(lineage, plan, "effect_aaaaaaaaaaaa", activation, "CASE-A", host=host)
    assert result["status"] == "executed"
    assert executed == ["DeliverMessage"]
    assert host.registered == [("resident_bbbbbbbbbbbb", "msg_reg")]


def test_workflow_bindings_read_the_receipt_a_delivering_effect_returns(monkeypatch):
    """工作流触发读取 Effect 实际返回的投递回执

    真实执行投递消息的 Effect 后，把它的回执按 TriggerEvent 的格式交给工作流触发，触发结果拿到实际送达的消息 ID。

    The workflow trigger reads the very result a real DeliverMessage Effect produces."""
    from management_portal import isa_client, lineage_workflows
    from management_portal.hatg import routing

    lineage = _lineage("lineage_effectscript9")
    effect_id, resident_id = "effect_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"
    event = lineage_primitives.LineageEvent(id="event_workflow_9", type="manual.triggered", payload={}, identity=OperationIdentity.for_actor("CASE-A").to_dict())
    activation = lineage_primitives.EffectInvocation(id="invocation_workflow_9", activation=event, arguments={"prompt": "today"})
    delivered = {"resident_id": resident_id, "message_id": "msg_0123456789abcdef",
                 "input_state": {"message_id": "msg_0123456789abcdef", "state": "queued", "reply_message_ids": []}}
    monkeypatch.setattr(routing, "execute", lambda call, context: _Outcome("committed", dict(delivered), instruction_id="inst_9"))
    monkeypatch.setattr(execution.lineage_program_runtime, "effect_result_projection", lambda *a, **k: {"calls": k.get("calls")})
    result = execution._execute_program_effect_component(
        lineage, _plan(deliver_message_script("Fixed prompt")), effect_id, activation, "CASE-A", host=_Host(),
    )
    # TriggerEvent reports each Effect target as {wire_id, target_id, **Effect result}.
    receipt = {"event_id": "event_workflow_9", "trigger_path_id": journal.trigger_paths.effect_finished(lineage["lineage_id"],
                   execution.effect_idempotency_key(lineage["lineage_id"], effect_id, activation))["trigger_path_id"],
               "delivery": {"results": [{"wire_id": "wire_cccccccccccc", "target_id": effect_id, **result}]}}
    monkeypatch.setattr(isa_client, "call", lambda *args, **kwargs: receipt)
    binding = {"lineage_id": lineage["lineage_id"], "event_source_id": "event_dddddddddddd",
               "effect_id": effect_id, "resident_id": resident_id}
    handle = lineage_workflows.WorkflowBindings().trigger(binding, actor="CASE-A", prompt="today", request_id="request_9")
    assert handle["message_id"] == "msg_0123456789abcdef"
    assert handle["event_id"] == "event_workflow_9" and handle["status"] == "running"
