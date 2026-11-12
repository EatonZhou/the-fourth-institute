import json
import os

import pytest

from scripts.provider_usage_incremental import IncrementalCache
from scripts.provider_weekly_usage import build_report, moment, SOURCES

START, END = '2026-11-01T00:00:00Z', '2026-11-08T00:00:00Z'


def request(n):
    return {'request_id': str(n), 'occurred_at': START, 'model': 'm', 'usage': {'total_tokens': n}}


def append(path, row, newline=True):
    with path.open('a') as f:
        f.write(json.dumps(row) + ('\n' if newline else ''))


def run(cache, path, fmt='requests'):
    return build_report({'accounts': [{'provider': 'p', 'account_id': 'a', 'sources': [
        {'path': str(path), 'format': fmt, 'session_id': 's'}]}]}, START, END, cache=cache)['accounts'][0]


def test_unchanged_append_partial_and_restart(tmp_path):
    p = tmp_path / 'raw.jsonl'; append(p, request(10))
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True)
    assert run(cache, p)['totals']['total_tokens'] == 10
    assert run(cache, p)['totals']['total_tokens'] == 10
    assert cache.stats['cache_hits'] == 1
    before = cache.stats['jsonl_bytes_read']
    append(p, request(20))
    assert run(cache, p)['totals']['total_tokens'] == 30
    assert cache.stats['appended_files'] == 1
    assert cache.stats['jsonl_bytes_read'] - before == len((json.dumps(request(20))+'\n').encode())
    append(p, request(30), newline=False)
    assert run(cache, p)['totals']['total_tokens'] == 30
    cache.close()
    with p.open('a') as f: f.write('\n')
    cache = IncrementalCache(tmp_path / 'cache')
    result = run(cache, p)
    assert result['totals']['total_tokens'] == 60
    assert 'pending_partial_json_line' not in result['gaps']
    cache.close()


@pytest.mark.parametrize('change', ['truncate', 'replace', 'rewrite'])
def test_changed_prefix_does_not_keep_old_events(tmp_path, change):
    p = tmp_path / 'raw.jsonl'; append(p, request(10)); append(p, request(20))
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True); run(cache, p)
    if change == 'replace':
        other = tmp_path / 'other'; append(other, request(40)); os.replace(other, p)
    else:
        p.write_text(json.dumps(request(40 if change == 'truncate' else 99))+'\n')
    assert run(cache, p)['totals']['total_tokens'] in (40, 99)
    cache.close()


def test_codex_model_and_identity_survive_append(tmp_path):
    p = tmp_path / 'raw.jsonl'
    append(p, {'type': 'session_meta', 'timestamp': START, 'payload': {'id': 's'}})
    append(p, {'type': 'turn_context', 'payload': {'model': 'actual-model'}})
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True); run(cache, p, 'codex')
    append(p, {'type': 'event_msg', 'timestamp': START, 'payload': {'type': 'token_count',
        'info': {'last_token_usage': {'input_tokens': 10}, 'total_token_usage': {'input_tokens': 10}}}})
    out = run(cache, p, 'codex')
    assert out['totals']['total_tokens'] == 10
    assert out['buckets'][0]['model'] == 'actual-model'
    assert run(cache, p, 'codex')['requests'] == 1
    cache.close()


def test_kimi_identical_occurrences_survive_append(tmp_path):
    p = tmp_path / 'wire.jsonl'
    row = {'type': 'usage.record', 'time': moment(START).timestamp()*1000,
           'model': 'kimi', 'usage': {'inputOther': 10}}
    append(p, row)
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True); run(cache, p, 'kimi')
    append(p, row)
    assert run(cache, p, 'kimi')['totals']['total_tokens'] == 20
    cache.close()


def test_rebuild_and_failed_refresh_rollback(tmp_path):
    p = tmp_path / 'raw.jsonl'; append(p, request(10))
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True); run(cache, p)
    append(p, request(20))
    class Failed:
        def __init__(self, *a): pass
        def read(self): raise ValueError('parse failure')
    src = {'path': str(p), 'format': 'requests', 'session_id': 's', 'execution_session_id': 's'}
    with pytest.raises(ValueError):
        list(cache.read(src, Failed, set(), moment(START), moment(END)))
    assert run(cache, p)['totals']['total_tokens'] == 30
    cache.close()
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True)
    assert run(cache, p)['totals']['total_tokens'] == 30
    assert cache.stats['raw_files_read'] == 1
    cache.close()


def test_changed_sqlite_wal_invalidates_file_cache(tmp_path):
    import sqlite3
    p = tmp_path / 'native.db'
    c = sqlite3.connect(p)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE gen_metadata(idx INTEGER,data BLOB)'); c.commit()
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True); run(cache, p, 'antigravity')
    run(cache, p, 'antigravity'); assert cache.stats['cache_hits'] == 1
    c.execute('INSERT INTO gen_metadata VALUES(1, ?)', (b'',)); c.commit()
    run(cache, p, 'antigravity'); assert cache.stats['raw_files_read'] == 2
    cache.close(); c.close()


def test_default_never_bootstraps_or_rebuilds_silently(tmp_path):
    with pytest.raises(ValueError, match='--rebuild'):
        IncrementalCache(tmp_path / 'cache')
    cache = IncrementalCache(tmp_path / 'cache', rebuild=True)
    cache.db.execute("UPDATE version SET value='obsolete'"); cache.db.commit(); cache.close()
    with pytest.raises(ValueError, match='--rebuild'):
        IncrementalCache(tmp_path / 'cache')


def test_cli_missing_index_stops_before_reading_manifest(tmp_path, capsys):
    from scripts.provider_weekly_usage import main
    with pytest.raises(SystemExit) as error:
        main(['--manifest', str(tmp_path / 'nonexistent.json'), '--cache-root', str(tmp_path / 'cache'),
              '--since', START, '--until', END])
    assert error.value.code == 2
    assert '--rebuild' in capsys.readouterr().err
