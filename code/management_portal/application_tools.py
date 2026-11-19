"""Web-owned application operations; deliberately separate from the HATG ISA."""
from __future__ import annotations

from functools import lru_cache

from .application_tool_contracts import ApplicationToolDeclaration, permission_catalog, permission_declaration, tool_declarations
from .hatg.contracts import InstructionCall, InstructionContext, InstructionError, normalize_error
from .portal_features import contributions


@lru_cache(maxsize=1)
def registry():
    declarations = tool_declarations()
    handlers, versions = {}, {}
    for feature, module in contributions("resident.tools"):
        if handlers.keys() & module.HANDLERS.keys():
            raise RuntimeError(f"Duplicate application tool handler in {feature}")
        handlers.update(module.HANDLERS)
        if getattr(module, "version_of", None):
            versions.update(dict.fromkeys(module.HANDLERS, module.version_of))
    names = {declaration.name for declaration in declarations.values()}
    if names != handlers.keys():
        raise RuntimeError(f"Application tool declarations and handlers disagree: {sorted(names ^ handlers.keys())}")
    return declarations, handlers, versions


def require_selected(caller, ref):
    if not caller.startswith("resident_"):
        return
    from .hatg import routing
    settings = routing.execute(InstructionCall("GetResidentSettings", {"resident_id": caller}),
                               InstructionContext(caller, "harness")).raise_for_error()
    config = (settings.get("settings") or {}).get("capability_config") or {}
    if not any(item.get("ref") == ref and item.get("enabled", True)
               for item in config.get("tools", [])):
        raise PermissionError(f"Resident application tool disabled: {ref}")


def list_tools(caller):
    # Directory discovery is not business authorization and never registers a tool.
    return {"schema": "tfi.application-tool-catalog.v1",
            "tools": [d.schema() for d in tool_declarations().values()]}


@lru_cache(maxsize=1)
def kernel():
    from management_common.portal_database import portal_database
    from .hatg.kernel import InstructionKernel, OwnerBinding
    from .hatg.store import ExecutionStore
    declarations, handlers, versions = registry()
    def version_of(inv, object_id):
        callback = versions.get(inv.name)
        return callback(inv, object_id) if callback else None
    return InstructionKernel(OwnerBinding("web", handlers=handlers, version_of=version_of),
        store=ExecutionStore(portal_database(schema="web_instructions")),
        catalog={d.name: d for d in declarations.values()}, maintenance_lookup=permission_declaration)


def execute(payload, context: InstructionContext):
    from .hatg.kernel import new_instruction_id
    ref = payload.get("tool", "") if isinstance(payload, dict) else ""
    try:
        if not isinstance(payload, dict) or payload.get("schema") != "tfi.application-tool-call.v1":
            raise ValueError("Expected tfi.application-tool-call.v1")
        if set(payload) - {"schema", "tool", "arguments", "idempotency_key", "expected_version"}:
            raise ValueError("Unknown application tool call fields")
        declaration = tool_declarations().get(ref)
        if declaration is None:
            raise InstructionError("instruction.unknown", f"Unknown application tool: {ref}")
        require_selected(context.caller, ref)
        call = InstructionCall.from_payload({k: v for k, v in payload.items()
                                            if k not in {"schema", "tool"}} | {"instruction": declaration.name})
        receipt = kernel().execute(call, context).to_payload()
    except Exception as exc:
        receipt = {"instruction_id": new_instruction_id("web"), "status": "failed",
                   "replayed": False, "error": normalize_error(exc).to_dict()}
    return {"schema": "tfi.application-tool-result.v1", "tool": ref,
            "call_id": receipt["instruction_id"], "status": receipt["status"],
            "replayed": receipt["replayed"],
            **({"result": receipt.get("result")} if receipt["status"] == "committed"
               else {"error": receipt.get("error")})}
