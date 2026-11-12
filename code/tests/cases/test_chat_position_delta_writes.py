"""Physical projection writes follow changed rows, with the full projection as oracle."""
import pytest
import json
from management_portal.execution import chat_position_index as index
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution import agent_data_traces
from tests.cases.test_chat_position_index import _message


def instrument(c):
    c.execute('CREATE TEMP TABLE projection_writes (tab text, op text, kind text) ON COMMIT DROP')
    c.execute("""CREATE FUNCTION pg_temp.count_projection_write() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN INSERT INTO projection_writes VALUES(TG_TABLE_NAME,TG_OP,
      COALESCE(to_jsonb(NEW),to_jsonb(OLD))->>'source_kind'); RETURN NULL; END $$""")
    for table in ['chat_message_positions','chat_actor_activity']:
        c.execute(f'CREATE TRIGGER count_projection_write AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pg_temp.count_projection_write()')


def counts(c):
    return {(r['tab'],r['op'],r['kind']):r['n'] for r in c.execute('SELECT tab,op,kind,count(*) n FROM projection_writes GROUP BY 1,2,3').fetchall()}


def oracle(c, trace):
    expected=c.execute('WITH wanted('+','.join(index.POSITION_COLUMNS)+') AS ('+index.SEGMENT_ROWS+' UNION ALL '+index.NOTICE_ROWS+') SELECT * FROM wanted ORDER BY source_kind,source_id',(trace,trace)).fetchall()
    actual=c.execute("SELECT * FROM chat_message_positions WHERE sequence_id=%s AND source_kind<>'sync' ORDER BY source_kind,source_id",(trace,)).fetchall()
    assert actual==expected
    expected_activity=c.execute('WITH wanted(sequence_id,actor,latest_at) AS ('+index._ACTIVITY_ROWS+index._ACTIVITY_GROUP+') SELECT * FROM wanted ORDER BY actor',(index.RELAYED_COPY_PATTERN,trace,trace)).fetchall()
    assert c.execute('SELECT * FROM chat_actor_activity WHERE sequence_id=%s ORDER BY actor',(trace,)).fetchall()==expected_activity
    assert index.current(c,trace)


@pytest.mark.parametrize('size',[10,100])
def test_unchanged_refresh_and_incremental_replay_write_nothing(size):
    store=AgentDataStore();resident='resident_delta_budget'
    for i in range(size): store.display_chat_message(resident,{**_message('m'+str(i)),'author_id':'sample_admin'})
    store.display_chat_message(resident,{**_message('notice',role='system'),'author_id':'agent_harness'})
    trace=store.trace_sequence_id(resident)
    with store.database.transaction() as c:
        instrument(c)
        index.refresh_sequence(c,trace)
        rows=c.execute("SELECT source_kind,source_id FROM chat_message_positions WHERE sequence_id=%s AND source_kind<>'sync'",(trace,)).fetchall()
        index.upsert_segments(c,trace,[r['source_id'] for r in rows if r['source_kind']=='segment'])
        index.upsert_notices(c,trace,[r['source_id'] for r in rows if r['source_kind']=='notice'])
        oracle(c,trace)
        assert counts(c)=={}


def test_edit_remove_clear_and_rollback_keep_oracle_and_only_changed_rows():
    store=AgentDataStore();resident='resident_delta_changes'
    for i in range(20): store.display_chat_message(resident,{**_message('m'+str(i)),'author_id':'sample_admin'})
    trace=store.trace_sequence_id(resident)
    with store.database.transaction() as c:
        instrument(c)
        store.update_chat_message(resident,'m0',{**_message('m0',text='edited'),'author_id':'sample_admin'})
        oracle(c,trace)
        non_sync={key:n for key,n in counts(c).items() if key[0]=='chat_message_positions' and key[2]!='sync'}
        assert sum(non_sync.values())==2
        before=c.execute('SELECT * FROM chat_message_positions WHERE sequence_id=%s ORDER BY source_kind,source_id',(trace,)).fetchall()
        with pytest.raises(RuntimeError,match='rollback'):
            with c.raw.transaction():
                member=store.sequence_segment_ids(trace)[-1]
                agent_data_traces.remove_trace_member(c,trace,member)
                oracle(c,trace)
                raise RuntimeError('rollback')
        assert c.execute('SELECT * FROM chat_message_positions WHERE sequence_id=%s ORDER BY source_kind,source_id',(trace,)).fetchall()==before
        agent_data_traces.remove_trace_member(c,trace,store.sequence_segment_ids(trace)[-1])
        oracle(c,trace)
        store.clear_chat(resident,actor_ref='sample_admin')
        oracle(c,trace)


