"""Opt-in translation cache and task state; no worker, route or provider calls."""
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
CREATE TABLE IF NOT EXISTS reader_translation_verified_results (
 task_id TEXT PRIMARY KEY, lookup_key TEXT NOT NULL, final_cache_key TEXT NOT NULL,
 provenance JSONB NOT NULL, published_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());
CREATE INDEX IF NOT EXISTS reader_translation_verified_lookup
 ON reader_translation_verified_results(lookup_key,published_at DESC,task_id);
'''
FIELDS = {'text', 'source_language', 'target_language', 'model', 'prompt', 'terminology', 'context', 'access'}


class ReuseProofUnavailable(RuntimeError):
    """Existing success cannot be compared to current material; no model retry."""


class TranslationCache:
    def __init__(self, *, authenticate, prepare, authorize_access, execute, validate_reuse=None):
        self.authenticate, self.prepare = authenticate, prepare
        self.authorize_access, self.execute = authorize_access, execute
        # Trusted owner capability: read-only proof that CURRENT materialized
        # input/model equals this historical evidence. Never browser input.
        # No validator means no cross-operation verified hit, not blind reuse.
        self.validate_reuse = validate_reuse
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
        return self.request(context, operation_id=operation_id, request=request,
                            refresh=refresh, _verified=True)

    def request(self, context, *, operation_id, request, refresh=False, _verified=False):
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
        if _verified:
            key = 'lookup:' + key
        digest = hashlib.sha256((raw + json.dumps(refresh)).encode()).hexdigest()
        if _verified:
            digest = hashlib.sha256(('verified:' + digest).encode()).hexdigest()
        with self.database.transaction(key='operation:' + actor + ':' + operation_id) as db:
            old = db.execute('SELECT digest,task_id FROM reader_translation_operations WHERE actor=%s AND operation_id=%s', (actor, operation_id)).fetchone()
            if old:
                if old[0] != digest: raise Conflict('operation reused with changed request')
                task_id, cache = old[1], 'operation'
            else:
                db.execute('INSERT INTO reader_translation_keys(cache_key) VALUES (%s) ON CONFLICT DO NOTHING', (key,))
                success, active = db.execute('SELECT successful_task,active_task FROM reader_translation_keys WHERE cache_key=%s FOR UPDATE', (key,)).fetchone()
                if _verified:
                    success = None
                    if not refresh:
                        # A discovery index is not an alias. Every use must
                        # prove current inputs/configuration before any model
                        # admission, with fresh authorization on both payloads.
                        candidates = db.execute('SELECT v.task_id,v.provenance,t.payload FROM reader_translation_verified_results v JOIN reader_translation_tasks t ON t.task_id=v.task_id WHERE v.lookup_key=%s AND t.status=\'succeeded\' ORDER BY v.published_at DESC,v.task_id LIMIT 16', (key,)).fetchall()
                        if candidates and self.validate_reuse is None:
                            raise ReuseProofUnavailable('Current materialization proof is unavailable; use explicit refresh to regenerate')
                        for candidate_id, provenance, candidate_payload in candidates:
                            self._authorize(context, actor, candidate_payload)
                            proof = self.validate_reuse(context, payload, provenance)
                            if (not isinstance(proof, dict) or not proof.get('current_input_model_digest')
                                    or not proof.get('configuration_revision') or not proof.get('materialization_revision')):
                                raise ReuseProofUnavailable('Current materialization proof is incomplete')
                            if (_canonical(candidate_payload) == raw and isinstance(proof, dict)
                                    and proof.get('current_input_model_digest') == provenance['actual']['input_model_digest']
                                    and proof.get('configuration_revision')
                                    and proof.get('materialization_revision')):
                                success = candidate_id
                                break
                if success and not refresh:
                    task_id, cache = success, 'hit'
                elif active:
                    task_id, cache = active, 'inflight'
                else:
                    task_id, cache = secrets.token_hex(16), 'miss'
                    db.execute("INSERT INTO reader_translation_tasks(task_id,cache_key,payload,requested_actor,status,publication_mode) VALUES (%s,%s,%s,%s,'queued',%s)", (task_id, key, Jsonb(payload), actor, 'verified' if _verified else 'legacy'))
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
                raise ValueError('verified tasks require finish_verified')
            # Use the same lock order as request: key before task.
            db.execute('SELECT cache_key FROM reader_translation_keys WHERE cache_key=%s FOR UPDATE', (key,))
            row = db.execute("UPDATE reader_translation_tasks SET status=%s,result=%s,error=%s,outcome=%s,lease_token=NULL,lease_until=NULL WHERE task_id=%s AND status='running' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING task_id", ('failed' if error is not None else 'succeeded', None if error is not None else result, 'translation execution failed' if error is not None else None, Jsonb(outcome) if outcome is not None else None, task_id, token)).fetchone()
            if not row: raise Conflict('expired or superseded lease')
            db.execute('UPDATE reader_translation_keys SET active_task=NULL WHERE cache_key=%s AND active_task=%s', (key, task_id))
            if error is None:
                db.execute('UPDATE reader_translation_keys SET successful_task=%s WHERE cache_key=%s', (task_id, key))
        return self.get(context, task_id)

    @staticmethod
    def final_key(payload, actual):
        """Scope and semantics bind the post-submission material fingerprint."""
        value = {'schema': 'paper.translation-final.v1', 'descriptor': payload,
                 'input_model_digest': actual['input_model_digest'],
                 'provider': actual['provider'], 'requested_model': actual['requested_model'],
                 'reported_model': actual['reported_model']}
        return 'final:' + hashlib.sha256(_canonical(value).encode()).hexdigest()

    def finish_verified(self, context, task_id, token, *, result, provenance, final_cache_key):
        """Re-read actual owner evidence; atomically publish result and provenance.

        Provenance supplies the ORIGINAL submitted handle, not an attestation
        boolean. ActualTranslationEvidence reauthorizes and reads the immutable
        request and completion. This method never submits/resumes a model.
        """
        self._execution_owner(context, task_id)
        if not isinstance(result, str) or not result or len(result.encode()) > 256 * 1024:
            raise ValueError('bounded successful translation required')
        from management_portal.paper_translation_workflow import ActualTranslationEvidence
        descriptor = self.descriptor(context, task_id)
        actual = ActualTranslationEvidence().read_task(actor=self._actor(context), task_id=task_id,
                                                       handle=provenance['handle'], descriptor=descriptor)
        if actual.get('state') != 'completed' or actual.get('text') != result:
            raise ValueError('matching actual completion required')
        # A caller-supplied digest can never substitute for the actual read.
        if 'actual' in provenance and _canonical(provenance['actual']) != _canonical(actual):
            raise Conflict('actual provenance changed')
        with self.database.transaction() as db:
            key, payload, mode = db.execute('SELECT cache_key,payload,publication_mode FROM reader_translation_tasks WHERE task_id=%s', (task_id,)).fetchone()
            if mode != 'verified' or final_cache_key != self.final_key(payload, actual):
                raise ValueError('actual final key required for a verified task')
            db.execute('INSERT INTO reader_translation_keys(cache_key) VALUES (%s) ON CONFLICT DO NOTHING', (final_cache_key,))
            # Consistent order for concurrent publications of the same final key.
            for lock_key in sorted((key, final_cache_key)):
                db.execute('SELECT cache_key FROM reader_translation_keys WHERE cache_key=%s FOR UPDATE', (lock_key,))
            changed = db.execute("UPDATE reader_translation_tasks SET status='succeeded',result=%s,error=NULL,lease_token=NULL,lease_until=NULL WHERE task_id=%s AND status='running' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING task_id", (result, task_id, token)).fetchone()
            if not changed:
                raise Conflict('expired or superseded lease')
            stored = {'handle': provenance['handle'], 'actual': actual}
            db.execute('INSERT INTO reader_translation_verified_results(task_id,lookup_key,final_cache_key,provenance) VALUES (%s,%s,%s,%s)', (task_id, key, final_cache_key, Jsonb(stored)))
            db.execute('UPDATE reader_translation_keys SET successful_task=%s WHERE cache_key=%s', (task_id, final_cache_key))
            db.execute('UPDATE reader_translation_keys SET active_task=NULL WHERE cache_key=%s AND active_task=%s', (key, task_id))
        return self.get(context, task_id)

    def _execution_owner(self, context, task_id):
        self.get(context, task_id)
        with self.database.read() as db:
            actor = db.execute('SELECT requested_actor FROM reader_translation_tasks WHERE task_id=%s', (task_id,)).fetchone()[0]
        if actor != self._actor(context):
            raise PermissionError('task execution must use its original authenticated requester')

    def run(self, context, task_id):
        self._execution_owner(context, task_id)
        with self.database.read() as db:
            mode = db.execute('SELECT publication_mode FROM reader_translation_tasks WHERE task_id=%s', (task_id,)).fetchone()[0]
        if mode == 'verified':
            raise ValueError('verified tasks must use their original formal Resident workflow')
        claimed = self.claim(context, task_id)
        if not claimed: return self.get(context, task_id)
        token, payload = claimed
        try:
            # Contract: canonical submit-or-resume keyed by task_id, fresh context per TASK.
            # A reclaimed lease must resume its binding, never submit another execution.
            result = self.execute(context, {'task_id': task_id, 'fresh_context_id': task_id, 'history': [], 'descriptor': payload})
            if not isinstance(result, str) or not result or len(result.encode()) > 256 * 1024:
                raise ValueError('invalid translation result')
        except Exception:
            return self.finish(context, task_id, token, error=True)
        return self.finish(context, task_id, token, result=result)
