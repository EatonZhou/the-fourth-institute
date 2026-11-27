from unittest.mock import Mock

import pytest

from management_portal.kimi_transcript_client import KimiTranscriptClient, TurnItems


def test_transcript_replay_after_disconnect_keeps_final_text_once():
    events = []
    items = TurnItems("session", "prompt", events.append)
    frame = {"kind": "text", "frameId": "answer", "role": "assistant", "text": "答"}
    turn = {"state": "running", "steps": [{"state": "running", "frames": [frame]}]}
    assert items.render(turn)[0] == ""
    frame["text"] += "案"
    turn["state"] = turn["steps"][0]["state"] = "completed"
    turn["steps"][0]["frames"].append({"kind": "notice", "frameId": "notice"})
    assert items.render(turn)[0] == "答案"
    items.render(turn)
    assert [e["params"]["delta"] for e in events if "delta" in e["params"]] == ["答", "案"]
    assert [e["params"]["item"]["text"] for e in events if e["method"] == "item/completed"] == ["答案"]


def test_old_prompt_is_resolved_by_identity_across_transcript_pages():
    client = KimiTranscriptClient("http://localhost", home=".")
    requested = {"kind": "turn", "turnId": "old", "triggerPromptId": "p"}
    client.transcript = Mock(return_value={"items": [requested], "has_more": False})
    page = {"items": [{"kind": "turn", "turnId": "new", "triggerPromptId": "other"}], "has_more": True}
    assert client._prompt_turn("s", "p", page, True)[1] is requested
    client.transcript.assert_called_once_with("s", before="new")


def test_approvals_are_scoped_to_turn_tool_and_sent_once():
    client = KimiTranscriptClient("http://localhost", home=".")
    client._interaction = Mock()
    owner = {"state": "running", "steps": [{"frames": [{"kind": "tool", "toolCallId": "a"}]}]}
    req = {"interactionId": "id", "toolCallId": "a", "interactionKind": "approval",
           "state": "pending", "request": {"toolName": "Write"}}
    page = {"interactions": [dict(req, toolCallId="unrelated"), req]}
    resolved = set()
    for _ in range(2):
        client._resolve_interactions("s", page, owner, ["Read"], resolved)
    client._interaction.assert_called_once_with("s", "id", {"kind": "approval", "decision": "rejected", "feedback": ""})


def test_individual_subagent_stop_preserves_parent_turn():
    client = KimiTranscriptClient("http://localhost", home=".")
    client._request = Mock(side_effect=AssertionError("must not cancel the session"))
    assert client.stop_subagent(session_id="s", tool_use_id="child") == {
        "stopped": False, "reason": "unsupported_by_provider"}


def test_native_question_answer_is_bound_to_the_prompt_and_sent_once(tmp_path):
    client = KimiTranscriptClient("http://localhost", home=tmp_path)
    client._interaction = Mock()
    events = []
    question = {"id": "q", "question": "选择", "allow_other": True, "multi_select": True,
                "options": [{"id": "a", "label": "A"}]}
    req = {"interactionId": "i", "request": {"questions": [question]}}
    client._open_question("session", "prompt", req, events.append)
    key = events[0]["params"]["item"]["user_input"]["request_id"]
    reply = {"request_id": key, "answers": {"q": {"answers": ["A", "自填"]}}}
    with pytest.raises(ValueError):
        client.answer_question("session", "other", reply)
    client.answer_question("session", "prompt", reply)
    with pytest.raises(ValueError):
        client.answer_question("session", "prompt", reply)
    client._interaction.assert_called_once_with("session", "i", {"kind": "question", "method": "click",
        "answers": {"q": {"kind": "multi_with_other", "option_ids": ["a"], "other_text": "自填"}}})
    assert events[-1]["params"]["item"]["user_input"]["status"] == "answered"
