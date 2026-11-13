"""Project domain records shared by authoritative reads and index projection."""
from typing import Any

from management_common.text_values import stripped_text
from management_portal import project_registry
from management_portal.resource_index_records import resource_record


def index_record(item: dict[str, Any]) -> dict[str, Any]:
    if isinstance(item.get('project'), dict):
        item = {**item['project'], 'resource_id': item.get('resource_id')}
    project_id = stripped_text(item.get('resource_id') or item.get('id'))
    return resource_record(
        resource_id=project_id, resource_type='project', title=project_id,
        summary=item.get('description'), adapter='project', facets=['project'],
        attributes={
            'status': stripped_text(item.get('status')),
            'project_type': stripped_text(item.get('project_type')), 'path': stripped_text(item.get('path')),
            'ddl': stripped_text(item.get('ddl')), 'tags': list(item.get('tags') or []),
        },
        search_terms=[item.get('status'), item.get('path'), *(item.get('tags') or [])],
    )


def authoritative_record(resource_id: str) -> dict[str, Any] | None:
    item = project_registry.project(resource_id)
    return index_record(item) if item is not None else None


AUTHORITATIVE_RECORDS = {'project': authoritative_record}
INDEX_FORMATTERS = {'project': index_record}
