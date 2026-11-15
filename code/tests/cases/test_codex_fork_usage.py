"""Fork inheritance is a native turn identity relation, never a clock window."""
import json
from pathlib import Path
import pytest
from scripts.provider_weekly_usage import SOURCES, build_report
from scripts.provider_usage_incremental import IncrementalCache
from scripts.provider_usage_parallel import ParallelIncrementalCache

START, END = '2026-11-01T00:00:00Z', '2026-11-02T00:00:00Z'


def write(path, rows, mode='w'):
    with path.open(mode) as f:
        for r in rows:
            f.write(json.dumps(r)+'\n')


def turn(identity):
    return {'type': 'turn_context', 'payload': {'turn_id': identity, 'model': 'gpt'}}


def usage(at=START, total=10):
    return {'timestamp': at, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'last_token_usage': {'input_tokens': 10}, 'total_token_usage': {'input_tokens': total}}}}


def setup(tmp_path):
    parent, child = tmp_path / 'parent.jsonl', tmp_path / 'child.jsonl'
    write(parent, [{'type': 'session_meta', 'payload': {'id': 'parent'}}, turn('old'), usage()])
    write(child, [{'timestamp': START, 'type': 'session_meta', 'payload': {'id': 'child', 'forked_from_id': 'parent'}},
                  {'type': 'session_meta', 'payload': {'id': 'parent'}}, turn('old'),
                  usage('2026-11-01T00:00:30Z'), turn('new'), usage(START, 20)])
    return {'format': 'codex', 'path': str(child), 'session_id': 'child', 'fork_parent_path': str(parent)}


def report(src, cache=None):
    return build_report({'accounts': [{'provider': 'codex', 'account_id': 'a', 'sources': [src]}]},
                        START, END, cache=cache)['accounts'][0]


def test_delayed_import_excluded_but_immediate_new_usage_counted(tmp_path):
    result = report(setup(tmp_path))
    assert result['requests'] == 1
    assert result['totals']['input_tokens'] == 10
    assert 'fork_import_records_excluded' in result['gaps']


@pytest.mark.parametrize('cache_type', [IncrementalCache, ParallelIncrementalCache])
def test_append_restart_and_replay_keep_turn_ownership(tmp_path, cache_type):
    src = setup(tmp_path)
    cache = cache_type(tmp_path / 'cache', rebuild=True)
    assert report(src, cache)['requests'] == 1
    cache.close()
    write(Path(src['path']), [usage(START, 30)], 'a')
    cache = cache_type(tmp_path / 'cache')
    assert report(src, cache)['requests'] == 2
    assert cache.stats['appended_files'] == 1
    assert report(src, cache)['requests'] == 2
    cache.close()


@pytest.mark.parametrize('broken', ['missing', 'wrong_parent', 'no_turn'])
def test_missing_parent_evidence_does_not_become_measured_zero(tmp_path, broken):
    src = setup(tmp_path)
    parent = Path(src['fork_parent_path'])
    if broken == 'missing':
        parent.unlink()
    elif broken == 'wrong_parent':
        write(parent, [{'type': 'session_meta', 'payload': {'id': 'someone-else'}}])
    else:
        write(parent, [{'type': 'session_meta', 'payload': {'id': 'parent'}}, usage()])
    gaps = set()
    with pytest.raises((OSError, ValueError)):
        list(SOURCES['codex'](src, gaps).read())
    assert 'fork_parent_history_unavailable_or_invalid' in gaps


def test_unknown_child_turn_is_explicitly_excluded(tmp_path):
    src = setup(tmp_path)
    write(Path(src['path']), [turn(''), usage(START, 40)], 'a')
    result = report(src)
    assert result['requests'] == 1
    assert 'fork_request_without_turn_identity_excluded' in result['gaps']


def test_discovery_includes_archives_and_recovers_after_parent_move(tmp_path):
    from scripts.provider_usage_codex_fork import inherited_turns
    parent_id = '12345678-1234-4234-8234-123456789abc'
    home = tmp_path / 'home'
    sessions = home / 'sessions'; sessions.mkdir(parents=True)
    archive = home / 'archived_sessions'; archive.mkdir()
    parent = sessions / f'rollout-{parent_id}.jsonl'
    write(parent, [{'type': 'session_meta', 'payload': {'id': parent_id}}, turn('old')])
    src = {'path': str(sessions / 'child.jsonl')}
    assert inherited_turns(src, parent_id) == ('old',)
    parent.rename(archive / parent.name)
    assert inherited_turns(src, parent_id) == ('old',)


def test_missing_parent_gap_survives_incremental_reader_failure(tmp_path):
    src = setup(tmp_path)
    Path(src['fork_parent_path']).unlink()
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True)
    result = report(src, cache)
    assert 'fork_parent_history_unavailable_or_invalid' in result['gaps']
    assert 'source_unreadable_or_invalid:codex' in result['gaps']
    cache.close()


def test_parent_quota_only_startup_does_not_require_request_turn(tmp_path):
    from scripts.provider_usage_codex_fork import inherited_turns
    src = setup(tmp_path)
    parent = Path(src['fork_parent_path'])
    write(parent, [{'type': 'session_meta', 'payload': {'id': 'parent'}},
                   {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None}}, turn('old'), usage()])
    assert inherited_turns(src, 'parent') == ('old',)
