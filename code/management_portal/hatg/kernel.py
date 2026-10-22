"""The instruction kernel shared by every owning service.

Every owning service runs one kernel with the handlers for the instructions
it owns.  The kernel follows the execution steps of decision_898b8911a2ff:
look up the declaration, validate arguments, ask the permission system,
answer read-only calls directly, and for state-changing calls register an
execution record, honour the optional idempotency key and the optional
expected version, run the handler and store the outcome.  The record is
written in its own short transactions; the handler commits through its
domain's transactions, so notifications and other services see committed
state.  A failure only affects the failing instruction.
"""
from __future__ import annotations

import copy
import logging
import secrets
import threading
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, ContextManager, Iterator, Mapping

from management_common.operation_identity import operation_identity_scope
from management_common.principal import OperationIdentity
from management_common.projection_receipts import CommittedProjectionError, collect_projection_failures

from . import slow_call_profile
from .catalog import declaration as declaration_for, instruction_catalog
from .contracts import (
    InstructionCall,
    InstructionContext,
    InstructionError,
    InstructionResult,
    failed_result,
    normalize_error,
)
from .declarations import OWNER_MARKS, InstructionDeclaration
from .store import ExecutionStore, PRUNE_BATCH_SIZE

LOGGER = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


_CURRENT_INSTRUCTION: ContextVar[str] = ContextVar("hatg_current_instruction", default="")


def current_instruction_id() -> str:
    """ID of the recorded instruction whose handler is running, or ``""``."""

    return _CURRENT_INSTRUCTION.get()


def new_instruction_id(owner: str) -> str:
    return f"inst_{OWNER_MARKS[owner]}_{secrets.token_hex(10)}"


def owner_of_instruction_id(instruction_id: str) -> str:
    parts = str(instruction_id or "").split("_")
    if len(parts) != 3 or parts[0] != "inst":
        return ""
    return next((owner for owner, mark in OWNER_MARKS.items() if mark == parts[1]), "")


@dataclass(frozen=True)
class Invocation:
    """One instruction as seen by its handler."""

    declaration: InstructionDeclaration
    call: InstructionCall
    context: InstructionContext
    instruction_id: str

    @property
    def name(self) -> str:
        return self.declaration.name

    @property
    def arguments(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.call.arguments))

    def argument(self, name: str, default: Any = None) -> Any:
        value = self.call.arguments.get(name)
        return copy.deepcopy(value) if value is not None else default

    @property
    def caller(self) -> str:
        return self.context.caller

    @property
    def entry(self) -> str:
        return self.context.entry

    @property
    def idempotency_key(self) -> str:
        return self.call.idempotency_key

    @property
    def expected_version(self) -> Any:
        return self.call.expected_version

    def expected_version_for(self, object_id: str) -> Any:
        """Version token the caller expects for one object, or None."""

        expected = self.call.expected_version
        if expected is None:
            return None
        if isinstance(expected, Mapping):
            return expected.get(object_id)
        return expected


Handler = Callable[[Invocation], Any]
StreamHandler = Callable[[Invocation], Iterator[list[dict[str, Any]]]]


@dataclass
class OwnerBinding:
    """What one owning service contributes to its kernel."""

    owner: str
    handlers: dict[str, Handler] = field(default_factory=dict)
    streams: dict[str, StreamHandler] = field(default_factory=dict)
    # Current version token of an object ID, for the optional version check.
    version_of: Callable[[Invocation, str], Any] | None = None
    # Record scope for a state-changing call: ("lineage", id) or ("caller", id).
    record_scope: Callable[[Invocation], tuple[str, str]] | None = None
    # Serialization/state scope around a state-changing call.
    execution_scope: Callable[[Invocation], ContextManager[Any]] | None = None
    # Complete projections that failed after a committed domain change.
    recover_projections: Callable[[list[dict[str, Any]], Invocation], None] | None = None
    # A declared state-changing instruction whose concrete call reads only
    # (for example a read-only resource action) runs without a record.
    reads_only: Callable[["Invocation"], bool] | None = None
    # The arguments a call is judged on when the owner derives one the caller may omit
    # (RunScript's Lineage); the recorded call and the handler keep the caller's arguments.
    authorization_arguments: Callable[[InstructionDeclaration, Mapping[str, Any], InstructionContext], Mapping[str, Any]] | None = None

    def handler(self, name: str) -> Handler:
        return self.handlers[name]


