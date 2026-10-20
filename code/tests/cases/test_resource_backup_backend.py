from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from management_portal.resource_backup_backend import (
    ResourceBackupBackend,
    ResourceBackupConflict,
    ResourceBackupUnavailable,
)


FIXED_NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
from management_portal.backup_lineage import OPERATIONS, SCHEDULES

TIMER_OPERATIONS = set(SCHEDULES)
ACTION_OPERATIONS = set(OPERATIONS)


@pytest.fixture(autouse=True)
def configured_backup(monkeypatch):
    monkeypatch.setattr("management_portal.backup_lineage.read_site_config", lambda: {
        "deployment": {"backup": {"lineage_id": "lineage_111111111111",
                                  "restore_guide_resource_id": "information_777777777777"}}})


class FakeWorkflow:
    def __init__(self, *, available=True, start_error=None, active="", calendars=None):
        self.available = available
        self.start_error = start_error
        self.active = active
        self.calendars = calendars or {}
        self.started = []

    def schedule(self, actor):
        if not self.available:
            raise RuntimeError("Lineage unavailable")
        return {
            "readable": True,
            "lineage_id": "lineage_111111111111",
            "actions": [
                {"name": op, "operation": op, "event_source_id": op, "effect_id": op + "_effect",
                 "available": True, "active": op == self.active}
                for op in OPERATIONS
            ],
            "timers": [
                {"name": op, "operation": op, "event_source_id": op,
                 "available": True, "paused": False,
                 "calendar": self.calendars.get(op, "每天 03:00"),
                 "next_trigger_at": "2026-07-30T09:00:00Z"}
                for op in SCHEDULES
            ],
        }

    def trigger(self, event_source_id, actor):
        self.started.append(event_source_id)
        if self.start_error:
            raise self.start_error
        return {"event_id": "event1", "trigger_path_id": "path1", "delivery": {
            "results": [{"target_id": event_source_id + "_effect", "status": "executed"}]}}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def write_yaml(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True),
        encoding="utf-8",
    )


def make_backend(
    root: Path,
    workflow: FakeWorkflow,
    *,
    disk_usage=None,
) -> ResourceBackupBackend:
    return ResourceBackupBackend(
        root,
        workflow=workflow,
        disk_usage=disk_usage,
        clock=lambda: FIXED_NOW,
    )


def test_missing_status_returns_complete_setup_required_overview_contracts(tmp_path, subtests, isolated_portal_case):
    """缺少备份状态时返回完整待配置概览并阻止冲突运行"""
    with subtests.test(contract='test_missing_status_returns_complete_setup_required_overview'), isolated_portal_case() as case_path:
        tmp_path = case_path
        workflow = FakeWorkflow(available=False)
        backend = make_backend(tmp_path, workflow)
        overview = backend.get_overview('viewer')
        assert overview['schema'] == 'tfi.resource-backup-overview.v1'
        assert overview['health']['status'] == 'setup_required'
        assert overview['target']['status'] == 'setup_required'
        assert overview['active_run'] is None
        assert overview['recent_runs'] == []
        assert overview['coverage'] == []
        assert overview['permissions'] == {'can_view': True, 'can_run': True, 'can_verify': True, 'can_cancel': True}
        assert [item['id'] for item in overview['policy']['profiles']] == ['critical', 'content']
        assert overview['policy']['retention'] == {'hourly': 0, 'daily': 0, 'weekly': 0, 'monthly': 0}
        assert overview['policy']['verification']['modes'] == ['check', 'deep-check']
        assert overview['warnings']
        assert overview['restore_guide']['resource_id'] == 'information_777777777777'
    with subtests.test(contract='test_active_projection_and_executor_diagnostics_raise_custom_conflicts'), isolated_portal_case() as case_path:
        tmp_path = case_path
        write_json(tmp_path / 'state' / 'status.json', {'schema': 'tfi.agent_status.v1', 'state': 'running', 'current_operation': 'backup:critical', 'current_run_id': 'backup-critical-running'})
        workflow = FakeWorkflow()
        backend = make_backend(tmp_path, workflow)
        with pytest.raises(ResourceBackupConflict):
            backend.request_backup('content', 'sample_admin')
        with pytest.raises(ResourceBackupConflict):
            backend.request_verification('check', 'sample_admin')
        assert workflow.started == []
        # A running workflow blocks a second request, whatever status.json says.
        write_json(tmp_path / 'state' / 'status.json', {'schema': 'tfi.agent_status.v1', 'state': 'idle'})
        workflow = FakeWorkflow(active='content')
        with pytest.raises(ResourceBackupConflict):
            make_backend(tmp_path, workflow).request_verification('check', 'sample_admin')
        assert workflow.started == []
    with subtests.test(contract='test_executor_failure_raises_unavailable_without_leaking_diagnostics'), isolated_portal_case() as case_path:
        tmp_path = case_path
        write_json(tmp_path / 'status.json', {'schema': 'tfi.agent_status.v1', 'state': 'idle'})
        workflow = FakeWorkflow(start_error=RuntimeError('RESTIC_PASSWORD=secret /private/password-file'))
        backend = make_backend(tmp_path, workflow)
        with pytest.raises(ResourceBackupUnavailable, match='Resource Backup executor is unavailable') as caught:
            backend.request_backup('critical', 'sample_admin')
        assert 'RESTIC_PASSWORD' not in str(caught.value)
        assert workflow.started == ['critical']


