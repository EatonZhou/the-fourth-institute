"""Current message versions, reactions and full-history search share Chatroom state."""
import pytest

from management_portal import chatroom_backend as rooms
from management_portal import chatroom_store
from tests.cases.test_chatroom_backend import lineage, text, ALICE, BOB, DAVE, HELPER, REVIEWER  # noqa: F401


def test_edits_are_owned_versioned_and_do_not_repeat_mentions(lineage):
    """只有作者能按版本编辑 Chatroom 消息，编辑后不重新投递或提醒 @ 对象"""
    room = rooms.create_chatroom(ALICE, [BOB, DAVE])["chatroom_id"]
    original = rooms.post_message(ALICE, room, text(f"旧正文 @bob @{HELPER}"))["message"]
    assert original["can_edit"] and original["can_retract"] and not original["reactions"]
    before = rooms.get_chatroom(BOB, room)
    cursor = rooms.list_messages(BOB, room)["change_cursor"]
    with pytest.raises(PermissionError):
        rooms.edit_message(BOB, room, original["message_id"], "冒充", expected_version=original["version"])
    edited = rooms.edit_message(ALICE, room, original["message_id"], f"新正文 @dave @{REVIEWER}", expected_version=original["version"])["message"]
    assert edited["ordinal"] == original["ordinal"] and edited["edited_at"]
    assert edited["version"] != original["version"]
    with pytest.raises(ValueError, match="已被修改"):
        rooms.edit_message(ALICE, room, original["message_id"], "过期编辑", expected_version=original["version"])
    assert len(lineage.deliveries()) == 1
    assert rooms.get_chatroom(BOB, room)["unread_count"] == before["unread_count"]
    assert not rooms.get_chatroom(BOB, room)["unread_mentions"]
    assert not rooms.get_chatroom(DAVE, room)["unread_mentions"]
    ancestors = rooms._agent_data().chat_message_ancestors(edited["version"], original["message_id"])
    assert any(rooms._message_text(m) == rooms._message_text(original) for m in ancestors)
    changes = rooms.list_messages(BOB, room, after=original["ordinal"], change_cursor=cursor)
    assert not changes["messages"] and changes["updated_messages"][0]["version"] == edited["version"]
    assert not changes["updated_messages"][0]["can_edit"]
    assert not rooms.search_messages(BOB, room, "旧正文")["messages"]
    assert rooms.search_messages(BOB, room, "新正文")["messages"][0]["message_id"] == original["message_id"]


def test_retraction_hides_body_attachments_quotes_and_reactions(lineage):
    """只有作者能撤回消息，撤回后隐藏正文、附件、引用文字和表情回应"""
    room = rooms.create_chatroom(ALICE, [BOB])["chatroom_id"]
    lineage.uploads['upload_aa'] = {'ready': True, 'file_name': '方案.pdf', 'media_type': 'application/pdf', 'size': 8}
    original = rooms.post_message(ALICE, room, [*text('重要正文'), {'type': 'file', 'upload_id': 'upload_aa'}])["message"]
    edited = rooms.edit_message(ALICE, room, original['message_id'], '修改正文', expected_version=original['version'])['message']
    assert edited['content_parts'][1]['upload_id'] == 'upload_aa'
    quoted = rooms.post_message(BOB, room, text('收到'), quote={'message_id': original['message_id']})['message']
    rooms.react_message(BOB, room, original['message_id'], '👍', active=True)
    with pytest.raises(PermissionError):
        rooms.retract_message(BOB, room, original['message_id'], expected_version=edited['version'])
    result = rooms.retract_message(ALICE, room, original['message_id'], expected_version=edited['version'])['message']
    assert result['retracted_at'] and result['content_parts'] == [] and not result['reactions']
    assert result['ordinal'] == original['ordinal']
    current = {m['message_id']: m for m in rooms.list_messages(BOB, room)['messages']}
    assert current[quoted['message_id']]['quote']['text'] == '这条消息已撤回'
    assert not current[result['message_id']]['can_edit']
    assert not rooms.search_messages(BOB, room, '方案.pdf')['messages']
    with pytest.raises(ValueError, match='已经撤回'):
        rooms.edit_message(ALICE, room, original['message_id'], '复活', expected_version=result['version'])
    with pytest.raises(ValueError, match='不能添加'):
        rooms.react_message(BOB, room, original['message_id'], '👍', active=True)
    rooms.retract_message(BOB, room, quoted['message_id'], expected_version=quoted['version'])
    assert rooms.get_chatroom(ALICE, room)['last_message']['preview'] == '[消息已撤回]'


