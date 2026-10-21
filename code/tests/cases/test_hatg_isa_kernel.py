"""Instruction kernel and routing (decision_898b8911a2ff “指令格式”“执行原则”“执行位置”).

A fake ExecutionService binding stands in for a real owner so the kernel's own
rules are tested without domain behavior: optional idempotency, optional
version tokens, streaming, argument validation and routing to the owner.

:module: isa.instructions
:importance: P2
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from management_common.portal_database import portal_database
from management_portal.hatg import kernel as kernel_module, routing
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.kernel import InstructionKernel, OwnerBinding
from management_portal.hatg.store import ExecutionStore


OWNER = "execution"
CONTEXT = InstructionContext(caller="alice", entry="web")


class FakeExecution:
    """Handlers for every instruction ExecutionService owns; each call is counted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.versions: dict[str, Any] = {}

    def handler(self, name: str):
        def run(invocation) -> dict[str, Any]:
            self.calls.append((name, dict(invocation.arguments)))
            return {"call": len(self.calls), "account_id": invocation.arguments.get("account_id", "")}

        return run

    def binding(self) -> OwnerBinding:
        names = [name for name, item in instruction_catalog().items() if item.owner == OWNER]
        return OwnerBinding(
            owner=OWNER,
            handlers={name: self.handler(name) for name in names},
            version_of=lambda invocation, object_id: self.versions.get(object_id),
        )


@pytest.fixture
def fake():
    return FakeExecution()


@pytest.fixture
def kernel(fake):
    store = ExecutionStore(portal_database(schema=f"isa_kernel_{uuid.uuid4().hex[:8]}"))
    return InstructionKernel(fake.binding(), store=store, authorize=lambda *args: None)


def scheduler_call(**controls: Any) -> InstructionCall:
    return InstructionCall.from_payload({
        "instruction": "UpdateProviderAccount",
        "arguments": {
            "account_id": "acct_one", "max_concurrency": 2,
            "dispatch_interval_min_seconds": 1, "dispatch_interval_max_seconds": 5,
        },
        **controls,
    })


def test_mutation_without_idempotency_key_runs_every_time(kernel, fake):
    """不带 idempotency_key 的写指令每次调用都执行并得到新的 instruction_id"""
    first = kernel.execute(scheduler_call(), CONTEXT)
    second = kernel.execute(scheduler_call(), CONTEXT)
    assert (first.status, second.status) == ("committed", "committed")
    assert not first.replayed and not second.replayed
    assert len(fake.calls) == 2
    assert first.instruction_id != second.instruction_id
    assert first.instruction_id.startswith("inst_exe_")


def test_same_idempotency_key_replays_the_first_result(kernel, fake):
    """相同 idempotency_key 的重复调用回放首次结果，不再执行处理函数"""
    first = kernel.execute(scheduler_call(idempotency_key="k-1"), CONTEXT)
    again = kernel.execute(scheduler_call(idempotency_key="k-1"), CONTEXT)
    assert first.status == again.status == "committed"
    assert again.replayed is True
    assert again.instruction_id == first.instruction_id
    assert len(fake.calls) == 1


def test_same_key_with_different_arguments_is_an_idempotency_conflict(kernel, fake):
    """相同 idempotency_key 配不同参数时返回 instruction.idempotency_conflict"""
    kernel.execute(scheduler_call(idempotency_key="k-2"), CONTEXT)
    changed = scheduler_call(idempotency_key="k-2")
    changed = InstructionCall(
        instruction=changed.instruction,
        arguments={**changed.arguments, "max_concurrency": 9},
        idempotency_key=changed.idempotency_key,
    )
    result = kernel.execute(changed, CONTEXT)
    assert result.status == "failed"
    assert result.error["code"] == "instruction.idempotency_conflict"
    assert len(fake.calls) == 1


def test_keys_are_per_caller(kernel, fake):
    """idempotency_key 按调用者区分，不同调用者用同一个键各自执行"""
    kernel.execute(scheduler_call(idempotency_key="shared"), CONTEXT)
    other = kernel.execute(scheduler_call(idempotency_key="shared"), InstructionContext(caller="bob", entry="web"))
    assert other.status == "committed" and not other.replayed
    assert len(fake.calls) == 2


def test_expected_version_is_optional_and_compared_when_given(kernel, fake):
    """给出 expected_version 时与当前版本比较，过期版本返回冲突且不执行"""
    fake.versions["acct_one"] = 7
    stale = kernel.execute(scheduler_call(expected_version={"acct_one": 6}), CONTEXT)
    assert stale.status == "failed"
    assert stale.error["code"] == "instruction.version_conflict"
    assert stale.error["details"]["current_version"] == 7
    assert fake.calls == []
    current = kernel.execute(scheduler_call(expected_version={"acct_one": 7}), CONTEXT)
    assert current.status == "committed"
    # An object whose owner compares its own token is passed through.
    delegated = kernel.execute(scheduler_call(expected_version={"acct_two": "opaque"}), CONTEXT)
    assert delegated.status == "committed"
    assert len(fake.calls) == 2


