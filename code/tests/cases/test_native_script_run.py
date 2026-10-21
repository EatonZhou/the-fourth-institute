"""Native ScriptRun process handoff keeps the durable run record outside Web."""
from __future__ import annotations

import sys
import os
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from management_portal.society_service.script_runs import service


def test_macos_launch_uses_detached_runner_and_returns_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="darwin", executable=sys.executable))
    monkeypatch.setattr(service.runtime, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(service.runtime, "PRIVATE_ROOT", tmp_path / "private")
    monkeypatch.setattr(service, "_issue_token", lambda *_args: "test-token")
    called = []
    def launch(*args, **kwargs):
        called.append((args, kwargs))
        return 4321
    monkeypatch.setattr(service, "launch_job", launch)
    record = {"script_run_id": "script_run_test", "unit": "portal-script-test",
              "arguments": {}, "caller_id": "user", "lineage_id": "lineage_aaaaaaaaaaaa",
              "trigger_path_id": "", "effect_id": ""}
    with patch.dict(os.environ, {"PORTAL_DATABASE_URL": "service-only-sentinel"}):
        assert service.launch(record, {"cwd": str(tmp_path), "runtime": "python"}) == 4321
    assert called[0][0][0] == record["unit"]
    assert called[0][1]["platform"] == "darwin"
    assert called[0][1]["log_path"].name == "runner.log"
    assert called[0][1]["inherit_environment"] is False
    assert "PORTAL_DATABASE_URL" not in called[0][1]["environment"]


def test_windows_script_run_validates_native_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="win32"))
    script = tmp_path / "task.py"
    script.write_text("pass\n")
    assert service._validate({"path": str(script)})["runtime"] == "python"
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="unsupported"))
    with pytest.raises(NotImplementedError, match="Unsupported ScriptRun platform"):
        service._validate({"path": str(script)})


def test_macos_cancel_signals_detached_runner_group(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="darwin"))
    row = {"script_run_id": "script_run_test", "lineage_id": "lineage_aaaaaaaaaaaa",
           "status": "running", "unit": "portal-script-test", "pid": 4321,
           "started_at": service.now(), "duration_seconds": 0}
    @contextmanager
    def edit(_identifier):
        yield row
    monkeypatch.setattr(service.store, "edit", edit)
    monkeypatch.setattr(service.runtime, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(service, "revoke", lambda _row: None)
    monkeypatch.setattr(service, "_mac_runner_matches", lambda _pid, _identifier: True)
    called = []
    monkeypatch.setattr(service.process_runtime, "process_alive", lambda pid: pid == 4321)
    def terminate(pid, **kwargs):
        called.append((pid, kwargs))
        result = service.runtime.RUN_ROOT / "script_run_test" / "result.json"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(json.dumps({"status": "cancelled"}))
    monkeypatch.setattr(service.process_runtime, "terminate_process_tree", terminate)
    assert service.cancel("script_run_test", "user", lineage_id=row["lineage_id"])["status"] == "cancelled"
    assert called == [(4321, {"timeout": 15})]


def test_macos_cancel_refuses_reused_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="darwin"))
    row = {"script_run_id": "script_run_test", "lineage_id": "lineage_aaaaaaaaaaaa",
           "status": "running", "unit": "portal-script-test", "pid": 4321,
           "started_at": service.now(), "duration_seconds": 0}
    @contextmanager
    def edit(_identifier):
        yield row
    monkeypatch.setattr(service.store, "edit", edit)
    monkeypatch.setattr(service.runtime, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(service, "_mac_runner_matches", lambda _pid, _identifier: False)
    monkeypatch.setattr(service.process_runtime, "process_alive", lambda _pid: True)
    monkeypatch.setattr(service.process_runtime, "terminate_process_tree", lambda *_args, **_kwargs: pytest.fail("wrong process signalled"))
    with pytest.raises(RuntimeError, match="identity"):
        service.cancel("script_run_test", "user", lineage_id=row["lineage_id"])


def test_macos_cancel_during_launch_waits_for_child_pid(tmp_path, monkeypatch):
    script = tmp_path / "task.py"
    script.write_text("pass\n", encoding="utf-8")
    lineage_id = "lineage_aaaaaaaaaaaa"
    row = {}

    @contextmanager
    def edit(_identifier):
        yield row

    def admit(record):
        row.update(record)
        return row, "created"

    def launch(record, _args):
        pending = service.cancel(record["script_run_id"], "user", lineage_id=lineage_id)
        assert pending["status"] == "running"
        assert row["cancel_requested"] is True
        return 4321

    monkeypatch.setattr(service, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(service, "_validate", lambda _args: {"path": str(script)})
    monkeypatch.setattr(service.store, "admit", admit)
    monkeypatch.setattr(service.store, "edit", edit)
    monkeypatch.setattr(service, "launch", launch)
    monkeypatch.setattr(service.runtime, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(service, "revoke", lambda _row: None)
    monkeypatch.setattr(service, "_mac_runner_matches", lambda _pid, _identifier: True)
    signalled = []
    monkeypatch.setattr(service.process_runtime, "process_alive", lambda pid: pid == 4321)
    def terminate(pid, **kwargs):
        signalled.append((pid, kwargs))
        result = service.runtime.RUN_ROOT / row["script_run_id"] / "result.json"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(json.dumps({"status": "cancelled"}))
    monkeypatch.setattr(service.process_runtime, "terminate_process_tree", terminate)

    receipt = service.start({"lineage_id": lineage_id}, "user")
    assert receipt["status"] == "cancelled"
    assert signalled == [(4321, {"timeout": 15})]
