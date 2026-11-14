"""Unchanged Job queue publication must not create new Mailbox row versions.

:module: resident.session
:aspects: performance, functional
"""
from copy import deepcopy
import pytest
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.society_service.lineage_data_mailbox import sync_mailbox_queue
from management_common.json_columns import json_value


def item(index):
    mid = f"msg_budget_{index}"
    return {"user_message_id": mid, "actor": "alice", "enqueued_at": "2026-11-14T00:00:00Z",
            "input_message": {"message_id": mid, "role": "user", "author_id": "alice",
                              "content_parts": [{"type": "text", "text": f"fixture {index}"}]}}


def rows(store, rid):
    with store.database.read_current() as connection:
        return {row['message_id']: dict(row) for row in connection.execute(
            "SELECT message_id,xmin::text AS version,place,position,group_id,updated_at,message_json "
            "FROM resident_mailbox WHERE resident_id=%s", (rid,)).fetchall()}


def sync(store, rid, queue):
    store.database.write(lambda connection: sync_mailbox_queue(connection, rid, queue))


def test_unchanged_queue_sync_does_not_update_rows(resident_record, record_property):
    store=get_lineage_data_store();rid=resident_record['resident_id'];queue=[item(i) for i in range(8)]
    sync(store,rid,queue);previous=rows(store,rid);changed=0
    for _ in range(3):
        sync(store,rid,queue);current=rows(store,rid)
        changed += sum(current[mid]['version'] != previous[mid]['version'] for mid in previous)
        previous=current
    record_property('unchanged_queue_row_updates',changed)
    assert changed == 0


def test_queue_sync_preserves_changes_grouping_and_rollback(resident_record):
    store=get_lineage_data_store();rid=resident_record['resident_id'];queue=[item(i) for i in range(3)]
    sync(store,rid,queue);before=rows(store,rid)
    reordered=[queue[1],queue[0],queue[2]];sync(store,rid,reordered);after=rows(store,rid)
    assert [after[x['user_message_id']]['position'] for x in reordered] == [0,1,2]
    assert before[queue[2]['user_message_id']]['version'] == after[queue[2]['user_message_id']]['version']
    group=item('group');group['mailbox_members']=queue[:2]
    sync(store,rid,[group,queue[2]]);grouped=rows(store,rid)
    for entry in queue[:2]:
        member=grouped[entry['user_message_id']]
        assert (member['place'],member['group_id']) == ('group',group['user_message_id'])
    sync(store,rid,queue);split=rows(store,rid)
    assert split[group['user_message_id']]['place'] == 'pending'
    assert all(split[x['user_message_id']]['place']=='queue' for x in queue)
    altered=deepcopy(queue);altered[0]['input_message']['content_parts'][0]['text']='must not overwrite original'
    sync(store,rid,altered)
    assert rows(store,rid)[queue[0]['user_message_id']]['message_json'] == before[queue[0]['user_message_id']]['message_json']
    stable=rows(store,rid)
    with pytest.raises(RuntimeError,match='rollback'):
        def abort(connection):
            sync_mailbox_queue(connection,rid,list(reversed(queue)))
            raise RuntimeError('rollback')
        store.database.write(abort)
    assert rows(store,rid) == stable
