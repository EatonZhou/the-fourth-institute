"""Directory registrations that prevent explicit Workspace removal.

Read authoritative registrations, never the eventually consistent search index.
The caller holds the Workspace lock and keeps these locks through file removal.
"""
from pathlib import Path

from management_common.instance_locations import current_instance, located_path
from management_common.json_columns import json_value


def directory_dependencies(folder, connection):
    from .registry_store import get_registry_store
    registry = get_registry_store()
    registry._ensure()
    table = f'"{registry.database.schema}".registry_documents'
    # SHARE conflicts with registration/move writes, and remains held until
    # the enclosing retirement transaction commits or rolls back.
    connection.execute(f'LOCK TABLE {table} IN SHARE MODE')
    root = Path(folder)
    blocked = set()

    def check(resource_id, path):
        if not path:
            return
        target = Path(located_path(path)).resolve(strict=False)
        if target == root or root in target.parents:
            blocked.add(resource_id)

    rows = connection.execute(
        f"SELECT collection,document_id,document_json FROM {table} "
        "WHERE collection=ANY(%s)", (['repo', 'article', 'project_registry'],),
    ).fetchall()
    for row in rows:
        document = json_value(row['document_json'])
        if document.get('deleted_at') or document.get('status') == 'deleted':
            continue
        rid = row['document_id']
        if row['collection'] == 'repo':
            check(rid, document.get('canonical_checkout'))
            for checkout in document.get('checkouts') or []:
                check(rid, checkout.get('path'))
        elif row['collection'] == 'article':
            check(rid, document.get('canonical_root'))
            for location in document.get('locations') or []:
                check(rid, location.get('path'))
        else:
            for project in document.get('projects') or []:
                # Project IDs define their directory; old relative path hints
                # are not a second authority for choosing a folder.
                check(project['id'], str(current_instance().area('projects') / project['id']))
    return blocked
