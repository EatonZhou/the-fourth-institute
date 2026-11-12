import json
import sqlite3
import os

from scripts.provider_weekly_usage import build_report
from scripts.provider_usage_native import expand_sources, quota_rows

START = '2026-11-01T00:00:00Z'
END = '2026-11-08T00:00:00Z'
MS = 1793577600000


def report(sources, **extras):
    return build_report({'accounts': [{'provider': 'p', 'account_id': 'a', 'sources': sources, **extras}]}, START, END)['accounts'][0]


def varint(n):
    out = b''
    while n >= 128:
        out += bytes([(n & 127) | 128]); n >>= 7
    return out + bytes([n])


def pb(field, value):
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return varint(field * 8 + 2) + varint(len(value)) + value
    return varint(field * 8) + varint(value)


def test_antigravity_verified_model_time_tokens_and_id(tmp_path):
    p = tmp_path / 'native.db'
    usage = pb(2, 10) + pb(3, 5) + pb(5, 20) + pb(9, 2) + pb(11, 'response')
    chat = pb(4, usage) + pb(9, pb(4, pb(1, MS // 1000))) + pb(19, 'gemini-response') + pb(21, 'DO-NOT-USE-DISPLAY-NAME')
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE gen_metadata(idx INTEGER,data BLOB)')
        c.execute('INSERT INTO gen_metadata VALUES(?,?)', (1, pb(1, chat)))
        c.execute('INSERT INTO gen_metadata VALUES(?,?)', (2, pb(1, chat)))
    result = report([{'format': 'antigravity', 'path': str(p)}])
    assert result['requests'] == 1  # Repeated provider response ID.
    assert result['totals']['total_tokens'] == 35
    assert result['totals']['reasoning_output_tokens'] == 2
    assert result['buckets'][0]['model'] == 'gemini-response'


def test_antigravity_missing_response_model_is_unknown_not_display_model(tmp_path):
    p = tmp_path / 'native.db'
    chat = pb(4, pb(2, 10)) + pb(9, pb(4, pb(1, MS // 1000))) + pb(21, 'display')
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE gen_metadata(idx INTEGER,data BLOB)')
        c.execute('INSERT INTO gen_metadata VALUES(?,?)', (1, pb(1, chat)))
    result = report([{'format': 'antigravity', 'path': str(p)}])
    assert result['buckets'][0]['model'] == 'unknown'
    assert 'antigravity_missing_response_model' in result['gaps']


def test_antigravity_empty_database_is_not_a_schema_failure(tmp_path):
    p = tmp_path / 'empty.db'
    sqlite3.connect(p).close()
    result = report([{'format': 'antigravity', 'path': str(p)}])
    assert 'antigravity_empty_database' in result['gaps']
    assert result['source_failures'] == {}


def test_opencode_sqlite_uses_steps_once_and_follows_children(tmp_path):
    p = tmp_path / 'opencode.db'
    with sqlite3.connect(p) as c:
        c.executescript('''CREATE TABLE session(id TEXT,parent_id TEXT);
            CREATE TABLE message(id TEXT,session_id TEXT,time_created INTEGER,data TEXT);
            CREATE TABLE part(id TEXT,message_id TEXT,time_created INTEGER,data TEXT);''')
        c.executemany('INSERT INTO session VALUES(?,?)', [('s', None), ('child', 's'), ('other', None)])
        for i, sid in enumerate(('s', 'child', 'other')):
            c.execute('INSERT INTO message VALUES(?,?,?,?)', (str(i), sid, MS, json.dumps({
                'role': 'assistant', 'modelID': 'model-' + sid, 'tokens': {'input': 999}})))
            c.execute('INSERT INTO part VALUES(?,?,?,?)', ('p' + str(i), str(i), MS, json.dumps({
                'type': 'step-finish', 'tokens': {'input': 10, 'output': 5, 'reasoning': 2, 'cache': {'read': 20}}})))
    result = report([{'format': 'opencode_sqlite', 'path': str(p), 'native_session_id': 's'}])
    assert result['requests'] == 2
    assert result['totals']['total_tokens'] == 74
    assert {b['model'] for b in result['buckets']} == {'model-s', 'model-child'}


def test_kimi_main_discovers_child_and_keeps_same_parent_provenance(tmp_path):
    root = tmp_path / 'session' / 'agents'
    for name in ('main', 'child'):
        (root / name).mkdir(parents=True)
        (root / name / 'wire.jsonl').write_text(json.dumps({'type': 'usage.record', 'time': MS,
            'agentId': name, 'model': 'kimi', 'usage': {'inputOther': 10}}) + '\n')
    result = report([{'format': 'kimi', 'path': str(root / 'main/wire.jsonl'), 'session_id': 's'}],
                    resident_records=[{'session_id': 's', 'started_at': START, 'finished_at': END, 'usage': {'total_tokens': 20}}])
    assert result['requests'] == 2
    assert result['resident_comparisons'][0]['difference'] == 0


def test_claude_and_dsh_child_discovery_and_missing_child(tmp_path):
    main = tmp_path / 'main.jsonl'
    main.write_text('')
    child = tmp_path / 'main' / 'subagents' / 'child.jsonl'
    child.parent.mkdir(parents=True); child.write_text('')
    assert len(list(expand_sources([{'format': 'claude', 'path': str(main)}], set()))) == 2
    main.write_text(json.dumps({'type': 'subagent/catalog', 'data': {'childId': 'missing'}}))
    gaps = set()
    list(expand_sources([{'format': 'dsh', 'path': str(main)}], gaps))
    assert 'referenced_child_log_missing' in gaps


def test_existing_snapshot_and_monitor_history_are_normalized():
    snapshot = {'observed_at': START, 'accounts': [{'account_id': 'a', 'rate_limits': [
        {'id': 'weekly', 'window_duration_mins': 10080, 'resets_at': END}]}]}
    assert list(quota_rows(snapshot))[0]['at'] == START
    history = [{'account_id': 'a', 'finished_at': END, 'sample': {'rate_limits': [
        {'observed_at': START, 'resets_at': END, 'window_duration_mins': 10080}]}}]
    assert list(quota_rows(history))[0]['at'] == START
    legacy = {'observed_at': START, 'accounts': [{'account_id': 'a', 'rate_limits': {
        'primary': {'used_percent': 10, 'resets_at': END, 'window_duration_mins': 10080},
        'by_limit_id': {'codex': {'primary': {'used_percent': 10, 'resets_at': END, 'window_duration_mins': 10080}}}}}]}
    normalized = list(quota_rows(legacy))
    assert len(normalized) == 2
    assert normalized[1]['quota_group'] == 'codex'
    assert normalized[1]['remaining_percent'] == 90


def test_cycle_bound_uses_token_volume_and_does_not_certify_partial_query(tmp_path):
    p = tmp_path / 'requests.jsonl'
    p.write_text(json.dumps({'request_id': 'r', 'occurred_at': '2026-11-04T00:00:00Z',
                            'model': 'm', 'usage': {'total_tokens': 100}}) + '\n')
    q = [{'at': at, 'resets_at': reset, 'remaining_percent': remaining, 'window_duration_mins': 10080}
         for at, reset, remaining in [
            ('2026-11-01T00:00:00Z', '2026-11-08T00:00:00Z', 0),
            ('2026-11-02T00:00:00Z', '2026-11-09T00:00:00Z', 100),
            ('2026-11-06T00:00:00Z', '2026-11-09T00:00:00Z', 0),
            ('2026-11-07T00:00:00Z', '2026-11-14T00:00:00Z', 100)]]
    result = report([{'format': 'requests', 'path': str(p)}], quota_observations=q)
    bound = result['cycle_bounds'][0]
    assert bound['lower_tokens'] == bound['upper_tokens'] == 100
    assert bound['within_10_percent'] is True
    assert bound['models']['m']['lower_tokens'] == 100
    assert bound['whole_account_error_bound'] is None
    assert bound['uncertainty_scope'] == 'inferred_boundary_timing_only'


def test_symlinks_and_duplicate_paths_not_scanned_twice(tmp_path):
    p = tmp_path / 'wire.jsonl'; p.write_text('')
    link = tmp_path / 'link.jsonl'; link.symlink_to(p)
    gaps = set()
    sources = [{'format': 'requests', 'path': str(x)} for x in (p, p, link)]
    assert len(list(expand_sources(sources, gaps))) == 1
    assert 'symlink_source_excluded' in gaps


def test_old_file_mtime_does_not_hide_new_usage(tmp_path):
    p = tmp_path / 'requests.jsonl'
    p.write_text(json.dumps({'request_id': 'r', 'occurred_at': START, 'model': 'm',
                            'usage': {'total_tokens': 12}}) + '\n')
    os.utime(p, (0, 0))
    assert report([{'format': 'requests', 'path': str(p)}])['totals']['total_tokens'] == 12


def test_expired_latest_quota_does_not_revive_an_older_endpoint(tmp_path):
    p = tmp_path / 'requests.jsonl'
    p.write_text(json.dumps({'request_id': 'r', 'occurred_at': '2026-11-05T00:00:00Z',
                            'model': 'm', 'usage': {'total_tokens': 12}}) + '\n')
    q = [{'at': START, 'resets_at': END, 'window_duration_mins': 10080},
         {'at': '2026-11-02T00:00:00Z', 'resets_at': '2026-11-03T00:00:00Z', 'window_duration_mins': 10080}]
    assert report([{'format': 'requests', 'path': str(p)}], quota_observations=q)['buckets'][0]['period_kind'] == 'calendar_week'


def test_local_manifest_joins_historical_route_and_reads_direct_api_results(tmp_path, monkeypatch):
    from scripts import provider_usage_local as local
    from scripts.provider_weekly_usage import moment
    monkeypatch.setattr(local, 'portal_path', lambda name: tmp_path / name)
    root = tmp_path / 'account'
    log = root / 'sessions' / 'native.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'native'}}) + '\n')
    dsh = tmp_path / 'provider-state/dsh/dsh-123/sessions/user/dsh-123/session.v1.jsonl'
    dsh.parent.mkdir(parents=True); dsh.write_text('')
    accounts = [
        {'provider': 'codex', 'account_id': 'physical', 'backend_route_id': 'old',
         'registration': {'config': {'account_dir': str(root)}}},
        {'provider': 'codex', 'account_id': 'historic', 'backend_route_id': 'actual'},
        {'provider': 'deepseek_primary', 'account_id': 'api', 'backend_route_id': 'deepseek_primary'},
        {'provider': 'dsh', 'account_id': 'dsh', 'backend_route_id': 'dsh'}]
    start, end = moment(START), moment(END)
    executions = [{'provider': 'deepseek_primary', 'execution_id': 'exec', 'backend_session_id': '',
                   'started_at': start, 'finished_at': end,
                   'usage_json': {'api_id': 'api', 'model': 'served', 'prompt_tokens': 10, 'completion_tokens': 5,
                                  'prompt_cache_hit_tokens': 4, 'accounted': {'total_tokens': 15}}}]
    manifest = local.local_manifest(accounts, start, end, evidence_reader=lambda *_: (
        [{'provider': 'codex', 'backend_session_id': 'native', 'backend_route_id': 'actual'},
         {'provider': 'dsh', 'backend_session_id': 'dsh-123', 'backend_route_id': 'dsh'}], executions, []))
    by_id = {a['account_id']: a for a in manifest['accounts']}
    assert len(by_id['historic']['sources']) == 1
    assert not by_id['physical']['sources']
    assert len(by_id['dsh']['sources']) == 1
    result = build_report(manifest, START, END)['accounts'][2]
    assert result['totals'] == {}  # Completion at the half-open endpoint is excluded.
    executions[0]['finished_at'] = moment('2026-11-07T00:00:00Z')
    manifest = local.local_manifest(accounts, start, end, evidence_reader=lambda *_: ([], executions, []))
    result = build_report(manifest, START, END)['accounts'][2]
    assert result['totals']['total_tokens'] == 15
    assert result['totals']['cached_input_tokens'] == 4
    assert result['buckets'][0]['model'] == 'served'
    assert result['resident_comparisons'][0]['difference'] == 0


def test_unregistered_codex_home_of_the_same_login_joins_that_account(tmp_path, monkeypatch):
    """宿主用户自己的 ~/.codex 登录了同一个 ChatGPT 账号时，它的会话并入该账号；其他登录不并入。"""
    from scripts import provider_usage_local as local
    from scripts.provider_weekly_usage import moment
    monkeypatch.setattr(local, 'portal_path', lambda name: tmp_path / name)
    def home(name, login):
        root = tmp_path / name
        (root / 'sessions').mkdir(parents=True)
        (root / 'auth.json').write_text(json.dumps({'tokens': {'account_id': login, 'access_token': 'SECRET'}}))
        (root / 'sessions' / f'{name}.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': name}}) + '\n')
        return root
    registered, own, other = home('registered', 'acct-1'), home('own', 'acct-1'), home('other', 'acct-2')
    monkeypatch.setattr(local, 'unregistered_codex_homes', lambda: [own, other, registered])
    accounts = [{'provider': 'codex', 'account_id': 'codex_a', 'backend_route_id': 'a',
                 'registration': {'config': {'account_dir': str(registered)}}}]
    m = local.local_manifest(accounts, moment(START), moment(END), evidence_reader=lambda *_: ([], [], []))
    [entry] = [a for a in m['accounts'] if a['account_id'] == 'codex_a']
    assert sorted(s['session_id'] for s in entry['sources']) == ['own', 'registered']
    assert entry['extra_codex_homes'] == [str(own)]
    assert 'SECRET' not in json.dumps(m)


def test_local_manifest_includes_native_profile_home_and_retained_runtime_dirs(tmp_path, monkeypatch):
    from scripts import provider_usage_local as local
    from management_common import provider_account_paths
    from scripts.provider_weekly_usage import moment
    monkeypatch.setattr(local, 'portal_path', lambda name: tmp_path / name)
    monkeypatch.setattr(provider_account_paths, 'registered_home', lambda _: tmp_path / 'profile')
    kimi = tmp_path / 'profile/sessions/wd/session/agents/main/wire.jsonl'
    kimi.parent.mkdir(parents=True); kimi.write_text('')
    claude = tmp_path / 'runtime/claude/config/projects/project/native.jsonl'
    claude.parent.mkdir(parents=True); claude.write_text('')
    accounts = [{'provider': 'kimi', 'account_id': 'api', 'backend_route_id': 'api',
                 'registration': {'kind': 'api_profile'}},
                {'provider': 'claude', 'account_id': 'c', 'backend_route_id': 'c'}]
    sessions = [{'provider': 'claude', 'backend_session_id': 'native', 'backend_route_id': 'c',
                 'config_dir': str(claude.parents[2])}]
    m = local.local_manifest(accounts, moment(START), moment(END), evidence_reader=lambda *_: (sessions, [], []))
    assert len(m['accounts'][0]['sources']) == len(m['accounts'][1]['sources']) == 1
