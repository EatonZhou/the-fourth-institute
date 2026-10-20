"""Internal Resource owner maintenance for an unverifiable Resident replay memo.

This is not a delete API or proof recovery. Callers must already be authorized
for maintenance, supply an exact inspected preimage, and retain the private
archive. Typed deletion facts and public history are never modified here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from management_common.portal_database import portal_database
from management_common.file_permissions import set_created_file_permissions
from . import registry_store
from .collaboration_registry import CollaborationDescriptorStore
from .domain_projection_evidence import SOURCE_COLLECTION, EVIDENCE_COLLECTION, SOURCE_SCHEMA

QUARANTINE_COLLECTION = 'domain_tombstone_quarantines'


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _instant(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)


def _row(row):
    if row is None:
        return None
    return {**dict(row), 'sha256': _digest(row['raw'].encode())}


def _inspect(store, connection, resource_id, *, locked=False):
    if not re.fullmatch(r'resident_[0-9a-f]{12}', resource_id):
        raise ValueError('Maintenance only supports an explicit Resident identity')
    authority = portal_database(schema='lineage')
    if authority.dsn != store.database.dsn:
        raise ValueError('Typed authority must share the registry transaction database')
    schema = store.database.schema
    suffix = ' FOR SHARE NOWAIT' if locked else ''
    result = {'resource_id': resource_id}
    for label, collection in [('source', SOURCE_COLLECTION), ('public', 'resident'),
                              ('evidence', EVIDENCE_COLLECTION), ('quarantine', QUARANTINE_COLLECTION)]:
        result[label] = _row(connection.execute(
            f'SELECT revision,xmin::text AS xmin,document_json::text AS raw '
            f'FROM "{schema}".registry_documents WHERE collection=%s AND document_id=%s' + suffix,
            (collection, resource_id)).fetchone())
    # Read the authoritative typed table in the same transaction, without
    # initializing or mutating the Society domain owner.
    result['typed'] = _row(connection.execute(
        f'SELECT xmin::text AS xmin,to_jsonb(r)::text AS raw FROM "{authority.schema}".residents r '
        'WHERE resident_id=%s' + suffix, (resource_id,)).fetchone())
    result['retired'] = bool(connection.execute(
        f'SELECT resource_id FROM "{schema}".retired_identities WHERE resource_id=%s', (resource_id,)).fetchone())
    return result


def inspect_resident_memo(store, resource_id):
    """Read-only preflight; the caller initializes its owners separately."""
    with store.database.snapshot() as connection:
        return _inspect(store, connection, resource_id)


def _archive(path, document):
    path = Path(path)
    if path.parent.is_symlink() or (os.name != 'nt' and path.parent.stat().st_mode & 0o077):
        raise ValueError('Archive requires a private existing directory')
    data = _bytes(document) + b'\n'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        set_created_file_permissions(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt':
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return _digest(data)


def quarantine_resident_memo(store, resource_id, *, expected, archive_path, actor, reason):
    """Fence one proven deleted identity and quarantine its invalid memo.

    Revisions and notifications follow the registry owner transaction. Any
    mismatch or concurrent writer aborts the complete operation. A successful
    retry recognizes its maintenance receipt, not an invented old proof.
    """
    if not actor or not reason or expected.get('resource_id') != resource_id:
        raise ValueError('Maintenance requires actor, reason and exact target')
    request_sha = _digest(_bytes(expected))
    collections = ('resident', SOURCE_COLLECTION, EVIDENCE_COLLECTION, QUARANTINE_COLLECTION)
    with store.writer(*collections) as writer:
        connection = writer.connection
        if connection.execute('SHOW transaction_isolation').fetchone()['transaction_isolation'] != 'read committed':
            raise ValueError('Maintenance requires a fresh read-committed transaction')
        acquired = connection.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired',
            (store.database.schema + ':retired-identity:' + resource_id,)).fetchone()['acquired']
        if not acquired:
            raise ValueError('Concurrent identity writer; repeat the complete preflight')
        current = _inspect(store, connection, resource_id, locked=True)
        prior = current['quarantine']
        if prior:
            receipt = json.loads(prior['raw'])
            if (receipt.get('request_sha256') == request_sha and current['source'] is None
                    and current['retired'] and current['evidence'] == expected['evidence']
                    and all(current[k] == expected[k] for k in ('public', 'typed'))):
                return {**receipt, 'changed': False}
            raise ValueError('CAS conflict with existing maintenance receipt')
        if current != expected:
            raise ValueError('CAS preimage changed')
        if not current['source'] or not current['public'] or not current['typed'] or current['evidence']:
            raise ValueError('Require source, public and typed deletion, without an authenticated receipt')
        public = CollaborationDescriptorStore(registry=store).normalize(json.loads(current['public']['raw']), expected_id=resource_id)
        typed = json.loads(current['typed']['raw'])
        source = json.loads(current['source']['raw'])
        if (public['status'] != 'deleted' or not public.get('deleted_at')
                or not typed.get('deleted_at') or not typed.get('deleted_by')
                or _instant(public['deleted_at']) != _instant(typed['deleted_at'])
                or typed.get('resident_id') != resource_id):
            raise ValueError('Public and typed deletion facts do not agree')
        if (source.get('schema') == SOURCE_SCHEMA
                or not str(source.get('schema', '')).endswith('.domain-tombstone-source.v2')
                or source.get('resource_id') != resource_id
                or _instant(source.get('deleted_at')) != _instant(public['deleted_at'])):
            raise ValueError('Only an exact obsolete same-version replay memo can be quarantined')
        receipt = dict(schema='tfi.domain-tombstone-quarantine.v1', resource_id=resource_id,
                       actor=actor, reason=reason, maintained_at=datetime.now(timezone.utc).isoformat(),
                       request_sha256=request_sha, proof_recovered=False)
        archive_sha = _archive(archive_path, {'maintenance': receipt, 'before': current})
        receipt['archive_sha256'] = archive_sha
        connection.execute(f'INSERT INTO "{store.database.schema}".retired_identities(resource_id) '
                           'VALUES (%s) ON CONFLICT DO NOTHING', (resource_id,))
        writer.put(QUARANTINE_COLLECTION, resource_id, receipt)
        if not writer.delete(SOURCE_COLLECTION, resource_id):
            raise ValueError('CAS source disappeared')
        after = _inspect(store, connection, resource_id)
        if any(after[key] != current[key] for key in ('public', 'typed', 'evidence')):
            raise ValueError('Protected deletion facts changed')
        store.database.after_commit(lambda: registry_store.announce_registry_changes(SOURCE_COLLECTION, {resource_id: 'deleted'}))
        store.database.after_commit(lambda: registry_store.announce_registry_changes(QUARANTINE_COLLECTION, {resource_id: 'created'}))
    return {**receipt, 'changed': True}
