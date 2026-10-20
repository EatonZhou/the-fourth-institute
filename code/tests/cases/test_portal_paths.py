from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from management_common import portal_paths
from management_portal.http_admin import AdminHttp
from scripts import migrate_portal_paths as migration
from scripts import portal_background_jobs


def test_new_install_defaults_to_portal_data_and_relative_paths_ignore_cwd(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    root.mkdir()
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", root)
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", tmp_path / "config.json")
    for name in portal_paths.ENV_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    assert portal_paths.path("share_root") == root / "portal_data" / "share"
    assert portal_paths.resolve_directory("portal_data/staging") == root / "portal_data" / "staging"


def test_copied_legacy_config_does_not_bind_new_install_to_old_instance(tmp_path, monkeypatch):
    root = tmp_path / "new-portal"
    (root / "config").mkdir(parents=True)
    old_root = tmp_path / "old-portal" / "portal_instances"
    old_root.mkdir(parents=True)
    (root / "config" / "portal_instances.json").write_text(
        json.dumps({"root": str(old_root)}), encoding="utf-8"
    )
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", root)
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", tmp_path / "paths.json")
    for name in portal_paths.ENV_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    assert portal_paths.path("instances_root") == root / "portal_data" / "instances"


def test_queue_passes_path_environment_to_detached_service(tmp_path, monkeypatch):
    monkeypatch.setattr(migration.sys, "platform", "linux")
    monkeypatch.setattr(migration.shutil, "which", lambda _name: "/usr/bin/systemd-run")
    monkeypatch.setattr(migration, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(migration, "LOCK_PATH", migration.JOBS_DIR / "active.lock")
    monkeypatch.setattr(migration, "validate", lambda _target: ({}, {}))
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(tmp_path / "paths.json"))
    commands = []
    monkeypatch.setattr(migration.subprocess, "run", lambda command, **_kwargs: commands.append(command))
    migration.queue_migration({})
    assert f"--setenv=PORTAL_PATHS_CONFIG={tmp_path / 'paths.json'}" in commands[0]


def test_abandoned_migration_lock_reports_failure_before_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(migration, "LOCK_PATH", migration.JOBS_DIR / "active.lock")
    migration.JOBS_DIR.mkdir()
    old_id = "c" * 32
    migration.LOCK_PATH.write_text(old_id, encoding="utf-8")
    migration._atomic_json(migration._job_path(old_id), {"id": old_id, "state": "copying"})
    monkeypatch.setattr(migration.time, "time", lambda: migration.LOCK_PATH.stat().st_mtime + 61)
    monkeypatch.setattr(migration, "job_alive", lambda *_args: False)
    assert migration._clear_abandoned_lock()
    assert not migration.LOCK_PATH.exists()
    assert migration.job_status(old_id)["state"] == "failed"


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_native_maintenance_job_detaches_from_web(tmp_path, monkeypatch, platform):
    launched = []
    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))
        return type("Child", (), {"pid": 321})()
    monkeypatch.setattr(portal_background_jobs, "spawn_process", fake_popen)
    pid = portal_background_jobs.launch_job(
        "portal-path-migration-test", ["python", "job.py"],
        cwd=tmp_path, log_path=tmp_path / "job.log", platform=platform,
    )
    assert pid == 321
    assert launched[0][0] == ["python", "job.py"]
    assert launched[0][1]["stdin"] == portal_background_jobs.subprocess.DEVNULL
    assert launched[0][1]["tree_id"] == "portal-path-migration-test"
    assert launched[0][1]["kill_on_owner_exit"] is False


def test_native_migration_queue_persists_child_pid_without_saving_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(migration, "LOCK_PATH", migration.JOBS_DIR / "active.lock")
    monkeypatch.setattr(migration, "validate", lambda _target: ({"old": "source"}, {"new": "target"}))
    monkeypatch.setattr(migration, "launch_job", lambda *_args, **_kwargs: 321)
    queued = migration.queue_migration({})
    assert queued["state"] == "queued"
    assert migration.job_status(queued["id"])["pid"] == 321


