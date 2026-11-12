"""Translation cache and fenced task state; execution belongs to the service."""
import hashlib
import json
import secrets

from psycopg.types.json import Jsonb
from management_common.domain_database import DomainDatabase
from management_portal.paper_reader_annotations import _canonical, Conflict

DDL = '''
CREATE TABLE IF NOT EXISTS reader_translation_keys (
 cache_key TEXT PRIMARY KEY, successful_task TEXT, active_task TEXT);
CREATE TABLE IF NOT EXISTS reader_translation_tasks (
 task_id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, payload JSONB NOT NULL, requested_actor TEXT NOT NULL,
 status TEXT NOT NULL, result TEXT, error TEXT, lease_token TEXT, lease_until TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS reader_translation_operations (
 actor TEXT NOT NULL, operation_id TEXT NOT NULL, digest TEXT NOT NULL,
 task_id TEXT NOT NULL, PRIMARY KEY(actor,operation_id));
ALTER TABLE reader_translation_tasks ADD COLUMN IF NOT EXISTS publication_mode TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE reader_translation_tasks ADD COLUMN IF NOT EXISTS outcome JSONB;
'''
FIELDS = {'text', 'source_language', 'target_language', 'model', 'prompt', 'terminology', 'context', 'access'}


class TranslationCache:
    def __init__(self, *, authenticate, prepare, authorize_access):
        self.authenticate, self.prepare = authenticate, prepare
        self.authorize_access = authorize_access
        self.database = DomainDatabase('paper', 'reader_translation', DDL)

    def _actor(self, context):
        actor = self.authenticate(context)
        if not isinstance(actor, str) or not actor or len(actor) > 512:
            raise PermissionError('authenticated principal required')
        return actor

    def _authorize(self, context, actor, payload):
        access = payload['access']
        if access['kind'] == 'private' and access['actor'] != actor:
            raise PermissionError('private translation')
        self.authorize_access(context, payload)

    def request_verified(self, context, *, operation_id, request, refresh=False):
        raise ValueError('Verified subscription translation experiment is permanently cancelled')

    def request(self, context, *, operation_id, request, refresh=False):
        actor = self._actor(context)
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128 or type(refresh) is not bool:
            raise ValueError('invalid operation or refresh')
        # prepare resolves actual model/settings, exact sent prompt/context and server-approved access.
        payload = json.loads(_canonical(self.prepare(context, request)))
        if set(payload) != FIELDS or not isinstance(payload['text'], str) or not payload['text']:
            raise ValueError('complete translation descriptor required')
        for name in ('source_language', 'target_language'):
            if not isinstance(payload[name], str) or not payload[name]: raise ValueError('language required')
        if any(not isinstance(payload[name], dict) for name in ('model', 'prompt', 'terminology', 'context', 'access')):
            raise ValueError('resolved settings required')
        if not {'service', 'model', 'settings', 'revision'} <= set(payload['model']) or not {'version', 'system'} <= set(payload['prompt']) or not {'version', 'terms'} <= set(payload['terminology']):
            raise ValueError('actual model configuration and versioned prompt/terms required')
        access = payload['access']
        if access.get('kind') == 'private':
            payload['access'] = {'kind': 'private', 'actor': actor}
        elif access.get('kind') != 'shared' or not isinstance(access.get('scope'), str) or not access['scope']:
            raise ValueError('server-approved access scope required')
        self._authorize(context, actor, payload)
        raw = _canonical(payload)
        key = hashlib.sha256(raw.encode()).hexdigest()
        digest = hashlib.sha256((raw + json.dumps(refresh)).encode()).hexdigest()
        with self.database.transaction(key='operation:' + actor + ':' + operation_id) as db:
            old = db.execute('SELECT digest,task_id FROM reader_translation_operations WHERE actor=%s AND operation_id=%s', (actor, operation_id)).fetchone()
            if old:
                if old[0] != digest: raise Conflict('operation reused with changed request')
                task_id, cache = old[1], 'operation'
            else:
                db.execute('INSERT INTO reader_translation_keys(cache_key) VALUES (%s) ON CONFLICT DO NOTHING', (key,))
                success, active = db.execute('SELECT successful_task,active_task FROM reader_translation_keys WHERE cache_key=%s FOR UPDATE', (key,)).fetchone()
                if success and not refresh:
                    task_id, cache = success, 'hit'
                elif active:
                    task_id, cache = active, 'inflight'
                else:
                    task_id, cache = secrets.token_hex(16), 'miss'
                    db.execute("INSERT INTO reader_translation_tasks(task_id,cache_key,payload,requested_actor,status,publication_mode) VALUES (%s,%s,%s,%s,'queued','legacy')", (task_id, key, Jsonb(payload), actor))
                    db.execute('UPDATE reader_translation_keys SET active_task=%s WHERE cache_key=%s', (task_id, key))
                db.execute('INSERT INTO reader_translation_operations VALUES (%s,%s,%s,%s)', (actor, operation_id, digest, task_id))
        return {**self.get(context, task_id), 'cache': cache}

    def get(self, context, task_id):
        actor = self._actor(context)
        with self.database.read() as db:
            row = db.execute('SELECT payload,status,result,error,outcome FROM reader_translation_tasks WHERE task_id=%s', (task_id,)).fetchone()
        if not row: raise KeyError('translation task missing')
        self._authorize(context, actor, row[0])
        value = dict(task_id=task_id, status=row[1], text=row[2], error=row[3])
        if row[4] is not None: value['outcome'] = row[4]
        return value

    def descriptor(self, context, task_id):
        self.get(context, task_id)
        with self.database.read() as db:
            return db.execute('SELECT payload FROM reader_translation_tasks WHERE task_id=%s', (task_id,)).fetchone()[0]

    def claim(self, context, task_id, *, lease_seconds=60):
        self._execution_owner(context, task_id)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600: raise ValueError('invalid lease')
        token = secrets.token_hex(16)
        with self.database.transaction() as db:
            row = db.execute("UPDATE reader_translation_tasks SET status='running',lease_token=%s,lease_until=clock_timestamp()+(%s * interval '1 second') WHERE task_id=%s AND (status='queued' OR (status='running' AND lease_until<=clock_timestamp())) RETURNING payload", (token, lease_seconds, task_id)).fetchone()
        return (token, row[0]) if row else None

    def finish(self, context, task_id, token, *, result=None, error=None, outcome=None):
        self._execution_owner(context, task_id)
        if error is None and (not isinstance(result, str) or not result or len(result.encode()) > 256 * 1024):
            raise ValueError('bounded successful translation required')
        with self.database.transaction() as db:
            key, mode = db.execute('SELECT cache_key,publication_mode FROM reader_translation_tasks WHERE task_id=%s', (task_id,)).fetchone()
            if mode == 'verified' and error is None:
                raise ValueError('Verified subscription translation experiment is permanently cancelled')
            # Use the same lock order as request: key before task.
            db.execute('SELECT cache_key FROM reader_translation_keys WHERE cache_key=%s FOR UPDATE', (key,))
            row = db.execute("UPDATE reader_translation_tasks SET status=%s,result=%s,error=%s,outcome=%s,lease_token=NULL,lease_until=NULL WHERE task_id=%s AND status='running' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING task_id", ('failed' if error is not None else 'succeeded', None if error is not None else result, 'translation execution failed' if error is not None else None, Jsonb(outcome) if outcome is not None else None, task_id, token)).fetchone()
            if not row: raise Conflict('expired or superseded lease')
            db.execute('UPDATE reader_translation_keys SET active_task=NULL WHERE cache_key=%s AND active_task=%s', (key, task_id))
            if error is None:
                db.execute('UPDATE reader_translation_keys SET successful_task=%s WHERE cache_key=%s', (task_id, key))
        return self.get(context, task_id)

    def _execution_owner(self, context, task_id):
        self.get(context, task_id)
        with self.database.read() as db:
            actor = db.execute('SELECT requested_actor FROM reader_translation_tasks WHERE task_id=%s', (task_id,)).fetchone()[0]
        if actor != self._actor(context):
            raise PermissionError('task execution must use its original authenticated requester')
