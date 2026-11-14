"""Web-owned application operations; deliberately separate from the HATG ISA."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .hatg.contracts import InstructionCall, InstructionContext, InstructionError, normalize_error
from .hatg.declarations import DecisionSlot, InstructionDeclaration, Param


@dataclass(frozen=True)
class ApplicationToolDeclaration:
    ref: str
    name: str
    description: str
    parameters: tuple[Param, ...] = ()
    result: tuple[Param, ...] = ()
    decides: tuple[DecisionSlot, ...] = ()
    mutates: bool = False
    target: str = ""
    one_of: tuple[tuple[str, ...], ...] = ()
    any_of: tuple[str, ...] = ()

    def __post_init__(self):
        import re
        if not re.fullmatch(r"application:[a-z][a-z0-9_.-]*", self.ref):
            raise ValueError(f"Invalid application tool ref: {self.ref}")
        InstructionCall(self.name)
        self.operation()

    def operation(self):
        return InstructionDeclaration(name=self.name, category="application", owner="web",
            description=self.description, parameters=self.parameters, result=self.result,
            decides=self.decides, mutates=self.mutates, target=self.target,
            one_of=self.one_of, any_of=self.any_of)

    def __getattr__(self, name):
        # The shared kernel and permission compiler use the same operation contract.
        return getattr(self.operation(), name)

    def schema(self):
        properties = {}
        for parameter in self.parameters:
            item = parameter.schema()
            item.pop("name", None)
            item.pop("required", None)
            if item.get("type") == "any":
                item.pop("type")
            if "max_length" in item:
                item["maxLength"] = item.pop("max_length")
            properties[parameter.name] = item
        inputs = {"type": "object", "properties": properties, "additionalProperties": False,
                  "required": [p.name for p in self.parameters if p.required]}
        return {"ref": self.ref, "name": self.name, "description": self.description,
                "input_schema": inputs, "parameters": [p.schema() for p in self.parameters],
                "result": [p.schema() for p in self.result], "mutates": self.mutates,
                "target": self.target, "one_of": [list(g) for g in self.one_of], "any_of": list(self.any_of),
                "decides": [slot.to_dict() for slot in self.decides]}


@lru_cache(maxsize=1)
def registry():
    from .portal_features import contributions
    declarations, handlers, versions = {}, {}, {}
    names = set()
    for _feature, module in contributions("resident.tools"):
        for declaration in module.DECLARATIONS:
            if declaration.ref in declarations or declaration.name in names:
                raise RuntimeError(f"Duplicate application tool: {declaration.ref}")
            declarations[declaration.ref] = declaration
            names.add(declaration.name)
            handlers[declaration.name] = module.HANDLERS[declaration.name]
            if getattr(module, "version_of", None):
                versions[declaration.name] = module.version_of
    return declarations, handlers, versions


def permission_catalog():
    """Business A elements keep their names; registration grants no permissions."""
    from .hatg.catalog import instruction_catalog
    catalog = dict(instruction_catalog())
    for declaration in registry()[0].values():
        if declaration.name in catalog:
            raise RuntimeError(f"Application tool also registered in ISA: {declaration.name}")
        catalog[declaration.name] = declaration
    return catalog


def permission_declaration(name):
    try:
        return permission_catalog()[name]
    except KeyError:
        raise InstructionError("instruction.unknown", f"Unknown operation: {name}") from None


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
            "tools": [d.schema() for d in registry()[0].values()]}


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
        declaration = registry()[0].get(ref)
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
