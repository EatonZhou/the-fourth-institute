from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from management_common import process_runtime
from management_portal.society_service import SocietyServiceUnavailable
from management_portal.resources.errors import ResourceServiceUnavailable

from management_portal.test_framework.tau import CaseNotSent, TauConfig, ResidentInvoker, browser_runtime_config, build_prompt, parse_result
from management_portal.test_framework.catalog import ROOT
from management_portal.test_framework.dev_target import DevBuildMismatch, DevTargetError, DevTargetVerifier, VerifiedDevTarget
from management_portal.test_framework.runner_ids import TAU_RUNNER_ID
from management_portal.test_framework.tau_residents import TAU_ACTOR, TauResidentAssignments


class UnsupportedRunner(RuntimeError):
    pass


class SubmissionPending(RuntimeError):
    """Observation has no terminal business fact; keep the original work alive."""


@dataclass(frozen=True)
class AdapterResult:
    status: str
    returncode: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


class RunnerAdapter:
    id = ""

    def execute(
        self, *, run_id: str, planned: dict[str, Any], run_dir: Path,
        actor: str, emit: Callable[[str], None], cancelled: Callable[[], bool],
        on_submitted: Callable[[dict[str, Any]], None] | None = None,
    ) -> AdapterResult:
        raise NotImplementedError

    def cleanup(self, *, run_dir: Path, emit: Callable[[str], None]) -> None:
        return None


@dataclass(frozen=True)
class HelperProcess:
    parent: int
    command: tuple[str, ...]
    created_at: float


def _process_snapshot() -> dict[int, HelperProcess]:
    # psutil provides native metadata on all three platforms, not process control.
    try:
        import psutil
    except ImportError as error:
        raise RuntimeError("TAU 辅助进程归属检查需要 psutil；请安装平台运行依赖") from error
    snapshot: dict[int, HelperProcess] = {}
    for process in psutil.process_iter():
        try:
            with process.oneshot():
                snapshot[process.pid] = HelperProcess(process.ppid(), tuple(process.cmdline()), process.create_time())
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied as error:
            if sys.platform == "win32":
                # Windows cleanup is owned by the recorded Job objects, not
                # this command-line scan. Even querying the owner of protected
                # system processes is denied to ordinary users. They provide
                # no evidence of a helper and must never be signalled. Keep
                # scanning for readable, unmanaged helpers (rejected below).
                continue
            # Foreign protected processes cannot be our helper. If even that
            # cannot be established, leave a failed cleanup receipt.
            try:
                if process.username() != psutil.Process().username():
                    continue
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                pass
            raise RuntimeError(f"TAU 无法核实辅助进程归属（pid={process.pid}）") from error
    return snapshot


def _artifact_helper_targets(run_dir: Path, snapshot: dict[int, HelperProcess] | None = None) -> set[int]:
    """Resolve helper trees whose command line proves ownership by one TAU case."""
    marker = str(run_dir.resolve())
    snapshot = _process_snapshot() if snapshot is None else snapshot
    protected = {os.getpid()}
    ancestor = os.getppid()
    while ancestor > 1 and ancestor in snapshot and ancestor not in protected:
        protected.add(ancestor)
        ancestor = snapshot[ancestor].parent
    roots = {
        pid for pid, process in snapshot.items()
        if pid not in protected and any(
            value == marker or value.startswith(marker + os.sep)
            for argument in process.command
            for value in [argument.partition("=")[2] if argument.startswith("--") and "=" in argument else argument]
        )
    }
    if not roots:
        return set()
    targets = set(roots)
    changed = True
    while changed:
        changed = False
        for pid, process in snapshot.items():
            if process.parent in targets and pid not in targets and pid not in protected:
                targets.add(pid)
                changed = True
    return targets


