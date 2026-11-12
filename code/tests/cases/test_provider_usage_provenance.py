from datetime import datetime, timezone
import uuid

from scripts.provider_usage_provenance import recover_native_sessions, recover_prompt_sessions, runtime_config_dirs


class Connection:
    def __init__(self, rows): self.rows = rows
    def execute(self, sql, arguments):
        assert 'prompt' not in sql and 'diff' not in sql
        if "kind='subscription_native_link'" in sql:
            return []
        if 'FROM segments' in sql:
            return []
        return self.rows


def artifact(ex='execution-x', native='native', provider='codex', kind='event', thread=None):
    return {'execution_id': ex, 'backend_session_id': native, 'provider': provider,
            'type': kind, 'thread_id': thread}


def test_recover_stateless_native_identity_without_overwriting_logical_session():
    r = {'execution_id': 'execution-x', 'provider': 'codex', 'backend_session_id': 'execution-x'}
    recover_native_sessions(Connection([artifact()]), [r])
    assert r['native_session_id'] == 'native'
    assert r['backend_session_id'] == 'execution-x'


def test_conflicting_or_wrong_provider_artifacts_do_not_guess():
    r = {'execution_id': 'execution-x', 'provider': 'codex', 'backend_session_id': 'execution-x'}
    recover_native_sessions(Connection([artifact(provider='kimi')]), [r])
    assert 'native_session_id' not in r
    recover_native_sessions(Connection([artifact(), artifact(native='different')]), [r])
    assert r['native_link_conflict'] and 'native_session_id' not in r


def test_only_explicit_native_turn_uses_thread_id():
    r = {'execution_id': 'execution-x', 'provider': 'kimi', 'backend_session_id': 'execution-x'}
    recover_native_sessions(Connection([artifact(native=None, provider=None, thread='s', kind='tool')]), [r])
    assert 'native_session_id' not in r
    recover_native_sessions(Connection([artifact(native=None, provider=None, thread='s', kind='native_turn')]), [r])
    assert r['native_session_id'] == 's'


def test_runtime_discovery_includes_persistent_and_migrated_layouts(tmp_path):
    roots = [tmp_path/'p/agents/l/residents/r/runtime/backend/claude/config',
             tmp_path/'p/knowledge_base/agents/l/residents/r2/runtime/backend/claude/config']
    for p in roots: p.mkdir(parents=True)
    assert runtime_config_dirs(tmp_path) == {str(p) for p in roots}
    separate = tmp_path/'lineages/l/residents/r/runtime/backend/claude/config'
    separate.mkdir(parents=True)
    assert runtime_config_dirs(tmp_path, tmp_path/'lineages') == {str(p) for p in roots + [separate]}


def test_local_manifest_joins_recovered_id_and_includes_runtime_transcript(tmp_path, monkeypatch):
    from scripts import provider_usage_local as local
    monkeypatch.setattr(local, 'portal_path', lambda key: tmp_path/key)
    p=tmp_path/'projects/p/agents/l/residents/r/runtime/backend/claude/config/projects/w/s.jsonl'
    p.parent.mkdir(parents=True); p.write_text('')
    r={'execution_id':'execution-x','provider':'claude','backend_session_id':'execution-x',
       'backend_route_id':'route','native_session_id':'s','native_link_basis':'durable_native_artifact',
       'usage_json':{'total_tokens':10},'started_at':datetime(2026,11,1,tzinfo=timezone.utc),
       'finished_at':datetime(2026,11,2,tzinfo=timezone.utc)}
    a={'provider':'claude','account_id':'api','backend_route_id':'route'}
    out=local.local_manifest([a],r['started_at'],r['finished_at'],evidence_reader=lambda *_:([],[r],[]))['accounts'][0]
    assert out['sources'][0]['session_id']=='s'
    assert out['resident_records'][0]['session_id']=='s'
    assert out['resident_records'][0]['logical_session_id']=='execution-x'


def test_kimi_exact_prompt_id_recovers_native_session(tmp_path):
    p = tmp_path/'wire.jsonl'
    p.write_text('{"type":"turn.prompt","promptId":"msg_1","input":"private"}\n')
    r = {'execution_id':'execution-x', 'provider':'kimi', 'backend_session_id':'execution-x',
         'provider_turn_id':'msg_1', 'backend_route_id':'route'}
    sources = [(('kimi','api'), {'format':'kimi', 'path':str(p), 'session_id':'native'})]
    recover_prompt_sessions([r], sources, {('kimi','route'):('kimi','api')})
    assert r['native_session_id'] == 'native'
    assert r['native_link_basis'] == 'exact_native_prompt_id'
    assert r['backend_session_id'] == 'execution-x'


