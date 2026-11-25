"""One service-control contract for Portal's persistent services.

This module owns only operating-system service operations; it never decides
which Portal component should be restarted or when a request may be
interrupted.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from management_common.site_config import read_site_config
from management_common.native_output import run_native_capture


Runner = Callable[..., subprocess.CompletedProcess[str]]
_CODEX_SHUTDOWN_TIMEOUT = 65  # TERM and KILL windows in the account host, plus cleanup.
_LAUNCHD_PID = re.compile(r"(?m)^\s*pid\s*=\s*([1-9]\d*)\s*$")


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = run_native_capture(args, timeout=None, merge_output=True)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} exited with {result.returncode}: {(result.stdout or '')[-4000:].strip()}")
    return result


def _state(service: str, *, load: str, active: str, sub: str, pid: int = 0) -> dict[str, Any]:
    return {"service": service, "load": load, "active": active,
            "sub": sub, "pid": pid}


class ServiceControl:
    """start/stop/restart/status for a registered persistent service."""

    def __init__(self, *, platform: str | None = None, runner: Runner | None = None,
                 agent_root: Path | None = None, uid: int | None = None, shutdown_state: Path | None = None):
        value = platform or sys.platform
        self.platform = "windows" if value == "win32" else "macos" if value == "darwin" else "linux" if value.startswith("linux") else value
        if self.platform not in {"linux", "macos", "windows"}:
            raise RuntimeError(f"Portal service control does not support {value}")
        self.run = runner or _run
        self.agent_root = agent_root or Path.home() / "Library" / "LaunchAgents"
        self.uid = os.getuid() if uid is None and hasattr(os, "getuid") else uid
        self.shutdown_state = shutdown_state

    @staticmethod
    def label(service: str) -> str:
        prefix = 'com.tfi.portal.'
        if not isinstance(prefix, str) or not re.fullmatch(r'[A-Za-z0-9.-]+\.', prefix):
            raise ValueError('Invalid configured launchd label prefix')
        return prefix + service.removesuffix(".service")

    def _launch_domain(self) -> str:
        if self.uid is None:
            raise RuntimeError("macOS launchd user ID is unavailable")
        return f"gui/{self.uid}"

    def _launch_target(self, service: str) -> str:
        return f"{self._launch_domain()}/{self.label(service)}"

    def is_enabled(self, service: str) -> bool:
        """Read persistent startup policy separately from process liveness."""
        if self.platform == "linux":
            result = self.run("systemctl", "--user", "is-enabled", service, check=False)
            return result.returncode == 0 and result.stdout.strip() in {"enabled", "enabled-runtime"}
        if self.platform == "macos":
            result = self.run("launchctl", "print-disabled", self._launch_domain())
            match = re.search(r'"' + re.escape(self.label(service)) + r'"\s*=>\s*(true|false)', result.stdout or "")
            return (self.agent_root / f"{self.label(service)}.plist").is_file() and not (match and match[1] == "true")
        raise RuntimeError("Persistent service switches require Linux or macOS")

    def set_enabled(self, service: str, enabled: bool) -> None:
        """Persist startup policy and converge the process without restarting it."""
        if self.platform == "linux":
            self.run("systemctl", "--user", "enable" if enabled else "disable", "--now", service)
        elif self.platform == "macos":
            self.run("launchctl", "enable" if enabled else "disable", self._launch_target(service))
            (self.start if enabled else self.stop)(service)
        else:
            raise RuntimeError("Persistent service switches require Linux or macOS")

    def status(self, service: str) -> dict[str, Any]:
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
            pid = _LAUNCHD_PID.search(output)
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

    def start(self, *services: str) -> None:
        for service in services:
            if self.platform == "linux":
                self.run("systemctl", "--user", "start", service)
            elif self.platform == "macos":
                state = self.status(service)
                if state["active"] == "active":
                    continue
                target = self._launch_target(service)
                if state["load"] != "loaded":
                    raise RuntimeError(f"launchd job is not installed: {target}")
                if "-codex-app-server@" in service:
                    self._drain_codex_host(service, 0)
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
            if self.platform == "linux":
                self.run("systemctl", "--user", "stop", service)
            elif self.platform == "macos":
                target = self._launch_target(service)
                job = self.run("launchctl", "print", target, check=False)
                if "-codex-app-server@" in service:
                    pid = _LAUNCHD_PID.search(job.stdout or "") if not job.returncode else None
                    self._drain_codex_host(service, int(pid.group(1)) if pid else 0)
                if not job.returncode:
                    self.run("launchctl", "bootout", target)
                    self._wait_stopped(service)
            else:
                state = self.status(service)
                if state["load"] != "not-found" and state["sub"] not in {"stopped", "dead"}:
                    self.run("sc.exe", "stop", service)
                    self._wait_stopped(service)

    def _drain_codex_host(self, service: str, pid: int) -> None:
        # The account host starts Codex in a separate process group. Keep the
        # launchd job installed while the host handles SIGTERM and reaps it.
        import psutil
        from management_common.configuration_files import atomic_json

        state = self.shutdown_state / (service + ".shutdown.json") if self.shutdown_state else None
        processes = {}
        if state is not None and state.is_file():
            # A resumed update may find the launcher gone. Wait for the same
            # children even after they have been reparented or the job unloaded.
            for child_pid, created_at in json.loads(state.read_text(encoding="utf-8"))["processes"]:
                try:
                    process = psutil.Process(child_pid)
                    if process.create_time() == created_at:
                        processes[child_pid] = process
                except psutil.NoSuchProcess:
                    continue

        host = None
        if pid:
            try:
                host = psutil.Process(pid)
                for process in [host, *host.children(recursive=True)]:
                    processes[process.pid] = process
            except psutil.NoSuchProcess:
                pass
        if processes and state is not None:
            identities = [[process.pid, process.create_time()] for process in processes.values()]
            atomic_json(state, {"processes": identities})
        if host is not None:
            try:
                host.terminate()
            except psutil.NoSuchProcess:
                pass
        if processes:
            _, running = psutil.wait_procs(list(processes.values()), timeout=_CODEX_SHUTDOWN_TIMEOUT)
            if running:
                pids = ", ".join(str(process.pid) for process in running)
                raise TimeoutError(f"Codex account host did not drain for {service} (PIDs: {pids})")
        if state is not None:
            state.unlink(missing_ok=True)

    def _wait_stopped(self, service: str, timeout: float = 90) -> None:
        deadline = time.monotonic() + timeout
        while True:
            state = self.status(service)
            stopped = (state["sub"] in {"stopped", "dead"} if self.platform == "windows"
                       else state["active"] in {"inactive", "failed"} and not state["pid"])
            if stopped:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{self.platform} service did not stop: {service}")
            time.sleep(0.2)

    def restart(self, service: str) -> None:
        if self.platform == "linux":
            self.run("systemctl", "--user", "restart", service)
        elif self.platform == "macos":
            target = self._launch_target(service)
            if "-codex-app-server@" in service:
                self.stop(service)
                self.start(service)
            elif self.run("launchctl", "print", target, check=False).returncode:
                self.start(service)
            else:
                self.run("launchctl", "kickstart", "-k", target)
        else:
            self.stop(service)
            self.start(service)
