"""One-time upgrades of resource directory ownership and operation receipts.

Classification fields are retired here, never interpreted by runtime resource
operations. File locations, owners, IDs and payloads are unchanged.
"""
from copy import deepcopy
import json

from psycopg import sql
from psycopg.types.json import Jsonb

from management_common.json_columns import json_value
from management_common.location_records import stored_record


def without_classification(value):
    if isinstance(value, dict):
        return {key: without_classification(item) for key, item in value.items()
                if not (key == 'origin' and item in ('created', 'adopted'))}
    if isinstance(value, list):
        return [without_classification(item) for item in value]
    return value


def upgrade_registry(connection, schema):
    """Enlist in the registry initialization transaction, bump changed revisions."""
    meta = sql.Identifier(schema, 'registry_meta')
    key = 'directory_ownership_v1'
    location_key = 'directory_ownership_locations_v1'
    completed = {row['key'] for row in connection.execute(
        sql.SQL('SELECT key FROM {} WHERE key=ANY(%s)').format(meta), ([key, location_key],)).fetchall()}
    if completed == {key, location_key}:
        return 0
    table = sql.Identifier(schema, 'registry_documents')
    changed = 0
    for row in connection.execute(sql.SQL(
            'SELECT collection,document_id,document_json FROM {} WHERE collection=ANY(%s) FOR UPDATE'
            ).format(table), (['repo', 'article', 'paper', 'knowledge', 'knowledge_moves'],)).fetchall():
        # Migration compares the stored representation. json_value is a runtime
        # reader and expands area references; writing that result via Jsonb used
        # to persist host paths. Also repair records processed by that version.
        raw = row['document_json']
        before = json.loads(raw) if isinstance(raw, str) else raw
        after = stored_record(without_classification(before))
        if before == after:
            continue
        revision = connection.execute('SELECT nextval(%s)',
            (sql.Identifier(schema, 'registry_revision_seq').as_string(),)).fetchone()[0]
        connection.execute(sql.SQL('UPDATE {} SET document_json=%s,revision=%s WHERE collection=%s AND document_id=%s')
            .format(table), (Jsonb(after), revision, row['collection'], row['document_id']))
        connection.execute(sql.SQL('INSERT INTO {}(collection,revision) VALUES (%s,%s) '
            'ON CONFLICT(collection) DO UPDATE SET revision=EXCLUDED.revision')
            .format(sql.Identifier(schema, 'registry_collections')), (row['collection'], revision))
        changed += 1
    for marker in (key, location_key):
        connection.execute(sql.SQL('INSERT INTO {}(key,value) VALUES (%s,%s) '
            'ON CONFLICT(key) DO NOTHING').format(meta), (marker, 'complete'))
    return changed


def upgrade_creations(connection, schema):
    """Retain the rollback distinction between allocating and registering files."""
    meta = sql.Identifier(schema, 'directory_schema_migrations')
    # The caller holds the creation schema's initialization advisory lock.
    connection.execute(sql.SQL('CREATE TABLE IF NOT EXISTS {} (key TEXT PRIMARY KEY)').format(meta))
    key = 'ownership_v1'
    if connection.execute(sql.SQL('SELECT 1 FROM {} WHERE key=%s').format(meta), (key,)).fetchone():
        return 0
    table = sql.Identifier(schema, 'resource_creations')
    changed = 0
    for row in connection.execute(sql.SQL(
            "SELECT operation_id,payload,artifacts FROM {} WHERE payload ? 'origin' "
            "OR artifacts @> '[{{\"kind\":\"adopted\"}}]'::jsonb FOR UPDATE").format(table)).fetchall():
        payload, artifacts = deepcopy(json_value(row['payload'])), deepcopy(json_value(row['artifacts']))
        classification = payload.pop('origin', None)
        if classification in ('created', 'adopted'):
            payload['artifact_kind'] = 'existing_directory' if classification == 'adopted' else 'directory'
        for artifact in artifacts:
            if artifact.get('kind') == 'adopted':
                artifact['kind'] = 'existing_directory'
        connection.execute(sql.SQL('UPDATE {} SET payload=%s,artifacts=%s WHERE operation_id=%s').format(table),
            (Jsonb(payload), Jsonb(artifacts), row['operation_id']))
        changed += 1
    connection.execute(sql.SQL('INSERT INTO {}(key) VALUES (%s)').format(meta), (key,))
    return changed


def upgrade_index(connection):
    """Remove retired directory attributes from the disposable persisted index."""
    key = 'directory_ownership_v1'
    if connection.execute('SELECT 1 FROM resource_index_meta WHERE key=%s', (key,)).fetchone():
        return 0
    changed = 0
    rows = connection.execute('SELECT resource_id,record_json FROM resource_index_records FOR UPDATE').fetchall()
    for row in rows:
        before = json_value(row['record_json'])
        after = deepcopy(before)
        after['attributes'] = without_classification(before.get('attributes') or {})
        if before != after:
            connection.execute('UPDATE resource_index_records SET record_json=%s WHERE resource_id=%s',
                               (json.dumps(after, ensure_ascii=False), row['resource_id']))
            changed += 1
    connection.execute('INSERT INTO resource_index_meta(key,value) VALUES (%s,%s)', (key, 'complete'))
    return changed
