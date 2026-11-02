"""Native reasoning survives capture while display remains a readable projection."""
from copy import deepcopy

import pytest

from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.provider_event_projection import project_provider_event
from management_portal.execution.provider_reasoning import provider_reasoning
from management_portal.execution.runtime_details import EventContext, RuntimeDetails
from management_portal.execution.session_history_import import validate_codex_preservation
from tests.cases.test_agent_data_projection import _runtime_session, _turn_request


def _native_item():
    return {
        "type": "reasoning", "id": "thought-one",
        "summary": [{"type": "summary_text", "text": "public summary"}],
        "content": [], "encrypted_content": "opaque-native-context",
    }


@pytest.mark.parametrize("envelope", ["response_item", "notification"])
def test_native_reasoning_capture_preserves_context_without_chat_exposure(envelope):
    """Native typed content imports exactly; replay is idempotent and display excludes ciphertext.

    :module: execution.service
    :importance: P1
    :design: decision_b1ab45c4b3ec
    """
    native = _native_item()
    event = {"type": "response_item", "payload": native}
    if envelope == "notification":
        event = {
            "method": "item/completed",
            "params": {"turnId": "turn-one", "item": native},
        }
    normalized = provider_reasoning(event)
    assert normalized.native_content == native
    assert normalized.content == {"summary": ["public summary"], "encrypted": True}
    assert native["encrypted_content"] not in repr(normalized)
    native["summary"][0]["text"] = "mutated after normalization"
    assert normalized.native_content["summary"][0]["text"] == "public summary"
    native["summary"][0]["text"] = "public summary"

    store = AgentDataStore()
    request = _turn_request("resident_native_reasoning")
    projector = AgentDataProjector(store)
    projector.begin(request, _runtime_session(request, "session-one"))
    for _ in range(2):
        projector.observe(provider="codex", backend_route_id="zero", backend_session_id="session-one",
                          execution_id=request.execution_id, event=event)
    segments = store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"]
    thoughts = [s for s in segments if s["metadata"].get("kind") == "reasoning"]
    assert len(thoughts) == 1
    assert thoughts[0]["content"] == native
    assert thoughts[0]["metadata"]["representation"] == "native"
    assert validate_codex_preservation(thoughts) == [native]
    assert store.read_chat_messages(request.agent_id) == []
    details = RuntimeDetails(include_updates=True)
    project_provider_event(event, EventContext(1, "2026-11-02T00:00:00Z", request.execution_id), details)
    assert native["encrypted_content"] not in repr(details.__dict__)
    if envelope == "notification":
        assert "public summary" in repr(details.__dict__)
    changed = deepcopy(event)
    changed_native = changed.get("payload") or changed["params"]["item"]
    changed_native["encrypted_content"] = "different-native-context"
    with pytest.raises(ValueError, match="immutable content"):
        projector.observe(provider="codex", backend_route_id="zero", backend_session_id="session-one",
                          execution_id=request.execution_id, event=changed)


def test_summary_only_reasoning_remains_incomplete():
    """A summary cannot become complete preserved context.

    :module: execution.service
    :importance: P1
    :design: decision_b1ab45c4b3ec
    """
    event = {"method": "item/completed", "params": {"item": {
        "type": "reasoning", "id": "thought-one", "summary": ["public summary"],
    }}}
    normalized = provider_reasoning(event)
    assert normalized.native_content is None
    with pytest.raises(ValueError, match="Cannot preserve reasoning"):
        validate_codex_preservation([{"segment_type": "model", "content": normalized.content,
                                      "metadata": {"kind": "reasoning"}}])


def test_native_only_reasoning_can_be_captured_without_readable_summary():
    """Opaque native context is retained even if the notification has no readable blocks.

    :module: execution.service
    :importance: P1
    :design: decision_b1ab45c4b3ec
    """
    native = {**_native_item(), "summary": []}
    event = {"method": "item/completed", "params": {"item": native}}
    normalized = provider_reasoning(event)
    assert normalized.content == {"encrypted": True}
    assert normalized.native_content == native
