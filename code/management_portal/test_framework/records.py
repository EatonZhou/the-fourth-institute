from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from management_common.format_tags import schema_matches
from management_common.directory_retention import enqueue_directory_cleanup
from management_portal.execution.activity_redaction import redact_sensitive_text
from management_portal.business_tasks import process_identity, process_is_alive
from management_portal.shared_state import atomic_replace_json, exclusive_file_lock
from management_portal.test_framework.run_ids import new_test_run_id
from management_portal.test_framework.tau_usage_report import report_counts


LOGGER = logging.getLogger(__name__)


TERMINAL = frozenset({"reported", "passed", "failed", "cancelled", "error", "unsupported"})
# Why a result did not pass.  The framework sets what it can tell from the run
# (a precondition that kept the case from running, a runner or process that
# broke); a failed verdict stays ``unknown`` until someone looks at it.
FAILURE_CLASSES = ("product", "test", "environment", "precondition", "concurrent-change", "unknown")


def failure_class(status: str, returncode: int | None, details: dict[str, Any]) -> str:
    """The failure class of one result ('' when it passed or was cancelled)."""

    if status in {"reported", "passed", "cancelled"}:
        return ""
    declared = str(details.get("failure_class") or "")
    if declared in FAILURE_CLASSES:
        return declared
    if status == "unsupported":
        return "precondition"
    if status == "error" or (returncode is not None and returncode < 0):
        return "environment"
    return "unknown"


def aggregate_status(statuses: list[str]) -> str:
    """The most severe status: error, unsupported, failed, cancelled, then passed."""
    for candidate in ("error", "unsupported", "failed", "cancelled"):
        if candidate in statuses:
            return candidate
    return "reported" if "reported" in statuses else "passed"


def is_tau_run(record: dict[str, Any]) -> bool:
    plan = record.get("plan") if isinstance(record.get("plan"), dict) else {}
    return plan.get("campaign") == "tau" or any(
        selected.get("runner", {}).get("id") == "tau" for selected in plan.get("selected", [])
    )


OUTPUT_LIMIT = 180_000
GROUP_OUTPUT_LIMIT = 60_000