def test_reads_run_without_execution_records(kernel, fake):
    """读指令不记录执行，带 idempotency_key 也每次重新执行"""
    result = kernel.execute(InstructionCall.from_payload({
        "instruction": "GetUsage", "arguments": {"period": "today", "group_by": "resident"},
        "idempotency_key": "ignored-for-reads",
    }), CONTEXT)
    again = kernel.execute(InstructionCall.from_payload({
        "instruction": "GetUsage", "arguments": {"period": "today", "group_by": "resident"},
        "idempotency_key": "ignored-for-reads",
    }), CONTEXT)
    assert result.status == again.status == "committed"
    assert not again.replayed
    assert len(fake.calls) == 2


def test_arguments_are_validated_before_the_handler(kernel, fake):
    """缺少必填参数或带未知参数的调用在进入处理函数前被拒绝"""
    missing = kernel.execute(InstructionCall.from_payload({
        "instruction": "UpdateProviderAccount", "arguments": {"account_id": "acct_one"},
    }), CONTEXT)
    unknown = kernel.execute(InstructionCall.from_payload({
        "instruction": "GetUsage", "arguments": {"period": "today", "group_by": "resident", "extra": 1},
    }), CONTEXT)
    assert missing.error["code"] == unknown.error["code"] == "instruction.invalid_arguments"
    assert fake.calls == []


def test_kernel_rejects_bindings_that_miss_or_borrow_instructions(fake):
    """指令内核拒绝缺少自身指令实现或实现了其他 owner 指令的绑定"""
    binding = fake.binding()
    binding.handlers.pop("GetUsage")
    with pytest.raises(RuntimeError, match="GetUsage"):
        InstructionKernel(binding, store=None)
    binding = fake.binding()
    binding.handlers["GetLineage"] = lambda invocation: {}
    with pytest.raises(RuntimeError, match="GetLineage"):
        InstructionKernel(binding, store=None)


def test_a_handler_for_an_instruction_the_catalog_removed_is_ignored(fake, caplog):
    """目录已删除的指令的处理函数只记警告，不阻止内核构建"""
    # Release Web can be older than the shared catalog (OpenDirectChatroom, 2026-10-03).
    binding = fake.binding()
    binding.handlers["RetiredInstruction"] = lambda invocation: {}
    with caplog.at_level("WARNING", logger=kernel_module.__name__):
        InstructionKernel(binding, store=None)
    assert "RetiredInstruction" in caplog.text


def test_release_web_starts_without_a_handler_the_shared_catalog_already_declares(kernel, fake, caplog, monkeypatch):
    """Release Web 缺少共享目录新声明指令的实现时仍能启动

    Release Web 使用 Dev 工作树的共享指令目录；目录先声明、处理函数尚未发布的 Web 指令不阻止启动，调用返回 instruction.owner_unavailable；运行自身目录的进程仍拒绝启动。
    """
    # Release Web runs a published commit against the catalog in the Dev tree;
    # an instruction declared there before its handler shipped stopped
    # Release Web from starting (2026-10-02).  Only Release reports it as
    # unavailable; a tree running its own catalog still refuses to start.
    import sys

    class SharedSourceFinder:
        def find_spec(self, *args, **kwargs):
            return None

    monkeypatch.setattr(sys, "meta_path", [SharedSourceFinder(), *sys.meta_path])
    binding = fake.binding()
    binding.handlers.pop("GetUsage")
    with caplog.at_level("WARNING", logger=kernel_module.__name__):
        release = InstructionKernel(binding, store=kernel.store, authorize=lambda *args: None)
    assert "GetUsage" in caplog.text
    assert "GetUsage" not in binding.handlers
    result = release.execute(InstructionCall.from_payload({
        "instruction": "GetUsage", "arguments": {"period": "today", "group_by": "resident"},
    }), CONTEXT)
    assert result.status == "failed"
    assert result.error["code"] == "instruction.owner_unavailable"
    assert fake.calls == []


def test_unknown_instruction_is_reported_by_the_entry():
    """调用不存在的指令时入口返回 instruction.unknown"""
    result = routing.execute({"instruction": "NoSuchInstruction", "arguments": {}}, CONTEXT)
    assert result.status == "failed"
    assert result.error["code"] == "instruction.unknown"


