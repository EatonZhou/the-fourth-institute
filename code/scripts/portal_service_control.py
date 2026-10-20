"""One service-control contract for Portal's two persistent service pools.

The process topology belongs to ``portal_source_layout``.  This module owns
only operating-system service operations; it never decides which Portal
component should be restarted or when a request may be interrupted.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from scripts.portal_source_layout import INSTANCE_LAYOUT
from management_common.site_compatibility import compatibility_value
from management_common.native_output import run_native_capture


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = run_native_capture(args, timeout=None, merge_output=True)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} exited with {result.returncode}: {(result.stdout or '')[-4000:].strip()}")
    return result


def _state(service: str, *, load: str, active: str, sub: str, pid: int = 0) -> dict[str, Any]:
    return {"service": service, "load": load, "active": active,
            "sub": sub, "pid": pid}


class ServiceControl:
    """start/stop/restart/status/health for a registered persistent service.

    ``reload`` and ``signal_main`` are explicit Linux-only capabilities used
    by the current zero-interruption ExecutionService and Worker handovers.
    A platform without those capabilities must not silently substitute a
    process-killing restart.
    """

    def __init__(self, *, platform: str | None = None, runner: Runner | None = None,
                 agent_root: Path | None = None, uid: int | None = None):
        value = platform or sys.platform
        self.platform = "windows" if value == "win32" else "macos" if value == "darwin" else "linux" if value.startswith("linux") else value
        if self.platform not in {"linux", "macos", "windows"}:
            raise RuntimeError(f"Portal service control does not support {value}")
        self.run = runner or _run
        self.agent_root = agent_root or Path.home() / "Library" / "LaunchAgents"
        self.uid = os.getuid() if uid is None and hasattr(os, "getuid") else uid

    @staticmethod
    def label(service: str) -> str:
        prefix = compatibility_value('launchd_label_prefix', 'com.tfi.portal.')
        if not isinstance(prefix, str) or not re.fullmatch(r'[A-Za-z0-9.-]+\.', prefix):
            raise ValueError('Invalid configured launchd label prefix')
        return prefix + service.removesuffix(".service")

    def _launch_domain(self) -> str:
        if self.uid is None:
            raise RuntimeError("macOS launchd user ID is unavailable")
        return f"gui/{self.uid}"

    def _launch_target(self, service: str) -> str:
        return f"{self._launch_domain()}/{self.label(service)}"

    def _members(self, service: str) -> tuple[str, ...] | None:
        if self.platform == "linux":
            return None
        for config in INSTANCE_LAYOUT.values():
            if service == config["target"]:
                return tuple(config["services"])
        return None

    def status(self, service: str) -> dict[str, Any]:
        members = self._members(service)
        if members is not None:
            states = [self.status(member) for member in members]
            loaded = all(item["load"] == "loaded" for item in states)
            active = all(item["active"] == "active" for item in states)
            return _state(service, load="loaded" if loaded else "not-found",
                          active="active" if active else "inactive",
                          sub="running" if active else "dead")
        if self.platform == "linux":
            result = self.run("systemctl", "--user", "show", service,
                              "-p", "LoadState", "-p", "ActiveState",
                              "-p", "SubState", "-p", "MainPID", check=False)
            values = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
            return _state(service, load=values.get("LoadState", "not-found"),
                          active=values.get("ActiveState", "inactive"),
                          sub=values.get("SubState", "dead"),
                          pid=int(values.get("MainPID") or 0))
        if self.platform == "macos":
            result = self.run("launchctl", "print", self._launch_target(service), check=False)
            if result.returncode:
                exists = (self.agent_root / f"{self.label(service)}.plist").is_file()
                return _state(service, load="loaded" if exists else "not-found",
                              active="inactive", sub="dead")
            output = result.stdout or ""
            state = re.search(r"(?m)^\s*state\s*=\s*(\w+)", output)
            pid = re.search(r"(?m)^\s*pid\s*=\s*(\d+)", output)
            running = state is not None and state.group(1) == "running"
            return _state(service, load="loaded", active="active" if running else "inactive",
                          sub=state.group(1) if state else "waiting",
                          pid=int(pid.group(1)) if pid else 0)
        result = self.run("sc.exe", "queryex", service, check=False)
        if result.returncode:
            if result.returncode == 1060:
                return _state(service, load="not-found", active="inactive", sub="dead")
            raise RuntimeError(f"Cannot query Windows service {service} (exit {result.returncode}): {(result.stdout or '').strip()}")
        output = result.stdout or ""
        state = re.search(r"(?m)^\s*STATE\s*:\s*\d+\s+([A-Z_]+)", output)
        pid = re.search(r"(?m)^\s*PID\s*:\s*(\d+)", output)
        name = state.group(1) if state else "UNKNOWN"
        active = "active" if name == "RUNNING" else "failed" if name == "UNKNOWN" else "inactive"
        return _state(service, load="loaded", active=active,
                      sub=name.lower(), pid=int(pid.group(1)) if pid and name != "STOPPED" else 0)

    def health(self, service: str, probe: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
        state = self.status(service)
        result = probe() if probe is not None else {"ok": True}
        return {**state, "ok": state["active"] == "active" and bool(result.get("ok")),
                "probe": result}

    def start(self, *services: str) -> None:
        for service in services:
            members = self._members(service)
            if members is not None:
                self.start(*members)
            elif self.platform == "linux":
                self.run("systemctl", "--user", "start", service)
            elif self.platform == "macos":
                state = self.status(service)
                if state["active"] == "active":
                    continue
                target = self._launch_target(service)
                if state["load"] != "loaded":
                    raise RuntimeError(f"launchd job is not installed: {target}")
                current = self.run("launchctl", "print", target, check=False)
                if current.returncode:
                    self.run("launchctl", "bootstrap", self._launch_domain(),
                             str(self.agent_root / f"{self.label(service)}.plist"))
                else:
                    self.run("launchctl", "kickstart", target)
            else:
                state = self.status(service)
                if state["load"] != "loaded":
                    raise RuntimeError(f"Windows service is not installed: {service}")
                if state["active"] != "active":
                    self.run("sc.exe", "start", service)

    def stop(self, *services: str) -> None:
        for service in services:
            members = self._members(service)
            if members is not None:
                self.stop(*reversed(members))
            elif self.platform == "linux":
                self.run("systemctl", "--user", "stop", service)
            elif self.platform == "macos":
                target = self._launch_target(service)
                if not self.run("launchctl", "print", target, check=False).returncode:
                    self.run("launchctl", "bootout", target)
            else:
                state = self.status(service)
                if state["load"] != "not-found" and state["sub"] not in {"stopped", "dead"}:
                    self.run("sc.exe", "stop", service)
                    self._wait_stopped(service)

    def _wait_stopped(self, service: str, timeout: float = 90) -> None:
        deadline = time.monotonic() + timeout
        while self.status(service)["sub"] not in {"stopped", "dead"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Windows service did not stop: {service}")
            time.sleep(0.2)

    def restart(self, service: str) -> None:
        if self.platform == "linux":
            self.run("systemctl", "--user", "restart", service)
        elif self.platform == "macos":
            target = self._launch_target(service)
            if self.run("launchctl", "print", target, check=False).returncode:
                self.start(service)
            else:
                self.run("launchctl", "kickstart", "-k", target)
        else:
            self.stop(service)
            self.start(service)

    def reload(self, service: str) -> None:
        if self.platform != "linux":
            raise NotImplementedError(f"{self.platform} does not support Portal's Linux hot reload: {service}")
        self.run("systemctl", "--user", "reload", service)

    def signal_main(self, service: str, name: str) -> None:
        if self.platform != "linux":
            raise NotImplementedError(f"{self.platform} does not support Portal's Linux service signal: {service}")
        if not hasattr(signal, name):
            raise ValueError(f"unknown signal: {name}")
        self.run("systemctl", "--user", "kill", "--kill-whom=main", f"--signal={name}", service)
