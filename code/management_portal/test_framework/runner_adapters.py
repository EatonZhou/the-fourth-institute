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
        def should_cancel() -> bool:
            return cancelled()

        def verify_ready_target() -> VerifiedDevTarget:
            waiting = False
            build_wait_started = None
            while True:
                try:
                    return self.verify_target(self.config.storage_state)
                except DevTargetError as exc:
                    build_mismatch = isinstance(exc, DevBuildMismatch)
                    if should_cancel() or (not build_mismatch and not exc.temporarily_unavailable):
                        raise
                    now = time.monotonic()
                    if build_mismatch and build_wait_started is None:
                        build_wait_started = now
                        emit(
                            "TAU 检测到 DEV 构建差异，最多等待 60 秒重新验证："
                            + json.dumps(exc.details, ensure_ascii=False) + "\n"
                        )
                    if build_wait_started is not None and now - build_wait_started >= 60:
                        raise
                    if not waiting:
                        emit("TAU 正在等待 DEV 预检连接恢复；不会重复投递已提交消息。\n")
                        waiting = True
                    time.sleep(1.0)

        resume_handle = planned.get("_resume_invocation")
        def save_checkpoint(handle: dict[str, Any], *, safe: bool = False) -> None:
            from management_portal.shared_state import atomic_replace_json

            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_replace_json(run_dir / "tau-invocation.json", {
                "schema": "tfi.tau-invocation-checkpoint.v1",
                "handle": dict(handle), "safe_to_cleanup": safe,
            }, mode=0o600)

        if resume_handle:
            save_checkpoint(resume_handle)
        original_target = None
        if resume_handle:
            saved_target = resume_handle.get("target_preflight")
            try:
                original_target = VerifiedDevTarget(**saved_target) if isinstance(saved_target, dict) else None
            except TypeError:
                pass
        original_evidence = original_target.result_details() if original_target else {
            "status": "not_recorded", "reason": "原输入未保存首次 DEV 检查记录",
        }
        try:
            target = verify_ready_target()
        except DevTargetError as exc:
            emit(f"TAU 预检失败：{exc}\n")
            return AdapterResult(
                status="pending" if resume_handle else ("cancelled" if cancelled() else "failed"),
                details={
                    "summary": str(exc),
                    "issues": [str(exc)],
                    "evidence": [],
                    **({"target_preflight": original_evidence, "target_resume_check": exc.details}
                       if resume_handle else {"target_preflight": exc.details}),
                },
            )
        if not resume_handle:
            original_target = target
        target_evidence = {
            "target_preflight": original_target.result_details() if original_target else original_evidence,
        }
        if resume_handle:
            target_evidence["target_resume_check"] = target.result_details()
        prepared: dict[str, str] = {}
        submitted: dict[str, str] = {}

        persist_submission = on_submitted

        def handle_assigned(handle: dict[str, str], *, before_submit: bool = False) -> None:
            from management_common.resource_lifecycle import LifecycleStore

            if before_submit:
                # Resident handoff runs this callback before TriggerEvent or
                # DeliverMessage. A local ownership rejection cannot have
                # accepted an input and must not become an unknown receipt.
                try:
                    LifecycleStore().bind(lifecycle_id, handle['resident_id'])
                except Exception as exc:
                    rejected = {**handle, 'submission_state': 'not_submitted',
                                'submission_reason': str(exc)}
                    save_checkpoint(rejected, safe=True)
                    raise CaseNotSent(str(exc)) from exc
            prepared.update(handle)
            save_checkpoint(prepared)
            if persist_submission is not None:
                persist_submission({**prepared, "target_preflight": asdict(target)})
            if not before_submit:
                LifecycleStore().bind(lifecycle_id, handle['resident_id'])

        def handle_submitted(handle: dict[str, str]) -> None:
            submitted.update(handle)
            save_checkpoint(submitted)
            if persist_submission is not None:
                persist_submission({**handle, "target_preflight": asdict(target)})
            self.assignments.record_identity(
                str(planned["module"]),
                resident_id=str(handle.get("resident_id") or ""),
                lineage_id=str(handle.get("lineage_id") or ""),
            )
            emit(f"已提交 Resident：{handle.get('resident_id', '')} / {handle.get('message_id', '')}\n")

        runtime_options = {
            key: value
            for key, value in {"model": self.config.model, "permission_preset": "full-access"}.items()
            if value
        }
        assignment = self.assignments.assignment(str(planned["module"]))
        required_account = planned.get("resident_account_id")
        if not planned.get("_resume_invocation") and required_account and assignment.get("backend_route_id") != required_account:
            return AdapterResult(status="unsupported", details={
                "summary": "TAU 模块账号与本次 Campaign 指定账号不一致，未发送用例",
                "required_account": required_account,
                "configured_account": assignment.get("backend_route_id", ""),
            })
        runtime_options.update(assignment.get("runtime_options") or {})
        if not planned.get("_resume_invocation") and assignment.get("configuration_id") and assignment.get("resident_id"):
            from management_portal.test_framework.tau_runtime import prepare_configuration

            configuration = prepare_configuration(
                assignment, cancelled=should_cancel, emit=emit,
                refresh_assignment=lambda: self.assignments.assignment(str(planned["module"])),
                replace_unusable_assignment=lambda unusable: self.assignments.replace_unusable_resident(
                    str(planned["module"]),
                    expected_resident_id=str(unusable.get("resident_id") or ""),
                    changed_by=actor,
                ),
            )
            if not configuration["ready"]:
                emit("TAU Resident 的账号或 fast 配置尚未就绪，本用例未发送。\n")
                return AdapterResult(status="cancelled" if cancelled() else "unsupported", details={
                    "summary": "TAU Resident 配置尚未就绪，未发送用例", "configuration": configuration,
                })
            assignment = self.assignments.assignment(str(planned["module"]))
        resident_scope, lineage_scope = self.assignments.scopes(assignment)
        from management_common.resource_lifecycle import LifecycleStore

        lifecycle_id = LifecycleStore().open(str(run_dir.resolve()), actor=TAU_ACTOR,
                                             run_id=run_id, case_id=str(planned['case_id']))
        if resume_handle and resume_handle.get('resident_id'):
            LifecycleStore().bind(lifecycle_id, resume_handle['resident_id'])

        def write_run_context() -> None:
            # The case's first page action needs the evidence directory and the
            # build it starts from; the framework records both before the case
            # is sent, so that the Resident only reads them back (several TAU
            # cases failed only because a helper wrote the build after the
            # first navigation).
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name in ('fixtures', 'helpers'):
                (run_dir / name).mkdir(exist_ok=True, mode=0o700)
            context = {
                "run_id": run_id,
                "case_id": str(planned["case_id"]),
                "resource_lifecycle_id": lifecycle_id,
                "evidence_dir": str(run_dir.resolve()),
                "build_id": target.actual_build_id,
                "browser_runtime": browser_runtime_config(),
                "recorded_by": "framework",
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            path = run_dir / "run-context.json"
            if path.exists():
                # Resume keeps the original build/evidence identity, while
                # upgrading an older context with the durable cleanup scope.
                context = {**context, **json.loads(path.read_text(encoding="utf-8")),
                           "resource_lifecycle_id": lifecycle_id}
            standalone_reason = planned["implementation"].get("standalone_git_fixture", "")
            if not isinstance(standalone_reason, str):
                raise ValueError("Invalid TAU standalone_git_fixture")
            if standalone_reason:
                from management_portal.test_framework.tau_fixture_paths import prepare
                context["standalone_fixture_dir"] = str(prepare(run_dir))
            temporary = run_dir / ".run-context.json.tmp"
            temporary.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)

        def execute_or_resume() -> dict[str, Any]:
            if prepared and not submitted:
                resolver = self.resolve_submission
                if resolver is None:
                    from management_portal.resident_handoff import resolve_invocation_submission

                    resolver = resolve_invocation_submission
                resolved = resolver(actor=TAU_ACTOR, handle=dict(prepared))
                if resolved.get("submission_state") == "submitted" and resolved.get("message_id"):
                    handle_submitted(resolved)
                elif resolved.get("submission_state") == "not_submitted":
                    save_checkpoint(resolved, safe=True)
                    prepared.clear()
                    raise CaseNotSent("原提交已确认未受理；本用例不自动重新发送")
                else:
                    handle_assigned(resolved)
                    raise SubmissionPending(str(resolved.get("submission_reason") or resolved.get("reason") or "原提交结果尚待确认"))
            if submitted:
                if self.resume is None:
                    raise RuntimeError("TAU adapter has no Resident resume capability")
                write_run_context()
                return self.resume(
                    actor=TAU_ACTOR,
                    resident_id=submitted.get("resident_id", ""),
                    lineage_id=submitted.get("lineage_id", ""),
                    message_id=submitted.get("message_id", ""),
                    cancellation_requested=should_cancel,
                )
            current_assignment = self.assignments.assignment(str(planned["module"]))
            if required_account and current_assignment.get("backend_route_id") != required_account:
                raise CaseNotSent("TAU 模块账号已变更，与本次 Campaign 指定账号不一致，未发送用例")
            current_options = {**runtime_options, **(current_assignment.get("runtime_options") or {})}
            if current_assignment.get("resident_id") and current_assignment.get("configuration_id"):
                current_options.pop("service_tier", None)
            write_run_context()
            return self.invoke(
                actor=TAU_ACTOR,
                prompt=build_prompt(planned, self.config, run_dir, target, run_id, assignment=current_assignment),
                provider=self.config.provider,
                backend_route_id=str(current_assignment.get("backend_route_id") or ""),
                cwd=str(ROOT),
                resident_scope=resident_scope,
                lineage_scope=lineage_scope,
                title=f"Portal TAU · {planned['module']}",
                runtime_options=current_options,
                business_ref=f"{run_id}:{planned['case_id']}",
                metadata={"test_run_id": run_id, "test_case_id": planned["case_id"], "test_module_id": planned["module"], "runner": self.id},
                **({"resident_runtime": planned["resident_runtime"]} if planned.get("resident_runtime") else {}),
                cancellation_requested=should_cancel,
                on_assigned=lambda handle: handle_assigned(handle, before_submit=True),
                on_submitted=handle_submitted,
            )

        try:
            if isinstance(resume_handle, dict) and resume_handle:
                restored = {key: value for key, value in resume_handle.items() if isinstance(value, str)}
                if restored.get("message_id") and restored.get("submission_state", "submitted") == "submitted":
                    submitted.update(restored)
                else:
                    prepared.update(restored)
                emit(
                    "接续 Resident："
                    f"{restored.get('resident_id', '')} / {restored.get('message_id') or restored.get('request_id', '')}\n"
                )
            reconnect_reported = False
            while True:
                try:
                    result = execute_or_resume()
                    if reconnect_reported:
                        emit(f"已取得原消息 {submitted['message_id']} 的结果。\n")
                    break
                except SubmissionPending:
                    raise
                except Exception as exc:
                    from management_portal.resident_handoff import ResidentInputTerminated

                    if isinstance(exc, ResidentInputTerminated):
                        save_checkpoint(submitted, safe=True)
                        raise
                    # A submit exception is not a business rejection. Resolve the
                    # original request through its owner before touching the input.
                    if prepared and not submitted:
                        from management_portal.lineage_workflows import WorkflowPending, WorkflowFailed, submission_error

                        if isinstance(exc, (WorkflowPending, WorkflowFailed)):
                            handle_assigned(exc.handle)
                            if isinstance(exc, WorkflowFailed):
                                save_checkpoint(prepared, safe=True)
                                prepared.clear()
                                raise CaseNotSent(str(exc)) from exc
                        else:
                            handle_assigned(submission_error(prepared, exc))
                        if not reconnect_reported:
                            emit("TAU 正在只读核对原提交；不会重新投递或清理仍运行的环境。\n")
                            reconnect_reported = True
                        # The next iteration observes only; it never calls invoke.
                        try:
                            result = execute_or_resume()
                            break
                        except (SocietyServiceUnavailable, ResourceServiceUnavailable):
                            time.sleep(1.0)
                            continue
                    if not isinstance(exc, (SocietyServiceUnavailable, ResourceServiceUnavailable)):
                        raise
                    # Only a durably accepted input can be resumed safely.
                    # Replaying an unconfirmed submission could create work twice.
                    if (
                        not submitted.get("resident_id")
                        or not submitted.get("message_id")
                        or self.resume is None
                        or should_cancel()
                    ):
                        raise
                    if not reconnect_reported:
                        emit(f"TAU 依赖服务暂时不可用，等待接续原消息 {submitted['message_id']}：{exc}\n")
                        reconnect_reported = True
                    time.sleep(1.0)
        except Exception as exc:
            from management_portal.resident_handoff import ResidentInputTerminated

            if isinstance(exc, ResidentInputTerminated) and submitted:
                save_checkpoint(submitted, safe=True)
            cancellation: dict[str, Any] = {}
            cleanup_handle = submitted or prepared
            if (submitted.get("resident_id") and submitted.get("message_id")
                    and not self.cleanup_ready(run_dir=run_dir)):
                try:
                    state = self.cancel_invocation(
                        submitted["resident_id"],
                        TAU_ACTOR,
                        submitted["message_id"],
                    )
                    while state.get("state") not in {"completed", "failed", "cancelled"}:
                        time.sleep(1.0)
                        reader = self.read_input_state
                        if reader is None:
                            from management_portal import isa_client

                            state = isa_client.call(TAU_ACTOR, "GetInputState", {
                                "resident_id": submitted["resident_id"], "message_id": submitted["message_id"],
                            })["input_state"]
                        else:
                            state = reader(actor=TAU_ACTOR, resident_id=submitted["resident_id"],
                                           message_id=submitted["message_id"])
                    save_checkpoint(submitted, safe=True)
                    cancellation = {
                        "attempted": True,
                        "state": str(state.get("state") or ""),
                    }
                    emit(
                        "TAU 异常退出后已回收 Resident 输入："
                        f"{cleanup_handle['message_id']} / {cancellation['state'] or 'requested'}\n"
                    )
                except Exception as cancel_exc:
                    cancellation = {
                        "attempted": True,
                        "error": str(cancel_exc) or type(cancel_exc).__name__,
                    }
                    emit(
                        "TAU 异常退出后的 Resident 输入回收失败："
                        f"{cancellation['error']}\n"
                    )
            if not self.cleanup_ready(run_dir=run_dir):
                return AdapterResult(status="pending", details={
                    "summary": "原 TAU 提交或输入尚未确认终止，继续保留环境与恢复身份。",
                    "observation_error": str(exc) or type(exc).__name__,
                    "conversation": dict(cleanup_handle), **target_evidence,
                    **({"resident_cancellation": cancellation} if cancellation else {}),
                })
            if cancelled():
                return AdapterResult(
                    status="cancelled",
                    details={
                        "summary": "TAU 已取消。",
                        "issues": [],
                        "evidence": [],
                        **({"resident_cancellation": cancellation} if cancellation else {}),
                    },
                )
            message = str(exc) or type(exc).__name__
            if isinstance(exc, CaseNotSent) and not submitted.get("message_id"):
                emit(f"TAU 用例未发送：{message}\n")
                return AdapterResult(status="unsupported", details={
                    "summary": message, "issues": [], "evidence": [], **target_evidence,
                })
            emit(f"TAU Resident 执行失败：{message}\n")
            return AdapterResult(
                status="error",
                details={
                    "summary": "Resident 未能完成本次 TAU 用例。",
                    "issues": [message],
                    "evidence": [],
                    **target_evidence,
                    "conversation": {
                        "resident_id": cleanup_handle.get("resident_id", ""),
                        "lineage_id": cleanup_handle.get("lineage_id", ""),
                        "message_id": cleanup_handle.get("message_id", ""),
                    },
                    **({"resident_cancellation": cancellation} if cancellation else {}),
                },
            )
        save_checkpoint(submitted or result, safe=True)
        message = str(result.get("message") or "")
        self.assignments.record_identity(
            str(planned["module"]),
            resident_id=str(result.get("resident_id") or submitted.get("resident_id") or ""),
            lineage_id=str(result.get("lineage_id") or submitted.get("lineage_id") or ""),
        )
        if message:
            emit(message + "\n")
        requires_visual = "ui" in (planned.get("aspects") or [])
        try:
            parsed = parse_result(message, require_visual=requires_visual)
        except ValueError as exc:
            parse_error = str(exc)
            emit(f"TAU 结果解析失败：{parse_error}\n")
            return AdapterResult(
                status="error",
                details={
                    "summary": "Resident 已结束，但未交付可解析的 TAU 结论。",
                    "failure_class": "test",
                    "issues": [parse_error],
                    "evidence": [],
                    **target_evidence,
                    "conversation": {
                        "resident_id": str(
                            result.get("resident_id")
                            or submitted.get("resident_id")
                            or ""
                        ),
                        "lineage_id": str(
                            result.get("lineage_id")
                            or submitted.get("lineage_id")
                            or ""
                        ),
                        "message_id": str(
                            result.get("message_id")
                            or submitted.get("message_id")
                            or ""
                        ),
                    },
                },
            )
        invalid_evidence = []
        screenshot_evidence = []
        evidence_root = run_dir.resolve()
        for item in parsed["evidence"]:
            candidate = (run_dir / item).resolve(strict=False)
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

        target_postflight: dict[str, Any]
        try:
            completed_target = verify_ready_target()
            target_postflight = completed_target.result_details()
            baseline = original_target or target
            target_postflight["build_changed"] = (
                completed_target.actual_build_id != original_target.actual_build_id
                if original_target else None
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
                emit(
                    "记录正常 DEV 构建变化："
                    f"{baseline.actual_build_id} → {completed_target.actual_build_id}；保留本次行为结论。\n"
                )
        except DevTargetError as exc:
            target_postflight = dict(exc.details)
            parsed["status"] = "failed"
            parsed["issues"].append(f"TAU 运行后 DEV 复检失败：{exc}")
        status = str(parsed.pop("status"))
        return AdapterResult(
            status=status,
            details={
                **parsed,
                **target_evidence,
                "target_postflight": target_postflight,
                "conversation": {
                    "resident_id": str(result.get("resident_id") or submitted.get("resident_id") or ""),
                    "lineage_id": str(result.get("lineage_id") or submitted.get("lineage_id") or ""),
                    "message_id": str(result.get("message_id") or submitted.get("message_id") or ""),
                },
            },
        )
