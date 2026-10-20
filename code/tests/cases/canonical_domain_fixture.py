"""Seed actual domain facts before exercising descriptor publication."""
from management_portal.society_service import resource_client
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_lineage_catalogs import _record, _lineage_definition


def canonical_projection(resource_id, **changes):
    state = get_lineage_data_store()
    if resource_id.startswith("lineage_"):
        record = _lineage_definition(lineage_id=resource_id, **changes)
        state.put_lineage_definition(resource_id, record, {})
        return resource_client._lineage_resource_projection(state.lineage_definition(resource_id))
    lineage_id = changes.pop("lineage_id", "lineage_987654321abc")
    try:
        state.lineage_definition(lineage_id)
    except KeyError:
        state.put_lineage_definition(lineage_id, _lineage_definition(lineage_id=lineage_id), {})
    record = {**_record(resource_id, lineage_id=lineage_id),
              "updated_at": "2026-10-08T00:00:00Z", **changes}
    deleted_at = record.pop('deleted_at', '')
    deleted_by = record.pop('deleted_by', '')
    state.put_resident_record(resource_id, record)
    stored = state.resident_record(resource_id)
    if deleted_at:
        state.remove_residents([{'resident_id': resource_id, 'deleted_at': deleted_at, 'deleted_by': deleted_by}])
        stored.update(deleted_at=deleted_at, deleted_by=deleted_by)
    return resource_client._resident_resource_projection(stored)