@pytest.mark.parametrize("refusal", [
    "instruction_error",
    "permission_error",
])
def test_authorization_denial_stays_closed_without_retry(kernel, fake, monkeypatch, refusal):
    """权限端口拒绝时不执行 owner 且不可重试

    内核在执行前向权限端口提问；端口抛出 instruction.forbidden 或 PermissionError 时，调用以不可重试的 instruction.forbidden 失败，owner 不执行，幂等键不被占用，权限允许后用同一个键重试只执行一次。

    :importance: P1
    :design: decision_b820faf42218
    """
    from management_portal import permission_system
    from management_portal.hatg.contracts import InstructionError

    def refuse(*args, **kwargs):
        if refusal == "instruction_error":
            raise InstructionError("instruction.forbidden", "Denied")
        raise PermissionError("Denied")

    monkeypatch.setattr(kernel, "_authorize", None)
    monkeypatch.setattr(permission_system, "authorize_instruction", refuse)
    call = scheduler_call(idempotency_key="after-permission-granted")
    result = kernel.execute(call, CONTEXT)
    assert result.status == "failed"
    assert result.error["code"] == "instruction.forbidden"
    assert result.error["retryable"] is False
    assert result.error["commit_state"] == "not_committed"
    assert fake.calls == []

    monkeypatch.setattr(permission_system, "authorize_instruction", lambda *args, **kwargs: None)
    allowed = kernel.execute(call, CONTEXT)
    assert allowed.status == "committed"
    assert len(fake.calls) == 1


def test_routing_sends_each_call_to_its_owner(monkeypatch, kernel, fake):
    """ISA 路由把每个调用发给指令所属 owner 的内核，socket 模式下走远程调用"""
    monkeypatch.setattr(kernel_module, "_KERNELS", {OWNER: kernel})
    result = routing.execute(scheduler_call(), CONTEXT)
    assert result.status == "committed"
    assert fake.calls[0][0] == "UpdateProviderAccount"

    remote = []
    monkeypatch.setenv("PORTAL_ISA_ROUTING", "socket")
    from management_portal.hatg import socket_rpc

    def call_remote(owner, call, context):
        remote.append((owner, call.instruction, context.caller))
        return kernel.execute(call, context)

    monkeypatch.setattr(socket_rpc, "call_remote", call_remote)
    routing.execute({"instruction": "GetLineage", "arguments": {"lineage_id": "lineage_aaaaaaaaaaaa"}}, CONTEXT)
    routing.execute({"instruction": "ResolveResource", "arguments": {"address": "knowledge_aaaaaaaaaaaa"}}, CONTEXT)
    assert remote == [("lineage", "GetLineage", "alice"), ("resource", "ResolveResource", "alice")]


def test_clear_mailbox_runs_where_its_target_mailbox_is_held(monkeypatch):
    """ClearMailbox 带 resident_id 在 SocietyService 执行，带 user_id 在 Web 执行，owner 不符的本地调用被拒绝"""
    from management_portal.hatg import socket_rpc
    from management_portal.hatg.declarations import InstructionDeclaration, P

    declaration = instruction_catalog()["ClearMailbox"]
    assert declaration.owners == ("web", "lineage")
    assert declaration.owner_for({"resident_id": "resident_aaaaaaaaaaaa"}) == "lineage"
    assert declaration.owner_for({"user_id": "alice", "mode": "all"}) == "web"
    assert declaration.schema()["argument_owners"] == [{"argument": "resident_id", "owner": "lineage"}]
    with pytest.raises(RuntimeError, match="未声明参数"):
        InstructionDeclaration(name="X", category="conversation", owner="web", description="d",
                               parameters=(P("a"),), argument_owners=(("b", "lineage"),))

    remote = []
    monkeypatch.setattr(kernel_module, "_KERNELS", {})
    monkeypatch.setenv("PORTAL_ISA_ROUTING", "socket")
    monkeypatch.setattr(socket_rpc, "call_remote", lambda owner, call, context: remote.append(owner) or "sent")
    assert routing.execute({"instruction": "ClearMailbox", "arguments": {"resident_id": "resident_aaaaaaaaaaaa"}}, CONTEXT) == "sent"
    assert routing.execute({"instruction": "ClearMailbox", "arguments": {"user_id": "alice", "mode": "all"}}, CONTEXT) == "sent"
    assert remote == ["lineage", "web"]
    refused = routing.execute_local(InstructionCall.from_payload(
        {"instruction": "ClearMailbox", "arguments": {"resident_id": "resident_aaaaaaaaaaaa"}}), CONTEXT, owner="web")
    assert refused.status == "failed" and "不由 web 执行" in refused.error["message"]

    from types import SimpleNamespace
    from management_portal.hatg.contracts import InstructionError
    from management_portal.society_service.isa import execution

    arguments = {"resident_id": "resident_aaaaaaaaaaaa", "mode": "all"}
    with pytest.raises(InstructionError, match="只用于清理 User Mailbox"):
        execution.clear_mailbox(SimpleNamespace(argument=arguments.get))


