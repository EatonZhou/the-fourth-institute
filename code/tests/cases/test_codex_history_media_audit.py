"""Production native tool capture must keep images typed across a Fork."""
from __future__ import annotations

import copy
import json

import pytest

from management_portal.execution.agent_data_output_capture import live_tool_segment_id
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.codex_history import response_items_from_segments
from management_portal.execution.native_tool_events import NativeToolEventTranslator, tool_completed
from tests.cases.test_agent_data_projection import _runtime_session, _turn_request


def _replay(result, *, status="completed", error=None):
    return response_items_from_segments([{
        "segment_type": "tool", "status": status,
        "content": {"tool_name": "inspect", "call_id": "audit-call",
                    "input": {"arguments": {"path": "/work/figure.png"}},
                    "result": result, "error": error},
    }])[1]["output"]


@pytest.mark.parametrize(("part", "expected"), [
    ({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "YWJj"}},
     "data:image/jpeg;base64,YWJj"),
    ({"type": "image", "mimeType": "image/png", "data": "YWJj"}, "data:image/png;base64,YWJj"),
    ({"type": "image", "source": {"type": "url", "url": "https://example.test/a.png"}},
     "https://example.test/a.png"),
    ({"type": "image_url", "image_url": {"url": "https://example.test/a.png", "detail": "high"}},
     "https://example.test/a.png"),
    ({"type": "inputImage", "imageUrl": "data:image/webp;base64,YWJj"}, "data:image/webp;base64,YWJj"),
])
def test_production_translator_preserves_declared_image_transport(part, expected):
    """Provider source blocks retain actual MIME or URL through common events.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    original = copy.deepcopy(part)
    translator = NativeToolEventTranslator()
    translator.translate({"type": "response_item", "payload": {
        "type": "function_call", "call_id": "audit-call", "name": "inspect", "arguments": {},
    }})
    completed, = translator.translate({"type": "response_item", "payload": {
        "type": "function_call_output", "call_id": "audit-call", "output": [part],
    }})
    assert completed["params"]["item"]["contentItems"] == [{"type": "inputImage", "imageUrl": expected}]
    assert part == original


@pytest.mark.parametrize("part", [
    {"type": "image", "source": {"type": "base64", "data": "YWJj"}},
    {"type": "image_url", "image_url": {"url": "not-an-image-url"}},
    {"type": "image", "data": "YWJj"},
    {"type": "inputImage", "imageUrl": {"unexpected": "object"}},
])
def test_explicit_unknown_image_transport_is_not_invented(part):
    """Typed images with missing transport facts fail instead of inventing MIME.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    with pytest.raises(ValueError, match="image"):
        tool_completed("audit-call", "inspect", {}, [part])


@pytest.mark.parametrize("failed", [False, True])
def test_captured_native_media_and_failure_survive_history_replay(failed):
    """Real canonical capture preserves typed images and failed state without error text.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    store = AgentDataStore()
    request = _turn_request("resident_media_audit")
    session = _runtime_session(request, "audit-session")
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    event = tool_completed("audit-call", "inspect", {"path": "/work/a.png"}, [
        {"type": "text", "text": "partial figure"},
        {"type": "inputImage", "imageUrl": "data:image/png;base64,YWJj"},
    ], failed=failed)
    projector.observe(provider="codex", backend_route_id="zero", backend_session_id="audit-session",
                      execution_id=request.execution_id, event=event)
    captured = store.segment(live_tool_segment_id(request.execution_id, "audit-call"))
    assert captured["status"] == ("failed" if failed else "completed")
    assert captured["content"]["error"] is None
    original = copy.deepcopy(captured)
    output = response_items_from_segments([captured])[1]["output"]
    assert output[:2] == [
        {"type": "input_text", "text": "partial figure"},
        {"type": "input_image", "image_url": "data:image/png;base64,YWJj"},
    ]
    if failed:
        assert any(part["type"] == "input_text" and json.loads(part["text"]).get("status") == "failed"
                   for part in output[2:])
    output[1]["image_url"] = "changed"
    assert captured == original


def test_dynamic_media_retains_result_error_and_sibling_metadata():
    """The capture result wrapper retains metadata surrounding dynamic image blocks.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    result = {"result": {"contentItems": [{"type": "inputImage", "imageUrl": "https://example.test/a.png"}],
                         "success": False}, "stderr": "partial"}
    original = copy.deepcopy(result)
    output = _replay(result, error={"code": "partial"})
    assert output[0] == {"type": "input_image", "image_url": "https://example.test/a.png"}
    assert [json.loads(part["text"]) for part in output[1:]] == [
        {"success": False}, {"stderr": "partial"}, {"error": {"code": "partial"}},
    ]
    assert result == original


def test_nested_business_image_descriptors_remain_json():
    """An ordinary business dictionary is not recursively treated as tool media.

    :module: execution.service
    :importance: P1
    :design: decision_3e8c2a7f91d4
    """
    business = {"files": [{"type": "image", "path": "/work/a.png"}], "count": 1}
    assert json.loads(_replay(business)) == business
