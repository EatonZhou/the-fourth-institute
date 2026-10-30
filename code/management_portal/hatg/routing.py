"""Send each call to the service that owns its target (decision_898b8911a2ff).

Entries (web, CLI, Harness tool, Lineage runtime) call :func:`execute` or
:func:`stream`.  The router looks up the declaration and hands the call to the
owning service: in process when this process runs that owner's kernel,
otherwise over the owner's ``instruction.execute`` socket.  Reading the
instruction catalog is answered by the entry itself; an execution record is
read from the service whose mark is in the instruction ID.

``PORTAL_ISA_ROUTING=socket`` is set for deployed Portal processes.  The
default ``local`` builds every owner's kernel in this process, which is what
tests and one-off scripts use.
"""
from __future__ import annotations

import copy
import importlib
import logging
import os
import threading
from typing import Any, Iterator, Mapping

from .catalog import declaration as declaration_for, instruction_catalog
from .contracts import (
    InstructionCall,
    InstructionContext,
    InstructionError,
    InstructionResult,
    failed_result,
    normalize_error,
)
from .kernel import (
    InstructionKernel,
    is_instruction_id,
    local_kernel,
    new_instruction_id,
    owner_of_instruction_id,
    register_kernel,
)

LOGGER = logging.getLogger(__name__)

KERNEL_BUILDERS = {
    "lineage": "management_portal.society_service.isa:build_kernel",
    "resource": "management_portal.resources.isa:build_kernel",
    "execution": "management_portal.execution.isa:build_kernel",
    "web": "management_portal.web_isa:build_kernel",
}
_BUILD_LOCK = threading.Lock()


def routing_mode() -> str:
    return str(os.getenv("PORTAL_ISA_ROUTING") or "local").strip().lower()


def build_kernel(owner: str) -> InstructionKernel:
    module_name, _, function = KERNEL_BUILDERS[owner].partition(":")
    return getattr(importlib.import_module(module_name), function)()


def kernel_for(owner: str, *, required: bool = False) -> InstructionKernel | None:
    kernel = local_kernel(owner)
    if kernel is not None:
        return kernel
    if routing_mode() == "socket" and not required:
        return None
    with _BUILD_LOCK:
        kernel = local_kernel(owner)
        if kernel is None:
            kernel = build_kernel(owner)
            register_kernel(kernel)
    return kernel


def _as_call(call: InstructionCall | Mapping[str, Any]) -> InstructionCall:
    return call if isinstance(call, InstructionCall) else InstructionCall.from_payload(call)


def assign_instruction_id(call: InstructionCall | Mapping[str, Any], *, reuse: str = "") -> str:
    """An owner-routable ID a caller can journal before :func:`execute`.

    The owner records the call under this ID, so a caller interrupted before
    the result arrives still knows which record holds its recovery dependency.
    ``reuse`` (the ID an interrupted attempt journaled) is kept when the same
    owner could have assigned it. Empty when the call cannot be routed.
    """
    try:
        parsed = _as_call(call)
        declaration = declaration_for(parsed.instruction)
    except InstructionError:  # an invalid call or unknown instruction: execute reports the same error
        return ""
    except Exception:  # noqa: BLE001 - so does execute for any other
        LOGGER.warning("Instruction ID not assigned; the call cannot be routed", exc_info=True)
        return ""
    if declaration.owner == "entry":
        return ""
    owner = declaration.owner_for(parsed.arguments)
    return reuse if is_instruction_id(reuse, owner) else new_instruction_id(owner)


def execute(
    call: InstructionCall | Mapping[str, Any], context: InstructionContext, *, instruction_id: str = "",
) -> InstructionResult:
    """Execute one call from any entry and return the v2 result.

    ``instruction_id`` comes from :func:`assign_instruction_id`; empty lets
    the owner assign one.
    """

    from dataclasses import replace
    from management_common.resource_lifecycle import current_lifecycle

    if current_lifecycle() and not context.resource_lifecycle_id:
        context = replace(context, resource_lifecycle_id=current_lifecycle())

    try:
        parsed = _as_call(call)
        declaration = declaration_for(parsed.instruction)
    except Exception as exc:  # noqa: BLE001
        name = str(call.get("instruction") or "") if isinstance(call, Mapping) else ""
        return failed_result(new_instruction_id("entry"), name, normalize_error(exc))
    if declaration.owner == "entry":
        return _self_description(parsed, context)
    owner = declaration.owner_for(parsed.arguments)
    kernel = kernel_for(owner)
    if kernel is not None:
        return kernel.execute(parsed, context, instruction_id=instruction_id)
    from .socket_rpc import call_remote

    try:
        return call_remote(owner, parsed, context, instruction_id=instruction_id)
    except Exception as exc:  # noqa: BLE001
        return failed_result(instruction_id or new_instruction_id("entry"), parsed.instruction, normalize_error(exc))


def execute_local(
    call: InstructionCall, context: InstructionContext, *, owner: str, instruction_id: str = "",
) -> InstructionResult:
    """Execute on this process's own kernel (used by the socket method)."""

    declaration = declaration_for(call.instruction)
    if declaration.owner == "entry":
        return _self_description(call, context, local_owner=owner)
    if declaration.owner_for(call.arguments) != owner:
        return failed_result(
            new_instruction_id(owner),
            call.instruction,
            InstructionError("instruction.failed", f"{call.instruction} 不由 {owner} 执行"),
        )
    kernel = kernel_for(owner, required=True)
    return kernel.execute(call, context, instruction_id=instruction_id)