def test_kimi_wrong_account_and_duplicate_prompt_are_not_guessed(tmp_path):
    sources=[]
    for sid in ('one','two'):
        p=tmp_path/(sid+'.jsonl')
        p.write_text('{"type":"turn.prompt","promptId":"msg_1"}\n')
        sources.append((('kimi','api'), {'format':'kimi','path':str(p),'session_id':sid}))
    r={'execution_id':'execution-x','provider':'kimi','backend_session_id':'execution-x',
       'provider_turn_id':'msg_1','backend_route_id':'other'}
    recover_prompt_sessions([r],sources,{('kimi','other'):('kimi','different')})
    assert 'native_session_id' not in r
    r['backend_route_id']='route'
    recover_prompt_sessions([r],sources,{('kimi','route'):('kimi','api')})
    assert r['native_link_conflict'] and 'native_session_id' not in r


def test_claude_uses_only_exact_retained_prompt_uuid(tmp_path):
    p=tmp_path/'s.jsonl'
    p.write_text('{"type":"assistant","uuid":"not-user"}\n'
                 '{"type":"user","uuid":"prompt-uuid"}\n')
    r={'execution_id':'execution-x','provider':'claude','backend_session_id':'execution-x',
       'provider_turn_id':'execution-x','native_prompt_id':'prompt-uuid','backend_route_id':'route'}
    recover_prompt_sessions([r],[(('claude','api'),{'format':'claude','path':str(p),'session_id':'native'})],
                            {('claude','route'):('claude','api')})
    assert r['native_session_id']=='native'


def test_shared_claude_prompt_identity_preserves_native_protocol():
    from management_portal.claude_message_identity import turn_prompt_uuid
    assert turn_prompt_uuid('resident:main', 'turn-lost') == str(uuid.uuid5(
        uuid.UUID('5d0d8c43-6f1e-4b8e-9a51-0b7f3a1c2d64'), 'resident:main\nturn-lost'))


def test_native_tool_evidence_can_disambiguate_replayed_transcripts(tmp_path):
    sources=[]
    for sid, ids in [('parent',['toolu_one']), ('child',['toolu_one','toolu_two'])]:
        p=tmp_path/(sid+'.jsonl')
        import json
        p.write_text(json.dumps({'type':'assistant','message':{'content':[
            {'type':'tool_use','id':i} for i in ids]}})+'\n')
        sources.append((('claude','api'),{'format':'claude','path':str(p),'session_id':sid}))
    r={'execution_id':'execution-x','provider':'claude','backend_session_id':'execution-x',
       'native_item_ids':['toolu_one','toolu_two'],'backend_route_id':'route'}
    recover_prompt_sessions([r],sources,{('claude','route'):('claude','api')})
    assert r['native_session_id']=='child'


def test_disjoint_native_items_are_conflicting_not_an_arbitrary_match(tmp_path):
    sources=[]
    for sid in ('one','two'):
        p=tmp_path/(sid+'.jsonl')
        p.write_text('{"type":"response_item","payload":{"id":"msg_'+sid+'"}}\n')
        sources.append((('codex','api'),{'format':'codex','path':str(p),'session_id':sid}))
    r={'execution_id':'execution-x','provider':'codex','backend_session_id':'execution-x',
       'native_item_ids':['msg_one','msg_two'],'backend_route_id':'route'}
    recover_prompt_sessions([r],sources,{('codex','route'):('codex','api')})
    assert r['native_link_conflict'] and 'native_session_id' not in r


def test_exact_answer_hash_requires_matching_account_and_execution_interval(tmp_path):
    import hashlib
    import json
    text='A specific complete native answer. ' * 4
    digest=hashlib.sha256(text.encode()).hexdigest()
    sources=[]
    for sid, at in [('right','2026-11-01T00:00:02Z'),('old','2026-10-01T00:00:02Z')]:
        p=tmp_path/(sid+'.jsonl')
        p.write_text(json.dumps({'type':'event_msg','timestamp':at,
                                'payload':{'type':'agent_message','message':text}})+'\n')
        sources.append((('codex','api'),{'format':'codex','path':str(p),'session_id':sid}))
    r={'execution_id':'execution-x','provider':'codex','backend_session_id':'execution-x',
       'native_answer_hashes':[digest],'backend_route_id':'route',
       'started_at':datetime(2026,11,1,tzinfo=timezone.utc),
       'finished_at':datetime(2026,11,1,0,0,3,tzinfo=timezone.utc)}
    recover_prompt_sessions([r],sources,{('codex','route'):('codex','other')})
    assert 'native_session_id' not in r
    recover_prompt_sessions([r],sources,{('codex','route'):('codex','api')})
    assert r['native_session_id']=='right'
    assert r['native_link_basis']=='exact_native_answer_hash_and_interval'


