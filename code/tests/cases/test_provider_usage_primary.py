"""Native usage is independent of Portal coverage, for every native runtime."""
import json
import sqlite3
from pathlib import Path
import pytest

from scripts.provider_weekly_usage import build_report, moment
from tests.cases.test_provider_weekly_usage import source, codex_records, START, END, AT, MS


def native_source(tmp_path, provider):
    if provider=='codex':
        return source(tmp_path,'codex',codex_records())
    if provider=='claude':
        return source(tmp_path,'claude',[{'type':'assistant','timestamp':AT,
            'message':{'id':'response','model':'native-model','usage':{'input_tokens':10,'output_tokens':5}}}])
    if provider=='kimi':
        return source(tmp_path,'kimi',[{'type':'usage.record','time':MS,'agentId':'main',
            'model':'native-model','usage':{'inputOther':10,'output':5}}])
    if provider=='dsh':
        return source(tmp_path,'dsh',[{'type':'request/context','time':MS,'data':{'model':'native-model'}},
            {'type':'assistant/message','time':MS+1,'seq':1,'data':{'usage':{'inputTokens':10,'outputTokens':5}}}])
    if provider=='opencode':
        p=tmp_path/'opencode.json'
        p.write_text(json.dumps({'requests':[{'id':'r','at_ms':MS,'model':'native-model',
            'usage':{'input_tokens':10,'output_tokens':5}}]}))
        return {'format':'opencode','path':str(p),'session_id':'native-session'}
    if provider=='antigravity':
        from tests.cases.test_provider_usage_native import pb
        p=tmp_path/'native.db'
        chat=pb(4,pb(2,10)+pb(3,5))+pb(9,pb(4,pb(1,MS//1000)))+pb(19,'native-model')
        with sqlite3.connect(p) as c:
            c.execute('CREATE TABLE gen_metadata(idx INTEGER,data BLOB)')
            c.execute('INSERT INTO gen_metadata VALUES(?,?)',(1,pb(1,chat)))
        return {'format':'antigravity','path':str(p),'session_id':'native-session'}


@pytest.mark.parametrize('provider',['codex','claude','kimi','dsh','opencode','antigravity'])
def test_native_requests_without_portal_executions_are_counted(tmp_path,provider):
    raw=native_source(tmp_path,provider)
    a={'provider':provider,'account_id':'a','sources':[raw]}
    first=build_report({'accounts':[a]},START,END)['accounts'][0]
    assert first['requests']>0 and first['totals']['total_tokens']>0
    assert first['usage_source']=='native_session_history'
    assert first['resident_comparisons']==[]
    a['resident_records']=[{'execution_id':'unlinked','session_id':'not-native',
        'started_at':START,'finished_at':END,'usage':{'total_tokens':999999}}]
    a['execution_requests']=[{'request_id':'fake','session_id':'not-native','occurred_at':AT,
                              'model':'wrong-model','usage':{'total_tokens':999999}}]
    second=build_report({'accounts':[a]},START,END)['accounts'][0]
    assert second['totals']==first['totals'] and second['requests']==first['requests']
    assert second['excluded_execution_requests']==1
    assert 'portal_execution_not_linked_to_native_history' in second['gaps']


def test_native_format_wins_even_when_export_is_listed_first(tmp_path):
    raw=native_source(tmp_path,'claude')
    exported=source(tmp_path,'requests',[{'request_id':'export','occurred_at':AT,
        'usage':{'total_tokens':9999}}],name='export')
    result=build_report({'accounts':[{'provider':'claude','account_id':'a',
        'sources':[exported,raw]}]},START,END)['accounts'][0]
    assert result['totals']['total_tokens']==15


def test_unreadable_native_history_does_not_promote_an_export_to_native_totals(tmp_path):
    exported=source(tmp_path,'requests',[{'request_id':'r','occurred_at':AT,
                                         'usage':{'total_tokens':100}}])
    result=build_report({'accounts':[{'provider':'claude','account_id':'a','sources':[
        exported,{'format':'claude','path':str(tmp_path/'missing')}]}]},START,END)['accounts'][0]
    assert result['totals']=={} and result['source_failures']


@pytest.mark.parametrize('provider',['codex','claude','kimi','antigravity'])
def test_missing_native_source_is_not_filled_with_execution_totals(provider):
    a={'provider':provider,'account_id':'a','sources':[], 'execution_requests':[
        {'request_id':'e','session_id':'s','occurred_at':AT,'usage':{'total_tokens':100}}]}
    result=build_report({'accounts':[a]},START,END)['accounts'][0]
    assert result['requests']==0 and result['totals']=={}
    assert 'no_attributable_requests_not_proof_of_zero_usage' in result['gaps']


@pytest.mark.parametrize('portal_mode',['empty','disabled','unavailable'])
@pytest.mark.parametrize('provider',['codex','claude','kimi','antigravity'])
def test_local_native_discovery_does_not_require_portal_records(tmp_path,monkeypatch,portal_mode,provider):
    from scripts import provider_usage_local as local
    monkeypatch.setattr(local,'portal_path',lambda area:tmp_path/area)
    seed=tmp_path/'seed';seed.mkdir()
    raw=native_source(seed,provider)
    root=tmp_path/'account'
    paths={'codex':'sessions/y/native.jsonl','claude':'projects/p/native.jsonl',
           'kimi':'sessions/w/native/agents/main/wire.jsonl',
           'antigravity':'antigravity-acp/conversations/native.db'}
    p=root/paths[provider];p.parent.mkdir(parents=True)
    data=Path(raw['path']).read_bytes()
    if provider=='codex':
        data=(json.dumps({'type':'session_meta','timestamp':START,'payload':{'id':'native'}})+'\n').encode()+data
    p.write_bytes(data)
    def evidence(*_):
        if portal_mode=='disabled': raise AssertionError('must not query Portal')
        if portal_mode=='unavailable': raise OSError('private path and credentials must not be exported')
        return [],[],[]
    accounts=[{'provider':provider,'account_id':'a','registration':{'kind':'subscription_login',
                'config':{'account_dir':str(root)}}}]
    manifest=local.local_manifest(accounts,moment(START),moment(END),evidence_reader=evidence,
                                  portal_evidence=portal_mode!='disabled')
    result=build_report(manifest,START,END)
    assert result['accounts'][0]['totals']['total_tokens']>0
    assert result['accounts'][0]['usage_source']=='native_session_history'
    assert 'private path' not in json.dumps(result)
    assert result['portal_provenance']['status']=={'empty':'available','disabled':'disabled','unavailable':'unavailable'}[portal_mode]
