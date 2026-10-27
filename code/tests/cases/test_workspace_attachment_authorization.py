import base64
from pathlib import Path

import pytest

from management_portal.society_service import (
    resident_store,
    workspace_contracts,
    workspace_domain,
)
from management_portal.society_service import resident_media_domain
from management_portal.society_service.workspace_attachments import authorize_attachment_read


VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def conversation_uploads(tmp_path, monkeypatch):
    for module in (workspace_domain, workspace_contracts):
        monkeypatch.setattr(module, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
        monkeypatch.setattr(module, "CHAT_ATTACHMENT_CACHE_ROOT", tmp_path / "files")
    conversation_id = "resident_aaaaaaaaaaaa"
    viewers = {"CASE-A", "reader", "resident_bbbbbbbbbbbb"}

    def resolve_identity(resident_id, actor):
        if resident_id != conversation_id or actor not in viewers:
            raise PermissionError("Conversation is not visible")
        return {"resident_id": resident_id, "lineage_id": "lineage_aaaaaaaaaaaa"}

    monkeypatch.setattr(
        workspace_domain.resident_domain_port, "resolve_resident_identity", resolve_identity,
    )
    uploads = {}
    for kind, save, filename, content_type in (
        ("image", workspace_domain.save_workspace_pasted_image, "test.png", "image/png"),
        ("file", workspace_domain.save_workspace_attachment, "test.txt", "text/plain"),
    ):
        uploads[kind] = save({
            "resident_id": conversation_id,
            "filename": filename,
            "content_type": content_type,
            "data_bytes": VALID_PNG_BYTES if kind == "image" else b"attachment bytes",
        }, "CASE-A")
    return uploads, viewers


def test_conversation_attachment_viewers_share_preview_and_input(conversation_uploads, subtests):
    """能查看对话的用户和 Resident 都可预览附件并作为输入转发，但不能修改"""
    uploads, viewers = conversation_uploads
    for kind, upload in uploads.items():
        path = Path(upload["path"])
        metadata_path = path.with_suffix(path.suffix + ".json")
        original_metadata = metadata_path.read_bytes()
        for actor in sorted(viewers):
            with subtests.test(kind=kind, actor=actor):
                assert workspace_domain.resolve_workspace_link_file_path(str(path), actor=actor) == path
                preview = workspace_domain.get_workspace_link_file(str(path), actor=actor)
                assert preview["allows"] == {"UpdateWorkspaceFile": False}
                if kind == "image":
                    assert workspace_domain.resolve_workspace_pasted_image(upload["file_id"], actor)[0] == path
                media = resident_media_domain.resident_input_media(
                    [{"kind": kind, "path": str(path)}],
                    actor=actor,
                    resident_id="resident_cccccccccccc",
                    content="forward original attachment",
                )
                assert media[0]["source"] == str(path)
                assert metadata_path.read_bytes() == original_metadata
                # An attachment is in no Workspace: its preview has no save button.
                assert preview["allows"] == {"UpdateWorkspaceFile": False} and preview["file_address"] == ""


def test_conversation_attachment_denial_applies_to_uploader_and_resident(conversation_uploads, subtests):
    """对话不可见时上传者本人和 Resident 都不能读取或转发其中的附件"""
    uploads, viewers = conversation_uploads
    viewers.clear()
    for kind, upload in uploads.items():
        for actor in ("CASE-A", "stranger", "resident_bbbbbbbbbbbb"):
            with subtests.test(kind=kind, actor=actor):
                with pytest.raises(PermissionError, match="Conversation"):
                    workspace_domain.resolve_workspace_link_file_path(upload["path"], actor=actor)
                if kind == "image":
                    with pytest.raises(PermissionError, match="Conversation"):
                        workspace_domain.resolve_workspace_pasted_image(upload["file_id"], actor)
                with pytest.raises(PermissionError, match="Conversation"):
                    resident_media_domain.resident_input_media(
                        [{"kind": kind, "path": upload["path"]}],
                        actor=actor,
                        resident_id="resident_cccccccccccc",
                        content="retry",
                    )


def test_attachment_drafts_and_integrity_remain_private(conversation_uploads, tmp_path, subtests, monkeypatch):
    """未绑定对话的附件草稿只有上传者可读，元数据身份不符、缺失或符号链接越界时拒绝读取

    权限系统开启时未绑定对话的附件草稿只有上传者可读；权限系统关闭时已登录主体都可读取。不论开关，未登录读取、元数据身份不符、缺失或符号链接越界都被拒绝。

    :design: decision_b820faf42218
    """
    from tests.cases import permission_test_support

    uploads, _viewers = conversation_uploads
    root = workspace_domain.CHAT_ATTACHMENT_CACHE_ROOT
    draft = workspace_domain.save_workspace_attachment({
        "filename": "draft.txt", "data_bytes": b"private draft",
    }, "CASE-A")
    path = Path(draft["path"])
    with subtests.test(contract="unbound draft"):
        assert workspace_domain.resolve_workspace_link_file_path(str(path), actor="reader") == path
        permission_test_support.enable(monkeypatch)
        assert authorize_attachment_read(path, "CASE-A", root=root)["resident_id"] == ""
        for actor in ("reader", "resident_bbbbbbbbbbbb"):
            with pytest.raises(PermissionError, match="private"):
                workspace_domain.resolve_workspace_link_file_path(str(path), actor=actor)
        with pytest.raises(PermissionError, match="authenticated"):
            authorize_attachment_read(path, "", root=root)
    path = Path(uploads["file"]["path"])
    metadata_path = path.with_suffix(path.suffix + ".json")
    metadata = resident_store.read_json(metadata_path, {})
    with subtests.test(contract="identity mismatch"):
        resident_store.write_json(metadata_path, {**metadata, "file_id": "CASE-A/wrong.txt"})
        with pytest.raises(PermissionError, match="identity mismatch"):
            workspace_domain.resolve_workspace_link_file_path(str(path), actor="CASE-A")
    with subtests.test(contract="missing metadata"):
        metadata_path.unlink()
        with pytest.raises(PermissionError, match="metadata is missing"):
            workspace_domain.resolve_workspace_link_file_path(str(path), actor="CASE-A")
    with subtests.test(contract="metadata symlink escape"):
        outside = tmp_path / "outside.json"
        resident_store.write_json(outside, metadata)
        metadata_path.symlink_to(outside)
        with pytest.raises(PermissionError, match="escapes upload root"):
            authorize_attachment_read(path, "CASE-A", root=root)
    with subtests.test(contract="file symlink escape"):
        outside = tmp_path / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        symlink = root / "escape.txt"
        symlink.symlink_to(outside)
        with pytest.raises(PermissionError, match="escapes upload root"):
            authorize_attachment_read(symlink, "CASE-A", root=root)


def test_chat_image_upload_validates_decoded_bytes_and_pixel_limit(tmp_path, monkeypatch):
    """聊天图片上传按实际字节校验是否为有效图片，并限制像素数"""
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    with pytest.raises(ValueError, match="有效图片"):
        workspace_domain.save_workspace_pasted_image({
            "filename": "spoofed.png",
            "content_type": "image/png",
            "data_bytes": b"not an image",
        }, "CASE-A")
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_MAX_PIXELS", 0)
    with pytest.raises(ValueError, match="像素数"):
        workspace_domain.save_workspace_pasted_image({
            "filename": "one-pixel.png",
            "content_type": "image/png",
            "data_bytes": VALID_PNG_BYTES,
        }, "CASE-A")