def test_overview_normalizes_projection_and_never_exposes_secrets(
    tmp_path: Path,
) -> None:
    """备份概览规范化状态且不暴露秘密配置"""
    secret_password_path = "/private/password-file"
    secret_command = "restic backup --password-file /private/password-file"
    secret_environment = "RESTIC_PASSWORD=do-not-expose"
    repository_path = "/private/repository"
    write_yaml(
        tmp_path / "config" / "config.yaml",
        {
            "policy": {
                "default_profile": "content",
                "retention": {
                    "keep_hourly": 24,
                    "keep_daily": 30,
                    "keep_weekly": 12,
                    "keep_monthly": 12,
                },
            },
            "target": {
                "label": "Local encrypted repository",
                "kind": "filesystem",
                "path": repository_path,
                "encrypted": True,
                "password_path": secret_password_path,
                "command": secret_command,
                "env": secret_environment,
            },
        },
    )
    write_json(
        tmp_path / "state" / "status.json",
        {
            "schema": "tfi.agent_status.v1",
            "state": "running",
            "last_success_at": "2026-07-30T06:00:00Z",
            "profiles": {
                "critical": {
                    "last_success_at": "2026-07-30T06:00:00Z",
                },
                "content": {
                    "last_success_at": "2026-07-30T04:00:00Z",
                },
            },
            "target": {
                "status": "ready",
                "repository_size_bytes": 4096,
                "password_file": secret_password_path,
                "environment": secret_environment,
            },
            "coverage": {
                "control-plane": {
                    "label": "Control plane",
                    "tier": "critical",
                    "required": True,
                    "status": "ok",
                    "paths": ["/share/Agents/_registry", "/share/Agents/_archive"],
                    "latest_snapshot_at": "2026-07-30T06:00:00Z",
                },
            },
            "active_run": {
                "id": "backup-critical-active",
                "operation": "backup",
                "profile": "critical",
                "state": "backing-up",
                "phase": "snapshotting",
                "started_at": "2026-07-30T07:55:00Z",
                "progress": {
                    "bytes_done": 512,
                    "bytes_total": 1024,
                },
                "command": secret_command,
                "env": secret_environment,
            },
            "warnings": [
                {
                    "code": "repository-warning",
                    "message": secret_command,
                },
            ],
        },
    )
    write_json(
        tmp_path / "state" / "runs" / "backup-content-finished" / "result.json",
        {
            "schema": "tfi.agent_run.v1",
            "command": "run-once",
            "run_id": "backup-content-finished",
            "status": "success",
            "started_at": "2026-07-30T03:55:00Z",
            "finished_at": "2026-07-30T04:00:00Z",
            "summary": {
                "profile": "content",
                "snapshot_id": "abc123",
                "planned_restic_command": secret_command,
            },
            "outputs": [{"path": secret_password_path}],
        },
    )
    disk_paths: list[str] = []

    def fake_disk_usage(path):
        disk_paths.append(str(path))
        return SimpleNamespace(total=10_000, used=4_000, free=6_000)

    workflow = FakeWorkflow(calendars={"content": "*-*-* 00/4:27:00"})
    backend = make_backend(
        tmp_path,
        workflow,
        disk_usage=fake_disk_usage,
    )

    overview = backend.get_overview("sample_admin")

    assert overview["health"]["status"] == "running"
    assert overview["health"]["latest_success_at"] == "2026-07-30T06:00:00Z"
    assert overview["active_run"]["status"] == "running"
    assert overview["active_run"]["profile"] == "critical"
    assert overview["active_run"]["progress"]["bytes_done"] == 512
    assert overview["recent_runs"][0]["run_id"] == "backup-content-finished"
    assert overview["recent_runs"][0]["status"] == "completed"
    assert overview["recent_runs"][0]["profile"] == "content"
    assert overview["recent_runs"][0]["snapshot_id"] == "abc123"
    assert overview["coverage"][0]["status"] == "protected"
    assert overview["coverage"][0]["path_count"] == 2
    assert overview["target"] == {
        "label": "Local encrypted repository",
        "kind": "filesystem",
        "status": "ready",
        "encrypted": True,
        "repository_size_bytes": 4096,
        "total_bytes": 10_000,
        "used_bytes": 4_000,
        "free_bytes": 6_000,
    }
    assert disk_paths == [repository_path]
    profiles = {
        item["id"]: item
        for item in overview["policy"]["profiles"]
    }
    assert profiles["critical"]["status"] == "running"
    assert profiles["critical"]["calendar"] == ""
    assert profiles["content"]["calendar"] == "*-*-* 00/4:27:00"
    assert profiles["content"]["next_run_at"] == "2026-07-30T09:00:00Z"
    assert overview["permissions"]["can_run"] is True
    assert {
        item["name"]
        for item in overview["schedule"]["actions"]
        if item["available"]
    } == set(ACTION_OPERATIONS)
    assert {
        item["name"]
        for item in overview["schedule"]["timers"]
        if item["available"]
    } == set(TIMER_OPERATIONS)
    assert "scheduler_unavailable" not in {item["code"] for item in overview["warnings"]}

    serialized = json.dumps(overview, ensure_ascii=False)
    assert secret_password_path not in serialized
    assert secret_command not in serialized
    assert secret_environment not in serialized
    assert "password_path" not in serialized
    assert "password_file" not in serialized
    assert '"command"' not in serialized
    assert '"env"' not in serialized


