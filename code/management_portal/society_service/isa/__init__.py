"""SocietyService instruction handlers and its kernel.

SocietyService owns the Resident, conversation (except the inbox),
execution/queue, Lineage definition, shared state, run and reusable-module
instructions, plus the organization instructions other than Project ones.
"""
from __future__ import annotations

from typing import Any

from management_portal.hatg.kernel import InstructionKernel, Invocation, OwnerBinding

from . import conversation, definition, execution, families, function_blocks, organization, residents, runs, shared_state, script_runs, templates, system_workflows
from .common import guard_definition_handler
from ..lineage_definition_scope import guard_source_scope


def _handlers() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for module in (residents, conversation, execution, definition, shared_state, runs, families, function_blocks, organization, script_runs, templates, system_workflows):
        for name, handler in module.HANDLERS.items():
            if name in merged:
                raise RuntimeError(f"SocietyService 重复登记指令实现：{name}")
            merged[name] = guard_source_scope(name, guard_definition_handler(name, handler))
    return merged


def _streams() -> dict[str, Any]:
    return {**conversation.STREAMS, **definition.STREAMS, **runs.STREAMS}


def record_scope(inv: Invocation) -> tuple[str, str]:
    lineage_id = str(inv.call.arguments.get("lineage_id") or "")
    if inv.declaration.target == "lineage_id" and lineage_id.startswith("lineage_"):
        return ("lineage", lineage_id)
    return ("caller", inv.caller)


def recover_projections(failures: list[dict[str, Any]], inv: Invocation) -> None:
    from management_portal.society_service import lineage_domain

    lineage_domain.retry_lineage_projections(failures, inv.caller)


def authorization_arguments(declaration, arguments, context):
    """RunScript is judged on the Lineage it runs in, also when the caller omits ``lineage_id``."""
    if declaration.name != "RunScript":
        return arguments
    from management_portal.society_service.script_runs.service import owning_lineage

    return {**arguments, "lineage_id": owning_lineage(arguments, context.caller)}


def binding() -> OwnerBinding:
    return OwnerBinding(
        owner="lineage",
        handlers=_handlers(),
        streams=_streams(),
        record_scope=record_scope,
        recover_projections=recover_projections,
        authorization_arguments=authorization_arguments,
    )


def build_kernel() -> InstructionKernel:
    from management_portal.hatg.store import ExecutionStore
    from management_portal.society_service.state_store import get_lineage_data_store

    return InstructionKernel(
        binding(),
        store=ExecutionStore(get_lineage_data_store().database, create=False),
    )