def stream(call: InstructionCall | Mapping[str, Any], context: InstructionContext, *, cancellation=None) -> Iterator[list[dict[str, Any]]]:
    """Subscribe to a streaming instruction; yields batches of items."""

    parsed = _as_call(call)
    declaration = declaration_for(parsed.instruction)
    if not declaration.streaming:
        raise InstructionError("instruction.invalid_arguments", f"{parsed.instruction} 不是流式指令")
    kernel = kernel_for(declaration.owner)
    if kernel is not None:
        return kernel.stream(parsed, context)
    from .socket_rpc import stream_remote

    return stream_remote(declaration.owner, parsed, context, cancellation=cancellation)


def stream_local(call: InstructionCall, context: InstructionContext, *, owner: str) -> Iterator[list[dict[str, Any]]]:
    return kernel_for(owner, required=True).stream(call, context)


def execute_or_raise(call: InstructionCall | Mapping[str, Any], context: InstructionContext) -> Any:
    """Convenience for in-process callers: return the result or raise its error."""

    return execute(call, context).raise_for_error()


def release_recovery(recovery_key: str, instruction_ids: list[str], context: InstructionContext) -> dict[str, int]:
    """Acknowledge durable Effect completion to the original instruction owners.

    This is an internal runtime acknowledgement, not a user-callable ISA.
    A failed owner remains protected and can safely be acknowledged again.
    """
    if context.entry != "lineage" or not recovery_key or context.recovery_key != recovery_key:
        raise InstructionError("instruction.forbidden", "只有持有对应恢复依赖的 Lineage 运行入口可以释放")
    grouped: dict[str, list[str]] = {}
    for instruction_id in set(instruction_ids):
        owner = owner_of_instruction_id(instruction_id)
        if owner in KERNEL_BUILDERS:
            grouped.setdefault(owner, []).append(instruction_id)
    released = {}
    for owner, ids in grouped.items():
        kernel = kernel_for(owner)
        if kernel is not None:
            released[owner] = kernel.store.release_recovery(caller=context.caller, recovery_key=recovery_key, instruction_ids=ids)
        else:
            from .socket_rpc import release_recovery_remote
            released[owner] = release_recovery_remote(owner, recovery_key, ids, context)
    return released


# -- self-description (decision_aa4fdbac8364) ------------------------------------


def _resource_actions() -> list[dict[str, Any]]:
    from .resource_actions import resource_action_catalog

    return [action.schema() for action in resource_action_catalog().values()]


def list_instructions(category: str = "") -> list[dict[str, Any]]:
    items = []
    for name in sorted(instruction_catalog()):
        declaration = instruction_catalog()[name]
        if category and declaration.category != category:
            continue
        summary = declaration.summary()
        if name == "RunResourceAction":
            summary["resource_actions"] = _resource_actions()
        items.append(summary)
    return items


def instruction_schema(name: str) -> dict[str, Any]:
    from management_portal import permission_system

    schema = declaration_for(name).schema()
    # What a call needs under the active permission scheme (decision_b820faf42218).
    schema["permission"] = permission_system.instruction_requirement(name)
    if name == "RunResourceAction":
        schema["resource_actions"] = _resource_actions()
    return schema


def _self_description(
    call: InstructionCall, context: InstructionContext, *, local_owner: str = "",
) -> InstructionResult:
    instruction_id = new_instruction_id("entry")
    try:
        declaration = declaration_for(call.instruction)
        declaration.validate_arguments(call.arguments)
        if call.instruction == "ListInstructions":
            category = str(call.arguments.get("category") or "")
            # Every caller discovers instructions here first, so the agent guide is named here too.
            result: Any = {"instructions": list_instructions(category), "guide": "/guide/agent.md"}
        elif call.instruction == "GetInstructionSchema":
            result = {"schema": instruction_schema(str(call.arguments["instruction"]))}
        elif call.instruction == "GetInstructionExecution":
            instruction_id_arg = str(call.arguments.get("instruction_id") or "")
            original_instruction = str(call.arguments.get("instruction") or "")
            request_key = str(call.arguments.get("idempotency_key") or "").strip()
            if instruction_id_arg:
                if original_instruction or call.arguments.get("idempotency_key"):
                    raise InstructionError("instruction.invalid_arguments", "instruction_id 与 instruction+idempotency_key 互斥")
                owner = owner_of_instruction_id(instruction_id_arg)
            else:
                if not original_instruction or not request_key:
                    raise InstructionError("instruction.invalid_arguments", "需要原指令名和非空幂等键")
                original = declaration_for(original_instruction)
                if original.argument_owners:
                    # The key alone does not say which service ran the call.
                    raise InstructionError("instruction.invalid_arguments", f"{original_instruction} 按参数在不同服务执行，请用 instruction_id 查询")
                owner = original.owner
                if not original.mutates:
                    raise InstructionError("instruction.not_found", "执行记录不存在；只读指令不保存执行记录")
            if owner in {"", "entry"}:
                raise InstructionError("instruction.not_found", "执行记录不存在；只读指令不保存执行记录")
            if local_owner and owner != local_owner:
                raise InstructionError("instruction.not_found", "执行记录不在这个服务中")
            kernel = kernel_for(owner, required=bool(local_owner))
            if kernel is None:
                from .socket_rpc import call_remote

                return call_remote(owner, call, context)
            if instruction_id_arg:
                result = kernel.execution_record(instruction_id_arg, context)
            else:
                result = kernel.execution_record_for_request(original_instruction, request_key, context)
        else:  # pragma: no cover - catalog guarantees the three names
            raise InstructionError("instruction.unknown", call.instruction)
    except Exception as exc:  # noqa: BLE001
        return failed_result(instruction_id, call.instruction, normalize_error(exc))
    return InstructionResult(instruction_id, call.instruction, "committed", result=copy.deepcopy(result))