def test_provenance_metadata_cache_reuses_unchanged_raw(tmp_path,monkeypatch):
    from scripts import provider_usage_provenance as provenance
    from scripts.provider_usage_incremental import IncrementalCache
    p=tmp_path/'wire.jsonl'
    p.write_text('{"type":"turn.prompt","promptId":"msg_1"}\n')
    sources=[(('kimi','api'),{'format':'kimi','path':str(p),'session_id':'native'})]
    def record():
        return {'execution_id':'execution-x','provider':'kimi','backend_session_id':'execution-x',
                'provider_turn_id':'msg_1','backend_route_id':'route'}
    cache=IncrementalCache(str(tmp_path/'cache'),rebuild=True)
    try:
        provenance.recover_prompt_sessions([record()],sources,{('kimi','route'):('kimi','api')},cache)
        monkeypatch.setattr(provenance,'kimi_prompt_ids',lambda _: (_ for _ in ()).throw(AssertionError('raw reread')))
        r=record()
        provenance.recover_prompt_sessions([r],sources,{('kimi','route'):('kimi','api')},cache)
        assert r['native_session_id']=='native'
    finally:
        cache.close()


def test_database_link_rows_do_not_require_dict_get():
    class Row:
        def __init__(self, **values): self.values=values
        def __getitem__(self, key): return self.values[key]
    class MetadataConnection:
        def execute(self, sql, args):
            if "FROM agent_operations" in sql:
                return []
            if "JOIN payloads" in sql:
                return [Row(execution_id='execution-x',content_sha256='digest',body=b'answer')]
            return [Row(execution_id='execution-x',item_id='msg_native')]
    record={'execution_id':'execution-x','provider':'codex','backend_session_id':'execution-x'}
    recover_native_sessions(MetadataConnection(),[record])
    assert record['native_item_ids']==['msg_native']
    assert record['native_answer_hashes']==['digest']


def test_short_terminal_answers_require_unique_time_scoped_source(tmp_path):
    import hashlib
    p=tmp_path/'s.jsonl'
    p.write_text('{"type":"event_msg","timestamp":"2026-11-01T00:00:02Z",'
                 '"payload":{"type":"agent_message","message":"Done.\\n"}}\n')
    r={'execution_id':'execution-x','provider':'codex','backend_session_id':'execution-x',
       'native_answer_hashes':[hashlib.sha256(b'Done.').hexdigest()], 'backend_route_id':'route',
       'started_at':datetime(2026,11,1,tzinfo=timezone.utc),
       'finished_at':datetime(2026,11,1,0,0,3,tzinfo=timezone.utc)}
    source={'format':'codex','path':str(p),'session_id':'native'}
    recover_prompt_sessions([r],[(('codex','api'),source)],{('codex','route'):('codex','api')})
    assert r['native_session_id']=='native'
    r.pop('native_session_id')
    other=tmp_path/'other.jsonl';other.write_text(p.read_text())
    recover_prompt_sessions([r],[(('codex','api'),source),
        (('codex','api'),{**source,'path':str(other),'session_id':'other'})],{('codex','route'):('codex','api')})
    assert 'native_session_id' not in r and r['native_link_conflict']


def test_explicit_subscription_link_is_preferred_to_legacy_artifact_guess():
    class DurableConnection(Connection):
        def execute(self,sql,args):
            if "kind='subscription_native_link'" in sql:
                return [{'execution_id':'execution-x','provider':'codex','native_session_id':'real-native'}]
            return super().execute(sql,args)
    r={'execution_id':'execution-x','provider':'codex','backend_session_id':'execution-x'}
    recover_native_sessions(DurableConnection([artifact(native='legacy-guess')]),[r])
    assert r['native_session_id']=='real-native'
    assert r['native_link_basis']=='durable_subscription_native_link'


def test_multiple_explicit_native_links_are_not_arbitrarily_flattened():
    class DurableConnection(Connection):
        def execute(self,sql,args):
            if "kind='subscription_native_link'" in sql:
                return [{'execution_id':'execution-x','provider':'codex','native_session_id':sid}
                        for sid in ('one','two')]
            return super().execute(sql,args)
    r={'execution_id':'execution-x','provider':'codex','backend_session_id':'execution-x'}
    recover_native_sessions(DurableConnection([]),[r])
    assert r['native_link_conflict'] and 'native_session_id' not in r
