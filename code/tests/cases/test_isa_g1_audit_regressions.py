"""Behavior regressions from the G1 ISA audit.

:module: spec.design-governance
"""
import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from management_common.operation_errors import OperationError
from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError, normalize_error
from management_portal.hatg.kernel import Invocation
from tests.cases.test_hatg_isa_kernel import CONTEXT, fake, kernel as instruction_kernel, scheduler_call  # noqa: F401


def invocation(name, arguments, version=None):
    return Invocation(declaration(name), InstructionCall(name, arguments, expected_version=version), CONTEXT, "inst_test")


def test_get_user_exposes_its_update_version(monkeypatch):
    """GetUser 返回用于更新的 version 字段"""
    from management_portal import web_isa

    access = SimpleNamespace(user=lambda _: {"configured": True}, _revision=lambda: 7)
    monkeypatch.setattr(web_isa, "_access", lambda: access)
    monkeypatch.setattr(web_isa, "_public", lambda user: user)
    result = web_isa.get_user(invocation("GetUser", {"user_id": "alice"}))
    assert result["version"] == 7
    assert "version" in {field.name for field in declaration("GetUser").result}


def test_targetless_mutation_accepts_optional_version(instruction_kernel):
    """没有目标对象的写指令接受可选的 expected_version"""
    result = instruction_kernel.execute(scheduler_call(expected_version=123), CONTEXT)
    assert result.status == "committed", result.error
    instruction_kernel.binding.version_of = None
    assert instruction_kernel.execute(scheduler_call(expected_version=123), CONTEXT).status == "committed"


def test_version_lookup_failure_is_saved_for_replay(instruction_kernel):
    """版本查找失败的结果被保存，同键重试回放同一失败"""
    def missing(*_args):
        raise KeyError("missing")
    instruction_kernel.binding.version_of = missing
    call = scheduler_call(idempotency_key="missing-version", expected_version={"missing": 1})
    first = instruction_kernel.execute(call, CONTEXT)
    again = instruction_kernel.execute(call, CONTEXT)
    assert first.error["code"] == again.error["code"] == "instruction.not_found"
    assert again.replayed
    assert instruction_kernel.store.get(first.instruction_id)["status"] == "failed"


def test_optional_null_replays_as_omitted(instruction_kernel, fake):
    """可选参数传 null 与省略视为同一请求，同键调用回放结果"""
    # A declared optional parameter has the same meaning when null or omitted.
    param = next(p for p in declaration("CreateModelApi").parameters if not p.required and p.type != "any")
    args = {p.name: ({} if p.type == "object" else "g1") for p in declaration("CreateModelApi").parameters if p.required}
    call = InstructionCall("CreateModelApi", args, "null-model")
    first = instruction_kernel.execute(InstructionCall(call.instruction, {**args, param.name: None}, call.idempotency_key), CONTEXT)
    again = instruction_kernel.execute(call, CONTEXT)
    assert first.status == again.status == "committed", (first.error, again.error)
    assert again.replayed and len(fake.calls) == 1


def test_any_null_remains_a_value_in_fingerprint():
    """any 类型参数的 null 是有效值，与省略的请求指纹不同"""
    from management_portal.hatg.store import fingerprint

    assert fingerprint(InstructionCall("UpdateSharedState", {"lineage_id": "lineage_aaaaaaaaaaaa", "key": "x", "value": None})) != fingerprint(InstructionCall("UpdateSharedState", {"lineage_id": "lineage_aaaaaaaaaaaa", "key": "x"}))


@pytest.mark.parametrize("code,status,retryable,expected", [
    ("storage.temporary", 500, True, "instruction.owner_unavailable"),
    ("storage.permanent", 503, False, "instruction.failed"),
    ("lineage.busy", 503, True, "instruction.owner_unavailable"),
    ("operation.forbidden", 403, False, "instruction.forbidden"),
])
def test_domain_errors_keep_reason_and_retryability(code, status, retryable, expected):
    """domain 错误映射为 ISA 错误码时保留原因和是否可重试"""
    error = normalize_error(OperationError("failure", code=code, http_status=status, retryable=retryable))
    assert error.code == expected
    assert error.details["reason"] == code
    assert error.retryable is retryable


