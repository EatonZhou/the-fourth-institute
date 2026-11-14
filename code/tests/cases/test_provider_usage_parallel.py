"""Parallel preparation preserves serial event semantics and atomic checkpoints."""
import json

import pytest

from scripts.provider_usage_incremental import IncrementalCache
from scripts.provider_usage_parallel import ParallelIncrementalCache, parse_source
from scripts.provider_weekly_usage import build_report


START, END = '2026-11-01T00:00:00Z', '2026-11-08T00:00:00Z'


def append(path, n, newline=True):
    with path.open('a') as stream:
        stream.write(json.dumps({'request_id': str(n), 'occurred_at': START,
                                'model': 'm', 'usage': {'total_tokens': n}}) + ('\n' if newline else ''))


def manifest(paths):
    return {'accounts': [{'provider': 'p', 'account_id': 'a', 'sources': [
        {'format': 'requests', 'path': str(p), 'session_id': p.stem} for p in paths]}]}


def report(cache, paths):
    return build_report(manifest(paths), START, END, cache=cache)['accounts'][0]


def test_parallel_matches_serial_then_reuses_existing_index(tmp_path):
    paths = [tmp_path / f'{i}.jsonl' for i in range(8)]
    for i, path in enumerate(paths):
        append(path, i + 1)
    with_cache = IncrementalCache(tmp_path / 'index', rebuild=True)
    expected = report(with_cache, paths)
    with_cache.close()
    parallel = ParallelIncrementalCache(tmp_path / 'index', workers=2)
    try:
        assert report(parallel, paths) == expected
        assert parallel._pool is None
        assert parallel.stats['cache_hits'] == len(paths)
        for path in paths:
            append(path, 20)
        actual = report(parallel, paths)
        assert parallel._pool is not None
        assert parallel.stats['appended_files'] == len(paths)
        serial = IncrementalCache(tmp_path / 'serial', rebuild=True)
        try:
            assert actual == report(serial, paths)
        finally:
            serial.close()
    finally:
        parallel.close()


def test_partial_line_restart_and_append_offsets(tmp_path):
    path = tmp_path / 'native.jsonl'
    append(path, 10)
    cache = ParallelIncrementalCache(tmp_path / 'index', rebuild=True, workers=2)
    try:
        assert report(cache, [path])['totals']['total_tokens'] == 10
        before = cache.stats['jsonl_bytes_read']
        append(path, 20, newline=False)
        result = report(cache, [path])
        assert result['totals']['total_tokens'] == 10
        assert 'pending_partial_json_line' in result['gaps']
        assert cache.stats['jsonl_bytes_read'] - before < path.stat().st_size
    finally:
        cache.close()
    with path.open('a') as stream:
        stream.write('\n')
    cache = ParallelIncrementalCache(tmp_path / 'index', workers=2)
    try:
        result = report(cache, [path])
        assert result['totals']['total_tokens'] == 30
        assert 'pending_partial_json_line' not in result['gaps']
    finally:
        cache.close()


@pytest.mark.parametrize('change', ['truncate', 'replace', 'rewrite'])
def test_non_append_change_replaces_instead_of_doubling(tmp_path, change):
    path = tmp_path / 'native.jsonl'
    append(path, 10)
    cache = ParallelIncrementalCache(tmp_path / 'index', rebuild=True, workers=2)
    try:
        report(cache, [path])
        if change == 'replace':
            other = tmp_path / 'other'
            append(other, 99)
            other.replace(path)
        else:
            path.write_text('')
            append(path, 99 if change == 'rewrite' else 1)
        result = report(cache, [path])
        assert result['totals']['total_tokens'] == (99 if change != 'truncate' else 1)
    finally:
        cache.close()


def test_changed_file_or_checkpoint_cannot_commit_stale_result(tmp_path):
    path = tmp_path / 'native.jsonl'
    append(path, 10)
    cache = ParallelIncrementalCache(tmp_path / 'index', rebuild=True, workers=2)
    source = {'format': 'requests', 'path': str(path), 'session_id': 's'}
    try:
        job, _ = cache._job(source)
        parsed = parse_source(job)
        cache._commit(job, parsed)
        with pytest.raises(OSError, match='checkpoint changed'):
            cache._commit(job, parsed)
        append(path, 20)
        job, _ = cache._job(source)
        parsed = parse_source(job)
        append(path, 30)
        with pytest.raises(OSError, match='checkpoint changed'):
            cache._commit(job, parsed)
        assert cache.db.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        with pytest.raises(OSError, match='before parallel'):
            parse_source(job)
    finally:
        cache.close()


def test_source_failure_keeps_checkpoint_and_does_not_hide_good_source(tmp_path):
    bad, good = tmp_path / 'bad.jsonl', tmp_path / 'good.jsonl'
    append(bad, 10)
    append(good, 20)
    cache = ParallelIncrementalCache(tmp_path / 'index', rebuild=True, workers=2)
    try:
        report(cache, [bad, good])
        with bad.open('ab') as stream:
            stream.write(b'{"usage":"\xff"}\n')
        append(good, 30)
        result = report(cache, [bad, good])
        assert result['totals']['total_tokens'] == 50
        assert result['source_failures'] == {'requests:UnicodeDecodeError': 1}
        assert cache.db.execute('SELECT count(*) FROM events').fetchone()[0] == 3
    finally:
        cache.close()


