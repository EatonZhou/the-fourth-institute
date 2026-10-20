"""Current lifecycle facts for release discovery, without publication ACLs.

All reads use the caller's readonly database snapshot. Index presence alone is
not authority for a domain object; no index repair or source mutation occurs.
"""
from pathlib import Path
from management_common.json_columns import json_value

from management_portal.permission_system.facts import live


def current_records(source, records):
    if not records:
        return {}
    ids = set(records)
    retired = {r['resource_id'] for r in source.rows('resource_registry', 'retired_identities',
                                                   predicates={'resource_id': ids})}
    descriptors, projects = {}, {}
    has_projects = any(r.get('resource_type') == 'project' for r in records.values())
    for row in source.rows('resource_registry', 'registry_documents', predicates={'document_id': ids | ({'projects'} if has_projects else set())}):
        if row['collection'] == 'project_registry' and row['document_id'] == 'projects':
            projects = {p['id']: p for p in json_value(row['document_json']).get('projects', [])}
            continue
        if row['collection'] == records[row['document_id']].get('resource_type'):
            descriptors[row['document_id']] = json_value(row['document_json'])
    # These are the same typed authority rows used by the native domain owners.
    authorities = {'lineage': ('lineage', 'lineages', 'lineage_id'),
                   'resident': ('lineage', 'residents', 'resident_id'),
                   'family': ('lineage', 'families', 'resource_id'),
                   'workspace': ('workspace', 'workspaces', 'workspace_id'),
                   'workbench': ('workbenches', 'apps', 'id')}
    canonical = {}
    for kind, (schema, table, key) in authorities.items():
        selected = {rid for rid, record in records.items() if record.get('resource_type') == kind}
        if selected:
            canonical[kind] = {row[key]: row for row in source.rows(schema, table, predicates={key: selected})}
    result = {}
    for rid, record in records.items():
        kind = record.get('resource_type')
        if rid in retired or not live(record):
            continue
        descriptor = descriptors.get(rid)
        if descriptor is not None and not live(descriptor):
            continue
        if kind == 'project' and (rid not in projects or not live(projects[rid])):
            continue
        if kind in authorities:
            row = canonical[kind].get(rid)
            if row is None or not live(row):
                continue
        else:
            # File-backed resources cease to exist when their registered root
            # disappears; no directory/title guessing or filesystem scan.
            path = record.get('source', {}).get('path')
            if kind == 'repo':
                path = record.get('attributes', {}).get('canonical_checkout') or path
            if path and not Path(path).exists():
                continue
        result[rid] = record
    return result
