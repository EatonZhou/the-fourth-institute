from __future__ import annotations

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from io import StringIO
import subprocess

import pytest

from scripts import portal_dev_source_reloader, portal_services


class RestartOrderTrace:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[tuple] = []
        self.commands: list[list[str]] = []
        self.resource_error: str | None = None
        self.verify_components: list[str] = []
        monkeypatch.setattr(portal_services, "_prepare_units", lambda instance: self.events.append(("prepare", instance)))
        monkeypatch.setattr(portal_services, "_capture_pids", lambda *_args: {})
        monkeypatch.setattr(portal_services, "_run", self.run_unit)
        monkeypatch.setattr(portal_services, "_wait_resource_service_healthy", self.resource_ready)
        monkeypatch.setattr(portal_services, "_wait_healthy", self.healthy)
        monkeypatch.setattr(portal_services, "_restart_web_with_notice", lambda instance, seconds, **_kwargs: self.events.append(("countdown", instance, seconds)))
        monkeypatch.setattr(portal_services, "publish_component_state", lambda instance, component, state, **_kwargs: self.events.append(("lifecycle", instance, component, state)))
        monkeypatch.setattr(portal_services, "_require_idle", lambda *_args, **_kwargs: pytest.fail("restart must not pause or drain the Scheduler"))
        monkeypatch.setattr(portal_services, "operation_lock", lambda _path: nullcontext())
        monkeypatch.setattr(portal_services, "_operation_lock_path", lambda _instance: "isolated-operation-lock")
        monkeypatch.setattr(portal_services.portal_dev_generation, "activate", lambda **kwargs: self.events.append(("activate", tuple(kwargs["components"]))))
        monkeypatch.setattr(portal_services, "_unit_state", lambda unit: {"service": unit, "active": "active", "pid": 10})
        monkeypatch.setattr(portal_dev_source_reloader, "source_state", lambda: {})
        monkeypatch.setattr(portal_dev_source_reloader.subprocess, "run", self.run_service_cli)

    def run_unit(self, *args: str, **_kwargs) -> subprocess.CompletedProcess:
        assert args[:2] == ("systemctl", "--user")
        self.events.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def resource_ready(self, instance: str) -> None:
        self.events.append(("resource-health", instance))
        if self.resource_error:
            raise RuntimeError(self.resource_error)
        self.events.append(("resource-ready", instance))

    def healthy(self, instance: str, **kwargs) -> dict:
        self.verify_components = list(kwargs["verify_components"])
        self.events.append(("verify", instance))
        return {"ok": True}

    def run_service_cli(self, args: list[str], **_kwargs) -> subprocess.CompletedProcess:
        assert args[0] == str(portal_services.ROOT / "scripts/portal_services")
        assert args[1:3] == ["restart", "dev"]
        self.commands.append(list(args))
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = portal_services.main(args[1:])
        return subprocess.CompletedProcess(args, status, output.getvalue(), errors.getvalue())
