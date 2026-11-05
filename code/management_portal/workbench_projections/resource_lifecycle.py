"""Resource-owned projection lifetime: release its identities and complete workflows.

Providers enqueue this owner's effects with resource unregistration. Dedicated invocation
bindings name the root resource_id; shared function roles have no such ownership.
"""
from management_portal import resident_handoff, isa_client
from management_portal.hatg.contracts import InstructionError


class ResourceProjectionCleanup:
    """Consume resource deletion's ID outbox without restoring deleted objects.

    Associations remain owned by the existing projection lifecycle. The outbox
    has no directory paths and needs no new schema or background worker.
    """
    def __init__(self):
        from management_portal.resources.registry_store import get_registry_store
        from management_portal.resources.directory_registrations import PROJECTION_RELEASES
        self.store = get_registry_store()
        self.collection = PROJECTION_RELEASES

    def pending(self):
        return sorted(self.store.documents(self.collection).values(),
                      key=lambda item: (item['attempts'], item['resource_id']))

    def run_pending(self, *, resource_ids=None, limit=100):
        outcomes = []
        selected = set(resource_ids) if resource_ids is not None else None
        for entry in self.pending():
            resource_id = entry['resource_id']
            if selected is not None and resource_id not in selected:
                continue
            if len(outcomes) >= limit:
                break
            # Serialize runners per resource, without keeping a data transaction
            # open across ISA calls. A lost response replays the existing owner.
            with self.store.database.read() as connection:
                lock_key = connection.execute('SELECT hashtextextended(%s,0)',
                                              (self.store.database.schema + ':projection-release:' + resource_id,)).fetchone()[0]
            with self.store.database.advisory_lock(lock_key):
                current = self.store.get(self.collection, resource_id)
                if current is None:
                    continue
                try:
                    release_resource(resource_id, actor=current['actor'])
                except Exception as error:
                    current.update(attempts=current['attempts'] + 1, error=str(error))
                    with self.store.writer(self.collection) as writer:
                        writer.put(self.collection, resource_id, current)
                    outcomes.append({**current, 'status': 'retry'})
                else:
                    with self.store.writer(self.collection) as writer:
                        writer.delete(self.collection, resource_id)
                    outcomes.append({'resource_id': resource_id, 'status': 'completed'})
        return outcomes


def finish_resource_releases(resource_ids):
    return [{'resource_id': item['resource_id'], 'error': item['error']}
            for item in ResourceProjectionCleanup().run_pending(resource_ids=resource_ids)
            if item['status'] == 'retry']


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
        from management_portal.resources.directory_registrations import is_deleted
        # A Workbench can be removed in the same Project transaction as the
        # discussed resource. Its roles are already absent: requiring a read
        # of that tombstoned app would keep the release outbox pending forever.
        # A denied or unavailable live app still fails and retains the retry.
        if app_id and not is_deleted(app_id):
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
