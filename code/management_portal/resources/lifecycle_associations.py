"""Resource-owned proof for correcting a lazy persistent-root association."""
from pathlib import Path

from management_common.directory_paths import location_key
from management_common.resource_lifecycle import LifecycleStore
from management_portal import project_registry
from .registry_store import get_registry_store
from .workspace_store import get_workspace_store


def project_root_owner(path):
    """Only an exact registered Project root, never a child or guessed prefix."""
    target = location_key(Path(path))
    document = get_registry_store().get(project_registry.COLLECTION, project_registry.DOCUMENT_ID) or {}
    for project in document.get('projects', []):
        root = project_registry.runtime_path(project.get('path'))
        if root is not None and location_key(root) == target and project.get('directory') == {'owner_id': project['id']}:
            return project['id']
    return ''


def correct(inv, service):
    resource_id = inv.argument('resource_id')
    service.validate_id(resource_id, 'workspace')
    store = get_workspace_store()
    def validate(connection):
        store.cleanup.lock(connection)
        service.resolve(resource_id, actor=inv.caller)  # Reading is required; deletion authority is not borrowed.
        row = store.rows([resource_id], connection=connection).get(resource_id)
        if not row or row.get('operation_id') or row.get('owner_id') != resource_id:
            raise ValueError('Only a live implicit Workspace registration can be corrected')
        root = Path(row['path'])
        owner = project_root_owner(root)
        if not owner or root.is_symlink() or not root.is_dir():
            raise ValueError('Workspace is not the existing registered Project root')
        return {'kind': 'implicit_project_root_dependency', 'dependency_owner_id': owner,
                'workspace_id': resource_id, 'workspace_created_at': row['created_at'],
                'workspace_updated_at': row['updated_at'], 'workspace_owner_id': row['owner_id'],
                'workspace_operation_id': row.get('operation_id') or ''}
    return LifecycleStore().correct_association(inv.argument('scope_id'), resource_id,
        actor=inv.caller, reason=inv.argument('reason'), validate=validate)
