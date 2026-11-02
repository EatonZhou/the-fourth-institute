"""Offline resource directory migration, called with the maintenance transaction.

Stop clients before invoking. No startup ALTER, connection creation, automatic
filesystem traversal, or deletion is performed. The caller supplies the existing
registry schema, index schema and any disk-only inventory missed by that index.
Historical provenance defaults to adopted; created requires explicit evidence.
"""
from pathlib import Path
import os
import json

from psycopg import sql
from psycopg.types.json import Jsonb


def migrate(connection, *, registry_schema, index_schema=None, inventory=(), created_paths=(), dry_run=False):
    from management_common.instance_locations import located_path, stored_location

    def canonical(path):
        return str(Path(located_path(str(path))).resolve(strict=False))

    proven = {canonical(path) for path in created_paths}

    def entry(path, owner, previous=None):
        previous = previous or {}
        origin = previous.get('origin')
        if origin not in {'created', 'adopted'}:
            origin = 'created' if canonical(path) in proven else 'adopted'
        return {**previous, 'path': stored_location(located_path(str(path))), 'origin': origin, 'owner_id': owner}

    table = sql.Identifier(registry_schema, 'registry_documents')
    changed = []

    def put(collection, resource_id, document):
        if dry_run:
            changed.append(resource_id)
            return
        revision = connection.execute(sql.SQL('SELECT nextval(%s) AS revision'),
                                      (sql.Identifier(registry_schema, 'registry_revision_seq').as_string(),)).fetchone()['revision']
        connection.execute(sql.SQL('INSERT INTO {} (collection,document_id,revision,document_json) VALUES (%s,%s,%s,%s) '
                                   'ON CONFLICT(collection,document_id) DO UPDATE SET revision=EXCLUDED.revision,document_json=EXCLUDED.document_json').format(table),
                           (collection, resource_id, revision, Jsonb(document)))
        connection.execute(sql.SQL('INSERT INTO {} (collection,revision) VALUES (%s,%s) ON CONFLICT(collection) DO UPDATE SET revision=EXCLUDED.revision')
                           .format(sql.Identifier(registry_schema, 'registry_collections')), (collection, revision))
        changed.append(resource_id)

    rows = connection.execute(sql.SQL('SELECT collection,document_id,document_json FROM {} WHERE collection=ANY(%s)').format(table),
                              (['repo', 'article', 'paper', 'knowledge'],)).fetchall()
    existing = {(row['collection'], row['document_id']) for row in rows}
    retired = {row['resource_id'] for row in connection.execute(sql.SQL('SELECT resource_id FROM {}').format(
        sql.Identifier(registry_schema, 'deleted_identities'))).fetchall()}
    for row in rows:
        stored_document = json.loads(row['document_json']) if isinstance(row['document_json'], str) else row['document_json']
        collection, resource_id, document = row['collection'], row['document_id'], dict(stored_document)
        field = {'repo': 'checkouts', 'article': 'roots'}.get(collection, 'directories')
        entries = [entry(item['path'], resource_id, item) for item in document.get(field) or []]
        canonical_path = document.get({'repo': 'canonical_checkout', 'article': 'canonical_root'}.get(collection, ''))
        if canonical_path and canonical(canonical_path) not in {canonical(item['path']) for item in entries}:
            entries.append(entry(canonical_path, resource_id, {'origin': document.get('origin')}))
        document[field] = entries
        if field == 'directories' and entries:
            document['directory'] = {key: entries[0][key] for key in ('origin', 'owner_id')}
        if document != stored_document:
            put(collection, resource_id, document)

    candidates = list(inventory)
    if index_schema:
        indexed = connection.execute(sql.SQL('SELECT record_json FROM {}').format(
            sql.Identifier(index_schema, 'resource_index_records'))).fetchall()
        for row in indexed:
            record = json.loads(row['record_json']) if isinstance(row['record_json'], str) else row['record_json']
            kind = record.get('resource_type')
            attrs, source = record.get('attributes') or {}, record.get('source') or {}
            resource_id = record['resource_id']
            if resource_id in retired or attrs.get('status') in {'deleted', 'retired'} or attrs.get('deleted_at'):
                continue
            if kind == 'paper':
                path = attrs.get('_store_root') or attrs.get('folder') or str(Path(source['path']).parent)
                candidates.append({'collection': 'paper', 'resource_id': resource_id, 'paths': [path]})
            elif 'knowledge' in (record.get('facets') or []) and kind != 'article':
                path = Path(source['path'])
                root = Path(attrs.get('content_root') or path.parent)
                paths = [path] if attrs.get('single_file') else [root]
                if kind != 'skill' and root.parent.name == 'knowledge_base':
                    paths = [path, root / resource_id]
                candidates.append({'collection': 'knowledge', 'resource_id': resource_id, 'paths': paths})
    for item in candidates:
        collection, resource_id = item['collection'], item['resource_id']
        if collection not in {'paper', 'knowledge'}:
            raise ValueError('Inventory must belong to Paper or Knowledge owner')
        if (collection, resource_id) in existing or resource_id in retired:
            continue
        entries = [entry(path, resource_id) for path in item['paths']]
        if not entries:
            raise ValueError('Directory inventory requires paths')
        put(collection, resource_id, {'id': resource_id, 'directories': entries,
            'directory': {key: entries[0][key] for key in ('origin', 'owner_id')}})
        existing.add((collection, resource_id))
    return changed


def inventory_legacy_cleanup_paths(*, paper_roots=(), knowledge_roots=(), skill_roots=()):
    """Report caller-selected legacy locations and lstat bytes; never follow links or delete."""
    paths = [Path(root) / '_trash' for root in paper_roots]
    paths += [Path(root) / '.trash' for root in (*knowledge_roots, *skill_roots)]
    report = []
    for path in dict.fromkeys(paths):
        size, count = 0, 0
        errors = []
        if path.is_symlink() or path.is_file():
            size, count = path.lstat().st_size, 1
        elif path.is_dir():
            def onerror(error):
                errors.append(str(error))
            for root, dirs, files in os.walk(path, followlinks=False, onerror=onerror):
                for name in [*dirs, *files]:
                    child = Path(root) / name
                    if child.is_symlink() or child.is_file():
                        try:
                            size += child.lstat().st_size
                            count += 1
                        except OSError as error:
                            errors.append(str(error))
        report.append({'path': str(path), 'exists': path.exists() or path.is_symlink(),
                       'bytes': size, 'entries': count, 'errors': errors})
    return report