def test_detached_script_runner_receives_only_explicit_environment(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(portal_background_jobs, "spawn_process",
                        lambda *_args, **kwargs: launched.append(kwargs) or type("Child", (), {"pid": 4})())
    with patch.dict(os.environ, {"PORTAL_DATABASE_URL": "service-only-sentinel"}):
        portal_background_jobs.launch_job(
            "script", ["python", "runner.py"], cwd=tmp_path, log_path=tmp_path / "runner.log",
            environment={"HOME": str(tmp_path)}, inherit_environment=False, platform="darwin",
        )
    assert launched[0]["env"] == {"HOME": str(tmp_path)}


def test_windows_migration_checks_alias_privilege_before_stopping_services(tmp_path, monkeypatch):
    source = tmp_path / "old"
    source.mkdir()
    destination = tmp_path / "new"
    monkeypatch.setattr(Path, "symlink_to", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(RuntimeError, match="Developer Mode"):
        migration._check_directory_alias_support(
            {"share_root": str(source)}, {"share_root": str(destination)}, platform="win32"
        )


def test_migration_validates_before_saving_and_copies_verified_data(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    root.mkdir()
    config = tmp_path / "settings" / "paths.json"
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", root)
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", config)
    monkeypatch.setattr(migration, "JOBS_DIR", config.parent / "jobs")
    monkeypatch.setattr(migration, "LOCK_PATH", migration.JOBS_DIR / "active.lock")
    old = {key: str(tmp_path / "old" / key) for key in portal_paths.KEYS}
    new = {key: str(tmp_path / "new" / key) for key in portal_paths.KEYS}
    portal_paths.write_config(old)
    Path(old["share_root"]).mkdir(parents=True)
    (Path(old["share_root"]) / "message.txt").write_text("portal data", encoding="utf-8")
    assert migration.validate(new) == (old, new)
    Path(new["share_root"]).mkdir(parents=True)
    (Path(new["share_root"]) / "existing.txt").write_text("other data", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        migration.validate(new)
    (Path(new["share_root"]) / "existing.txt").unlink()
    actions = []
    monkeypatch.setattr(migration, "_service", lambda instance, action: actions.append((instance, action)))
    monkeypatch.setattr(migration, "_router", lambda action: actions.append(("router", action)))
    migration.JOBS_DIR.mkdir(parents=True)
    job_id = "a" * 32
    migration.LOCK_PATH.write_text(job_id, encoding="utf-8")
    migration._atomic_json(migration._job_path(job_id), {"id": job_id, "state": "queued", "source": old, "target": new})
    migration.run(job_id)
    assert portal_paths.paths() == {key: Path(value) for key, value in new.items()}
    assert (Path(new["share_root"]) / "message.txt").read_text(encoding="utf-8") == "portal data"
    assert (Path(old["share_root"]) / "message.txt").exists()
    assert Path(old["share_root"]).is_symlink()
    assert Path(old["share_root"]).resolve() == Path(new["share_root"])
    assert migration.job_status(job_id)["state"] == "completed"
    assert actions == [("dev", "stop"), ("release", "stop"), ("router", "stop"), ("release", "start"), ("dev", "start")]


def test_http_refuses_unconfirmed_path_change(monkeypatch):
    admin = AdminHttp(
        respond=lambda _request, status, payload: (status, payload),
        session_actor=lambda _request: "owner",
        access_payload=lambda _actor: {},
        read_json=lambda _request: {"migrate": False, "paths": {}},
        is_admin=lambda _actor: True,
    )
    monkeypatch.setattr("management_portal.http_admin.queue_migration", lambda _paths: pytest.fail("migration queued"))
    status, payload = admin.paths_post(object(), None, None)
    assert status == 400
    assert "migration" in payload["error"].lower()


def test_failed_restart_restores_paths_and_existing_data(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    root.mkdir()
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", root)
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", tmp_path / "settings" / "paths.json")
    monkeypatch.setattr(migration, "JOBS_DIR", tmp_path / "settings" / "jobs")
    monkeypatch.setattr(migration, "LOCK_PATH", migration.JOBS_DIR / "active.lock")
    old = {key: str(tmp_path / "old" / key) for key in portal_paths.KEYS}
    new = {key: str(tmp_path / "new" / key) for key in portal_paths.KEYS}
    for key, name in portal_paths.ENV_KEYS.items():
        monkeypatch.setenv(name, old[key])
    original = Path(old["share_root"])
    original.mkdir(parents=True)
    (original / "data.txt").write_text("original", encoding="utf-8")
    migration.JOBS_DIR.mkdir(parents=True)
    job_id = "b" * 32
    migration.LOCK_PATH.write_text(job_id, encoding="utf-8")
    migration._atomic_json(migration._job_path(job_id), {"id": job_id, "state": "queued", "source": old, "target": new})
    calls = []
    def service(instance, action, *, force=False):
        calls.append((instance, action, force))
        if instance == "release" and action == "start" and len(calls) == 3:
            raise RuntimeError("service unhealthy")
    monkeypatch.setattr(migration, "_service", service)
    monkeypatch.setattr(migration, "_router", lambda _action: None)
    with pytest.raises(RuntimeError, match="service unhealthy"):
        migration.run(job_id)
    assert portal_paths.paths() == {key: Path(value) for key, value in old.items()}
    assert not portal_paths.CONFIG_PATH.exists()
    assert original.is_dir() and not original.is_symlink()
    assert (original / "data.txt").read_text(encoding="utf-8") == "original"
    assert not migration.LOCK_PATH.exists()
    assert migration.job_status(job_id)["state"] == "failed"
