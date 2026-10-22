"""Original idempotent request lookup uses the owner store and receipt authorization."""
from copy import deepcopy
import json
from unittest.mock import MagicMock

import pytest

from management_common.portal_database import portal_database
from management_portal.hatg import routing, socket_rpc
from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import InstructionKernel
from management_portal.hatg.store import ExecutionStore
from tests.cases.test_hatg_isa_kernel import fake, kernel, scheduler_call  # noqa: F401
from tests.cases import permission_test_support


def lookup(arguments, actor="alice"):
    return routing.execute(InstructionCall("GetInstructionExecution", arguments), InstructionContext(actor, "web"))


@pytest.mark.parametrize("lost_response", ["empty", "timeout", "malformed"])
def test_lost_owner_response_retains_queryable_execution_without_replay(kernel, fake, monkeypatch, lost_response):
    """owner 已提交而回执丢失时，入口返回原执行 ID，查询不重放写入。"""
    connection = MagicMock()
    stream = connection.makefile.return_value
    owner_responses = []
    sent = []

    def send(encoded):
        payload = json.loads(encoded)
        sent.append(payload)
        socket_rpc.serve(payload["params"], payload["id"], owner_responses.append,
                         owner="execution", verified_caller="alice")

    connection.sendall.side_effect = send
    if lost_response == "timeout":
        stream.readline.side_effect = TimeoutError("owner committed before response timeout")
    else:
        stream.readline.return_value = b"" if lost_response == "empty" else b"broken response"
    monkeypatch.setattr(socket_rpc, "connect_service", lambda *a, **k: (connection, "test.sock"))
    monkeypatch.setattr(socket_rpc, "_signed", lambda *a: {})
    monkeypatch.setattr(routing, "kernel_for", lambda owner, required=False: kernel if required else None)
    result = routing.execute(scheduler_call(), InstructionContext("alice", "web"))
    assert result.status == "failed" and result.error["commit_state"] == "unknown"
    assert len(sent) == len(owner_responses) == len(fake.calls) == 1
    assert result.instruction_id == sent[0]["params"]["instruction_id"]
    assert result.instruction_id == owner_responses[0]["result"]["instruction_id"]
    connection.close.assert_called_once()
    stream.close.assert_called_once()
    monkeypatch.setattr(routing, "kernel_for", lambda *a, **k: kernel)
    record = lookup({"instruction_id": result.instruction_id}).raise_for_error()
    assert record["status"] == "committed"
    assert lookup({"instruction_id": result.instruction_id}, "outsider").error["code"] == "instruction.not_found"
    assert len(fake.calls) == 1


@pytest.mark.parametrize("stage", ["connect", "sendall", "makefile"])
def test_instruction_transport_distinguishes_unsent_from_unknown_without_retry(monkeypatch, stage):
    """连接前故障才是未提交；发送或读流建立中断保留未知且不重发。"""
    connection = MagicMock()
    connect = MagicMock(return_value=(connection, "test.sock"))
    (connect if stage == "connect" else getattr(connection, stage)).side_effect = OSError("interrupted")
    monkeypatch.setattr(socket_rpc, "connect_service", connect)
    with pytest.raises(InstructionError) as error:
        socket_rpc._open("execution", {"id": "transport-test"}, 1)
    assert error.value.commit_state == ("not_committed" if stage == "connect" else "unknown")
    assert connect.call_count == 1
    assert connection.sendall.call_count == (0 if stage == "connect" else 1)
    assert connection.close.call_count == (0 if stage == "connect" else 1)


@pytest.mark.parametrize("instruction_id", ["inst_lin_" + "a" * 20, "inst_exe_bad", 123, 0, None, []])
def test_owner_rejects_invalid_transport_execution_id_before_mutation(kernel, fake, instruction_id):
    """内部请求的执行 ID 必须具有该 owner 的规范格式。"""
    result = kernel.execute(scheduler_call(), InstructionContext("alice", "web"), instruction_id=instruction_id)
    assert result.error["code"] == "instruction.invalid_arguments"
    assert not fake.calls


