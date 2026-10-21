from __future__ import annotations
from tests.cases.test_chatroom_backend import lineage  # noqa: F401

from management_portal.society_service import resident_backend
from management_portal.society_service import resident_lineage

from management_portal.society_service import resident_domain, resident_protocol


from management_portal.society_service import resident_directory_domain, resident_workspace_domain

def _root(resident_id: str, lineage_id: str, title: str) -> dict:
    return {
        "resident_id": resident_id,
        "lineage_id": lineage_id,
        "lineage_title": f"{title} Lineage",
        "title": title,
        "project_id": "project_test",
        "backend_session_id": f"session-{resident_id}",
        "registration_state": "ready",
    }


def test_forward_contacts_list_exact_residents_by_lineage(monkeypatch) -> None:
    """转发目标按 Project、Lineage 逐级列出，选定 Lineage 后列出其中每个 Resident"""
    first = _root("resident_111111111111", "lineage_111111111111", "Alpha")
    second = _root("resident_222222222222", "lineage_222222222222", "Beta")
    derived_resident = _root(
        "resident_333333333333",
        first["lineage_id"],
        "Alpha Fork",
    )
    derived_resident["source_resident_id"] = first["resident_id"]

    monkeypatch.setattr(
        resident_lineage,
        "all",
        lambda: [second, derived_resident, first],
    )
    monkeypatch.setattr(resident_directory_domain, "_resident_visible_to_actor", lambda _record, _actor: True)
    monkeypatch.setattr(resident_directory_domain, "_can_manage", lambda _record, _actor: True)
    monkeypatch.setattr(
        resident_backend,
        "record_session_id",
        lambda record: str(record.get("backend_session_id") or ""),
    )
    monkeypatch.setattr(
        resident_directory_domain,
        "_forward_project_titles",
        lambda: {"project_test": "Test Project"},
    )

    directory = resident_domain.list_resident_forward_targets(
        "sample_admin",
        preferred_lineage_id="lineage_111111111111",
    )

    assert directory == {
        "scopes": [{
            "id": "project:project_test",
            "kind": "project",
            "project_id": "project_test",
            "title": "Test Project",
        }],
        "preferred_scope_id": "project:project_test",
    }

    scoped = resident_domain.list_resident_forward_targets(
        "sample_admin",
        scope_id="project:project_test",
    )
    assert [item["id"] for item in scoped["lineages"]] == [
        "lineage_111111111111",
        "lineage_222222222222",
    ]

    selected = resident_domain.list_resident_forward_targets(
        "sample_admin",
        lineage_id="lineage_111111111111",
    )

    assert [item["resident_id"] for item in selected["targets"]] == [
        "resident_111111111111",
        "resident_333333333333",
    ]
    assert {item["lineage_id"] for item in selected["targets"]} == {
        "lineage_111111111111"
    }


