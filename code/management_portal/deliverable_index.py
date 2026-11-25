"""Upper-layer projection of the deliverable owner's current metadata."""
from management_portal.resource_index_records import resource_record
from management_portal.resources.deliverables import COLLECTION
from management_portal.resources.registry_store import get_registry_store


class DeliverableRegistryProjection:
    resource_type = 'deliverable'
    collection = COLLECTION

    def validate(self, resource_id, raw):
        if raw.get('resource_id') != resource_id or raw.get('resource_type') != self.resource_type:
            raise ValueError('deliverable registry identity mismatch')
        return raw

    def project(self, item, *, adapter):
        return resource_record(resource_id=item['resource_id'], resource_type=self.resource_type,
            # Publication can commit before a delayed index refresh. A stale
            # draft projection must never reveal draft text under new PAR facts.
            title=item['title'] if item['status'] != 'draft' else item['resource_id'],
            summary=item['summary'] if item['status'] != 'draft' else '', adapter=adapter, modified_at=item['updated_at'],
            attributes={'project_ids': item['projects'], 'status': item['status'],
                        'created_by': item['created_by'], 'created_for': item['created_for'],
                        'author_id': item['author_id'], 'revision': item['revision']},
            search_terms=[item['author_id'], *item['projects']], url=f"/api/resources/content?address={item['resource_id']}")


REGISTRY_PROJECTION = DeliverableRegistryProjection()


def authoritative_record(resource_id):
    record = get_registry_store().get(COLLECTION, resource_id)
    return REGISTRY_PROJECTION.project(record, adapter='resource_registry') if record else None


AUTHORITATIVE_RECORDS = {'deliverable': authoritative_record}
