from unittest import mock

import pytest

from management_portal import (
    knowledge_archive_backend,
)


IMAGE = {
    "media_type": "image", "source_type": "local_path",
    "source": "/srv/agent-staging/shared/portal_uploads/resident_chat_images/CASE-A/test.png",
    "mime_type": "image/png", "name": "test.png",
}


def test_archive_proxy_preserves_native_media_contract(monkeypatch):
    """知识归档 Chat 代理把图片 input_media 原样转发给归档后端"""
    monkeypatch.setattr(knowledge_archive_backend, "resolve_resource_references", lambda **_kwargs: [])
    client = knowledge_archive_backend.KnowledgeArchiveBackendClient()
    with mock.patch.object(client, "_request", return_value={}) as request:
        client.send_message("CASE-A", "ka_test", "看图", message_id="msg_image", input_media=[IMAGE])
    assert request.call_args.args == ("POST", "/api/archive/chat", {
        "actor": "CASE-A", "archive_id": "ka_test", "content": "看图", "message_id": "msg_image", "input_media": [IMAGE],
    })