def test_lookup_scopes_key_to_actor_and_instruction_without_replay(kernel, fake, monkeypatch):
    """GetInstructionExecution 按调用者和指令查找幂等键对应的原执行，不重放也不改记录"""
    monkeypatch.setattr(routing, "kernel_for", lambda owner, **_: kernel if owner == "execution" else pytest.fail(owner))
    first = kernel.execute(scheduler_call(idempotency_key="shared"), InstructionContext("alice", "web"))
    second = kernel.execute(scheduler_call(idempotency_key="shared"), InstructionContext("bob", "web"))
    before = [deepcopy(kernel.store.get(row.instruction_id)) for row in (first, second)]
    selector = {"instruction": "UpdateProviderAccount", "idempotency_key": "shared"}
    for actor, row in (("alice", first), ("bob", second)):
        observed = lookup(selector, actor).raise_for_error()
        assert observed["instruction_id"] == row.instruction_id
        assert observed == lookup({"instruction_id": row.instruction_id}, actor).raise_for_error()
    assert lookup(selector, "outsider").error["code"] == "instruction.not_found"
    assert lookup({**selector, "idempotency_key": "absent"}).error["code"] == "instruction.not_found"
    assert kernel.store.find_instruction_id(caller="alice", instruction="OtherInstruction", request_key="shared") == ""
    assert len(fake.calls) == 2
    assert before == [kernel.store.get(row.instruction_id) for row in (first, second)]


def test_same_actor_key_on_different_instructions_resolves_each_original(kernel, fake, monkeypatch):
    """同一调用者在不同指令上用相同幂等键时分别查到各自的原执行"""
    monkeypatch.setattr(routing, "kernel_for", lambda owner, **_: kernel if owner == "execution" else pytest.fail(owner))
    context = InstructionContext("alice", "web")
    first = kernel.execute(scheduler_call(idempotency_key="same-key"), context)
    second = kernel.execute(InstructionCall("UpdateModelApi", {"api_id": "fixture", "changes": {}},
                                           idempotency_key="same-key"), context)
    assert first.status == second.status == "committed" and first.instruction_id != second.instruction_id
    for result in (first, second):
        found = lookup({"instruction": result.instruction, "idempotency_key": "same-key"}).raise_for_error()
        assert found["instruction_id"] == result.instruction_id
    assert len(fake.calls) == 2


@pytest.mark.parametrize("selector", [
    {}, {"instruction": "TriggerEvent"}, {"idempotency_key": "key"},
    {"instruction": "TriggerEvent", "idempotency_key": "   "},
    {"instruction_id": "inst_lin_original", "instruction": "TriggerEvent", "idempotency_key": "key"},
    {"instruction_id": "inst_lin_original", "instruction": "TriggerEvent"},
    {"instruction_id": "inst_lin_original", "idempotency_key": "key"},
    {"instruction": "TriggerEvent", "idempotency_key": "x" * 241},
])
def test_invalid_selector_never_reaches_owner(selector, monkeypatch):
    """GetInstructionExecution 的查询条件不合法时在入口拒绝，不到达 owner"""
    monkeypatch.setattr(routing, "kernel_for", lambda *_args, **_kwargs: pytest.fail("invalid selector reached owner"))
    assert lookup(selector).error["code"] == "instruction.invalid_arguments"
    assert declaration("GetInstructionExecution").schema()["one_of"] == [
        ["instruction_id"], ["instruction", "idempotency_key"]]