def _runs_published_code_against_shared_catalog() -> bool:
    """Whether this process imports the shared packages from another tree (Release Web)."""
    import sys

    return any(type(finder).__name__ == "SharedSourceFinder" for finder in sys.meta_path)


def _with_unpublished_handlers(binding: OwnerBinding, names: list[str]) -> OwnerBinding:
    """A copy of ``binding`` whose missing instructions answer as unavailable."""
    import dataclasses

    def unavailable(name: str) -> Callable[..., Any]:
        def handler(_invocation: Any) -> Any:
            raise InstructionError(
                "instruction.owner_unavailable",
                f"{name} 的实现还没有发布到当前 Portal 实例",
                retryable=True,
            )
        return handler

    catalog = instruction_catalog()
    handlers = dict(binding.handlers)
    streams = dict(binding.streams)
    for name in names:
        (streams if catalog[name].streaming else handlers)[name] = unavailable(name)
    return dataclasses.replace(binding, handlers=handlers, streams=streams)


class InstructionKernel:
    def __init__(
        self,
        binding: OwnerBinding,
        *,
        store: ExecutionStore | None,
        authorize: Callable[[InstructionDeclaration, Mapping[str, Any], InstructionContext], None] | None = None,
    ) -> None:
        self.binding = binding
        self.owner = binding.owner
        self.store = store
        self._projection_cursor = ""
        self._maintenance_lock = threading.Lock()
        self._authorize = authorize
        missing = sorted(
            name
            for name, item in instruction_catalog().items()
            if self.owner in item.owners
            and name not in (binding.streams if item.streaming else binding.handlers)
        )
        if missing and _runs_published_code_against_shared_catalog():
            # Release Web runs a published commit against the catalog in the
            # Dev tree. An instruction the catalog gained before its Web
            # handler was published must not keep Release Web from starting
            # (management-portal-release.service failed this way on 10-02):
            # calls to it fail as unavailable until the handler ships.
            LOGGER.warning("%s 尚未发布这些指令的实现，调用将返回不可用：%s", self.owner, "、".join(missing))
            binding = _with_unpublished_handlers(binding, missing)
            self.binding = binding
        elif missing:
            raise RuntimeError(
                f"{self.owner} 拥有的指令缺少实现：" + "、".join(missing)
            )
        catalog = instruction_catalog()
        registered = [*binding.handlers, *binding.streams]
        foreign = sorted(name for name in registered if name in catalog and self.owner not in catalog[name].owners)
        if foreign:
            raise RuntimeError(f"{self.owner} 为不属于自己的指令登记了实现：" + "、".join(foreign))
        # Release Web runs a published commit against the shared catalog in the
        # Dev tree, so it can still carry a handler for an instruction the catalog
        # has since removed. Calls resolve through the catalog first, so such a
        # handler is never reached.
        retired = sorted(name for name in registered if name not in catalog)
        if retired:
            LOGGER.warning("%s 登记了目录中已不存在的指令，忽略：%s", self.owner, "、".join(retired))

    # -- helpers -------------------------------------------------------------

    def _resolve(self, call: InstructionCall, *, streaming: bool) -> InstructionDeclaration:
        declaration = declaration_for(call.instruction)
        owner = declaration.owner_for(call.arguments)
        if owner != self.owner:
            raise InstructionError(
                "instruction.failed",
                f"{call.instruction} 由 {owner} 执行，不能在 {self.owner} 执行",
            )
        if declaration.streaming != streaming:
            raise InstructionError(
                "instruction.invalid_arguments",
                (
                    f"{call.instruction} 是流式指令，请通过订阅入口调用"
                    if declaration.streaming
                    else f"{call.instruction} 不是流式指令"
                ),
            )
        declaration.validate_arguments(call.arguments)
        return declaration

    def _authorize_call(
        self, declaration: InstructionDeclaration, call: InstructionCall, context: InstructionContext,
        *, instruction_id: str = "",
    ):
        if _portal_system_call(context):
            return
        derive = self.binding.authorization_arguments
        arguments = derive(declaration, call.arguments, context) if derive is not None else call.arguments
        if self._authorize is not None:
            return self._authorize(declaration, arguments, context)
        from management_portal import permission_system

        return permission_system.authorize_instruction(declaration, arguments, context, instruction_id=instruction_id)

    @staticmethod
    @contextmanager
    def _identity_scope(context: InstructionContext) -> Iterator[None]:
        from management_common.execution_association import execution_scope
        from management_common.resource_lifecycle import lifecycle_scope

        try:
            identity_scope: ContextManager[Any] = operation_identity_scope(OperationIdentity.for_actor(context.caller))
        except (ValueError, TypeError):
            identity_scope = nullcontext()
        with identity_scope, (execution_scope(context.execution_id) if context.execution_id else nullcontext()), lifecycle_scope(
            context.resource_lifecycle_id, actor=context.caller, discover=True,
        ):
            yield

    @staticmethod
    @contextmanager
    def _instruction_scope(instruction_id: str) -> Iterator[None]:
        token = _CURRENT_INSTRUCTION.set(instruction_id)
        try:
            yield
        finally:
            _CURRENT_INSTRUCTION.reset(token)

    def _check_versions(self, invocation: Invocation) -> None:
        expected = invocation.expected_version
        if expected is None:
            return
        if self.binding.version_of is None:
            return  # Objects without a token accept the control without comparison.
        if isinstance(expected, Mapping):
            pairs = list(expected.items())
        else:
            from .declarations import target_value

            target = target_value(invocation.declaration, invocation.call.arguments)
            if not target:
                return  # The handler may compare a domain-specific token itself.
            pairs = [(target, expected)]
        for object_id, token in pairs:
            current = self.binding.version_of(invocation, object_id)
            if current is None:
                # The owning domain compares this token itself (for example a
                # resource provider's own content version).
                continue
            if str(current) != str(token):
                raise InstructionError(
                    "instruction.version_conflict",
                    f"{object_id} 的版本已经变化",
                    details={"object_id": object_id, "expected_version": token, "current_version": current},
                )

    def _scope(self, invocation: Invocation) -> tuple[str, str]:
        if self.binding.record_scope is not None:
            return self.binding.record_scope(invocation)
        return ("caller", invocation.caller)

    # -- public API ------------------------------------------------------------

    def execute(
        self, call: InstructionCall, context: InstructionContext, *, instruction_id: str = "",
    ) -> InstructionResult:
        if instruction_id != "":
            import re

            if not isinstance(instruction_id, str) or not re.fullmatch(
                rf"inst_{OWNER_MARKS[self.owner]}_[0-9a-f]{{20}}", instruction_id,
            ):
                return failed_result(new_instruction_id(self.owner), call.instruction,
                                     InstructionError("instruction.invalid_arguments", "执行 ID 与指令 owner 不匹配"))
        else:
            instruction_id = new_instruction_id(self.owner)
        try:
            declaration = self._resolve(call, streaming=False)
            self._authorize_call(declaration, call, context, instruction_id=instruction_id)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a result
            return failed_result(instruction_id, call.instruction, normalize_error(exc))
        invocation = Invocation(declaration, call, context, instruction_id)
        if not declaration.mutates or (self.binding.reads_only is not None and self.binding.reads_only(invocation)):
            try:
                with self._identity_scope(context), slow_call_profile.track(self.owner, declaration.name):
                    result = self.binding.handler(declaration.name)(invocation)
            except Exception as exc:  # noqa: BLE001
                return failed_result(instruction_id, call.instruction, normalize_error(exc))
            completed = InstructionResult(instruction_id, call.instruction, "committed", result=result)
        else:
            completed = self._execute_mutation(invocation)
        return completed

    def _execute_mutation(self, invocation: Invocation) -> InstructionResult:
        call = invocation.call
        scope_factory = self.binding.execution_scope
        try:
            activity = self.store.active_execution(invocation.instruction_id) if self.store else nullcontext(True)
            with activity as acquired, (scope_factory(invocation) if scope_factory else nullcontext()):
                if not acquired:
                    raise InstructionError("instruction.in_progress", "指令仍在执行", retryable=True, commit_state="unknown")
                # The record is written in its own short transactions; the
                # handler commits through its domain's own transactions, so
                # notifications and other services see committed state.
                return self._run_recorded(invocation)
        except Exception as exc:  # noqa: BLE001
            return failed_result(invocation.instruction_id, call.instruction, normalize_error(exc))

    def _run_recorded(self, invocation: Invocation) -> InstructionResult:
        store = self.store
        call = invocation.call
        instruction_id = invocation.instruction_id
        if store is not None:
            record, replayed = store.begin(
                instruction_id=instruction_id,
                call=call,
                caller=invocation.caller,
                entry=invocation.entry,
                scope=self._scope(invocation),
                now=now_iso(),
                recovery_key=invocation.context.recovery_key,
            )
            if replayed:
                return self._replay(invocation, record)
        try:
            self._check_versions(invocation)
        except Exception as exc:  # noqa: BLE001 - token reads can fail like handlers
            error = normalize_error(exc)
            if store is not None:
                store.finish(instruction_id, status="failed", now=now_iso(), error=error.to_dict())
            return failed_result(instruction_id, call.instruction, error)
        failures: list[dict[str, Any]] = []
        committed_failure: Exception | None = None
        try:
            with self._identity_scope(invocation.context), self._instruction_scope(instruction_id), \
                    slow_call_profile.track(self.owner, invocation.name):
                with collect_projection_failures() as failures:
                    try:
                        result = self.binding.handler(invocation.name)(invocation)
                    except Exception as exc:  # noqa: BLE001
                        if not failures:
                            raise
                        committed_failure = exc
            if committed_failure is not None:
                raise committed_failure
        except CommittedProjectionError as exc:
            # The domain committed but reported its projections as pending itself.
            error = self._projection_error(invocation, [*failures, *exc.failures])
            if store is not None:
                store.finish(
                    instruction_id, status="committed", now=now_iso(), result=None,
                    error={**error.to_dict(), "details": {"projection_failures": error.details["projection_failures"]}},
                )
            return failed_result(instruction_id, call.instruction, error)
        except Exception as exc:  # noqa: BLE001
            error = normalize_error(exc)
            if failures:
                error.details.update({
                    "domain_committed": True,
                    "projection_failures": copy.deepcopy(failures),
                })
                error.commit_state = "committed"
            if store is not None:
                store.finish(instruction_id, status="failed", now=now_iso(), error=error.to_dict())
            return failed_result(instruction_id, call.instruction, error)
        if failures:
            error = self._projection_error(invocation, failures)
            if store is not None:
                store.finish(
                    instruction_id, status="committed", now=now_iso(), result=result,
                    error={**error.to_dict(), "details": {"projection_failures": failures}},
                )
            return failed_result(instruction_id, call.instruction, error)
        if store is not None:
            store.finish(instruction_id, status="committed", now=now_iso(), result=result)
        return InstructionResult(instruction_id, call.instruction, "committed", result=result)

    def _projection_error(self, invocation: Invocation, failures: list[dict[str, Any]]) -> InstructionError:
        retryable = bool(invocation.idempotency_key and self.binding.recover_projections)
        return InstructionError(
            "instruction.projection_pending",
            "领域修改已经提交，页面投影或资源索引尚未确认；用同一调用和同一幂等键重试只补齐投影",
            details={
                "instruction_id": invocation.instruction_id,
                "domain_committed": True,
                "projection_failures": copy.deepcopy(failures),
                **({"retry": "same_instruction_same_idempotency_key", "retryable": True} if retryable else {}),
            },
            retryable=retryable,
            commit_state="committed",
        )

    def _replay(self, invocation: Invocation, record: dict[str, Any]) -> InstructionResult:
        instruction_id = str(record.get("instruction_id") or invocation.instruction_id)
        if (record.get("error") or {}).get("code") == "instruction.projection_pending":
            record = self._recover_projection(invocation, record, now=now_iso())
        error = record.get("error") if isinstance(record.get("error"), Mapping) else None
        if record.get("status") == "failed" or error:
            failure = InstructionError(
                str((error or {}).get("code") or "instruction.failed"),
                str((error or {}).get("message") or "指令执行失败"),
                details=(error or {}).get("details") if isinstance((error or {}).get("details"), Mapping) else {},
                retryable=bool((error or {}).get("retryable")),
                commit_state=str((error or {}).get("commit_state") or "not_committed"),
            )
            return InstructionResult(
                instruction_id, invocation.name, "failed", error=failure.to_dict(), replayed=True,
            )
        return InstructionResult(
            instruction_id,
            invocation.name,
            "committed",
            result=copy.deepcopy(record.get("references") or {}),
            replayed=True,
        )

    def _recover_projection(self, invocation: Invocation, record: dict[str, Any], *, now: str) -> dict[str, Any]:
        if self.binding.recover_projections is None or self.store is None:
            return record
        instruction_id = record["instruction_id"]
        # A retry and background maintenance may arrive together. The lock is
        # outside a transaction, and the receipt is reread after acquiring it.
        with self.store.active_execution(instruction_id) as acquired:
            if not acquired:
                return record
            current = self.store.get(instruction_id)
            if current is None or (current.get("error") or {}).get("code") != "instruction.projection_pending":
                return current or record
            failures = list((current["error"].get("details") or {}).get("projection_failures") or [])
            error = None
            try:
                if not failures:
                    raise ValueError("执行记录缺少可恢复的投影引用")
                with collect_projection_failures() as remaining, self._identity_scope(invocation.context):
                    self.binding.recover_projections(failures, invocation)
                if remaining:
                    return current
            except Exception as exc:
                failure = normalize_error(exc)
                if failure.code not in {"instruction.not_found", "instruction.forbidden", "instruction.invalid_arguments"}:
                    # Outages leave the committed receipt intact for the next
                    # maintenance cycle or a caller's same-key retry.
                    return current
                error = InstructionError(
                    failure.code, f"领域修改已提交，投影恢复无法完成：{failure}",
                    commit_state="committed", details={
                        "reason": "projection_recovery_failed", "projection_failures": failures,
                    },
                ).to_dict()
            self.store.finish(instruction_id, status="failed" if error else "committed", now=now,
                              result=current.get("references"), error=error)
            return self.store.get(instruction_id)

    def maintain(self, *, now: str | None = None) -> dict[str, int]:
        """Bounded owner maintenance; never invoke a business handler."""
        counts = {"interrupted": 0, "projections": 0, "pruned": 0}
        if self.store is None or not self._maintenance_lock.acquire(blocking=False):
            return counts
        try:
            timestamp = now or now_iso()
            counts["interrupted"] = self.store.settle_interrupted(now=timestamp)
            records = self.store.pending_projections(after=self._projection_cursor)
            self._projection_cursor = records[-1]["instruction_id"] if len(records) == PRUNE_BATCH_SIZE else ""
            for record in records:
                # Receipts hold references, not original arguments. Projection
                # owners must recover from those references and current facts.
                try:
                    invocation = Invocation(
                        declaration_for(record["instruction"]), InstructionCall(record["instruction"], {}),
                        InstructionContext(record["caller"], "system"), record["instruction_id"],
                    )
                    recovered = self._recover_projection(invocation, record, now=timestamp)
                except Exception:
                    LOGGER.exception("Instruction projection recovery failed: %s", record["instruction_id"])
                    continue
                if (recovered.get("error") or {}).get("code") != "instruction.projection_pending":
                    counts["projections"] += 1
            counts["pruned"] = self.store.prune(now=timestamp)
            return counts
        finally:
            self._maintenance_lock.release()

    def stream(self, call: InstructionCall, context: InstructionContext) -> Iterator[list[dict[str, Any]]]:
        """Validate and authorize, then yield batches of stream items."""

        declaration = self._resolve(call, streaming=True)
        instruction_id = new_instruction_id(self.owner)
        self._authorize_call(declaration, call, context, instruction_id=instruction_id)
        invocation = Invocation(declaration, call, context, instruction_id)
        return self.binding.streams[declaration.name](invocation)

    def execution_record_for_request(
        self, instruction: str, request_key: str, context: InstructionContext,
    ) -> dict[str, Any]:
        """Locate this actor's original request, then apply normal receipt authorization."""
        instruction_id = self.store.find_instruction_id(
            caller=context.caller, instruction=instruction, request_key=request_key,
        ) if self.store is not None else ""
        if not instruction_id:
            raise InstructionError("instruction.not_found", "执行记录不存在或已被回收")
        return self.execution_record(instruction_id, context)

    def execution_record(self, instruction_id: str, context: InstructionContext) -> dict[str, Any]:
        if self.store is None:
            raise InstructionError("instruction.not_found", "执行记录不存在或已被回收")
        record = self.store.get(instruction_id)
        if record is None:
            raise InstructionError("instruction.not_found", "执行记录不存在或已被回收")
        if not _portal_system_call(context):
            # The scheme judges a Lineage-scoped receipt on the Lineage the saved receipt names
            # (``via: instruction_execution.lineage``), never on anything the caller says.
            token = _RECEIPT_IN_HAND.set(record)
            try:
                self._authorize_call(
                    declaration_for("GetInstructionExecution"),
                    InstructionCall("GetInstructionExecution", {"instruction_id": instruction_id}), context,
                )
            finally:
                _RECEIPT_IN_HAND.reset(token)
            if record["scope_kind"] != "lineage" and record["caller"] != context.caller:
                # A receipt scoped to its caller is open only to that caller.
                raise InstructionError("instruction.not_found", "执行记录不存在或已被回收")
        if record["status"] == "executing":
            # Authorize before touching another caller's receipt. The same
            # activity lock used by retry and maintenance proves owner liveness;
            # querying a disconnected execution never repeats its handler.
            self.store.settle_execution(instruction_id, now=now_iso())
            record = self.store.get(instruction_id)
        result = {
            key: copy.deepcopy(record[key])
            for key in ("instruction_id", "instruction", "status", "references", "error", "created_at", "updated_at")
        }
        return result