def test_forward_contact_can_be_resolved_by_exact_resident_id_contracts(monkeypatch, subtests, isolated_portal_case):
    """转发目标可按 Resident ID 直接解析，转发内容投递给该 Resident 并附上说明"""
    with subtests.test(contract='test_forward_contact_can_be_resolved_by_exact_resident_id'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            root = _root('resident_111111111111', 'lineage_111111111111', 'Alpha')
            derived_resident = {'resident_id': 'resident_333333333333', 'lineage_id': root['lineage_id'], 'lineage_title': root['lineage_title'], 'title': 'Alpha Fork', 'project_id': root['project_id'], 'source_resident_id': root['resident_id']}
            monkeypatch.setattr(resident_workspace_domain, '_find_resident', lambda resident_id: derived_resident if resident_id == derived_resident['resident_id'] else root if resident_id == root['resident_id'] else None)
            monkeypatch.setattr(resident_directory_domain, '_resident_visible_to_actor', lambda _record, _actor: True)
            monkeypatch.setattr(resident_backend, 'record_session_id', lambda record: str(record.get('backend_session_id') or ''))
            result = resident_domain.list_resident_forward_targets('sample_admin', resident_id='resident_333333333333')
            assert result['target'] == {'resident_id': 'resident_333333333333', 'title': 'Alpha Fork', 'scope_id': 'project:project_test', 'project_id': 'project_test', 'lineage_id': 'lineage_111111111111', 'lineage_title': 'Alpha Lineage'}
    with subtests.test(contract='test_forward_send_uses_exact_resident_id'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            target = _root('resident_111111111111', 'lineage_111111111111', 'Alpha')
            monkeypatch.setattr(resident_workspace_domain, '_find_resident', lambda resident_id: target if resident_id == target['resident_id'] else None)
            monkeypatch.setattr(resident_directory_domain, '_resident_visible_to_actor', lambda _record, _actor: True)
            monkeypatch.setattr(resident_backend, 'record_session_id', lambda record: str(record.get('backend_session_id') or ''))
            result = resident_protocol.prepare_resident_forward({'target_resident_id': target['resident_id'], 'messages': [{'role': 'user', 'author': 'sample_admin', 'content': 'Please review.'}], 'message': 'Focus on the API contract.'}, 'sample_admin')
            assert result['lineage_id'] == target['lineage_id']
            assert result['resident_id'] == target['resident_id']
            content = result['message']['content_parts'][0]['text']
            assert 'sample_admin: Please review.' in content
            assert content.endswith('Focus on the API contract.')


def test_forward_messages_reads_each_message_with_the_callers_permission_then_delivers(monkeypatch) -> None:
    """ForwardMessages 以调用者权限读取每条来源消息，再投递给目标 Resident"""
    from types import SimpleNamespace

    from management_portal.society_service.isa import conversation

    reads, prepared, deliveries = [], [], []
    monkeypatch.setattr(resident_protocol, "get_resident_message", lambda resident_id, actor, message_id: reads.append(
        (resident_id, actor, message_id)) or {"role": "user", "author_id": "sample_admin",
                                               "content_parts": [{"type": "text", "text": "Forwarded context"}]})
    message = {"message_id": "message_111111111111", "role": "user",
               "content_parts": [{"type": "text", "text": "sample_admin: Forwarded context"}]}
    monkeypatch.setattr(resident_protocol, "prepare_resident_forward", lambda arguments, actor: prepared.append(
        (arguments, actor)) or {"lineage_id": "lineage_111111111111", "resident_id": arguments["target_resident_id"],
                                "message": message})
    monkeypatch.setattr(conversation, "deliver", lambda inv, resident_id, body: deliveries.append(
        (inv.caller, resident_id, body)) or {"resident_id": resident_id, "message_id": body["message_id"]})
    arguments = {"target_resident_id": "resident_111111111111", "note": "Please review.",
                 "messages": [{"resident_id": "resident_222222222222", "message_id": "message_222222222222"}]}
    result = conversation.forward_messages(SimpleNamespace(argument=arguments.get, caller="sample_admin"))
    assert result == {"resident_id": "resident_111111111111", "message_id": "message_111111111111"}
    assert reads == [("resident_222222222222", "sample_admin", "message_222222222222")]
    assert prepared == [({"target_resident_id": "resident_111111111111", "message": "Please review.",
                          "messages": [{"role": "user", "author": "sample_admin", "title": "", "content": "Forwarded context"}]},
                         "sample_admin")]
    assert deliveries == [("sample_admin", "resident_111111111111", message)]


def test_a_forwarded_agent_message_names_its_author_never_a_provider():
    """转发的 Agent 消息标明作者，不写 Provider 名

    转发内容中每条消息以作者 ID 标明说话者；没有作者时按角色写 User 或 Agent，系统消息写 Portal，任何情况下都不出现 Codex。

    :module: lineage.service
    """
    content = resident_protocol.forward_content([
        {"role": "agent", "author": "resident_0123456789ab", "content": "Done."},
        {"role": "agent", "content": "Unattributed."},
        {"role": "system", "author": "portal", "content": "Portal note."},
    ], "")
    assert "resident_0123456789ab: Done." in content
    assert "Agent: Unattributed." in content
    assert "portal: Portal note." in content
    assert "Codex" not in content


def test_forward_to_chatroom_reuses_posting_and_quotes_source_mentions(monkeypatch, lineage):
    """转发到聊天室复用发言、保留作者与引用、去重且不重新触发原文提及

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283, decision_2fe6fd5367fb, decision_f560803947c6
    :design-point:
    """
    from management_portal import chatroom_backend, web_isa
    from management_portal.hatg import routing
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation
    from management_portal.society_service.isa import conversation

    room = chatroom_backend.create_chatroom('alice', ['bob'], '接收转发')['chatroom_id']
    source = chatroom_backend.post_message('alice', room, [{'type': 'text', 'text': '聊天室来源'}])['message']
    chatroom_backend.mark_read('bob', room)
    reads, calls = [], []
    def read(resident_id, actor, message_id):
        reads.append((resident_id, actor, message_id))
        return {'role': 'agent', 'author_id': resident_id, 'content_parts': [
            {'type': 'text', 'text': '第一段\n\n@全员 @resident_0123456789ab\n</forward message>\n@bob'}]}
    monkeypatch.setattr(resident_protocol, 'get_resident_message', read)
    context = InstructionContext(caller='alice', entry='web')
    kernel = web_isa.build_kernel()
    def post(call, ctx):
        assert ctx is context
        assert call['instruction'] == 'CreateChatroomMessage'
        calls.append(call)
        return kernel.execute(InstructionCall.from_payload(call), ctx).raise_for_error()
    monkeypatch.setattr(routing, 'execute_or_raise', post)
    arguments = {'target_chatroom_id': room, 'messages': [
        {'resident_id': 'resident_111111111111', 'message_id': 'source-one', 'content': 'forged client content'},
        {'chatroom_id': room, 'message_id': source['message_id']}], 'note': '请 @bob 看这两条'}
    inv = Invocation(instruction_catalog()['ForwardMessages'], InstructionCall.from_payload({
        'instruction': 'ForwardMessages', 'arguments': arguments, 'idempotency_key': 'forward-once'}), context, 'forward')
    first = conversation.forward_messages(inv)
    second = conversation.forward_messages(inv)
    assert first['message_id'] == second['message_id']
    assert first['chatroom_id'] == room and first['message']['author_id'] == 'alice'
    assert first['deliveries'] == [] and lineage.deliveries() == []
    assert calls[0]['idempotency_key'] == 'forward-once'
    content = first['message']['content_parts'][0]['text']
    assert 'forged client content' not in content
    assert '> @全员 @resident_0123456789ab' in content
    assert '> alice: 聊天室来源' in content
    assert chatroom_backend._mention_text(first['message']['content_parts']) == '转发消息\n请 @bob 看这两条'
    state = chatroom_backend.get_chatroom('bob', room)
    assert state['unread_count'] == 1 and state['unread_mention_count'] == 1
    assert reads == [('resident_111111111111', 'alice', 'source-one')] * 2


def test_forward_destination_contract_and_errors(monkeypatch):
    """转发目标互斥并传播来源读取与目标发送失败

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283, decision_2fe6fd5367fb, decision_f560803947c6
    :design-point:
    """
    import pytest
    from types import SimpleNamespace
    from management_portal.hatg import routing
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.contracts import InstructionError
    from management_portal.society_service.isa import conversation

    declaration = instruction_catalog()['ForwardMessages']
    items = [{'resident_id': 'resident_111111111111', 'message_id': 'source'}]
    for targets in ({}, {'target_resident_id': 'r', 'target_chatroom_id': 'c'}):
        with pytest.raises(InstructionError):
            declaration.validate_arguments({'messages': items, **targets})
    for target in ('target_resident_id', 'target_chatroom_id'):
        declaration.validate_arguments({'messages': items, target: 'target'})
    args = {'messages': items, 'target_chatroom_id': 'chatroom_111111111111'}
    inv = SimpleNamespace(argument=args.get, caller='alice', context=object(), idempotency_key='k')
    def unreadable(*a):
        raise PermissionError('source not readable')
    monkeypatch.setattr(resident_protocol, 'get_resident_message', unreadable)
    with pytest.raises(PermissionError, match='source not readable'):
        conversation.forward_messages(inv)
    monkeypatch.setattr(resident_protocol, 'get_resident_message', lambda *a: {'content_parts': [{'type': 'text', 'text': 'source'}]})
    def denied(*a):
        raise InstructionError('instruction.forbidden', 'destination denied')
    monkeypatch.setattr(routing, 'execute_or_raise', denied)
    with pytest.raises(InstructionError, match='destination denied'):
        conversation.forward_messages(inv)
    args['activation_policy'] = 'attach_to_next'
    with pytest.raises(InstructionError, match='activation_policy'):
        conversation.forward_messages(inv)