def test_key_lookup_routes_to_original_owner_over_socket_without_mutation(kernel, fake, monkeypatch):
    """按幂等键查询经 socket 路由到原指令所属 owner，不产生新执行"""
    from management_portal.hatg import socket_rpc
    original = kernel.execute(scheduler_call(idempotency_key="remote"), InstructionContext("alice", "web"))
    selector = {"instruction": "UpdateProviderAccount", "idempotency_key": "remote"}
    routes = []
    monkeypatch.setattr(routing, "kernel_for", lambda owner, required=False: kernel if required else None)
    def remote(owner, call, context):
        routes.append((owner, call, context))
        return routing.execute_local(call, context, owner=owner)
    monkeypatch.setattr(socket_rpc, "call_remote", remote)
    result = lookup(selector).raise_for_error()
    assert result["instruction_id"] == original.instruction_id
    assert len(routes) == 1 and routes[0][0] == "execution"
    assert routes[0][1].instruction == "GetInstructionExecution" and routes[0][1].arguments == selector
    assert routes[0][2].caller == "alice" and len(fake.calls) == 1
    mismatch = routing.execute_local(InstructionCall("GetInstructionExecution", selector),
                                    InstructionContext("alice", "web"), owner="lineage")
    assert mismatch.error["code"] == "instruction.not_found"


def test_missing_expired_or_read_only_request_never_creates_execution(kernel, fake, monkeypatch):
    """幂等键不存在、已过期或属于读指令时查询返回不存在且不创建执行"""
    monkeypatch.setattr(routing, "kernel_for", lambda *_args, **_kwargs: kernel)
    first = kernel.execute(scheduler_call(idempotency_key="expired"), InstructionContext("alice", "web"))
    assert kernel.store.prune(now="2099-01-01T00:00:00Z") == 1
    selector = {"instruction": "UpdateProviderAccount", "idempotency_key": "expired"}
    assert lookup(selector).error["code"] == "instruction.not_found"
    assert lookup({"instruction": "GetUsage", "idempotency_key": "read"}).error["code"] == "instruction.not_found"
    assert kernel.store.find_instruction_id(caller="alice", instruction=selector["instruction"], request_key="expired") == ""
    assert len(fake.calls) == 1


def test_key_lookup_authorizes_persisted_lineage_before_settling_interrupted_owner(monkeypatch):
    """按幂等键查询先按原执行保存的 Lineage 授权，通过后才把中断的执行结算为失败"""
    from management_portal.society_service.isa import binding
    lineage_id = "lineage_aaaaaaaaaaaa"
    decoy = "lineage_bbbbbbbbbbbb"
    context = InstructionContext("bob", "web")
    owner_binding = binding()
    owner_binding.handlers = dict(owner_binding.handlers)
    effects = []
    def crash(inv):
        effects.append(inv.instruction_id)
        raise SystemExit("owner exited after acceptance")
    owner_binding.handlers["TriggerEvent"] = crash
    kernel = InstructionKernel(owner_binding, store=ExecutionStore(portal_database(schema="lookup_auth")))
    monkeypatch.setattr(routing, "kernel_for", lambda owner, **_: kernel if owner == "lineage" else pytest.fail(owner))
    with pytest.raises(SystemExit):
        kernel.execute(InstructionCall("TriggerEvent", {"lineage_id": lineage_id, "event_source_id": "event_source_fixture"},
                                       idempotency_key="authorization"), context)
    original, = effects
    assert kernel.store.get(original)["status"] == "executing"
    permissions = permission_test_support.enable(monkeypatch)
    deny = permissions.deny("bob", lineage_id)
    # The selector names no Lineage: the receipt's own Lineage decides.
    selector = {"instruction": "TriggerEvent", "idempotency_key": "authorization"}
    assert lookup({**selector, "lineage_id": decoy}, "bob").error["code"] == "instruction.invalid_arguments"
    assert lookup(selector, "bob").error["code"] == "instruction.not_found"
    assert kernel.store.get(original)["status"] == "executing"  # Denial must not settle the receipt.
    assert lookup(selector, "alice").error["code"] == "instruction.not_found"  # Nobody selects another actor's key.
    store = permissions.store
    with store.transaction() as connection:
        store.delete(connection, where="entry_id = %s", parameters=(deny.entry_id,))
    found = lookup(selector, "bob").raise_for_error()
    assert found["instruction_id"] == original and found["status"] == "failed"
    assert found["error"]["details"]["reason"] == "interrupted" and found["error"]["commit_state"] == "unknown"
    assert effects == [original]
