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
        self.project_summary = "Shared goal"
        self.projects = ["project_test"]

    def get(self, resource_id, *, actor):
        assert actor == "alice"
        return {"resource_id": resource_id,
                "resource_type": "project" if resource_id.startswith("project_") else "article",
                "title": resource_id, "projects": list(self.projects)}

    def action(self, resource_id, action, *, actor):
        assert actor == "alice" and action == "summary.read"
        return {"text": self.project_summary}

    def resolve(self, resource_id, *, actor):
        return {"record": self.get(resource_id, actor=actor), "exists": True}

    def query(self, *, actor, projects, limit):
        assert actor == "alice"
        return {"resources": [], "total": 0}

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
                    "project_ids": list(self.projects),
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


def test_project_context_refreshes_for_existing_resident_and_validates_before_prepare(backend, monkeypatch):
    chat, service = backend
    first = chat.prepare("alice", "article_test", "first")
    assert "Shared goal" in first["initial_context"]["context_text"]
    chat._resources.project_summary = "Updated shared goal"
    second = chat.prepare("alice", "article_test", "second")
    assert second["resident_id"] == first["resident_id"]
    assert "Updated shared goal" in second["initial_context"]["context_text"]
    chat.send_message("alice", "article_test", "send")
    parts = service.invocations[-1][0]["arguments"]["message"]["content_parts"]
    assert "Updated shared goal" in parts[1]["context"]["context_text"]
    chat._resources.projects = ["project_test", "project_second"]
    monkeypatch.setattr(chat, "_ensure", lambda *a, **kw: pytest.fail("must validate project first"))
    with pytest.raises(ValueError, match="请选择"):
        chat.prepare("alice", "article_test", "ambiguous")
    with pytest.raises(PermissionError):
        chat.send_message("alice", "article_test", "unrelated", project_id="project_foreign")
    assert len(service.invocations) == 1


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


def test_repo_chat_resolves_directory_for_read_prepare_and_send(backend, tmp_path, monkeypatch):
    """Repo 根不是字节文档；聊天读取、准备及发送均授权解析目录，沿用 Resident 入口。"""
    from urllib.parse import urlparse
    from management_portal.http_knowledge import KnowledgeHttp
    chat, service = backend
    repo_id = "repo_123456789abc"
    directory = tmp_path / "wt-sim53"
    directory.mkdir()
    calls = []

    class RepoResources:
        denied = False

        def resolve(self, address, *, actor):
            calls.append((address, actor))
            if self.denied:
                raise PermissionError("revoked")
            assert address == repo_id and actor == "alice"
            return {"resource_type": "repo", "target_kind": "repo", "exists": True,
                    "checkout_path": str(directory),
                    "record": {"resource_id": repo_id, "resource_type": "repo", "name": "ArchExplorer"}}

        def read_content(self, *args, **kwargs):
            raise AssertionError("repo root 没有字节内容")

    resources = RepoResources()
    chat._resources = resources
    monkeypatch.setattr(discussion_residents, "resolve_resource_lineage",
                        lambda **kw: "lineage_knowledge01" if kw["resource_type"] == "repo" else None)
    monkeypatch.setattr(discussion_residents.resident_handoff, "lookup_invocation_resident", lambda **_: {})
    prepared = []

    def ensure(**kwargs):
        prepared.append(kwargs)
        return {"resident_id": "resident_knowledge01", "lineage_id": "lineage_knowledge01"}

    monkeypatch.setattr(discussion_residents.resident_handoff, "ensure_invocation_resident", ensure)
    checked = []
    host = SimpleNamespace(
        session_actor=lambda request: "alice",
        require_resource=lambda resource, actor: checked.append((resource, actor)),
        knowledge_item_chat_backend=lambda: SimpleNamespace(knowledge_item_chat_backend=chat),
        respond=lambda request, status, body: (status, body),
    )
    status, state = KnowledgeHttp.get_api_item_chat(host, None,
        urlparse(f"/knowledge/api/item-chat?kb_id={repo_id}"), "mallory")
    assert status == 200 and state["binding_state"] == "unbound"
    assert state["chat_workspace"]["composer"]["can_send"]
    assert checked == [(repo_id, "alice")]
    assert state["item"]["path"] == str(directory)
    assert state["item"]["title"] == "ArchExplorer"
    assert repo_id in state["initial_context"]["context_text"]
    assert service.invocations == [], "reading must not create or send a conversation"
    assert chat.prepare("alice", repo_id, "", purpose="settings")["resident_id"]
    assert chat.prepare("alice", repo_id, "inspect")["resident_id"]
    assert service.invocations == [], "preparation must not send a model task"
    chat.send_message("alice", repo_id, "inspect")
    context = service.invocations[0][0]["arguments"]["message"]["content_parts"][1]["context"]["context_text"]
    assert str(directory) in context and repo_id in context
    assert calls == [(repo_id, "alice")] * 4
    assert [entry["title"] for entry in prepared] == ["ArchExplorer"] * 3
    resources.denied = True
    for operation in [lambda: chat.get_state("alice", repo_id),
                      lambda: chat.prepare("alice", repo_id, "inspect"),
                      lambda: chat.send_message("alice", repo_id, "inspect")]:
        with pytest.raises(PermissionError, match="revoked"):
            operation()
    assert len(service.invocations) == 1
