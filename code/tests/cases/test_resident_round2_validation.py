from management_portal.society_service import resident_conversation_stream
from pathlib import Path

import pytest

from management_portal.society_service import resident_domain, resident_protocol
from tests.cases.test_resident_folder_creation_contract import (
    _new_session_payload,
    future_creation_store,
)


RECONCILE_INPUT = resident_protocol.get_input_reconciliation_state
COMPLETED_EVIDENCE = resident_protocol.get_completed_message_evidence


from management_portal.society_service import resident_conversation_projection_domain, resident_workspace_domain

def message(message_id: str, role: str, text: str) -> dict:
    return {
        "message_id": message_id,
        "role": role,
        "author_id": "sample_admin" if role == "user" else "fixture-agent",
        "content_parts": [{"type": "text", "text": text}],
    }


@pytest.mark.parametrize("actor", ["CASE-A", "sample_admin", "resident_b8eee35e8db7"])
def test_actual_prompt_checks_sensitive_permission_before_resolving(monkeypatch, tmp_path, actor):
    """用户和 Resident 可读取实际发送内容，Portal 服务或匿名调用在解析 Resident 之前被拒绝"""
    resident_id = "resident_57acd34d2cd0"
    # Any User or Resident may read; a Portal service or nobody may not, and is refused before the Resident resolves.
    assert resident_conversation_projection_domain._can_view_actual_sent_prompts(actor, resident_id)
    monkeypatch.setattr(resident_workspace_domain, "resolve_resident_runtime_target", lambda *_: pytest.fail("refused prompt must not resolve"))
    for refused in ("", "portal"):
        assert not resident_conversation_projection_domain._can_view_actual_sent_prompts(refused, resident_id)
        with pytest.raises(PermissionError, match="无权读取实际发送内容"):
            resident_domain.read_actual_sent_prompt(resident_id, refused, "fixture-message")


@pytest.mark.parametrize("actor", ["CASE-A", "sample_admin", "resident_b8eee35e8db7"])
def test_actual_prompt_returns_only_requested_synthetic_provider_message(
    future_creation_store: Path, monkeypatch: pytest.MonkeyPatch, actor: str,
) -> None:
    """读取实际发送内容只返回所请求消息的 Provider 输入，消息不存在时报错"""
    payload = _new_session_payload("Round 2 prompt fixture")
    record = resident_domain.register_resident(payload, "sample_admin")
    resident_id = record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    repository.append_message(resident_id, message("round2-prompt", "user", "synthetic input"))

    def attach(messages, **kwargs):
        assert [item["message_id"] for item in messages] == ["round2-prompt"]
        messages[0]["actual_sent_prompt"] = "synthetic provider envelope"
        messages[0]["actual_sent_prompt_source"] = "isolated-fixture"

    monkeypatch.setattr(resident_conversation_projection_domain, "_attach_actual_sent_prompts", attach)
    actual = resident_domain.read_actual_sent_prompt(resident_id, actor, "round2-prompt")
    assert actual == {
        "resident_id": resident_id, "message_id": "round2-prompt",
        "content": "synthetic provider envelope", "source": "isolated-fixture",
    }
    with pytest.raises(KeyError):
        resident_domain.read_actual_sent_prompt(resident_id, actor, "absent-prompt")


@pytest.mark.parametrize("state,with_reply,expected", [
    (None, False, "missing"),
    ("queued", False, "queued"),
    ("running", False, "running"),
    ("failed", False, "failed"),
    ("cancelled", False, "cancelled"),
    ("completed", False, "missing"),
    ("completed", True, "completed"),
])
def test_reconciliation_requires_persisted_completion_evidence(
    future_creation_store: Path, monkeypatch: pytest.MonkeyPatch,
    state: str | None, with_reply: bool, expected: str,
) -> None:
    """输入核对只在有已保存的回复时报告 completed，否则如实返回排队、运行、失败或缺失"""
    record = resident_domain.register_resident(
        _new_session_payload("Round 2 input reconciliation"), "sample_admin"
    )
    resident_id = record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    repository.append_message(resident_id, message("round2-input", "user", "synthetic request"))
    reply_ids = []
    if with_reply:
        repository.append_message(resident_id, {**message("round2-reply", "agent", "synthetic final"),
                                                "reply_to_message_id": "round2-input"})
        reply_ids.append("round2-reply")
    if state:
        repository.set_input_state(resident_id, "round2-input", state, updated_at="2026-10-02T00:56:41Z")
    monkeypatch.setattr(resident_protocol, "get_completed_message_evidence", COMPLETED_EVIDENCE)
    result = RECONCILE_INPUT(resident_id, "sample_admin", "round2-input")
    assert result["status"] == expected
    assert result["message_id"] == "round2-input"
    if state in {"completed", "failed", "cancelled"}:
        assert result["completed_at"] == "2026-10-02T00:56:41Z"
    if expected == "completed":
        assert result["evidence"]["reply"] == "synthetic final"
        assert result["evidence"]["reply_message_ids"] == reply_ids
    else:
        assert result["evidence"] == {}
