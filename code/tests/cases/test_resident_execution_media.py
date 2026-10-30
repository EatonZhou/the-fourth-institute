"""Chat attachments and images reach the execution as native input media of the exact Resident.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from tests.cases.resident_delivery_test_support import published_delivery as _published_delivery
from management_portal.execution import slash_commands

import json
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_execution_domain,
    resident_fork_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_lineage,
    resident_protocol,
    resident_runtime_domain,
    resident_session_backend_domain,
    workspace_domain,
    workspace_contracts,
)
from management_portal.society_service import resident_domain as backend
from management_portal.society_service import resident_media_domain
from management_portal.society_service import workspace_attachments
from management_portal.execution.agent_data_store import get_agent_data_store
from tests.cases.resident_execution_test_support import VALID_PNG_BYTES
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


def test_chat_attachments_use_the_exact_resident_identity(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """聊天图片和附件使用目标 Resident 的准确身份"""
    root_id = str(resident_record["resident_id"])
    child_id = "resident_bbbbbbbbbbbb"
    registry = resident_lineage.snapshot(resident_record)
    registry["residents"].append({
        "resident_id": child_id,
        "source_resident_id": root_id,
        "lineage_id": resident_record["lineage_id"],
        "session_state": "active",
        "created_by": "CASE-A",
        "backend": "codex",
    })
    resident_fork_domain._persist_derived_resident(resident_record, registry["residents"][-1])
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(
        workspace_domain,
        "CHAT_ATTACHMENT_CACHE_ROOT",
        tmp_path / "attachments",
    )

    image = workspace_domain.save_workspace_pasted_image({
        "resident_id": child_id,
        "filename": "clipboard.png",
        "content_type": "image/png",
        "data_bytes": VALID_PNG_BYTES,
    }, "CASE-A")
    attachment = workspace_domain.save_workspace_attachment({
        "resident_id": child_id,
        "filename": "notes.txt",
        "content_type": "text/plain",
        "data_bytes": b"attachment body",
    }, "CASE-A")

    for result in (image, attachment):
        meta_path = Path(result["path"]).with_suffix(
            Path(result["path"]).suffix + ".json"
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["resident_id"] == child_id
        assert "fork_id" not in meta


def test_uploaded_chat_image_preview_can_be_resolved_immediately(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """聊天图片上传后可立即解析预览内容"""
    resident_id = str(resident_record["resident_id"])
    image_root = tmp_path / "images"
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", image_root)
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", image_root)
    image_bytes = VALID_PNG_BYTES

    uploaded = workspace_domain.save_workspace_pasted_image({
        "resident_id": resident_id,
        "filename": "preview.png",
        "content_type": "image/png",
        "data_bytes": image_bytes,
    }, "CASE-A")
    path, content_type = workspace_domain.resolve_workspace_pasted_image(
        str(uploaded["file_id"]),
        "CASE-A",
    )

    assert path.read_bytes() == image_bytes
    assert content_type == "image/png"


def test_resident_image_reaches_execution_as_native_input_media(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 图片暂存后以原生媒体传入执行请求"""
    resident_id = str(resident_record["resident_id"])
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    image = workspace_domain.save_workspace_pasted_image({
        "resident_id": resident_id,
        "filename": "latency-probe.png",
        "content_type": "image/png",
        "data_bytes": VALID_PNG_BYTES,
    }, "CASE-A")
    content = f"识别图片中的内容。\n\n[image: {image['path']}]"
    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_intake_domain, "resolve_context_refs", return_value={}),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            resident_id,
            "CASE-A",
            content,
            input_media=[{
                "media_type": "image",
                "source_type": "local_path",
                "source": image["path"],
                "mime_type": "image/png",
                "name": "latency-probe.png",
            }],
    ))

    assert result["resident_id"] == resident_id
    assert result["message"]["message_id"] == result["message_id"]
    assert result["input_state"]["state"] == "running"
    assert result["state"]["work_phase"] == "starting_session"
    job = resident_execution_work.read(resident_record)
    staged_image = Path(job["input_media"][0]["source"])
    assert staged_image != Path(image["path"])
    assert staged_image.read_bytes() == VALID_PNG_BYTES
    assert job["input_media"][0]["original_source"] == image["path"]
    assert "actual_sent_prompt" not in job

    Path(image["path"]).unlink()
    prepared = resident_execution_domain._prepare_resident_prompt(resident_record, job)
    assert str(image["path"]) not in prepared["actual_sent_prompt"]
    assert "[attached image: latency-probe.png]" in prepared["actual_sent_prompt"]
    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        prepared,
    )
    assert request.input_media[0].source == str(staged_image)
    assert staged_image.is_file()
    assert request.runtime_options.get("images") is None

    job["outcome"] = "completed"
    job["finished_at"] = "2026-09-28T00:00:00Z"
    resident_execution_work.write(resident_record, job)
    # A terminal Job alone cannot discard a still-running Conversation input.
    assert staged_image.exists()


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("media_type", ["image", "file"])
def test_inline_media_positions_survive_delivery_and_provider_capture(resident_record, monkeypatch, tmp_path, queued, media_type):
    """消息执行前后保留图片与文件的插入位置

    首次发送和排队执行均保留有空白与空段的图文顺序；模型输入仍包含文件读取路径。

    :module: resident.conversation
    """
    from management_portal.execution.agent_data_projection import AgentDataProjector
    from management_portal.execution.message_projection import unwrap_message
    from tests.cases.test_agent_data_projection import _runtime_session, _invocation

    resident_id = resident_record["resident_id"]
    message_id = "msg_" + "d" * 32
    upload_id = "upload_" + "a" * 24
    attachment = tmp_path / upload_id / ("inline.png" if media_type == "image" else "manuscript.docx")
    attachment.parent.mkdir()
    attachment.write_bytes(VALID_PNG_BYTES if media_type == "image" else b"file-content")
    monkeypatch.setattr(resident_media_domain, "resident_input_media", lambda *a, **kw: [{
        "media_type": media_type, "source_type": "local_path", "source": str(attachment),
        "mime_type": "image/png" if media_type == "image" else "application/octet-stream", "name": attachment.name,
    }])
    monkeypatch.setattr(resident_intake_domain, "resolve_context_refs", lambda *a, **kw: {})
    if queued:
        _published_delivery(backend.deliver_resident_message_content(resident_id, "sample_admin", "Previous work"))
    parts = [
        {"type": "text", "text": "\n1. Before "},
        {"type": media_type, "upload_id": upload_id, "name": attachment.name},
        {"type": "text", "text": ""},
        {"type": "text", "text": "\n2. After.\n"},
    ]
    result = _published_delivery(resident_protocol.deliver_resident_input({"resident_id": resident_id, "message": {
        "message_id": message_id, "role": "user", "content_parts": parts,
    }}, "sample_admin"))
    job = resident_execution_work.read(resident_record)
    if queued:
        assert result["input_state"]["state"] == "queued"
        waiting = resident_execution_work.waiting_input_queue(job)
        job = resident_execution_work.promote_queue_item(job, waiting[0], [], "2026-10-13T10:00:00Z")
        resident_execution_work.write(resident_record, job)
    body = "\n".join(part["text"] for part in parts if part["type"] == "text")
    prepared = resident_execution_domain._prepare_resident_prompt(resident_record, job)
    provider_body = body if media_type == "image" else body + f"\n\n[file: {attachment}]"
    assert unwrap_message(prepared["actual_sent_prompt"]) == provider_body
    request = resident_execution_domain._build_resident_execution_request(resident_record, prepared)
    store = get_agent_data_store()
    session = _runtime_session(request, "inline-roundtrip-session")
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, _invocation(request, session))
    assert store.chat_message(resident_id, message_id)["content_parts"] == parts


