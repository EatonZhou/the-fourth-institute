"""Follow authenticated Resident input origins to the initiating human."""
from __future__ import annotations

from typing import Any, Mapping

from management_common.principal import validate_message_origin


def _current_input(resident_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any] | None] | None:
    """The Resident's current work record and the input message it runs on."""
    from . import resident_catalog, resident_execution_work, resident_conversation_stream

    try:
        record = resident_catalog.get_catalog().get(resident_id)
    except KeyError:
        return None
    job = resident_execution_work.read(record)
    message = job.get('input_message')
    if not message and job.get('user_message_id'):
        message = resident_conversation_stream.get_repository().message(resident_id, job['user_message_id'])
    return job, message


def _initiator(message: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Who initiated an input message, as its authenticated origin states, or None."""
    origin = (message or {}).get('origin')
    if not isinstance(origin, Mapping):
        return None
    try:
        origin = validate_message_origin(origin)
    except (ValueError, TypeError):
        return None
    return origin.get('initiated_by') or origin['sender']


def human_initiator(resident_id: str, *, message: Mapping[str, Any] | None = None) -> str:
    visited = set()
    current = resident_id
    while current and current not in visited:
        visited.add(current)
        if message is None:
            found = _current_input(current)
            message = found[1] if found else None
        initiator = _initiator(message)
        if initiator is None:
            return ''
        if initiator['kind'] == 'user':
            return str(initiator['id'])
        if initiator['kind'] != 'resident':
            return ''
        current, message = str(initiator['id']), None
    return ''


def execution_initiator(resident_id: str, execution_id: str) -> str:
    """The User whose input started ``execution_id``, when it is the Resident's running execution, or "".

    Only the direct input counts: a Resident forwarding a User's request
    records itself as the initiator, and its current work may already serve
    someone else, so the chain is not followed.
    """
    from . import resident_execution_work

    if not resident_id or not execution_id:
        return ''
    found = _current_input(resident_id)
    if found is None:
        return ''
    job, message = found
    if not resident_execution_work.is_active(job) or not resident_execution_work.identity_matches(job, execution_id):
        return ''
    initiator = _initiator(message)
    return str(initiator['id']) if initiator is not None and initiator['kind'] == 'user' else ''


def notification_recipients(initiator: str) -> list[str]:
    """Follow work provenance, falling back only to a Resident's human creator."""
    from management_common.principal import actor_principal
    from management_common.operation_identity import current_operation_identity
    from . import resident_catalog
    if not initiator:
        return []
    principal = actor_principal(initiator)
    if principal.kind == 'user':
        return [principal.id]
    if principal.kind != 'resident':
        return []
    identity = current_operation_identity()
    if identity and identity.principal == principal and identity.initiated_by.kind == 'user':
        return [identity.initiated_by.id]
    human = human_initiator(principal.id)
    if human:
        return [human]
    try:
        creator = str(resident_catalog.get_catalog().get(principal.id).get('created_by') or '')
    except KeyError:
        return []
    return [creator] if creator and actor_principal(creator).kind == 'user' else []
