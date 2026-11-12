"""Incremental auxiliary quota evidence; no provider processes or usage probes."""
from datetime import datetime, timedelta, timezone
import hashlib
import ast
import inspect
import json
import sqlite3

from management_common.file_permissions import ensure_private_directory
from scripts.provider_usage_incremental import stamp, anchors
from scripts.provider_usage_native import record_path, expand_sources
from scripts.provider_usage_native import quota_rows
from scripts.provider_usage_refresh import CLAUDE_QUOTA_MARKERS, claude_quota_records, home_quota_records


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def home_pages(provider, account, after_id, replay_from):
    from management_common.portal_database import portal_database
    from management_common.database_time import iso_time
    from management_common.json_columns import json_value
    database = portal_database(schema='execution_account_observations')
    try:
        with database.connect() as c:
            c.execute('SET TRANSACTION READ ONLY')
            if not c.execute("SELECT to_regclass(current_schema()||'.observations') AS t").fetchone()['t']:
                raise OSError('Home observation table is not available')
            page_after = 0
            while True:
                rows = c.execute('''SELECT id,account_id,finished_at,sample FROM observations
                    WHERE provider=%s AND account_id=%s AND id>%s
                    AND (id>%s OR finished_at>=%s) ORDER BY id LIMIT 1000''',
                    (provider, account, page_after, after_id, replay_from)).fetchall()
                if not rows:
                    break
                yield [{**dict(r), 'finished_at': iso_time(r['finished_at']),
                        'sample': json_value(r['sample'])} for r in rows]
                page_after = rows[-1]['id']
    finally:
        database.close()


