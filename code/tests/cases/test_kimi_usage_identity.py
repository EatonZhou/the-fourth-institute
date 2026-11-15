"""Native additive UsageRecord identity must agree across ledger and offline audit."""
import json
import hashlib

from management_portal.execution.provider_turn_usage import read_kimi_turn_usage
from scripts.provider_weekly_usage import KimiSource


def row(time=1001, **extra):
    return {'type': 'usage.record', 'agentId': 'main', 'time': time,
            'model': 'kimi', 'usage': {'inputOther': 10, 'inputCacheRead': 20, 'output': 3}, **extra}


def wire(tmp_path, records, agent='main'):
    path = tmp_path / 'agents' / agent / 'wire.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r)+'\n' for r in records))
    return path


def offline(path):
    return list(KimiSource({'path': str(path), 'session_id': 's'}, set()).read())


def test_identical_additive_records_keep_distinct_stable_ledger_ids(tmp_path):
    path = wire(tmp_path, [row(), row()])
    observed = read_kimi_turn_usage(path, since_ms=1000)
    assert observed.turn['total_tokens'] == 66
    ids = [r.request_id for r in observed.requests]
    assert len(set(ids)) == 2
    assert [r.usage for r in observed.requests] == [r['usage'] for r in offline(path)]
    assert ids[0] == 'kimi:'+hashlib.sha256(path.read_text().splitlines()[0].encode()).hexdigest()[:32]
    assert ids == [r.request_id for r in read_kimi_turn_usage(path, since_ms=1000).requests]


def test_parent_foreign_agent_is_excluded_child_wire_is_counted_once(tmp_path):
    path = wire(tmp_path, [row(), row(agentId='child')])
    child = wire(tmp_path, [row(agentId='child')], 'child')
    assert len(offline(path)) == 1
    observed = read_kimi_turn_usage(path, since_ms=1000)
    assert observed.turn['total_tokens'] == 66
    assert len({r.request_id for r in observed.requests}) == len(offline(path) + offline(child)) == 2


def test_time_slice_does_not_change_event_identity(tmp_path):
    path = wire(tmp_path, [row(time=999), row(), row(time=1002)])
    all_ids = [r.request_id for r in read_kimi_turn_usage(path, since_ms=0).requests]
    sliced = read_kimi_turn_usage(path, since_ms=1000, until_ms=1001)
    assert [r.request_id for r in sliced.requests] == all_ids[1:2]


def test_explicit_request_replay_is_not_added_twice(tmp_path):
    path = wire(tmp_path, [row(requestId='r'), row(requestId='r')])
    observed = read_kimi_turn_usage(path, since_ms=1000)
    assert len(observed.requests) == 1
    assert observed.turn['total_tokens'] == 33