def test_self_description_is_answered_by_the_entry(monkeypatch):
    """ListInstructions 和 GetInstructionSchema 由入口直接应答并列出完整目录"""
    monkeypatch.setattr(kernel_module, "_KERNELS", {})
    monkeypatch.setenv("PORTAL_ISA_ROUTING", "socket")
    listing = routing.execute({"instruction": "ListInstructions", "arguments": {}}, CONTEXT)
    assert listing.status == "committed"
    names = {item["instruction"] if "instruction" in item else item["name"] for item in listing.result["instructions"]}
    # The entry lists the whole catalog, whatever instructions it declares.
    assert names == set(instruction_catalog())
    schema = routing.execute({"instruction": "GetInstructionSchema", "arguments": {"instruction": "WatchTriggerPath"}}, CONTEXT)
    assert schema.status == "committed"
    assert schema.result["schema"]["streaming"] is True


def test_streaming_instructions_only_run_through_the_stream_entry(monkeypatch):
    """流式指令只能经流式入口调用，普通入口拒绝流式指令，流式入口拒绝普通指令"""
    batches = [[{"cursor": "1", "type": "invocation"}], [{"cursor": "2", "type": "run_finished"}]]
    names = [name for name, item in instruction_catalog().items() if "lineage" in item.owners]
    binding = OwnerBinding(
        owner="lineage",
        handlers={name: (lambda invocation: {}) for name in names if not instruction_catalog()[name].streaming},
        streams={name: (lambda invocation: iter(batches)) for name in names if instruction_catalog()[name].streaming},
    )
    lineage_kernel = InstructionKernel(binding, store=None, authorize=lambda *args: None)
    monkeypatch.setattr(kernel_module, "_KERNELS", {"lineage": lineage_kernel})
    call = {"instruction": "WatchTriggerPath", "arguments": {"lineage_id": "lineage_aaaaaaaaaaaa", "trigger_path_id": "run_aaaaaaaaaaaa"}}
    assert list(routing.stream(call, CONTEXT)) == batches
    direct = routing.execute(call, CONTEXT)
    assert direct.status == "failed"
    assert direct.error["code"] == "instruction.invalid_arguments"
    with pytest.raises(Exception):
        routing.stream({"instruction": "GetLineage", "arguments": {"lineage_id": "lineage_aaaaaaaaaaaa"}}, CONTEXT)


@pytest.mark.parametrize("code,status,expected", [
    ("authorization.version_conflict", 409, "instruction.version_conflict"),
    ("credentials_unavailable", 503, "instruction.failed"),
    ("authorization.invalid_principal", 400, "instruction.invalid_arguments"),
    ("authorization.denied", 403, "instruction.forbidden"),
    ("resource.missing", 404, "instruction.not_found"),
    ("resource.busy", 409, "instruction.conflict"),
    ("storage.broken", 500, "instruction.failed"),
])
def test_owner_error_codes_outside_the_vocabulary_are_mapped_and_kept_as_reason(code, status, expected):
    """owner 返回的非标准错误码映射到 ISA 错误码，原错误码保留在 reason"""
    from management_common.operation_errors import OperationError
    from management_portal.hatg.contracts import ERROR_STATUS, InstructionError, normalize_error

    for raised in (OperationError("owner failure", code=code, http_status=status),
                   InstructionError(code, "owner failure", http_status=status)):
        error = normalize_error(raised)
        assert error.code == expected and error.code in ERROR_STATUS
        assert error.details["reason"] == code
        assert error.http_status == ERROR_STATUS[expected]


def test_an_interrupted_execution_is_reported_unknown_not_rerun(kernel, fake):
    """执行中断的写指令重试时报告提交状态未知，不重新执行"""
    call = scheduler_call(idempotency_key="interrupted")
    first = kernel.execute(call, CONTEXT)
    assert first.status == "committed" and len(fake.calls) == 1
    # Simulate a process that died just after its business side effect.
    kernel.store.database.write(lambda connection: connection.execute(
        "UPDATE instruction_executions SET status='executing' WHERE instruction_id=%s",
        (first.instruction_id,),
    ))
    retried = kernel.execute(call, CONTEXT)
    assert len(fake.calls) == 1
    assert retried.status == "failed" and retried.replayed
    assert retried.error["commit_state"] == "unknown"
    assert retried.error["details"]["reason"] == "interrupted"
    again = kernel.execute(call, CONTEXT)
    assert again.error == retried.error and len(fake.calls) == 1
