"""Backup schedule ownership and the actual ISA Event -> Effect admission path."""
import json
from types import SimpleNamespace

import pytest

from management_portal import backup_lineage
from management_portal.resource_backup_backend import ResourceBackupBackend, ResourceBackupUnavailable
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service.isa import build_kernel
from management_portal.society_service.script_runs import service
from scripts import resource_backup_lineage as runner
from tests.cases.test_isa_audit_g4 import make_lineage, registered_members
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR


def call(name, arguments):
    result = build_kernel().execute(InstructionCall.from_payload({
        "instruction": name, "arguments": arguments,
    }), InstructionContext(caller=ACTOR, entry="web"))
    return result.raise_for_error()


@pytest.fixture
def workflow(primitive_store, monkeypatch):
    lid = make_lineage()["lineage_id"]
    monkeypatch.setattr(backup_lineage, "read_site_config", lambda: {"deployment": {"backup": {"lineage_id": lid}}})
    call("ApplyLineageChanges", {"lineage_id": lid, "operations": backup_lineage.migration_operations()})
    bindings = backup_lineage.component_bindings(call("GetLineage", {"lineage_id": lid})["lineage"])
    controller = backup_lineage.BackupLineage(lambda name, args, actor: call(name, args))
    return lid, bindings, controller


def test_graph_draft_preserves_schedules_and_controls_manual_trigger(workflow, monkeypatch, tmp_path):
    """备份计划保留定时安排并独立控制手动触发"""
    lid, bindings, controller = workflow
    launched = []
    def start(arguments, actor, **kwargs):
        launched.append(arguments)
        return {"script_run_id": "srun_aaaaaaaaaaaa", "status": "running", "started_at": "2026-10-15T01:00:00Z"}
    monkeypatch.setattr(service, "start", start)
    backend = ResourceBackupBackend(tmp_path, workflow=controller)
    schedule = controller.schedule(ACTOR)
    assert len(schedule["actions"]) == 6
    assert len(schedule["timers"]) == 5
    assert all(item["available"] for item in schedule["actions"] + schedule["timers"])
    source_id = bindings["content"]["timer_event_id"]
    call("PauseComponent", {"lineage_id": lid, "component_id": source_id, "paused": True})
    content_timer = next(t for t in controller.schedule(ACTOR)["timers"] if t["operation"] == "content")
    assert content_timer["paused"] and not content_timer["available"]
    assert content_timer["next_trigger_at"] == ""
    # A disabled timer does not disable the distinct manual Event.
    receipt = backend.request_backup("content", ACTOR)
    assert receipt["accepted"] and receipt["event_id"] and receipt["trigger_path_id"]
    assert launched[0]["argv"] == ["content"]
    assert launched[0]["exclusive_key"] == "resource-backup"
    call("PauseComponent", {"lineage_id": lid, "component_id": bindings["content"]["effect_id"], "paused": True})
    with pytest.raises(ResourceBackupUnavailable):
        backend.request_backup("content", ACTOR)
    assert len(launched) == 1
    call("PauseComponent", {"lineage_id": lid, "component_id": bindings["content"]["effect_id"], "paused": False})
    call("PauseComponent", {"lineage_id": lid, "component_id": bindings["content"]["manual_event_id"], "paused": True})
    with pytest.raises(ResourceBackupUnavailable):
        backend.request_backup("content", ACTOR)
    assert len(launched) == 1


def test_missing_binding_and_disconnected_graph_cannot_start(workflow, tmp_path):
    """备份绑定缺失或连线断开时拒绝启动"""
    lid, bindings, controller = workflow
    graph = call("GetLineage", {"lineage_id": lid})["lineage"]
    original_invoke = controller._invoke
    for component in graph["program"]["components"]:
        if component["id"] == bindings["content"]["manual_event_id"]:
            component.pop("client_key")
    controller._invoke = lambda *args: {"lineage": graph}
    backend = ResourceBackupBackend(tmp_path, workflow=controller)
    with pytest.raises(ResourceBackupUnavailable):
        backend.request_backup("content", ACTOR)
    controller._invoke = original_invoke
    call("ApplyLineageChanges", {"lineage_id": lid, "operations": [{"op": "update", "kind": "component",
        "id": bindings["content"]["manual_event_id"], "changes": {"title": "Renamed by user"}}]})
    assert next(a for a in controller.schedule(ACTOR)["actions"] if a["operation"] == "content")["available"]
    graph = call("GetLineage", {"lineage_id": lid})["lineage"]["program"]
    wire = next(w for w in graph["wires"] if w["source"] == bindings["content"]["manual_event_id"])
    call("ApplyLineageChanges", {"lineage_id": lid, "operations": [{"op": "delete", "kind": "wire", "id": wire["id"]}]})
    with pytest.raises(ResourceBackupUnavailable):
        backend.request_backup("content", ACTOR)


@pytest.mark.parametrize("operation", runner.OPERATIONS)
def test_executor_fixed_commands_preserve_lock_outcome_without_secret_output(monkeypatch, tmp_path, operation):
    """备份固定命令保留锁占用结果且隐藏敏感输出"""
    invoked = []
    def run(argv, **kwargs):
        invoked.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "status": "skipped_locked", "run_id": "backup1", "errors": ["secret-value"],
            "outputs": ["/private/repository"],
        }), stderr="secret-value")
    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner.run_operation(operation, service_root=tmp_path)
    assert result["status"] == "skipped_locked" and result["succeeded"]
    assert "secret-value" not in json.dumps(result)
    assert "repository" not in json.dumps(result)
    assert invoked[0][0][-len(runner.OPERATIONS[operation]):] == list(runner.OPERATIONS[operation])
    assert invoked[0][1]["umask"] == 0o077


def test_executor_rejects_arbitrary_operation_and_failed_output(monkeypatch):
    """备份执行拒绝任意操作并标记命令失败"""
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="secret", stderr="private"))
    with pytest.raises(ValueError):
        runner.run_operation("content; reboot")
    result = runner.run_operation("content")
    assert result["status"] == "failed" and not result["succeeded"]


def test_web_actor_is_forwarded_to_isa(monkeypatch):
    """网页调用者身份传递给 TriggerEvent"""
    seen = []
    monkeypatch.setattr(backup_lineage, "read_site_config", lambda: {
        "deployment": {"backup": {"lineage_id": "lineage_111111111111"}}})
    monkeypatch.setattr(backup_lineage.routing, "execute_or_raise", lambda call, context: seen.append((call, context)) or {})
    backup_lineage.BackupLineage().trigger("event_source_example", "alice")
    assert seen[0][1].caller == "alice"
    assert seen[0][0]["instruction"] == "TriggerEvent"
