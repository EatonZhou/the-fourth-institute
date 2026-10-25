"""Test executor: runs one Test run outside the Portal Web process.

``RunCoordinator`` starts it as a systemd user unit (``tfi-testrun-<id>``)
on Linux, or as an independent Windows Job with a lifetime launch lock,
with ``python -m management_portal.test_framework.executor --state-root <dir> <run_id>``. The
unit is outside the Web cgroup and has a low CPU weight, so tests neither
compete with Portal on equal terms nor stop when Web restarts. The executor
claims the run and writes progress through ``RunRecords``: a local run runs
all groups of its plan concurrently within the host-wide slot budget; a TAU
Campaign runs its module groups against the real DEV (``tau_campaign``).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from management_common import portal_paths
from management_portal.business_tasks import process_identity
from management_portal.test_framework.catalog import ROOT
from management_portal.test_framework.local_runs import PytestGroup, group_runner
from management_portal.test_framework.paths import instance_name
from management_portal.test_framework.records import TERMINAL, RunRecords, aggregate_status, failure_class, is_tau_run, now_iso


FLUSH_SECONDS = 2.0
from management_common.site_compatibility import compatibility_value

UNIT_PREFIX = compatibility_value("test_framework.unit_prefix", "tfi-testrun-")
if not isinstance(UNIT_PREFIX, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+-", UNIT_PREFIX):
    raise ValueError("test_framework.unit_prefix must be a unit-name prefix")
FAILED_STATUSES = {"failed", "error"}


# A TAU Campaign reaches Residents through the Portal services exactly as Web does.
SERVICE_ROUTING = {
    "PORTAL_ISA_ROUTING": "socket",
    "PORTAL_SOCIETY_SERVICE_MODE": "client",
    "PORTAL_RESOURCE_SERVICE_MODE": "client",
}
SECRET_NAME = re.compile(r"DATABASE_URL|TOKEN|SECRET|PASSWORD|_KEY\b")


def unit_name(run_id: str) -> str:
    return UNIT_PREFIX + run_id.removeprefix("testrun_")


def postgres_environment_file() -> str:
    from management_common.postgres_configuration import postgres_env_file
    return str(postgres_env_file())


def unit_environment(state_root: Path, *, tau: bool) -> dict[str, str]:
    """The environment of an executor unit; it never carries a secret.

    A TAU Campaign gets the Portal service routing and test settings of the
    launching process; the unit reads the database URL from the PostgreSQL
    environment file, so the URL never appears in a command line.
    """
    environment = {
        "PORTAL_TEST_FRAMEWORK_STATE_ROOT": str(state_root),
        "PORTAL_INSTANCE": instance_name(),
        **{
            key: os.environ[key]
            for key in ("PATH", "PLAYWRIGHT_BROWSERS_PATH", "PORTAL_TEST_STORAGE_STATE", "PORTAL_TEST_SLOT_ROOT", "PORTAL_TEST_MAX_CONCURRENCY",
                        "PORTAL_SITE_CONFIG", "PORTAL_PATHS_CONFIG", *portal_paths.ENV_KEYS.values())
            if os.environ.get(key)
        },
    }
    if tau:
        # Browser binaries and the local profile belong to this instance's
        # installation, not to the systemd user manager's default HOME/cache.
        for key in ("HOME",):
            if os.environ.get(key):
                environment[key] = os.environ[key]
        environment.update({
            key: value for key, value in os.environ.items()
            if key.startswith("PORTAL_") and value and not SECRET_NAME.search(key) and key not in environment
        })
        for key, value in SERVICE_ROUTING.items():
            environment.setdefault(key, value)
    return environment


def launch_unit(run_id: str, state_root: Path, *, tau: bool = False) -> None:
    """Start one executor outside the Web process's lifecycle."""
    if sys.platform == "win32":
        from management_portal.test_framework.native_executor import launch

        launch(run_id, state_root, tau=tau)
        return
    command = [
        "systemd-run", "--user", "--collect", "--quiet", f"--unit={unit_name(run_id)}",
        # Tests yield to Portal under contention instead of sharing CPU equally.
        "--property=CPUWeight=20", "--property=IOWeight=20", "--nice=10",
        "--property=TasksMax=4096", "--property=KillMode=control-group", "--property=TimeoutStopSec=15",
        # A TAU Campaign lasts as long as its cases; a local run is bounded.
        "--property=RuntimeMaxSec=" + ("infinity" if tau else "28800"),
        *(["--property=EnvironmentFile=" + postgres_environment_file()] if tau else []),
        f"--working-directory={ROOT}",
        *(f"--setenv={key}={value}" for key, value in unit_environment(state_root, tau=tau).items()),
        sys.executable, "-m", "management_portal.test_framework.executor", "--state-root", str(state_root), run_id,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("测试执行单元启动失败：" + (result.stderr or result.stdout)[-2000:])


class _Progress:
    """Buffers one run's output, results and group changes into periodic record writes."""

    def __init__(self, records: RunRecords, run_id: str):
        self.records = records
        self.run_id = run_id
        self._lock = threading.Lock()
        self._output: dict[str, str] = {}
        self._results: list[tuple[str, dict[str, Any]]] = []
        self._groups: dict[str, dict[str, Any]] = {}
        self.stop_reason = ""

    def output(self, group_id: str, text: str) -> None:
        with self._lock:
            self._output[group_id] = self._output.get(group_id, "") + text

    def result(self, group_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._results.append((group_id, result))

    def patch(self, group_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            self._groups.setdefault(group_id, {}).update(fields)

    def stop(self, reason: str) -> None:
        with self._lock:
            self.stop_reason = self.stop_reason or reason

    def flush(self) -> None:
        with self._lock:
            output, self._output = self._output, {}
            results, self._results = self._results, []
            groups, self._groups = self._groups, {}
        record = self.records.apply_progress(self.run_id, output=output, results=results, groups=groups)
        if record.get("status") == "cancelling":
            self.stop("cancelled")


class _GroupContext:
    def __init__(self, progress: _Progress, group: dict[str, Any], selected: list[dict[str, Any]], run_dir: Path):
        self.progress = progress
        self.group = group
        self.group_id = str(group["group_id"])
        self.selected = selected
        self.run_dir = run_dir
        self.statuses: list[str] = []
        self.final_status = ""
        self.error = ""

    def output(self, text: str) -> None:
        self.progress.output(self.group_id, text)

    def started(self, item_index: int) -> None:
        self.progress.patch(self.group_id, {"current_case_id": self.selected[item_index]["case_id"]})

    def result(self, item_index: int, status: str, returncode: int | None, details: dict[str, Any]) -> None:
        item = self.selected[item_index]
        details = dict(details)
        classification = failure_class(status, returncode, details)
        details.pop("failure_class", None)
        self.statuses.append(status)
        self.progress.result(self.group_id, {
            "case_id": item["case_id"],
            "implementation_id": item["implementation"]["id"],
            "runner": item["runner"]["id"],
            "module_id": str(item.get("module") or ""),
            "group_id": self.group_id,
            "item_index": item_index,
            "status": status,
            **({"failure_class": classification} if classification else {}),
            "returncode": returncode,
            **({"details": details} if details else {}),
        })
        if status in FAILED_STATUSES and not self.group.get("continue_on_failure", True):
            self.progress.stop("fail-fast")

    def patch(self, fields: dict[str, Any]) -> None:
        self.progress.patch(self.group_id, fields)

    def stop_reason(self) -> str:
        return self.progress.stop_reason


class LocalRunExecutor:
    """Executes the groups of one claimed local run and publishes its final status."""

    def __init__(
        self,
        records: RunRecords,
        run_root: Path,
        *,
        runner_factory: Callable[..., PytestGroup] = group_runner,
        flush_seconds: float = FLUSH_SECONDS,
    ):
        self.records = records
        self.run_root = run_root
        self.runner_factory = runner_factory
        self.flush_seconds = flush_seconds

    def run(self, run_id: str) -> str:
        record = self.records.claim(run_id, process_identity())
        if record is None:
            return ""
        progress = _Progress(self.records, run_id)
        run_dir = self.run_root / run_id
        contexts: list[_GroupContext] = []
        errors: list[str] = []
        try:
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            selected = record["plan"]["selected"]
            threads = []
            for group in record["plan"].get("execution", {}).get("groups") or []:
                context = _GroupContext(progress, group, selected, run_dir)
                contexts.append(context)
                thread = threading.Thread(
                    target=self._run_group, args=(context, selected, errors),
                    name=f"test-group-{group['group_id']}", daemon=True,
                )
                threads.append(thread)
                thread.start()
            while any(thread.is_alive() for thread in threads):
                time.sleep(self.flush_seconds)
                progress.flush()
            progress.flush()
        except Exception as exc:
            errors.append(f"测试执行器异常：{exc}")
            progress.flush()
        statuses = [context.final_status or "error" for context in contexts]
        if len(errors) > sum(1 for context in contexts if context.error):
            statuses.append("error")  # the executor itself failed, not only a group
        status = aggregate_status(statuses) if statuses else "passed"
        self.records.update(run_id, {
            "status": status,
            "error": "；".join(errors),
            "current_case_id": "",
            "active_case_ids": [],
            "finished_at": now_iso(),
        })
        return status

    def _run_group(self, context: _GroupContext, selected: list[dict[str, Any]], errors: list[str]) -> None:
        group = context.group
        facts: dict[str, Any] = {}
        error = ""
        try:
            runner = self.runner_factory(group, selected)
            facts = runner.run(context) or {}
            error = str(facts.get("error") or "")
        except Exception as exc:
            error = f"{group.get('label') or group['group_id']}：{exc}"
            context.output(traceback.format_exc()[-4000:])
        statuses = list(context.statuses)
        if error:
            # Writing into protected state is a test defect; anything else broke the group.
            statuses.append("failed" if facts.get("isolation_holes") else "error")
            errors.append(error)
        context.error = error
        context.final_status = aggregate_status(statuses) if statuses else "passed"
        context.patch({
            "status": context.final_status,
            "error": error,
            "current_case_id": "",
            "waiting": "",
            **({"isolation_holes": facts["isolation_holes"]} if facts.get("isolation_holes") else {}),
        })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Test run.")
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("run_id")
    arguments = parser.parse_args(argv)
    records = RunRecords(arguments.state_root / "runs.json")
    run_root = arguments.state_root / "run-artifacts"
    try:
        if is_tau_run(records.get(arguments.run_id)):
            from management_portal.test_framework.tau_campaign import TauCampaignExecutor, tau_adapter

            executor = TauCampaignExecutor(records, run_root, tau_adapter(arguments.state_root))
        else:
            executor = LocalRunExecutor(records, run_root)
        status = executor.run(arguments.run_id)
    except Exception as exc:
        # A run this executor owns must not stay open behind it; another
        # executor's run is left to that executor.
        record = records.get(arguments.run_id)
        if record.get("executor") == process_identity() and record.get("status") not in TERMINAL:
            records.update(arguments.run_id, {
                "status": "error", "error": f"测试执行器异常：{exc}",
                "current_case_id": "", "active_case_ids": [], "finished_at": now_iso(),
            })
        raise
    return 0 if status in {"passed", ""} else 1


if __name__ == "__main__":
    sys.exit(main())