def test_successful_profile_promotes_configured_target_and_coverage(
    tmp_path: Path,
) -> None:
    """备份成功后目标就绪且对应覆盖项标记受保护"""
    write_yaml(
        tmp_path / "config" / "config.yaml",
        {
            "target": {
                "label": "/data 本机快速恢复层",
                "kind": "local_restic",
                "path": str(tmp_path / "repository"),
                "encrypted": True,
                "status": "setup_required",
            },
            "coverage": [
                {
                    "source_id": "control-plane",
                    "label": "统一 ID 与注册表",
                    "tier": "critical",
                    "status": "setup_required",
                },
                {
                    "source_id": "large-assets",
                    "label": "大模型与数据集",
                    "tier": "external",
                    "status": "excluded",
                },
            ],
        },
    )
    write_json(
        tmp_path / "state" / "status.json",
        {
            "schema": "tfi.agent_status.v1",
            "state": "idle",
            "last_success_at": "2026-07-30T06:00:00Z",
            "profiles": {
                "critical": {
                    "status": "healthy",
                    "last_success_at": "2026-07-30T06:00:00Z",
                },
            },
        },
    )
    backend = make_backend(
        tmp_path,
        FakeWorkflow(),
        disk_usage=lambda _path: SimpleNamespace(
            total=10_000,
            used=4_000,
            free=6_000,
        ),
    )

    overview = backend.get_overview("sample_admin")

    assert overview["target"]["status"] == "ready"
    coverage = {
        item["source_id"]: item
        for item in overview["coverage"]
    }
    assert coverage["control-plane"]["status"] == "protected"
    assert coverage["large-assets"]["status"] == "excluded"
    assert {
        item["code"]
        for item in overview["warnings"]
    } >= {"no_offsite_replica", "large_assets_excluded"}


