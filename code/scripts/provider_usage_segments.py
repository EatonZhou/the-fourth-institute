"""Lossless cache blocks for observed weekly quota descent; never billing proof.

The summaries serve weekly audit. Compressed members retain request identities,
deduplication, intermediate reset evidence and exact arbitrary time slicing.
"""
import json
import math
import zlib
from datetime import datetime


LIMIT = 256
MAGIC = b'usage-descent-v1\0'


def epoch(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def unpack(payload):
    if isinstance(payload, bytes):
        if not payload.startswith(MAGIC):
            raise ValueError('Unknown usage cache block')
        return json.loads(zlib.decompress(payload[len(MAGIC):]))['members']
    return [json.loads(payload)]


def observation(event):
    """Require contemporaneous native weekly samples, not inferred capacity."""
    if event.get('quota_only') or not event.get('id') or not event.get('session'):
        return None
    if event.get('model') in (None, '', 'unknown'):
        return None
    at = epoch(event['at'])
    samples = []
    evidence_rows = event.get('quota_evidence') or []
    # Earlier normalized caches retain native weekly samples in `quotas`.
    # Do not infer them from token totals or a nearby Home snapshot.
    if not evidence_rows and event.get('quotas'):
        if any(epoch(q['at']) != at for q in event['quotas']):
            return None
        evidence_rows = [{'scope': 'account_state', 'source': 'native_event.quotas',
                          'observed_at': event['at'], 'windows': event['quotas']}]
    for evidence in evidence_rows:
        if evidence.get('scope') != 'account_state':
            continue
        if evidence.get('source') not in ('codex.token_count.rate_limits', 'claude.rate_limit_info',
                                         'native_event.quotas'):
            continue
        if evidence.get('stale') or epoch(evidence['observed_at']) != at:
            return None
        if (evidence.get('flags') or {}).get('isUsingOverage') is True:
            return None
        for w in evidence.get('windows') or []:
            if w.get('window_duration_mins') != 10080 and w.get('id') not in (
                    'seven_day', 'seven_day_sonnet', 'seven_day_opus', 'seven_day_overage_included'):
                continue
            reset = w.get('resets_at')
            reset = float(reset) if type(reset) in (int, float) else epoch(reset)
            remaining = w.get('remaining_percent')
            if remaining is None and type(w.get('used_percent')) in (int, float):
                remaining = 100 - w['used_percent']
            if remaining is None and type(w.get('utilization')) in (int, float):
                remaining = 100 * (1 - w['utilization'])
            if (w.get('stale') or type(remaining) not in (int, float)
                    or not math.isfinite(remaining) or not 0 <= remaining <= 100
                    or not math.isfinite(reset) or reset <= at):
                return None
            key = json.dumps([evidence['source'], w.get('quota_group'), w.get('id'),
                              sorted(w.get('models') or []), reset], sort_keys=True)
            samples.append((key, remaining))
    if not samples or len({k for k, _ in samples}) != len(samples):
        return None
    return tuple(sorted(samples))


def compatible(left, right):
    fields = ('session', 'execution_session', 'model', 'service_tier', 'identity_epoch')
    if any(left.get(k) != right.get(k) for k in fields) or epoch(right['at']) <= epoch(left['at']):
        return False
    def funding(event):
        return [(e.get('source'), e.get('flags'), e.get('credits'))
                for e in event.get('quota_evidence') or []]
    if funding(left) != funding(right):
        return False
    a, b = observation(left), observation(right)
    return bool(a and b and [k for k, _ in a] == [k for k, _ in b]
                and all(y <= x for (_, x), (_, y) in zip(a, b)))


def encode(events):
    if len(events) > 1 and observation(events[0]) != observation(events[-1]):
        totals = {}
        for event in events:
            for field, value in event['usage'].items():
                totals[field] = totals.get(field, 0) + value
        summary = {'count': len(events), 'start': events[0]['at'], 'end': events[-1]['at'],
                   'usage': totals, 'weekly_start': observation(events[0]),
                   'weekly_end': observation(events[-1]), 'members': events}
        yield epoch(events[0]['at']), epoch(events[-1]['at']), MAGIC + zlib.compress(
            json.dumps(summary, separators=(',', ':')).encode())
    else:
        for event in events:
            yield epoch(event['at']), epoch(event['at']), json.dumps(event, separators=(',', ':'))


def pack(events):
    run = []
    for event in events:
        try:
            joins = bool(run and len(run) < LIMIT and compatible(run[-1], event))
        except (ValueError, TypeError, KeyError, OverflowError):
            joins = False
        if run and not joins:
            yield from encode(run)
            run = []
        run.append(event)
    if run:
        yield from encode(run)


def initialize(db):
    if 'end_at' not in {row[1] for row in db.execute('PRAGMA table_info(events)')}:
        db.execute('ALTER TABLE events ADD COLUMN end_at REAL')


def write(db, source, events, *, append):
    """Caller owns the checkpoint transaction. Only the tail may join an append."""
    head = []
    if append:
        tail = db.execute('SELECT seq,payload FROM events WHERE source=? ORDER BY seq DESC LIMIT 1',
                          (source,)).fetchone()
        if tail:
            head = unpack(tail[1])
            db.execute('DELETE FROM events WHERE seq=?', (tail[0],))
    else:
        db.execute('DELETE FROM events WHERE source=?', (source,))
    from itertools import chain
    db.executemany('INSERT INTO events(source,at,end_at,payload) VALUES(?,?,?,?)',
                   ((source, at, end, payload) for at, end, payload in pack(chain(head, events))))


def read(db, source, since, until):
    for (payload,) in db.execute('''SELECT payload FROM events WHERE source=?
            AND (end_at>=? OR (end_at IS NULL AND at>=?)) AND at<? ORDER BY seq''',
            (source, since, since, until)):
        for event in unpack(payload):
            if since <= epoch(event['at']) < until:
                yield event
