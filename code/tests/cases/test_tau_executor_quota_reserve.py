"""A TAU executor on an account at its quota reserve neither hangs nor is orphaned.

In a full TAU run account five reached its quota reserve, which closes
intake on every Resident of the account.  Preparing a module saw its closed
executor, restarted it to no effect and replaced it; the case then failed
with "账号配置尚未就绪" because the successor was never created, and the old
executor was orphaned.  Other modules created fresh executors whose case
messages queued behind the closed intake for hours with no output.

Preparation now stops at once with the reason when the account's quota is
reserved, and a module executor that was replaced for another reason gets its
successor created and prepared before the case is sent.

:module: operations.test-workbench
"""
from __future__ import annotations

from unittest import mock

import pytest

from tests.cases.test_tau_runtime import configured  # noqa: F401 - shared fixture
from management_portal.test_framework import tau_runtime
from management_portal.test_framework.tau_residents import TAU_ACTOR


def test_an_account_at_its_quota_reserve_stops_preparation_with_the_reason(configured, monkeypatch):  # noqa: F811
    """TAU 执行账号到达额度保留线时立即说明原因停止，不重启也不替换模块 Resident

    账号额度为 reserved 时准备阶段直接报错，模块执行 Resident 保持不变。
    """
    configured.state.options["source"]["account_id"] = "five"
    configured.state.conversation["intake_state"] = "closed"
    monkeypatch.setattr(tau_runtime, "reserved_account_quota",
                        lambda route: {"status": "reserved", "remaining_percent": 0.0} if route == "five" else None)
    replace = mock.Mock(side_effect=AssertionError("replaced an executor the quota reserve closed"))
    configured.restart.side_effect = AssertionError("restarted an executor the quota reserve closed")

    with pytest.raises(RuntimeError, match="额度已到保留线（剩余 0%）"):
        tau_runtime.prepare_configuration(
            configured.assignment, cancelled=lambda: False, emit=lambda _message: None, poll_interval=0,
            refresh_assignment=lambda: configured.assignments.assignment("workbench.panels"),
            replace_unusable_assignment=replace,
        )

    replace.assert_not_called()
    configured.restart.assert_not_called()
    assert configured.assignments.assignment("workbench.panels")["resident_id"] == configured.assignment["resident_id"]


def test_a_replaced_executor_gets_its_successor_before_the_case_is_sent(configured, monkeypatch):  # noqa: F811
    """关闭且无法重启的 TAU 执行 Resident 被替换后先创建并准备继任者再发送用例

    首次准备替换了执行 Resident 时，再次解析模块身份并准备新的 Resident，用例发给继任者。
    """
    from management_portal import lineage_workflows, resident_handoff

    successor = {"resident_id": "resident_5acce55e0001", "lineage_id": "lineage_5acce55e0001"}
    prepared = []

    def ensure(assignments, module_id, **_options):
        current = assignments.assignment(module_id)
        if current["resident_id"]:
            return current
        return assignments.record_identity(module_id, **successor)

    def prepare(assignment, **options):
        prepared.append(assignment["resident_id"])
        if len(prepared) == 1:
            options["replace_unusable_assignment"](assignment)
            return {"ready": True, "status": "pending_create", "resident_id": ""}
        return {"ready": True, "status": "ready", "resident_id": assignment["resident_id"]}

    invoke = mock.Mock(return_value={"status": "completed"})
    monkeypatch.setattr(tau_runtime, "ensure_module_resident", ensure)
    monkeypatch.setattr(tau_runtime, "prepare_configuration", prepare)
    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "install", lambda self, **_: {"event_source_id": "event_source_probe"})
    monkeypatch.setattr(resident_handoff, "run_resident_turn", invoke)
    options = {"actor": TAU_ACTOR, "provider": "codex", "prompt": "probe", "metadata": {"test_module_id": "workbench.panels"}}

    assert tau_runtime.invoke_configured_resident(configured.assignments, **options) == {"status": "completed"}

    assert prepared == [configured.assignment["resident_id"], successor["resident_id"]]
    assert invoke.call_args.kwargs["resident_binding"] == successor
