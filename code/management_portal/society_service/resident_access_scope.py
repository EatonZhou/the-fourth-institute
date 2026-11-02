"""The actual instruction slot for a private resource, without changing the caller."""
from contextlib import contextmanager
from contextvars import ContextVar

_CALL = ContextVar("society_access_instruction", default=None)


@contextmanager
def instruction_scope(inv):
    token = _CALL.set((inv.declaration.name, inv.call.arguments))
    try:
        yield
    finally:
        _CALL.reset(token)


def action_for(resource_id, fallback):
    call = _CALL.get()
    if call is None:
        return fallback
    from management_portal import permission_system
    scheme, _engine, _instance = permission_system.active().current()
    actions = []
    for slot in scheme.slots(call[0]):
        value = call[1]
        for part in slot.argument.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        values = value if isinstance(value, (list, tuple)) else [value]
        if not slot.via and not slot.place and resource_id in values:
            actions.append(slot.element)
    # A multi-slot call must not accidentally acquire a broader action.
    return actions[0] if len(set(actions)) == 1 else fallback


def action_for_parameter(instruction, parameter):
    from management_portal import permission_system
    scheme, _engine, _instance = permission_system.active().current()
    actions = {slot.element for slot in scheme.slots(instruction) if slot.argument == parameter and not slot.via}
    return next(iter(actions)) if len(actions) == 1 else instruction
