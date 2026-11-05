"""Display ownership follows confirmed delivery; execution outcomes stay intact."""
from management_portal.society_service import family_facade
from tests.cases.test_conversation_input_work import delivered_work, team, owners  # noqa: F401


def test_confirmed_injection_owns_following_output_across_reads(delivered_work):
    rid, repo, store, head, appended, execution, _ = delivered_work
    message = {"message_id": "msg_following", "role": "agent", "author_id": rid,
               "reply_to_message_id": head, "execution_id": execution,
               "content_parts": [{"type": "text", "text": "Answer the supplied correction"}]}
    repo.append_message(rid, message)
    public = repo.message(rid, message["message_id"])
    assert public["reply_to_message_id"] == head
    assert public["display_reply_to_message_id"] == appended
    blocks = repo.reply_blocks([(rid, head), (rid, appended)])
    assert not blocks[(rid, head)]["replies"]
    assert [r["message_id"] for r in blocks[(rid, appended)]["replies"]] == [message["message_id"]]
    assert repo.reply_rounds([(rid, head)])[(rid, head)]["replies"][0]["message_id"] == message["message_id"]
    # An independently read page does not need the primary request loaded.
    assert repo.reply_blocks([(rid, appended)])[(rid, appended)]["replies"] == blocks[(rid, appended)]["replies"]


def test_unconfirmed_mailbox_input_cannot_steal_reply(delivered_work):
    rid, repo, store, head, appended, execution, _ = delivered_work
    queued = {"message_id": "msg_still_queued", "role": "user", "author_id": "CASE-A",
              "content_parts": [{"type": "text", "text": "not delivered"}]}
    repo.accept_user_message(rid, queued)
    reply = {"message_id": "msg_after_queue", "role": "agent", "author_id": rid,
             "reply_to_message_id": head, "execution_id": execution,
             "content_parts": [{"type": "text", "text": "still answers the delivered input"}]}
    repo.append_message(rid, reply)
    assert repo.message(rid, reply["message_id"])["display_reply_to_message_id"] == appended


def test_facade_injected_page_gets_its_reply_without_loading_original(team, delivered_work, monkeypatch):
    rid, repo, store, head, appended, execution, _ = delivered_work
    facade = team.source
    monkeypatch.setattr(family_facade, "relay_links", lambda _: {
        "msg_facade_head": {"resident_id": rid, "message_id": head},
        "msg_facade_append": {"resident_id": rid, "message_id": appended},
    })
    repo.append_message(rid, {"message_id": "msg_correction", "role": "agent", "author_id": rid,
        "reply_to_message_id": head, "execution_id": execution,
        "content_parts": [{"type": "text", "text": "correction result"}]})
    request = {"message_id": "msg_facade_append", "role": "user", "author_id": "CASE-A"}
    page = family_facade.project_messages(facade, [request])
    assert [item["message_id"] for item in page] == ["msg_facade_append", "msg_correction"]
    assert page[1]["family_reply"]["request_message_id"] == "msg_facade_append"
    assert page[1]["display_reply_to_message_id"] == "msg_facade_append"
    single = family_facade.projected_reply(facade, "msg_correction")
    assert single["family_reply"] == page[1]["family_reply"]
    assert single["display_reply_to_message_id"] == page[1]["display_reply_to_message_id"]


def test_request_blocks_browser():
    """Native shared Chat verifies placement and a reading anchor at two widths."""
    import json
    import os
    import subprocess
    from pathlib import Path
    from management_portal.resources.portal import portal_route_script
    source = Path(__file__).with_name("javascript") / "chat_round_rail.browser.cjs"
    result = subprocess.run(["node", str(source)], text=True, capture_output=True,
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        env={**os.environ, "CHAT_REPLY_BLOCKS_ONLY": "1"}, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_delivered_collaboration_keeps_user_reply_block(team, delivered_work, monkeypatch):
    """A live reply and later results stay under the user request after a colleague arrives."""
    from management_portal.execution.appended_input_projection import materialize_delivered_input
    from management_portal.execution.contracts import ExecutionInputControl
    rid, repo, store, head, appended, execution, turn = delivered_work
    colleague = 'resident_abcdefabcdef'
    facade = team.source
    monkeypatch.setattr(family_facade, 'relay_links', lambda _: {
        'msg_facade_user': {'resident_id': rid, 'message_id': appended},
    })
    request = {'message_id': 'msg_facade_user', 'role': 'user', 'author_id': 'CASE-A'}

    def output(identity):
        repo.append_message(rid, {'message_id': identity, 'role': 'agent', 'author_id': rid,
            'reply_to_message_id': head, 'execution_id': execution,
            'content_parts': [{'type': 'text', 'text': identity}]})

    def deliver(identity, sender):
        repo.append_message(rid, {'message_id': identity, 'role': 'user', 'author_id': sender['id'],
            'origin': {'schema': 'tfi.message-origin.v1', 'sender': sender,
                       'producer': {'kind': 'service', 'id': 'portal'}},
            'content_parts': [{'type': 'text', 'text': identity}]})
        control = ExecutionInputControl.new(execution_id=execution, actor=colleague,
            expected_turn_id=turn, source_segment_id=store.chat_message_provenance(rid, identity)['segment_id'],
            input=[{'type': 'text', 'text': identity}])
        materialize_delivered_input(store, {**control.to_payload(), 'status': 'delivered',
            'updated_at': '2026-10-01T00:00:03Z'})

    output('msg_before_collaboration')
    deliver('msg_collaboration', {'kind': 'resident', 'id': colleague})
    output('msg_after_collaboration')
    expected = ['msg_before_collaboration', 'msg_after_collaboration']
    assert [m['message_id'] for m in repo.reply_blocks([(rid, appended)])[(rid, appended)]['replies']] == expected
    page = family_facade.project_messages(facade, [request, {
        'message_id': 'msg_facade_info', 'role': 'user', 'author_id': colleague, 'held_info': True,
    }])
    assert [m['message_id'] for m in page] == ['msg_facade_user', *expected, 'msg_facade_info']
    for identity in expected:
        assert repo.message(rid, identity)['display_reply_to_message_id'] == appended
        assert family_facade.projected_reply(facade, identity)['family_reply']['request_message_id'] == 'msg_facade_user'

    # A genuine user correction delivered by a Resident still establishes a new block.
    deliver('msg_user_correction', {'kind': 'user', 'id': 'CASE-A'})
    output('msg_after_correction')
    blocks = repo.reply_blocks([(rid, appended), (rid, 'msg_user_correction')])
    assert [m['message_id'] for m in blocks[(rid, appended)]['replies']] == expected
    assert [m['message_id'] for m in blocks[(rid, 'msg_user_correction')]['replies']] == ['msg_after_correction']
    assert repo.message(rid, 'msg_after_correction')['reply_to_message_id'] == head