def test_order_swap_updates_only_moved_rows_and_keeps_sync():
    store=AgentDataStore();resident='resident_delta_order'
    for i in range(10): store.display_chat_message(resident,_message('m'+str(i)))
    trace=store.trace_sequence_id(resident)
    with store.database.transaction() as c:
        instrument(c)
        c.execute('SELECT 1 FROM sequences WHERE sequence_id=%s FOR UPDATE',(trace,))
        c.execute('UPDATE sequence_segments SET ordinal=100+ordinal WHERE sequence_id=%s AND ordinal IN (1,10)',(trace,))
        c.execute('UPDATE sequence_segments SET ordinal=111-ordinal WHERE sequence_id=%s AND ordinal>100',(trace,))
        agent_data_traces.touch_sequence(c,trace,'portal')
        index.refresh_sequence(c,trace)
        oracle(c,trace)
        writes={key:n for key,n in counts(c).items() if key[0]=='chat_message_positions' and key[2]!='sync'}
        assert writes=={('chat_message_positions','UPDATE','segment'):2}


def test_withdraw_latest_activity_retreats_then_clear_removes_it():
    store=AgentDataStore();resident='resident_delta_activity'
    for i in range(2):
        store.display_chat_message(resident,{**_message('m'+str(i)),'author_id':'sample_admin',
            'created_at':f'2026-10-12T0{i}:00:00Z'})
    trace=store.trace_sequence_id(resident)
    with store.database.transaction() as c:
        instrument(c)
        agent_data_traces.remove_trace_member(c,trace,store.sequence_segment_ids(trace)[-1])
        oracle(c,trace)
        activity={key:n for key,n in counts(c).items() if key[0]=='chat_actor_activity'}
        assert activity=={('chat_actor_activity','UPDATE',None):1}
        assert str(c.execute('SELECT latest_at FROM chat_actor_activity WHERE sequence_id=%s',(trace,)).fetchone()['latest_at']).startswith('2026-10-12 00:00:00')
        store.clear_chat(resident,actor_ref='sample_admin')
        oracle(c,trace)
        assert c.execute('SELECT count(*) n FROM chat_actor_activity WHERE sequence_id=%s',(trace,)).fetchone()['n']==0


def test_full_rebuild_reference_matches_delta_with_physical_counts(record_testsuite_property):
    """Execute the former full rebuild as an independent SQL oracle in the fixture."""
    store=AgentDataStore();resident='resident_delta_reference'
    for i in range(100): store.display_chat_message(resident,{**_message('m'+str(i)),'author_id':'sample_admin'})
    trace=store.trace_sequence_id(resident)
    with store.database.transaction() as c:
        instrument(c)
        c.execute("DELETE FROM chat_message_positions WHERE sequence_id=%s AND source_kind<>'sync'",(trace,))
        c.execute('INSERT INTO chat_message_positions '+index.COLUMNS+' '+index.SEGMENT_ROWS,(trace,))
        c.execute('INSERT INTO chat_message_positions '+index.COLUMNS+' '+index.NOTICE_ROWS,(trace,))
        c.execute('DELETE FROM chat_actor_activity WHERE sequence_id=%s',(trace,))
        c.execute('INSERT INTO chat_actor_activity(sequence_id,actor,latest_at) '+index._ACTIVITY_ROWS+index._ACTIVITY_GROUP,(index.RELAYED_COPY_PATTERN,trace,trace))
        old=counts(c)
        assert old=={('chat_message_positions','DELETE','segment'):100,
                    ('chat_message_positions','INSERT','segment'):100,
                    ('chat_actor_activity','DELETE',None):1,
                    ('chat_actor_activity','INSERT',None):1}
        before=c.execute('SELECT * FROM chat_message_positions WHERE sequence_id=%s ORDER BY source_kind,source_id',(trace,)).fetchall()
        c.execute('DELETE FROM projection_writes')
        index.refresh_sequence(c,trace)
        oracle(c,trace)
        assert c.execute('SELECT * FROM chat_message_positions WHERE sequence_id=%s ORDER BY source_kind,source_id',(trace,)).fetchall()==before
        assert counts(c)=={}
        record_testsuite_property('projection_physical_row_writes',json.dumps({
            'reference_full_refresh':[{'table':k[0],'operation':k[1],'kind':k[2],'rows':v} for k,v in old.items()],
            'delta_refresh':[], 'method':'isolated PostgreSQL AFTER ROW trigger; identical canonical rows/activity'}))
