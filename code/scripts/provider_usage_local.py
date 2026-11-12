"""Local service/operator mode: read installed native records and historical provenance.

Not a public endpoint. The caller must already have local access to the installed
instance and selected account directories. No identity substitution or secrets
are exported. All queries use a read-only transaction.
"""
import json
from pathlib import Path
from psycopg import Error as DatabaseError

from management_common.database_time import iso_time
from management_common.json_columns import json_value
from management_common.portal_database import portal_database, _database_schema
from management_common.portal_paths import path as portal_path
from management_portal.execution.scheduler_usage import normalize_token_usage
from scripts.provider_usage_native import record_path
from scripts.provider_usage_provenance import recover_native_sessions, recover_prompt_sessions, runtime_config_dirs


def codex_login_account(home):
    """ChatGPT account ID a Codex home is logged into, or None. Only the ID is read."""
    try:
        tokens = json.loads((Path(home) / 'auth.json').read_text()).get('tokens') or {}
    except (OSError, ValueError, AttributeError):
        return None
    account = tokens.get('account_id') if isinstance(tokens, dict) else None
    return str(account) if account else None


def unregistered_codex_homes():
    """Codex homes of the host user: $CODEX_HOME and ~/.codex, when present."""
    import os
    candidates = [os.environ.get('CODEX_HOME'), str(Path.home() / '.codex')]
    homes = []
    for value in candidates:
        if value and Path(value).is_dir() and Path(value) not in homes:
            homes.append(Path(value))
    return homes


def existing_evidence(since, until, *, include_home=True):
    database = portal_database(schema='agent')
    with database.connect() as c:
        c.execute('SET TRANSACTION READ ONLY')
        sessions = [dict(r) for r in c.execute('SELECT provider,backend_route_id,backend_session_id FROM provider_sessions')]
        runtime_schema = _database_schema('execution_runtime')
        exists = c.execute('''SELECT 1 FROM information_schema.tables
            WHERE table_schema=%s AND table_name='executions' ''', (runtime_schema,)).fetchone()
        if exists:
            from management_portal.execution.native_options import native_config_dir
            for r in c.execute(f'''SELECT DISTINCT p.provider,p.backend_route_id,p.backend_session_id,
                r.request_json::jsonb #>> '{{runtime_options,runtime_root}}' AS runtime_root,
                r.request_json::jsonb #>> '{{session,binding_id}}' AS binding_id
                FROM "{runtime_schema}".executions r JOIN executions e USING(execution_id)
                JOIN provider_sessions p USING(provider_session_ref) WHERE p.provider='claude' '''):
                sessions.append({**dict(r), 'config_dir': str(native_config_dir('claude', r['runtime_root'] or '', r['binding_id'] or ''))})
        executions = [dict(r) for r in c.execute('''SELECT e.execution_id,e.agent_id,e.provider,e.provider_turn_id,e.started_at,e.finished_at,e.usage_json,
            p.backend_route_id,p.backend_session_id FROM executions e
            LEFT JOIN provider_sessions p USING(provider_session_ref)
            WHERE e.started_at < %s AND (e.finished_at IS NULL OR e.finished_at > %s)''', (until, since))]
        recover_native_sessions(c, executions)
        if exists:
            # Only retained requests prove overrides/bindings; never guess an expired key.
            pending = {r['execution_id']: r for r in executions if r['provider'] == 'claude'
                       and not r.get('native_session_id') and (r.get('backend_session_id') or '').startswith('execution-')}
            if pending:
                from management_portal.claude_message_identity import turn_prompt_uuid
                for r in c.execute(f'''SELECT execution_id,
                    request_json::jsonb #>> '{{runtime_options,session_key}}' AS session_key,
                    request_json::jsonb #>> '{{session,binding_id}}' AS binding_id
                    FROM "{runtime_schema}".executions WHERE execution_id=ANY(%s)
                    AND request_json::jsonb ? 'execution_id' ''', (list(pending),)):
                    record = pending[r['execution_id']]
                    if record.get('provider_turn_id'):
                        record['native_prompt_id'] = turn_prompt_uuid(
                            r['session_key'] or r['binding_id'] or r['execution_id'], record['provider_turn_id'])
        schemas = {r['table_schema'] for r in c.execute('''SELECT table_schema FROM information_schema.tables
            WHERE table_name='observations' AND table_schema=%s''', (_database_schema('execution_account_observations'),))}
        quota = []
        if schemas and include_home:
            # Identifier comes only from the fixed internal schema normalizer.
            schema = _database_schema('execution_account_observations')
            for r in c.execute(f'''SELECT provider,account_id,finished_at,sample FROM "{schema}".observations
                WHERE finished_at >= %s AND finished_at <= %s''', (since, until)):
                from scripts.provider_usage_refresh import home_quota_records
                record = {'account_id': r['account_id'], 'finished_at': iso_time(r['finished_at']),
                          'sample': json_value(r['sample'])}
                quota.extend({'provider': r['provider'], **q} for q in home_quota_records([record]))
    return sessions, executions, quota


