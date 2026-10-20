"""Control the three user services that make up the optional Telepus bridge."""
from __future__ import annotations

import subprocess
import sys
from typing import Any, Callable

from management_common.operation_errors import OperationError


UNITS = (
    "telepus-breakglass-worker.service",
    "telepus-portal-refresh.service",
    "telepus-telegram-router.service",
)


class TelepusRuntime:
    def __init__(self, runner: Callable[..., Any] = subprocess.run):
        self.run = runner

    def status(self) -> dict[str, Any]:
        if sys.platform != "linux":
            return {"available": False, "enabled": False, "running": False,
                    "complete": False, "missing": list(UNITS), "units": [],
                    "error": "Telepus 服务开关目前需要 Linux systemd 用户服务"}
        try:
            result = self.run(
                ["systemctl", "--user", "show", *UNITS, "-p", "Id", "-p", "LoadState",
                 "-p", "ActiveState", "-p", "UnitFileState", "--no-pager"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "enabled": False, "running": False,
                    "complete": False, "missing": list(UNITS), "units": [], "error": str(exc)}
        if result.returncode:
            return {"available": False, "enabled": False, "running": False,
                    "complete": False, "missing": list(UNITS), "units": [],
                    "error": (result.stderr or result.stdout or "无法读取 Telepus 服务状态").strip()}
        parsed = {}
        for block in (result.stdout or "").strip().split("\n\n"):
            values = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if values.get("Id") in UNITS:
                parsed[values["Id"]] = values
        units = [{"name": name,
                  "loaded": parsed.get(name, {}).get("LoadState") == "loaded",
                  "active": parsed.get(name, {}).get("ActiveState") == "active",
                  "enabled": parsed.get(name, {}).get("UnitFileState") in {"enabled", "enabled-runtime"}}
                 for name in UNITS]
        missing = [item["name"] for item in units if not item["loaded"]]
        enabled = any(item["enabled"] or item["active"] for item in units)
        running = all(item["active"] for item in units)
        return {"available": True, "enabled": enabled, "running": running,
                "complete": not missing and running and all(item["enabled"] for item in units),
                "missing": missing, "units": units, "error": ""}

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        before = self.status()
        if not before["available"]:
            raise OperationError(before["error"], code="telepus.service_unavailable", http_status=503)
        if enabled and before["missing"]:
            raise OperationError("缺少 Telepus 服务：" + "、".join(before["missing"]),
                                 code="telepus.service_missing", http_status=409,
                                 commit_state="not_committed")
        # When an installation is incomplete, turning it off must still stop
        # every installed unit.  No contacts or database records are removed.
        units = UNITS if enabled else tuple(item["name"] for item in before["units"] if item["loaded"])
        if not units:
            return before
        command = ["systemctl", "--user", "enable" if enabled else "disable", "--now", *units]
        try:
            result = self.run(command, capture_output=True, text=True, timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OperationError(str(exc), code="telepus.service_control_failed", http_status=503) from exc
        if result.returncode:
            raise OperationError((result.stderr or result.stdout or "Telepus 服务切换失败").strip(),
                                 code="telepus.service_control_failed", http_status=503)
        after = self.status()
        if not after["available"] or (enabled and not after["complete"]) or (not enabled and after["enabled"]):
            raise OperationError("Telepus 服务状态未达到请求的状态，请重新读取服务状态",
                                 code="telepus.service_control_incomplete", http_status=503)
        return after
