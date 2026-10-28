"""File approval scope comes from the original item on the owning connection."""
from copy import deepcopy

import pytest

from management_common.provider_user_input import content_part
from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import GoalWaiter, TurnWaiter


METHOD = "item/fileChange/requestApproval"


def fixture_client(tmp_path, goal=False):
    client = CodexAppServerClient(tmp_path / "absent.sock")
    events, sent = [], []
    client.rpc.send = sent.append
    client.user_inputs._send = sent.append
    waiter_type = GoalWaiter if goal else TurnWaiter
    waiters = client.work.goals if goal else client.work.turns
    waiters["thread-scope"] = waiter_type("thread-scope", turn_id="turn-scope", event_callback=events.append)
    return client, events, sent


def original_item(**overrides):
    return {"threadId": "thread-scope", "turnId": "turn-scope", "item": {
        "id": "file-item", "type": "fileChange", "status": "inProgress", "changes": [
            {"path": "/workspace/原文件.txt", "kind": {"type": "update", "move_path": "/workspace/新文件.txt"},
             "diff": "@@ -1 +1 @@\n-before\n+<script>after</script>\n"}]}, **overrides}


def request(**overrides):
    return {"threadId": "thread-scope", "turnId": "turn-scope", "itemId": "file-item",
            "reason": None, "grantRoot": None, **overrides}


def pending(events):
    return next(event["params"]["item"] for event in reversed(events)
                if event["params"].get("item", {}).get("user_input", {}).get("status") == "pending")


@pytest.mark.parametrize("goal", [False, True])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_exact_original_scope_is_immutable_and_never_changes_wire_answer(tmp_path, goal, action):
    client, events, sent = fixture_client(tmp_path, goal)
    item = original_item()
    expected = deepcopy(item["item"]["changes"])
    client.events.dispatch_notification("item/started", item)
    item["item"]["changes"][0]["diff"] = "later change must not replace captured scope"
    wire = request()
    client.events.answer_server_request(81, METHOD, wire)
    part = content_part(pending(events), "execution-scope")
    assert part["interaction"]["request"] == wire
    assert "file_change" not in wire
    assert part["interaction"]["file_change"] == {
        "thread_id": "thread-scope", "turn_id": "turn-scope", "item_id": "file-item", "changes": expected}
    assert sent == []
    response = {"request_id": part["request_id"], "action": action}
    client.user_inputs.respond("thread-scope", "turn-scope", response)
    assert sent == [{"id": 81, "result": {"decision": action}}]
    with pytest.raises(ValueError, match="expired"):
        client.user_inputs.respond("thread-scope", "turn-scope", response)


@pytest.mark.parametrize("scope_case", ["missing", "thread", "turn", "item", "completed", "no_owner"])
def test_scope_cannot_be_borrowed_from_other_native_identity_or_completed_item(tmp_path, scope_case):
    client, events, sent = fixture_client(tmp_path)
    item = original_item()
    if scope_case == "thread": item["threadId"] = "other-thread"
    if scope_case == "turn": item["turnId"] = "other-turn"
    if scope_case == "item": item["item"]["id"] = "other-item"
    if scope_case == "no_owner": owner = client.work.turns.pop("thread-scope")
    if scope_case != "missing":
        client.events.dispatch_notification("item/completed" if scope_case == "completed" else "item/started", item)
    if scope_case == "no_owner": client.work.turns["thread-scope"] = owner
    client.events.answer_server_request(82, METHOD, request())
    assert "file_change" not in pending(events)["user_input"]["interaction"]
    assert sent == []


@pytest.mark.parametrize("expiry", ["turn", "thread", "connection"])
def test_expired_connection_scope_is_not_reused(tmp_path, expiry):
    client, events, sent = fixture_client(tmp_path)
    client.events.dispatch_notification("item/started", original_item())
    if expiry == "turn":
        client.events.dispatch_notification("turn/completed", {"threadId": "thread-scope", "turn": {"id": "turn-scope", "status": "completed"}})
    elif expiry == "thread":
        client.user_inputs.expire("thread-scope")
    else:
        client.user_inputs.expire()
    client.events.answer_server_request(83, METHOD, request())
    assert "file_change" not in pending(events)["user_input"]["interaction"]
    assert sent == []


def test_scope_does_not_extend_permissions_approval(tmp_path):
    client, events, sent = fixture_client(tmp_path)
    client.events.dispatch_notification("item/started", original_item())
    client.events.answer_server_request(84, "item/permissions/requestApproval", request(permissions={"network": {"enabled": True}}))
    part = pending(events)["user_input"]
    assert "file_change" not in part["interaction"]
    client.user_inputs.respond("thread-scope", "turn-scope", {"request_id": part["request_id"], "action": "accept"})
    assert sent == [{"id": 84, "result": {"permissions": {"network": {"enabled": True}}, "scope": "turn"}}]
