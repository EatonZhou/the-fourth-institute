"""Google Drive backup uses one authenticated ScriptRun on the backup Lineage."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from management_portal import backup_lineage
from tests.cases.test_backup_lineage import workflow, call
from tests.cases.test_lineage_generic_primitives import primitive_store


LINEAGE = "lineage_0123456789ab"
RUN = "script_run_0123456789abcdef01234567"
ACTOR = "resident_0123456789ab"
CODE = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch, tmp_path):
    script = importlib.import_module("scripts.google_drive_backup")
    monkeypatch.setattr(backup_lineage, "read_site_config", lambda: {
        "deployment": {"backup": {"lineage_id": LINEAGE},
                       "portal_operations": {"lineage_id": "lineage_ffffffffffff"}}})
    monkeypatch.setattr(backup_lineage, "PORTAL_ROOT", CODE)
    monkeypatch.setattr(script, "load", lambda: SimpleNamespace(
        directory=lambda name: tmp_path / name,
        document={"dependencies": {"backup": str(tmp_path / "backup/src/main.py")}}))
    monkeypatch.setenv("PORTAL_SCRIPT_INPUT", str(tmp_path / "runtime/script_runs" / RUN / "io/input.json"))
    state = SimpleNamespace(record={
        "script_run_id": RUN, "lineage_id": LINEAGE, "caller_id": ACTOR,
        "path": str(CODE / "scripts/google_drive_backup.py"), "status": "running",
        "arguments": {"profile": "probe"}, "exclusive_key": "resource-backup",
    }, hints={"script_run_id": RUN}, outputs=[], calls=[], lookups=[], failure=None)

    def current_run(**arguments):
        state.lookups.append(arguments)
        if state.failure:
            raise state.failure
        return deepcopy(state.record)

    monkeypatch.setitem(sys.modules, "portal_script", SimpleNamespace(
        inputs=lambda: state.hints, get_current_run=current_run,
        set_output=state.outputs.append,
        isa=lambda *a, **k: pytest.fail("backup must not launch a nested RunScript")))

    def run(operation, service_root, network_route=None):
        state.calls.append((operation, service_root, network_route))
        return {"operation": operation, "status": "success", "succeeded": True,
                "verified_bytes": 65536, "network_route": network_route or "inherited"}

    monkeypatch.setattr(script.resource_backup_drive, "run", run)
    state.script = script
    return state


@pytest.mark.parametrize("profile", ["critical", "content", "probe"])
def test_authenticated_run_uses_persisted_parameters_without_child_run(runner, profile, tmp_path):
    runner.record["arguments"] = {"profile": profile, "network_route": "vps"}
    runner.hints.update(caller_id="forged", arguments={"profile": "content", "network_route": "direct"})
    assert runner.script.main() == 0
    assert runner.lookups == [{"lineage_id": LINEAGE, "script_run_id": RUN}]
    assert runner.calls == [("drive-" + profile, tmp_path / "backup", "vps")]
    assert runner.outputs[0]["succeeded"] is True


@pytest.mark.parametrize("changes", [
    {"lineage_id": "lineage_ffffffffffff"}, {"script_run_id": "script_run_ffffffffffffffffffffffff"},
    {"path": str(CODE / "scripts/resource_backup_lineage.py")}, {"status": "completed"},
    {"caller_id": "@admin"}, {"arguments": []}, {"exclusive_key": "other"},
])
def test_invalid_persisted_run_is_rejected_before_backup(runner, changes):
    runner.record.update(changes)
    with pytest.raises(PermissionError):
        runner.script.main()
    assert not runner.calls


def test_expired_relay_lease_does_not_fall_back_to_input_actor(runner):
    runner.failure = PermissionError("lease revoked")
    with pytest.raises(PermissionError, match="lease revoked"):
        runner.script.main()
    assert not runner.calls


@pytest.mark.parametrize("arguments", [{}, {"profile": "other"}, {"profile": "probe", "network_route": "bad"},
    {"profile": "probe", "caller_id": ACTOR}, {"profile": "probe", "destination": "/private"}])
def test_invalid_arguments_cannot_select_arbitrary_backup_or_identity(runner, arguments):
    runner.record["arguments"] = arguments
    with pytest.raises(ValueError):
        runner.script.main()
    assert not runner.calls


def test_backup_failure_keeps_safe_output_and_failure_exit(runner, monkeypatch, capsys):
    def fail(*a, **k):
        raise RuntimeError("credential=secret /private/repository")
    monkeypatch.setattr(runner.script.resource_backup_drive, "run", fail)
    assert runner.script.main() == 1
    assert runner.outputs[0]["status"] == "failed"
    assert not runner.outputs[0]["succeeded"]
    assert "secret" not in json.dumps(runner.outputs) + capsys.readouterr().out


def test_run_arguments_use_backup_binding_and_mandatory_lock(runner):
    assert backup_lineage.google_drive_run_arguments({"profile": "probe"}) == {
        "lineage_id": LINEAGE, "path": str(CODE / "scripts/google_drive_backup.py"),
        "runtime": "python", "arguments": {"profile": "probe"}, "exclusive_key": "resource-backup"}


def test_fixed_graph_entries_have_no_new_timers_and_reuse_existing_lock(runner):
    operations = backup_lineage.google_drive_operations()
    effects = [o["value"] for o in operations if o["kind"] == "component" and o["value"]["kind"] == "effect"]
    events = [o["value"] for o in operations if o["kind"] == "component" and o["value"]["kind"] == "event_source"]
    assert len(effects) == len(events) == 3
    assert all(e["config"] == {"event_type": "manual.triggered"} for e in events)
    for effect in effects:
        script = effect["config"]["script"]
        assert 'exclusive_key="resource-backup"' in script
        assert "google_drive_backup.py" in script and "RunGoogleDriveBackup" not in script
        assert "arguments:=" in script and "argv:=" not in script


def test_legacy_backup_executor_rejects_retired_drive_modes():
    from scripts.resource_backup_lineage import run_operation
    with pytest.raises(ValueError, match="Unsupported"):
        run_operation("drive-probe", service_root=Path("/not-used"))


def test_generated_effect_json_is_an_object_and_receipt_remains_async():
    from management_portal.society_service.lineage_effect_script import CallResult, Interpreter, parse

    calls = []
    def execute(request):
        calls.append(request)
        return CallResult(True, {"script_run_id": RUN, "status": "running"})
    for operation in backup_lineage.google_drive_operations():
        value = operation["value"]
        if value["kind"] != "effect":
            continue
        outcome = Interpreter(parse(value["config"]["script"]), {}, execute).run()
        assert outcome.status == "succeeded"
        assert outcome.outputs == {"script_run_id": RUN, "status": "running"}
    assert [c.arguments["arguments"] for c in calls] == [
        {"profile": "critical"}, {"profile": "content"}, {"profile": "probe"}]
    assert all(c.instruction == "RunScript" and c.arguments["exclusive_key"] == "resource-backup" for c in calls)


def test_drive_graph_uses_real_effect_parser_and_original_caller(workflow, monkeypatch):
    from management_portal.society_service.script_runs import service

    lineage, _, controller = workflow
    call("ApplyLineageChanges", {"lineage_id": lineage, "operations": backup_lineage.google_drive_operations()})
    graph = call("GetLineage", {"lineage_id": lineage})["lineage"]
    event = next(c for c in graph["program"]["components"]
                 if c.get("client_key") == "system:backup:drive-probe:manual")
    launched = []
    def start(arguments, actor, **kwargs):
        launched.append((arguments, actor))
        return {"script_run_id": RUN, "status": "running", "started_at": "2026-11-26T14:00:00Z"}
    monkeypatch.setattr(service, "start", start)
    receipt = controller.trigger(event["id"], "unused-web-label")
    assert receipt["event_id"] and receipt["trigger_path_id"]
    assert len(launched) == 1, json.dumps(receipt, ensure_ascii=False)
    arguments, actor = launched[0]
    assert arguments["arguments"] == {"profile": "probe"}
    assert arguments["exclusive_key"] == "resource-backup"
    assert arguments["path"] == backup_lineage.google_drive_script_path()
    from tests.cases.test_lineage_generic_primitives import ACTOR as ORIGINAL_ACTOR
    assert actor == ORIGINAL_ACTOR
