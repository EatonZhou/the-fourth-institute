"""The HATG ISA instruction catalog: the declarations of all instruction categories.

The core categories are declared here; an installed feature adds its own
instructions and categories through ``isa.instructions`` (``portal_features.py``).
"""
from __future__ import annotations

from functools import lru_cache
from dataclasses import replace
from ..lineage_versions import DEFINITION_MUTATIONS, VERSION_DESCRIPTION

from ..declarations import CATEGORIES, InstructionDeclaration, merge
from . import (
    conversation, environment, family, family_internal, function_block, isa, lineage_definition,
    lineage_run, lineage_state, mailbox, organization, permission, resident, resident_execution,
    resource, user, script_run, template,
)


def feature_instructions() -> tuple:
    """The ISA modules of the installed features: ``DECLARATIONS``, ``HANDLERS``, ``CATEGORIES``."""
    from management_portal import portal_features

    return tuple(module for _feature, module in portal_features.contributions("isa.instructions"))


@lru_cache(maxsize=1)
def categories() -> dict[str, str]:
    """Category -> title: the core categories, then those the installed features add."""
    found = dict(CATEGORIES)
    for module in feature_instructions():
        for name, title in getattr(module, "CATEGORIES", {}).items():
            if found.setdefault(name, title) != title:
                raise RuntimeError(f"指令类别 {name} 有两个标题：{found[name]}、{title}")
    return found


@lru_cache(maxsize=1)
def instruction_catalog() -> dict[str, InstructionDeclaration]:
    catalog = merge(
        resource.DECLARATIONS,
        user.DECLARATIONS,
        resident.DECLARATIONS,
        conversation.DECLARATIONS,
        mailbox.DECLARATIONS,
        resident_execution.DECLARATIONS,
        lineage_definition.DECLARATIONS,
        lineage_state.DECLARATIONS,
        lineage_run.DECLARATIONS,
        script_run.DECLARATIONS,
        function_block.DECLARATIONS,
        family.DECLARATIONS,
        family_internal.DECLARATIONS,
        template.DECLARATIONS,
        organization.DECLARATIONS,
        permission.DECLARATIONS,
        environment.DECLARATIONS,
        isa.DECLARATIONS,
        *(module.DECLARATIONS for module in feature_instructions()),
    )
    names = categories()
    unknown = sorted(f"{name}（{item.category}）" for name, item in catalog.items() if item.category not in names)
    if unknown:
        raise RuntimeError("指令使用未知类别：" + "、".join(unknown))
    listing = catalog["ListInstructions"]
    catalog["ListInstructions"] = replace(listing, parameters=tuple(
        replace(parameter, enum=tuple(names)) if parameter.name == "category" else parameter
        for parameter in listing.parameters
    ))
    return {name: replace(item, description=item.description + VERSION_DESCRIPTION)
            if name in DEFINITION_MUTATIONS else item for name, item in catalog.items()}



def declaration(name: str) -> InstructionDeclaration:
    from ..contracts import InstructionError

    try:
        return instruction_catalog()[name]
    except KeyError:
        raise InstructionError("instruction.unknown", f"未登记的指令：{name}") from None
