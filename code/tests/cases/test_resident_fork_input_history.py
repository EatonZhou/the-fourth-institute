""":module: resident.conversation
:design: decision_f560803947c6
:importance: P1
"""
from types import SimpleNamespace

import pytest

from management_common.principal import message_origin
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import resident_input_history, resident_protocol
from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository


def message(identity, author='sample_admin'):
    return {'message_id': identity, 'role': 'user', 'author_id': author,
            'origin': message_origin(author), 'created_at': '2026-11-07T12:00:00Z',
            'content_parts': [{'type': 'text', 'text': identity}]}


@pytest.fixture
def history(isolated_portal_case, monkeypatch):
    store = AgentDataStore()
    source, child = 'resident_recall_source', 'resident_recall_child'
    store.display_chat_message(source, message('msg_oldest'))
    store.display_chat_message(source, message('msg_other_user', 'other_user'))
    store.display_chat_message(source, message('msg_resident', 'resident_aaaaaaaaaaaa'))
    store.display_chat_message(source, message('msg_older'))
    compact = store.put_segment(segment_type='compaction', content_type='application/json',
                                content={'replacement_history': [{'type': 'message', 'role': 'user', 'content': 'summary'}]})
    store.append_agent_segments(source, [compact], actor_ref='sample_admin')
    inherited = store.display_chat_message(source, message('msg_inherited'))['segment_id']
    store.fork_agent(source_agent_id=source, target_agent_id=child, actor_ref='sample_admin',
                     segment_ids=[compact, inherited])
    store.display_chat_message(child, message('msg_own'))
    store.display_chat_message(source, message('msg_parent_later'))
    records = {source: {'resident_id': source}, child: {'resident_id': child, 'source_resident_id': source,
                                                      'context_source_resident_id': source, 'fork_mode': 'full'}}
    visible = set(records)
    monkeypatch.setattr(resident_input_history.resident_access, 'visible_resident_records',
                        lambda ids, actor, surface: [records[key] for key in ids if key in visible])
    repository = ResidentConversationRepository(None, store)
    def page(token='', limit=2, target=child):
        return resident_input_history.input_history_page(repository, records[target], 'sample_admin', token, limit=limit)
    return SimpleNamespace(store=store, source=source, child=child, records=records,
                           visible=visible, page=page, repository=repository)


def identities(page):
    return [item['message_id'] for item in page['messages']]


def test_recall_continues_before_compaction_without_changing_the_fork(history, monkeypatch):
    """上箭头跨压缩分页读取本人旧输入，排除其他作者和分叉后来源输入；轨迹与模型窗口不变。"""
    h = history
    before = h.store.sequence_segment_ids(h.store.trace_sequence_id(h.child))
    context = h.store.agent_context_snapshot(h.child)
    monkeypatch.setattr(resident_protocol, '_target', lambda *args: SimpleNamespace(resident_id=h.child, record=h.records[h.child]))
    monkeypatch.setattr(resident_protocol.resident_conversation_stream, 'get_repository', lambda: h.repository)
    first = resident_protocol.get_input_history(h.child, 'sample_admin', limit=2)
    assert identities(first) == ['msg_own', 'msg_inherited']
    assert first['next_page_token'].startswith('fork:')
    second = h.page(first['next_page_token'])
    assert identities(second) == ['msg_older', 'msg_oldest']
    assert second['next_page_token'] == ''
    assert second['resident_id'] == h.child
    assert identities(h.page(limit=20)) == ['msg_own', 'msg_inherited', 'msg_older', 'msg_oldest']
    assert h.store.sequence_segment_ids(h.store.trace_sequence_id(h.child)) == before
    assert h.store.agent_context_snapshot(h.child) == context


@pytest.mark.parametrize('mode', ['compacted', 'recent-5', 'recent-10', 'message'])
def test_only_full_window_forks_extend_personal_history(history, mode):
    """压缩继承能回溯；显式选轮次或消息的 Fork 不扩大选定范围。"""
    history.records[history.child]['fork_mode'] = mode
    expected = ['msg_own', 'msg_inherited']
    if mode == 'compacted':
        expected += ['msg_older', 'msg_oldest']
    assert identities(history.page(limit=20)) == expected


def test_nested_fork_skips_empty_ancestor_pages(history):
    """嵌套 Fork 跨过无额外输入的中间轨迹，每页持续前进且不重复。"""
    h = history
    grandchild = 'resident_recall_nested'
    h.store.fork_agent(source_agent_id=h.child, target_agent_id=grandchild, actor_ref='sample_admin',
                       segment_ids=h.store.sequence_segment_ids(h.store.trace_sequence_id(h.child)))
    h.records[grandchild] = {'resident_id': grandchild, 'source_resident_id': h.child, 'fork_mode': 'full'}
    token, seen, result = '', set(), []
    for _ in range(8):
        page = h.page(token, limit=1, target=grandchild)
        result.extend(identities(page))
        token = page['next_page_token']
        if not token:
            break
        assert token not in seen
        seen.add(token)
    assert token == ''
    assert result == ['msg_own', 'msg_inherited', 'msg_older', 'msg_oldest']


def test_source_access_is_checked_on_every_page_and_cursor_is_bounded(history):
    """分页重新检查来源可见性；伪造边界不能越过 Fork，其他目标的游标拒绝。"""
    h = history
    token = h.page()['next_page_token']
    widened = resident_input_history._cursor(h.child, 1, '999999999')
    assert identities(h.page(widened)) == ['msg_older', 'msg_oldest']
    for bad in ['fork:invalid', resident_input_history._cursor('resident_other', 1, '9')]:
        with pytest.raises(ValueError, match='Invalid input page token'):
            h.page(bad)
    h.visible.clear()
    assert h.page(token)['messages'] == []
    assert identities(h.page(limit=20)) == ['msg_own', 'msg_inherited']


def test_unproven_fork_sources_do_not_add_history(history):
    """来源登记与持久 Fork 回执不一致时不猜历史。"""
    h = history
    different = 'resident_different'
    h.records[different] = {'resident_id': different}
    h.visible.add(different)
    h.records[h.child]['context_source_resident_id'] = different
    assert identities(h.page(limit=20)) == ['msg_own', 'msg_inherited']
    assert h.store.input_history_predecessor(h.source) == {}