def test_reactions_are_per_actor_idempotent_and_leave_unread_unchanged(lineage):
    """表情回应按用户记录且重复添加无变化，不改变未读数和消息版本"""
    room = rooms.create_chatroom(ALICE, [BOB])["chatroom_id"]
    original = rooms.post_message(ALICE, room, text('完成'))['message']
    before = rooms.get_chatroom(BOB, room)['unread_count']
    rooms.react_message(ALICE, room, original['message_id'], '✅', active=True)
    rooms.react_message(BOB, room, original['message_id'], '✅', active=True)
    cursor = rooms.list_messages(BOB, room)['change_cursor']
    duplicate = rooms.react_message(BOB, room, original['message_id'], '✅', active=True)['message']
    assert duplicate['reactions'] == [{'emoji': '✅', 'users': [ALICE, BOB], 'count': 2, 'active': True}]
    assert rooms.list_messages(BOB, room)['change_cursor'] == cursor
    removed = rooms.react_message(BOB, room, original['message_id'], '✅', active=False)['message']
    assert removed['reactions'][0]['users'] == [ALICE] and not removed['reactions'][0]['active']
    assert rooms.get_chatroom(BOB, room)['unread_count'] == before
    assert removed['version'] == original['version']
    with pytest.raises(ValueError):
        rooms.react_message(BOB, room, original['message_id'], '<script>', active=True)


def test_search_reads_all_pages_with_literal_matches_and_room_isolation(lineage, monkeypatch):
    """Chatroom 搜索按字面忽略大小写匹配并翻完所有分页，只搜本 Chatroom"""
    room = rooms.create_chatroom(ALICE, [BOB])["chatroom_id"]
    other = rooms.create_chatroom(ALICE, [BOB])["chatroom_id"]
    first = rooms.post_message(ALICE, room, text('Old [中文] Match'))['message']
    for n in range(7):
        rooms.post_message(BOB, room, text(f'无关 {n}'))
    last = rooms.post_message(BOB, room, text('New [中文] MATCH'))['message']
    rooms.post_message(ALICE, other, text('[中文] match 私聊'))
    monkeypatch.setattr(rooms, 'MAX_PAGE_SIZE', 3)
    page = rooms.search_messages(ALICE, room, '[中文] match', limit=1)
    assert [m['message_id'] for m in page['messages']] == [last['message_id']]
    page2 = rooms.search_messages(ALICE, room, '[中文] match', before=page['next_before'], limit=1)
    assert [m['message_id'] for m in page2['messages']] == [first['message_id']]
    assert not page2['next_before']
    assert rooms._agent_data().chat_message(other, first['message_id']) is None
    assert rooms.list_messages(ALICE, room, before=str(first['ordinal'] + 1))['messages'][-1]['message_id'] == first['message_id']


def test_chatroom_input_history_and_search_page_with_cursor(lineage):
    """ListInputHistory 与 SearchMessages 带 chatroom_id 时在 Web 执行，用 cursor 翻页，只在还有更多时返回 next_cursor"""
    from types import SimpleNamespace

    from management_portal import chatroom_web_isa, web_isa
    from management_portal.hatg.catalog import instruction_catalog

    room = rooms.create_chatroom(ALICE, [BOB])['chatroom_id']
    for n in range(3):
        rooms.post_message(ALICE, room, text(f'输入 {n} match'))
    rooms.post_message(BOB, room, text('别人的 match'))
    call = lambda arguments: SimpleNamespace(caller=ALICE, argument=arguments.get)  # noqa: E731
    bodies = lambda page: [rooms._message_text(m) for m in page['messages']]  # noqa: E731
    catalog = instruction_catalog()
    handlers = web_isa.binding().handlers
    for name, handler in (('ListInputHistory', chatroom_web_isa.list_chatroom_input_history),
                          ('SearchMessages', chatroom_web_isa.search_chatroom_messages)):
        assert catalog[name].owner_for({'chatroom_id': room}) == 'web'
        assert handlers[name] is handler

    newest = chatroom_web_isa.list_chatroom_input_history(call({'chatroom_id': room, 'limit': 2}))
    assert bodies(newest) == ['输入 2 match', '输入 1 match'] and newest['next_cursor']
    rest = chatroom_web_isa.list_chatroom_input_history(call({'chatroom_id': room, 'cursor': newest['next_cursor'], 'limit': 2}))
    assert bodies(rest) == ['输入 0 match'] and 'next_cursor' not in rest

    found = chatroom_web_isa.search_chatroom_messages(call({'chatroom_id': room, 'query': 'MATCH', 'limit': 3}))
    assert bodies(found) == ['别人的 match', '输入 2 match', '输入 1 match'] and found['next_cursor']
    older = chatroom_web_isa.search_chatroom_messages(call({'chatroom_id': room, 'query': 'MATCH', 'cursor': found['next_cursor'], 'limit': 3}))
    assert bodies(older) == ['输入 0 match'] and 'next_cursor' not in older
    assert 'next_before' not in found and 'next_before' not in newest

    page = chatroom_web_isa.list_chatroom_messages(call({'chatroom_id': room, 'limit': 2}))
    assert page['next_cursor'] and 'next_before' not in page
    earlier = chatroom_web_isa.list_chatroom_messages(call({'chatroom_id': room, 'cursor': page['next_cursor'], 'limit': 10}))
    everything = chatroom_web_isa.list_chatroom_messages(call({'chatroom_id': room, 'limit': 10}))['messages']
    assert [m['message_id'] for m in earlier['messages'] + page['messages']] == [m['message_id'] for m in everything]
    assert 'next_cursor' not in earlier