def _terminate_artifact_helpers(run_dir: Path) -> list[int]:
    """Stop helper trees whose command line proves ownership by one TAU case."""
    from management_portal.test_framework.tau_browser_owner import cleanup_browsers

    managed = cleanup_browsers(run_dir)
    snapshot = _process_snapshot()
    targets = _artifact_helper_targets(run_dir, snapshot)
    if not targets:
        return managed
    if sys.platform == "win32":
        raise RuntimeError("TAU 外部浏览器没有受管 Windows Job 身份；保留资源，需启动方接入 process_runtime")
    import psutil  # _process_snapshot already diagnosed a missing dependency.
    roots = {pid for pid in targets if snapshot[pid].parent not in targets}
    for pid in sorted(roots):
        try:
            if psutil.Process(pid).create_time() != snapshot[pid].created_at:
                raise RuntimeError(f"TAU 辅助进程身份已改变（pid={pid}）")
            # Only the runtime may signal a verified POSIX session/tree. It
            # rejects non-leaders rather than killing an unrelated shared group.
            process_runtime.terminate_process_tree(pid, timeout=2)
        except psutil.NoSuchProcess:
            continue
    return sorted(set(managed) | targets)


class TauAdapter(RunnerAdapter):
    id = TAU_RUNNER_ID

    def __init__(
        self,
        config: TauConfig,
        invoke: ResidentInvoker,
        verify_target: Callable[[Path], VerifiedDevTarget] | None = None,
        assignments: TauResidentAssignments | None = None,
        resume: ResidentInvoker | None = None,
        cancel_invocation: Callable[[str, str, str], dict[str, Any]] | None = None,
        resolve_submission: ResidentInvoker | None = None,
        read_input_state: ResidentInvoker | None = None,
    ):
        self.config = config
        self.invoke = invoke
        self.resume = resume
        if cancel_invocation is None:
            from management_portal.resident_handoff import (
                cancel_resident_invocation,
            )

            cancel_invocation = cancel_resident_invocation
        self.cancel_invocation = cancel_invocation
        self.resolve_submission = resolve_submission
        self.read_input_state = read_input_state
        self.assignments = assignments or TauResidentAssignments(
            config.storage_state.parent / "tau-residents.json"
        )
        self.verify_target = verify_target or DevTargetVerifier(
            registry_path=config.deployment_registry
        ).verify

    @staticmethod
    def cleanup_ready(*, run_dir: Path) -> bool:
        path = run_dir / "tau-invocation.json"
        if not path.exists():
            return True
        # A corrupt checkpoint cannot authorize destructive cleanup.
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            return (isinstance(checkpoint, dict)
                    and checkpoint.get("schema") == "tfi.tau-invocation-checkpoint.v1"
                    and checkpoint.get("safe_to_cleanup") is True)
        except (ValueError, OSError):
            return False

    def cleanup(self, *, run_dir: Path, emit: Callable[[str], None]) -> dict[str, Any]:
        if not self.cleanup_ready(run_dir=run_dir):
            raise RuntimeError("TAU 原提交或输入尚未确认终止，保留浏览器与临时资源")
        terminated: list[int] = []
        remaining: list[int] = []
        cleanup_error = ''
        try:
            terminated = _terminate_artifact_helpers(run_dir)
            remaining = sorted(_artifact_helper_targets(run_dir))
        except Exception as error:
            cleanup_error = str(error)
        fixtures = {}
        if not remaining and not cleanup_error:
            from management_portal.test_framework.tau_fixtures import cleanup as cleanup_fixtures
            try:
                fixtures = cleanup_fixtures(run_dir)
            except Exception as exc:
                cleanup_error = str(exc)
        receipt = {
            "schema": "tfi.portal-tau-helper-cleanup.v1",
            "checked_at_epoch": time.time(),
            "terminated_pids": terminated,
            "remaining_pids": remaining,
            "complete": not remaining and not cleanup_error,
            "fixtures": fixtures,
            "error": cleanup_error,
            "evidence": "framework-helper-cleanup.json",
        }
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt_path = run_dir / receipt["evidence"]
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        receipt_path.chmod(0o600)
        if terminated:
            emit(f"已回收 {len(terminated)} 个 TAU 辅助进程。\n")
        if remaining:
            raise RuntimeError(f"仍有 {len(remaining)} 个本用例辅助进程未退出")
        if cleanup_error:
            raise RuntimeError(cleanup_error)
        return receipt

    def prune_evidence(self, *, run_dir: Path, status: str, details: dict[str, Any]) -> dict[str, int] | None:
        """Delete the files the report does not cite; the verdict rests on the cited ones.

        Without a parsed report (error, cancelled) everything stays for diagnosis.
        """
        # Standard passive observations belong to this result even when the
        # executor omitted them. Retain them before pruning and result storage.
        if run_dir.is_dir():
            root = run_dir.resolve()
            for name in ("tau-browser-observations.jsonl", "tau-helper-calls.jsonl", "tau-browser-identity.jsonl"):
                candidate = run_dir / name
                if candidate.is_file() and candidate.resolve().parent == root:
                    evidence = details.setdefault("evidence", [])
                    if name not in evidence:
                        evidence.append(name)
        if status not in {"passed", "failed"} or not run_dir.is_dir():
            return None
        root = run_dir.resolve()
        cited = {os.path.normpath(root / str(item)) for item in details.get("evidence") or []}
        files = freed = 0
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            for name in filenames + [name for name in dirnames if os.path.islink(os.path.join(dirpath, name))]:
                path = os.path.join(dirpath, name)
                if path in cited:
                    continue
                try:
                    size = os.lstat(path).st_size
                    os.unlink(path)
                except OSError:
                    continue
                files += 1
                freed += size
            for name in dirnames:
                try:
                    os.rmdir(os.path.join(dirpath, name))
                except OSError:
                    pass
        return {"files": files, "bytes": freed}

    def finalize_group(self, *, run_id: str, module_id: str, run_dir: Path,
                       emit: Callable[[str], None],
                       lineage_fixtures: dict[str, str] | None = None) -> dict[str, Any]:
        from management_portal.test_framework.tau_cleanup import TauTeamCleanup

        registered = self.assignments.registered()
        return TauTeamCleanup().run(
            run_id=run_id, module_id=module_id, run_dir=run_dir, emit=emit,
            protected_lineages={str(row.get("lineage_id") or "") for row in registered},
            protected_residents={str(row.get("resident_id") or "") for row in registered},
            resident_lineages={row["lineage_id"] for row in registered
                               if row.get("module_id") == module_id and row.get("lineage_id")},
            lineage_fixtures=lineage_fixtures,
        )

    def execute(
        self, *, run_id: str, planned: dict[str, Any], run_dir: Path,
        actor: str, emit: Callable[[str], None], cancelled: Callable[[], bool],
        on_submitted: Callable[[dict[str, Any]], None] | None = None,
    ) -> AdapterResult:
        reason = self.config.unavailable_reason()
        if reason:
            raise UnsupportedRunner(reason)
        return _TauCaseRun(
            self, run_id=run_id, planned=planned, run_dir=run_dir, actor=actor,
            emit=emit, cancelled=cancelled, on_submitted=on_submitted,
        ).execute()


