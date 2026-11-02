"""Native account-switch context, independently of the display transcript.

:module: execution.service
:importance: P1
"""
import json
from types import SimpleNamespace

import pytest

from management_portal.execution.codex_preservation import read_native_window, native_seed


def archive(tmp_path, records):
    p = tmp_path/'rollout.jsonl'
    p.write_text(''.join(json.dumps(x)+'\n' for x in records))
    return p


def record(kind, payload):
    return {'type':kind, 'payload':payload}


def test_native_window_retains_compaction_ciphertext_and_tool_pairs(tmp_path):
    reasoning={'type':'reasoning','encrypted_content':'ciphertext','summary':[]}
    call={'type':'function_call','call_id':'call','name':'exec','arguments':'{}'}
    output={'type':'function_call_output','call_id':'call','output':'done'}
    p=archive(tmp_path,[record('session_meta',{'id':'thread'}),
        record('response_item',{'type':'message','role':'user','content':'obsolete'}),
        record('compacted',{'replacement_history':[reasoning]}),
        record('response_item',call),record('response_item',output)])
    assert read_native_window(p,'thread')==[reasoning,call,output]


@pytest.mark.parametrize('change', ['wrong_session','missing_replacement','torn','rollback','missing_ciphertext'])
def test_unknown_native_history_never_becomes_empty_success(tmp_path,change):
    rows=[record('session_meta',{'id':'thread'})]
    if change=='wrong_session':rows[0]['payload']['id']='other'
    if change=='missing_replacement':rows.append(record('compacted',{}))
    if change=='rollback':rows.append(record('event_msg',{'type':'thread_rolled_back'}))
    if change=='missing_ciphertext':rows.append(record('response_item',{'type':'reasoning','summary':[]}))
    p=archive(tmp_path,rows)
    if change=='torn':p.write_text(p.read_text()+'{')
    with pytest.raises(ValueError):read_native_window(p,'thread')


def test_immutable_native_seed_is_idempotent_and_does_not_rewrite_chat(tmp_path,monkeypatch):
    from management_portal.execution.agent_data_store import get_agent_data_store
    store=get_agent_data_store()
    item={'type':'reasoning','encrypted_content':'opaque','summary':[]}
    path=archive(tmp_path,[record('session_meta',{'id':'thread'}),record('response_item',item)])
    routes=SimpleNamespace(find_thread_rollout=lambda *a,**k:path)
    binding={'provider':'codex','backend_session_id':'thread','backend_route_id':'one','provider_session_ref':'source'}
    first=native_seed('agent',binding,store=store,routes=routes)
    assert native_seed('agent',binding,store=store,routes=routes)==first
    assert store.fork_context_segments(first['segments'])[0]['content']=={'replacement_history':[item]}
    from management_portal.execution.codex_history_delivery import CodexHistoryDelivery
    delivered=[]
    delivery=CodexHistoryDelivery(references=first['segments'],route_id='five',agent_id='agent',
        actor='test',execution_id='switch',store=store,send=lambda tid,items:delivered.append((tid,items)))
    delivery.initialize('target')
    delivery.initialize('target')
    assert delivered==[('target',[item])]
    session=store.ensure_provider_session(provider='codex',backend_route_id='five',backend_session_id='target',agent_id='agent',actor_ref='test')
    store.record_context_received(first['source_agent_id'],session['provider_session_ref'],
        [r['segment_id'] for r in first['segments']],actor_ref='test')
    assert store.provider_context_snapshot(provider_session_ref=session['provider_session_ref'],agent_id='agent')['segments'][0]['content']=={'replacement_history':[item]}
    assert store.read_chat_messages('agent')==[]
    assert len(store.agent_context_snapshot('agent')['segments'])==1