@pytest.mark.parametrize('legacy_intent', [False, True])
def test_pending_delivery_keeps_accepted_version_after_edit_and_retraction(lineage, monkeypatch, legacy_intent):
    """待投递的 @ 意图在消息编辑和撤回后仍按接收时的版本发给原 Resident"""
    room = rooms.create_chatroom(ALICE, [BOB])["chatroom_id"]
    deliver = rooms._deliver
    monkeypatch.setattr(rooms, '_deliver', lambda *_: None)
    original = rooms.post_message(ALICE, room, text(f'@{HELPER} 原任务'))['message']
    if legacy_intent:
        with rooms._Transaction(room) as records:
            records[room]['deliveries'][0].pop('trigger_segment_id')
    edited = rooms.edit_message(ALICE, room, original['message_id'], f'@{REVIEWER} 新任务', expected_version=original['version'])['message']
    rooms.retract_message(ALICE, room, original['message_id'], expected_version=edited['version'])
    delivery = rooms._store().get(room)['deliveries'][0]
    deliver(room, delivery['id'])
    assert len(lineage.deliveries()) == 1
    body = lineage.deliveries()[0][1]['message']['content_parts'][0]['text']
    assert '原任务' in body and '新任务' not in body
    assert lineage.deliveries()[0][1]['resident_id'] == HELPER


def test_message_change_failure_rolls_back_current_version(lineage, monkeypatch):
    """记录消息变更失败时编辑回滚，消息保持原版本"""
    room = rooms.create_chatroom(ALICE, [BOB])["chatroom_id"]
    original = rooms.post_message(ALICE, room, text('原文'))['message']
    def fail(*_args):
        raise RuntimeError('change feed unavailable')
    monkeypatch.setattr(chatroom_store, 'record_message_change', fail)
    with pytest.raises(RuntimeError):
        rooms.edit_message(ALICE, room, original['message_id'], '不应保存', expected_version=original['version'])
    assert rooms._agent_data().chat_message(room, original['message_id'])['segment_id'] == original['version']


def test_retraction_failure_rolls_back_reactions_feed_and_room_summary(lineage, monkeypatch):
    """撤回中途失败时表情回应、变更游标和 Chatroom 摘要全部回滚"""
    room = rooms.create_chatroom(ALICE, [BOB])['chatroom_id']
    original = rooms.post_message(ALICE, room, text('保留正文'))['message']
    rooms.react_message(BOB, room, original['message_id'], '❤️', active=True)
    cursor = rooms.list_messages(BOB, room)['change_cursor']
    save = chatroom_store.ChatroomState.save
    def fail_after_save(self, chatroom_id, value, previous):
        save(self, chatroom_id, value, previous)
        raise RuntimeError('metadata failure')
    monkeypatch.setattr(chatroom_store.ChatroomState, 'save', fail_after_save)
    with pytest.raises(RuntimeError):
        rooms.retract_message(ALICE, room, original['message_id'], expected_version=original['version'])
    monkeypatch.setattr(chatroom_store.ChatroomState, 'save', save)
    current = rooms.list_messages(BOB, room)
    message = next(m for m in current['messages'] if m['message_id'] == original['message_id'])
    assert message['version'] == original['version']
    assert message['reactions'][0]['emoji'] == '❤️'
    assert current['change_cursor'] == cursor
    assert rooms.get_chatroom(BOB, room)['last_message']['preview'] == '保留正文'
