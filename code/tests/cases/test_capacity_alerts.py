"""Alert transitions, retries and parallel observations without live notices."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from management_portal import capacity_alerts as alerts
from tests.cases.test_lineage_generic_primitives import primitive_store


def snapshot(seconds=0, database=10, disk=20, queue=0):
    observed = (datetime(2026, 10, 15, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return {"schema": "tfi.runtime-capacity.v1", "observed_at": observed,
        "database": {"status": "ok", "used_percent": database, "connections": database,
                     "usable_connections": 100},
        "disks": {"status": "ok", "filesystems": [{"key": "device:1", "path": "/share",
                  "used_percent": disk, "total_bytes": 1000, "free_bytes": 100}]},
        "execution_queue": {"status": "ok", "queued_count": queue,
                            "active_count": 2, "max_concurrency": 10}}


def advance(state, sample):
    return alerts.advance(state, sample, lineage_id="lineage_test", recipients=["sample_admin"],
                          policy=alerts.policy_with())


@pytest.mark.parametrize("source,key,values", [
    ("database", "database", (80, 90, 75, 70)),
    ("disk", "disk:device:1", (85, 95, 82, 80)),
])
def test_capacity_sustained_warning_critical_hysteresis_recovery_and_new_incident(source, key, values):
    """容量持续超限后预警升级并经滞回恢复且区分新事件"""
    warning, critical, middle, recovered = values
    state = advance(None, snapshot(**{source: warning}))
    assert state["outbox"] == []
    state = advance(state, snapshot(299, **{source: warning}))
    assert state["outbox"] == []
    state = advance(state, snapshot(300, **{source: warning}))
    assert state["metrics"][key]["level"] == "warning"
    assert len(state["outbox"]) == 1
    state = advance(state, snapshot(301, **{source: critical}))
    assert state["metrics"][key]["level"] == "critical"
    assert len(state["outbox"]) == 2
    state = advance(state, snapshot(600, **{source: middle}))
    assert state["metrics"][key]["level"] == "critical"
    state = advance(state, snapshot(900, **{source: recovered}))
    assert len(state["outbox"]) == 2
    state = advance(state, snapshot(1200, **{source: recovered}))
    assert state["metrics"][key]["level"] == "normal"
    assert [n["severity"] for n in state["outbox"]] == ["warning", "error", "success"]
    state = advance(state, snapshot(1201, **{source: critical}))
    assert len({n["notification_id"] for n in state["outbox"]}) == 4


def test_queue_burst_and_critical_still_require_duration_and_eligible_count_is_preferred():
    """队列告警要求持续超限并优先使用可调度队列数"""
    state = advance(None, snapshot(queue=100))
    assert state["outbox"] == []
    state = advance(state, snapshot(300, queue=0))
    assert state["outbox"] == []
    sample = snapshot(600, queue=100)
    sample["execution_queue"]["eligible_queued_count"] = 0
    state = advance(state, sample)
    assert state["metrics"]["queue"]["value"] == 0
    state = advance(state, snapshot(900, queue=50))
    state = advance(state, snapshot(1800, queue=50))
    assert state["metrics"]["queue"]["level"] == "critical"
    assert "包含主动暂停和额度等待" in state["outbox"][0]["message"]


def test_unknown_is_not_recovery_and_interrupts_sustained_samples():
    """观测不可用时保持原告警并打断连续采样"""
    state = advance(None, snapshot(database=99))
    sample = snapshot(300)
    sample["database"] = {"status": "unavailable", "code": "database_unavailable"}
    state = advance(state, sample)
    sample["observed_at"] = snapshot(600)["observed_at"]
    state = advance(state, sample)
    assert state["metrics"]["database"]["level"] == "critical"
    assert state["metrics"]["database"]["available"] is False
    assert state["metrics"]["availability:database"]["level"] == "warning"
    assert all(n["severity"] != "success" for n in state["outbox"])
    state = advance(state, snapshot(900, database=99))
    state = advance(state, snapshot(1200, database=99))
    assert state["metrics"]["database"]["level"] == "critical"
    assert state["outbox"][-1]["title"] == "运行容量恢复：数据库连接采集"
    assert len(state["outbox"]) == 3


def test_long_gap_repeated_or_older_sample_cannot_confirm_warning():
    """重复过期或间隔过长的观测不能直接确认预警"""
    state = advance(None, snapshot(database=85))
    assert advance(state, snapshot(database=85)) == state
    assert advance(state, snapshot(-300, database=85)) == state
    state = advance(state, snapshot(1800, database=85))
    assert state["outbox"] == []
    state = advance(state, snapshot(2100, database=85))
    assert len(state["outbox"]) == 1


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True, "80"])
def test_bad_values_do_not_become_good_observations(invalid):
    """容量观测拒绝非数值或无效数值"""
    with pytest.raises(ValueError):
        advance(None, snapshot(database=invalid))


def test_policy_validation_and_missing_filesystem_does_not_resolve_alert():
    """容量策略拒绝无效字段且文件系统缺失不解除预警"""
    with pytest.raises(ValueError):
        alerts.policy_with({"disk": {"recovery": 99}})
    with pytest.raises(ValueError):
        alerts.policy_with({"typo": 1})
    state = advance(None, snapshot(disk=99))
    changed = snapshot(300)
    changed["disks"]["filesystems"][0]["key"] = "device:2"
    state = advance(state, changed)
    assert state["metrics"]["disk:device:1"]["level"] == "critical"
    assert state["metrics"]["disk:device:1"]["available"] is False


class FakeISA:
    """Imitates atomic shared-state CAS and notification deduplication only."""
    def __init__(self):
        self.state = None
        self.sample = snapshot(database=99)
        self.notices = {}
        self.calls = []
        self.lose_ack = False
        self.conflict_once = False

    def __call__(self, instruction, **args):
        self.calls.append(instruction)
        if instruction == "GetRuntimeCapacity":
            return {"capacity": deepcopy(self.sample)}
        if instruction == "GetSharedState":
            return {"value": deepcopy(self.state)}
        if instruction == "UpdateSharedState":
            if self.conflict_once:
                # A competing run commits the identical observation first.
                self.conflict_once = False
                self.state = deepcopy(args["value"])
                return {"matched": False}
            matched = self.state == args["expected_value"]
            if matched:
                self.state = deepcopy(args["value"])
            return {"matched": matched}
        if instruction == "CreateNotification":
            assert self.state["outbox"], "notice must be durable before delivery"
            created = args["notification_id"] not in self.notices
            self.notices.setdefault(args["notification_id"], deepcopy(args))
            if self.lose_ack:
                self.lose_ack = False
                raise RuntimeError("reply lost after successful delivery")
            return {"created": created, "notification_id": args["notification_id"]}
        raise AssertionError(instruction)


def run(isa):
    return alerts.run_once(isa, lineage_id="lineage_test", recipients=["sample_admin"])


def test_notification_ack_loss_retries_exact_notice_and_subsequent_observation_does_not_repeat():
    """通知确认丢失后重试同一通知且后续观测不重复发送"""
    isa = FakeISA()
    isa.lose_ack = True
    with pytest.raises(RuntimeError, match="reply lost"):
        run(isa)
    assert len(isa.state["outbox"]) == len(isa.notices) == 1
    result = run(isa)
    assert result["pending_notifications"] == 0
    assert len(isa.notices) == 1
    isa.sample = snapshot(300, database=99)
    run(isa)
    assert len(isa.notices) == 1
    isa.sample = snapshot(600)
    run(isa)
    isa.sample = snapshot(900)
    run(isa)
    assert len(isa.notices) == 2
    assert list(isa.notices.values())[-1]["severity"] == "success"


def test_competing_cas_does_not_double_count_or_duplicate_notification():
    """并发状态比较交换不重复计数或发送通知"""
    isa = FakeISA()
    isa.conflict_once = True
    run(isa)
    assert isa.state["sequence"] == 1
    assert len(isa.notices) == 1
    assert not isa.state["outbox"]


def test_real_isa_workflow_definition_and_durable_notification_delivery(primitive_store):
    """真实 ISA 工作流通过诊断并持久去重投递告警"""
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.society_service.isa import build_kernel
    from management_portal.society_service import lineage_human_notifications
    from tests.cases.test_isa_audit_g4 import make_lineage

    lineage_id = make_lineage()["lineage_id"]
    kernel = build_kernel()

    def isa(name, **arguments):
        if name == "GetRuntimeCapacity":
            return {"capacity": snapshot(database=99)}
        return kernel.execute(InstructionCall.from_payload({"instruction": name, "arguments": arguments}),
                              InstructionContext(caller="sample_admin", entry="web")).raise_for_error()

    isa("ApplyLineageChanges", lineage_id=lineage_id,
        operations=alerts.workflow_operations("/tmp/portal_capacity_alerts.py", ["sample_admin"]))
    diagnostics = isa("GetLineageDiagnostics", lineage_id=lineage_id)
    assert diagnostics["runnable"], diagnostics
    alerts.run_once(isa, lineage_id=lineage_id, recipients=["sample_admin"])
    alerts.run_once(isa, lineage_id=lineage_id, recipients=["sample_admin"])
    state = isa("GetSharedState", lineage_id=lineage_id, key=alerts.STATE_KEY)["value"]
    assert state["sequence"] == 1 and not state["outbox"]
    groups = lineage_human_notifications.list_actor_notifications("sample_admin", [lineage_id])
    notices = [n for group in groups for n in group["notifications"]]
    assert len(notices) == 1
    assert notices[0]["title"] == "运行容量预警：数据库连接"


def test_workflow_script_preserves_unicode_spaces_and_literal_path_through_interpreter():
    """告警工作流通过解释器完整保留含特殊字符的路径"""
    from management_portal.society_service.lineage_primitives import EffectInvocation, make_event
    from tests.cases.effect_script_support import lower_effect_call

    path = '/share/Projects/AI员工/a folder/$literal "name"/portal_capacity_alerts.py'
    definition = alerts.workflow_operations(path, ["sample_admin"])[2]["value"]["config"]
    invocation = EffectInvocation(id="invoke_test", activation=make_event("manual.triggered"))
    call = lower_effect_call("lineage_test", "effect_test", definition, invocation)
    assert call["instruction"] == "RunScript"
    assert call["arguments"]["path"] == path
    assert call["arguments"]["arguments"] == {"recipients": ["sample_admin"], "policy": alerts.DEFAULT_POLICY}
