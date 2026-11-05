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
