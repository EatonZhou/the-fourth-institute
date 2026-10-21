"""TAU Campaign driver: runs one TAU Campaign inside its Test executor unit.

The executor unit (``management_portal.test_framework.executor``) is started by Portal Web but
lives outside it, so publishing and restarting Web never interrupts a
Campaign. Cases act on the explicitly selected instance as ``test-actor``; the unit
is not sandboxed. One thread per module group runs its cases in order, at
most ``max_parallel_modules`` groups at a time.

A unit that ends before its Campaign (host restart, killed process) leaves the
run with a dead executor. Web relaunches the unit; the new executor takes the
run over and continues every module after its last result, waiting on a case
already submitted to a Resident instead of sending it again.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import time
from pathlib import Path
from typing import Any

from management_portal.business_tasks import process_identity
from management_portal.test_framework.catalog import planned_source_changed
from management_portal.test_framework.compatibility import test_actor
from management_portal.test_framework.records import (
    FAILURE_CLASSES,
    TERMINAL,
    RunRecords,
    aggregate_status,
    failure_class,
    now_iso,
)
from management_portal.test_framework.runner_adapters import RunnerAdapter, UnsupportedRunner
from management_portal.test_framework.runner_ids import TAU_RUNNER_ID
from management_portal.test_framework.tau_limits import tau_max_parallel_modules
from management_portal.test_framework import slots


def tau_adapter(state_root: Path) -> RunnerAdapter:
    """The TAU adapter of a Test executor, configured from its environment."""
    from management_portal.test_framework.runner_adapters import TauAdapter
    from management_portal.test_framework.tau import TauConfig
    from management_portal.test_framework.tau_residents import TauResidentAssignments

    residents = TauResidentAssignments(state_root / "tau-residents.json")

    def invoke_resident(**kwargs: Any) -> dict[str, Any]:
        from management_portal.test_framework.tau_runtime import invoke_configured_resident

        return invoke_configured_resident(residents, **kwargs)

    def resume_resident(**kwargs: Any) -> dict[str, Any]:
        from management_portal.resident_handoff import resume_and_wait

        return resume_and_wait(**kwargs)

    return TauAdapter(
        TauConfig.from_environment(), invoke_resident,
        assignments=residents, resume=resume_resident,
    )


def tau_report(record: dict[str, Any]) -> dict[str, Any]:
    results = list(record.get("results") or [])
    counts = {
        name: sum(1 for item in results if item.get("status") == name)
        for name in ("passed", "failed", "error", "cancelled", "unsupported")
    }
    execution = record.get("plan", {}).get("execution", {})
    return {
        "schema": "tfi.portal-tau-report.v1",
        "campaign_id": record.get("run_id"),
        "status": record.get("status"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "summary": {
            "module_count": len(record.get("groups") or []),
            "case_count": len(results),
            **counts,
            "failure_classes": {
                name: count for name in FAILURE_CLASSES
                if (count := sum(1 for item in results if item.get("failure_class") == name))
            },
        },
        "execution": {
            "mode": execution.get("mode"),
            "max_parallel_modules": execution.get("max_parallel_modules"),
            "account": test_actor(),
            "target": "DEV",
        },
        "modules": [
            {
                "module_id": group.get("module_id"),
                "status": group.get("status"),
                "error": group.get("error"),
                "cleanup": group.get("cleanup", {}),
                "cases": sorted(group.get("results") or [], key=lambda item: item.get("item_index", 0)),
            }
            for group in record.get("groups") or []
        ],
    }


class TauCampaignExecutor:
    """Executes the module groups of one claimed TAU Campaign and publishes its report."""

    def __init__(self, records: RunRecords, run_root: Path, adapter: RunnerAdapter):
        self.records = records
        self.run_root = run_root
        self.adapter = adapter

    def run(self, run_id: str) -> str:
        record = self.records.claim(run_id, process_identity())
        if record is None:
            return ""
        run_dir = self.run_root / run_id
        status, error = "passed", ""
        try:
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            selected = record["plan"]["selected"]
            group_specs = record["plan"].get("execution", {}).get("groups") or []
            group_records = {
                str(group.get("group_id") or ""): group
                for group in record.get("groups") or []
            }
            # A taken-over Campaign continues only the modules that have not finished.
            pending_specs = [
                group for group in group_specs
                if group_records.get(str(group.get("group_id") or ""), {}).get("status") not in TERMINAL
                or group_records.get(str(group.get("group_id") or ""), {}).get("active_invocation")
            ]
            pending_specs.sort(key=lambda group: not bool(
                group_records.get(str(group.get("group_id") or ""), {}).get("active_invocation")
            ))
            outcomes: list[tuple[str, str]] = [
                (str(group.get("status") or "error"), str(group.get("error") or ""))
                for group in group_records.values()
                if group.get("status") in TERMINAL and not group.get("active_invocation")
            ]
            if pending_specs:
                execution = record["plan"].get("execution", {})
                try:
                    limit = int(execution.get("max_parallel_modules"))
                except (TypeError, ValueError):
                    limit = tau_max_parallel_modules()
                workers = min(len(pending_specs), max(1, limit), slots.max_concurrency())
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"tau-{run_id}") as pool:
                    futures = [
                        pool.submit(self._execute_group_with_slot, run_id, group, selected, run_dir,
                                    str(record.get("created_by") or ""))
                        for group in pending_specs
                    ]
                    outcomes.extend(future.result() for future in as_completed(futures))
            status = aggregate_status([item[0] for item in outcomes])
            error = "；".join(item[1] for item in outcomes if item[1])
        except Exception as exc:
            status, error = "error", str(exc)
        if any(group.get("active_invocation") for group in self.records.get(run_id).get("groups") or []):
            # An observation failure cannot finalize a Campaign that still owns
            # an unresolved submission/input. A later executor resumes that ID.
            status = "cancelling" if self._is_cancelled(run_id) else "running"
        final_patch: dict[str, Any] = {
            "status": status,
            "error": error,
            "current_case_id": "",
            "active_case_ids": [],
            "finished_at": now_iso() if status in TERMINAL else "",
        }
        final_patch["report"] = tau_report({**self.records.get(run_id), **final_patch})
        # Publish the terminal status and its report in one record update so
        # readers can never observe "finished" without the final report.
        self.records.update(run_id, final_patch)
        return status

    def _append_group(self, run_id: str, group_id: str, text: str) -> None:
        self.records.append_output(run_id, text, group_id=group_id)

    def _is_cancelled(self, run_id: str) -> bool:
        # Web persists a cancellation in the record; this process only reads it.
        try:
            return self.records.get(run_id).get("status") == "cancelling"
        except (KeyError, RuntimeError):
            return False

    @staticmethod
    def _verify_source(item: dict[str, Any]) -> None:
        if planned_source_changed(item):
            raise RuntimeError(f"运行计划对应的源码已变化：{item['implementation']['source']}")

    def _execute_group_with_slot(
        self, run_id: str, group_spec: dict[str, Any], selected: list[dict[str, Any]],
        run_dir: Path, actor: str,
    ) -> tuple[str, str]:
        group_id = str(group_spec["group_id"])
        self.records.update_group(run_id, group_id, {"status": "queued"})
        # Every campaign uses the same Resident for a module. Keep its entire
        # invocation and cleanup together, even when several campaigns run.
        def reserved():
            holders = self.records.invocation_holders()
            if (run_id, group_id) in holders:
                return False
            # A saved invocation outlives its executor's file locks. Resume it
            # before sending a fresh case to that Resident or taking capacity
            # needed by a queued resumption in another module.
            return any(owner_group == group_id or status == "queued"
                       for (_, owner_group), status in holders.items())

        while True:
            if (self._is_cancelled(run_id)
                    and (run_id, group_id) not in self.records.invocation_holders()):
                self.records.update_group(run_id, group_id, {"status": "cancelled"})
                return "cancelled", ""
            if reserved():
                time.sleep(1)
                continue
            with slots.exclusive(f"tau-module:{group_spec['module_id']}",
                                 stop=lambda: self._is_cancelled(run_id) or reserved()) as acquired:
                if not acquired or reserved():
                    continue
                try:
                    self._recover_module_cleanup(run_id, group_id)
                except Exception as exc:
                    error = f"前次 TAU 资源清理尚未完成：{exc}"
                    self.records.update_group(run_id, group_id, {"status": "error", "error": error})
                    return "error", error
                return self._execute_group_in_slot(run_id, group_spec, selected, run_dir, actor)

    def _recover_module_cleanup(self, run_id: str, group_id: str) -> None:
        """Finish failed fixture cleanup before reusing a module's Resident."""
        cleanup = getattr(self.adapter, "cleanup", None)
        if not callable(cleanup):
            return
        for previous_id in sorted(self.records.run_ids() - {run_id}):
            try:
                previous = self.records.get(previous_id)
            except KeyError:
                # Terminal reports can be deleted or expire while we scan.
                continue
            for group in previous.get("groups") or []:
                if group.get("group_id") != group_id:
                    continue
                if previous.get("status") not in TERMINAL and group.get("status") not in TERMINAL:
                    continue
                for result in group.get("results") or []:
                    details = result.get("details") or {}
                    receipt = details.get("framework_cleanup")
                    case_dir = self.run_root / previous_id / group_id.replace(":", "-").replace("/", "-") / result["case_id"]
                    receipt_path = case_dir / "framework-helper-cleanup.json"
                    # An exception prevents cleanup() from returning its receipt;
                    # its durable file still records the incomplete cleanup.
                    if receipt_path.exists():
                        receipt = json.loads(receipt_path.read_text())
                    if isinstance(receipt, dict) and receipt.get("complete"):
                        continue
                    if not isinstance(receipt, dict) and not details.get("cleanup_error"):
                        continue
                    cleanup(run_dir=case_dir, emit=lambda text: self._append_group(run_id, group_id, text))
                    self._append_group(run_id, group_id, f"已补完前次测试的资源清理：{previous_id}/{result['case_id']}\n")

    def _execute_group_in_slot(
        self, run_id: str, group_spec: dict[str, Any], selected: list[dict[str, Any]],
        run_dir: Path, actor: str,
    ) -> tuple[str, str]:
        group_id = str(group_spec["group_id"])
        self.records.update_group(run_id, group_id, {
            "waiting": "等待主机共享测试并发名额（上限 %s）" % slots.max_concurrency(),
        })
        try:
            lease = slots.acquire("tau", 1, stop=lambda: self._is_cancelled(run_id))
        finally:
            self.records.update_group(run_id, group_id, {"waiting": ""})
        if lease is None:
            # The normal path still withdraws a resumed invocation and runs cleanup.
            return self._execute_group(run_id, group_spec, selected, run_dir, actor)
        try:
            return self._execute_group(run_id, group_spec, selected, run_dir, actor)
        finally:
            lease.release()

    def _execute_group(
        self,
        run_id: str,
        group_spec: dict[str, Any],
        selected: list[dict[str, Any]],
        run_dir: Path,
        actor: str,
    ) -> tuple[str, str]:
        group_id = str(group_spec["group_id"])
        group_record = next(
            (item for item in self.records.get(run_id).get("groups") or [] if item.get("group_id") == group_id),
            {},
        )
        prior_results = list(group_record.get("results") or [])
        prior_statuses = [str(item.get("status") or "") for item in prior_results]
        status = aggregate_status(prior_statuses) if prior_statuses else "passed"
        error = ""
        self.records.update_group(run_id, group_id, {"status": "running"})
        try:
            for item_index in group_spec.get("item_indexes") or []:
                if any(int(result.get("item_index", -1)) == int(item_index) for result in prior_results):
                    continue
                item = selected[int(item_index)]
                if str(item["runner"]["id"]) != TAU_RUNNER_ID:
                    raise UnsupportedRunner(f"TAU Campaign 不执行 Runner {item['runner']['id']!r}")
                active_invocation = group_record.get("active_invocation")
                resume_confirmed = (
                    isinstance(active_invocation, dict)
                    and active_invocation.get("case_id") == item.get("case_id")
                    and all(active_invocation.get(key) for key in ("resident_id", "lineage_id"))
                    and any(active_invocation.get(key) for key in ("message_id", "request_id", "instruction_id", "trigger_path_id"))
                )
                # A case already with a Resident still goes to the adapter, which
                # withdraws that Resident input when the Campaign is cancelled.
                if self._is_cancelled(run_id) and not resume_confirmed:
                    status = "cancelled"
                    break
                if not resume_confirmed:
                    self._verify_source(item)
                case_started_at = (
                    group_record.get("case_started_at_epoch")
                    if group_record.get("current_case_id") == item["case_id"] else None
                )
                if not isinstance(case_started_at, (int, float)) or case_started_at <= 0:
                    case_started_at = datetime.now(timezone.utc).timestamp()
                self.records.update_group(run_id, group_id, {
                    "current_case_id": item["case_id"], "case_started_at_epoch": case_started_at,
                })
                self._append_group(run_id, group_id, f"\n[{item['case_title']}] {item['runner']['id']}\n")
                case_dir = run_dir / group_id.replace(":", "-").replace("/", "-") / str(item["case_id"])
                case_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                planned = dict(item)
                planned["_started_at_epoch"] = case_started_at
                if resume_confirmed:
                    planned["_resume_invocation"] = active_invocation
                cleanup_result = None
                cleanup_error = ''
                try:
                    outcome = self.adapter.execute(
                        run_id=run_id,
                        planned=planned,
                        run_dir=case_dir,
                        actor=actor,
                        emit=lambda chunk: self._append_group(run_id, group_id, chunk),
                        cancelled=lambda: self._is_cancelled(run_id),
                        on_submitted=lambda handle: self.records.update_group(
                            run_id,
                            group_id,
                            {"active_invocation": {**dict(handle), "case_id": item["case_id"]}},
                        ),
                    )
                finally:
                    cleanup = getattr(self.adapter, "cleanup", None)
                    ready = getattr(self.adapter, "cleanup_ready", None)
                    if callable(cleanup) and (not callable(ready) or ready(run_dir=case_dir)):
                        try:
                            cleanup_result = cleanup(
                                run_dir=case_dir,
                                emit=lambda chunk: self._append_group(run_id, group_id, chunk),
                            )
                        except Exception as exc:
                            cleanup_error = str(exc)
                            self._append_group(run_id, group_id, f"测试清理失败：{exc}\n")
                if outcome.status == "pending":
                    status = "running"
                    error = str(outcome.details.get("observation_error") or outcome.details.get("summary") or "")
                    self.records.update_group(run_id, group_id, {"error": error})
                    break
                if cleanup_error:
                    outcome = type(outcome)(status='error', returncode=outcome.returncode,
                                            details={**outcome.details, 'cleanup_error': cleanup_error})
                if isinstance(cleanup_result, dict):
                    outcome.details["framework_cleanup"] = cleanup_result
                    evidence = outcome.details.setdefault("evidence", [])
                    cleanup_evidence = str(cleanup_result.get("evidence") or "").strip()
                    if cleanup_evidence and cleanup_evidence not in evidence:
                        evidence.append(cleanup_evidence)
                prune = getattr(self.adapter, "prune_evidence", None)
                if callable(prune):
                    try:
                        pruned = prune(run_dir=case_dir, status=outcome.status, details=outcome.details)
                    except Exception as exc:
                        self._append_group(run_id, group_id, f"未引用证据清理失败：{exc}\n")
                    else:
                        if pruned and pruned.get("files"):
                            outcome.details["uncited_evidence_removed"] = pruned
                item_status = outcome.status
                if item_status not in TERMINAL:
                    raise RuntimeError(f"Runner 返回了无效的终态：{item_status}")
                details = dict(outcome.details)
                classification = failure_class(item_status, outcome.returncode, details)
                details.pop("failure_class", None)
                result = {
                    "case_id": item["case_id"],
                    "implementation_id": item["implementation"]["id"],
                    "runner": item["runner"]["id"],
                    "module_id": str(item.get("module") or ""),
                    "group_id": group_id,
                    "item_index": int(item_index),
                    "status": item_status,
                    **({"failure_class": classification} if classification else {}),
                    "returncode": outcome.returncode,
                    **({"details": details} if details else {}),
                }
                self.records.append_result(run_id, group_id, result)
                self.records.update_group(run_id, group_id, {"active_invocation": {}, "case_started_at_epoch": 0})
                if cleanup_error:
                    # continue_on_failure applies to test verdicts. An unfinished
                    # resource lifecycle cannot accept the module's next case.
                    status = "error"
                    error = f"测试资源清理尚未完成，停止本模块后续用例：{cleanup_error}"
                    break
                if item_status != "passed":
                    status = item_status
                    if not group_spec.get("continue_on_failure"):
                        break
        except UnsupportedRunner as exc:
            status, error = "unsupported", str(exc)
        except Exception as exc:
            status, error = "error", str(exc)
        finally:
            current_group = next((group for group in self.records.get(run_id).get("groups") or []
                                  if group.get("group_id") == group_id), {})
            unresolved = bool(current_group.get("active_invocation"))
            if unresolved:
                status = "running"
            else:
                status, error = self._finalize_modules(run_id, group_id, group_spec, selected, run_dir, status, error)
            self.records.update_group(
                run_id,
                group_id,
                {"status": status, "error": error,
                 "current_case_id": current_group.get("current_case_id", "") if unresolved else ""},
            )
        return status, error

    def _finalize_modules(
        self,
        run_id: str,
        group_id: str,
        group_spec: dict[str, Any],
        selected: list[dict[str, Any]],
        run_dir: Path,
        status: str,
        error: str,
    ) -> tuple[str, str]:
        """Remove the temporary teams of every module the group touched; a failed removal is an error."""
        finalizer = getattr(self.adapter, "finalize_group", None)
        if not callable(finalizer):
            return status, error
        modules = list(dict.fromkeys(
            str(selected[int(index)]["module"])
            for index in group_spec.get("item_indexes") or []
            if selected[int(index)]["runner"]["id"] == TAU_RUNNER_ID
        ))
        cleanup_results: dict[str, dict[str, Any]] = {}
        for module_id in modules:
            try:
                self.records.update_group(run_id, group_id, {
                    "cleanup": {"status": "running", "modules": cleanup_results},
                })
                cleanup = finalizer(
                    run_id=run_id, module_id=module_id,
                    run_dir=run_dir / "team-cleanup" / module_id,
                    emit=lambda chunk: self._append_group(run_id, group_id, chunk),
                    lineage_fixtures={
                        item["case_id"]: item["implementation"]["lineage_fixture"]
                        for item in selected
                        if item["module"] == module_id and item["runner"]["id"] == TAU_RUNNER_ID
                        and item["implementation"].get("lineage_fixture")
                    },
                )
                cleanup_results[module_id] = cleanup
                if cleanup.get("status") != "complete":
                    raise RuntimeError("；".join(cleanup.get("errors") or ["临时团队清理未完成"]))
            except Exception as exc:
                error = "；".join(value for value in (error, f"TAU 团队回收失败：{module_id}: {exc}") if value)
                status = "error"
                cleanup_results[module_id] = {
                    **cleanup_results.get(module_id, {}), "status": "error", "error": str(exc),
                }
                self._append_group(run_id, group_id, f"TAU 团队回收失败：{module_id}: {exc}\n")
        if cleanup_results:
            self.records.update_group(run_id, group_id, {"cleanup": {
                "status": "complete" if all(result.get("status") == "complete" for result in cleanup_results.values()) else "error",
                "modules": cleanup_results,
            }})
        return status, error
