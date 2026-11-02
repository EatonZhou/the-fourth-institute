"""Authenticated resource creation recovery surface."""
from ..resource_creation_owner import resource_creation


def get_creation(inv):
    return {'creation': resource_creation(inv.arguments['lineage_id'], inv.arguments['resource_id'], inv.caller)}


def cancel_creation(inv):
    return {'creation': resource_creation(inv.arguments['lineage_id'], inv.arguments['resource_id'], inv.caller,
        cancel=True, originating_instruction_id=inv.arguments.get('originating_instruction_id', ''))}


HANDLERS = {'GetResourceCreation': get_creation, 'CancelResourceCreation': cancel_creation}
