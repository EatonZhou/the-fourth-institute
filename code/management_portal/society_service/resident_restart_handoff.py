"""Move an unstarted Execution back to the same Resident input's preparation.

The restart owns the durable intent. Execution owns the atomic no-claim proof.
Only a matching receipt allows the original input to leave its old execution.
"""
from __future__ import annotations

import copy
import secrets

from . import resident_execution_work
from .domain_ports import execution_port


class ExecutionHandedOff(Exception):
    """An internal execution ended without ending its business input."""


def receipt(snapshot):
    return (snapshot.get('result') or {}).get('unstarted_handoff') or {}


def pending(record, job):
    handoff = ((record.get('session_restart') or {}).get('deferred') or {}).get('handoff') or {}
    return bool(handoff and handoff.get('execution_id') == job.get('execution_id'))


def can_preserve(record, job):
    handoff = ((record.get('session_restart') or {}).get('deferred') or {}).get('handoff') or {}
    return resident_execution_work.can_preserve_for_session_restart(job) or bool(
        handoff.get('phase') == 'withdrawn'
        and handoff.get('execution_id') == job.get('execution_id')
        and handoff.get('message_id') == job.get('user_message_id')
    )


def restore_preparation(record):
    """Called inside the restart's release transaction, after binding promotion.

    Reserving the replacement earlier would give it the source Provider's
    immutable provenance. Failure/cancellation instead reserves on the retained
    source. The store keeps the input links and schedules old execution release.
    """
    from .resident_execution_storage import queue_input
    from . import resident_lifecycle_domain

    deferred = (record.get('session_restart') or {}).get('deferred') or {}
    handoff = deferred.get('handoff') or {}
    if not handoff:
        return
    if handoff.get('phase') != 'withdrawn':
        raise ValueError('排队执行交接回执尚未确认')
    job = resident_execution_work.read(record)
    if (job.get('execution_id') != handoff['execution_id']
            or job.get('user_message_id') != handoff['message_id']):
        raise ValueError('排队输入已变化，不能替换交接执行')
    item = queue_input(job)
    for key in ('actual_sent_prompt', 'prompt_built_at', 'prompt_builder_version', 'runtime_context'):
        item.pop(key, None)
    resident_execution_work.promote_queue_item(
        job, item, resident_execution_work.normalized_queue(job), resident_lifecycle_domain.now_iso(),
    )
    job['execution_id'] = handoff['replacement_execution_id']
    job['intake_state'] = 'closed' if record.get('intake_open') is False else 'open'
    resident_execution_work.write(record, job)
    # Retain the original/replacement association after deferred work is gone.
    record['session_restart']['input_handoff'] = copy.deepcopy(handoff)


def advance(record):
    from . import resident_lifecycle_domain, resident_workspace_domain

    rid = record['resident_id']
    with resident_lifecycle_domain.state_lock(rid):
        current = copy.deepcopy(resident_workspace_domain.find_resident(rid) or {})
        state = current.get('session_restart') or {}
        deferred = state.get('deferred') or {}
        job = resident_execution_work.read(current)
        handoff = deferred.get('handoff')
        if (deferred.get('phase') != 'waiting' or not resident_execution_work.is_active(job)
                or not resident_execution_work.submitted_execution_id(job)
                or (job.get('stop_requested_at') and not handoff)
                or (job.get('operation') or 'turn') != 'turn'):
            return False
        if handoff and handoff.get('phase') == 'withdrawn':
            return can_preserve(current, job)
        if not handoff:
            handoff = {"execution_id": job['execution_id'], "message_id": job['user_message_id'],
                       "replacement_execution_id": 'execution-' + secrets.token_hex(12),
                       "operation_id": state['restart_id'], "binding_id": 'resident:' + rid}
            deferred['handoff'] = handoff
            resident_workspace_domain.save_resident_record(current)
        if handoff['execution_id'] != job['execution_id'] or handoff['message_id'] != job['user_message_id']:
            return False
        actor = resident_execution_work.request_actor(job)
        performer = deferred['actor']
    # A connection loss leaves the exact intent for the next reconciliation.
    result = execution_port.service().withdraw_unstarted(
        handoff['execution_id'], actor=actor, performed_by=performer,
        **{key: handoff[key] for key in ('binding_id', 'operation_id', 'replacement_execution_id')},
    )
    with resident_lifecycle_domain.state_lock(rid):
        current = copy.deepcopy(resident_workspace_domain.find_resident(rid) or {})
        deferred = (current.get('session_restart') or {}).get('deferred') or {}
        if deferred.get('handoff') != handoff:
            return False
        job = resident_execution_work.read(current)
        if job.get('execution_id') != handoff['execution_id']:
            return False
        if not result.get('withdrawn'):
            deferred.pop('handoff', None)
            resident_workspace_domain.save_resident_record(current)
            return False
        if any(result.get(key) != handoff[key] for key in
               ('execution_id', 'binding_id', 'operation_id', 'replacement_execution_id')):
            raise ValueError('unstarted handoff receipt does not match the saved intent')
        deferred['handoff'] = {**handoff, 'phase': 'withdrawn'}
        resident_workspace_domain.save_resident_record(current)
    return True