def _redact_run_data(value: Any, *, _depth: int = 0) -> Any:
    """Redact free-form runner output without interpreting catalog field names."""
    if _depth >= 30:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            key: _redact_run_data(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_run_data(item, _depth=_depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_run_data(item, _depth=_depth + 1) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _executor_alive(record: dict[str, Any]) -> bool:
    owner = record.get("executor")
    if isinstance(owner, dict):
        return process_is_alive(owner)
    # Records from before executor identities were introduced keep their PID
    # until they finish or are recovered; new claims always store an identity.
    try:
        value = int(record.get("executor_pid"))
        if value <= 0:
            return False
        os.kill(value, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


class RunRecords:
    """Owns durable Run Result records; it does not know runner commands."""

    def __init__(self, path: Path):
        self.path = path
        self.records_root = path.with_suffix("")
        self._lock = threading.RLock()

    @contextmanager
    def _locked(self):
        # Independent web workers and CLI executors update one index and may
        # update the same Run. Keep each complete read/modify/write serialized.
        with self._lock, exclusive_file_lock(self.path.with_suffix(".lock")):
            yield

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"测试运行记录无法读取：{self.path}") from exc
        if (
            not isinstance(raw, dict)
            or not schema_matches(raw.get("schema"), expected="tfi.test-run-index.v1")
            or not isinstance(raw.get("runs"), list)
        ):
            raise RuntimeError(f"测试运行记录结构无效：{self.path}")
        return [dict(item) for item in raw["runs"] if isinstance(item, dict)]

    @staticmethod
    def _run_id(value: object) -> str:
        run_id = str(value or "").strip()
        if not re.fullmatch(r"testrun_[0-9a-f]{16}", run_id):
            raise ValueError("测试运行 ID 无效")
        return run_id

    def _record_path(self, run_id: object) -> Path:
        return self.records_root / f"{self._run_id(run_id)}.json"

    def _read_record(self, run_id: object) -> dict[str, Any]:
        path = self._record_path(run_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"测试运行记录无法读取：{path}") from exc
        if not isinstance(record, dict) or record.get("run_id") != self._run_id(run_id):
            raise RuntimeError(f"测试运行记录结构无效：{path}")
        return record

    @staticmethod
    def _index_entry(record: dict[str, Any]) -> dict[str, Any]:
        plan = record.get("plan") if isinstance(record.get("plan"), dict) else {}
        selected = []
        for item in plan.get("selected") or []:
            if not isinstance(item, dict):
                continue
            runner = item.get("runner") if isinstance(item.get("runner"), dict) else {}
            selected.append({
                "case_id": str(item.get("case_id") or ""),
                "runner": {"id": str(runner.get("id") or "")},
            })
        return {
            "run_id": str(record.get("run_id") or ""),
            "status": str(record.get("status") or ""),
            "created_by": str(record.get("created_by") or ""),
            "created_at": str(record.get("created_at") or ""),
            "started_at": str(record.get("started_at") or ""),
            "finished_at": str(record.get("finished_at") or ""),
            **({"usage_summary": report_counts(record.get("results") or [])} if is_tau_run(record) else {}),
            "plan": {
                "campaign": str(plan.get("campaign") or ""),
                "selected": selected,
            },
        }

    @staticmethod
    def _replace_index_entry(index: list[dict[str, Any]], record: dict[str, Any]) -> None:
        run_id = str(record.get("run_id") or "")
        for position, item in enumerate(index):
            if item.get("run_id") == run_id:
                index[position] = RunRecords._index_entry(record)
                return
        raise KeyError(f"未知测试运行：{run_id}")

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_replace_json(path, payload, mode=0o600, indent=None, sort_keys=False)

    def _write_index(self, index: list[dict[str, Any]]) -> None:
        self._write_json(self.path, {
            "schema": "tfi.test-run-index.v1",
            "runs": index,
        })

    def _write_record(self, record: dict[str, Any]) -> None:
        clean = _redact_run_data(record)
        self._write_json(self._record_path(clean.get("run_id")), clean)

    def _mutate(self, run_id: str, mutation) -> dict[str, Any]:
        with self._locked():
            index = self._read_index()
            if not any(item.get("run_id") == run_id for item in index):
                raise KeyError(f"未知测试运行：{run_id}")
            record = self._read_record(run_id)
            mutation(record)
            record["updated_at"] = now_iso()
            self._write_record(record)
            self._replace_index_entry(index, record)
            self._write_index(index)
            return dict(record)

    def create(self, plan: dict[str, Any], actor: str) -> dict[str, Any]:
        run_id = new_test_run_id()
        stamp = now_iso()
        group_specs = plan.get("execution", {}).get("groups") or []
        record = {
            "run_id": run_id,
            "plan": plan,
            "status": "queued",
            "created_by": actor,
            "executor": process_identity(),
            "created_at": stamp,
            "updated_at": stamp,
            "started_at": "",
            "finished_at": "",
            "current_case_id": "",
            "active_case_ids": [],
            "output": "",
            "results": [],
            "error": "",
            "groups": [
                {
                    "group_id": str(group.get("group_id") or ""),
                    "module_id": str(group.get("module_id") or ""),
                    "label": str(group.get("label") or ""),
                    "lane": str(group.get("lane") or ""),
                    "runner": str(group.get("runner") or "").strip(),
                    "status": "queued",
                    "current_case_id": "",
                    "output": "",
                    "results": [],
                    "error": "",
                    "active_invocation": {},
                }
                for group in group_specs
            ],
            "report": {},
        }
        with self._locked():
            index = self._read_index()
            index.append(self._index_entry(record))
            self._write_record(record)
            self._write_index(index)
        return dict(record)

    def run_ids(self) -> set[str]:
        with self._locked():
            return {str(item.get("run_id") or "") for item in self._read_index()}

    def invocation_holders(self) -> dict[tuple[str, str], str]:
        """Persisted TAU invocations reserve their module across executor restarts."""
        with self._locked():
            holders = {}
            for item in self._read_index():
                if item.get("status") in TERMINAL:
                    continue
                record = self._read_record(item["run_id"])
                for group in record.get("groups") or []:
                    if group.get("active_invocation"):
                        holders[(item["run_id"], group["group_id"])] = group.get("status", "")
            return holders

    def get(self, run_id: str) -> dict[str, Any]:
        with self._locked():
            if any(item.get("run_id") == run_id for item in self._read_index()):
                return dict(self._read_record(run_id))
        raise KeyError(f"未知测试运行：{run_id}")

    def delete_many(self, run_ids: list[str], *, run_root: Path | None = None) -> list[dict[str, Any]]:
        """Atomically delete terminal Run Result records."""
        normalized = list(dict.fromkeys(str(run_id or "").strip() for run_id in run_ids))
        if not normalized or any(not run_id for run_id in normalized):
            raise ValueError("请选择要删除的测试运行报告")
        with self._locked():
            index = self._read_index()
            known = {str(item.get("run_id") or "") for item in index}
            missing = [run_id for run_id in normalized if run_id not in known]
            if missing:
                raise KeyError(f"未知测试运行：{missing[0]}")
            records = {run_id: self._read_record(run_id) for run_id in normalized}
            active = [run_id for run_id in normalized if records[run_id].get("status") not in TERMINAL]
            if active:
                raise ValueError(f"运行尚未结束，不能删除报告：{active[0]}")
            deleted = [dict(records[run_id]) for run_id in normalized]
            deleting = set(normalized)
            from management_common.directory_cleanup_store import get_directory_cleanup_store
            queue = get_directory_cleanup_store()
            with queue.database.transaction() as connection:
                for run_id in normalized:
                    paths = [self._record_path(run_id)]
                    if run_root is not None:
                        paths.append(run_root / run_id)
                    for path in paths:
                        enqueue_directory_cleanup(path.absolute(), owner_id=run_id, operation_id='report-delete:' + run_id,
                                      actor='directory-retention', connection=connection)
            self._write_index([item for item in index if item.get("run_id") not in deleting])
            return deleted

    def update(self, run_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        patch = _redact_run_data(patch)
        record = self._mutate(run_id, lambda item: item.update(patch))
        if patch.get('status') in TERMINAL:
            return self.finish_cleanup(run_id)
        return record

    @staticmethod
    def _cleanup_receipt(run_id):
        from .run_cleanup import finish_run
        try:
            return finish_run(run_id)
        except Exception as error:
            return {'status': 'error', 'errors': [str(error)]}

    @staticmethod
    def _apply_cleanup_receipt(item, receipt):
        previous = item.get('resource_cleanup', {})
        changed = previous != receipt
        # This field is a diagnostic summary, not a log of every maintenance
        # attempt. Retrying the same unresolved error must not grow it forever.
        new_errors = [error for error in receipt.get('errors', [])
                      if error not in previous.get('errors', [])]
        item['resource_cleanup'] = receipt
        if receipt['status'] == 'error':
            changed = changed or item.get('status') != 'error'
            item['status'] = 'error'
            if new_errors:
                item['error'] = '；'.join(filter(None, [item.get('error', ''), *new_errors]))
        return changed

    def finish_cleanup(self, run_id):
        receipt = self._cleanup_receipt(run_id)
        return self._mutate(run_id, lambda item: self._apply_cleanup_receipt(item, receipt))

    def finish_pending_cleanup(self):
        """Retry terminal cleanup, then merge receipts with one index commit.

        Records are the truth even when an earlier record-first write crashed
        before updating the index. Domain cleanup must run outside the file
        lock: stopping owned work can need that same lock for final progress.
        """
        with self._locked():
            pending = []
            for entry in self._read_index():
                item = self._read_record(entry['run_id'])
                if item.get('status') in TERMINAL and item.get('resource_cleanup', {}).get('status') != 'complete':
                    pending.append(item['run_id'])
        receipts = [(run_id, self._cleanup_receipt(run_id)) for run_id in pending]
        updated, errors = [], []
        if not receipts:
            return {'updated': updated, 'errors': errors}
        with self._locked():
            index = self._read_index()
            positions = {entry['run_id']: position for position, entry in enumerate(index)}
            index_changed = False
            for run_id, receipt in receipts:
                if run_id not in positions:
                    continue  # A concurrent report deletion owns its cleanup.
                item = self._read_record(run_id)
                if item.get('status') not in TERMINAL or item.get('resource_cleanup', {}).get('status') == 'complete':
                    continue
                # Still report an unchanged failure: suppressing redundant
                # persistence must not turn maintenance health green.
                errors.extend(receipt.get('errors', []))
                changed = self._apply_cleanup_receipt(item, receipt)
                if changed:
                    item['updated_at'] = now_iso()
                    self._write_record(item)
                summary = self._index_entry(item)
                stale_index = index[positions[run_id]] != summary
                if stale_index:
                    index[positions[run_id]] = summary
                    index_changed = True
                if changed or stale_index:
                    index_changed = True
                    updated.append(run_id)
            if index_changed:
                # Same record-first crash contract as ordinary mutation. A
                # partial write is recovered from records, never index status.
                self._write_index(index)
        return {'updated': updated, 'errors': errors}

    def expire(self, run_root: Path, *, now=None):
        """Delete terminal evidence and records seven days after completion.

        Cleanup intent is durable before removing the index entry. A failed file
        deletion keeps its queue reservation, independently of the Run index.
        """
        from management_common.directory_cleanup_store import get_directory_cleanup_store
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=7)
        queue = get_directory_cleanup_store()
        removed = []
        with self._locked():
            index = self._read_index()
            with queue.database.transaction() as connection:
                for item in index:
                    if item.get('status') not in TERMINAL or not item.get('finished_at'):
                        continue
                    if datetime.fromisoformat(item['finished_at'].replace('Z', '+00:00')) > cutoff:
                        continue
                    run_id = self._run_id(item['run_id'])
                    for path in (run_root / run_id, self._record_path(run_id)):
                        enqueue_directory_cleanup(path.absolute(), owner_id=run_id, operation_id='expire:' + run_id,
                                      actor='directory-retention', connection=connection)
                    removed.append(run_id)
            # This file index cannot share the SQL commit. Keep the durable
            # deletion intent first so a failed commit never orphans evidence.
            self._write_index([item for item in index if item['run_id'] not in removed])
        return removed

    def append_output(self, run_id: str, text: str, *, group_id: str = "") -> dict[str, Any]:
        text = redact_sensitive_text(text)
        def append(item):
            item["output"] = (str(item.get("output") or "") + text)[-OUTPUT_LIMIT:]
            if group_id:
                for group in item.get("groups") or []:
                    if group.get("group_id") == group_id:
                        group["output"] = (str(group.get("output") or "") + text)[-GROUP_OUTPUT_LIMIT:]
                        break
        return self._mutate(run_id, append)

    @staticmethod
    def _project_active_cases(item: dict[str, Any]) -> None:
        active = [
            str(group.get("current_case_id") or "")
            for group in item.get("groups") or []
            if group.get("status") == "running" and group.get("current_case_id")
        ]
        item["active_case_ids"] = active
        item["current_case_id"] = active[0] if len(active) == 1 else ""

    def update_group(self, run_id: str, group_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        patch = _redact_run_data(patch)
        def update_group_record(item):
            for group in item.get("groups") or []:
                if group.get("group_id") == group_id:
                    group.update(patch)
                    break
            else:
                raise KeyError(f"未知测试运行分组：{group_id}")
            self._project_active_cases(item)
        return self._mutate(run_id, update_group_record)

    def apply_progress(
        self,
        run_id: str,
        *,
        output: dict[str, str],
        results: list[tuple[str, dict[str, Any]]],
        groups: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Write a batch of group output, results and group changes in one record update.

        An executor running many processes reports through this single write
        per interval instead of rewriting the record for every line or result.
        """
        if not output and not results and not groups:
            return self.get(run_id)
        output = {group_id: redact_sensitive_text(text) for group_id, text in output.items() if text}
        results = [(group_id, _redact_run_data(result)) for group_id, result in results]
        groups = {group_id: _redact_run_data(patch) for group_id, patch in groups.items()}

        def apply(item):
            by_id = {str(group.get("group_id") or ""): group for group in item.get("groups") or []}
            for group_id, text in output.items():
                item["output"] = (str(item.get("output") or "") + text)[-OUTPUT_LIMIT:]
                if group_id in by_id:
                    group = by_id[group_id]
                    group["output"] = (str(group.get("output") or "") + text)[-GROUP_OUTPUT_LIMIT:]
            for group_id, result in results:
                item.setdefault("results", []).append(result)
                if group_id in by_id:
                    by_id[group_id].setdefault("results", []).append(result)
            for group_id, patch in groups.items():
                if group_id not in by_id:
                    raise KeyError(f"未知测试运行分组：{group_id}")
                by_id[group_id].update(patch)
            self._project_active_cases(item)
        return self._mutate(run_id, apply)

    def claim(self, run_id: str, identity: dict[str, Any]) -> dict[str, Any] | None:
        """Make the calling executor the owner of a run; None when there is nothing for it to run.

        A queued run starts. A run whose executor is gone is taken over: a TAU
        Campaign continues where it stopped and a cancelling one finishes its
        cancellation; a local run cannot continue and is closed.
        """
        with self._locked():
            index = self._read_index()
            if not any(item.get("run_id") == run_id for item in index):
                raise KeyError(f"未知测试运行：{run_id}")
            record = self._read_record(run_id)
            stamp = now_iso()
            status = record.get("status")
            if status in TERMINAL:
                return None
            if status == "cancelling" and not record.get("started_at"):
                # Cancelled before any executor started it.
                record.update({"status": "cancelled", "finished_at": stamp})
                for group in record.get("groups") or []:
                    if group.get("status") not in TERMINAL:
                        group["status"] = "cancelled"
            elif status == "queued":
                record.update({"status": "running", "started_at": record.get("started_at") or stamp, "executor": identity})
            elif _executor_alive(record):
                return None
            elif is_tau_run(record):
                record["executor"] = identity
            else:
                self._close_interrupted(record, stamp)
            # Written by framework versions that resumed TAU inside Web.
            record.pop("recovery_pending", None)
            record["updated_at"] = stamp
            self._write_record(record)
            self._replace_index_entry(index, record)
            self._write_index(index)
            return dict(record) if record.get("executor") == identity and record["status"] not in TERMINAL else None

    def append_result(self, run_id: str, group_id: str, result: dict[str, Any]) -> dict[str, Any]:
        result = _redact_run_data(result)
        def append(item):
            item.setdefault("results", []).append(result)
            for group in item.get("groups") or []:
                if group.get("group_id") == group_id:
                    group.setdefault("results", []).append(result)
                    break
        return self._mutate(run_id, append)

    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._locked():
            index = self._read_index()
            selected = index if limit is None else index[-max(1, min(limit, 100)) :]
            return [dict(self._read_record(item.get("run_id"))) for item in reversed(selected)]

    def recent_summaries(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the fields required by the run-history list.

        Full plans, duplicated group results, reports and output remain owned by
        the run-detail endpoint and are loaded only when a user opens a run.
        """
        with self._locked():
            index = self._read_index()
            return [dict(item) for item in reversed(index[-max(1, min(limit, 100)) :])]

    @staticmethod
    def _close_interrupted(item: dict[str, Any], stamp: str) -> None:
        """Close a local run whose executor is gone; its processes went with it."""
        item.update({
            "status": "error",
            "error": "测试执行进程在运行完成前退出。",
            "current_case_id": "",
            "active_case_ids": [],
            "finished_at": stamp,
            "updated_at": stamp,
        })
        for group in item.get("groups") or []:
            if group.get("status") not in TERMINAL:
                group.update({
                    "status": "error",
                    "error": "测试执行进程在本组完成前退出。",
                    "current_case_id": "",
                })

    def recover_interrupted(self, run_ids: set[str] | None = None) -> int:
        """Close started local runs (all, or the given ones) whose executor process is gone."""
        return self._scan_recovery(run_ids, close_interrupted=True)[0]

    def recover_unowned(self, run_ids: set[str] | None = None) -> list[str]:
        """Close interrupted local runs and select resumable work in one scan."""
        return self._scan_recovery(run_ids, close_interrupted=True)[1]

    def _scan_recovery(self, run_ids, *, close_interrupted):
        with self._locked():
            index = self._read_index()
            stamp = now_iso()
            recovered, unowned, changed = 0, [], False
            for position, entry in enumerate(index):
                if run_ids is not None and entry.get("run_id") not in run_ids:
                    continue
                item = self._read_record(entry.get("run_id"))
                if item.get('status') not in TERMINAL and not _executor_alive(item):
                    if close_interrupted and item.get('status') != 'queued' and not is_tau_run(item):
                        self._close_interrupted(item, stamp)
                        recovered += 1
                        self._write_record(item)
                    else:
                        unowned.append(item['run_id'])
                if close_interrupted:
                    summary = self._index_entry(item)
                    if summary != entry:
                        index[position] = summary
                        changed = True
            if changed:
                self._write_index(index)
            return recovered, unowned

    def unowned(self, run_ids: set[str] | None = None) -> list[str]:
        """Unfinished runs (all, or the given ones) that no live executor owns."""
        return self._scan_recovery(run_ids, close_interrupted=False)[1]

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        def cancel(item):
            if item.get("status") not in TERMINAL:
                item["status"] = "cancelling"
        return self._mutate(run_id, cancel)


# A reader retries an executor launch for the same run at most this often.
RELAUNCH_SECONDS = 30.0


class RunCoordinator:
    """Owns the run lifecycle in the Portal process: create, launch, read, cancel, delete.

    Every run executes in its own Test executor unit (``management_portal.test_framework.executor``):
    local runs as sandboxed batches, TAU Campaigns against the real DEV. This
    process starts the unit through ``launch`` and afterwards only reads and
    annotates the run's records, so restarting it never stops a run.
    """

    def __init__(
        self,
        records: RunRecords,
        run_root: Path,
        *,
        launch: Callable[[str], None] | None = None,
    ):
        self.records = records
        self.run_root = run_root
        self.launch = launch or self._launch_unit
        self._lock = threading.Lock()
        self._launched_at: dict[str, float] = {}

    def _launch_unit(self, run_id: str) -> None:
        from management_portal.test_framework.executor import launch_unit

        launch_unit(run_id, self.records.path.parent, tau=is_tau_run(self.records.get(run_id)))

    def start(self, plan: dict[str, Any], actor: str) -> dict[str, Any]:
        if not plan.get("executable") or not plan.get("selected"):
            raise ValueError("运行计划没有可执行的测试实现")
        if is_tau_run({"plan": plan}):
            from management_portal.society_service.application_target_configuration import admission_scope

            # The shared admission lock covers durable acceptance. A freeze
            # waits for this record, while already accepted executors may run.
            with admission_scope("tau"):
                record = self.records.create(plan, actor)
        else:
            record = self.records.create(plan, actor)
        self.remove_orphan_artifacts()
        try:
            self.launch(record["run_id"])
        except Exception as exc:
            return self.records.update(record["run_id"], {
                "status": "error", "error": str(exc), "finished_at": now_iso(),
            })
        return record

    def remove_orphan_artifacts(self) -> list[str]:
        """Remove evidence whose record is gone; active runs are always retained."""
        from management_common.directory_cleanup_store import get_directory_cleanup_store
        queue = get_directory_cleanup_store()
        removed = []
        with self.records._locked(), queue.database.transaction() as connection:
            known = {item['run_id'] for item in self.records._read_index()}
            for root in (self.run_root, self.records.records_root):
                if not root.exists():
                    continue
                for path in root.iterdir():
                    name = path.stem if root == self.records.records_root else path.name
                    if re.fullmatch(r'testrun_[0-9a-f]{16}', name) and name not in known:
                        enqueue_directory_cleanup(path.absolute(), owner_id=name, operation_id='orphan:' + name,
                                      actor='directory-retention', connection=connection)
                        removed.append(name)
        queue.run_pending()
        return removed

    def recover(self, run_ids: set[str] | None = None) -> list[str]:
        """Close local runs whose executor is gone and relaunch an executor for every other unowned run.

        Runs once when Web starts (after a host restart no unit survives) and
        whenever a reader finds a run without a live executor. A relaunched
        executor takes a TAU Campaign over; the unit name admits one executor
        per run, so a launch for a run whose unit is still active fails harmlessly.
        """
        launched = []
        for run_id in self.records.recover_unowned(run_ids):
            now = time.monotonic()
            with self._lock:
                last = self._launched_at.get(run_id)
                if last is not None and now - last < RELAUNCH_SECONDS:
                    continue
                self._launched_at[run_id] = now
            try:
                self.launch(run_id)
            except Exception as exc:  # noqa: BLE001 - a later reader retries
                LOGGER.warning("Test executor relaunch failed for %s: %s", run_id, exc)
                continue
            launched.append(run_id)
        return launched

    def get(self, run_id: str, actor: str) -> dict[str, Any]:
        if not actor:
            raise PermissionError("需要已认证用户")
        record = self.records.get(run_id)
        if record.get("status") not in TERMINAL and not _executor_alive(record):
            self.recover({run_id})
            record = self.records.get(run_id)
        if is_tau_run(record):
            record["usage_summary"] = report_counts(record.get("results") or [])
        return record

    def cancel(self, run_id: str, actor: str) -> dict[str, Any]:
        record = self.get(run_id, actor)
        if record.get("created_by") != actor:
            raise PermissionError("只有运行发起者可以取消测试运行")
        if record.get("status") in TERMINAL:
            return record
        # The executor sees the persisted request and stops its work.
        return self.records.request_cancel(run_id)

    def delete_reports(self, run_ids: list[str], actor: str) -> dict[str, Any]:
        if not actor:
            raise PermissionError("需要已认证用户")
        from management_common.directory_cleanup_store import get_directory_cleanup_store
        deleted = self.records.delete_many(run_ids, run_root=self.run_root)
        outcomes = get_directory_cleanup_store().run_pending()
        artifact_warnings = [f"运行产物清理失败：{row['owner_id']}（{row['last_error']}）"
                             for row in outcomes if row['status'] != 'completed' and row['owner_id'] in run_ids]
        return {
            "schema": "tfi.test-run-deletion.v1",
            "deleted_run_ids": [str(item["run_id"]) for item in deleted],
            "deleted_count": len(deleted),
            "artifact_warnings": artifact_warnings,
        }
