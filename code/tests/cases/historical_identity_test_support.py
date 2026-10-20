"""Publish canonical identity before deletion."""
from copy import deepcopy
from pathlib import Path

from management_portal.society_service.lineage_catalog import LineageCatalog
from tests.cases.test_lineage_catalogs import _record as legacy_fixture_record, _lineage_definition


def deleted_identity(state, record, *, deleted_at, deleted_by='CASE-A'):
    resident_id = record['resident_id']
    lineage_id = record['lineage_id']
    created_lineage = False
    try:
        state.lineage_definition(lineage_id)
    except KeyError:
        LineageCatalog(state).upsert(_lineage_definition(lineage_id=lineage_id))
        created_lineage = True
    value = {**_record(resident_id, lineage_id=lineage_id), **deepcopy(record)}
    value.pop('deleted_at', None)
    value.pop('deleted_by', None)
    value.pop('lineage_membership', None)
    state.put_resident_record(resident_id, value)
    state.remove_residents([{'resident_id': resident_id, 'deleted_at': deleted_at, 'deleted_by': deleted_by}])
    if created_lineage:
        state.remove_lineage(lineage_id, deleted_by="CASE-A")
    return state.resident_record(resident_id, include_deleted=True)


def _record(resident_id, *, lineage_id=None):
    kwargs = {} if lineage_id is None else {"lineage_id": lineage_id}
    record = legacy_fixture_record(resident_id, **kwargs)
    record.pop("lineage_membership", None)
    record["backend"] = "codex"
    from management_portal.society_service.lineage_storage import canonical_lineage_folder
    root = canonical_lineage_folder(record["lineage_id"])
    own_path = str(root / "residents" / resident_id / "workspace")
    record["workspace_path"] = str(root)
    record["default_cwd"] = own_path
    record["owned_workspace"]["path"] = own_path
    return record
