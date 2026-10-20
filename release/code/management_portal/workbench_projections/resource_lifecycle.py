"""Resource-owned projection lifetime: release its identities and complete workflows.

Providers notify this owner before deleting their resource. Dedicated invocation
bindings name the root resource_id; shared function roles have no such ownership.
"""
from management_portal import resident_handoff, isa_client
from management_portal.hatg.contracts import InstructionError

def release_resource(resource_id: str, *, actor: str) -> set[str]:
    """A deleted resource owns all users' discussions, for every resource type."""
    entries = resident_handoff.get_portal_ui_state_store().list_namespace(
        resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    selected = {entry['value']['resident_id']: entry['value'] for entry in entries
                if (entry['value'].get('metadata') or {}).get('resource_id') == resource_id
                and entry['value'].get('resident_id')}
    for entry in entries:
        value = entry['value']
        if (value.get('resident_id') in selected
                and (value.get('metadata') or {}).get('resource_id') != resource_id):
            raise ValueError('Discussion Resident is still bound to another resource')
    for rid, value in selected.items():
        # Membership may have moved since the scope was saved. Resolve it now.
        try:
            record = isa_client.call(actor, 'GetResident', {'resident_id': rid})['resident']
            lineage_id = record['lineage_id']
        except InstructionError as exc:
            if exc.code != 'instruction.not_found':
                raise
            lineage_id = value['lineage_id']
        app_id = (value.get('metadata') or {}).get('workbench_id')
        if app_id:
            from .service import get_service
            service = get_service()
            app = service.store.get(app_id, actor)
            role = value['metadata']['workbench_role']
            if any(member == rid and name != role for binding in app['bindings'].values()
                   for name, member in binding.get('residents', {}).items()):
                raise ValueError('Discussion Resident is still bound to another workbench role')
            roles = {role: rid}
            service.release_roles(app, actor, roles)
        # Also replays interrupted deletion once a workbench binding is gone.
        resident_handoff.release_resident(actor=actor, resident_id=rid, lineage_id=lineage_id)
    return set(selected)
