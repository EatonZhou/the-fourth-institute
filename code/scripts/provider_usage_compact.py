"""Compact normalized cache only; do not refresh providers or bless parser versions."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import provider_usage_segments as segments
from scripts.provider_usage_native import record_path


def fingerprint(events):
    digest, count = hashlib.sha256(), 0
    for event in events:
        digest.update(json.dumps(event, sort_keys=True, separators=(',', ':')).encode() + b'\n')
        count += 1
    return count, digest.hexdigest()


def compact(db):
    segments.initialize(db)
    db.commit()
    before = db.execute('SELECT count(*),sum(length(payload)) FROM events').fetchone()
    checked, merged = 0, 0
    # One write transaction: a failure leaves all event/checkpoint pairs intact.
    with db:
        db.execute('BEGIN IMMEDIATE')
        sources = [r[0] for r in db.execute('SELECT DISTINCT source FROM events')]
        for source in sources:
            rows = db.execute('SELECT payload FROM events WHERE source=? ORDER BY seq', (source,)).fetchall()
            events = [event for (payload,) in rows for event in segments.unpack(payload)]
            packed = list(segments.pack(events))
            if len(packed) >= len(rows):
                continue
            expected = fingerprint(events)
            db.execute('DELETE FROM events WHERE source=?', (source,))
            db.executemany('INSERT INTO events(source,at,end_at,payload) VALUES(?,?,?,?)',
                           ((source, at, end, payload) for at, end, payload in packed))
            actual = fingerprint(segments.read(db, source, float('-inf'), float('inf')))
            if actual != expected:
                raise ValueError('Usage segment roundtrip mismatch; transaction rolled back')
            checked += expected[0]
            merged += 1
    after = db.execute('SELECT count(*),sum(length(payload)) FROM events').fetchone()
    return {'before_rows': before[0], 'after_rows': after[0], 'before_payload_bytes': before[1],
            'after_payload_bytes': after[1], 'merged_sources': merged, 'verified_events': checked,
            'parser_version_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-root', default='features:provider_usage/cache')
    parser.add_argument('--vacuum', action='store_true')
    args = parser.parse_args()
    path = record_path(args.cache_root) / 'usage.sqlite'
    with sqlite3.connect('file:' + str(path) + '?mode=rw', uri=True, timeout=60) as db:
        result = compact(db)
        if args.vacuum:
            db.execute('VACUUM')
        result['database_bytes'] = path.stat().st_size
    print(json.dumps(result))


if __name__ == '__main__':
    main()