def test_health_separates_recovery_points_from_verification_and_sizes_repository(
    tmp_path: Path,
) -> None:
    """备份概览区分恢复点与校验时间并统计仓库大小"""
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "pack").write_bytes(b"12345")
    write_yaml(
        tmp_path / "config" / "config.yaml",
        {
            "target": {
                "label": "/data 本机快速恢复层",
                "kind": "local_restic",
                "path": str(repository),
                "encrypted": True,
            },
        },
    )
    write_json(
        tmp_path / "state" / "status.json",
        {
            "schema": "tfi.agent_status.v1",
            "state": "idle",
            "last_success_at": "2026-07-30T08:00:00Z",
            "profiles": {
                "critical": {"last_success_at": "2026-07-30T06:00:00Z"},
                "content": {"last_success_at": "2026-07-30T07:00:00Z"},
            },
            "maintenance": {
                "check": {
                    "status": "healthy",
                    "last_success_at": "2026-07-30T08:00:00Z",
                },
            },
        },
    )
    write_json(
        tmp_path / "state" / "runs" / "maintain-check.json",
        {
            "schema": "tfi.agent_run.v1",
            "command": "maintain",
            "run_id": "maintain-check",
            "kind": "verification",
            "status": "success",
            "started_at": "2026-07-30T07:59:00Z",
            "finished_at": "2026-07-30T08:00:00Z",
            "summary": {"operation": "check"},
        },
    )
    backend = make_backend(
        tmp_path,
        FakeWorkflow(),
        disk_usage=lambda _path: SimpleNamespace(
            total=10_000,
            used=4_000,
            free=6_000,
        ),
    )

    overview = backend.get_overview("sample_admin")

    assert overview["health"]["latest_success_at"] == "2026-07-30T07:00:00Z"
    assert overview["health"]["last_verified_at"] == "2026-07-30T08:00:00Z"
    assert overview["target"]["repository_size_bytes"] == 5
    assert overview["recent_runs"][0]["profile_label"] == "仓库校验"
    assert overview["recent_runs"][0]["summary"] == "校验已完成"


def test_permissions_allowed_values_and_bound_events(
    tmp_path: Path,
) -> None:
    """备份操作校验参数并以调用者身份触发绑定事件，TriggerEvent 的拒绝原样成为权限错误

    处理代码不再按身份另做检查：能否运行由 TriggerEvent 对备份 Lineage 的判定决定，拒绝时返回 PermissionError 而不是“执行器不可用”。
    """
    from management_portal.hatg.contracts import InstructionError

    write_json(
        tmp_path / "state" / "status.json",
        {
            "schema": "tfi.agent_status.v1",
            "state": "idle",
        },
    )
    refused = FakeWorkflow(start_error=InstructionError("instruction.forbidden", "no"))
    with pytest.raises(PermissionError):
        make_backend(tmp_path, refused).request_backup("critical", "intern")
    workflow = FakeWorkflow()
    backend = make_backend(tmp_path, workflow)

    with pytest.raises(ValueError):
        backend.request_backup("critical; reboot", "sample_admin")
    with pytest.raises(ValueError):
        backend.request_verification("retention", "sample_admin")
    assert workflow.started == []

    assert backend.request_backup("critical", "sample_admin")["event_source_id"] == "critical"
    assert backend.request_backup("content", "sample_admin")["accepted"] is True
    assert backend.request_verification("check", "sample_admin")["accepted"] is True
    assert backend.request_verification("deep-check", "sample_admin")["accepted"] is True

    # Each action triggers the bound manual Event with the authenticated actor.
    assert workflow.started == [
        "critical",
        "content",
        "check",
        "deep-check",
    ]


def test_backup_buttons_ask_the_instructions_they_call(tmp_path: Path, monkeypatch) -> None:
    """备份页的按钮按它们调用的指令判定：运行与校验问 TriggerEvent，取消问 CancelScriptRun，都对备份 Lineage

    不再按调用者是不是 User 或 Resident 决定。
    """
    from management_portal import permission_system
    from management_portal import backup_lineage
    lineage_id = "lineage_111111111111"
    monkeypatch.setattr(backup_lineage, "read_site_config", lambda: {
        "deployment": {"backup": {"lineage_id": lineage_id}}})

    asked = []

    def allows(actor, instruction, arguments=None):
        asked.append((actor, instruction, dict(arguments or {})))
        return instruction == "TriggerEvent" and actor == "sample_admin"

    monkeypatch.setattr(permission_system, "allows", allows)
    backend = ResourceBackupBackend(
        tmp_path,
        workflow=FakeWorkflow(available=False),
        clock=lambda: FIXED_NOW,
    )

    assert backend.get_overview("sample_admin")["permissions"] == {
        "can_view": True, "can_run": True, "can_verify": True, "can_cancel": False}
    assert backend.get_overview("resident_aaaaaaaaaaaa")["permissions"]["can_run"] is False
    assert {(instruction, arguments["lineage_id"]) for _, instruction, arguments in asked} == {
        ("TriggerEvent", lineage_id), ("CancelScriptRun", lineage_id)}
