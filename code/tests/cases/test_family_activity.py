"""A facade represents all Family work without reopening finished requests.

:module: lineage.hatg
:importance: P1
:aspects: functional, behavior
:design: decision_44cf587c1721
"""
import copy
from pathlib import Path

import pytest

from management_portal.society_service import family_activity, family_facade, resident_protocol
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service import resident_conversation_records
from tests.cases.family_test_support import mock_family_rosters
from tests.cases.test_family_facade import facade, input_delivery, resident_record  # noqa: F401

ROUTER = 'resident_111111111111'
STEWARD = 'resident_222222222222'
MEMBER = 'resident_333333333333'
DELETED = 'resident_444444444444'
INNER = 'resident_555555555555'


@pytest.fixture
def family(facade, monkeypatch):
    repo = resident_conversation_stream.get_repository()
    store = repo.state_store
    source_states = {}
    repo._fixture_activity_states = source_states
    live_states = repo.projection.states
    monkeypatch.setattr(repo.projection, "states", lambda ids: {
        rid: copy.deepcopy(source_states.get(rid, value)) for rid, value in live_states(ids).items()
    })
    for rid in (ROUTER, STEWARD, MEMBER, DELETED, INNER):
        record = copy.deepcopy(facade)
        record.update(resident_id=rid, family={})
        owned = record['owned_workspace']
        owned['path'] = str(Path(record['workspace_path']) / 'residents' / rid / 'workspace')
        from management_portal.society_service.resource_client import workspace_id_for_path
        owned['workspace_id'] = workspace_id_for_path(owned['path'])
        record['owned_workspace_id'] = owned['workspace_id']
        store.put_resident_record(rid, record)
    # Retain a busy historical projection to prove it cannot count as live work.
    put_state(repo, DELETED, 'running', queued=9)
    store.remove_residents([{'resident_id': DELETED, 'deleted_at': '2026-10-08T00:00:00Z', 'deleted_by': 'CASE-A'}])
    instance = {'id': 'fbi_family0001', 'family': {'facade': facade['resident_id'], 'router': ROUTER, 'steward': STEWARD,
                'members': [MEMBER, DELETED]}}
    definition = {'function_blocks': [instance]}
    mock_family_rosters(monkeypatch, lambda: definition['function_blocks'])
    return facade, repo, instance, definition


def put_state(repo, rid, work='idle', queued=0, closed=False):
    state = {'work_state': work, 'active_message_id': 'msg_0000000000000001' if work == 'running' else '',
             'queued_count': queued, 'intake_state': 'closed' if closed else 'open'}
    repo.set_state(rid, state)
    repo._fixture_activity_states[rid] = state


@pytest.mark.parametrize('worker', [MEMBER, ROUTER, STEWARD])
def test_family_activity_survives_a_completed_request_without_stop_permission(family, worker):
    """Family 全体运行与请求完成状态独立"""
    facade, repo, _, _ = family
    rid = facade['resident_id']
    request = 'msg_00000000000000f1'
    repo.set_input_state(rid, request, 'completed')
    put_state(repo, rid, closed=True)
    put_state(repo, worker, 'running', closed=True)
    with pytest.raises(KeyError):
        put_state(repo, DELETED, 'running', queued=10)
    assert repo.current_projection(DELETED)['state']['queued_count'] == 9
    raw = repo.current_state(rid)
    expected = {'work_state': 'running', 'running_count': 1, 'queued_count': 0}
    single = resident_protocol.get_resident_state(rid, 'CASE-A')
    batch = resident_protocol.get_resident_states([rid], 'CASE-A')['items'][0]
    conversation = resident_protocol.get_resident_conversation_snapshot(rid, 'CASE-A')
    assert single["resident_id"] == batch["resident_id"] == rid
    for result in (single, batch, conversation):
        assert result['state']['family_activity'] == expected
        assert result['state']['work_state'] == 'idle'
        assert result['state']['active_message_id'] == ''
        assert result['state']['intake_state'] == 'closed'
        assert result['controls']['can_stop'] is False
    assert repo.current_state(rid) == raw
    assert repo.input_state(rid, request)['state'] == 'completed'


def test_family_activity_tracks_queue_idle_and_nested_members_once(family, monkeypatch):
    """Family 汇总排队、空闲、嵌套和回收站状态

    汇总只读成员是否存在和 Family 角色，不解析成员的 Workspace 路径。
    """
    facade, repo, instance, definition = family
    rid = facade['resident_id']
    record = repo.state_store.resident_record(INNER)
    record['family'] = {'role': 'facade', 'family_id': 'fbi_inner'}
    repo.state_store.put_resident_record(INNER, record)
    instance['family']['members'].append(INNER)
    definition['function_blocks'].append({'id': 'fbi_inner', 'family': {'facade': INNER, 'members': [MEMBER, rid]}})
    store = family_activity.get_lineage_data_store()
    read_residents = store.residents_by_id
    catalog_reads = []
    def only_requested_facade(ids):
        requested = list(ids)
        assert requested == [rid], 'the Family walk must not project members\' Workspaces'
        catalog_reads.append(requested)
        return read_residents(requested)
    monkeypatch.setattr(store, 'residents_by_id', only_requested_facade)
    put_state(repo, MEMBER, 'running', queued=2)
    put_state(repo, ROUTER, 'running')
    assert repo.current_projection(DELETED)['state']['queued_count'] == 9
    def activity():
        return resident_protocol.read_resident_states([rid])[rid]['family_activity']
    assert activity() == {'work_state': 'running', 'running_count': 2, 'queued_count': 2}
    put_state(repo, MEMBER, 'queued', queued=2)
    put_state(repo, ROUTER)
    assert activity() == {'work_state': 'queued', 'running_count': 0, 'queued_count': 2}
    put_state(repo, MEMBER)
    assert activity() == {'work_state': 'idle', 'running_count': 0, 'queued_count': 0}
    put_state(repo, rid, 'queued', queued=1)
    assert activity() == {'work_state': 'queued', 'running_count': 0, 'queued_count': 1}
    assert catalog_reads  # The facade's execution gate still reads its own record.


def test_unrelayed_member_state_changes_refresh_the_facade(family):
    """非转交任务的成员状态变化也刷新门面"""
    facade, _, _, _ = family
    for rid in (MEMBER, ROUTER, STEWARD):
        assert family_facade.projection_targets({'resident_id': rid, 'event_type': resident_conversation_records.STATE_CHANGED,
                                                'payload': {'state': {'work_state': 'running'}}}) == [facade['resident_id']]
    assert family_activity.projection_targets({'resident_id': MEMBER, 'event_type': resident_conversation_records.ACTIVITY_CHANGED}) == []
