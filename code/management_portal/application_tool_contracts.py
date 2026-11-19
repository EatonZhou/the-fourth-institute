"""Installed application tool declarations consumed by permissions and Web."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .hatg.contracts import InstructionCall, InstructionError
from .hatg.declarations import DecisionSlot, InstructionDeclaration, Param
from .portal_features import contributions


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


def tool_declarations() -> dict[str, ApplicationToolDeclaration]:
    by_ref = {}
    names = set()
    # A process can still hold the old feature registry during a rolling load.
    parts = contributions("resident.tool_declarations") or contributions("resident.tools")
    for _feature, module in parts:
        for declaration in module.DECLARATIONS:
            if declaration.ref in by_ref or declaration.name in names:
                raise RuntimeError(f"Duplicate application tool: {declaration.ref}")
            by_ref[declaration.ref] = declaration
            names.add(declaration.name)
    return by_ref


def permission_catalog():
    """Business A elements keep their names; registration grants no permissions."""
    from .hatg.catalog import instruction_catalog
    catalog = dict(instruction_catalog())
    for declaration in tool_declarations().values():
        if declaration.name in catalog:
            raise RuntimeError(f"Application tool also registered in ISA: {declaration.name}")
        catalog[declaration.name] = declaration
    return catalog


def permission_declaration(name):
    try:
        return permission_catalog()[name]
    except KeyError:
        raise InstructionError("instruction.unknown", f"Unknown operation: {name}") from None
