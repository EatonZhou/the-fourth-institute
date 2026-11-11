""":importance: P3"""
from __future__ import annotations

from management_portal import discussion_residents

from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import isa_client, knowledge_item_chat_backend
from management_portal.knowledge_item_chat_backend import KnowledgeItemChatBackend


class _Resources:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read_content(self, resource_id: str, *, actor: str):
        return (
            self.path.read_bytes(),
            "text/markdown",
            {
                "resolved_path": str(self.path),
                "record": {
                    "resource_id": resource_id,
                    "title": "Test Article",
                    "type": "article",
                    "project": "project_test",
                },
            },
        )


class _ResidentService:
    def __init__(self) -> None:
        self.invocations: list[tuple[dict, str]] = []

    def call(self, actor: str, instruction: str, arguments: dict, *, key: str = "") -> dict:
        self.invocations.append(({"instruction": instruction, "arguments": arguments, "idempotency_key": key}, actor))
        return {"message_id": "message_one"}


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "article.md"
    source.write_text("# Current article\n\nBody", encoding="utf-8")
    service = _ResidentService()
    monkeypatch.setattr(
        "management_portal.knowledge_item_chat_backend.get_society_service",
        lambda: SimpleNamespace(
            workspace=SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(isa_client, "call", service.call)
    def resolve_resource_lineage(*, actor, resource_type):
        assert actor == "alice"
        assert resource_type == "article"
        return "lineage_knowledge01"
    monkeypatch.setattr(discussion_residents, "resolve_resource_lineage", resolve_resource_lineage)
    monkeypatch.setattr(discussion_residents.resident_handoff, "lookup_invocation_resident", lambda **_: {
        "resident_id": "resident_knowledge01", "lineage_id": "lineage_knowledge01"})
    monkeypatch.setattr(
        discussion_residents.resident_handoff,
        "ensure_invocation_resident",
        lambda **_kwargs: {
            "resident_id": "resident_knowledge01",
            "lineage_id": "lineage_knowledge01",
        },
    )
    monkeypatch.setattr(
        "management_portal.knowledge_item_chat_backend.agent_context_selection.resolve",
        lambda *_args, **_kwargs: [],
    )
    return KnowledgeItemChatBackend(resource_service=_Resources(source)), service


def test_knowledge_item_state_projects_the_canonical_resident_chat(backend) -> None:
    """知识条目对话状态投影条目对应 Resident 的对话，初始上下文含条目 ID 和资源路径"""
    chat, _service = backend
    state = chat.get_state("alice", "article_test")
    assert state["username"] == "alice"
    assert state["resident_id"] == "resident_knowledge01"
    assert state["chat_workspace"]["target"]["id"] == "resident_knowledge01"
    assert state["chat_workspace"]["messages"] == []
    assert "article_test" in state["initial_context"]["context_text"]
    assert "资源路径" in state["initial_context"]["context_text"]


def test_explicit_settings_prepares_without_sending_and_reuses_discussion(backend, monkeypatch):
    """读取不创建；显式设置可在首条消息前创建并复用同一讨论，且不投递消息。"""
    chat, service = backend
    created = []
    identity = {"resident_id": "resident_knowledge01", "lineage_id": "lineage_knowledge01"}
    monkeypatch.setattr(discussion_residents.resident_handoff, "lookup_invocation_resident",
                        lambda **_: identity if created else {})

    def ensure(**kwargs):
        created.append(kwargs)
        return identity

    monkeypatch.setattr(discussion_residents.resident_handoff, "ensure_invocation_resident", ensure)
    state = chat.get_state("alice", "article_test")
    assert state["binding_state"] == "unbound"
    assert "模型" in state["chat_workspace"]["composer"]["meta"]
    assert not created
    with pytest.raises(ValueError, match="message required"):
        chat.prepare("alice", "article_test", "")
    with pytest.raises(ValueError, match="purpose"):
        chat.prepare("alice", "article_test", "", purpose="unknown")
    assert not created
    configured = chat.prepare("alice", "article_test", "", purpose="settings")
    assert configured["resident_id"] == identity["resident_id"]
    assert chat.get_state("alice", "article_test")["resident_id"] == configured["resident_id"]
    first_send = chat.prepare("alice", "article_test", "first real message")
    assert first_send["resident_id"] == configured["resident_id"]
    assert created[0]["resident_scope"] == created[1]["resident_scope"]
    assert not service.invocations, "preparation must never send a model task"
    monkeypatch.setattr(chat, "_item_snapshot", lambda *_: (_ for _ in ()).throw(PermissionError("revoked")))
    with pytest.raises(PermissionError, match="revoked"):
        chat.prepare("alice", "article_test", "", purpose="settings")
    assert len(created) == 2


def test_settings_preparation_route_forwards_intent_and_authenticated_actor(backend):
    from management_portal.http_knowledge import KnowledgeHttp
    chat, service = backend
    host = SimpleNamespace(
        knowledge_item_chat_backend=lambda: SimpleNamespace(knowledge_item_chat_backend=chat),
        respond=lambda request, status, body: (status, body),
    )
    status, result = KnowledgeHttp.post_chat_prepare(host, None,
        {"kb_id": "article_test", "purpose": "settings", "actor": "mallory"}, "alice")
    assert status == 200
    assert result["username"] == "alice"
    assert result["resident_id"] == "resident_knowledge01"
    assert not service.invocations


def test_knowledge_message_uses_resource_body_as_runtime_context(backend) -> None:
    """知识条目对话消息经 DeliverMessage 投递，条目正文作为上下文引用随消息发送"""
    chat, service = backend
    chat.send_message("alice", "article_test", "inspect")
    instruction, actor = service.invocations[0]
    assert actor == "alice"
    assert instruction["instruction"] == "DeliverMessage"
    payload = instruction["arguments"]
    assert payload["resident_id"] == "resident_knowledge01"
    assert payload["activation_policy"] == "enqueue_and_trigger"
    parts = payload["message"]["content_parts"]
    assert parts[0] == {"type": "text", "text": "inspect"}
    assert parts[1]["type"] == "context_reference"
    assert "Current article" in parts[1]["context"]["context_text"]


def test_knowledge_image_is_stored_for_actor_and_delivered_as_image(backend, monkeypatch) -> None:
    """知识条目对话中的图片以发送者身份存入 Resident 工作区，并作为图片投递"""
    chat, service = backend
    uploads = []

    def save_image(payload, actor):
        uploads.append((payload, actor))
        return {"filename": "screenshot.png", "path": "/staging/alice/screenshot.png", "content_type": "image/png"}

    monkeypatch.setattr(
        knowledge_item_chat_backend,
        "get_society_service",
        lambda: SimpleNamespace(workspace=SimpleNamespace(save_workspace_pasted_image=save_image)),
    )
    chat.send_message("alice", "article_test", "请看图片", [{
        "name": "screenshot.png", "mime": "image/png", "data_url": "data:image/png;base64,aW1hZ2U=",
    }], message_id="image_message")
    assert uploads == [({
        "resident_id": "resident_knowledge01", "filename": "screenshot.png",
        "content_type": "image/png", "data_base64": "aW1hZ2U=",
    }, "alice")]
    instruction, actor = service.invocations[0]
    assert actor == "alice"
    payload = instruction["arguments"]
    assert payload["message"]["message_id"] == "image_message"
    assert payload["message"]["content_parts"][-1] == {
        "type": "image", "media_type": "image", "source_type": "local_path",
        "source": "/staging/alice/screenshot.png", "name": "screenshot.png",
    }


def test_knowledge_attachment_rejects_unverified_local_paths(backend) -> None:
    """知识条目对话拒绝未经核实的本地路径附件，只接受 base64 data URL"""
    chat, service = backend
    with pytest.raises(ValueError, match="base64 data URL"):
        chat.send_message("alice", "article_test", "inspect", [{"path": "/staging/other/secret.png"}])
    assert service.invocations == []


def test_knowledge_resource_identity_must_match_resolution(tmp_path: Path) -> None:
    """读取到的资源 ID 与解析结果不一致时，知识条目对话拒绝加载"""
    source = tmp_path / "article.md"
    source.write_text("body", encoding="utf-8")

    class Wrong(_Resources):
        def read_content(self, resource_id: str, *, actor: str):
            data, content_type, resolved = super().read_content(resource_id, actor=actor)
            resolved["record"]["resource_id"] = "article_other"
            return data, content_type, resolved

    chat = KnowledgeItemChatBackend(resource_service=Wrong(source))
    with pytest.raises(ValueError, match="mismatch"):
        chat.get_state("alice", "article_test")
