"""Offline conversion of the old installed Articles tree into project areas.

Preview changes only the caller's transaction. Copy preparation retains every
source byte; database apply requires the exact preview and verified target trees.
No instance area is added and no path guard is weakened.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from psycopg import sql
from management_common.location_records import transform
from management_common.portal_database import _database_schema


def stored_json(value):
    # Runtime json_value materializes every area reference. An offline edit must
    # retain the exact stored spelling of unrelated fields, including on Windows.
    return json.loads(value) if isinstance(value, str) else value


def ordinary_path(path):
    """Reject links/junctions, including ancestors, before following a tree."""
    path = Path(path)
    for item in (path, *path.parents):
        if not os.path.lexists(item):
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Article migration refuses links or reparse points: ' + str(item))
    return path


def tree_manifest(root):
    root = ordinary_path(root)
    if not root.is_dir():
        raise ValueError('Article directory is missing: ' + str(root))
    entries = []
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in sorted([*directories, *files]):
            path = ordinary_path(Path(parent) / name)
            info = path.lstat()
            entry = {'path': path.relative_to(root).as_posix()}
            if stat.S_ISDIR(info.st_mode):
                entry['kind'] = 'directory'
            elif stat.S_ISREG(info.st_mode):
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                entry.update(kind='file', size=info.st_size, sha256=digest)
            else:
                raise ValueError('Article migration requires ordinary files: ' + str(path))
            entries.append(entry)
    entries.sort(key=lambda item: item['path'])
    digest = hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {'sha256': digest, 'entries': entries}


def plan(connection, instance):
    table = sql.Identifier(_database_schema('resource_registry'), 'registry_documents')
    rows = connection.execute(sql.SQL('SELECT collection,document_id,document_json FROM {} '
        "WHERE collection IN ('project_registry','article') ORDER BY collection,document_id FOR UPDATE").format(table)).fetchall()
    documents = {(row['collection'], row['document_id']): stored_json(row['document_json']) for row in rows}
    projects = documents.get(('project_registry', 'projects'), {}).get('projects', [])
    project_by_id = {item['id']: item for item in projects}
    if len(project_by_id) != len(projects):
        raise ValueError('Ambiguous project registry')
    result = {'schema': 'tfi.installed-article-migration.v1', 'instance_root': str(instance.root),
              'projects': [], 'articles': []}
    for project in projects:
        pid = project['id']
        if not re.fullmatch(r'[\w.@+-]{1,80}', pid) or pid in {'.', '..'}:
            raise ValueError('Invalid project ID for legacy conversion')
        if project.get('path') != '/Projects/' + pid:
            continue
        target = ordinary_path(instance.area('projects') / pid)
        if not target.is_dir():
            raise ValueError('Legacy project directory is missing: ' + pid)
        result['projects'].append({'project_id': pid, 'source': project['path'],
                                   'reference': instance.reference(target)})
    legacy = instance.root / 'data' / 'share' / 'Articles'
    for (collection, resource_id), document in documents.items():
        if collection != 'article':
            continue
        source = legacy / resource_id
        paths = [item['path'] for item in document.get('roots') or []]
        paths += [document['canonical_root']] if document.get('canonical_root') else []
        if not any(Path(value) == source for value in paths):
            continue
        if not re.fullmatch(r'article_[0-9a-f]{12}', resource_id):
            raise ValueError('Invalid legacy Article identity')
        pids = document.get('project_ids') or []
        if len(pids) != 1 or pids[0] not in project_by_id:
            raise ValueError('Legacy Article requires one registered project: ' + resource_id)
        project = project_by_id[pids[0]]
        project_root = ordinary_path(instance.area('projects') / pids[0])
        reference = instance.reference(project_root)
        if project.get('path') not in ('/Projects/' + pids[0], reference, str(project_root), project_root.as_posix()):
            raise ValueError('Article project directory needs explicit reconciliation: ' + resource_id)
        if not project_root.is_dir():
            raise ValueError('Article project directory is missing: ' + resource_id)
        target = ordinary_path(project_root / 'articles' / resource_id)
        manifest = tree_manifest(source)
        primary = document.get('primary_file')
        if not primary or not any(item['kind'] == 'file' and item['path'] == primary for item in manifest['entries']):
            raise ValueError('Legacy Article primary file is missing: ' + resource_id)
        if os.path.lexists(target) and tree_manifest(target) != manifest:
            raise ValueError('Article target differs from source: ' + resource_id)
        result['articles'].append({'resource_id': resource_id, 'project_id': pids[0],
            'source': str(source), 'target': str(target), 'reference': instance.reference(target), 'manifest': manifest})
    result['projects'].sort(key=lambda item: item['project_id'])
    result['articles'].sort(key=lambda item: item['resource_id'])
    return result


def verify_targets(proposal):
    for item in proposal['articles']:
        if tree_manifest(item['source']) != item['manifest'] or tree_manifest(item['target']) != item['manifest']:
            raise ValueError('Article copy no longer matches the preview: ' + item['resource_id'])


def prepare_copies(proposal):
    """Caller revalidates the database plan and offline state before this call."""
    for item in proposal['articles']:
        source, target = ordinary_path(item['source']), ordinary_path(item['target'])
        if tree_manifest(source) != item['manifest']:
            raise ValueError('Article source changed after preview: ' + item['resource_id'])
        if not os.path.lexists(target):
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_name('.' + target.name + '-migration-' + uuid.uuid4().hex)
            shutil.copytree(source, staging)
            if tree_manifest(staging) != item['manifest']:
                raise ValueError('Article staged copy differs; retained for inspection: ' + str(staging))
            # Never merge into or replace an existing target.
            if os.path.lexists(target):
                raise ValueError('Article target appeared during copy')
            staging.rename(target)
    verify_targets(proposal)


def rewrite(connection, proposal):
    """Only typed resource locations change; IDs, prose and histories remain."""
    from management_portal.resources.registry_store import RegistryWriter
    schema = _database_schema('resource_registry')
    pairs = [(Path(item['source']), item['reference']) for item in proposal['articles']]

    def locate(value):
        path = Path(value)
        for source, reference in pairs:
            if path.is_absolute() and path.is_relative_to(source):
                relative = path.relative_to(source).as_posix()
                return reference + ('' if relative == '.' else '/' + relative)
        return value

    table = sql.Identifier(schema, 'registry_documents')
    rows = connection.execute(sql.SQL('SELECT collection,document_id,document_json FROM {} FOR UPDATE').format(table)).fetchall()
    writer = RegistryWriter(connection, tuple({row['collection'] for row in rows}), schema)
    changed = []
    for row in rows:
        original = stored_json(row['document_json'])
        document = transform(original, locate)
        if (row['collection'], row['document_id']) == ('project_registry', 'projects'):
            by_id = {item['project_id']: item for item in proposal['projects']}
            for project in document.get('projects', []):
                if project['id'] in by_id:
                    project['path'] = by_id[project['id']]['reference']
        if document != original:
            writer.put(row['collection'], row['document_id'], document)
            changed.append([row['collection'], row['document_id']])
    # The resource index is derived but must not serve its former source path
    # between startup and the first complete adapter refresh.
    from management_portal.resources.config import resource_database_schema
    index = sql.Identifier(_database_schema(resource_database_schema()), 'resource_index_records')
    if connection.execute('SELECT to_regclass(%s)', (index.as_string(),)).fetchone()[0]:
        for row in connection.execute(sql.SQL('SELECT ctid::text AS row_id,record_json FROM {} FOR UPDATE').format(index)).fetchall():
            original = stored_json(row['record_json'])
            document = transform(original, locate)
            if document != original:
                connection.execute(sql.SQL('UPDATE {} SET record_json=%s WHERE ctid=%s::tid').format(index),
                                   (json.dumps(document, ensure_ascii=False), row['row_id']))
    return changed