_KERNELS: dict[str, InstructionKernel] = {}
_KERNELS_LOCK = threading.Lock()


def register_kernel(kernel: InstructionKernel) -> None:
    with _KERNELS_LOCK:
        _KERNELS[kernel.owner] = kernel


def local_kernel(owner: str) -> InstructionKernel | None:
    with _KERNELS_LOCK:
        return _KERNELS.get(owner)


def reset_kernels_for_tests() -> None:
    with _KERNELS_LOCK:
        _KERNELS.clear()


# The receipt GetInstructionExecution has already read, while the scheme judges it.
_RECEIPT_IN_HAND: ContextVar[dict[str, Any] | None] = ContextVar("hatg_receipt_in_hand", default=None)


def receipt_scopes(instruction_ids: list[str], _arguments: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """The Lineage each receipt belongs to ("" when scoped to its caller), for ``via: instruction_execution.lineage``.

    Receipts are read from this process's kernels; one kept by another service cannot be judged here.
    """
    from management_portal.permission_system.requests import Unresolved

    in_hand = _RECEIPT_IN_HAND.get()
    found = {}
    for instruction_id in instruction_ids:
        if in_hand is not None and in_hand["instruction_id"] == instruction_id:
            record = in_hand
        else:
            kernel = local_kernel(owner_of_instruction_id(instruction_id))
            if kernel is None or kernel.store is None:
                raise Unresolved(f"执行记录 {instruction_id} 不在这个服务中")
            record = kernel.store.get(instruction_id)
        if record is not None:
            found[instruction_id] = {"lineage": record["scope_id"] if record["scope_kind"] == "lineage" else ""}
    return found



def _portal_system_call(context: InstructionContext) -> bool:
    """Portal's own in-process work, which holds no credentials.

    In-process callers acting for a User or Resident (Chatroom delivery,
    Notice Board classification) also use the system entry; they name that
    actor as caller and go through the policy like any other entry.
    """
    from management_common.principal import actor_principal

    # An explicit application Event keeps Portal's authenticated service
    # principal when entering its Effect. The Lineage entry retains recovery
    # bookkeeping and the trigger-path trace; it never becomes a User caller.
    if context.entry != "system" and not (
        context.entry == "lineage" and context.trigger_path_id
    ):
        return False
    try:
        principal = actor_principal(context.caller)
    except ValueError:
        return False
    return principal.kind == "service" and principal.id == "portal"
