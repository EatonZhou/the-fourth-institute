"""Native account-switch context, independently of the display transcript.

:module: execution.service
:importance: P1
"""
import json
from types import SimpleNamespace

import pytest

from management_portal.execution.codex_preservation import read_native_window
from management_portal.execution.context_preservation import prepare_context_preservation, commit_context_preservation


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


def test_latest_native_boundary_supersedes_damaged_and_rolled_back_history(tmp_path):
    reasoning = {'type': 'reasoning', 'encrypted_content': 'keep-exact', 'summary': []}
    call = {'type': 'function_call', 'call_id': 'call', 'name': 'exec', 'arguments': '{"cmd":"run"}'}
    output = {'type': 'function_call_output', 'call_id': 'call', 'output': 'keep output'}
    p = archive(tmp_path, [record('session_meta', {'id': 'thread'}),
        record('compacted', {'replacement_history': [{'type': 'message', 'content': 'old'}]}),
        record('event_msg', {'type': 'thread_rolled_back'})])
    with p.open('ab') as stream:
        stream.write(b'{"time\n')
        for row in [record('compacted', {'replacement_history': [reasoning]}),
                    record('response_item', call), record('response_item', output)]:
            stream.write((json.dumps(row) + '\n').encode())
    assert read_native_window(p, 'thread') == [reasoning, call, output]
    with pytest.raises(ValueError, match='another session'):
        read_native_window(p, 'other')


@pytest.mark.parametrize('compacted', [False, True])
def test_damaged_effective_history_is_not_skipped_or_replaced_by_older_boundary(tmp_path, compacted):
    rows = [record('session_meta', {'id': 'thread'})]
    if compacted:
        rows.append(record('compacted', {'replacement_history': []}))
    p = archive(tmp_path, rows)
    with p.open('ab') as stream:
        stream.write(b'{"time\n')
        stream.write((json.dumps(record('response_item', {'type': 'message', 'content': 'new'})) + '\n').encode())
    with pytest.raises(ValueError, match='active native history contains an invalid JSON record'):
        read_native_window(p, 'thread')


def test_invalid_newest_native_boundary_never_falls_back_to_older_snapshot(tmp_path):
    p = archive(tmp_path, [record('session_meta', {'id': 'thread'}),
        record('compacted', {'replacement_history': []}), record('compacted', {})])
    with pytest.raises(ValueError, match='compaction history is unavailable'):
        read_native_window(p, 'thread')


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
    session=store.ensure_provider_session(provider='codex',backend_route_id='one',backend_session_id='thread',agent_id='agent',actor_ref='test')
    binding=store.bind_provider_session('resident:agent',session['provider_session_ref'])
    prepared=prepare_context_preservation('agent',actor='test',operation_id='one',source_binding=binding,store=store,routes=routes)
    first=commit_context_preservation(prepared,store=store)
    assert prepare_context_preservation('agent',actor='test',operation_id='one',source_binding=binding,store=store,routes=routes)==prepared
    assert commit_context_preservation(prepared,store=store)==first
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