def test_missing_source_is_reported_and_bad_worker_counts_rejected(tmp_path):
    cache = ParallelIncrementalCache(tmp_path / 'index', rebuild=True, workers=2)
    try:
        assert report(cache, [tmp_path / 'missing'])['source_failures'] == {'requests:FileNotFoundError': 1}
        assert cache._pool is None
    finally:
        cache.close()
    for workers in (0, 17, True):
        with pytest.raises(ValueError, match='workers'):
            ParallelIncrementalCache(tmp_path / 'unused', workers=workers)


@pytest.mark.parametrize('fmt', ['codex', 'kimi', 'claude'])
def test_native_parser_state_and_duplicate_semantics_survive_append(tmp_path, fmt):
    path = tmp_path / 'wire.jsonl'
    header = [{'type': 'session_meta', 'timestamp': START, 'payload': {'id': 's'}},
              {'type': 'turn_context', 'payload': {'model': 'm'}}] if fmt == 'codex' else []
    row = {
        'codex': {'type': 'event_msg', 'timestamp': START, 'payload': {'type': 'token_count',
                  'info': {'last_token_usage': {'input_tokens': 10}, 'total_token_usage': {'input_tokens': 10}}}},
        'kimi': {'type': 'usage.record', 'time': 1793491200000, 'model': 'm', 'usage': {'inputOther': 10}},
        'claude': {'type': 'assistant', 'timestamp': START, 'sessionId': 's',
                   'message': {'id': 'r', 'model': 'm', 'usage': {'input_tokens': 10}}},
    }[fmt]
    path.write_text(''.join(json.dumps(r) + '\n' for r in header + [row]))
    data = {'accounts': [{'provider': fmt, 'account_id': 'a', 'sources': [
        {'format': fmt, 'path': str(path), 'session_id': 's'}]}]}
    cache = ParallelIncrementalCache(tmp_path / 'index', rebuild=True, workers=2)
    try:
        build_report(data, START, END, cache=cache)
        with path.open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        actual = build_report(data, START, END, cache=cache)['accounts'][0]
        assert actual == build_report(data, START, END)['accounts'][0]
        assert actual['requests'] == (2 if fmt == 'kimi' else 1)
        assert cache.stats['appended_files'] == 1
    finally:
        cache.close()


def test_sqlite_wal_invalidates_parallel_source(tmp_path):
    import sqlite3
    path = tmp_path / 'native.db'
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE gen_metadata(idx INTEGER,data BLOB)')
    connection.commit()
    data = {'accounts': [{'provider': 'antigravity', 'account_id': 'a', 'sources': [
        {'format': 'antigravity', 'path': str(path), 'session_id': 's'}]}]}
    cache = ParallelIncrementalCache(tmp_path / 'index', rebuild=True, workers=2)
    try:
        build_report(data, START, END, cache=cache)
        build_report(data, START, END, cache=cache)
        assert cache.stats['cache_hits'] == 1
        connection.execute('INSERT INTO gen_metadata VALUES(1, ?)', (b'',))
        connection.commit()
        result = build_report(data, START, END, cache=cache)['accounts'][0]
        assert result['source_failures'] == {}
        assert cache.stats['raw_files_read'] == 2
    finally:
        cache.close()
        connection.close()


def test_failed_batch_write_rolls_back_old_events_and_checkpoint(tmp_path):
    import sqlite3
    path = tmp_path / 'native.jsonl'
    append(path, 10)
    cache = ParallelIncrementalCache(tmp_path / 'index', rebuild=True, workers=2)
    source = {'format': 'requests', 'path': str(path), 'session_id': 's'}
    try:
        job, _ = cache._job(source)
        cache._commit(job, parse_source(job))
        checkpoint = cache.db.execute('SELECT * FROM sources').fetchall()
        events = cache.db.execute('SELECT * FROM events').fetchall()
        path.write_text('')
        append(path, 99)
        job, _ = cache._job(source)
        parsed = parse_source(job)
        cache.db.execute('''CREATE TRIGGER reject_event BEFORE INSERT ON events
                            BEGIN SELECT RAISE(ABORT, 'test write failure'); END''')
        cache.db.commit()
        with pytest.raises(sqlite3.Error):
            cache._commit(job, parsed)
        assert cache.db.execute('SELECT * FROM sources').fetchall() == checkpoint
        assert cache.db.execute('SELECT * FROM events').fetchall() == events
    finally:
        cache.close()


def test_cli_defaults_to_parallel_and_validates_worker_count(tmp_path, capsys):
    from scripts.provider_weekly_usage import main
    path = tmp_path / 'native.jsonl'
    append(path, 10)
    data = tmp_path / 'manifest.json'
    data.write_text(json.dumps(manifest([path])))
    args = ['--manifest', str(data), '--since', START, '--until', END,
            '--cache-root', str(tmp_path / 'index'), '--rebuild']
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['accounts'][0]['totals']['total_tokens'] == 10
    assert result['incremental']['raw_files_read'] == 1
    with pytest.raises(SystemExit) as error:
        main(args + ['--workers', '17'])
    assert error.value.code == 2