def local_manifest(accounts, since, until, evidence_reader=existing_evidence, cache=None, *, portal_evidence=True, home_evidence=True):
    provenance = {'status': 'disabled'}
    sessions, executions, quota = [], [], []
    if portal_evidence:
        try:
            sessions, executions, quota = (evidence_reader(since, until, include_home=home_evidence)
                                          if evidence_reader is existing_evidence else evidence_reader(since, until))
            provenance = {'status': 'available'}
        except (OSError, DatabaseError) as error:
            # A failed auxiliary projection must not hide retained native usage.
            provenance = {'status': 'unavailable', 'error_type': type(error).__name__}
    sessions = list(sessions) + [
        {'provider': r['provider'], 'backend_route_id': r.get('backend_route_id', ''),
         'backend_session_id': r['native_session_id']}
        for r in executions if r.get('native_session_id')]
    entries = {(a['provider'], a['account_id']): {
        'provider': a['provider'], 'account_id': a['account_id'], 'route_id': a.get('backend_route_id'),
        'account_kind': (a.get('registration') or {}).get('kind') or a.get('kind', ''),
        'sources': [], 'resident_records': [], 'quota_observations': [], 'session_aliases': {}}
        for a in accounts}
    routes = {(a['provider'], a.get('backend_route_id') or a['account_id']): (a['provider'], a['account_id']) for a in accounts}
    native = {}
    for s in sessions:
        key = (s['provider'], s['backend_session_id'])
        owner = routes.get((s['provider'], s['backend_route_id']))
        if owner:
            native.setdefault(key, set()).add(owner)

    def destination(provider, session='', default=None):
        owners = native.get((provider, session), set())
        if len(owners) == 1:
            return entries[next(iter(owners))]
        if not owners and default is not None:
            return entries[default]
        if not session and (provider, provider) in routes:
            return entries[routes[(provider, provider)]]
        key = (provider, 'unresolved-account')
        return entries.setdefault(key, {'provider': provider, 'account_id': key[1], 'sources': [],
                                        'resident_records': [], 'quota_observations': [], 'session_aliases': {}})

    def include(provider, p, fmt, session='', default=None, **extra):
        destination(provider, session, default)['sources'].append({'format': fmt, 'path': str(p),
                                                                  'session_id': session, **extra})

    def include_codex_home(root, default):
        for area in ('sessions', 'archived_sessions'):
            for p in (root / area).rglob('*.jsonl'):
                if cache:
                    include('codex', p, 'codex', cache.session_header(p), default)
                    continue
                sid = ''
                with p.open(encoding='utf-8') as stream:
                    try:
                        head = json.loads(stream.readline())
                        if head.get('type') == 'session_meta':
                            sid = head.get('payload', {}).get('id', '')
                    except ValueError:
                        pass
                include('codex', p, 'codex', sid, default)

    codex_homes = {}  # Login account -> registered account, to attach unregistered homes of the same login.
    for a in accounts:
        registration = a.get('registration') or {}
        root = registration.get('config', {}).get('account_dir')
        if registration.get('kind') == 'api_profile' and a['provider'] in ('codex', 'kimi'):
            from management_common.provider_account_paths import registered_home
            root = registered_home(registration)
        if not root:
            continue
        root = record_path(root)
        provider = a['provider']; default = (provider, a['account_id'])
        if provider == 'codex':
            include_codex_home(root, default)
            login = codex_login_account(root)
            if login:
                codex_homes.setdefault(login, (default, set()))[1].add(root.resolve())
        elif provider == 'claude':
            for p in (root / 'projects').glob('*/*.jsonl'):
                include(provider, p, 'claude', p.stem, default)
        elif provider == 'kimi':
            for p in (root / 'sessions').glob('*/*/agents/main/wire.jsonl'):
                include(provider, p, 'kimi', p.parents[2].name, default)
        elif provider == 'antigravity':
            for p in (root / 'antigravity-acp/conversations').glob('*.db'):
                include(provider, p, 'antigravity', p.stem, default)
    # A Codex home outside the registered account directories (e.g. the host user's own
    # ~/.codex) logged into the same ChatGPT account draws on the same weekly limit.
    for home in unregistered_codex_homes():
        login = codex_login_account(home)
        owner = codex_homes.get(login) if login else None
        if owner and home.resolve() not in owner[1]:
            owner[1].add(home.resolve())
            include_codex_home(home, owner[0])
            entries[owner[0]].setdefault('extra_codex_homes', []).append(str(home))
    state = portal_path('provider-state')
    config_dirs = {s['config_dir'] for s in sessions if s.get('config_dir')}
    config_dirs.update(runtime_config_dirs(portal_path('projects'), portal_path('lineages')))
    config_dirs.update(str(p) for p in (state / 'agent-config').glob('*/claude/config'))
    for directory in sorted(config_dirs):
        for p in (record_path(directory) / 'projects').glob('*/*.jsonl'):
            include('claude', p, 'claude', p.stem)
    for p in (state / 'kimi-code').rglob('wire.jsonl'):
        if p.parent.name == 'main':
            include('kimi', p, 'kimi', p.parents[2].name)
    from management_portal.execution.native_session_compaction import dsh_session_path
    dsh = state / 'dsh'
    for root in sorted(dsh.iterdir()) if dsh.is_dir() else []:
        if root.is_dir():
            p = dsh_session_path(dsh, root.name)
            if p:
                include('dsh', p, 'dsh', root.name)
    for p in (state / 'opencode').rglob('*.db'):
        wrapper = p.relative_to(state / 'opencode').parts[0]
        entry = destination('opencode', wrapper)
        meta = state / 'opencode' / wrapper / 'state.json'
        sid = json.loads(meta.read_text()).get('opencode_session_id') if meta.is_file() else None
        entry['sources'].append({'format': 'opencode_sqlite', 'path': str(p), 'native_session_id': sid,
                                 'execution_session_id': wrapper})
        if sid:
            entry['session_aliases'][sid] = wrapper
    sources = [(key, source) for key, entry in entries.items() for source in entry['sources']]
    recover_prompt_sessions(executions, sources, routes, cache)
    for r in executions:
        owner = routes.get((r['provider'], r.get('backend_route_id')))
        if owner and r.get('native_session_id'):
            native.setdefault((r['provider'], r['native_session_id']), set()).add(owner)
    for entry in entries.values():
        entry['sources'] = []
    for key, source in sources:
        destination(key[0], source.get('session_id', ''), key)['sources'].append(source)
    for r in executions:
        usage = json_value(r['usage_json']) or {}
        if not normalize_token_usage(usage.get('accounted', usage)):
            continue
        direct = r['provider'] not in ('codex', 'claude', 'kimi', 'dsh', 'opencode', 'antigravity')
        if direct and usage.get('api_id'):
            key = (r['provider'], usage['api_id'])
            entry = entries.setdefault(key, {'provider': key[0], 'account_id': key[1], 'sources': [],
                'resident_records': [], 'quota_observations': []})
        else:
            entry = destination(r['provider'], r.get('native_session_id') or r.get('backend_session_id') or '')
        entry['resident_records'].append({'execution_id': r['execution_id'],
            'session_id': r.get('native_session_id') or r.get('backend_session_id') or '',
            'logical_session_id': r.get('backend_session_id') or '',
            'native_link_basis': r.get('native_link_basis', 'provider_session'),
            'native_link_conflict': r.get('native_link_conflict', False),
            'evidence_kind': 'execution_result' if direct else 'native_request_log',
            'started_at': iso_time(r['started_at']), 'finished_at': iso_time(r['finished_at']),
            'usage': usage})
        # Direct API execution results already retain the served model and usage.
        # Harness executions are comparisons only: their native requests may
        # include many calls and must not be counted a second time.
        if direct:
            if not (r['started_at'] and r['finished_at'] and since <= r['started_at'] < r['finished_at'] <= until):
                continue
            counted = normalize_token_usage(usage)
            counted.update(normalize_token_usage(usage.get('accounted', usage)))
            entry.setdefault('execution_requests', []).append({
                'request_id': r['execution_id'], 'occurred_at': iso_time(r['finished_at']),
                'model': usage.get('model'), 'usage': counted,
                'session_id': r.get('backend_session_id') or r['execution_id']})
            entry['resident_records'][-1]['session_id'] = r.get('backend_session_id') or r['execution_id']
    snapshots = sorted((portal_path('features') / 'codex_limit_usage/daily').glob('*.json'))
    for key, entry in entries.items():
        entry['quota_observations'] = [q for q in quota if (q['provider'], q['account_id']) == key]
        if key[0] == 'codex':
            entry['quota_files'] = [str(p) for p in snapshots]
    return {'accounts': list(entries.values()), 'portal_provenance': provenance}
