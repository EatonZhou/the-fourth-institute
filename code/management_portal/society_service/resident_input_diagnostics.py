"""Authorize a lifecycle read through an input, never through a supplied execution ID.

This owner port only resolves identities. Execution owns the diagnostic facts and
must check the immutable request against this association before projecting them.
"""
from __future__ import annotations

from management_portal import permission_system
from . import family_facade, resident_input_scope, resident_record_domain


def resolve_input_execution(*, actor: str, resident_id: str, message_id: str) -> dict:
    from . import resident_protocol

    if not actor or not resident_id or not message_id:
        raise ValueError("Input diagnostics requires actor, resident_id and message_id")
    arguments = {"resident_id": resident_id, "message_id": message_id}
    if not permission_system.allows(actor, "GetInputState", arguments):
        raise PermissionError("Input diagnostics requires input read access")
    # Also enforces scoped-input ownership and the actual conversation boundary.
    # Diagnostics is deliberately not requested here, so this cannot recurse.
    state = resident_protocol.get_resident_input_state(resident_id, actor, message_id)
    record = resident_record_domain.find_resident_for_admission(resident_id)
    if not record or record.get("deleted_at"):
        # A deleted Resident's sender-fate receipt is not a runtime audit grant.
        return {"available": False, "reason": "resident_unavailable"}

    holder_id, holder_message = resident_id, message_id
    if family_facade.is_facade(record):
        _, (holder_id, holder_message) = family_facade.relay_holder(resident_id, message_id)
        if (holder_id, holder_message) != (resident_id, message_id):
            # A relay cannot override an isolated input's explicit caller scope.
            resident_input_scope.permits(holder_id, actor, holder_message)
            holder = resident_record_domain.find_resident_for_admission(holder_id)
            if not holder or holder.get("deleted_at"):
                return {"available": False, "reason": "resident_unavailable"}
            # Resolve again after following the current, persisted relay. This
            # does not rely on an earlier facade snapshot during reassignment.
            state = resident_protocol._stored_resident_input_state(holder_id, holder_message)

    execution_id = str((state.get("work") or {}).get("execution_id") or "")
    if not execution_id:
        return {"available": False, "reason": "execution_not_associated"}
    group = str(state.get("grouped_into_message_id") or "")
    if group:
        from . import resident_conversation_stream

        own = resident_conversation_stream.get_repository().input_state(holder_id, holder_message)
        if str((own.get("work") or {}).get("execution_id") or "") == execution_id:
            # A terminal input may retain its own execution after grouping.
            group = ""
        else:
            resident_input_scope.permits(holder_id, actor, group)
    return {
        "available": True,
        "resident_id": holder_id,
        "message_id": holder_message,
        "execution_input_id": group or holder_message,
        "execution_id": execution_id,
    }


def read_input_diagnostics(*, actor: str, resident_id: str, message_id: str) -> dict:
    from . import execution_client
    from .state_store import get_lineage_data_store

    # The Execution RPC resolves and authorizes the association independently.
    # No original execution actor or external execution ID crosses this port.
    result = execution_client.service().input_diagnostics(
        actor=actor, resident_id=resident_id, message_id=message_id,
    )
    if result.get('available') and all(result.get(key) for key in ('resident_id', 'message_id', 'execution_id')):
        result = {**result, 'submission': get_lineage_data_store().executions.input_execution_diagnostics(
            result['resident_id'], result['message_id'], result['execution_id'],
        )}
        if result['submission'].get('current_job_matches'):
            from . import family_quota, resident_execution_work

            record = resident_record_domain.find_resident(result['resident_id'])
            if record and not record.get('deleted_at'):
                result['reserve_admission'] = family_quota.admission_diagnostics(record)
                result['continuation_running'] = resident_execution_work.continuation_running(record)
    return result
