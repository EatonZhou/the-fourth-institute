"""Detached maintenance jobs leave the Web process and receive only explicit environment."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from scripts import portal_background_jobs


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_native_maintenance_job_detaches_from_web(tmp_path, monkeypatch, platform):
    launched = []

    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))
        return type("Child", (), {"pid": 321})()
    monkeypatch.setattr(portal_background_jobs, "spawn_process", fake_popen)
    pid = portal_background_jobs.launch_job(
        "portal-maintenance-test", ["python", "job.py"],
        cwd=tmp_path, log_path=tmp_path / "job.log", platform=platform,
    )
    assert pid == 321
    assert launched[0][0] == ["python", "job.py"]
    assert launched[0][1]["stdin"] == portal_background_jobs.subprocess.DEVNULL
    assert launched[0][1]["tree_id"] == "portal-maintenance-test"
    assert launched[0][1]["kill_on_owner_exit"] is False


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
