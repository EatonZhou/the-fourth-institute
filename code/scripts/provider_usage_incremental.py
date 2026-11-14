"""Private, instance-local checkpoints of normalized usage, never message bodies."""
import hashlib
import json
import sqlite3
import ast
from pathlib import Path

from management_common.file_permissions import ensure_private_directory
from scripts.provider_usage_native import record_path
from scripts import provider_usage_segments as segments


def stamp(path):
    def stat(p):
        try:
            s = p.stat()
            return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
        except FileNotFoundError:
            return None
    return [stat(path), stat(Path(str(path) + '-wal'))]


def anchors(path, size):
    with path.open('rb') as f:
        first = f.read(min(size, 4096))
        f.seek(max(0, size - 4096))
        last = f.read(min(size, 4096))
    return hashlib.sha256(first + last).hexdigest()


class IncrementalCache:
    def __init__(self, root='features:provider_usage/cache', rebuild=False):
        root = record_path(root)
        ensure_private_directory(root)
        self.db = sqlite3.connect(root / 'usage.sqlite', timeout=60)
        (root / 'usage.sqlite').chmod(0o600)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS version(value TEXT);
            CREATE TABLE IF NOT EXISTS sources(key TEXT PRIMARY KEY,stamp TEXT,offset INTEGER,state TEXT,anchor TEXT,gaps TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,source TEXT,at REAL,payload TEXT);
            CREATE INDEX IF NOT EXISTS events_source_time ON events(source,at);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,stamp TEXT,payload TEXT);''')
        segments.initialize(self.db)
        self.db.commit()
        import management_portal.execution.provider_turn_usage as turn
        import management_portal.execution.scheduler_usage as usage
        import management_portal.codex_app_server_client as codex
        # The cache codec owns storage, not parsing. Its changes must not force
        # native history to be reparsed; parser semantics alone own this stamp.
        files = [Path(__file__).with_name('provider_usage_native.py'),
                 Path(turn.__file__), Path(usage.__file__), Path(codex.__file__)]
        weekly = ast.parse(Path(__file__).with_name('provider_weekly_usage.py').read_text())
        readers = [node for node in weekly.body
                   if isinstance(node, ast.ClassDef) and (node.name == 'NativeSource' or
                       any(isinstance(base, ast.Name) and base.id == 'NativeSource' for base in node.bases))
                   or isinstance(node, ast.FunctionDef) and node.name == 'rows']
        attribution = ast.parse(Path(__file__).with_name('provider_usage_attribution.py').read_text())
        readers.extend(node for node in attribution.body if isinstance(node, ast.FunctionDef) and
                       node.name in {'fields', 'codex_evidence', 'claude_evidence',
                                     'request_metadata', 'antigravity_evidence'})
        # Evidence/aggregation changes do not invalidate normalized parser output.
        version = hashlib.sha256(b''.join(p.read_bytes() for p in files) +
                                 ''.join(ast.dump(node) for node in readers).encode()).hexdigest()
        old = self.db.execute('SELECT value FROM version').fetchone()
        if not rebuild and old != (version,):
            self.db.close()
            raise ValueError('Usage index missing or parser changed; initialize explicitly with --rebuild')
        if rebuild or old != (version,):
            with self.db:
                for table in ('events', 'sources', 'metadata', 'version'):
                    self.db.execute('DELETE FROM ' + table)
                self.db.execute('INSERT INTO version VALUES(?)', (version,))
        self.stats = {'cache_hits': 0, 'raw_files_read': 0, 'appended_files': 0, 'jsonl_bytes_read': 0}

    def close(self):
        self.db.close()

    def metadata(self, path, kind, load):
        path = record_path(path)
        key, signature = kind + ':' + str(path), stamp(path)
        old = self.db.execute('SELECT stamp,payload FROM metadata WHERE key=?', (key,)).fetchone()
        if old and json.loads(old[0]) == signature:
            return json.loads(old[1])
        result = load(path)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?,?)',
                            (key, json.dumps(signature), json.dumps(result)))
        return result

    def session_header(self, path):
        def load(p):
            with p.open(encoding='utf-8') as f:
                try:
                    row = json.loads(f.readline())
                    return (row.get('payload') or {}).get('id', '') if row.get('type') == 'session_meta' else ''
                except (ValueError, AttributeError):
                    return ''
        return self.metadata(path, 'header', load)

    def child_ids(self, path):
        def load(p):
            result = []
            with p.open(encoding='utf-8') as f:
                for line in f:
                    if '"subagent/catalog"' in line:
                        try:
                            result.append((json.loads(line).get('data') or {}).get('childId', ''))
                        except ValueError:
                            pass
            return result
        return self.metadata(path, 'children', load)

    def read(self, source, reader_type, gaps, since, until):
        path = record_path(source['path'])
        key = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
        signature = stamp(path)
        if signature[0] is None:
            raise FileNotFoundError(path)
        self.db.execute('BEGIN IMMEDIATE')
        old = self.db.execute('SELECT stamp,offset,state,anchor,gaps FROM sources WHERE key=?', (key,)).fetchone()
        if old and json.loads(old[0]) == signature:
            self.stats['cache_hits'] += 1
            gaps.update(json.loads(old[4]))
            self.db.commit()
        else:
            previous = json.loads(old[0])[0] if old else None
            append = bool(old and source['format'] in ('codex', 'claude', 'kimi', 'dsh', 'requests')
                          and previous[:2] == signature[0][:2] and signature[0][2] > previous[2]
                          and anchors(path, previous[2]) == old[3])
            cursor = {'offset': old[1] if append else 0, 'parser': json.loads(old[2]) if append else {}}
            local_gaps = set(json.loads(old[4])) if append else set()
            local_gaps.discard('pending_partial_json_line')
            reader_source = {**source, '_cursor': cursor} if source['format'] in ('codex', 'claude', 'kimi', 'dsh', 'requests') else source
            # One source refresh is atomic. Failed parses cannot replace a good checkpoint.
            with self.db:
                segments.write(self.db, key,
                               (e for e in reader_type(reader_source, local_gaps).read() if e),
                               append=append)
                if stamp(path) != signature:
                    raise OSError('Source changed during incremental read; retry required')
                self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?)',
                    (key, json.dumps(signature), cursor['offset'], json.dumps(cursor['parser']),
                     anchors(path, signature[0][2]) if source['format'] not in ('antigravity', 'opencode_sqlite') else '',
                     json.dumps(sorted(local_gaps))))
            gaps.update(local_gaps)
            self.stats['raw_files_read'] += 1
            self.stats['appended_files'] += int(append)
            self.stats['jsonl_bytes_read'] += cursor.get('bytes_read', 0)
        yield from segments.read(self.db, key, since.timestamp(), until.timestamp())
