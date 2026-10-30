"""GetMoveOperation: reading a Resident move's workspace transfer (decision_6591e7793116)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.society_service import (
    invariant_locks,
    lineage_domain,
    lineage_reads,
    lineage_state,
    resident_lifecycle_steps_domain,
    resident_record_domain,
)
from management_portal.society_service import lineage_aggregate
from tests.cases.isa_test_support import run
from tests.cases.test_hatg_move_resident import ACTOR, execute, graph, request, teams  # noqa: F401 - teams is a fixture
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401 - fixture


@pytest.fixture
def transfer(teams):
    result = execute(request(teams))
    assert result.status == "committed", result
    return result


def query(operation_id, **options):
    return run("GetMoveOperation", {"operation_id": operation_id}, key="same-poll-key", **options)


def observed(operation_id, **options):
    return query(operation_id, **options).raise_for_error()


def test_operation_pending_completed_and_polling_preserve_the_original_move(teams, transfer):
    """GetMoveOperation 依次报告迁移的待处理和完成状态，轮询不改变两边 Lineage"""
    source, target, member = teams
    operation_id = transfer.result["operation_id"]
    graphs = [graph(source), graph(target)]
    first = query(operation_id)
    assert first.result == {
        "operation_id": operation_id, "resident_id": member["resident_id"],
        "operation_type": "workspace_transfer", "phase": "workspace_move",
        "status": "pending", "terminal": False, "error": None, "retry_at": None,
        "completed_steps": 0, "total_steps": 4,
        "source_lineage_id": source["lineage_id"], "target_lineage_id": target["lineage_id"],
        "instruction_id": transfer.instruction_id,
    }
    operations = resident_record_domain.lifecycle_operations()
    assert operations.run(operation_id, resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == member["resident_id"]
    completed = query(operation_id)
    assert completed.result["terminal"] is True
    assert completed.result["status"] == completed.result["phase"] == "completed"
    assert completed.result["error"] is None
    # A read keeps no execution record, so a repeated key never replays an old snapshot.
    assert not first.replayed and not completed.replayed
    assert query(operation_id).result == completed.result
    assert [graph(source), graph(target)] == graphs


@pytest.mark.parametrize("failure_index", [0, 1, 2, 3])
def test_operation_reports_retry_phase_and_error_without_running_work(teams, transfer, failure_index):
    """迁移步骤失败后 GetMoveOperation 报告重试阶段、已完成步数和错误，查询本身不执行步骤"""
    operation_id = transfer.result["operation_id"]
    operations = resident_record_domain.lifecycle_operations()
    calls = []

    def fail_at_step(effect_id, step):
        calls.append(step["kind"])
        if len(calls) == failure_index + 1:
            raise RuntimeError("workspace service unavailable")

    assert operations.run(operation_id, fail_at_step) == ""
    result = observed(operation_id)
    assert result["terminal"] is False
    assert result["status"] == "retrying"
    assert result["phase"] == calls[-1]
    assert result["completed_steps"] == failure_index
    assert result["error"] == {"message": "RuntimeError: workspace service unavailable", "retryable": True}
    assert result["retry_at"] is not None
    assert observed(operation_id) == result
    assert len(calls) == failure_index + 1


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize("completed", [False, True])
def test_operation_rechecks_both_read_grants_on_every_poll(teams, transfer, monkeypatch, side, completed):
    """每次查询迁移操作都重新检查源和目标 Lineage 的读取权限"""
    operation_id = transfer.result["operation_id"]
    if completed:
        resident_record_domain.lifecycle_operations().run(operation_id, lambda *_: None)
    observed(operation_id)
    blocked = teams[side]["lineage_id"]
    ensure_visible = lineage_state._ensure_visible

    def check(lineage, actor):
        if lineage["lineage_id"] == blocked:
            raise PermissionError("无权访问该 Lineage")
        return ensure_visible(lineage, actor)

    monkeypatch.setattr(lineage_state, "_ensure_visible", check)
    denied = query(operation_id)
    assert denied.status == "failed" and denied.error["code"] == "instruction.forbidden"


def test_operation_is_read_with_the_calling_residents_own_grants(teams, transfer, monkeypatch):
    """Resident 查询迁移操作时按它自己的权限检查"""
    principal = teams[2]["resident_id"]
    seen = []

    def check(lineage, actor):
        seen.append(actor)
        if actor == principal:
            raise PermissionError("无权访问该 Lineage")

    monkeypatch.setattr(lineage_state, "_ensure_visible", check)
    denied = query(transfer.result["operation_id"], actor=principal, entry="harness")
    assert denied.status == "failed" and "无权访问该 Lineage" in denied.error["message"]
    assert seen == [principal]


def test_operation_needs_read_permission_not_modify_permission(teams, transfer, monkeypatch):
    """查询迁移操作只需要两边 Lineage 的读取权限，不要求修改权限"""
    checked = set()

    def read_only(lineage, actor, **options):
        if options.get("operation") == "read":
            checked.add(lineage["lineage_id"])
            return True
        return False

    monkeypatch.setattr(lineage_state, "_actor_can_manage_lineage", read_only)
    monkeypatch.setattr(lineage_reads, "_actor_can_manage_lineage", read_only)
    monkeypatch.setattr(lineage_domain, "_actor_can_manage_lineage", read_only)
    monkeypatch.setattr(lineage_aggregate, "_actor_can_manage_lineage", read_only)
    assert observed(transfer.result["operation_id"])["terminal"] is False
    assert checked == {teams[0]["lineage_id"], teams[1]["lineage_id"]}


def test_operation_is_queryable_after_the_source_becomes_empty(teams, transfer):
    """源 Lineage 迁空后仍能查询最后一个成员的迁移操作"""
    operations = resident_record_domain.lifecycle_operations()
    operations.run(transfer.result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    call = request((teams[0], teams[1], teams[0]), key="move-last-member")
    call["arguments"]["allow_empty_source"] = True
    last = execute(call).raise_for_error()
    assert graph(teams[0])["nodes"] == []
    assert observed(last["operation_id"])["terminal"] is False
    operations.run(last["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    assert observed(last["operation_id"])["terminal"] is True
    assert graph(teams[0])["nodes"] == []


def test_operation_requires_a_move_receipt_even_if_the_outbox_has_a_matching_id(teams):
    """没有迁移回执的 outbox 操作 ID 在 GetMoveOperation 中报告不存在"""
    operations = resident_record_domain.lifecycle_operations()
    operation_id = operations.enqueue(teams[2]["resident_id"], [{"kind": "workspace_move"}])
    assert query(operation_id).error["code"] == "instruction.not_found"
    assert operations.pending(teams[2]["resident_id"])


@pytest.mark.parametrize("operation_id", ["resident-operation_" + "0" * 24, "resident-operation_" + "f" * 24])
def test_unknown_operation_is_not_reported_as_completed(teams, operation_id):
    """不存在的迁移操作 ID 报告不存在，不报告为已完成"""
    assert query(operation_id).error["code"] == "instruction.not_found"


@pytest.mark.parametrize("operation_id", ["../../escape", "resident-operation_123", "resident-operation_" + "A" * 24])
def test_operation_requires_exact_identity(teams, operation_id):
    """格式不符的迁移操作 ID 以参数错误拒绝"""
    assert query(operation_id).error["code"] == "instruction.invalid_arguments"


def test_operation_read_does_not_wait_for_transfer_or_graph_lock(teams, transfer):
    """查询迁移操作不等待正在执行的转移步骤或 Lineage 图锁"""
    entered, release = threading.Event(), threading.Event()
    operations = resident_record_domain.lifecycle_operations()

    def slow_step(*_):
        entered.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(operations.run, transfer.result["operation_id"], slow_step)
        try:
            assert entered.wait(2)
            with invariant_locks.lineage_scope(teams[0]["lineage_id"], operation="test.operation.query"):
                result = pool.submit(observed, transfer.result["operation_id"]).result(timeout=2)
            assert result["terminal"] is False
            assert result["phase"] == "workspace_move"
        finally:
            release.set()
        assert worker.result(timeout=2) == teams[2]["resident_id"]


def test_operation_is_not_terminal_until_outbox_completion_commits(teams, transfer, monkeypatch):
    """所有步骤执行完但 outbox 完成尚未提交时，迁移操作处于 finalizing 而非终态"""
    entered, release = threading.Event(), threading.Event()
    operations = resident_record_domain.lifecycle_operations()
    complete = operations.complete

    def paused_complete(operation_id):
        entered.set()
        assert release.wait(5)
        complete(operation_id)

    monkeypatch.setattr(operations, "complete", paused_complete)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(operations.run, transfer.result["operation_id"], lambda *_: None)
        try:
            assert entered.wait(2)
            result = observed(transfer.result["operation_id"])
            assert result["completed_steps"] == result["total_steps"]
            assert result["phase"] == "finalizing"
            assert result["terminal"] is False
        finally:
            release.set()
        assert worker.result(timeout=2) == teams[2]["resident_id"]
    assert observed(transfer.result["operation_id"])["terminal"] is True


@pytest.mark.parametrize("entry,caller,fork_mode,named,expected", [
    # Without a named account every caller leaves the route to the domain,
    # which inherits the source Resident's current account.
    ("web", "alice", "compacted", "", None),
    ("web", "alice", "message", "", None),
    ("lineage", "alice", "recent-5", "", None),
    ("web", "alice", "full", "", None),
    ("harness", "resident_cccccccccccc", "compacted", "", None),
    # Only an explicitly named account changes the route.
    ("web", "alice", "compacted", "acct_named", "acct_named"),
])
def test_fork_keeps_the_source_account_unless_one_is_named(monkeypatch, entry, caller, fork_mode, named, expected):
    """ForkResident 未指定账号时沿用源 Resident 的账号，只有显式指定才改用其他账号"""
    from types import SimpleNamespace

    from management_portal.hatg.catalog import declaration
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation
    from management_portal.society_service.isa import common, residents

    payloads = []
    source = "resident_aaaaaaaaaaaa"
    monkeypatch.setattr(residents, "lineage_of_resident", lambda _: "lineage_aaaaaaaaaaaa")
    monkeypatch.setattr(residents, "placement", lambda *_: {})
    monkeypatch.setattr(lineage_aggregate, "read_lineage", lambda lineage_id, actor: {"program": {}})
    monkeypatch.setattr(residents, "lineage_branches", lambda: SimpleNamespace(
        fork_lineage_resident=lambda lineage_id, payload, actor: payloads.append(payload) or {"created_resident": {"id": "resident_bbbbbbbbbbbb"}}))
    # GetResident's view: the account a Resident runs under is its "account".
    records = {source: {"provider": "codex", "account": "acct_source"},
               "resident_cccccccccccc": {"provider": "codex", "account": "acct_caller"}}
    fake_protocol = SimpleNamespace(get_resident=lambda resident_id, actor: records[resident_id])
    monkeypatch.setattr(residents, "protocol", lambda: fake_protocol)
    monkeypatch.setattr(common, "protocol", lambda: fake_protocol)
    arguments = {"lineage_id": "lineage_aaaaaaaaaaaa", "source_resident_id": source, "fork_mode": fork_mode}
    if fork_mode == "message":
        arguments["message"] = "msg_aaaaaaaaaaaa"
    if named:
        arguments["account_id"] = named
    invocation = Invocation(
        declaration=declaration("ForkResident"),
        call=InstructionCall.from_payload({"instruction": "ForkResident", "arguments": arguments}),
        context=InstructionContext(caller=caller, entry=entry),
        instruction_id="instruction_111111111111",
    )
    residents.fork_resident(invocation)
    assert payloads[0].get("backend_route_id") == expected


def test_get_resident_names_the_provider_and_account_create_resident_takes(monkeypatch):
    """GetResident 返回 CreateResident 接受的 Provider 与账号

    provider_api Resident 读回 provider=provider_api、account=它的模型 API，其他 Provider 不变；Resident 调用者因此能按同一 Provider 继承自己的账号。

    A provider_api Resident reads back as provider ``provider_api`` with its model API as the account,
    so a Resident caller of that kind inherits its account for the same provider."""
    from types import SimpleNamespace

    from management_portal.society_service import resident_protocol
    from tests.cases.model_api_test_support import KIMI_PRIMARY, register_accounts, register_apis

    register_apis(KIMI_PRIMARY)
    register_accounts({"account_id": "kimi_primary", "provider": "provider_api", "kind": "api_profile",
                       "route_id": "kimi_primary", "config": {"api_id": "kimi_primary"},
                       "max_concurrency": 8, "enabled": True})
    monkeypatch.setattr(resident_protocol, "_workspace_views", lambda record: [])
    for backend, route, expected in (("provider_api", "kimi_primary", ("provider_api", "kimi_primary")),
                                     ("kimi", "kimi_default", ("kimi", "kimi_default"))):
        record = {"resident_id": "resident_aaaaaaaaaaaa", "lineage_id": "lineage_aaaaaaaaaaaa", "title": "R",
                  "backend": backend, "backend_route_id": route}
        view = resident_protocol._resident_view(SimpleNamespace(record=record, resident_id="resident_aaaaaaaaaaaa"))
        assert (view["provider"], view["account"]) == expected
