"""Experimental Reader persistence; trusted authentication and attachment resolver required.

No HTTP route or production resolver is installed by this module.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
import math
import re

from psycopg.types.json import Jsonb
from management_common.domain_database import DomainDatabase

DDL = """
CREATE TABLE IF NOT EXISTS reader_annotation_scopes (
 id BIGSERIAL PRIMARY KEY, actor TEXT NOT NULL, paper_id TEXT NOT NULL,
 attachment_id TEXT NOT NULL, attachment_version TEXT NOT NULL,
 revision BIGINT NOT NULL DEFAULT 0,
 UNIQUE(actor,paper_id,attachment_id,attachment_version));
CREATE TABLE IF NOT EXISTS reader_annotations (
 scope_id BIGINT REFERENCES reader_annotation_scopes(id), annotation_id TEXT NOT NULL,
 value JSONB, revision BIGINT NOT NULL, PRIMARY KEY(scope_id,annotation_id));
CREATE TABLE IF NOT EXISTS reader_annotation_operations (
 scope_id BIGINT REFERENCES reader_annotation_scopes(id), operation_id TEXT NOT NULL,
 request_digest TEXT NOT NULL, response JSONB NOT NULL, PRIMARY KEY(scope_id,operation_id));
"""
MAX_BYTES = 256 * 1024
MAX_READ_ROWS = 4096
MAX_READ_BYTES = 4 * 1024 * 1024


def purge_paper_records(connection, paper_id):
    """Called only by the formal Paper deletion transaction after unregistering."""
    for table in ('reader_annotation_operations', 'reader_annotations'):
        connection.execute(f'DELETE FROM {table} WHERE scope_id IN (SELECT id FROM reader_annotation_scopes WHERE paper_id=%s)', (paper_id,))
    connection.execute('DELETE FROM reader_annotation_scopes WHERE paper_id=%s', (paper_id,))


class Conflict(ValueError):
    status = 409


def _geometry(value):
    if value['type'] not in ('highlight', 'note'):
        return
    position = value['position']
    if set(position) - {'pageIndex', 'rects', 'nextPageRects', 'rotation'}:
        raise ValueError('unsupported position fields')
    def rectangles(rects):
        if type(rects) is not list or not 1 <= len(rects) <= 500:
            raise ValueError('invalid rectangles')
        for rect in rects:
            if type(rect) is not list or len(rect) != 4 or any(type(n) not in (int, float) or abs(n) > 1e7 or not math.isfinite(n) for n in rect):
                raise ValueError('invalid rectangle coordinates')
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                raise ValueError('empty or inverted rectangle')
    rectangles(position.get('rects'))
    if value['type'] == 'note' and (len(position['rects']) != 1 or 'nextPageRects' in position):
        raise ValueError('note requires one page rectangle')
    if 'nextPageRects' in position:
        rectangles(position['nextPageRects'])
    if 'rotation' in position and (type(position['rotation']) is not int or position['rotation'] not in (0, 90, 180, 270)):
        raise ValueError('invalid rotation')


@dataclass(frozen=True)
class Attachment:
    paper_id: str
    attachment_id: str
    attachment_version: str


def _json(value, depth=0):
    if depth > 16:
        raise ValueError('JSON nesting limit')
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        if abs(value) > 1e15 or not math.isfinite(value):
            raise ValueError('invalid numeric value')
    elif type(value) is str:
        if len(value) > 64000 or '\x00' in value:
            raise ValueError('invalid string')
    elif type(value) in (dict, list):
        if len(value) > 2048:
            raise ValueError('JSON collection limit')
        if type(value) is dict:
            for key in value:
                if type(key) is not str:
                    raise ValueError('JSON key must be string')
                _json(key, depth + 1)
        for child in value.values() if type(value) is dict else value:
            _json(child, depth + 1)
    else:
        raise ValueError('unsupported JSON value')


def _canonical(value):
    _json(value)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(raw.encode('utf8')) > MAX_BYTES:
        raise ValueError('request size limit')
    return raw


class AnnotationStore:
    def __init__(self, *, authenticate, resolve_attachment):
        self.authenticate = authenticate
        self.resolve_attachment = resolve_attachment
        self.database = DomainDatabase('paper', 'reader_annotations', DDL)

    def _scope(self, context, selector):
        actor = self.authenticate(context)
        if not isinstance(actor, str) or not actor or len(actor) > 512:
            raise PermissionError('authenticated principal required')
        if set(selector) != {'paper_id', 'attachment_id', 'attachment_version'}:
            raise ValueError('exact attachment selector required')
        if any(type(v) is not str or not v or len(v) > 512 for v in selector.values()):
            raise ValueError('invalid attachment selector')
        descriptor = self.resolve_attachment(context, dict(selector))
        if not isinstance(descriptor, Attachment) or asdict(descriptor) != selector:
            raise Conflict('attachment descriptor mismatch')
        return actor, descriptor

    def _locked(self, db, actor, descriptor):
        values = (actor, descriptor.paper_id, descriptor.attachment_id, descriptor.attachment_version)
        db.execute('INSERT INTO reader_annotation_scopes(actor,paper_id,attachment_id,attachment_version) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', values)
        return db.execute('SELECT id,revision FROM reader_annotation_scopes WHERE actor=%s AND paper_id=%s AND attachment_id=%s AND attachment_version=%s FOR UPDATE', values).fetchone()

    def read(self, context, selector):
        _canonical(selector)
        actor, descriptor = self._scope(context, selector)
        with self.database.transaction() as db:
            row = db.execute('SELECT id,revision FROM reader_annotation_scopes WHERE actor=%s AND paper_id=%s AND attachment_id=%s AND attachment_version=%s FOR UPDATE', (actor, descriptor.paper_id, descriptor.attachment_id, descriptor.attachment_version)).fetchone()
            if not row:
                return {'document': asdict(descriptor), 'revision': '0', 'annotations': [], 'tombstones': []}
            scope_id, revision = row
            count, size = db.execute("SELECT count(*),COALESCE(sum(octet_length(COALESCE(value::text,''))+octet_length(annotation_id)+64),0) FROM reader_annotations WHERE scope_id=%s", (scope_id,)).fetchone()
            if count > MAX_READ_ROWS or size > MAX_READ_BYTES:
                raise ValueError('annotation read limit exceeded; no partial snapshot returned')
            rows = db.execute('SELECT annotation_id,value,revision FROM reader_annotations WHERE scope_id=%s ORDER BY annotation_id LIMIT %s', (scope_id, MAX_READ_ROWS + 1)).fetchall()
            if len(rows) > MAX_READ_ROWS or sum(len(json.dumps(r[1], ensure_ascii=False).encode()) + len(r[0]) + 64 for r in rows) > MAX_READ_BYTES:
                raise ValueError('annotation read limit exceeded; no partial snapshot returned')
            return {'document': asdict(descriptor), 'revision': str(revision),
                    'annotations': [r[1] for r in rows if r[1] is not None],
                    'tombstones': [{'id': r[0], 'revision': str(r[2])} for r in rows if r[1] is None]}

    def commit(self, context, payload):
        if type(payload) is not dict:
            raise ValueError('batch object required')
        raw = _canonical(payload)
        payload = json.loads(raw)  # immutable request snapshot
        selector = {k: payload.get(k) for k in ('paper_id', 'attachment_id', 'attachment_version')}
        if set(payload) != set(selector) | {'operation_id', 'expected_revision', 'format', 'upserts', 'deletes'}:
            raise ValueError('unknown or missing batch fields')
        if payload['format'] != 'zotero-reader-v1':
            raise ValueError('unsupported format')
        operation = payload['operation_id']
        if not isinstance(operation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', operation):
            raise ValueError('invalid operation_id')
        expected = payload['expected_revision']
        if not isinstance(expected, str) or not re.fullmatch(r'0|[1-9][0-9]{0,17}', expected):
            raise ValueError('invalid revision')
        upserts, deletes = payload['upserts'], payload['deletes']
        if type(upserts) is not list or type(deletes) is not list or len(upserts) + len(deletes) > 256:
            raise ValueError('invalid batch size')
        ids = []
        for value in upserts:
            if type(value) is not dict or type(value.get('type')) is not str or value.get('type') not in {'highlight', 'underline', 'note', 'text', 'image', 'ink'}:
                raise ValueError('invalid annotation')
            position = value.get('position')
            if not isinstance(position, dict) or type(position.get('pageIndex')) is not int or not 0 <= position['pageIndex'] < 100000:
                raise ValueError('invalid page index')
            if 'onlyTextOrComment' in value:
                raise ValueError('materialize full upsert before commit')
            _geometry(value)
            ids.append(value.get('id'))
        ids += deletes
        if any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', i) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('duplicate or invalid annotation id')
        actor, descriptor = self._scope(context, selector)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.database.transaction() as db:
            scope_id, revision = self._locked(db, actor, descriptor)
            receipt = db.execute('SELECT request_digest,response FROM reader_annotation_operations WHERE scope_id=%s AND operation_id=%s', (scope_id, operation)).fetchone()
            if receipt:
                if receipt[0] != digest:
                    raise Conflict('operation_id used with different body')
                return receipt[1]
            if str(revision) != expected:
                raise Conflict('revision conflict')
            revision += 1
            for identity, value in [(i, None) for i in deletes] + [(v['id'], v) for v in upserts]:
                db.execute('INSERT INTO reader_annotations(scope_id,annotation_id,value,revision) VALUES (%s,%s,%s,%s) ON CONFLICT(scope_id,annotation_id) DO UPDATE SET value=excluded.value,revision=excluded.revision', (scope_id, identity, Jsonb(value) if value is not None else None, revision))
            db.execute('UPDATE reader_annotation_scopes SET revision=%s WHERE id=%s', (revision, scope_id))
            response = {'document': asdict(descriptor), 'revision': str(revision), 'acknowledged_operation_id': operation, 'saved_ids': [v['id'] for v in upserts], 'deleted_ids': deletes}
            db.execute('INSERT INTO reader_annotation_operations VALUES (%s,%s,%s,%s)', (scope_id, operation, digest, Jsonb(response)))
        return response