class _TauCaseRun:
    """One TAU case sent to its Resident, or the resumption of its submission.

    The checkpoint (``tau-invocation.json``) records the submission as it
    becomes known (``prepared`` before the Resident accepted the input,
    ``submitted`` after), so a later run resumes the same input instead of
    sending the case again; it allows cleanup only once the input is over.
    """

    def __init__(
        self, adapter: TauAdapter, *, run_id: str, planned: dict[str, Any], run_dir: Path, actor: str,
        emit: Callable[[str], None], cancelled: Callable[[], bool],
        on_submitted: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self.adapter = adapter
        self.run_id = run_id
        self.planned = planned
        self.run_dir = run_dir
        self.actor = actor
        self.emit = emit
        self.cancelled = cancelled
        self.on_submitted = on_submitted
        self.resume_handle = planned.get("_resume_invocation")
        self.prepared: dict[str, str] = {}
        self.submitted: dict[str, str] = {}

    @property
    def module(self) -> str:
        return str(self.planned["module"])

    def execute(self) -> AdapterResult:
        if self.resume_handle:
            self.save_checkpoint(self.resume_handle)
        failed = self._check_target()
        if failed is not None:
            return failed
        refused = self._prepare_assignment()
        if refused is not None:
            return refused
        self._open_lifecycle()
        try:
            self._restore_resumed_submission()
            result = self._observe()
        except Exception as exc:
            return self._abort(exc)
        return self._verdict(result)

    def verify_ready_target(self) -> VerifiedDevTarget:
        waiting = False
        build_wait_started = None
        while True:
            try:
                return self.adapter.verify_target(self.adapter.config.storage_state)
            except DevTargetError as exc:
                build_mismatch = isinstance(exc, DevBuildMismatch)
                if self.cancelled() or (not build_mismatch and not exc.temporarily_unavailable):
                    raise
                now = time.monotonic()
                if build_mismatch and build_wait_started is None:
                    build_wait_started = now
                    self.emit(
                        "TAU 检测到 DEV 构建差异，最多等待 60 秒重新验证："
                        + json.dumps(exc.details, ensure_ascii=False) + "\n"
                    )
                if build_wait_started is not None and now - build_wait_started >= 60:
                    raise
                if not waiting:
                    self.emit("TAU 正在等待 DEV 预检连接恢复；不会重复投递已提交消息。\n")
                    waiting = True
                time.sleep(1.0)

    def save_checkpoint(self, handle: dict[str, Any], *, safe: bool = False) -> None:
        from management_portal.shared_state import atomic_replace_json

        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_replace_json(self.run_dir / "tau-invocation.json", {
            "schema": "tfi.tau-invocation-checkpoint.v1",
            "handle": dict(handle), "safe_to_cleanup": safe,
        }, mode=0o600)

    def _check_target(self) -> AdapterResult | None:
        """Verify the DEV target before the case; on resume, against the first check's record."""
        original_target = None
        if self.resume_handle:
            saved_target = self.resume_handle.get("target_preflight")
            try:
                original_target = VerifiedDevTarget(**saved_target) if isinstance(saved_target, dict) else None
            except TypeError:
                pass
        original_evidence = original_target.result_details() if original_target else {
            "status": "not_recorded", "reason": "原输入未保存首次 DEV 检查记录",
        }
        try:
            self.target = self.verify_ready_target()
        except DevTargetError as exc:
            self.emit(f"TAU 预检失败：{exc}\n")
            return AdapterResult(
                status="pending" if self.resume_handle else ("cancelled" if self.cancelled() else "failed"),
                details={
                    "summary": str(exc),
                    "issues": [str(exc)],
                    "evidence": [],
                    **({"target_preflight": original_evidence, "target_resume_check": exc.details}
                       if self.resume_handle else {"target_preflight": exc.details}),
                },
            )
        self.original_target = original_target if self.resume_handle else self.target
        self.target_evidence = {
            "target_preflight": self.original_target.result_details() if self.original_target else original_evidence,
        }
        if self.resume_handle:
            self.target_evidence["target_resume_check"] = self.target.result_details()
        return None

    def _prepare_assignment(self) -> AdapterResult | None:
        """Read the module's Resident assignment; a fresh case first gets its configuration ready."""
        assignments = self.adapter.assignments
        self.runtime_options = {
            key: value
            for key, value in {"model": self.adapter.config.model, "permission_preset": "full-access"}.items()
            if value
        }
        assignment = assignments.assignment(self.module)
        self.required_account = self.planned.get("resident_account_id")
        if not self.planned.get("_resume_invocation") and self.required_account and assignment.get("backend_route_id") != self.required_account:
            return AdapterResult(status="unsupported", details={
                "summary": "TAU 模块账号与本次 Campaign 指定账号不一致，未发送用例",
                "required_account": self.required_account,
                "configured_account": assignment.get("backend_route_id", ""),
            })
        self.runtime_options.update(assignment.get("runtime_options") or {})
        if not self.planned.get("_resume_invocation") and assignment.get("configuration_id") and assignment.get("resident_id"):
            from management_portal.test_framework.tau_runtime import prepare_configuration

            configuration = prepare_configuration(
                assignment, cancelled=self.cancelled, emit=self.emit,
                refresh_assignment=lambda: assignments.assignment(self.module),
                replace_unusable_assignment=lambda unusable: assignments.replace_unusable_resident(
                    self.module,
                    expected_resident_id=str(unusable.get("resident_id") or ""),
                    changed_by=self.actor,
                ),
            )
            if not configuration["ready"]:
                self.emit("TAU Resident 的账号或 fast 配置尚未就绪，本用例未发送。\n")
                return AdapterResult(status="cancelled" if self.cancelled() else "unsupported", details={
                    "summary": "TAU Resident 配置尚未就绪，未发送用例", "configuration": configuration,
                })
            assignment = assignments.assignment(self.module)
        self.resident_scope, self.lineage_scope = assignments.scopes(assignment)
        return None

    def _open_lifecycle(self) -> None:
        from management_common.resource_lifecycle import LifecycleStore

        self.lifecycle_id = LifecycleStore().open(str(self.run_dir.resolve()), actor=TAU_ACTOR,
                                                  run_id=self.run_id, case_id=str(self.planned['case_id']))
        if self.resume_handle and self.resume_handle.get('resident_id'):
            LifecycleStore().bind(self.lifecycle_id, self.resume_handle['resident_id'])

    def _restore_resumed_submission(self) -> None:
        resume_handle = self.resume_handle
        if not (isinstance(resume_handle, dict) and resume_handle):
            return
        restored = {key: value for key, value in resume_handle.items() if isinstance(value, str)}
        if restored.get("message_id") and restored.get("submission_state", "submitted") == "submitted":
            self.submitted.update(restored)
        else:
            self.prepared.update(restored)
        self.emit(
            "接续 Resident："
            f"{restored.get('resident_id', '')} / {restored.get('message_id') or restored.get('request_id', '')}\n"
        )

    def handle_assigned(self, handle: dict[str, str], *, before_submit: bool = False) -> None:
        from management_common.resource_lifecycle import LifecycleStore

        if before_submit:
            # Resident handoff runs this callback before TriggerEvent or
            # DeliverMessage. A local ownership rejection cannot have
            # accepted an input and must not become an unknown receipt.
            try:
                LifecycleStore().bind(self.lifecycle_id, handle['resident_id'])
            except Exception as exc:
                rejected = {**handle, 'submission_state': 'not_submitted',
                            'submission_reason': str(exc)}
                self.save_checkpoint(rejected, safe=True)
                raise CaseNotSent(str(exc)) from exc
        self.prepared.update(handle)
        self.save_checkpoint(self.prepared)
        if self.on_submitted is not None:
            self.on_submitted({**self.prepared, "target_preflight": asdict(self.target)})
        if not before_submit:
            LifecycleStore().bind(self.lifecycle_id, handle['resident_id'])

    def handle_submitted(self, handle: dict[str, str]) -> None:
        self.submitted.update(handle)
        self.save_checkpoint(self.submitted)
        if self.on_submitted is not None:
            self.on_submitted({**handle, "target_preflight": asdict(self.target)})
        self.adapter.assignments.record_identity(
            self.module,
            resident_id=str(handle.get("resident_id") or ""),
            lineage_id=str(handle.get("lineage_id") or ""),
        )
        self.emit(f"已提交 Resident：{handle.get('resident_id', '')} / {handle.get('message_id', '')}\n")

    def write_run_context(self) -> None:
        # The case's first page action needs the evidence directory and the
        # build it starts from; the framework records both before the case
        # is sent, so that the Resident only reads them back (several TAU
        # cases failed only because a helper wrote the build after the
        # first navigation).
        run_dir = self.run_dir
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ('fixtures', 'helpers'):
            (run_dir / name).mkdir(exist_ok=True, mode=0o700)
        context = {
            "run_id": self.run_id,
            "case_id": str(self.planned["case_id"]),
            "resource_lifecycle_id": self.lifecycle_id,
            "evidence_dir": str(run_dir.resolve()),
            "build_id": self.target.actual_build_id,
            "browser_runtime": browser_runtime_config(),
            "recorded_by": "framework",
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        path = run_dir / "run-context.json"
        if path.exists():
            # Resume keeps the original build/evidence identity, while
            # upgrading an older context with the durable cleanup scope.
            context = {**context, **json.loads(path.read_text(encoding="utf-8")),
                       "resource_lifecycle_id": self.lifecycle_id}
        standalone_reason = self.planned["implementation"].get("standalone_git_fixture", "")
        if not isinstance(standalone_reason, str):
            raise ValueError("Invalid TAU standalone_git_fixture")
        if standalone_reason:
            from management_portal.test_framework.tau_fixture_paths import prepare
            context["standalone_fixture_dir"] = str(prepare(run_dir))
        temporary = run_dir / ".run-context.json.tmp"
        temporary.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def _observe(self) -> dict[str, Any]:
        """The Resident's result for the case: sent once, or the original submission resumed.

        A submission whose receipt is unknown is resolved through its owner,
        never sent again; dependent services that are briefly unavailable are
        waited out once the input is durably accepted.
        """
        reconnect_reported = False
        while True:
            try:
                result = self._execute_or_resume()
                if reconnect_reported:
                    self.emit(f"已取得原消息 {self.submitted['message_id']} 的结果。\n")
                return result
            except SubmissionPending:
                raise
            except Exception as exc:
                from management_portal.resident_handoff import ResidentInputTerminated

                if isinstance(exc, ResidentInputTerminated):
                    self.save_checkpoint(self.submitted, safe=True)
                    raise
                # A submit exception is not a business rejection. Resolve the
                # original request through its owner before touching the input.
                if self.prepared and not self.submitted:
                    self._record_unconfirmed_submission(exc)
                    if not reconnect_reported:
                        self.emit("TAU 正在只读核对原提交；不会重新投递或清理仍运行的环境。\n")
                        reconnect_reported = True
                    # The next iteration observes only; it never calls invoke.
                    try:
                        return self._execute_or_resume()
                    except (SocietyServiceUnavailable, ResourceServiceUnavailable):
                        time.sleep(1.0)
                        continue
                if not self._may_await_reconnect(exc):
                    raise
                if not reconnect_reported:
                    self.emit(f"TAU 依赖服务暂时不可用，等待接续原消息 {self.submitted['message_id']}：{exc}\n")
                    reconnect_reported = True
                time.sleep(1.0)

    def _record_unconfirmed_submission(self, exc: Exception) -> None:
        from management_portal.lineage_workflows import WorkflowPending, WorkflowFailed, submission_error

        if isinstance(exc, (WorkflowPending, WorkflowFailed)):
            self.handle_assigned(exc.handle)
            if isinstance(exc, WorkflowFailed):
                self.save_checkpoint(self.prepared, safe=True)
                self.prepared.clear()
                raise CaseNotSent(str(exc)) from exc
        else:
            self.handle_assigned(submission_error(self.prepared, exc))

    def _may_await_reconnect(self, exc: Exception) -> bool:
        if not isinstance(exc, (SocietyServiceUnavailable, ResourceServiceUnavailable)):
            return False
        # Only a durably accepted input can be resumed safely.
        # Replaying an unconfirmed submission could create work twice.
        return not (
            not self.submitted.get("resident_id")
            or not self.submitted.get("message_id")
            or self.adapter.resume is None
            or self.cancelled()
        )

    def _execute_or_resume(self) -> dict[str, Any]:
        if self.prepared and not self.submitted:
            self._resolve_prepared_submission()
        if self.submitted:
            return self._resume()
        return self._invoke()

    def _resolve_prepared_submission(self) -> None:
        resolver = self.adapter.resolve_submission
        if resolver is None:
            from management_portal.resident_handoff import resolve_invocation_submission

            resolver = resolve_invocation_submission
        resolved = resolver(actor=TAU_ACTOR, handle=dict(self.prepared))
        if resolved.get("submission_state") == "submitted" and resolved.get("message_id"):
            self.handle_submitted(resolved)
        elif resolved.get("submission_state") == "not_submitted":
            self.save_checkpoint(resolved, safe=True)
            self.prepared.clear()
            raise CaseNotSent("原提交已确认未受理；本用例不自动重新发送")
        else:
            self.handle_assigned(resolved)
            raise SubmissionPending(str(resolved.get("submission_reason") or resolved.get("reason") or "原提交结果尚待确认"))

    def _resume(self) -> dict[str, Any]:
        if self.adapter.resume is None:
            raise RuntimeError("TAU adapter has no Resident resume capability")
        self.write_run_context()
        return self.adapter.resume(
            actor=TAU_ACTOR,
            resident_id=self.submitted.get("resident_id", ""),
            lineage_id=self.submitted.get("lineage_id", ""),
            message_id=self.submitted.get("message_id", ""),
            cancellation_requested=self.cancelled,
        )

    def _invoke(self) -> dict[str, Any]:
        adapter, planned = self.adapter, self.planned
        current_assignment = adapter.assignments.assignment(self.module)
        if self.required_account and current_assignment.get("backend_route_id") != self.required_account:
            raise CaseNotSent("TAU 模块账号已变更，与本次 Campaign 指定账号不一致，未发送用例")
        current_options = {**self.runtime_options, **(current_assignment.get("runtime_options") or {})}
        if current_assignment.get("resident_id") and current_assignment.get("configuration_id"):
            current_options.pop("service_tier", None)
        self.write_run_context()
        return adapter.invoke(
            actor=TAU_ACTOR,
            prompt=build_prompt(planned, adapter.config, self.run_dir, self.target, self.run_id, assignment=current_assignment),
            provider=adapter.config.provider,
            backend_route_id=str(current_assignment.get("backend_route_id") or ""),
            cwd=str(ROOT),
            resident_scope=self.resident_scope,
            lineage_scope=self.lineage_scope,
            title=f"Portal TAU · {planned['module']}",
            runtime_options=current_options,
            business_ref=f"{self.run_id}:{planned['case_id']}",
            metadata={"test_run_id": self.run_id, "test_case_id": planned["case_id"], "test_module_id": planned["module"], "runner": adapter.id},
            **({"resident_runtime": planned["resident_runtime"]} if planned.get("resident_runtime") else {}),
            cancellation_requested=self.cancelled,
            on_assigned=lambda handle: self.handle_assigned(handle, before_submit=True),
            on_submitted=self.handle_submitted,
        )

    def _abort(self, exc: Exception) -> AdapterResult:
        """The case's result when sending or observing it failed; its input is stopped first."""
        from management_portal.resident_handoff import ResidentInputTerminated

        if isinstance(exc, ResidentInputTerminated) and self.submitted:
            self.save_checkpoint(self.submitted, safe=True)
        cleanup_handle = self.submitted or self.prepared
        cancellation = self._cancel_submitted_input(cleanup_handle)
        cancellation_details = {"resident_cancellation": cancellation} if cancellation else {}
        if not self.adapter.cleanup_ready(run_dir=self.run_dir):
            return AdapterResult(status="pending", details={
                "summary": "原 TAU 提交或输入尚未确认终止，继续保留环境与恢复身份。",
                "observation_error": str(exc) or type(exc).__name__,
                "conversation": dict(cleanup_handle), **self.target_evidence,
                **cancellation_details,
            })
        if self.cancelled():
            return AdapterResult(
                status="cancelled",
                details={
                    "summary": "TAU 已取消。",
                    "issues": [],
                    "evidence": [],
                    **cancellation_details,
                },
            )
        message = str(exc) or type(exc).__name__
        if isinstance(exc, CaseNotSent) and not self.submitted.get("message_id"):
            self.emit(f"TAU 用例未发送：{message}\n")
            return AdapterResult(status="unsupported", details={
                "summary": message, "issues": [], "evidence": [], **self.target_evidence,
            })
        self.emit(f"TAU Resident 执行失败：{message}\n")
        return AdapterResult(
            status="error",
            details={
                "summary": "Resident 未能完成本次 TAU 用例。",
                "issues": [message],
                "evidence": [],
                **self.target_evidence,
                "conversation": {
                    "resident_id": cleanup_handle.get("resident_id", ""),
                    "lineage_id": cleanup_handle.get("lineage_id", ""),
                    "message_id": cleanup_handle.get("message_id", ""),
                },
                **cancellation_details,
            },
        )

    def _cancel_submitted_input(self, cleanup_handle: dict[str, str]) -> dict[str, Any]:
        """Cancel the accepted input and wait until it ends; ``{}`` when there is none to cancel."""
        submitted = self.submitted
        if not (submitted.get("resident_id") and submitted.get("message_id")
                and not self.adapter.cleanup_ready(run_dir=self.run_dir)):
            return {}
        try:
            state = self.adapter.cancel_invocation(
                submitted["resident_id"],
                TAU_ACTOR,
                submitted["message_id"],
            )
            while state.get("state") not in {"completed", "failed", "cancelled"}:
                time.sleep(1.0)
                state = self._read_input_state()
            self.save_checkpoint(submitted, safe=True)
            cancellation = {
                "attempted": True,
                "state": str(state.get("state") or ""),
            }
            self.emit(
                "TAU 异常退出后已回收 Resident 输入："
                f"{cleanup_handle['message_id']} / {cancellation['state'] or 'requested'}\n"
            )
        except Exception as cancel_exc:
            cancellation = {
                "attempted": True,
                "error": str(cancel_exc) or type(cancel_exc).__name__,
            }
            self.emit(
                "TAU 异常退出后的 Resident 输入回收失败："
                f"{cancellation['error']}\n"
            )
        return cancellation

    def _read_input_state(self) -> dict[str, Any]:
        submitted = self.submitted
        reader = self.adapter.read_input_state
        if reader is None:
            from management_portal import isa_client

            return isa_client.call(TAU_ACTOR, "GetInputState", {
                "resident_id": submitted["resident_id"], "message_id": submitted["message_id"],
            })["input_state"]
        return reader(actor=TAU_ACTOR, resident_id=submitted["resident_id"],
                      message_id=submitted["message_id"])

    def _verdict(self, result: dict[str, Any]) -> AdapterResult:
        """The case's result from the Resident's report, checked against its evidence and the DEV target."""
        self.save_checkpoint(self.submitted or result, safe=True)
        message = str(result.get("message") or "")
        self.adapter.assignments.record_identity(
            self.module,
            resident_id=str(result.get("resident_id") or self.submitted.get("resident_id") or ""),
            lineage_id=str(result.get("lineage_id") or self.submitted.get("lineage_id") or ""),
        )
        if message:
            self.emit(message + "\n")
        requires_visual = "ui" in (self.planned.get("aspects") or [])
        try:
            parsed = parse_result(message, require_visual=requires_visual)
        except ValueError as exc:
            parse_error = str(exc)
            self.emit(f"TAU 结果解析失败：{parse_error}\n")
            return AdapterResult(
                status="error",
                details={
                    "summary": "Resident 已结束，但未交付可解析的 TAU 结论。",
                    "failure_class": "test",
                    "issues": [parse_error],
                    "evidence": [],
                    **self.target_evidence,
                    "conversation": self._conversation(result),
                },
            )
        self._check_evidence(parsed, requires_visual)
        target_postflight = self._postflight(parsed)
        status = str(parsed.pop("status"))
        return AdapterResult(
            status=status,
            details={
                **parsed,
                **self.target_evidence,
                "target_postflight": target_postflight,
                "conversation": self._conversation(result),
            },
        )

    def _conversation(self, result: dict[str, Any]) -> dict[str, str]:
        return {key: str(result.get(key) or self.submitted.get(key) or "")
                for key in ("resident_id", "lineage_id", "message_id")}

    def _check_evidence(self, parsed: dict[str, Any], requires_visual: bool) -> None:
        invalid_evidence = []
        screenshot_evidence = []
        evidence_root = self.run_dir.resolve()
        for item in parsed["evidence"]:
            candidate = (self.run_dir / item).resolve(strict=False)
            if evidence_root not in candidate.parents or not candidate.is_file():
                invalid_evidence.append(item)
            elif candidate.suffix.casefold() in {".png", ".jpg", ".jpeg"}:
                screenshot_evidence.append(item)
        if invalid_evidence:
            parsed["status"] = "failed"
            parsed["issues"].append("证据文件不存在或越出证据目录：" + ", ".join(invalid_evidence))
        if requires_visual and not screenshot_evidence:
            parsed["status"] = "failed"
            parsed["issues"].append("UI 用例缺少有效的 PNG/JPEG 截图证据。")

    def _postflight(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """Check the DEV target again after the case; a different target fails it."""
        try:
            completed_target = self.verify_ready_target()
            target_postflight = completed_target.result_details()
            baseline = self.original_target or self.target
            target_postflight["build_changed"] = (
                completed_target.actual_build_id != self.original_target.actual_build_id
                if self.original_target else None
            )
            if (
                completed_target.instance_id != baseline.instance_id
                or completed_target.origin != baseline.origin
                or completed_target.actor != baseline.actor
            ):
                parsed["status"] = "failed"
                parsed["issues"].append(
                    "TAU 执行期间 DEV 目标发生变化："
                    f"{baseline.instance_id}@{baseline.origin} ({baseline.actor}) → "
                    f"{completed_target.instance_id}@{completed_target.origin} ({completed_target.actor})。"
                )
            elif target_postflight["build_changed"]:
                self.emit(
                    "记录正常 DEV 构建变化："
                    f"{baseline.actual_build_id} → {completed_target.actual_build_id}；保留本次行为结论。\n"
                )
        except DevTargetError as exc:
            target_postflight = dict(exc.details)
            parsed["status"] = "failed"
            parsed["issues"].append(f"TAU 运行后 DEV 复检失败：{exc}")
        return target_postflight
