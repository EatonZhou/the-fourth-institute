"""Resource-owned Workspace creation and durable cleanup progress."""
from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_portal.hatg.contracts import InstructionError, not_found

from .collaboration_registry import descriptor_resource_record
from .workspace_creation_store import WorkspaceCreationStore


def create_workspace(inv, service):
    owner_id = inv.argument('owner_id', '')
    if owner_id:
        owner = service.get(owner_id, actor=inv.caller)
        attributes = owner.get('attributes') or {}
        if attributes.get('status') == 'deleted' or attributes.get('deleted_at'):
            raise not_found('Workspace owner no longer exists')
    creator = WorkspaceCreationStore()
    row = creator.create(path=inv.argument('path'), owner_id=owner_id, actor=inv.caller,
                         operation_id=inv.argument('operation_id'), title=inv.argument('title', ''),
                         origin='created' if inv.name == 'CreateWorkspace' else 'adopted')
    record = descriptor_resource_record(creator.workspaces.descriptor(row))
    record = service._complete_mutation(record, resource_type='workspace', expected_id=row['workspace_id'],
                                       actor=inv.caller, kind='create', wait_for_index=False, index_timeout=0)
    return {'workspace_id': row['workspace_id'], 'resource': record}


def cleanup_status(inv, service):
    rows = get_directory_cleanup_store().status(owner_id=inv.argument('resource_id'))
    if any(row['actor'] != inv.caller for row in rows):
        try:
            service.get(inv.argument('resource_id'), actor=inv.caller)
        except (KeyError, PermissionError):
            raise InstructionError('instruction.forbidden', 'Directory cleanup is not visible to this caller') from None
    return {'items': rows}
