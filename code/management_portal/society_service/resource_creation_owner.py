"""Parent-authorized cancellation of unpublished Society resource allocations.

Creation owns execution admission and filesystem effects. Society owns the
frozen allocation, its parent and registration check; no original caller replay.
"""
from management_portal.resources.creation import get_resource_creation, cancel_resource_creation, resource_creation_status
from .creation_intents import resident_creation_intent, record_intent_cancellation
from . import resident_record_domain
from contextlib import contextmanager
import hashlib
import json


def resource_creation(lineage_id, resource_id, actor, *, cancel=False, originating_instruction_id=''):
    from management_portal import permission_system
    action = 'CancelResourceCreation' if cancel else 'GetResourceCreation'
    if not permission_system.allows(actor, action, {'lineage_id': lineage_id}):
        raise PermissionError('Parent Lineage permission required')
    if not resource_id.startswith('resident_'):
        raise ValueError('This owner supports Resident creation allocations')
    with resident_creation_intent(resource_id) as intent:
        if intent.request.get('payload', {}).get('lineage_id') != lineage_id:
            raise PermissionError('Creation does not belong to the authorized parent Lineage')
        def validate(row, connection):
            from .resident_creation_recovery import validate_unregistered_creation
            validate_unregistered_creation(intent, resource_id, row)
        if cancel:
            return cancel_resource_creation(resource_id, validate=validate,
                record_cancellation=lambda row, conn: record_intent_cancellation(intent, actor, conn),
                synchronous_completion=(lambda row, conn: _synchronous_completion(
                    intent, row, lineage_id, originating_instruction_id, conn)) if originating_instruction_id else None)
        row = get_resource_creation(resource_id)
        if row['payload'] != intent.request:
            raise ValueError('Creation journal does not match frozen allocation')
        return dict(resource_creation_status(row), lineage_id=lineage_id,
            registered=resident_record_domain.find_resident(resource_id) is not None,
            intent_completed=bool(intent.snapshot.get('completed')),
            cancellation=intent.snapshot.get('cancellation'),
            normal_handler_completion=intent.snapshot.get('normal_handler_completion'))


@contextmanager
def _synchronous_completion(intent, row, lineage_id, instruction_id, connection):
    """Attest only the plain CreateComponent Resident initializer's normal unwind.

    Creation calls this under its row/writer fence; the original intent lock is
    already held. HATG owns both the receipt read and active execution exclusion.
    No original actor is invoked and no execution receipt is rewritten.
    """
    from management_portal.hatg.store import ExecutionStore
    from management_portal.resources.creation import SynchronousCreationCompletion
    from .state_store import get_lineage_data_store
    from .resident_registration_domain import require_synchronous_resident_creation

    require_synchronous_resident_creation(intent.request, intent.snapshot)
    if (not row['artifacts'] or any(a.get('kind') != 'directory' or a.get('git_dependency')
                                   or 'writer' in a for a in row['artifacts'])):
        raise ValueError('Legacy completion requires plain directory artifacts without writer metadata')
    store = ExecutionStore(get_lineage_data_store().database, create=False)
    with store.active_execution(instruction_id) as acquired:
        if not acquired:
            raise ValueError('Originating instruction is still active')
        if (row['completed'] or intent.snapshot.get('completed')
                or resident_record_domain.find_resident(row['resource_id']) is not None):
            raise ValueError('Creation was registered or recovered')
        receipt = store.get(instruction_id)
        if not receipt:
            raise ValueError('Originating instruction receipt unavailable')
        error = receipt.get('error') or {}
        details = error.get('details') or {}
        # Deliberately recognize the domain CAS business failure, not generic
        # failed status, interruption settlement, transport errors or timeouts.
        if (receipt['instruction_id'] != instruction_id or receipt['instruction'] != 'CreateComponent'
                or receipt['scope_kind'] != 'lineage' or receipt['scope_id'] != lineage_id
                or receipt['status'] != 'failed' or error.get('commit_state') != 'not_committed'
                or error.get('code') != 'instruction.conflict' or error.get('retryable') is not False
                or details.get('object_id') != lineage_id or details.get('reason')
                or not isinstance(details.get('expected_version'), int)
                or not isinstance(details.get('current_version'), int)
                or details['expected_version'] == details['current_version']):
            raise ValueError('Originating receipt is not a normal Lineage CAS failure')
        caller = receipt['caller']
        digest = lambda value: hashlib.sha256((caller + '\0' + value).encode()).hexdigest()
        if (intent.key != 'resident:' + digest(instruction_id)
                or row['operation_id'] != 'resource-create:' + digest(intent.key)
                or intent.snapshot['record'].get('created_by') != caller
                or intent.snapshot['record'].get('lineage_id') != lineage_id):
            raise ValueError('Originating instruction does not match frozen creation identity')
        # Durable only in the same transaction as cancellation; never an ended
        # writer. A subsequent cleanup retry uses its own ordinary writer receipt.
        intent.snapshot['normal_handler_completion'] = {
            'contract': 'resident.synchronous_workspace.v1',
            'intent_key': intent.key, 'creation_key': row['operation_id'],
            'receipt': receipt,
        }
        connection.execute(f'UPDATE {intent.table} SET snapshot=%s::jsonb WHERE operation_id=%s',
                           (json.dumps(intent.snapshot), intent.key))
        yield SynchronousCreationCompletion(row['operation_id'], instruction_id)
