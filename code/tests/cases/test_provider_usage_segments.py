import copy
import json
import sqlite3

import pytest

from scripts import provider_usage_segments as segments
from scripts.provider_usage_compact import compact, fingerprint


def event(n, remaining=None):
    at = f'2026-11-01T00:{n:02}:00+00:00'
    return {'id': str(n), 'session': 's', 'execution_session': 'e', 'model': 'm',
            'at': at, 'usage': {'input_tokens': n, 'total_tokens': n * 2}, 'quotas': [],
            'quota_evidence': [{'scope': 'account_state', 'source': 'codex.token_count.rate_limits',
                                'observed_at': at, 'windows': [{'id': 'codex/secondary',
                                'quota_group': 'codex', 'window_duration_mins': 10080,
                                'resets_at': '2026-11-08T00:00:00Z',
                                'used_percent': 100 - (remaining if remaining is not None else 100-n)}]}]}


def database():
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE events(seq INTEGER PRIMARY KEY,source TEXT,at REAL,payload TEXT);
                        CREATE INDEX events_source_time ON events(source,at);''')
    segments.initialize(db)
    return db


def test_descent_summary_roundtrip_and_slice_inside_block():
    events = [event(n) for n in range(1, 10)]
    packed = list(segments.pack(events))
    assert len(packed) == 1
    assert segments.unpack(packed[0][2]) == events
    db = database()
    segments.write(db, 'a', events, append=False)
    assert list(segments.read(db, 'a', segments.epoch(events[3]['at']),
                              segments.epoch(events[6]['at']))) == events[3:6]
    segments.write(db, 'a', [event(10)], append=True)
    assert list(segments.read(db, 'a', 0, float('inf'))) == events + [event(10)]
    assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
    segments.write(db, 'a', [event(20)], append=False)
    assert list(segments.read(db, 'a', 0, float('inf'))) == [event(20)]


@pytest.mark.parametrize('boundary', ['rise', 'reset', 'stale', 'missing', 'model', 'tier',
                                      'identity', 'equal_time', 'overage', 'conflict', 'old_sample'])
def test_boundaries_never_merge(boundary):
    a, b = event(1), event(2)
    w = b['quota_evidence'][0]['windows'][0]
    if boundary == 'rise': w['used_percent'] = 0
    elif boundary == 'reset': w['resets_at'] = '2026-11-09T00:00:00Z'
    elif boundary == 'stale': w['stale'] = True
    elif boundary == 'missing': b.pop('quota_evidence')
    elif boundary == 'model': b['model'] = 'other'
    elif boundary == 'tier': b['service_tier'] = 'priority'
    elif boundary == 'identity': b['identity_epoch'] = 'new'
    elif boundary == 'equal_time': b['at'] = a['at']
    elif boundary == 'overage': b['quota_evidence'][0]['flags'] = {'isUsingOverage': True}
    elif boundary == 'conflict': b['quota_evidence'][0]['windows'].append(copy.deepcopy(w))
    elif boundary == 'old_sample': b['quota_evidence'][0]['observed_at'] = a['at']
    packed = list(segments.pack([a, b]))
    assert len(packed) == 2
    assert [e for _, _, payload in packed for e in segments.unpack(payload)] == [a, b]


def test_existing_cache_compaction_is_idempotent_and_preserves_logical_events():
    db = database()
    events = [event(n) for n in range(1, 10)]
    db.executemany('INSERT INTO events(source,at,payload) VALUES(?,?,?)',
                   [('a', segments.epoch(e['at']), json.dumps(e)) for e in events])
    db.commit()
    first = compact(db)
    assert first['before_rows'] == 9 and first['after_rows'] == 1
    assert first['verified_events'] == 9
    assert fingerprint(segments.read(db, 'a', 0, float('inf'))) == fingerprint(events)
    assert compact(db)['merged_sources'] == 0


def test_flat_quotas_and_missing_evidence_are_not_claimed_as_decline():
    assert len(list(segments.pack([event(1, 90), event(2, 90)]))) == 2
    a = event(1); a.pop('quota_evidence')
    assert len(list(segments.pack([a, event(2)]))) == 2


def test_older_native_weekly_samples_and_claude_windows():
    old, claude = [], []
    for n in (1, 2):
        e = event(n)
        e.pop('quota_evidence')
        e['quotas'] = [{'at': e['at'], 'quota_group': 'codex', 'window_duration_mins': 10080,
                        'remaining_percent': 100-n, 'resets_at': 1794096000}]
        old.append(e)
        c = event(n)
        info = c['quota_evidence'][0]
        info['source'] = 'claude.rate_limit_info'
        info['windows'] = [{'id': 'seven_day', 'utilization': n/100,
                             'resets_at': '2026-11-08T00:00:00Z'}]
        claude.append(c)
    for events in (old, claude):
        packed = list(segments.pack(events))
        assert len(packed) == 1
        assert segments.unpack(packed[0][2]) == events


def test_serial_parallel_reports_match_after_segment_append(tmp_path):
    from scripts.provider_usage_incremental import IncrementalCache
    from scripts.provider_usage_parallel import ParallelIncrementalCache
    from scripts.provider_weekly_usage import build_report
    path = tmp_path / 'rollout.jsonl'
    path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 's'}}) + '\n'
                    + json.dumps({'type': 'turn_context', 'payload': {'model': 'm'}}) + '\n')
    def add(n):
        row = {'type': 'event_msg', 'timestamp': event(n)['at'], 'payload': {'type': 'token_count',
                'info': {'last_token_usage': {'input_tokens': n, 'output_tokens': n, 'total_tokens': 2*n},
                         'total_token_usage': {'input_tokens': sum(range(n+1)),
                                              'output_tokens': sum(range(n+1)),
                                              'total_tokens': 2*sum(range(n+1))}},
                'rate_limits': {'limit_id': 'codex', 'secondary': {'window_minutes': 10080,
                                 'used_percent': n, 'resets_at': 1794096000}}}}
        with path.open('a') as f: f.write(json.dumps(row) + '\n')
    for n in range(1, 4): add(n)
    manifest = {'accounts': [{'provider': 'codex', 'account_id': 'a', 'sources': [
        {'format': 'codex', 'path': str(path), 'session_id': 's'}]}]}
    def report(cache=None):
        return build_report(manifest, '2026-11-01T00:00:00Z', '2026-11-08T00:00:00Z', cache=cache)['accounts']
    serial = IncrementalCache(tmp_path / 'serial', rebuild=True)
    parallel = ParallelIncrementalCache(tmp_path / 'parallel', rebuild=True, workers=1)
    try:
        expected = report()
        assert report(serial) == report(parallel) == expected
        assert serial.db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        add(4)
        assert report(serial) == report(parallel) == report()
        assert serial.db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
    finally:
        serial.close(); parallel.close()
