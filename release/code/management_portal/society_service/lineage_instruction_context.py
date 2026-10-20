"""Trusted ISA context shared by explicit Effects and Resident Invoke sugar."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Iterator

from management_common.operation_identity import operation_identity_scope
from management_common.principal import OperationIdentity
from management_portal.hatg.contracts import InstructionContext
from management_portal.portal_access_control import get_portal_access_store
from . import lineage_primitives


_EVENT_IDENTITY: ContextVar[OperationIdentity | None] = ContextVar("lineage_event_identity", default=None)


def current_event_identity() -> OperationIdentity | None:
    """Identity installed by an actual event boundary, never an ambient caller."""
    return _EVENT_IDENTITY.get()


def _require_live_users(identity: OperationIdentity) -> None:
    """Disabled Users cannot activate or replay even an unprotected instruction."""
    for principal in {identity.principal, identity.initiated_by}:
        if principal.kind == "user":
            access = get_portal_access_store()
            if not access.is_configured(principal.id) or not access.is_active(principal.id):
                raise PermissionError(f"Event User is unavailable or inactive: {principal.id}")


@contextmanager
def automatic_scope(actor: str) -> Iterator[None]:
    """A scheduler observation starts fresh, never borrowing its worker's scope."""
    identity = OperationIdentity.for_actor(actor)
    if identity.principal.kind == "service" and identity.principal.id != "portal":
        raise PermissionError("Automatic Lineage observations require the Portal service")
    _require_live_users(identity)
    token = _EVENT_IDENTITY.set(None)
    try:
        with operation_identity_scope(identity):
            yield
    finally:
        _EVENT_IDENTITY.reset(token)


@contextmanager
def event_scope(event: lineage_primitives.LineageEvent, actor: str) -> Iterator[None]:
    """Use the stored event identity."""
    if not event.identity:
        raise PermissionError("Event has no verified identity; explicit reissue is required")
    identity = OperationIdentity.from_dict(event.identity)
    if identity.principal.id != actor:
        raise PermissionError("Event identity disagrees with its dispatch principal")
    _require_live_users(identity)
    token = _EVENT_IDENTITY.set(identity)
    try:
        with operation_identity_scope(identity):
            yield
    finally:
        _EVENT_IDENTITY.reset(token)


def event_context(event: lineage_primitives.LineageEvent, actor: str) -> InstructionContext:
    """Attribute graph instructions to their initiator and current Trigger Path."""
    if not event.identity:
        raise PermissionError("Event has no verified identity; explicit reissue is required")
    identity = OperationIdentity.from_dict(event.identity)
    # Preserve a human delegation and service-forwarded provenance. A Resident
    # continuation without a User instead acts as the actual Resident.
    caller = identity.initiated_by
    if caller.kind != "user" and identity.principal.kind == "resident":
        caller = identity.principal
    if caller.kind == "service":
        # Explicit application Events keep the authenticated Portal service
        # principal. Only its own stored identity may retain service authority;
        # a derived User/Resident event cannot acquire service authority.
        if (identity.initiated_by.id != "portal" or actor != "portal"
                or identity.principal != identity.initiated_by):
            raise PermissionError("SocietyService Events require their own Portal identity")
    _require_live_users(identity)
    return InstructionContext(caller=caller.id, entry="lineage",
                              trigger_path_id=str(event.payload.get("trigger_path_id") or ""))


def resident_context(lineage_id: str, event: lineage_primitives.LineageEvent, actor: str,
                     resident_id: str, *, sender: str = "") -> InstructionContext:
    context = event_context(event, actor)
    if sender:
        context = replace(context, caller=sender)
    return context