class RefreshJournal:
    def __init__(self, root='features:provider_usage/refresh-cache'):
        root = record_path(root)
        ensure_private_directory(root)
        path = root / 'refresh.sqlite'
        self.db = sqlite3.connect(path, timeout=60)
        path.chmod(0o600)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS files (
            key TEXT PRIMARY KEY, stamp TEXT, offset INTEGER, anchor TEXT);
            CREATE TABLE IF NOT EXISTS home (key TEXT PRIMARY KEY, after_id INTEGER, latest TEXT);
            CREATE TABLE IF NOT EXISTS observations (
                source TEXT, key TEXT, provider TEXT, account TEXT, at REAL, payload TEXT,
                PRIMARY KEY(source,key));
            CREATE INDEX IF NOT EXISTS observations_account ON observations(provider,account,at);
            CREATE TABLE IF NOT EXISTS version(value INTEGER);
            CREATE TABLE IF NOT EXISTS parser_signature(value TEXT);''')
        version = self.db.execute('SELECT value FROM version').fetchone()
        if version is None:
            with self.db:
                self.db.execute('INSERT INTO version VALUES(1)')
        elif version != (1,):
            self.db.close()
            raise ValueError('Refresh journal version changed; use a new refresh-cache-root for explicit backfill')
        signature = digest([ast.dump(ast.parse(inspect.getsource(function))) for function in
            (claude_quota_records, home_quota_records, quota_rows, home_pages)])
        previous = self.db.execute('SELECT value FROM parser_signature').fetchone()
        if previous is None:
            # Establish the fingerprint baseline for the initial v1 journal.
            with self.db:
                self.db.execute('INSERT INTO parser_signature VALUES(?)', (signature,))
        elif previous != (signature,):
            self.db.close()
            raise ValueError('Refresh parser changed; use a new refresh-cache-root for explicit backfill')
        self.stats = {'raw_files_read': 0, 'raw_bytes_read': 0, 'file_cache_hits': 0,
                      'appended_files': 0, 'home_rows_replayed_or_new': 0, 'observations_added': 0,
                      'failures': [], 'home_replay_days': 1}
        self.failed_sources = set()

    def close(self):
        self.db.close()

    def _put(self, source, provider, account, observation):
        from scripts.provider_weekly_usage import moment
        self.db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?)',
                        (source, digest(observation), provider, account,
                         moment(observation['at']).timestamp(), json.dumps(observation)))

    def sync_file(self, provider, account, reference):
        path = record_path(reference)
        key = digest([provider, account, str(path)])
        signature = stamp(path)
        if signature[0] is None:
            raise FileNotFoundError(path)
        old = self.db.execute('SELECT stamp,offset,anchor FROM files WHERE key=?', (key,)).fetchone()
        if old and json.loads(old[0]) == signature:
            self.stats['file_cache_hits'] += 1
            return
        before = json.loads(old[0])[0] if old else None
        append = bool(old and before[:2] == signature[0][:2] and signature[0][2] > before[2]
                      and anchors(path, before[2]) == old[2])
        offset = old[1] if append else 0
        read = 0
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT stamp,offset,anchor FROM files WHERE key=?', (key,)).fetchone() != old:
                raise OSError('Quota file checkpoint changed concurrently')
            if not append:
                self.db.execute('DELETE FROM observations WHERE source=?', (key,))
            changes = self.db.total_changes
            with path.open('rb') as stream:
                stream.seek(offset)
                for raw in stream:
                    read += len(raw)
                    if not raw.endswith(b'\n'):
                        break
                    offset = stream.tell()
                    if not any(marker in raw for marker in CLAUDE_QUOTA_MARKERS):
                        continue
                    row = json.loads(raw)
                    if not isinstance(row, dict):
                        raise ValueError('Native quota record must be an object')
                    for observation in claude_quota_records(row):
                        self._put(key, provider, account, observation)
            anchor = anchors(path, signature[0][2])
            if stamp(path) != signature:
                raise OSError('Native quota file changed during read')
            added = self.db.total_changes - changes
            self.db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?)',
                            (key, json.dumps(signature), offset, anchor))
        self.stats['raw_files_read'] += 1
        self.stats['raw_bytes_read'] += read
        self.stats['appended_files'] += int(append)
        self.stats['observations_added'] += added

    def sync_home(self, provider, account, page_reader=home_pages):
        key = digest(['home', provider, account])
        old = self.db.execute('SELECT after_id,latest FROM home WHERE key=?', (key,)).fetchone()
        after, latest = old if old else (0, None)
        replay = datetime.fromisoformat(latest) - timedelta(days=1) if latest else datetime(1970, 1, 1, tzinfo=timezone.utc)
        for page in page_reader(provider, account, after, replay):
            if not page:
                continue
            new_after = max(after, max(r['id'] for r in page))
            newest = max([datetime.fromisoformat(r['finished_at'].replace('Z', '+00:00')) for r in page] +
                         ([datetime.fromisoformat(latest)] if latest else []))
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                current = self.db.execute('SELECT after_id,latest FROM home WHERE key=?', (key,)).fetchone()
                if current:
                    new_after = max(new_after, current[0])
                    newest = max(newest, datetime.fromisoformat(current[1]))
                changes = self.db.total_changes
                for row in page:
                    for observation in home_quota_records([row]):
                        self._put(key, provider, account, observation)
                added = self.db.total_changes - changes
                self.db.execute('INSERT OR REPLACE INTO home VALUES(?,?,?)', (key, new_after, newest.isoformat()))
            after, latest = new_after, newest.isoformat()
            self.stats['home_rows_replayed_or_new'] += len(page)
            self.stats['observations_added'] += added

    def observations(self, provider, account, since, until):
        from scripts.provider_weekly_usage import moment
        result = []
        for source, payload in self.db.execute('''SELECT source,payload FROM observations
                WHERE provider=? AND account=? AND at>=? AND at<?''',
                (provider, account, since.timestamp(), until.timestamp())):
            if source in self.failed_sources:
                continue
            q = json.loads(payload)
            if q.get('evidence_source') == 'monitor_quota' and not q.get('observed_at'):
                continue
            try:
                if since <= moment(q['at']) < until:
                    result.append(q)
            except (TypeError, ValueError, OverflowError):
                continue
        return result


def enrich_manifest(manifest, accounts, since, until, root='features:provider_usage/refresh-cache'):
    from psycopg import Error as DatabaseError
    registered = {(a['provider'], a['account_id']) for a in accounts
                  if ((a.get('registration') or {}).get('kind') or a.get('kind')) == 'subscription_login'}
    journal = RefreshJournal(root)
    try:
        for account in manifest['accounts']:
            provider, identity = account['provider'], account['account_id']
            if (provider, identity) not in registered:
                continue
            if provider == 'claude':
                sources = (s for s in account.get('sources', []) if s['format'] == 'claude')
                for source in expand_sources(sources, set()):
                    try:
                        journal.sync_file(provider, identity, source['path'])
                    except (OSError, ValueError, TypeError) as error:
                        journal.failed_sources.add(digest([provider, identity, str(record_path(source['path']))]))
                        journal.stats['failures'].append({'source': 'native_quota', 'provider': provider,
                                                         'account': identity, 'error': type(error).__name__})
            try:
                journal.sync_home(provider, identity)
            except (OSError, DatabaseError, ValueError, TypeError) as error:
                journal.failed_sources.add(digest(['home', provider, identity]))
                journal.stats['failures'].append({'source': 'home', 'provider': provider,
                                                 'account': identity, 'error': type(error).__name__})
            account.setdefault('quota_observations', []).extend(journal.observations(provider, identity, since, until))
        return {**journal.stats, 'status': 'partial' if journal.stats['failures'] else 'available',
                'late_commit_policy': 'replay_last_24h_of_finished_at; older_delayed_commits_require_backfill'}
    finally:
        journal.close()
