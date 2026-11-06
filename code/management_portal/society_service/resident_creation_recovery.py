"""Registration cancellation and recovery from its durable compensation outbox."""
import hashlib

from management_portal.resources.creation import get_resource_creation, cancel_resource_creation
from .creation_intents import resident_creation_intent, record_intent_cancellation
from . import resident_record_domain
from .resident_lifecycle import LifecycleStepPending


def terminal_failure(failure):
    return failure.get('commit_state') == 'not_committed' and failure.get('retryable') is False


def validate_unregistered_creation(intent, resource_id, row):
    if row['payload'] != intent.request:
        raise ValueError('Creation journal does not match frozen allocation')
    if intent.snapshot.get('completed') or resident_record_domain.find_resident(resource_id) is not None:
        raise ValueError('Creation was registered or recovered')


def cancel_failed_registration(intent, actor, failure):
    """Save disposition before cleanup; the pre-existing outbox retries it."""
    if not terminal_failure(failure):
        raise ValueError('Registration failure is not terminal')
    intent.refresh()
    resource_id = intent.snapshot['record']['resident_id']
    if intent.snapshot.get('completed') or resident_record_domain.find_resident(resource_id) is not None:
        raise ValueError('Creation was registered or recovered')
    intent.snapshot['cancellation_requested'] = {'actor': actor, 'failure': failure}
    intent.save()
    return _cancel(intent, actor, resource_id)


def _cancel(intent, actor, resource_id):
    try:
        get_resource_creation(resource_id)
    except KeyError:
        # No journal means preparation allocated no files or reservations.
        intent.db.write(lambda conn: record_intent_cancellation(intent, actor, conn))
        return dict(resource_id=resource_id, cancelled=True, cleanup_complete=True, errors=[])
    def validate(row, connection):
        validate_unregistered_creation(intent, resource_id, row)
        if row['actor'] != actor:
            raise ValueError('Failure cleanup does not belong to this creating actor')
    return cancel_resource_creation(resource_id, validate=validate,
        record_cancellation=lambda row, conn: record_intent_cancellation(intent, actor, conn))


def recover_registration(step):
    """Resolve the original instruction before consuming its compensation step.

    The step was persisted before initialization. Even failure to save a
    cancellation request leaves this owner responsible for the allocation.
    Unknown outcomes remain pending; neither age nor an idle lease is failure.
    """
    key = step.get('creation_operation_id')
    if not key:
        return  # Historical compensation did not declare this responsibility.
    resource_id, actor = step['record']['resident_id'], step['actor']
    with resident_creation_intent(resource_id) as intent:
        if intent.key != key or intent.snapshot['record']['created_by'] != actor:
            raise ValueError('Registration recovery allocation mismatch')
        if intent.snapshot.get('completed') or resident_record_domain.find_resident(resource_id) is not None:
            return
        requested = intent.snapshot.get('cancellation_requested')
        if intent.snapshot.get('cancellation') or requested:
            if requested and not terminal_failure(requested['failure']):
                raise ValueError('Invalid cancellation disposition')
            result = _cancel(intent, actor, resource_id)
        else:
            instruction_id = step.get('creation_instruction_id')
            if not instruction_id:
                raise LifecycleStepPending('Registration outcome requires original owner recovery')
            expected = 'resident:' + hashlib.sha256((actor + '\0' + step['creation_request_id']).encode()).hexdigest()
            if expected != key:
                raise ValueError('Registration instruction identity mismatch')
            from management_portal.hatg.store import ExecutionStore
            from .state_store import get_lineage_data_store
            store = ExecutionStore(get_lineage_data_store().database, create=False)
            with store.active_execution(instruction_id) as acquired:
                if not acquired:
                    raise LifecycleStepPending('Registration instruction is still active')
                receipt = store.get(instruction_id)
                if (not receipt or receipt['instruction_id'] != instruction_id
                        or receipt['caller'] != actor or receipt['status'] != 'failed'
                        or not terminal_failure(receipt.get('error') or {})):
                    raise LifecycleStepPending('Registration has no definitive failure receipt')
                result = cancel_failed_registration(intent, actor, receipt['error'])
        if not result['cleanup_complete']:
            raise LifecycleStepPending('Registration directory cleanup is pending')