def test_resident_workspace_image_marker_becomes_native_input_media(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace 图片标记转为原生输入媒体"""
    resident_id = str(resident_record["resident_id"])
    image_path = Path(str(resident_record["default_cwd"])) / "workspace-probe.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nworkspace-image")
    content = f"识别这张 workspace 图片。\n\n[image: {image_path}]"
    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_intake_domain, "resolve_context_refs", return_value={}),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            resident_id,
            "CASE-A",
            content,
    ))

    assert result["resident_id"] == resident_id
    assert result["message"]["message_id"] == result["message_id"]
    assert result["input_state"]["state"] == "running"
    assert result["state"]["work_phase"] == "starting_session"
    job = resident_execution_work.read(resident_record)
    assert Path(job["input_media"][0]["source"]).read_bytes() == image_path.read_bytes()
    assert job["input_media"][0]["original_source"] == str(image_path)
    assert "actual_sent_prompt" not in job
    prepared = resident_execution_domain._prepare_resident_prompt(resident_record, job)
    assert str(image_path) not in prepared["actual_sent_prompt"]
    assert "[attached image: workspace-probe.png]" in prepared["actual_sent_prompt"]


def test_resident_input_media_rejects_path_outside_workspace(
    resident_record: dict[str, object],
    tmp_path: Path,
) -> None:
    """Resident 输入媒体拒绝上传区和 Workspace 外路径"""
    outside_path = tmp_path / "outside.png"
    outside_path.write_bytes(b"\x89PNG\r\n\x1a\noutside-image")

    with pytest.raises(PermissionError, match="outside its upload and workspace"):
        resident_media_domain.resident_input_media(
            [{"kind": "image", "path": str(outside_path)}],
            actor="CASE-A",
            resident_id=str(resident_record["resident_id"]),
            content=f"[image: {outside_path}]",
            workspace_roots=resident_runtime_domain.resident_media_workspace_roots(
                resident_record
            ),
        )


def test_resident_input_media_rejects_another_residents_image(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """复用其他 Resident 图片时要求可访问其对话"""
    root_id = str(resident_record["resident_id"])
    child_id = "resident_bbbbbbbbbbbb"
    registry = resident_lineage.snapshot(resident_record)
    registry["residents"].append({
        "resident_id": child_id,
        "source_resident_id": root_id,
        "lineage_id": resident_record["lineage_id"],
        "created_by": "CASE-A",
        "backend": "codex",
    })
    resident_fork_domain._persist_derived_resident(resident_record, registry["residents"][-1])
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    image = workspace_domain.save_workspace_pasted_image({
        "resident_id": root_id,
        "filename": "root-only.png",
        "content_type": "image/png",
        "data_bytes": VALID_PNG_BYTES,
    }, "CASE-A")

    reused = resident_media_domain.resident_input_media(
        [{"kind": "image", "path": image["path"]}],
        actor="CASE-A",
        resident_id=child_id,
        content=f"[image: {image['path']}]",
    )
    assert reused[0]["source"] == image["path"]
    def reject_root_conversation(resident_id: str, _actor: str) -> dict[str, str]:
        if resident_id == root_id:
            raise PermissionError("hidden Conversation")
        return {"resident_id": resident_id}

    monkeypatch.setattr(
        workspace_attachments.resident_domain_port,
        "resolve_resident_identity",
        reject_root_conversation,
    )
    with pytest.raises(PermissionError, match="Conversation"):
        resident_media_domain.resident_input_media(
            [{"kind": "image", "path": image["path"]}],
            actor="CASE-A",
            resident_id=child_id,
            content=f"[image: {image['path']}]",
        )


def test_queue_promotion_preserves_native_input_media(
    resident_record: dict[str, object],
) -> None:
    """队列提升保留原生输入媒体"""
    job = {
        'execution_id': "job_current",
        "input_media": [],
        "queue": [],
    }
    media = [{
        "media_type": "image",
        "source_type": "local_path",
        "source": "/tmp/queued-probe.png",
        "mime_type": "image/png",
        "name": "queued-probe.png",
    }]
    next_job = {
        'execution_id': "job_image",
        "user_message_id": "message_image",
        "message": "inspect queued image",
        "actor": "CASE-A",
        "input_media": media,
    }

    resident_execution_work.promote_queue_item(
        job,
        next_job,
        [],
        "2026-07-27T00:01:00Z",
        retain_continuation=True,
    )

    assert job['execution_id'].startswith('execution-')
    assert job["input_media"] == media