@pytest.mark.parametrize("axis", ["x", "y"])
def test_update_component_rejects_canvas_coordinates(axis):
    """UpdateComponent 拒绝修改画布坐标 x 或 y"""
    from management_portal.society_service.isa.definition import update_component

    with pytest.raises(InstructionError) as failure:
        update_component(invocation("UpdateComponent", {"lineage_id": "lineage_aaaaaaaaaaaa", "component_id": "component_aaaaaaaaaaaa", "changes": {axis: 10}}))
    assert failure.value.code == "instruction.invalid_arguments"


@pytest.mark.parametrize("delete", [False, True])
def test_pending_mutation_changes_the_current_item_inside_the_runtime_lock(monkeypatch, delete):
    """待投递调用的修改在运行锁内作用于当前条目

    验证修改或删除待投递调用时在运行事务内读取并改变当前条目，修改后条目的修订号加一。
    """
    from management_portal.society_service import lineage_program_runtime as runtime

    from management_portal.society_service import trigger_paths
    from tests.cases.test_lineage_catalogs import _lineage_definition, LINEAGE_ID
    from management_portal.society_service.lineage_catalog import LineageCatalog
    from management_portal.society_service.state_store import get_lineage_data_store
    LineageCatalog(get_lineage_data_store()).upsert(_lineage_definition())
    event = {"id": "pending-event", "type": "manual.triggered", "occurred_at": "2026-10-12T00:00:00Z", "payload": {}}
    event["payload"]["trigger_path_id"] = trigger_paths.begin_event(LINEAGE_ID, "source-one", event, cause_kind="external", cause_id="pending-event")
    pending = {"claim_token": "delivery_aaaaaaaaaaaa", "revision": 2,
               "event": event, "cursor": "cursor-one", "queued_at": event["occurred_at"]}
    trigger_paths.delivery_waiting(LINEAGE_ID, "wire_aaaaaaaaaaaa", pending)
    state = {"event_wires": {"wire_aaaaaaaaaaaa": {"pending_events": [pending]}}}
    @contextmanager
    def transaction(_):
        yield state
    monkeypatch.setattr(runtime, "_runtime_transaction", transaction)
    monkeypatch.setattr(runtime, "_prune_expired_pending_events", lambda *_args, **_kwargs: None)
    inv = invocation("DeletePendingDelivery" if delete else "UpdatePendingDelivery", {"lineage_id": LINEAGE_ID, "wire_id": "wire_aaaaaaaaaaaa", "delivery_id": "delivery_aaaaaaaaaaaa"})
    result = runtime.update_pending_wire_delivery(inv.argument("lineage_id"), inv.argument("wire_id"), inv.argument("delivery_id"), delete=delete, prompt="new")
    assert result["status"] == ("deleted" if delete else "updated")
    if not delete:
        assert pending["revision"] == 3


@pytest.mark.parametrize("body,expected", [
    (b'{"instruction":"NoSuchInstruction"}', "instruction.unknown"),
    (b'{"instruction":"ListInstructions","extra":1}', "instruction.invalid_arguments"),
    (b'{', "instruction.invalid_arguments"),
])
def test_web_early_instruction_errors_have_v2_envelope(monkeypatch, body, expected):
    """Web 入口在指令执行前发现的错误也以 v2 结果信封返回"""
    from management_portal import asgi
    from tests.cases.test_portal_asgi import _request

    monkeypatch.setattr(asgi, "_instruction_principal", lambda _: ("alice", "web"))
    monkeypatch.setattr(asgi, "_same_origin", lambda _: True)
    response = asyncio.run(asgi.instruction_execute(_request("/api/instructions/execute", method="POST", body=body)))
    payload = json.loads(response.body)
    assert payload["schema"] == "tfi.hatg-instruction-result.v2"
    assert payload["instruction_id"].startswith("inst_ent_")
    assert payload["status"] == "failed" and payload["replayed"] is False
    assert payload["error"]["code"] == expected
